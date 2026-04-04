"""CDP and LLDP neighbour collector.

Collects layer-2 neighbour tables from network devices via SSH:
  - CDP  (Cisco Discovery Protocol) using ``show cdp neighbors detail``
  - LLDP (Link Layer Discovery Protocol) using ``show lldp neighbors detail``

Both parsers return the same :class:`~bare_metal_automation.models.CDPNeighbour`
dataclass so callers get a unified neighbour list regardless of which protocol
the device supports.  The :class:`NeighbourCollector` tries CDP first, falls
back to LLDP if CDP returns nothing, and merges deduplicated results.
"""

from __future__ import annotations

import logging
import re

from bare_metal_automation.models import CDPNeighbour

logger = logging.getLogger(__name__)

# Factory-default credential list to try in order
DEFAULT_CREDENTIALS: list[tuple[str, str]] = [
    ("cisco", "cisco"),
    ("admin", "admin"),
    ("admin", ""),
]


# ── CDP parsing ────────────────────────────────────────────────────────────

def parse_cdp_output(output: str) -> list[CDPNeighbour]:
    """Parse raw 'show cdp neighbors detail' output into structured records.

    Each CDP entry is separated by a line of dashes (------...).
    Extracts: device ID, platform, local/remote port, management IP, serial.
    """
    neighbours: list[CDPNeighbour] = []
    entries = re.split(r"-{20,}", output)

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        device_id_match = re.search(r"Device ID:\s*(\S+)", entry)
        platform_match = re.search(r"Platform:\s*(.+?),", entry)
        port_match = re.search(
            r"Interface:\s*(\S+),\s*Port ID \(outgoing port\):\s*(\S+)", entry
        )
        ip_match = re.search(r"IP(?:v4)? [Aa]ddress:\s*(\S+)", entry)
        # Some IOS versions report serial inside CDP capabilities/version block
        serial_match = re.search(r"SerialNumber:\s*(\S+)", entry)

        if device_id_match and port_match:
            neighbours.append(
                CDPNeighbour(
                    local_port=port_match.group(1),
                    remote_device_id=device_id_match.group(1),
                    remote_port=port_match.group(2),
                    remote_platform=(
                        platform_match.group(1).strip() if platform_match else ""
                    ),
                    remote_ip=ip_match.group(1) if ip_match else "",
                    remote_serial=serial_match.group(1) if serial_match else None,
                )
            )

    return neighbours


# ── LLDP parsing ───────────────────────────────────────────────────────────

def parse_lldp_output(output: str) -> list[CDPNeighbour]:
    """Parse raw 'show lldp neighbors detail' output into CDPNeighbour records.

    LLDP entries are separated by lines beginning with ``-----------`` or by
    the ``Local Intf:`` header that starts each entry on IOS/IOS-XE.

    Handles both Cisco IOS-XE and NX-OS LLDP output formats.
    """
    neighbours: list[CDPNeighbour] = []

    # Split on the entry separator ("----" or "Local Intf:" boundary)
    entries = re.split(r"(?:^-{10,}|(?=^Local Intf:))", output, flags=re.MULTILINE)

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        # IOS-XE: "Local Intf: GigabitEthernet1/0/1"
        # NX-OS:  "Local Port id: Ethernet1/1"
        local_port_match = re.search(
            r"Local (?:Intf|Port id):\s*(\S+)", entry, re.IGNORECASE
        )
        # "System Name: <hostname>" or "Port ID (outgoing port): <port>"
        system_name_match = re.search(r"System Name:\s*(\S+)", entry, re.IGNORECASE)
        chassis_id_match = re.search(r"Chassis id:\s*(\S+)", entry, re.IGNORECASE)
        remote_port_match = re.search(
            r"Port (?:id|ID)(?:\s+\(outgoing port\))?:\s*(\S+)", entry, re.IGNORECASE
        )
        # "System Description: Cisco IOS Software, ..."
        sys_desc_match = re.search(r"System Description:\s*(.+)", entry, re.IGNORECASE)
        # Management address
        mgmt_ip_match = re.search(
            r"Management Addresses?.*?IP(?:v4)?:\s*(\d[\d.]+)", entry,
            re.IGNORECASE | re.DOTALL,
        )
        if mgmt_ip_match is None:
            # Fallback: look for bare IP in the management address block
            mgmt_ip_match = re.search(
                r"Management Addresses?[^\n]*\n\s+(\d{1,3}(?:\.\d{1,3}){3})",
                entry,
                re.IGNORECASE,
            )

        if local_port_match and (system_name_match or chassis_id_match) and remote_port_match:
            device_id = (
                system_name_match.group(1)
                if system_name_match
                else chassis_id_match.group(1)  # type: ignore[union-attr]
            )
            platform = ""
            if sys_desc_match:
                # Take first line of system description as platform
                platform = sys_desc_match.group(1).strip().splitlines()[0]

            neighbours.append(
                CDPNeighbour(
                    local_port=local_port_match.group(1),
                    remote_device_id=device_id,
                    remote_port=remote_port_match.group(1),
                    remote_platform=platform,
                    remote_ip=mgmt_ip_match.group(1) if mgmt_ip_match else "",
                    remote_serial=None,
                )
            )

    return neighbours


