"""NetBox API client — thin wrapper around pynetbox."""

from __future__ import annotations

import logging
import re
from typing import Any

import pynetbox

logger = logging.getLogger(__name__)


# ── Custom exceptions (operator-friendly messages) ────────────────────────


class NetBoxConnectionError(Exception):
    """Cannot reach the NetBox server."""


class NetBoxAuthError(Exception):
    """NetBox API token is invalid or expired."""


class NetBoxNotFoundError(Exception):
    """Requested resource not found in NetBox."""


class NetBoxMappingError(Exception):
    """Device data in NetBox is incomplete or invalid."""


# ── Client ────────────────────────────────────────────────────────────────


class NetBoxClient:
    """Query NetBox for deployable node data.

    Wraps pynetbox to provide BMA-specific queries with
    operator-friendly error handling.
    """

    def __init__(
        self,
        url: str,
        token: str,
        timeout: int = 30,
    ) -> None:
        self.url = url.rstrip("/")
        try:
            self.api = pynetbox.api(self.url, token=token)
            self.api.http_session.timeout = timeout
        except Exception as e:
            raise NetBoxConnectionError(
                f"Cannot connect to NetBox at {self.url}: {e}",
            ) from e

    def ping(self) -> dict[str, Any]:
        """Test connectivity and authentication.

        Returns NetBox status info on success.
        Raises NetBoxConnectionError or NetBoxAuthError on failure.
        """
        try:
            status = self.api.status()
            logger.info(
                "Connected to NetBox %s (version %s)",
                self.url,
                status.get("netbox-version", "unknown"),
            )
            return status
        except pynetbox.RequestError as e:
            if "403" in str(e) or "401" in str(e):
                raise NetBoxAuthError(
                    "NetBox authentication failed — "
                    "check that your API token is valid",
                ) from e
            raise NetBoxConnectionError(
                f"Cannot reach NetBox at {self.url}: {e}",
            ) from e
        except Exception as e:
            raise NetBoxConnectionError(
                f"Cannot reach NetBox at {self.url}: {e}",
            ) from e

    def list_node_tags(
        self, pattern: str = r"^D\d+$",
    ) -> list[dict[str, Any]]:
        """Return all tags matching the node pattern.

        Default pattern matches D001, D002, etc.
        Returns list of dicts with 'name', 'slug', 'description'.
        """
        try:
            all_tags = self.api.extras.tags.all()
        except Exception as e:
            raise NetBoxConnectionError(
                f"Failed to fetch tags from NetBox: {e}",
            ) from e

        regex = re.compile(pattern)
        node_tags = []
        for tag in all_tags:
            if regex.match(tag.name):
                # Count devices with this tag
                devices = self.api.dcim.devices.filter(tag=tag.slug)
                device_count = len(list(devices))
                node_tags.append({
                    "name": tag.name,
                    "slug": tag.slug,
                    "description": tag.description or "",
                    "device_count": device_count,
                })

        return sorted(node_tags, key=lambda t: t["name"])

    def get_devices_by_tag(
        self, tag: str,
    ) -> list[Any]:
        """Return all devices with the specified tag.

        Args:
            tag: Tag slug (e.g. 'd001').

        Returns:
            List of pynetbox Device records.

        Raises:
            NetBoxNotFoundError if no devices found.
        """
        try:
            devices = list(self.api.dcim.devices.filter(tag=tag))
        except Exception as e:
            raise NetBoxConnectionError(
                f"Failed to query devices for tag '{tag}': {e}",
            ) from e

        if not devices:
            raise NetBoxNotFoundError(
                f"No devices found with tag '{tag}' in NetBox",
            )

        logger.info(
            "Found %d devices with tag '%s'",
            len(devices), tag,
        )
        return devices

    def get_config_context(
        self, device_id: int,
    ) -> dict[str, Any]:
        """Return the merged config context for a device.

        Config contexts in NetBox are layered (global, site, role,
        device) and merged. This returns the final merged result.
        """
        try:
            device = self.api.dcim.devices.get(device_id)
            if device is None:
                raise NetBoxNotFoundError(
                    f"Device {device_id} not found",
                )
            # pynetbox includes config_context in device detail
            return dict(device.config_context or {})
        except NetBoxNotFoundError:
            raise
        except Exception as e:
            raise NetBoxConnectionError(
                f"Failed to fetch config context for "
                f"device {device_id}: {e}",
            ) from e

    def get_device_ips(
        self, device_id: int,
    ) -> list[dict[str, Any]]:
        """Return all IP addresses assigned to a device."""
        try:
            ips = list(
                self.api.ipam.ip_addresses.filter(device_id=device_id),
            )
            return [
                {
                    "address": str(ip.address),
                    "interface": str(ip.assigned_object) if ip.assigned_object else "",
                    "role": ip.role or "",
                    "status": str(ip.status),
                }
                for ip in ips
            ]
        except Exception as e:
            raise NetBoxConnectionError(
                f"Failed to fetch IPs for device {device_id}: {e}",
            ) from e

    def get_prefixes_by_tag(
        self, tag: str,
    ) -> list[dict[str, Any]]:
        """Return IPAM prefixes tagged with the node tag."""
        try:
            prefixes = list(
                self.api.ipam.prefixes.filter(tag=tag),
            )
            return [
                {
                    "prefix": str(p.prefix),
                    "description": p.description or "",
                    "role": str(p.role) if p.role else "",
                    "vlan": (
                        {"id": p.vlan.id, "vid": p.vlan.vid}
                        if p.vlan
                        else None
                    ),
                }
                for p in prefixes
            ]
        except Exception as e:
            raise NetBoxConnectionError(
                f"Failed to fetch prefixes for tag '{tag}': {e}",
            ) from e

    def get_vlans_by_tag(
        self, tag: str,
    ) -> list[dict[str, Any]]:
        """Return VLANs tagged with the node tag."""
        try:
            vlans = list(self.api.ipam.vlans.filter(tag=tag))
            return [
                {
                    "vid": v.vid,
                    "name": v.name,
                    "description": v.description or "",
                    "role": str(v.role) if v.role else "",
                }
                for v in vlans
            ]
        except Exception as e:
            raise NetBoxConnectionError(
                f"Failed to fetch VLANs for tag '{tag}': {e}",
            ) from e
