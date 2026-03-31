"""Network device resetter — write erase + reload to restore factory defaults."""

from __future__ import annotations

import logging

from bare_metal_automation.models import (
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
)

logger = logging.getLogger(__name__)

# Factory default credentials to try if production creds fail
FACTORY_CREDENTIALS = [
    ("cisco", "cisco"),
    ("admin", "admin"),
    ("admin", ""),
]


class NetworkResetter:
    """Resets Cisco network devices to factory defaults via SSH.

    Issues ``write erase`` to clear startup-config, then ``reload`` so the
    device boots with no configuration.  After reset the device will be
    accessible with factory-default credentials and ready for ZTP
    re-provisioning.

    Devices must be reset **inside-out** (lowest BFS depth first) so that
    the management path to further devices stays intact until they have
    been reset.
    """

    def __init__(
        self,
        inventory: DeploymentInventory,
        ssh_timeout: int = 30,
    ) -> None:
        self.inventory = inventory
        self.ssh_timeout = ssh_timeout

    def reset_device(self, device: DiscoveredDevice) -> bool:
        """Factory-reset a single Cisco network device.

        Sequence:
        1. Connect via SSH (production creds, then factory defaults)
        2. ``write erase`` — clears startup-config
        3. ``reload`` — reboots to factory defaults
        """
        hostname = device.intended_hostname or device.hostname or device.ip
        logger.info(f"{hostname}: Starting factory reset")
        device.state = DeviceState.RESETTING

        try:
            connection = self._connect(device)
            if connection is None:
                device.state = DeviceState.FAILED
                return False

            # Step 1: write erase (clear startup-config)
            logger.info(f"{hostname}: Erasing startup configuration")
            output = connection.send_command_timing(
                "write erase",
                strip_prompt=False,
            )
            # Confirm the erase prompt ("[confirm]" or similar)
            if "confirm" in output.lower() or "[" in output:
                connection.send_command_timing("\n", strip_prompt=False)

            # Step 2: reload (no save)
            logger.info(f"{hostname}: Reloading device")
            output = connection.send_command_timing(
                "reload",
                strip_prompt=False,
            )
            # Handle "System configuration has been modified. Save? [yes/no]:"
            if "save" in output.lower() or "modified" in output.lower():
                connection.send_command_timing("no", strip_prompt=False)

            # Handle "Proceed with reload? [confirm]"
            if "confirm" in output.lower() or "[" in output:
                connection.send_command_timing("\n", strip_prompt=False)

            try:
                connection.disconnect()
            except Exception:
                pass  # Device may already be reloading

            device.state = DeviceState.RESET_COMPLETE
            logger.info(f"{hostname}: Factory reset initiated — device is reloading")
            return True

        except Exception as e:
            logger.error(f"{hostname}: Factory reset failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _connect(self, device: DiscoveredDevice):
        """Connect to device via SSH, trying production then factory creds."""
        from netmiko import ConnectHandler

        device_type_map = {
            "cisco_ios": "cisco_ios",
            "cisco_iosxe": "cisco_xe",
            "cisco_asa": "cisco_asa",
            "cisco_ftd": "cisco_ftd",
        }

        platform = device.device_platform
        if isinstance(platform, str):
            netmiko_type = device_type_map.get(platform, "cisco_ios")
        else:
            netmiko_type = device_type_map.get(
                platform.value if platform else "cisco_ios", "cisco_ios"
            )

        # Build credential list: production creds first, then factory defaults
        spec = self.inventory.get_device_spec(device.serial) or {}
        cred_list = []
        prod_user = spec.get("username")
        prod_pass = spec.get("password")
        if prod_user and prod_pass:
            cred_list.append((prod_user, prod_pass))
        cred_list.extend(FACTORY_CREDENTIALS)

        hostname = device.intended_hostname or device.hostname or device.ip

        for username, password in cred_list:
            try:
                conn = ConnectHandler(
                    device_type=netmiko_type,
                    host=device.ip,
                    username=username,
                    password=password,
                    timeout=self.ssh_timeout,
                )
                logger.info(f"{hostname}: Connected via SSH ({username}@{device.ip})")
                return conn
            except Exception:
                continue

        logger.error(f"{hostname}: SSH connection failed — all credentials exhausted")
        return None
