"""NetBox data mapper — transforms NetBox records to BMA inventory format.

Pure functions with no I/O, easily testable. Takes pynetbox objects
and config context dicts, returns BMA-compatible spec dicts.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Role and platform mappings ────────────────────────────────────────────

# NetBox device_role slugs → BMA role values
ROLE_MAP: dict[str, str] = {
    "core-switch": "core-switch",
    "access-switch": "access-switch",
    "distribution-switch": "distribution-switch",
    "border-router": "border-router",
    "perimeter-firewall": "perimeter-firewall",
    "compute-node": "compute-node",
    "management-server": "management-server",
    "ntp-server": "ntp-server",
    # Common NetBox alternatives
    "switch": "access-switch",
    "router": "border-router",
    "firewall": "perimeter-firewall",
    "server": "compute-node",
}

# NetBox device_type slugs or manufacturer → BMA platform values
PLATFORM_MAP: dict[str, str] = {
    "cisco_ios": "cisco_ios",
    "cisco_iosxe": "cisco_iosxe",
    "cisco_asa": "cisco_asa",
    "cisco_ftd": "cisco_ftd",
    "hpe_dl325_gen10": "hpe_dl325_gen10",
    "hpe_dl360_gen10": "hpe_dl360_gen10",
    "hpe_dl380_gen10": "hpe_dl380_gen10",
    "meinberg_lantime": "meinberg_lantime",
    # Common NetBox alternatives
    "cisco-ios": "cisco_ios",
    "cisco-iosxe": "cisco_iosxe",
    "cisco-asa": "cisco_asa",
    "hpe-dl325-gen10": "hpe_dl325_gen10",
    "hpe-dl360-gen10": "hpe_dl360_gen10",
    "hpe-dl380-gen10": "hpe_dl380_gen10",
    "meinberg-lantime": "meinberg_lantime",
}


# ── Device mapping ────────────────────────────────────────────────────────


def map_device_to_spec(
    device: Any,
    config_context: dict[str, Any],
    ip_addresses: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Map a NetBox device to a BMA (serial, spec_dict) pair.

    Args:
        device: pynetbox Device record.
        config_context: Merged config context dict from NetBox.
        ip_addresses: List of IP address dicts for this device.

    Returns:
        Tuple of (serial_number, spec_dict) matching inventory.yaml format.

    Raises:
        ValueError: If required fields are missing.
    """
    serial = device.serial
    if not serial:
        raise ValueError(
            f"Device '{device.name}' has no serial number in NetBox",
        )

    # Map role
    role_slug = str(device.device_role.slug) if device.device_role else ""
    role = ROLE_MAP.get(role_slug)
    if not role:
        raise ValueError(
            f"Device '{device.name}' has unmapped role "
            f"'{role_slug}' — add it to ROLE_MAP",
        )

    # Map platform
    platform_slug = (
        str(device.platform.slug)
        if device.platform
        else ""
    )
    platform = PLATFORM_MAP.get(platform_slug)
    if not platform:
        # Try device_type slug as fallback
        type_slug = (
            str(device.device_type.slug)
            if device.device_type
            else ""
        )
        platform = PLATFORM_MAP.get(type_slug)
    if not platform:
        raise ValueError(
            f"Device '{device.name}' has unmapped platform "
            f"'{platform_slug}' — add it to PLATFORM_MAP",
        )

    # Extract management IP from ip_addresses
    management_ip = ""
    management_subnet = ""
    if ip_addresses:
        # Use first IP, or primary if flagged
        primary_ip = ip_addresses[0]
        addr = primary_ip["address"]
        if "/" in addr:
            ip_part, prefix_len = addr.split("/")
            management_ip = ip_part
            management_subnet = _prefix_to_netmask(int(prefix_len))
        else:
            management_ip = addr

    # Build spec dict — start with config context, overlay core fields
    spec: dict[str, Any] = {}

    # Config context provides all the deep config (BIOS, RAID, iLO, NTP, etc.)
    spec.update(config_context)

    # Core fields (override config context if present)
    spec["role"] = role
    spec["hostname"] = device.name
    spec["platform"] = platform

    if management_ip:
        spec["management_ip"] = management_ip
    if management_subnet:
        spec["management_subnet"] = management_subnet

    # Template — from config context or default based on role
    if "template" not in spec:
        spec["template"] = _default_template(role)

    return serial, spec


