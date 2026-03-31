"""Tests for the checkpoint save/load/resume functionality."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bare_metal_automation.common.checkpoint import (
    deserialize_state,
    load_checkpoint,
    remove_checkpoint,
    save_checkpoint,
    serialize_state,
)
from bare_metal_automation.models import (
    CDPNeighbour,
    CablingResult,
    DeploymentPhase,
    DeploymentState,
    DevicePlatform,
    DeviceRole,
    DeviceState,
    DiscoveredDevice,
)
from bare_metal_automation.orchestrator import PHASE_ORDER, Orchestrator


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def sample_device() -> DiscoveredDevice:
    return DiscoveredDevice(
        ip="10.255.0.10",
        mac="aa:bb:cc:dd:ee:01",
        serial="FOC2145X0AB",
        platform="WS-C3850-48T",
        hostname="Switch-A",
        cdp_neighbours=[
            CDPNeighbour(
                local_port="GigabitEthernet1/0/48",
                remote_device_id="Switch-B.local",
                remote_port="GigabitEthernet1/0/1",
                remote_platform="WS-C3850-48T",
                remote_ip="10.255.0.11",
                remote_serial="FOC2145X0AC",
            ),
        ],
        state=DeviceState.CONFIGURED,
        role=DeviceRole.CORE_SWITCH,
        intended_hostname="sw-core-01",
        template_path="switches/core.j2",
        device_platform=DevicePlatform.CISCO_IOS,
        bfs_depth=1,
        config_order=0,
    )


@pytest.fixture
def sample_cabling_result() -> CablingResult:
    return CablingResult(
        local_port="GigabitEthernet1/0/48",
        status="correct",
        actual_remote="sw-access-01",
        actual_remote_port="GigabitEthernet1/0/1",
        intended_remote="sw-access-01",
        intended_remote_port="GigabitEthernet1/0/1",
        message="Connection matches design",
    )


@pytest.fixture
def sample_state(
    sample_device: DiscoveredDevice,
    sample_cabling_result: CablingResult,
) -> DeploymentState:
    state = DeploymentState()
    state.phase = DeploymentPhase.NETWORK_CONFIG
    state.discovered_devices = {"10.255.0.10": sample_device}
    state.topology_order = ["FOC2145X0AB"]
    state.cabling_results = {"FOC2145X0AB": [sample_cabling_result]}
    state.errors = []
    state.warnings = ["Minor cabling note"]
    return state


# ── Serialization round-trip ───────────────────────────────────────────────


class TestSerialization:
    def test_round_trip_preserves_state(
        self, sample_state: DeploymentState
    ) -> None:
        data = serialize_state(sample_state, "inventory.yaml", 30)
        restored = deserialize_state(data)

        assert restored.phase == sample_state.phase
        assert list(restored.discovered_devices.keys()) == ["10.255.0.10"]
        assert restored.topology_order == ["FOC2145X0AB"]
        assert restored.warnings == ["Minor cabling note"]
        assert restored.errors == []

    def test_device_fields_preserved(
        self, sample_state: DeploymentState
    ) -> None:
        data = serialize_state(sample_state, "inventory.yaml", 30)
        restored = deserialize_state(data)
        device = restored.discovered_devices["10.255.0.10"]

        assert device.serial == "FOC2145X0AB"
        assert device.state == DeviceState.CONFIGURED
        assert device.role == DeviceRole.CORE_SWITCH
        assert device.device_platform == DevicePlatform.CISCO_IOS
        assert device.bfs_depth == 1
        assert device.intended_hostname == "sw-core-01"

    def test_cdp_neighbours_preserved(
        self, sample_state: DeploymentState
    ) -> None:
        data = serialize_state(sample_state, "inventory.yaml", 30)
        restored = deserialize_state(data)
        device = restored.discovered_devices["10.255.0.10"]

        assert len(device.cdp_neighbours) == 1
        n = device.cdp_neighbours[0]
        assert n.remote_device_id == "Switch-B.local"
        assert n.remote_serial == "FOC2145X0AC"

    def test_cabling_results_preserved(
        self, sample_state: DeploymentState
    ) -> None:
        data = serialize_state(sample_state, "inventory.yaml", 30)
        restored = deserialize_state(data)

        assert "FOC2145X0AB" in restored.cabling_results
        results = restored.cabling_results["FOC2145X0AB"]
        assert len(results) == 1
        assert results[0].status == "correct"
        assert results[0].local_port == "GigabitEthernet1/0/48"

    def test_metadata_included(self, sample_state: DeploymentState) -> None:
        data = serialize_state(sample_state, "/path/to/inventory.yaml", 45)

        assert data["version"] == 1
        assert data["inventory_path"] == "/path/to/inventory.yaml"
        assert data["ssh_timeout"] == 45
        assert "saved_at" in data

    def test_none_enums_handled(self) -> None:
        """Devices with no role/platform should serialize cleanly."""
        device = DiscoveredDevice(ip="10.0.0.1", state=DeviceState.DISCOVERED)
        state = DeploymentState()
        state.discovered_devices = {"10.0.0.1": device}

        data = serialize_state(state, "inv.yaml", 30)
        restored = deserialize_state(data)
        d = restored.discovered_devices["10.0.0.1"]

        assert d.role is None
        assert d.device_platform is None
        assert d.state == DeviceState.DISCOVERED


# ── File I/O ───────────────────────────────────────────────────────────────


class TestFileIO:
    def test_save_and_load(
        self, tmp_path: Path, sample_state: DeploymentState
    ) -> None:
        cp = tmp_path / "checkpoint.json"
        save_checkpoint(sample_state, "inv.yaml", 30, cp)

        assert cp.exists()
        data = load_checkpoint(cp)
        assert data["phase"] == "network_config"
        assert data["inventory_path"] == "inv.yaml"

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_checkpoint(tmp_path / "nonexistent.json")

    def test_remove_checkpoint(
        self, tmp_path: Path, sample_state: DeploymentState
    ) -> None:
        cp = tmp_path / "checkpoint.json"
        save_checkpoint(sample_state, "inv.yaml", 30, cp)
        assert cp.exists()

        remove_checkpoint(cp)
        assert not cp.exists()

    def test_remove_nonexistent_is_noop(self, tmp_path: Path) -> None:
        remove_checkpoint(tmp_path / "nope.json")  # Should not raise

    def test_checkpoint_is_valid_json(
        self, tmp_path: Path, sample_state: DeploymentState
    ) -> None:
        cp = tmp_path / "checkpoint.json"
        save_checkpoint(sample_state, "inv.yaml", 30, cp)
        data = json.loads(cp.read_text())
        assert isinstance(data, dict)
        assert data["version"] == 1

    def test_atomic_write(
        self, tmp_path: Path, sample_state: DeploymentState
    ) -> None:
        """Verify no .tmp file is left behind after save."""
        cp = tmp_path / "checkpoint.json"
        save_checkpoint(sample_state, "inv.yaml", 30, cp)
        assert not (tmp_path / "checkpoint.tmp").exists()


# ── Orchestrator resume ────────────────────────────────────────────────────


class TestOrchestratorResume:
    def test_from_checkpoint(
        self, tmp_path: Path, sample_state: DeploymentState
    ) -> None:
        cp = tmp_path / "checkpoint.json"
        save_checkpoint(sample_state, "configs/inventory/inventory.yaml", 30, cp)

        orch = Orchestrator.from_checkpoint(checkpoint_path=cp)

        assert orch.state.phase == DeploymentPhase.NETWORK_CONFIG
        assert len(orch.state.discovered_devices) == 1
        assert orch.ssh_timeout == 30
        assert orch.inventory_path == Path("configs/inventory/inventory.yaml")

    def test_should_skip_logic(self) -> None:
        orch = Orchestrator(inventory_path="inv.yaml")

        # If we resumed after NETWORK_CONFIG, earlier phases should be skipped
        resume_after = DeploymentPhase.NETWORK_CONFIG
        assert orch._should_skip(DeploymentPhase.DISCOVERY, resume_after)
        assert orch._should_skip(DeploymentPhase.TOPOLOGY, resume_after)
        assert orch._should_skip(DeploymentPhase.NETWORK_CONFIG, resume_after)
        # Later phases should NOT be skipped
        assert not orch._should_skip(DeploymentPhase.LAPTOP_PIVOT, resume_after)
        assert not orch._should_skip(DeploymentPhase.SERVER_PROVISION, resume_after)

    def test_phase_order_is_complete(self) -> None:
        """Every non-FAILED phase should appear in PHASE_ORDER."""
        for phase in DeploymentPhase:
            if phase == DeploymentPhase.FAILED:
                continue
            assert phase in PHASE_ORDER, f"{phase} missing from PHASE_ORDER"
