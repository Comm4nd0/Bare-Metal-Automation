"""Cisco platform constants — PID maps, boot commands, flash paths.

Centralises vendor-specific constants that were previously scattered
across ``configurator/firmware.py`` and ``discovery/serial.py``.
"""

from __future__ import annotations

# Map PID prefixes -> BMA platform strings (longest-match first)
PID_PLATFORM_MAP: list[tuple[str, str]] = [
    ("C9300X", "cisco_iosxe"),
    ("C9300",  "cisco_iosxe"),
    ("C9500",  "cisco_iosxe"),
    ("C9200",  "cisco_iosxe"),
    ("WS-C",   "cisco_ios"),
    ("C6800",  "cisco_ios"),
    ("ISR",    "cisco_iosxe"),
    ("ASR",    "cisco_iosxe"),
    ("ASA",    "cisco_asa"),
    ("FPR",    "cisco_ftd"),
    ("FTD",    "cisco_ftd"),
]

# BMA platform string -> Netmiko device_type
NETMIKO_DEVICE_TYPE: dict[str, str] = {
    "cisco_ios": "cisco_ios",
    "cisco_iosxe": "cisco_xe",
    "cisco_asa": "cisco_asa",
    "cisco_ftd": "cisco_ftd",
}

# Flash filesystem name per platform
PLATFORM_FLASH: dict[str, str] = {
    "cisco_ios": "flash:",
    "cisco_iosxe": "bootflash:",
    "cisco_asa": "disk0:",
    "cisco_ftd": "disk0:",
}

# Boot variable command templates
PLATFORM_BOOT_CMD: dict[str, str] = {
    "cisco_ios": "boot system flash:{filename}",
    "cisco_iosxe": "boot system bootflash:{filename}",
    "cisco_asa": "boot system disk0:/{filename}",
    "cisco_ftd": "boot system disk0:/{filename}",
}

# Firmware transfer method per platform
PLATFORM_TRANSFER_METHOD: dict[str, str] = {
    "cisco_ios": "scp",
    "cisco_iosxe": "scp",
    "cisco_asa": "scp",
    "cisco_ftd": "scp",
}


def pid_to_platform(pid: str) -> str | None:
    """Map a Cisco hardware PID to a BMA platform string, or None."""
    if not pid:
        return None
    pid_upper = pid.upper()
    for prefix, platform in PID_PLATFORM_MAP:
        if pid_upper.startswith(prefix):
            return platform
    return None


def netmiko_type(platform: str) -> str:
    """Return the Netmiko device_type for a BMA platform string."""
    return NETMIKO_DEVICE_TYPE.get(platform, "cisco_ios")
