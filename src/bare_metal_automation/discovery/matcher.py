"""Inventory matcher — reconcile discovered devices against the expected inventory.

Compares the serials found during discovery with the list of expected devices
in the deployment inventory, assigns roles/hostnames, and (when running inside
the Django context) updates ``Device`` ORM records so the dashboard reflects
real hardware state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bare_metal_automation.models import (
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
)

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """Summary of the inventory match operation."""

    matched: dict[str, str] = field(default_factory=dict)    # serial → role
    unmatched_serials: list[str] = field(default_factory=list)  # seen but not in inventory
    missing_serials: list[str] = field(default_factory=list)    # in inventory but not seen

    @property
    def is_complete(self) -> bool:
        """True when every expected device was found and none are unknown."""
        return not self.missing_serials

    @property
    def has_unknown_devices(self) -> bool:
        return bool(self.unmatched_serials)

    def __str__(self) -> str:
        lines = [
            f"Matched: {len(self.matched)}",
            f"Unknown: {len(self.unmatched_serials)}",
            f"Missing: {len(self.missing_serials)}",
        ]
        if self.unmatched_serials:
            lines.append("  Unknown serials: " + ", ".join(self.unmatched_serials))
        if self.missing_serials:
            lines.append("  Missing serials: " + ", ".join(self.missing_serials))
        return "\n".join(lines)


class InventoryMatcher:
    """Match discovered devices to their intended roles and update the DB."""

    def __init__(self, inventory: DeploymentInventory) -> None:
        self.inventory = inventory

    def match(
        self,
        discovered: dict[str, DiscoveredDevice],
    ) -> MatchResult:
        """Reconcile *discovered* (ip → device) against the inventory.

        Mutates each ``DiscoveredDevice`` in place:
          - Sets ``role``, ``intended_hostname``, ``template_path``,
            ``device_platform``, and ``state`` (→ IDENTIFIED).
        """
        result = MatchResult()

        # Build reverse lookup: serial → device
        serial_to_device: dict[str, DiscoveredDevice] = {}
        for device in discovered.values():
            if device.serial:
                serial_to_device[device.serial] = device

        # Check each expected device
        for serial in self.inventory.expected_serials:
            if serial in serial_to_device:
                device = serial_to_device[serial]
                spec = self.inventory.get_device_spec(serial) or {}
                device.role = spec.get("role")
                device.intended_hostname = spec.get("hostname")
                device.template_path = spec.get("template")
                device.device_platform = spec.get("platform")
                device.state = DeviceState.IDENTIFIED
                result.matched[serial] = device.role or "unknown"
                logger.info(
                    f"Matched {serial} → "
                    f"{device.intended_hostname} ({device.role})"
                )
            else:
                result.missing_serials.append(serial)
                spec = self.inventory.devices.get(serial, {})
                logger.warning(
                    f"Missing device: {spec.get('hostname', serial)} "
                    f"(serial {serial})"
                )

        # Flag devices seen on the network that aren't in the inventory
        for serial, device in serial_to_device.items():
            if serial not in self.inventory.expected_serials:
                result.unmatched_serials.append(serial)
                logger.warning(
                    f"Unknown device at {device.ip}: serial {serial}"
                )

        return result

    def update_db(
        self,
        deployment_id: int,
        discovered: dict[str, DiscoveredDevice],
    ) -> None:
        """Create / update ``Device`` ORM records for all discovered devices.

        Designed to be called after :meth:`match` has been run so that
        ``intended_hostname`` and ``role`` are populated.

        Safe to call when not inside a Django context — silently no-ops if
        the Django ORM is unavailable.
        """
        try:
            from django.db import close_old_connections

            from bare_metal_automation.dashboard.models import Deployment, Device

            close_old_connections()
            try:
                deployment = Deployment.objects.get(pk=deployment_id)
            except Deployment.DoesNotExist:
                logger.warning(
                    f"Deployment {deployment_id} not found — skipping DB update"
                )
                return

            for ip, device in discovered.items():
                if device.serial is None:
                    continue

                Device.objects.update_or_create(
                    deployment=deployment,
                    serial=device.serial,
                    defaults={
                        "ip": ip,
                        "mac": device.mac or "",
                        "platform": device.device_platform or "",
                        "hostname": device.hostname or "",
                        "intended_hostname": device.intended_hostname or "",
                        "role": device.role or "",
                        "state": device.state.value,
                        "bfs_depth": device.bfs_depth,
                        "config_order": device.config_order,
                    },
                )
                logger.debug(
                    f"DB Device record upserted: {device.serial} "
                    f"({device.intended_hostname})"
                )

        except ImportError:
            # Running outside Django — no-op
            pass
        except Exception as e:
            logger.error(f"Failed to update Device DB records: {e}")

    def _log_to_db(
        self,
        deployment_id: int,
        level: str,
        message: str,
        phase: str = "discovery",
    ) -> None:
        """Write a log entry to the DeploymentLog table (best-effort)."""
        try:
            from bare_metal_automation.dashboard.models import Deployment, DeploymentLog

            dep = Deployment.objects.get(pk=deployment_id)
            DeploymentLog.objects.create(
                deployment=dep,
                level=level,
                phase=phase,
                message=message,
            )
        except Exception:
            pass
