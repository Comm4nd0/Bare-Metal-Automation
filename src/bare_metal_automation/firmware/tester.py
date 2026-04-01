"""Firmware upgrade tester — validate that a firmware upgrade doesn't break configs.

The test runner implements a safe upgrade-and-verify cycle:

1. **Snapshot** — capture current firmware version and running config
2. **Upgrade** — push the new firmware image and reload
3. **Re-apply config** — push the saved configuration back to the device
4. **Validate** — run the role-specific health checks (STP, OSPF, trunks, etc.)
5. **Report** — collect pass/fail results with detailed findings

If validation fails, the runner can optionally trigger a rollback to the
previous firmware version (for platforms that support it).

Usage::

    from bare_metal_automation.firmware.catalog import FirmwareCatalog
    from bare_metal_automation.firmware.tester import FirmwareTestRunner

    catalog = FirmwareCatalog.from_yaml("configs/firmware/catalog.yaml")
    runner = FirmwareTestRunner(catalog=catalog, inventory=inventory)

    # Test a single device
    result = runner.test_upgrade(device, target_version="17.09.04a")

    # Test all devices that need upgrades
    results = runner.test_all(devices)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bare_metal_automation.configurator.validator import ConfigValidator, ValidationResult
from bare_metal_automation.firmware.catalog import FirmwareCatalog, FirmwareEntry
from bare_metal_automation.models import (
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
)

logger = logging.getLogger(__name__)


class UpgradeTestPhase(Enum):
    """Phases of a firmware upgrade test."""

    SNAPSHOT = "snapshot"
    PRE_VALIDATION = "pre_validation"
    UPGRADE = "upgrade"
    CONFIG_REAPPLY = "config_reapply"
    POST_VALIDATION = "post_validation"
    ROLLBACK = "rollback"
    COMPLETE = "complete"


class UpgradeTestOutcome(Enum):
    """Overall outcome of a firmware test."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"
    ERROR = "error"


