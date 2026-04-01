"""Tests for the firmware management modules: catalog, tester, compliance."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from bare_metal_automation.firmware.catalog import FirmwareCatalog, FirmwareEntry
from bare_metal_automation.firmware.compliance import (
    ComplianceChecker,
    ComplianceReport,
    ComplianceStatus,
)
from bare_metal_automation.firmware.tester import (
    FirmwareTestResult,
    FirmwareTestRunner,
    UpgradeTestOutcome,
    UpgradeTestPhase,
)
from bare_metal_automation.models import (
    DeploymentInventory,
    DevicePlatform,
    DeviceRole,
    DiscoveredDevice,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def sample_catalog_yaml(tmp_path: Path) -> Path:
    """Write a sample catalog YAML and return its path."""
    data = {
        "platforms": {
            "cisco_ios": [
                {
                    "version": "15.2(4)M11",
                    "filename": "c2960x-universalk9-mz.152-4.M11.bin",
                    "md5": "abc123",
                    "min_version": "15.2(4)M7",
                    "release_notes": "Security fixes",
                    "recommended": True,
                },
                {
                    "version": "15.2(4)M7",
                    "filename": "c2960x-universalk9-mz.152-4.M7.bin",
                    "md5": "def456",
                    "release_notes": "Previous stable",
                },
            ],
            "cisco_iosxe": [
                {
                    "version": "17.09.04a",
                    "filename": "cat9k_iosxe.17.09.04a.SPA.bin",
                    "md5": "ghi789",
                    "min_version": "17.06.01",
                    "recommended": True,
                },
            ],
        }
    }
    path = tmp_path / "catalog.yaml"
    with open(path, "w") as fh:
        yaml.dump(data, fh)
    return path


@pytest.fixture
def catalog(sample_catalog_yaml: Path) -> FirmwareCatalog:
    return FirmwareCatalog.from_yaml(sample_catalog_yaml)


@pytest.fixture
def inventory() -> DeploymentInventory:
    return DeploymentInventory(
        name="test-deployment",
        bootstrap_subnet="10.255.0.0/16",
        laptop_ip="10.255.255.1",
        management_vlan=100,
        devices={
            "FOC2145X0AB": {
                "hostname": "sw-core-01",
                "platform": "cisco_ios",
                "role": "core-switch",
                "firmware_image": "c2960x-universalk9-mz.152-4.M7.bin",
                "firmware_version": "15.2(4)M7",
            },
            "FOC2145X0CD": {
                "hostname": "sw-access-01",
                "platform": "cisco_iosxe",
                "role": "access-switch",
                "firmware_image": "cat9k_iosxe.17.06.05.SPA.bin",
                "firmware_version": "17.06.05",
            },
        },
    )


@pytest.fixture
def device_ios() -> DiscoveredDevice:
    return DiscoveredDevice(
        ip="10.255.0.10",
        serial="FOC2145X0AB",
        platform="cisco_ios",
        hostname="Switch",
        intended_hostname="sw-core-01",
        device_platform=DevicePlatform.CISCO_IOS,
        role=DeviceRole.CORE_SWITCH,
    )


@pytest.fixture
def device_iosxe() -> DiscoveredDevice:
    return DiscoveredDevice(
        ip="10.255.0.20",
        serial="FOC2145X0CD",
        platform="cisco_iosxe",
        hostname="Switch",
        intended_hostname="sw-access-01",
        device_platform=DevicePlatform.CISCO_IOSXE,
        role=DeviceRole.ACCESS_SWITCH,
    )


# ── FirmwareCatalog tests ────────────────────────────────────────────────


class TestFirmwareCatalog:
    def test_load_from_yaml(self, catalog: FirmwareCatalog):
        assert "cisco_ios" in catalog.entries
        assert "cisco_iosxe" in catalog.entries
        assert len(catalog.entries["cisco_ios"]) == 2
        assert len(catalog.entries["cisco_iosxe"]) == 1

    def test_get_recommended(self, catalog: FirmwareCatalog):
        rec = catalog.get_recommended("cisco_ios")
        assert rec is not None
        assert rec.version == "15.2(4)M11"
        assert rec.recommended is True

    def test_get_recommended_missing_platform(self, catalog: FirmwareCatalog):
        assert catalog.get_recommended("juniper_junos") is None

    def test_get_version(self, catalog: FirmwareCatalog):
        entry = catalog.get_version("cisco_ios", "15.2(4)M7")
        assert entry is not None
        assert entry.filename == "c2960x-universalk9-mz.152-4.M7.bin"

    def test_get_version_not_found(self, catalog: FirmwareCatalog):
        assert catalog.get_version("cisco_ios", "99.99.99") is None

    def test_is_latest(self, catalog: FirmwareCatalog):
        assert catalog.is_latest("cisco_ios", "15.2(4)M11") is True
        assert catalog.is_latest("cisco_ios", "15.2(4)M7") is False

    def test_is_latest_no_recommendation(self, catalog: FirmwareCatalog):
        # Unknown platform has no recommendation => considered latest
        assert catalog.is_latest("unknown_platform", "1.0") is True

    def test_all_platforms(self, catalog: FirmwareCatalog):
        platforms = catalog.all_platforms
        assert "cisco_ios" in platforms
        assert "cisco_iosxe" in platforms

    def test_add_entry(self, catalog: FirmwareCatalog):
        new_entry = FirmwareEntry(
            platform="cisco_ios",
            version="15.2(4)M12",
            filename="new.bin",
            recommended=True,
        )
        catalog.add_entry(new_entry)
        assert catalog.get_version("cisco_ios", "15.2(4)M12") is not None

    def test_add_entry_replaces_same_version(self, catalog: FirmwareCatalog):
        replacement = FirmwareEntry(
            platform="cisco_ios",
            version="15.2(4)M7",
            filename="updated.bin",
        )
        catalog.add_entry(replacement)
        entry = catalog.get_version("cisco_ios", "15.2(4)M7")
        assert entry.filename == "updated.bin"

    def test_roundtrip_yaml(self, catalog: FirmwareCatalog, tmp_path: Path):
        out_path = tmp_path / "output.yaml"
        catalog.to_yaml(out_path)

        reloaded = FirmwareCatalog.from_yaml(out_path)
        assert len(reloaded.entries["cisco_ios"]) == len(catalog.entries["cisco_ios"])
        assert reloaded.get_recommended("cisco_ios").version == "15.2(4)M11"

    def test_load_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            FirmwareCatalog.from_yaml(tmp_path / "missing.yaml")


# ── FirmwareEntry tests ──────────────────────────────────────────────────


class TestFirmwareEntry:
    def test_is_upgrade_safe_no_min(self):
        entry = FirmwareEntry(
            platform="cisco_ios", version="15.2(4)M11", filename="a.bin"
        )
        assert entry.is_upgrade_safe("1.0") is True

    def test_is_upgrade_safe_above_min(self):
        entry = FirmwareEntry(
            platform="cisco_ios",
            version="15.2(4)M11",
            filename="a.bin",
            min_version="15.2(4)M7",
        )
        assert entry.is_upgrade_safe("15.2(4)M9") is True

    def test_is_upgrade_safe_at_min(self):
        entry = FirmwareEntry(
            platform="cisco_ios",
            version="15.2(4)M11",
            filename="a.bin",
            min_version="15.2(4)M7",
        )
        assert entry.is_upgrade_safe("15.2(4)M7") is True

    def test_is_upgrade_unsafe_below_min(self):
        entry = FirmwareEntry(
            platform="cisco_ios",
            version="15.2(4)M11",
            filename="a.bin",
            min_version="15.2(4)M7",
        )
        assert entry.is_upgrade_safe("15.2(4)M3") is False


# ── ComplianceChecker tests ──────────────────────────────────────────────


class TestComplianceChecker:
    def test_compliant_device(self, catalog: FirmwareCatalog, device_ios: DiscoveredDevice):
        checker = ComplianceChecker(catalog=catalog)

        with patch.object(checker, "_get_live_version", return_value="15.2(4)M11"):
            result = checker.check_device(device_ios)

        assert result.status == ComplianceStatus.COMPLIANT
        assert result.current_version == "15.2(4)M11"

    def test_upgrade_available(self, catalog: FirmwareCatalog, device_ios: DiscoveredDevice):
        checker = ComplianceChecker(catalog=catalog)

        with patch.object(checker, "_get_live_version", return_value="15.2(4)M7"):
            result = checker.check_device(device_ios)

        assert result.status == ComplianceStatus.UPGRADE_AVAILABLE
        assert result.upgrade_safe is True

    def test_upgrade_blocked(self, catalog: FirmwareCatalog, device_ios: DiscoveredDevice):
        checker = ComplianceChecker(catalog=catalog)

        with patch.object(checker, "_get_live_version", return_value="15.2(4)M3"):
            result = checker.check_device(device_ios)

        assert result.status == ComplianceStatus.UPGRADE_BLOCKED
        assert result.upgrade_safe is False

    def test_unreachable_device(self, catalog: FirmwareCatalog, device_ios: DiscoveredDevice):
        checker = ComplianceChecker(catalog=catalog)

        with patch.object(checker, "_get_live_version", return_value=""):
            result = checker.check_device(device_ios)

        assert result.status == ComplianceStatus.UNREACHABLE

    def test_unknown_platform(self, catalog: FirmwareCatalog):
        device = DiscoveredDevice(
            ip="10.255.0.99",
            platform="unknown_vendor",
            intended_hostname="mystery-box",
        )
        checker = ComplianceChecker(catalog=catalog)

        with patch.object(checker, "_get_live_version", return_value="1.0"):
            result = checker.check_device(device)

        assert result.status == ComplianceStatus.UNKNOWN

    def test_check_devices_report(
        self,
        catalog: FirmwareCatalog,
        device_ios: DiscoveredDevice,
        device_iosxe: DiscoveredDevice,
    ):
        checker = ComplianceChecker(catalog=catalog)

        def mock_version(device):
            versions = {
                "10.255.0.10": "15.2(4)M11",  # compliant
                "10.255.0.20": "17.06.05",  # upgrade available
            }
            return versions.get(device.ip, "")

        with patch.object(checker, "_get_live_version", side_effect=mock_version):
            report = checker.check_devices([device_ios, device_iosxe])

        assert report.total == 2
        assert report.compliant_count == 1
        assert report.upgrade_available_count == 1
        assert report.compliance_percentage == 50.0

    def test_report_to_dict(self):
        report = ComplianceReport()
        d = report.to_dict()
        assert d["total"] == 0
        assert d["compliance_percentage"] == 100.0

    def test_devices_needing_upgrade(
        self,
        catalog: FirmwareCatalog,
        device_ios: DiscoveredDevice,
    ):
        checker = ComplianceChecker(catalog=catalog)

        with patch.object(checker, "_get_live_version", return_value="15.2(4)M7"):
            report = checker.check_devices([device_ios])

        needing = report.devices_needing_upgrade()
        assert len(needing) == 1
        assert needing[0].hostname == "sw-core-01"


# ── FirmwareTestRunner tests ─────────────────────────────────────────────


class TestFirmwareTestRunner:
    def _make_runner(
        self, catalog: FirmwareCatalog, inventory: DeploymentInventory
    ) -> FirmwareTestRunner:
        return FirmwareTestRunner(
            catalog=catalog,
            inventory=inventory,
            management_vlan=100,
            rollback_on_failure=False,
        )

    def test_skip_already_at_target(
        self,
        catalog: FirmwareCatalog,
        inventory: DeploymentInventory,
        device_ios: DiscoveredDevice,
    ):
        runner = self._make_runner(catalog, inventory)
        mock_conn = MagicMock()
        mock_conn.send_command.return_value = (
            "Cisco IOS Software, Version 15.2(4)M11"
        )

        with patch.object(runner, "_connect", return_value=mock_conn):
            result = runner.test_upgrade(device_ios, target_version="15.2(4)M11")

        assert result.outcome == UpgradeTestOutcome.SKIPPED
        assert "Already at target" in result.findings[0]

    def test_skip_no_firmware_found(
        self,
        catalog: FirmwareCatalog,
        inventory: DeploymentInventory,
        device_ios: DiscoveredDevice,
    ):
        runner = self._make_runner(catalog, inventory)

        result = runner.test_upgrade(device_ios, target_version="99.99.99")

        assert result.outcome == UpgradeTestOutcome.SKIPPED
        assert "No" in result.error_message

    def test_skip_unsafe_upgrade_path(
        self,
        catalog: FirmwareCatalog,
        inventory: DeploymentInventory,
        device_ios: DiscoveredDevice,
    ):
        runner = self._make_runner(catalog, inventory)
        mock_conn = MagicMock()
        mock_conn.send_command.return_value = (
            "Cisco IOS Software, Version 15.2(4)M3"
        )

        with patch.object(runner, "_connect", return_value=mock_conn):
            result = runner.test_upgrade(device_ios, target_version="15.2(4)M11")

        assert result.outcome == UpgradeTestOutcome.SKIPPED
        assert "not supported" in result.error_message

    def test_connection_failure(
        self,
        catalog: FirmwareCatalog,
        inventory: DeploymentInventory,
        device_ios: DiscoveredDevice,
    ):
        runner = self._make_runner(catalog, inventory)

        with patch.object(runner, "_connect", return_value=None):
            result = runner.test_upgrade(device_ios)

        assert result.outcome == UpgradeTestOutcome.ERROR
        assert "Cannot connect" in result.error_message

    def test_upgrade_failure_stops_at_upgrade_phase(
        self,
        catalog: FirmwareCatalog,
        inventory: DeploymentInventory,
        device_ios: DiscoveredDevice,
    ):
        runner = self._make_runner(catalog, inventory)

        mock_conn = MagicMock()
        mock_conn.send_command.return_value = (
            "Cisco IOS Software, Version 15.2(4)M7"
        )

        with (
            patch.object(runner, "_connect", return_value=mock_conn),
            patch.object(runner, "_perform_upgrade", return_value=False),
        ):
            result = runner.test_upgrade(device_ios, target_version="15.2(4)M11")

        assert result.outcome == UpgradeTestOutcome.FAILED
        assert result.phase_reached == UpgradeTestPhase.UPGRADE

    def test_full_pass(
        self,
        catalog: FirmwareCatalog,
        inventory: DeploymentInventory,
        device_ios: DiscoveredDevice,
    ):
        """Test the full passing path: upgrade + config reapply + validation."""
        runner = self._make_runner(catalog, inventory)

        # First connection: returns old version + running config
        mock_conn_pre = MagicMock()
        mock_conn_pre.send_command.side_effect = [
            "Cisco IOS Software, Version 15.2(4)M7",  # show version
            # show running-config
            "hostname sw-core-01\ninterface Vlan100\n"
            " ip address 10.0.0.1 255.255.255.0",
            # Pre-validation (core-switch): STP root, trunk, mgmt VLAN
            "spanning-tree: this bridge is the root",
            "Gi1/0/1  802.1q  trunking",
            "100  VLAN100  active",
        ]

        # Post-upgrade connection: returns new version
        mock_conn_post = MagicMock()
        mock_conn_post.send_command.side_effect = [
            "Cisco IOS Software, Version 15.2(4)M11",  # show version (verify)
            "write OK",  # write memory after config reapply
        ]

        # Post-validation connection
        mock_conn_validate = MagicMock()
        mock_conn_validate.send_command.side_effect = [
            "spanning-tree: this bridge is the root",
            "Gi1/0/1  802.1q  trunking",
            "100  VLAN100  active",
        ]

        connections = [mock_conn_pre, mock_conn_post, mock_conn_validate]
        connect_calls = iter(connections)

        with (
            patch.object(runner, "_connect", side_effect=lambda d: next(connect_calls)),
            patch.object(runner, "_perform_upgrade", return_value=True),
            patch("time.sleep"),  # Skip convergence wait
            patch("socket.create_connection"),  # Mock management reachability check
        ):
            result = runner.test_upgrade(device_ios, target_version="15.2(4)M11")

        assert result.outcome == UpgradeTestOutcome.PASSED
        assert result.phase_reached == UpgradeTestPhase.COMPLETE
        assert result.previous_version == "15.2(4)M7"
        assert result.target_version == "15.2(4)M11"

    def test_test_all_stops_on_failure(
        self,
        catalog: FirmwareCatalog,
        inventory: DeploymentInventory,
        device_ios: DiscoveredDevice,
        device_iosxe: DiscoveredDevice,
    ):
        runner = self._make_runner(catalog, inventory)

        # First device fails to connect
        with patch.object(runner, "_connect", return_value=None):
            results = runner.test_all([device_ios, device_iosxe])

        assert len(results) == 2
        assert results[0].outcome == UpgradeTestOutcome.ERROR
        assert results[1].outcome == UpgradeTestOutcome.SKIPPED
        assert "prior device" in results[1].error_message

    def test_result_to_dict(self):
        result = FirmwareTestResult(
            device_hostname="sw-core-01",
            device_ip="10.0.0.1",
            device_serial="FOC123",
            platform="cisco_ios",
            outcome=UpgradeTestOutcome.PASSED,
        )
        d = result.to_dict()
        assert d["outcome"] == "passed"
        assert d["device_hostname"] == "sw-core-01"
