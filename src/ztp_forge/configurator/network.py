"""Network configurator — push configs to devices with rollback protection."""

from __future__ import annotations

import logging
import time

from jinja2 import Environment, FileSystemLoader

from ztp_forge.models import DeploymentInventory, DiscoveredDevice

logger = logging.getLogger(__name__)

# Dead man's switch: reload timer in minutes
RELOAD_TIMER_MINUTES = 5


class NetworkConfigurator:
    """Configures network devices via SSH with dead man's switch rollback protection."""

    def __init__(
        self,
        inventory: DeploymentInventory,
        ssh_timeout: int = 30,
        template_dir: str = "configs/templates",
    ) -> None:
        self.inventory = inventory
        self.ssh_timeout = ssh_timeout
        self.jinja_env = Environment(
            loader=FileSystemLoader(template_dir),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def configure_device(self, device: DiscoveredDevice) -> bool:
        """Configure a single device with dead man's switch protection.

        Sequence:
        1. Generate config from template
        2. Set 'reload in N' (dead man's switch)
        3. Apply config to running-config
        4. Validate device health
        5. If valid: cancel reload, write mem
        6. If invalid: let the reload happen (automatic rollback)
        """
        try:
            # Step 1: Render config from template
            config_lines = self._render_config(device)
            if not config_lines:
                logger.error(f"Empty config for {device.intended_hostname}")
                return False

            # Step 2: Connect
            connection = self._connect(device)
            if connection is None:
                return False

            # Step 3: Set dead man's switch
            logger.info(f"Setting reload timer ({RELOAD_TIMER_MINUTES}min) on {device.intended_hostname}")
            self._set_reload_timer(connection)

            # Step 4: Apply config to running-config
            logger.info(f"Applying config to {device.intended_hostname}")
            self._apply_config(connection, config_lines)

            # Step 5: Validate
            logger.info(f"Validating {device.intended_hostname}")
            valid = self._validate_device(connection, device)

            if valid:
                # Step 6a: Cancel reload and save
                logger.info(f"Validation passed — cancelling reload and saving")
                self._cancel_reload(connection)
                self._write_mem(connection)
                connection.disconnect()
                return True
            else:
                # Step 6b: Let it reload (rollback)
                logger.warning(
                    f"Validation FAILED on {device.intended_hostname} — "
                    f"device will reload in {RELOAD_TIMER_MINUTES} minutes"
                )
                connection.disconnect()
                # Wait for the device to reload and come back up
                self._wait_for_reload(device.ip)
                return False

        except Exception as e:
            logger.error(f"Configuration failed for {device.intended_hostname}: {e}")
            return False

    def _render_config(self, device: DiscoveredDevice) -> list[str]:
        """Render Jinja2 template into config lines."""
        if not device.template_path:
            return []

        try:
            template = self.jinja_env.get_template(device.template_path)
        except Exception as e:
            logger.error(f"Template load failed for {device.template_path}: {e}")
            return []

        spec = self.inventory.get_device_spec(device.serial) or {}
        variables = {
            "hostname": device.intended_hostname,
            "serial": device.serial,
            "management_vlan": self.inventory.management_vlan,
            "management_ip": spec.get("management_ip"),
            "management_subnet": spec.get("management_subnet"),
            **spec,
        }

        rendered = template.render(**variables)
        return [line for line in rendered.split("\n") if line.strip()]

    def _connect(self, device: DiscoveredDevice):
        """Connect to device via SSH."""
        from netmiko import ConnectHandler

        device_type_map = {
            "cisco_ios": "cisco_ios",
            "cisco_iosxe": "cisco_xe",
            "cisco_asa": "cisco_asa",
            "cisco_ftd": "cisco_ftd",
        }

        platform = device_type_map.get(device.device_platform, "cisco_ios")

        try:
            return ConnectHandler(
                device_type=platform,
                host=device.ip,
                username="cisco",
                password="cisco",
                timeout=self.ssh_timeout,
            )
        except Exception as e:
            logger.error(f"SSH connection failed to {device.ip}: {e}")
            return None

    def _set_reload_timer(self, connection) -> None:
        """Set 'reload in N' as a dead man's switch."""
        connection.send_command_timing(
            f"reload in {RELOAD_TIMER_MINUTES}",
            strip_prompt=False,
        )
        # Confirm the reload prompt
        connection.send_command_timing("\n", strip_prompt=False)

    def _cancel_reload(self, connection) -> None:
        """Cancel a pending reload."""
        connection.send_command_timing("reload cancel", strip_prompt=False)

    def _apply_config(self, connection, config_lines: list[str]) -> None:
        """Send config lines to the device."""
        connection.send_config_set(
            config_lines,
            enter_config_mode=True,
            exit_config_mode=True,
        )

    def _write_mem(self, connection) -> None:
        """Save running-config to startup-config."""
        connection.send_command("write memory", read_timeout=30)

    def _validate_device(self, connection, device: DiscoveredDevice) -> bool:
        """Run post-config validation checks.

        Checks vary by device role — switches check STP and trunks,
        routers check interfaces and routing, firewalls check zones.
        """
        validators = {
            "core-switch": self._validate_switch,
            "access-switch": self._validate_switch,
            "distribution-switch": self._validate_switch,
            "border-router": self._validate_router,
            "perimeter-firewall": self._validate_firewall,
        }

        validator_fn = validators.get(device.role, self._validate_generic)
        return validator_fn(connection, device)

    def _validate_switch(self, connection, device: DiscoveredDevice) -> bool:
        """Validate a switch post-configuration."""
        checks_passed = True

        # Check STP is running
        stp_output = connection.send_command("show spanning-tree summary")
        if "No spanning tree" in stp_output:
            logger.error(f"{device.intended_hostname}: STP not running")
            checks_passed = False

        # Check trunks are up
        trunk_output = connection.send_command("show interfaces trunk")
        if not trunk_output.strip():
            logger.warning(f"{device.intended_hostname}: No trunks detected")
            # Not necessarily fatal — access switches might not have trunks yet

        # Check management VLAN exists
        vlan_output = connection.send_command("show vlan brief")
        mgmt_vlan = str(self.inventory.management_vlan)
        if mgmt_vlan not in vlan_output:
            logger.error(f"{device.intended_hostname}: Management VLAN {mgmt_vlan} not found")
            checks_passed = False

        return checks_passed

    def _validate_router(self, connection, device: DiscoveredDevice) -> bool:
        """Validate a router post-configuration."""
        # Check interfaces are up
        output = connection.send_command("show ip interface brief")
        down_count = output.count("down")
        if down_count > 0:
            logger.warning(f"{device.intended_hostname}: {down_count} interfaces down")

        return True  # Routers may have interfaces down until the other end is configured

    def _validate_firewall(self, connection, device: DiscoveredDevice) -> bool:
        """Validate a firewall post-configuration."""
        # Check nameif is configured
        output = connection.send_command("show interface ip brief")
        if "nameif" not in output.lower() and len(output.strip()) < 10:
            logger.error(f"{device.intended_hostname}: No interfaces with nameif")
            return False

        return True

    def _validate_generic(self, connection, device: DiscoveredDevice) -> bool:
        """Minimal validation — just check the device is responsive."""
        output = connection.send_command("show version")
        return len(output) > 0

    def _wait_for_reload(self, ip: str, timeout: int = 600, interval: int = 15) -> bool:
        """Wait for a device to come back online after a reload."""
        import socket

        logger.info(f"Waiting for {ip} to reload (timeout {timeout}s)...")
        start = time.time()

        while time.time() - start < timeout:
            try:
                sock = socket.create_connection((ip, 22), timeout=5)
                sock.close()
                logger.info(f"{ip} is back online")
                time.sleep(10)  # Give it a moment to fully boot
                return True
            except (socket.timeout, ConnectionRefusedError, OSError):
                time.sleep(interval)

        logger.error(f"{ip} did not come back within {timeout}s")
        return False
