"""Discovery engine — find and identify devices on the bootstrap network."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from bare_metal_automation.models import (
    CDPNeighbour,
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
)
from bare_metal_automation.settings import DEFAULT_CREDENTIALS, LEASE_FILE

logger = logging.getLogger(__name__)


class DiscoveryEngine:
    """Discovers devices on the bootstrap network using DHCP leases and CDP."""

    def __init__(
        self,
        bootstrap_subnet: str,
        laptop_ip: str,
        ssh_timeout: int = 30,
        lease_file: str | None = None,
    ) -> None:
        self.bootstrap_subnet = bootstrap_subnet
        self.laptop_ip = laptop_ip
        self.ssh_timeout = ssh_timeout
        self.lease_file = Path(lease_file or LEASE_FILE)

    def get_dhcp_leases(self) -> dict[str, str]:
        """Parse dnsmasq lease file. Returns {ip: mac}."""
        leases: dict[str, str] = {}

        if not self.lease_file.exists():
            logger.warning(f"Lease file not found: {self.lease_file}")
            return leases

        with open(self.lease_file) as f:
            for line in f:
                # dnsmasq lease format: timestamp mac ip hostname client-id
                parts = line.strip().split()
                if len(parts) >= 3:
                    mac, ip = parts[1], parts[2]
                    if ip != self.laptop_ip:
                        leases[ip] = mac

        logger.info(f"Found {len(leases)} DHCP leases")
        return leases

    def probe_device(self, ip: str, mac: str) -> DiscoveredDevice:
        """SSH into a device, collect CDP neighbours and serial number."""
        device = DiscoveredDevice(ip=ip, mac=mac)

        for username, password in DEFAULT_CREDENTIALS:
            try:
                connection = self._ssh_connect(ip, username, password)
                if connection is None:
                    continue

                # Get serial and platform
                inventory_output = self._ssh_command(connection, "show inventory")
                device.serial, device.platform = self._parse_inventory(inventory_output)

                # Get hostname
                hostname_output = self._ssh_command(connection, "show running-config | include hostname")
                device.hostname = self._parse_hostname(hostname_output)

                # Get CDP neighbours
                cdp_output = self._ssh_command(connection, "show cdp neighbors detail")
                device.cdp_neighbours = self._parse_cdp(cdp_output)

                device.state = DeviceState.DISCOVERED
                connection.disconnect()
                break

            except Exception as e:
                logger.debug(f"Failed to probe {ip} with {username}: {e}")
                continue

        if device.state == DeviceState.UNKNOWN:
            # Might be an iLO endpoint — try Redfish
            device = self._probe_redfish(ip, mac, device)

        return device

    def _ssh_connect(self, ip: str, username: str, password: str):
        """Establish SSH connection using Netmiko."""
        try:
            from netmiko import ConnectHandler

            return ConnectHandler(
                device_type="cisco_ios",  # Try IOS first; handles most Cisco kit
                host=ip,
                username=username,
                password=password,
                timeout=self.ssh_timeout,
                auth_timeout=self.ssh_timeout,
            )
        except Exception as e:
            logger.debug(f"SSH to {ip} failed: {e}")
            return None

    def _ssh_command(self, connection, command: str) -> str:
        """Run a command over an established SSH session."""
        try:
            return connection.send_command(command, read_timeout=self.ssh_timeout)
        except Exception as e:
            logger.warning(f"Command '{command}' failed: {e}")
            return ""

    def _parse_inventory(self, output: str) -> tuple[str | None, str | None]:
        """Extract serial number and PID from 'show inventory' output."""
        serial = None
        platform = None

        serial_match = re.search(r"SN:\s*(\S+)", output)
        if serial_match:
            serial = serial_match.group(1)

        pid_match = re.search(r"PID:\s*(\S+)", output)
        if pid_match:
            platform = pid_match.group(1)

        return serial, platform

    def _parse_hostname(self, output: str) -> str | None:
        """Extract hostname from running config snippet."""
        match = re.search(r"hostname\s+(\S+)", output)
        return match.group(1) if match else None

    def _parse_cdp(self, output: str) -> list[CDPNeighbour]:
        """Parse 'show cdp neighbors detail' into structured data."""
        neighbours: list[CDPNeighbour] = []
        entries = re.split(r"-{20,}", output)

        for entry in entries:
            if not entry.strip():
                continue

            device_id_match = re.search(r"Device ID:\s*(\S+)", entry)
            platform_match = re.search(r"Platform:\s*(.+?),", entry)
            local_port_match = re.search(
                r"Interface:\s*(\S+),\s*Port ID \(outgoing port\):\s*(\S+)", entry
            )
            ip_match = re.search(r"IP address:\s*(\S+)", entry)

            if device_id_match and local_port_match:
                neighbours.append(
                    CDPNeighbour(
                        local_port=local_port_match.group(1),
                        remote_device_id=device_id_match.group(1),
                        remote_port=local_port_match.group(2),
                        remote_platform=platform_match.group(1).strip() if platform_match else "",
                        remote_ip=ip_match.group(1) if ip_match else "",
                    )
                )

        return neighbours

    def _probe_redfish(self, ip: str, mac: str, device: DiscoveredDevice) -> DiscoveredDevice:
        """Attempt to identify device via Redfish API (iLO)."""
        try:
            import requests

            url = f"https://{ip}/redfish/v1/Systems/1"
            resp = requests.get(
                url,
                auth=("Administrator", ""),  # iLO factory default
                verify=False,
                timeout=self.ssh_timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                device.serial = data.get("SerialNumber")
                device.platform = data.get("Model")
                device.hostname = data.get("HostName")
                device.state = DeviceState.DISCOVERED
                logger.info(f"Identified iLO at {ip}: {device.platform} ({device.serial})")

        except Exception as e:
            logger.debug(f"Redfish probe of {ip} failed: {e}")

        return device

    def match_to_inventory(
        self,
        discovered: dict[str, DiscoveredDevice],
        inventory: DeploymentInventory,
    ) -> None:
        """Match discovered devices to their intended roles from inventory."""
        for ip, device in discovered.items():
            if device.serial is None:
                continue

            spec = inventory.get_device_spec(device.serial)
            if spec:
                device.role = spec.get("role")
                device.intended_hostname = spec.get("hostname")
                device.template_path = spec.get("template")
                device.device_platform = spec.get("platform")
                device.state = DeviceState.IDENTIFIED
                logger.info(
                    f"Matched {device.serial} at {ip} → "
                    f"{device.intended_hostname} ({device.role})"
                )
            else:
                logger.warning(f"Unknown device at {ip}: serial {device.serial}")