@dataclass
class FirmwareTestResult:
    """Result of a firmware upgrade test for a single device."""

    device_hostname: str
    device_ip: str
    device_serial: str
    platform: str
    previous_version: str = ""
    target_version: str = ""
    final_version: str = ""
    outcome: UpgradeTestOutcome = UpgradeTestOutcome.SKIPPED
    phase_reached: UpgradeTestPhase = UpgradeTestPhase.SNAPSHOT
    pre_validation: ValidationResult | None = None
    post_validation: ValidationResult | None = None
    config_reapply_success: bool = False
    duration_seconds: float = 0.0
    error_message: str = ""
    findings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.outcome == UpgradeTestOutcome.PASSED

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API / dashboard consumption."""
        return {
            "device_hostname": self.device_hostname,
            "device_ip": self.device_ip,
            "device_serial": self.device_serial,
            "platform": self.platform,
            "previous_version": self.previous_version,
            "target_version": self.target_version,
            "final_version": self.final_version,
            "outcome": self.outcome.value,
            "phase_reached": self.phase_reached.value,
            "config_reapply_success": self.config_reapply_success,
            "duration_seconds": self.duration_seconds,
            "error_message": self.error_message,
            "findings": self.findings,
            "pre_validation_passed": (
                self.pre_validation.passed if self.pre_validation else None
            ),
            "post_validation_passed": (
                self.post_validation.passed if self.post_validation else None
            ),
        }


class FirmwareTestRunner:
    """Runs firmware upgrade tests: upgrade, re-apply config, validate.

    The runner uses the existing FirmwareConfigurator for the actual
    upgrade mechanics and ConfigValidator for health checks, composing
    them into a test pipeline with snapshot/restore semantics.
    """

    def __init__(
        self,
        catalog: FirmwareCatalog,
        inventory: DeploymentInventory,
        firmware_dir: str = "configs/firmware",
        ssh_timeout: int = 30,
        management_vlan: int = 0,
        rollback_on_failure: bool = True,
    ) -> None:
        self.catalog = catalog
        self.inventory = inventory
        self.firmware_dir = firmware_dir
        self.ssh_timeout = ssh_timeout
        self.management_vlan = management_vlan
        self.rollback_on_failure = rollback_on_failure

    def test_upgrade(
        self,
        device: DiscoveredDevice,
        target_version: str | None = None,
    ) -> FirmwareTestResult:
        """Run a full firmware upgrade test on a single device.

        Args:
            device: The device to test.
            target_version: Specific version to test.  If None, the
                catalog's recommended version for the platform is used.

        Returns:
            A FirmwareTestResult with the outcome and detailed findings.
        """
        start_time = time.monotonic()
        platform = (
            device.device_platform.value
            if device.device_platform
            else (device.platform or "")
        )

        result = FirmwareTestResult(
            device_hostname=device.intended_hostname or device.hostname or device.ip,
            device_ip=device.ip,
            device_serial=device.serial or "",
            platform=platform,
        )

        # Resolve target firmware
        target_entry = self._resolve_target(platform, target_version)
        if target_entry is None:
            result.outcome = UpgradeTestOutcome.SKIPPED
            result.error_message = (
                f"No {'recommended ' if not target_version else ''}firmware "
                f"found for platform {platform}"
                + (f" version {target_version}" if target_version else "")
            )
            result.findings.append(result.error_message)
            result.duration_seconds = time.monotonic() - start_time
            return result

        result.target_version = target_entry.version
        logger.info(
            "%s: Starting firmware upgrade test -> %s",
            result.device_hostname,
            target_entry.version,
        )

        try:
            # Phase 1: Snapshot — capture current state
            result.phase_reached = UpgradeTestPhase.SNAPSHOT
            connection = self._connect(device)
            if connection is None:
                result.outcome = UpgradeTestOutcome.ERROR
                result.error_message = f"Cannot connect to {device.ip}"
                result.duration_seconds = time.monotonic() - start_time
                return result

            current_version = self._get_current_version(connection, device)
            result.previous_version = current_version
            running_config = self._capture_running_config(connection)

            logger.info(
                "%s: Current version: %s, target: %s",
                result.device_hostname,
                current_version,
                target_entry.version,
            )

            # Already at target?
            if current_version == target_entry.version:
                result.outcome = UpgradeTestOutcome.SKIPPED
                result.final_version = current_version
                result.findings.append(
                    f"Already at target version {target_entry.version}"
                )
                connection.disconnect()
                result.duration_seconds = time.monotonic() - start_time
                return result

            # Safe upgrade path?
            if not target_entry.is_upgrade_safe(current_version):
                result.outcome = UpgradeTestOutcome.SKIPPED
                result.error_message = (
                    f"Upgrade from {current_version} to {target_entry.version} "
                    f"not supported (min_version: {target_entry.min_version})"
                )
                result.findings.append(result.error_message)
                connection.disconnect()
                result.duration_seconds = time.monotonic() - start_time
                return result

            # Phase 2: Pre-validation baseline
            result.phase_reached = UpgradeTestPhase.PRE_VALIDATION
            validator = ConfigValidator(management_vlan=self.management_vlan)
            result.pre_validation = validator.validate(device, connection)
            result.findings.append(
                f"Pre-upgrade validation: "
                f"{'PASS' if result.pre_validation.passed else 'FAIL'} "
                f"({len(result.pre_validation.findings)} check(s))"
            )
            connection.disconnect()

            # Phase 3: Firmware upgrade
            result.phase_reached = UpgradeTestPhase.UPGRADE
            device.state = DeviceState.FIRMWARE_UPGRADING
            upgrade_ok = self._perform_upgrade(device, target_entry)

            if not upgrade_ok:
                result.outcome = UpgradeTestOutcome.FAILED
                result.error_message = "Firmware upgrade failed"
                result.findings.append("FAIL: Firmware upgrade did not complete")
                device.state = DeviceState.FAILED
                result.duration_seconds = time.monotonic() - start_time
                return result

            result.findings.append(
                f"Firmware upgraded from {current_version} to {target_entry.version}"
            )

            # Phase 4: Re-apply configuration
            result.phase_reached = UpgradeTestPhase.CONFIG_REAPPLY
            reapply_conn = self._connect(device)
            if reapply_conn is None:
                result.outcome = UpgradeTestOutcome.FAILED
                result.error_message = (
                    "Cannot reconnect after firmware upgrade"
                )
                result.duration_seconds = time.monotonic() - start_time
                return result

            # Verify the new version
            new_version = self._get_current_version(reapply_conn, device)
            result.final_version = new_version

            if new_version != target_entry.version:
                result.outcome = UpgradeTestOutcome.FAILED
                result.error_message = (
                    f"Version mismatch: expected {target_entry.version}, "
                    f"got {new_version}"
                )
                result.findings.append(f"FAIL: {result.error_message}")
                reapply_conn.disconnect()
                result.duration_seconds = time.monotonic() - start_time
                return result

            # Push the saved config back
            config_ok = self._reapply_config(reapply_conn, device, running_config)
            result.config_reapply_success = config_ok

            if config_ok:
                result.findings.append("Configuration re-applied successfully")
            else:
                result.findings.append("WARN: Configuration re-apply had issues")

            # Phase 5: Post-upgrade validation
            result.phase_reached = UpgradeTestPhase.POST_VALIDATION
            # Allow convergence time for protocols (STP, OSPF, HSRP)
            logger.info(
                "%s: Waiting 30s for protocol convergence...",
                result.device_hostname,
            )
            time.sleep(30)

            post_conn = self._connect(device)
            if post_conn is None:
                result.outcome = UpgradeTestOutcome.FAILED
                result.error_message = (
                    "Cannot connect for post-upgrade validation"
                )
                result.duration_seconds = time.monotonic() - start_time
                return result

            result.post_validation = validator.validate(device, post_conn)
            post_conn.disconnect()

            result.findings.append(
                f"Post-upgrade validation: "
                f"{'PASS' if result.post_validation.passed else 'FAIL'} "
                f"({len(result.post_validation.findings)} check(s))"
            )

            # Copy detailed findings
            for finding in result.post_validation.findings:
                result.findings.append(f"  {finding}")

            # Determine outcome
            if result.post_validation.passed and config_ok:
                result.outcome = UpgradeTestOutcome.PASSED
                result.phase_reached = UpgradeTestPhase.COMPLETE
                device.state = DeviceState.FIRMWARE_UPGRADED
                logger.info(
                    "%s: Firmware test PASSED (%s -> %s)",
                    result.device_hostname,
                    current_version,
                    target_entry.version,
                )
            else:
                result.outcome = UpgradeTestOutcome.FAILED
                logger.warning(
                    "%s: Firmware test FAILED — post-validation issues",
                    result.device_hostname,
                )

                # Rollback if configured
                if self.rollback_on_failure:
                    result.phase_reached = UpgradeTestPhase.ROLLBACK
                    result.findings.append(
                        "Attempting rollback to previous firmware..."
                    )
                    rollback_ok = self._attempt_rollback(
                        device, current_version, running_config
                    )
                    if rollback_ok:
                        result.outcome = UpgradeTestOutcome.ROLLED_BACK
                        result.findings.append(
                            f"Rolled back to {current_version}"
                        )
                    else:
                        result.findings.append(
                            "WARN: Rollback failed — manual intervention needed"
                        )

        except Exception as e:
            result.outcome = UpgradeTestOutcome.ERROR
            result.error_message = str(e)
            result.findings.append(f"ERROR: {e}")
            logger.error(
                "%s: Firmware test error: %s",
                result.device_hostname,
                e,
            )

        result.duration_seconds = time.monotonic() - start_time
        return result

    def test_all(
        self,
        devices: list[DiscoveredDevice],
        target_version: str | None = None,
    ) -> list[FirmwareTestResult]:
        """Run firmware upgrade tests on multiple devices sequentially.

        Devices are tested one at a time to limit blast radius — if a
        firmware upgrade breaks something, we stop before affecting more
        devices.

        Args:
            devices: Devices to test.
            target_version: Optional override; uses catalog recommended
                version per-platform if None.

        Returns:
            List of test results, one per device.
        """
        results: list[FirmwareTestResult] = []

        for device in devices:
            result = self.test_upgrade(device, target_version=target_version)
            results.append(result)

            if result.outcome in (UpgradeTestOutcome.FAILED, UpgradeTestOutcome.ERROR):
                logger.warning(
                    "Stopping firmware test run — %s failed (%s)",
                    result.device_hostname,
                    result.outcome.value,
                )
                # Mark remaining devices as skipped
                for remaining in devices[devices.index(device) + 1 :]:
                    results.append(
                        FirmwareTestResult(
                            device_hostname=(
                                remaining.intended_hostname
                                or remaining.hostname
                                or remaining.ip
                            ),
                            device_ip=remaining.ip,
                            device_serial=remaining.serial or "",
                            platform=(
                                remaining.device_platform.value
                                if remaining.device_platform
                                else (remaining.platform or "")
                            ),
                            outcome=UpgradeTestOutcome.SKIPPED,
                            error_message=(
                                f"Skipped — prior device "
                                f"{result.device_hostname} failed"
                            ),
                        )
                    )
                break

        return results

    # ── Internal helpers ──────────────────────────────────────────────────

    def _resolve_target(
        self, platform: str, version: str | None
    ) -> FirmwareEntry | None:
        """Find the target firmware entry from the catalog."""
        if version:
            return self.catalog.get_version(platform, version)
        return self.catalog.get_recommended(platform)

    def _connect(self, device: DiscoveredDevice):
        """Open an SSH connection to the device."""
        from netmiko import ConnectHandler

        device_type_map = {
            "cisco_ios": "cisco_ios",
            "cisco_iosxe": "cisco_xe",
            "cisco_asa": "cisco_asa",
            "cisco_ftd": "cisco_ftd",
        }

        platform = device.device_platform.value if device.device_platform else "cisco_ios"
        netmiko_type = device_type_map.get(platform, "cisco_ios")

        try:
            return ConnectHandler(
                device_type=netmiko_type,
                host=device.ip,
                username="cisco",
                password="cisco",
                timeout=self.ssh_timeout,
            )
        except Exception as e:
            logger.error("SSH connection failed to %s: %s", device.ip, e)
            return None

    def _get_current_version(self, connection, device: DiscoveredDevice) -> str:
        """Extract firmware version from the device."""
        output = connection.send_command("show version")
        for line in output.splitlines():
            line_lower = line.lower()
            if "version" in line_lower and (
                "ios" in line_lower
                or "adaptive security" in line_lower
                or "software" in line_lower
            ):
                parts = line.split("Version")
                if len(parts) > 1:
                    version = parts[1].strip().split(",")[0].split()[0]
                    return version
        return "unknown"

    def _capture_running_config(self, connection) -> str:
        """Download the running configuration from the device."""
        return connection.send_command("show running-config", read_timeout=60)

    def _reapply_config(
        self,
        connection,
        device: DiscoveredDevice,
        config: str,
    ) -> bool:
        """Push a saved configuration back to the device.

        Filters out meta-lines (Building configuration, Current configuration,
        end, version, etc.) and sends the remaining config commands.
        """
        skip_prefixes = (
            "Building configuration",
            "Current configuration",
            "!",
            "version ",
            "end",
        )

        config_lines = []
        for line in config.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if any(stripped.startswith(pfx) for pfx in skip_prefixes):
                continue
            config_lines.append(line)

        if not config_lines:
            logger.warning(
                "%s: No config lines to re-apply",
                device.intended_hostname or device.ip,
            )
            return True

        try:
            connection.send_config_set(
                config_lines,
                read_timeout=120,
                cmd_verify=False,
            )
            connection.send_command("write memory", read_timeout=30)
            logger.info(
                "%s: Re-applied %d config lines",
                device.intended_hostname or device.ip,
                len(config_lines),
            )
            return True
        except Exception as e:
            logger.error(
                "%s: Config re-apply failed: %s",
                device.intended_hostname or device.ip,
                e,
            )
            return False

    def _perform_upgrade(
        self, device: DiscoveredDevice, entry: FirmwareEntry
    ) -> bool:
        """Perform the actual firmware upgrade using FirmwareConfigurator."""
        from bare_metal_automation.configurator.firmware import FirmwareConfigurator

        # Temporarily override the device spec with our target
        spec = self.inventory.get_device_spec(device.serial) or {}
        original_image = spec.get("firmware_image")
        original_version = spec.get("firmware_version")
        original_md5 = spec.get("firmware_md5")

        spec["firmware_image"] = entry.filename
        spec["firmware_version"] = entry.version
        spec["firmware_md5"] = entry.md5

        try:
            configurator = FirmwareConfigurator(
                inventory=self.inventory,
                firmware_dir=self.firmware_dir,
                ssh_timeout=self.ssh_timeout,
            )
            return configurator.upgrade_device(device)
        finally:
            # Restore original spec
            if original_image is not None:
                spec["firmware_image"] = original_image
            elif "firmware_image" in spec:
                del spec["firmware_image"]
            if original_version is not None:
                spec["firmware_version"] = original_version
            elif "firmware_version" in spec:
                del spec["firmware_version"]
            if original_md5 is not None:
                spec["firmware_md5"] = original_md5
            elif "firmware_md5" in spec:
                del spec["firmware_md5"]

    def _attempt_rollback(
        self,
        device: DiscoveredDevice,
        previous_version: str,
        saved_config: str,
    ) -> bool:
        """Try to roll back to the previous firmware version.

        Looks up the previous version in the catalog and performs a
        downgrade.  If the catalog doesn't have the previous version,
        rollback is not possible.
        """
        platform = (
            device.device_platform.value
            if device.device_platform
            else (device.platform or "")
        )
        previous_entry = self.catalog.get_version(platform, previous_version)

        if previous_entry is None:
            logger.warning(
                "%s: Previous version %s not in catalog — cannot rollback",
                device.intended_hostname or device.ip,
                previous_version,
            )
            return False

        logger.info(
            "%s: Rolling back to %s",
            device.intended_hostname or device.ip,
            previous_version,
        )

        upgrade_ok = self._perform_upgrade(device, previous_entry)
        if not upgrade_ok:
            return False

        # Re-apply the original config
        conn = self._connect(device)
        if conn is None:
            return False

        self._reapply_config(conn, device, saved_config)
        conn.disconnect()
        return True
