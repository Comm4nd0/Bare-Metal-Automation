"""Tests for the discovery engine — DHCP lease parsing, device probing, CDP parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bare_metal_automation.discovery.engine import DiscoveryEngine
from bare_metal_automation.models import (
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> DiscoveryEngine:
    lease_file = tmp_path / "dnsmasq.leases"
    lease_file.touch()
    return DiscoveryEngine(
        bootstrap_subnet="10.255.0.0/16",
        laptop_ip="10.255.0.1",
        ssh_timeout=10,
        lease_file=str(lease_file),
    )


@pytest.fixture
def inventory() -> DeploymentInventory:
    return DeploymentInventory(
        name="Test",
        bootstrap_subnet="10.255.0.0/16",
        laptop_ip="10.255.0.1",
        management_vlan=100,
        devices={
            "FOC2145X0AB": {
                "role": "core-switch",
                "hostname": "sw-core-01",
                "platform": "cisco_ios",
            },
            "CZ12345678": {
                "role": "compute-node",
                "hostname": "esxi-01",
                "platform": "hpe_dl325_gen10",
            },
        },
    )


SAMPLE_LEASE_CONTENT = """\
1700000000 aa:bb:cc:dd:ee:01 10.255.0.10 switch-a *
1700000001 aa:bb:cc:dd:ee:02 10.255.0.11 switch-b *
1700000002 aa:bb:cc:dd:ee:03 10.255.0.12 server-01 *
"""

SAMPLE_CDP_OUTPUT = """\
-------------------------
Device ID: Switch-B.local
Entry address(es):
  IP address: 10.255.0.11
Platform: cisco WS-C3850-48T,  Capabilities: Switch IGMP
Interface: GigabitEthernet1/0/48,  Port ID (outgoing port): GigabitEthernet1/0/1

-------------------------
Device ID: Switch-C
Entry address(es):
  IP address: 10.255.0.12
Platform: cisco WS-C2960X-48FPD-L,  Capabilities: Switch IGMP
Interface: GigabitEthernet1/0/47,  Port ID (outgoing port): GigabitEthernet1/0/2
"""

SAMPLE_INVENTORY_OUTPUT = """\
NAME: "1", DESCR: "WS-C3850-48T"
PID: WS-C3850-48T      , VID: V06, SN: FOC2145X0AB
"""


# ── DHCP lease parsing ───────────────────────────────────────────────────


class TestDHCPLeases:
    def test_parse_valid_leases(self, engine: DiscoveryEngine) -> None:
        engine.lease_file.write_text(SAMPLE_LEASE_CONTENT)

        leases = engine.get_dhcp_leases()

        assert len(leases) == 3
        assert leases["10.255.0.10"] == "aa:bb:cc:dd:ee:01"
        assert leases["10.255.0.11"] == "aa:bb:cc:dd:ee:02"
        assert leases["10.255.0.12"] == "aa:bb:cc:dd:ee:03"

    def test_parse_empty_lease_file(self, engine: DiscoveryEngine) -> None:
        engine.lease_file.write_text("")

        leases = engine.get_dhcp_leases()

        assert leases == {}

    def test_missing_lease_file(self, tmp_path: Path) -> None:
        engine = DiscoveryEngine(
            bootstrap_subnet="10.255.0.0/16",
            laptop_ip="10.255.0.1",
            lease_file=str(tmp_path / "nonexistent.leases"),
        )

        leases = engine.get_dhcp_leases()

        assert leases == {}

    def test_laptop_ip_excluded(self, engine: DiscoveryEngine) -> None:
        engine.lease_file.write_text(
            "1700000000 aa:bb:cc:dd:ee:01 10.255.0.1 laptop *\n"
            "1700000001 aa:bb:cc:dd:ee:02 10.255.0.10 switch *\n"
        )

        leases = engine.get_dhcp_leases()

        assert "10.255.0.1" not in leases
        assert "10.255.0.10" in leases

    def test_malformed_lines_skipped(self, engine: DiscoveryEngine) -> None:
        engine.lease_file.write_text(
            "short\n"
            "1700000000 aa:bb:cc:dd:ee:01 10.255.0.10 switch *\n"
            "incomplete line\n"
        )

        leases = engine.get_dhcp_leases()

        assert len(leases) == 1
        assert "10.255.0.10" in leases


# ── Device probing ───────────────────────────────────────────────────────


class TestDeviceProbing:
    def test_probe_device_success(self, engine: DiscoveryEngine) -> None:
        mock_conn = MagicMock()
        mock_conn.send_command.side_effect = [
            SAMPLE_INVENTORY_OUTPUT,
            "hostname Switch-A",
            SAMPLE_CDP_OUTPUT,
        ]

        with patch.object(engine, "_ssh_connect", return_value=mock_conn):
            device = engine.probe_device("10.255.0.10", "aa:bb:cc:dd:ee:01")

        assert device.state == DeviceState.DISCOVERED
        assert device.serial == "FOC2145X0AB"
        assert device.platform == "WS-C3850-48T"
        assert device.hostname == "Switch-A"
        assert len(device.cdp_neighbours) == 2
        mock_conn.disconnect.assert_called_once()

    def test_probe_device_ssh_failure_tries_redfish(
        self, engine: DiscoveryEngine
    ) -> None:
        with (
            patch.object(engine, "_ssh_connect", return_value=None),
            patch.object(engine, "_probe_redfish") as mock_redfish,
        ):
            mock_redfish.return_value = DiscoveredDevice(
                ip="10.255.0.20",
                mac="aa:bb:cc:dd:ee:03",
                serial="CZ12345678",
                state=DeviceState.DISCOVERED,
            )
            device = engine.probe_device("10.255.0.20", "aa:bb:cc:dd:ee:03")

        mock_redfish.assert_called_once()
        assert device.serial == "CZ12345678"

    def test_probe_device_ssh_exception_continues(
        self, engine: DiscoveryEngine
    ) -> None:
        """SSH exceptions should be caught and probing should try next credential."""
        call_count = 0

        def side_effect(ip, username, password):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Connection refused")
            return MagicMock(
                send_command=MagicMock(side_effect=[
                    SAMPLE_INVENTORY_OUTPUT,
                    "hostname Switch-A",
                    "",
                ])
            )

        with patch.object(engine, "_ssh_connect", side_effect=side_effect):
            device = engine.probe_device("10.255.0.10", "aa:bb:cc:dd:ee:01")

        assert device.state == DeviceState.DISCOVERED


# ── CDP parsing ──────────────────────────────────────────────────────────


class TestCDPParsing:
    def test_parse_cdp_multiple_neighbours(
        self, engine: DiscoveryEngine
    ) -> None:
        neighbours = engine._parse_cdp(SAMPLE_CDP_OUTPUT)

        assert len(neighbours) == 2

        n1 = neighbours[0]
        assert n1.remote_device_id == "Switch-B.local"
        assert n1.local_port == "GigabitEthernet1/0/48"
        assert n1.remote_port == "GigabitEthernet1/0/1"
        assert n1.remote_platform == "cisco WS-C3850-48T"
        assert n1.remote_ip == "10.255.0.11"

        n2 = neighbours[1]
        assert n2.remote_device_id == "Switch-C"
        assert n2.local_port == "GigabitEthernet1/0/47"
        assert n2.remote_port == "GigabitEthernet1/0/2"

    def test_parse_cdp_empty_output(self, engine: DiscoveryEngine) -> None:
        neighbours = engine._parse_cdp("")
        assert neighbours == []

    def test_parse_cdp_no_ip_address(self, engine: DiscoveryEngine) -> None:
        cdp_no_ip = """\
