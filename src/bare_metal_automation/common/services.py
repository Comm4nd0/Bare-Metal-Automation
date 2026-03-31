"""Laptop service status checks via systemd."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class ServiceStatus:
    name: str
    display_name: str
    icon: str
    status: str  # "active", "inactive", "failed", "unknown"
    detail: str


def _systemctl_is_active(service: str) -> str:
    """Return systemctl is-active output or 'unknown' on any error."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return "unknown"


def _check_any(*service_names: str) -> str:
    """Return 'active' if any candidate is active, else the last non-unknown result."""
    last = "unknown"
    for name in service_names:
        status = _systemctl_is_active(name)
        if status == "active":
            return "active"
        if status in ("inactive", "failed"):
            last = status
    return last


def get_laptop_services() -> list[ServiceStatus]:
    """Return status of deployment laptop services."""
    return [
        ServiceStatus(
            name="dhcp",
            display_name="DHCP",
            icon="bi-broadcast",
            status=_check_any("dnsmasq", "isc-dhcp-server"),
            detail="Bootstrap lease server",
        ),
        ServiceStatus(
            name="tftp",
            display_name="TFTP",
            icon="bi-arrow-left-right",
            status=_check_any("tftpd-hpa", "dnsmasq"),
            detail="Firmware / PXE transfers",
        ),
        ServiceStatus(
            name="http",
            display_name="HTTP",
            icon="bi-hdd-network",
            status=_check_any("nginx", "apache2"),
            detail="ISO / config file server",
        ),
        ServiceStatus(
            name="ssh",
            display_name="SSH",
            icon="bi-terminal",
            status=_check_any("sshd", "ssh"),
            detail="Device management access",
        ),
    ]
