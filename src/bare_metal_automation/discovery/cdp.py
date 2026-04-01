"""CDP neighbour collector — SSH into devices, run show cdp neighbors detail."""

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