-------------------------
Device ID: Switch-X
Platform: cisco WS-C3850,  Capabilities: Switch
Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): GigabitEthernet1/0/2
"""
        neighbours = engine._parse_cdp(cdp_no_ip)

        assert len(neighbours) == 1
        assert neighbours[0].remote_ip == ""

    def test_parse_cdp_missing_device_id_skipped(
        self, engine: DiscoveryEngine
    ) -> None:
        cdp_no_device = """\
-------------------------
Platform: cisco WS-C3850,  Capabilities: Switch
Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): GigabitEthernet1/0/2
"""
        neighbours = engine._parse_cdp(cdp_no_device)
        assert neighbours == []


# ── Inventory parsing ────────────────────────────────────────────────────


class TestInventoryParsing:
    def test_parse_inventory_serial_and_platform(
        self, engine: DiscoveryEngine
    ) -> None:
        serial, platform = engine._parse_inventory(SAMPLE_INVENTORY_OUTPUT)

        assert serial == "FOC2145X0AB"
        assert platform == "WS-C3850-48T"

    def test_parse_inventory_empty_output(
        self, engine: DiscoveryEngine
    ) -> None:
        serial, platform = engine._parse_inventory("")

        assert serial is None
        assert platform is None

    def test_parse_inventory_partial_output(
        self, engine: DiscoveryEngine
    ) -> None:
        serial, platform = engine._parse_inventory("PID: WS-C3850-48T")

        assert serial is None
        assert platform == "WS-C3850-48T"

    def test_parse_hostname(self, engine: DiscoveryEngine) -> None:
        hostname = engine._parse_hostname("hostname Switch-A")
        assert hostname == "Switch-A"

    def test_parse_hostname_missing(self, engine: DiscoveryEngine) -> None:
        hostname = engine._parse_hostname("")
        assert hostname is None


# ── Inventory matching ───────────────────────────────────────────────────


class TestInventoryMatching:
    def test_match_known_device(
        self, engine: DiscoveryEngine, inventory: DeploymentInventory
    ) -> None:
        devices = {
            "10.255.0.10": DiscoveredDevice(
                ip="10.255.0.10",
                serial="FOC2145X0AB",
                state=DeviceState.DISCOVERED,
            ),
        }

        engine.match_to_inventory(devices, inventory)

        device = devices["10.255.0.10"]
        assert device.state == DeviceState.IDENTIFIED
        assert device.role == "core-switch"
        assert device.intended_hostname == "sw-core-01"

    def test_match_unknown_device(
        self, engine: DiscoveryEngine, inventory: DeploymentInventory
    ) -> None:
        devices = {
            "10.255.0.99": DiscoveredDevice(
                ip="10.255.0.99",
                serial="UNKNOWN_SERIAL",
                state=DeviceState.DISCOVERED,
            ),
        }

        engine.match_to_inventory(devices, inventory)

        device = devices["10.255.0.99"]
        # State should not be updated to IDENTIFIED
        assert device.state == DeviceState.DISCOVERED

    def test_match_device_without_serial(
        self, engine: DiscoveryEngine, inventory: DeploymentInventory
    ) -> None:
        devices = {
            "10.255.0.99": DiscoveredDevice(
                ip="10.255.0.99",
                serial=None,
                state=DeviceState.DISCOVERED,
            ),
        }

        engine.match_to_inventory(devices, inventory)

        device = devices["10.255.0.99"]
        assert device.state == DeviceState.DISCOVERED
        assert device.role is None
