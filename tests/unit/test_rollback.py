"""Tests for rollback modules — server resetter, meinberg resetter, orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from bare_metal_automation.models import (
    DevicePlatform,
    DeviceRole,
    DeviceState,
    DiscoveredDevice,
    RollbackPhase,
)
from bare_metal_automation.rollback.orchestrator import (
    ROLLBACK_PHASE_ORDER,
    RollbackOrchestrator,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_device(
    ip: str,
    serial: str,
    hostname: str,
    platform: DevicePlatform = DevicePlatform.CISCO_IOS,
    role: DeviceRole = DeviceRole.CORE_SWITCH,
) -> DiscoveredDevice:
    device = DiscoveredDevice(
        ip=ip,
        mac="aa:bb:cc:dd:ee:ff",
        serial=serial,
        hostname=hostname,
        intended_hostname=hostname,
        state=DeviceState.PROVISIONED,
        role=role,
        device_platform=platform,
    )
    # management_ip is a dynamic attribute used by rollback modules
    device.management_ip = None  # type: ignore[attr-defined]
    return device


# ── HPEServerResetter tests ─────────────────────────────────────────────


class TestHPEServerResetter:
    def test_reset_server_full_success(self) -> None:
        from bare_metal_automation.rollback.server import HPEServerResetter

        device = _make_device(
            "10.0.0.20", "CZ123", "esxi-01",
            platform=DevicePlatform.HPE_DL325_GEN10,
            role=DeviceRole.COMPUTE_NODE,
        )
        resetter = HPEServerResetter()

        mock_client = MagicMock()
        mock_client.get.return_value = {"Model": "DL325", "SerialNumber": "CZ123"}

        with (
            patch(
                "bare_metal_automation.rollback.server.RedfishClient",
                return_value=mock_client,
            ),
            patch(
                "bare_metal_automation.rollback.server.time.sleep",
            ),
        ):
            result = resetter.reset_server(device)

        assert result is True
        assert device.state == DeviceState.POWERED_OFF

    def test_reset_server_connection_failure(self) -> None:
        from bare_metal_automation.rollback.server import HPEServerResetter

        device = _make_device(
            "10.0.0.20", "CZ123", "esxi-01",
            platform=DevicePlatform.HPE_DL325_GEN10,
            role=DeviceRole.COMPUTE_NODE,
        )
        resetter = HPEServerResetter()

        with patch(
            "bare_metal_automation.rollback.server.RedfishClient",
        ) as mock_cls:
            mock_client = MagicMock()
            mock_client.get.side_effect = Exception("Connection refused")
            mock_cls.return_value = mock_client

            result = resetter.reset_server(device)

        assert result is False
        assert device.state == DeviceState.FAILED

    def test_reset_server_step_failure(self) -> None:
        from bare_metal_automation.rollback.server import HPEServerResetter

        device = _make_device(
            "10.0.0.20", "CZ123", "esxi-01",
            platform=DevicePlatform.HPE_DL325_GEN10,
            role=DeviceRole.COMPUTE_NODE,
        )
        resetter = HPEServerResetter()

        mock_client = MagicMock()
        mock_client.get.return_value = {"Model": "DL325", "SerialNumber": "CZ123"}

        with (
            patch(
                "bare_metal_automation.rollback.server.RedfishClient",
                return_value=mock_client,
            ),
            patch.object(
                resetter, "_reset_bios",
                side_effect=Exception("BIOS reset failed"),
            ),
        ):
            result = resetter.reset_server(device)

        assert result is False
        assert device.state == DeviceState.FAILED

    def test_reset_bios_calls_correct_endpoint(self) -> None:
        from bare_metal_automation.rollback.server import HPEServerResetter

        resetter = HPEServerResetter()
        mock_client = MagicMock()

        resetter._reset_bios(mock_client, "esxi-01")

        mock_client.post.assert_called_once()
        call_path = mock_client.post.call_args.args[0]
        assert "Bios.ResetBios" in call_path

    def test_delete_raid_removes_logical_drives(self) -> None:
        from bare_metal_automation.rollback.server import HPEServerResetter

        resetter = HPEServerResetter()
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            {"Members": [{"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0"}]},
            {"Members": [
                {"@odata.id": "/path/LogicalDrives/1"},
                {"@odata.id": "/path/LogicalDrives/2"},
            ]},
        ]

        resetter._delete_raid(mock_client, "esxi-01")

        assert mock_client.delete.call_count == 2

    def test_power_off_sends_force_off(self) -> None:
        from bare_metal_automation.rollback.server import HPEServerResetter

        resetter = HPEServerResetter()
        mock_client = MagicMock()

        resetter._power_off(mock_client, "esxi-01")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args.args[0].endswith("ComputerSystem.Reset")
        assert call_args.args[1]["ResetType"] == "ForceOff"


# ── MeinbergResetter tests ──────────────────────────────────────────────


class TestMeinbergResetter:
    def test_factory_reset_success(self) -> None:
        from bare_metal_automation.rollback.meinberg import MeinbergResetter

        device = _make_device(
            "10.0.0.30", "MBG123", "ntp-01",
            platform=DevicePlatform.MEINBERG_LANTIME,
            role=DeviceRole.NTP_SERVER,
        )
        resetter = MeinbergResetter()

        mock_session = MagicMock()
        mock_status_resp = MagicMock()
        mock_status_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_status_resp

        mock_reset_resp = MagicMock()
        mock_reset_resp.status_code = 200
        mock_session.post.return_value = mock_reset_resp

        with patch(
            "bare_metal_automation.rollback.meinberg.time.sleep",
        ):
            with patch.object(
                resetter, "_create_session", return_value=mock_session,
            ):
                result = resetter.reset_device(device)

        assert result is True
        assert device.state == DeviceState.FACTORY_RESET

    def test_connection_failure(self) -> None:
        from bare_metal_automation.rollback.meinberg import MeinbergResetter

        device = _make_device(
            "10.0.0.30", "MBG123", "ntp-01",
            platform=DevicePlatform.MEINBERG_LANTIME,
            role=DeviceRole.NTP_SERVER,
        )
        resetter = MeinbergResetter()

        with patch.object(resetter, "_create_session", return_value=None):
            result = resetter.reset_device(device)

        assert result is False
        assert device.state == DeviceState.FAILED

    def test_factory_reset_endpoint_not_available_falls_back(self) -> None:
        from bare_metal_automation.rollback.meinberg import MeinbergResetter

        device = _make_device(
            "10.0.0.30", "MBG123", "ntp-01",
            platform=DevicePlatform.MEINBERG_LANTIME,
            role=DeviceRole.NTP_SERVER,
        )
        resetter = MeinbergResetter()

        mock_session = MagicMock()
        # Factory reset returns 404
        mock_reset_resp = MagicMock()
        mock_reset_resp.status_code = 404
        mock_session.post.return_value = mock_reset_resp
        mock_session.put.return_value = MagicMock()

        with (
            patch.object(
                resetter, "_create_session", return_value=mock_session,
            ),
            patch(
                "bare_metal_automation.rollback.meinberg.time.sleep",
            ),
        ):
            result = resetter.reset_device(device)

        assert result is True
        assert device.state == DeviceState.FACTORY_RESET
        # Verify manual revert methods were called (put calls)
        assert mock_session.put.call_count >= 1

    def test_create_session_failure(self) -> None:
        from bare_metal_automation.rollback.meinberg import MeinbergResetter

        resetter = MeinbergResetter()

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.get.side_effect = Exception("unreachable")
            mock_session_cls.return_value = mock_session

            result = resetter._create_session("10.0.0.30", "admin", "pass")

        assert result is None

    def test_exception_during_reset_marks_failed(self) -> None:
        from bare_metal_automation.rollback.meinberg import MeinbergResetter

        device = _make_device(
            "10.0.0.30", "MBG123", "ntp-01",
            platform=DevicePlatform.MEINBERG_LANTIME,
            role=DeviceRole.NTP_SERVER,
        )
        resetter = MeinbergResetter()

        mock_session = MagicMock()
        # Factory reset endpoint raises unexpected error
        mock_session.post.side_effect = RuntimeError("unexpected")

        with patch.object(
            resetter, "_create_session", return_value=mock_session,
        ):
            result = resetter.reset_device(device)

        assert result is False
        assert device.state == DeviceState.FAILED


# ── Orchestrator tests ───────────────────────────────────────────────────


class TestRollbackOrchestrator:
    def test_phase_order_is_complete(self) -> None:
        """Every non-FAILED RollbackPhase should appear in ROLLBACK_PHASE_ORDER."""
        for phase in RollbackPhase:
            if phase == RollbackPhase.ROLLBACK_FAILED:
                continue
            assert phase in ROLLBACK_PHASE_ORDER, f"{phase} missing from ROLLBACK_PHASE_ORDER"

    def test_should_skip_logic(self) -> None:
        orch = RollbackOrchestrator(inventory_path="inv.yaml")

        resume_after = RollbackPhase.ROLLBACK_SERVER_RESET
        assert orch._should_skip(RollbackPhase.ROLLBACK_PRE_FLIGHT, resume_after)
        assert orch._should_skip(RollbackPhase.ROLLBACK_NTP_RESET, resume_after)
        assert orch._should_skip(RollbackPhase.ROLLBACK_SERVER_RESET, resume_after)
        assert not orch._should_skip(RollbackPhase.ROLLBACK_LAPTOP_PIVOT, resume_after)
        assert not orch._should_skip(RollbackPhase.ROLLBACK_NETWORK_RESET, resume_after)

    def test_device_classification_cisco(self) -> None:
        orch = RollbackOrchestrator()
        orch.devices = {
            "10.0.0.1": _make_device("10.0.0.1", "S1", "sw1", DevicePlatform.CISCO_IOS),
            "10.0.0.2": _make_device("10.0.0.2", "S2", "sw2", DevicePlatform.CISCO_IOSXE),
            "10.0.0.3": _make_device("10.0.0.3", "S3", "svr1", DevicePlatform.HPE_DL325_GEN10),
        }

        cisco_devices = orch._cisco_devices()
        assert len(cisco_devices) == 2

    def test_device_classification_hpe(self) -> None:
        orch = RollbackOrchestrator()
        orch.devices = {
            "10.0.0.1": _make_device("10.0.0.1", "S1", "sw1", DevicePlatform.CISCO_IOS),
            "10.0.0.2": _make_device("10.0.0.2", "S2", "svr1", DevicePlatform.HPE_DL325_GEN10),
        }

        hpe_devices = orch._hpe_devices()
        assert len(hpe_devices) == 1
        assert hpe_devices[0].serial == "S2"

    def test_device_classification_ntp(self) -> None:
        orch = RollbackOrchestrator()
        orch.devices = {
            "10.0.0.1": _make_device(
                "10.0.0.1", "M1", "ntp1",
                DevicePlatform.MEINBERG_LANTIME,
                DeviceRole.NTP_SERVER,
            ),
            "10.0.0.2": _make_device("10.0.0.2", "S1", "sw1", DevicePlatform.CISCO_IOS),
        }

        ntp_devices = orch._ntp_devices()
        assert len(ntp_devices) == 1
        assert ntp_devices[0].serial == "M1"

    def test_save_and_load_checkpoint(self, tmp_path: Path) -> None:
        orch = RollbackOrchestrator(
            inventory_path="inv.yaml",
            rollback_checkpoint=tmp_path / "rollback.json",
        )
        orch.phase = RollbackPhase.ROLLBACK_SERVER_RESET
        orch.devices = {
            "10.0.0.1": _make_device("10.0.0.1", "S1", "sw1"),
        }
        orch.results = {"S1": True}

        orch._save_checkpoint()

        assert (tmp_path / "rollback.json").exists()
        data = json.loads((tmp_path / "rollback.json").read_text())
        assert data["phase"] == "rollback_server_reset"
        assert data["type"] == "rollback"

    def test_check_stop_not_set(self) -> None:
        orch = RollbackOrchestrator()
        assert orch._check_stop() is False

    def test_check_stop_when_set(self) -> None:
        import threading
        event = threading.Event()
        event.set()
        orch = RollbackOrchestrator(stop_event=event)
        assert orch._check_stop() is True

    def test_phase_index(self) -> None:
        orch = RollbackOrchestrator()
        idx = orch._phase_index(RollbackPhase.ROLLBACK_PRE_FLIGHT)
        assert idx == 0
        idx = orch._phase_index(RollbackPhase.ROLLBACK_COMPLETE)
        assert idx == len(ROLLBACK_PHASE_ORDER) - 1

    def test_phase_index_unknown_returns_negative(self) -> None:
        orch = RollbackOrchestrator()
        idx = orch._phase_index(RollbackPhase.ROLLBACK_FAILED)
        assert idx == -1