# ── Collectors ─────────────────────────────────────────────────────────────

class CDPCollector:
    """SSH into a network device and collect its CDP neighbour table."""

    def __init__(self, ssh_timeout: int = 30) -> None:
        self.ssh_timeout = ssh_timeout

    def collect(
        self,
        ip: str,
        credentials: list[tuple[str, str]] | None = None,
    ) -> list[CDPNeighbour]:
        """Connect to *ip* and return its parsed CDP neighbour list.

        Tries each credential pair in *credentials* (falls back to
        ``DEFAULT_CREDENTIALS``).  Returns an empty list if all fail.
        """
        creds = credentials or DEFAULT_CREDENTIALS

        for username, password in creds:
            connection = self._connect(ip, username, password)
            if connection is None:
                continue
            try:
                output = connection.send_command(
                    "show cdp neighbors detail",
                    read_timeout=self.ssh_timeout,
                )
                return parse_cdp_output(output)
            except Exception as e:
                logger.warning(f"CDP command failed on {ip}: {e}")
                return []
            finally:
                try:
                    connection.disconnect()
                except Exception:
                    pass

        logger.warning(f"All credentials failed for CDP collection on {ip}")
        return []

    # ── Internal ───────────────────────────────────────────────────────────

    def _connect(self, ip: str, username: str, password: str):  # type: ignore[return]
        """Return a Netmiko ConnectHandler or None on failure."""
        try:
            from netmiko import ConnectHandler

            return ConnectHandler(
                device_type="cisco_ios",
                host=ip,
                username=username,
                password=password,
                timeout=self.ssh_timeout,
                auth_timeout=self.ssh_timeout,
            )
        except Exception as e:
            logger.debug(f"SSH connection to {ip} failed ({username}): {e}")
            return None


class LLDPCollector:
    """SSH into a network device and collect its LLDP neighbour table."""

    def __init__(self, ssh_timeout: int = 30) -> None:
        self.ssh_timeout = ssh_timeout

    def collect(
        self,
        ip: str,
        credentials: list[tuple[str, str]] | None = None,
        device_type: str = "cisco_ios",
    ) -> list[CDPNeighbour]:
        """Connect to *ip* and return its parsed LLDP neighbour list.

        *device_type* is passed to Netmiko.  Supported values include
        ``cisco_ios``, ``cisco_nxos``, ``arista_eos``, ``juniper_junos``.
        """
        creds = credentials or DEFAULT_CREDENTIALS

        for username, password in creds:
            connection = self._connect(ip, username, password, device_type)
            if connection is None:
                continue
            try:
                output = connection.send_command(
                    "show lldp neighbors detail",
                    read_timeout=self.ssh_timeout,
                )
                return parse_lldp_output(output)
            except Exception as e:
                logger.warning(f"LLDP command failed on {ip}: {e}")
                return []
            finally:
                try:
                    connection.disconnect()
                except Exception:
                    pass

        logger.warning(f"All credentials failed for LLDP collection on {ip}")
        return []

    def _connect(self, ip: str, username: str, password: str, device_type: str):  # type: ignore[return]
        try:
            from netmiko import ConnectHandler

            return ConnectHandler(
                device_type=device_type,
                host=ip,
                username=username,
                password=password,
                timeout=self.ssh_timeout,
                auth_timeout=self.ssh_timeout,
            )
        except Exception as e:
            logger.debug(f"SSH connection to {ip} failed ({username}): {e}")
            return None


class NeighbourCollector:
    """Try CDP then LLDP, returning a merged deduplicated neighbour list.

    For each device pair (local_port, remote_device_id) only the first
    occurrence is kept, preferring CDP entries (which carry serial numbers).
    """

    def __init__(self, ssh_timeout: int = 30) -> None:
        self.ssh_timeout = ssh_timeout
        self._cdp = CDPCollector(ssh_timeout=ssh_timeout)
        self._lldp = LLDPCollector(ssh_timeout=ssh_timeout)

    def collect(
        self,
        ip: str,
        credentials: list[tuple[str, str]] | None = None,
        device_type: str = "cisco_ios",
    ) -> list[CDPNeighbour]:
        """Return neighbours discovered via CDP + LLDP (deduplicated)."""
        cdp_neighbours = self._cdp.collect(ip, credentials)
        lldp_neighbours = self._lldp.collect(ip, credentials, device_type)

        seen: set[tuple[str, str]] = set()
        merged: list[CDPNeighbour] = []

        for n in cdp_neighbours + lldp_neighbours:
            key = (n.local_port, n.remote_device_id)
            if key not in seen:
                seen.add(key)
                merged.append(n)

        if cdp_neighbours:
            logger.debug(f"{ip}: {len(cdp_neighbours)} CDP neighbour(s)")
        if lldp_neighbours:
            logger.debug(f"{ip}: {len(lldp_neighbours)} LLDP neighbour(s)")
        if merged:
            logger.info(f"{ip}: {len(merged)} neighbour(s) total (CDP+LLDP)")

        return merged
