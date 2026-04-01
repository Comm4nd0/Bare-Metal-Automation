"""Firmware compliance checker — audit fleet firmware against the catalog.

Scans devices (via SSH or cached state) and compares their running
firmware versions against the recommended versions in the firmware
catalog.  Produces a compliance report showing which devices are
up-to-date, which need upgrades, and which have unsafe upgrade paths.

Usage::

    from bare_metal_automation.firmware.catalog import FirmwareCatalog
    from bare_metal_automation.firmware.compliance import ComplianceChecker

    catalog = FirmwareCatalog.from_yaml("configs/firmware/catalog.yaml")
    checker = ComplianceChecker(catalog=catalog, ssh_timeout=30)

    report = checker.check_devices(devices)
    report.print_summary()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bare_metal_automation.firmware.catalog import FirmwareCatalog
from bare_metal_automation.models import DiscoveredDevice

logger = logging.getLogger(__name__)


class ComplianceStatus(Enum):
    """Firmware compliance status for a single device."""

    COMPLIANT = "compliant"
    UPGRADE_AVAILABLE = "upgrade_available"
    UPGRADE_BLOCKED = "upgrade_blocked"
    UNKNOWN = "unknown"
    UNREACHABLE = "unreachable"


@dataclass
class DeviceComplianceResult:
    """Firmware compliance status for one device."""

    hostname: str
    ip: str
    serial: str
    platform: str
    current_version: str
    recommended_version: str
    status: ComplianceStatus
    upgrade_safe: bool = True
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "ip": self.ip,
            "serial": self.serial,
            "platform": self.platform,
            "current_version": self.current_version,
            "recommended_version": self.recommended_version,
            "status": self.status.value,
            "upgrade_safe": self.upgrade_safe,
            "message": self.message,
        }


@dataclass
class ComplianceReport:
    """Aggregated firmware compliance report for a set of devices."""

    results: list[DeviceComplianceResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def compliant_count(self) -> int:
        return sum(1 for r in self.results if r.status == ComplianceStatus.COMPLIANT)

    @property
    def upgrade_available_count(self) -> int:
        return sum(
            1 for r in self.results if r.status == ComplianceStatus.UPGRADE_AVAILABLE
        )

    @property
    def blocked_count(self) -> int:
        return sum(
            1 for r in self.results if r.status == ComplianceStatus.UPGRADE_BLOCKED
        )

    @property
    def unreachable_count(self) -> int:
        return sum(
            1 for r in self.results if r.status == ComplianceStatus.UNREACHABLE
        )

    @property
    def compliance_percentage(self) -> float:
        if not self.results:
            return 100.0
        return (self.compliant_count / self.total) * 100.0

    def devices_needing_upgrade(self) -> list[DeviceComplianceResult]:
        """Return devices that have a safe upgrade path available."""
        return [
            r
            for r in self.results
            if r.status == ComplianceStatus.UPGRADE_AVAILABLE and r.upgrade_safe
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "compliant": self.compliant_count,
            "upgrade_available": self.upgrade_available_count,
            "blocked": self.blocked_count,
            "unreachable": self.unreachable_count,
            "compliance_percentage": round(self.compliance_percentage, 1),
            "devices": [r.to_dict() for r in self.results],
        }


class ComplianceChecker:
    """Check device firmware versions against the catalog."""

    def __init__(
        self,
        catalog: FirmwareCatalog,
        ssh_timeout: int = 30,
    ) -> None:
        self.catalog = catalog
        self.ssh_timeout = ssh_timeout

    def check_devices(
        self,
        devices: list[DiscoveredDevice],
        live_check: bool = True,
    ) -> ComplianceReport:
        """Check firmware compliance for a list of devices.

        Args:
            devices: Devices to check.
            live_check: If True, SSH into each device to get the current
                firmware version.  If False, skip unreachable devices
                and use cached info where available.

        Returns:
            A ComplianceReport with per-device results.
        """
        report = ComplianceReport()

        for device in devices:
            result = self._check_device(device, live_check=live_check)
            report.results.append(result)

        logger.info(
            "Compliance check complete: %d/%d compliant (%.1f%%)",
            report.compliant_count,
            report.total,
            report.compliance_percentage,
        )
        return report

    def check_device(
        self,
        device: DiscoveredDevice,
        live_check: bool = True,
    ) -> DeviceComplianceResult:
        """Check firmware compliance for a single device."""
        return self._check_device(device, live_check=live_check)

    def _check_device(
        self,
        device: DiscoveredDevice,
        live_check: bool,
    ) -> DeviceComplianceResult:
        """Internal: check one device against the catalog."""
        hostname = device.intended_hostname or device.hostname or device.ip
        platform = (
            device.device_platform.value
            if device.device_platform
            else (device.platform or "")
        )

        recommended = self.catalog.get_recommended(platform)
        recommended_version = recommended.version if recommended else ""

        # Get current version
        current_version = ""
        if live_check:
            current_version = self._get_live_version(device)
            if not current_version:
                return DeviceComplianceResult(
                    hostname=hostname,
                    ip=device.ip,
                    serial=device.serial or "",
                    platform=platform,
                    current_version="",
                    recommended_version=recommended_version,
                    status=ComplianceStatus.UNREACHABLE,
                    message=f"Cannot connect to {device.ip}",
                )

        if not recommended:
            return DeviceComplianceResult(
                hostname=hostname,
                ip=device.ip,
                serial=device.serial or "",
                platform=platform,
                current_version=current_version,
                recommended_version="",
                status=ComplianceStatus.UNKNOWN,
                message=f"No recommended firmware in catalog for {platform}",
            )

        # Compare
        if current_version == recommended.version:
            return DeviceComplianceResult(
                hostname=hostname,
                ip=device.ip,
                serial=device.serial or "",
                platform=platform,
                current_version=current_version,
                recommended_version=recommended.version,
                status=ComplianceStatus.COMPLIANT,
                message="Running recommended firmware",
            )

        # Upgrade available — check if safe
        safe = recommended.is_upgrade_safe(current_version)
        if safe:
            return DeviceComplianceResult(
                hostname=hostname,
                ip=device.ip,
                serial=device.serial or "",
                platform=platform,
                current_version=current_version,
                recommended_version=recommended.version,
                status=ComplianceStatus.UPGRADE_AVAILABLE,
                upgrade_safe=True,
                message=(
                    f"Upgrade available: {current_version} -> "
                    f"{recommended.version}"
                ),
            )

        return DeviceComplianceResult(
            hostname=hostname,
            ip=device.ip,
            serial=device.serial or "",
            platform=platform,
            current_version=current_version,
            recommended_version=recommended.version,
            status=ComplianceStatus.UPGRADE_BLOCKED,
            upgrade_safe=False,
            message=(
                f"Upgrade to {recommended.version} blocked — "
                f"current {current_version} below min_version "
                f"{recommended.min_version}"
            ),
        )

    def _get_live_version(self, device: DiscoveredDevice) -> str:
        """SSH into the device and get the running firmware version."""
        try:
            from netmiko import ConnectHandler

            device_type_map = {
                "cisco_ios": "cisco_ios",
                "cisco_iosxe": "cisco_xe",
                "cisco_asa": "cisco_asa",
                "cisco_ftd": "cisco_ftd",
            }

            platform = (
                device.device_platform.value
                if device.device_platform
                else "cisco_ios"
            )
            netmiko_type = device_type_map.get(platform, "cisco_ios")

            conn = ConnectHandler(
                device_type=netmiko_type,
                host=device.ip,
                username="cisco",
                password="cisco",
                timeout=self.ssh_timeout,
            )

            output = conn.send_command("show version")
            conn.disconnect()

            for line in output.splitlines():
                line_lower = line.lower()
                if "version" in line_lower and (
                    "ios" in line_lower
                    or "adaptive security" in line_lower
                    or "software" in line_lower
                ):
                    parts = line.split("Version")
                    if len(parts) > 1:
                        return parts[1].strip().split(",")[0].split()[0]

        except Exception as e:
            logger.warning(
                "Cannot get firmware version from %s: %s", device.ip, e
            )

        return ""
