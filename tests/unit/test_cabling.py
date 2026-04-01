"""Tests for the cabling validator — connection matching and report generation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bare_metal_automation.cabling.validator import CablingValidator
from bare_metal_automation.models import (
    CablingResult,
    CDPNeighbour,
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
    IntendedConnection,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def inventory() -> DeploymentInventory:
    return DeploymentInventory(
        name="Test Cabling",
        bootstrap_subnet="10.255.0.0/16",
        laptop_ip="10.255.0.1",
        management_vlan=100,
        devices={
            "S-CORE": {
                "role": "core-switch", "hostname": "sw-core-01",
                "platform": "cisco_ios",
            },
            "S-ACCESS": {
                "role": "access-switch", "hostname": "sw-access-01",
                "platform": "cisco_ios",
            },
            "S-SERVER": {
                "role": "compute-node", "hostname": "svr-01",
                "platform": "hpe_dl325_gen10",
            },
        },
    )


@pytest.fixture
def validator(inventory: DeploymentInventory) -> CablingValidator:
    return CablingValidator(inventory=inventory)


def _make_device(
    ip: str,
    serial: str,
    hostname: str,
    intended_hostname: str,
    cdp_neighbours: list[CDPNeighbour] | None = None,
    template_path: str | None = "switches/core.j2",
) -> DiscoveredDevice:
    return DiscoveredDevice(
        ip=ip,
        serial=serial,
        hostname=hostname,
        intended_hostname=intended_hostname,
        cdp_neighbours=cdp_neighbours or [],
        template_path=template_path,
        state=DeviceState.IDENTIFIED,
    )


# ── Connection diffing ───────────────────────────────────────────────────


class TestDiffConnections:
    def test_correct_connection(self, validator: CablingValidator) -> None:
        intended = {
            "Gi1/0/48": IntendedConnection(
                local_port="Gi1/0/48",
                remote_hostname="sw-access-01",
                remote_port="Gi1/0/1",
            ),
        }
        actual = {"Gi1/0/48": ("sw-access-01", "Gi1/0/1")}
        device = _make_device("10.0.0.1", "S-CORE", "core", "sw-core-01")

        results = validator._diff_connections(intended, actual, device)

        assert len(results) == 1
        assert results[0].status == "correct"
        assert results[0].local_port == "Gi1/0/48"

    def test_wrong_device(self, validator: CablingValidator) -> None:
        intended = {
            "Gi1/0/48": IntendedConnection(
                local_port="Gi1/0/48",
                remote_hostname="sw-access-01",
                remote_port="Gi1/0/1",
            ),
        }
        actual = {"Gi1/0/48": ("sw-core-02", "Gi1/0/1")}
        device = _make_device("10.0.0.1", "S-CORE", "core", "sw-core-01")

        results = validator._diff_connections(intended, actual, device)

        assert len(results) == 1
        assert results[0].status == "wrong_device"
        assert "sw-access-01" in results[0].message
        assert "sw-core-02" in results[0].message

    def test_wrong_port(self, validator: CablingValidator) -> None:
        intended = {
            "Gi1/0/48": IntendedConnection(
                local_port="Gi1/0/48",
                remote_hostname="sw-access-01",
                remote_port="Gi1/0/1",
            ),
        }
        actual = {"Gi1/0/48": ("sw-access-01", "Gi1/0/2")}
        device = _make_device("10.0.0.1", "S-CORE", "core", "sw-core-01")

        results = validator._diff_connections(intended, actual, device)

        assert len(results) == 1
        assert results[0].status == "wrong_port"
        assert results[0].intended_remote_port == "Gi1/0/1"
        assert results[0].actual_remote_port == "Gi1/0/2"

    def test_missing_connection(self, validator: CablingValidator) -> None:
        intended = {
            "Gi1/0/48": IntendedConnection(
                local_port="Gi1/0/48",
                remote_hostname="sw-access-01",
                remote_port="Gi1/0/1",
            ),
        }
        actual: dict = {}
        device = _make_device("10.0.0.1", "S-CORE", "core", "sw-core-01")

        results = validator._diff_connections(intended, actual, device)

        assert len(results) == 1
        assert results[0].status == "missing"
        assert "sw-access-01" in results[0].message

    def test_unexpected_connection(self, validator: CablingValidator) -> None:
        intended: dict = {}
        actual = {"Gi1/0/48": ("unknown-device", "Gi1/0/1")}
        device = _make_device("10.0.0.1", "S-CORE", "core", "sw-core-01")

        results = validator._diff_connections(intended, actual, device)

        assert len(results) == 1
        assert results[0].status == "unexpected"
        assert results[0].actual_remote == "unknown-device"

    def test_adaptable_connection(self, validator: CablingValidator) -> None:
        intended = {
            "Gi1/0/1": IntendedConnection(
                local_port="Gi1/0/1",
                remote_hostname="svr-01",
                remote_port=None,
                is_flexible=True,
            ),
        }
        actual = {"Gi1/0/1": ("svr-02", "iLO")}
        device = _make_device("10.0.0.1", "S-CORE", "core", "sw-core-01")

        results = validator._diff_connections(intended, actual, device)

        assert len(results) == 1
        assert results[0].status == "adaptable"
        assert "svr-02" in results[0].message

    def test_correct_no_remote_port_check(
        self, validator: CablingValidator
    ) -> None:
        """When intended has no remote_port, any port on the right device is correct."""
        intended = {
            "Gi1/0/48": IntendedConnection(
                local_port="Gi1/0/48",
                remote_hostname="sw-access-01",
                remote_port=None,
            ),
        }
        actual = {"Gi1/0/48": ("sw-access-01", "Gi1/0/99")}
        device = _make_device("10.0.0.1", "S-CORE", "core", "sw-core-01")

        results = validator._diff_connections(intended, actual, device)

        assert len(results) == 1
        assert results[0].status == "correct"

    def test_mixed_results(self, validator: CablingValidator) -> None:
        intended = {
            "Gi1/0/1": IntendedConnection(
                local_port="Gi1/0/1",
                remote_hostname="sw-access-01",
                remote_port="Gi1/0/1",
            ),
            "Gi1/0/2": IntendedConnection(
                local_port="Gi1/0/2",
                remote_hostname="svr-01",
                remote_port=None,
            ),
        }
        actual = {
            "Gi1/0/1": ("sw-access-01", "Gi1/0/1"),
            "Gi1/0/3": ("rogue-device", "Gi0/0"),
        }
        device = _make_device("10.0.0.1", "S-CORE", "core", "sw-core-01")

        results = validator._diff_connections(intended, actual, device)

        statuses = {r.status for r in results}
        assert "correct" in statuses
        assert "missing" in statuses
        assert "unexpected" in statuses


# ── Report generation ────────────────────────────────────────────────────


class TestReportGeneration:
    def test_print_report_runs_without_error(
        self, validator: CablingValidator
    ) -> None:
        results = {
            "S-CORE": [
                CablingResult(
                    local_port="Gi1/0/48",
                    status="correct",
                    actual_remote="sw-access-01",
                    actual_remote_port="Gi1/0/1",
                    intended_remote="sw-access-01",
                    intended_remote_port="Gi1/0/1",
                ),
                CablingResult(
                    local_port="Gi1/0/1",
                    status="missing",
                    intended_remote="svr-01",
                    message="No device detected",
                ),
            ],
        }

        # Should not raise
        with patch("bare_metal_automation.cabling.validator.console"):
            validator.print_report(results)

    def test_print_report_counts_statuses(
        self, validator: CablingValidator
    ) -> None:
        results = {
            "S-CORE": [
                CablingResult(local_port="Gi1", status="correct"),
                CablingResult(local_port="Gi2", status="correct"),
                CablingResult(local_port="Gi3", status="wrong_device", message="err"),
                CablingResult(local_port="Gi4", status="missing", message="miss"),
                CablingResult(local_port="Gi5", status="unexpected", message="unexp"),
                CablingResult(local_port="Gi6", status="adaptable", message="adapt"),
                CablingResult(local_port="Gi7", status="wrong_port", message="wp"),
            ],
        }

        # Just verify it completes without raising
        with patch("bare_metal_automation.cabling.validator.console"):
            validator.print_report(results)


# ── Description to connection parsing ────────────────────────────────────


class TestDescriptionToConnection:
    def test_known_hostname_extracted(
        self, validator: CablingValidator
    ) -> None:
        conn = validator._description_to_connection(
            "GigabitEthernet1/0/48",
            "Uplink to sw-access-01",
            False,
        )

        assert conn is not None
        assert conn.remote_hostname == "sw-access-01"
        assert conn.local_port == "GigabitEthernet1/0/48"

    def test_hostname_with_port_ref(
        self, validator: CablingValidator
    ) -> None:
        conn = validator._description_to_connection(
            "GigabitEthernet1/0/48",
            "Downlink to sw-access-01 Gi1/0/48",
            False,
        )

        assert conn is not None
        assert conn.remote_hostname == "sw-access-01"
        assert conn.remote_port == "Gi1/0/48"

    def test_unknown_hostname_returns_none(
        self, validator: CablingValidator
    ) -> None:
        conn = validator._description_to_connection(
            "GigabitEthernet1/0/48",
            "Uplink to isp-router-01",
            False,
        )

        assert conn is None

    def test_flexible_flag_passed(
        self, validator: CablingValidator
    ) -> None:
        conn = validator._description_to_connection(
            "GigabitEthernet1/0/1",
            "Server svr-01 iLO",
            True,
        )

        assert conn is not None
        assert conn.is_flexible is True