def map_deployment_metadata(
    tag: str,
    prefixes: list[dict[str, Any]],
    vlans: list[dict[str, Any]],
    laptop_ip: str = "",
) -> dict[str, Any]:
    """Map NetBox IPAM data to BMA deployment-level metadata.

    Args:
        tag: Node tag name (e.g. "D001").
        prefixes: List of prefix dicts from NetBox IPAM.
        vlans: List of VLAN dicts from NetBox IPAM.
        laptop_ip: Override for laptop IP (from config context or env).

    Returns:
        Dict with name, bootstrap_subnet, laptop_ip, management_vlan.
    """
    # Find bootstrap prefix (largest subnet, or one marked as bootstrap)
    bootstrap_subnet = ""
    for prefix in prefixes:
        desc = (prefix.get("description") or "").lower()
        role = (prefix.get("role") or "").lower()
        if "bootstrap" in desc or "bootstrap" in role:
            bootstrap_subnet = prefix["prefix"]
            break
    if not bootstrap_subnet and prefixes:
        # Fall back to first prefix
        bootstrap_subnet = prefixes[0]["prefix"]

    # Find management VLAN
    management_vlan = 0
    for vlan in vlans:
        desc = (vlan.get("description") or "").lower()
        name = (vlan.get("name") or "").lower()
        if "management" in desc or "management" in name or "mgmt" in name:
            management_vlan = vlan["vid"]
            break
    if management_vlan == 0 and vlans:
        # Fall back to first VLAN
        management_vlan = vlans[0]["vid"]

    # Derive laptop IP from bootstrap subnet if not provided
    if not laptop_ip and bootstrap_subnet:
        laptop_ip = _derive_laptop_ip(bootstrap_subnet)

    return {
        "name": tag,
        "bootstrap_subnet": bootstrap_subnet,
        "laptop_ip": laptop_ip,
        "management_vlan": management_vlan,
    }


# ── Helpers ───────────────────────────────────────────────────────────────


def _prefix_to_netmask(prefix_len: int) -> str:
    """Convert CIDR prefix length to dotted-decimal netmask."""
    mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    return ".".join(str((mask >> (8 * i)) & 0xFF) for i in range(3, -1, -1))


def _derive_laptop_ip(subnet: str) -> str:
    """Derive a laptop IP from the bootstrap subnet.

    Uses the last usable address in the subnet
    (e.g. 10.255.0.0/16 → 10.255.255.1).
    """
    if "/" not in subnet:
        return ""
    network, prefix_str = subnet.split("/")
    octets = [int(o) for o in network.split(".")]

    # Use .255.1 for /16, .1 for /24, etc.
    prefix_len = int(prefix_str)
    if prefix_len <= 16:
        octets[2] = 255
        octets[3] = 1
    elif prefix_len <= 24:
        octets[3] = 1
    else:
        octets[3] = 254

    return ".".join(str(o) for o in octets)


def _default_template(role: str) -> str:
    """Return a default template path based on device role."""
    templates: dict[str, str] = {
        "core-switch": "switches/core.j2",
        "access-switch": "switches/access.j2",
        "distribution-switch": "switches/distribution.j2",
        "border-router": "routers/border.j2",
        "perimeter-firewall": "firewalls/perimeter.j2",
        "compute-node": "servers/compute.j2",
        "management-server": "servers/management.j2",
        "ntp-server": "ntp/lantime.j2",
    }
    return templates.get(role, f"{role}.j2")
