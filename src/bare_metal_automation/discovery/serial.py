"""Serial number and platform extraction from 'show inventory' output.

Handles Cisco IOS / IOS-XE / ASA output formats and provides a mapping
from hardware Product IDs (PIDs) to the BMA platform identifiers.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Map PID prefixes → BMA platform strings
# Ordered longest-match first to avoid ambiguity.
_PID_PLATFORM_MAP: list[tuple[str, str]] = [
    ("C9300X", "cisco_iosxe"),
    ("C9300",  "cisco_iosxe"),
    ("C9500",  "cisco_iosxe"),
    ("C9200",  "cisco_iosxe"),
    ("WS-C",   "cisco_ios"),     # Catalyst classic (3850, 3750, etc.)
    ("C6800",  "cisco_ios"),
    ("ISR",    "cisco_iosxe"),
    ("ASR",    "cisco_iosxe"),
    ("ASA",    "cisco_asa"),
    ("FPR",    "cisco_ftd"),
    ("FTD",    "cisco_ftd"),
]


def pid_to_platform(pid: str) -> str | None:
    """Map a Cisco hardware PID to a BMA platform string, or None if unknown."""
    if not pid:
        return None
    pid_upper = pid.upper()
    for prefix, platform in _PID_PLATFORM_MAP:
        if pid_upper.startswith(prefix):
            return platform
    logger.debug(f"Unknown PID '{pid}' — platform not mapped")
    return None


def parse_inventory(output: str) -> tuple[str | None, str | None]:
    """Extract the chassis serial number and PID from 'show inventory' output.

    Returns ``(serial, pid)``.  Both may be None if parsing fails.

    The chassis entry is typically the first NAME block, e.g.::

        NAME: "Chassis", DESCR: "Cisco Catalyst 9300-48P"
        PID: C9300-48P  , VID: V01 , SN: FCW2345A0BC
    """
    serial: str | None = None
    pid: str | None = None

    # Look for the first SN: in the output (chassis serial)
    sn_match = re.search(r"\bSN:\s*(\S+)", output)
    if sn_match:
        serial = sn_match.group(1)

    # Look for the first PID:
    pid_match = re.search(r"\bPID:\s*(\S+)", output)
    if pid_match:
        pid = pid_match.group(1)

    return serial, pid


def collect_serial(
    connection,  # Netmiko ConnectHandler
    ssh_timeout: int = 30,
) -> tuple[str | None, str | None]:
    """Run 'show inventory' over an active SSH connection and parse the result.

    Returns ``(serial, pid)``.
    """
    try:
        output = connection.send_command(
            "show inventory", read_timeout=ssh_timeout
        )
        serial, pid = parse_inventory(output)
        if serial:
            logger.debug(f"Serial: {serial}, PID: {pid}")
        else:
            logger.warning("Could not extract serial from 'show inventory' output")
        return serial, pid
    except Exception as e:
        logger.warning(f"'show inventory' command failed: {e}")
        return None, None
