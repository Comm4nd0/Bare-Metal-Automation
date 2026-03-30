"""Unit tests for factory reset modules."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ztp_forge.models import (
    DeploymentInventory,
    DevicePlatform,
    DeviceRole,
    DeviceState,
    DiscoveredDevice,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def inventory() -> DeploymentInventory:
    return DeploymentInventory(
        name="Test Reset",
        bootstrap_subnet="10.255.0.0/16",
        laptop_ip="10.255.0.1",
        management_vlan=100,
        devices={
            "FOC2145X0AB": {
                "role": "core-switch",
                "hostname": "core-sw-01",
                "platform": "cisco_ios",
                "username": "admin",
                "password": "secret",
            },
            "FOC2145X0CD": {
                "role": "access-switch",
                "hostname": "access-sw-01",
                "platform": "cisco_ios",
            },
            "CZ12345678": {
                "role": "compute-node",
                "hostname": "esxi-01",
                "platform": "hpe_dl325_gen10",
                "ilo_username": "prodadmin",
                "ilo_password": "prodpass",
            },
            "MBG20231234": {
                "role": "ntp-server",
                "hostname": "ntp-01",
                "platform": "meinberg_lantime",
                "username": "ntpadmin",
                "password": "ntppass",
            },
        },
    )


def _make_device(
    ip: str,
    serial: str,
    hostname: str,
    platform: DevicePlatform = DevicePlatform.CISCO_IOS,
    role: DeviceRole = DeviceRole.CORE_SWITCH,
    bfs_depth: int | None = None,
) -> DiscoveredDevice:
    return DiscoveredDevice(
        ip=ip,
        mac="aa:bb:cc:dd:ee:ff",
        serial=serial,
        platform=platform.value,
        hostname=hostname,
        state=DeviceState.PROVISIONED,
        role=role,
        intended_hostname=hostname,
        device_platform=platform,
        bfs_depth=bfs_depth,
    )


# ── NetworkResetter tests ────────────────────────────────────────────────


class TestNetworkResetter:
    def test_reset_device_success(self, inventory: DeploymentInventory) -> None:
        from ztp_forge.resetter.network import NetworkResetter

        device = _make_device("10.255.0.10", "FOC2145X0AB", "core-sw-01")
        resetter = NetworkResetter(inventory=inventory)

        mock_conn = MagicMock()
        mock_conn.send_command_timing.return_value = "[confirm]"

        with patch.object(resetter, "_connect", return_value=mock_conn):
            result = resetter.reset_device(device)

        assert result is True
        assert device.state == DeviceState.RESET_COMPLETE

        # Verify write erase was sent
        calls = mock_conn.send_command_timing.call_args_list
        assert any("write erase" in str(c) for c in calls)
        assert any("reload" in str(c) for c in calls)

    def test_reset_device_connection_failure(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.network import NetworkResetter

        device = _make_device("10.255.0.10", "FOC2145X0AB", "core-sw-01")
        resetter = NetworkResetter(inventory=inventory)

        with patch.object(resetter, "_connect", return_value=None):
            result = resetter.reset_device(device)

        assert result is False
        assert device.state == DeviceState.FAILED

    def test_reset_device_exception(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.network import NetworkResetter

        device = _make_device("10.255.0.10", "FOC2145X0AB", "core-sw-01")
        resetter = NetworkResetter(inventory=inventory)

        mock_conn = MagicMock()
        mock_conn.send_command_timing.side_effect = Exception("SSH error")

        with patch.object(resetter, "_connect", return_value=mock_conn):
            result = resetter.reset_device(device)

        assert result is False
        assert device.state == DeviceState.FAILED

    def test_connect_tries_production_creds_first(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.network import NetworkResetter

        device = _make_device("10.255.0.10", "FOC2145X0AB", "core-sw-01")
        resetter = NetworkResetter(inventory=inventory)

        with patch("netmiko.ConnectHandler") as mock_handler:
            mock_handler.return_value = MagicMock()
            conn = resetter._connect(device)

        assert conn is not None
        # First call should use production creds from spec
        first_call = mock_handler.call_args_list[0]
        assert first_call.kwargs["username"] == "admin"
        assert first_call.kwargs["password"] == "secret"

    def test_connect_falls_back_to_factory_defaults(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.network import NetworkResetter

        # Use device without production creds in inventory
        device = _make_device("10.255.0.11", "FOC2145X0CD", "access-sw-01")
        resetter = NetworkResetter(inventory=inventory)

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Auth failed")
            return MagicMock()

        with patch("netmiko.ConnectHandler", side_effect=side_effect):
            conn = resetter._connect(device)

        assert conn is not None
        assert call_count == 3  # Failed twice, succeeded on third

    def test_reset_handles_save_prompt(
        self, inventory: DeploymentInventory
    ) -> None:
        """Verify reload handles 'save config?' prompt with 'no'."""
        from ztp_forge.resetter.network import NetworkResetter

        device = _make_device("10.255.0.10", "FOC2145X0AB", "core-sw-01")
        resetter = NetworkResetter(inventory=inventory)

        mock_conn = MagicMock()
        # send_command_timing is called multiple times:
        # 1. write erase -> returns [confirm]
        # 2. confirm -> OK
        # 3. reload -> returns save prompt
        # 4. "no" -> answer no to save
        # After that, disconnect() is a regular MagicMock method (no side_effect needed)
        mock_conn.send_command_timing.side_effect = [
            "[confirm]",  # write erase
            "OK",  # confirm write erase
            "System configuration has been modified. Save? [yes/no]:",  # reload
            "Proceed with reload? [confirm]",  # answer no to save
            "",  # confirm reload
        ]

        with patch.object(resetter, "_connect", return_value=mock_conn):
            result = resetter.reset_device(device)

        assert result is True


# ── HPEServerResetter tests ──────────────────────────────────────────────


class TestHPEServerResetter:
    def test_reset_server_success(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.server import HPEServerResetter

        device = _make_device(
            "10.255.0.20",
            "CZ12345678",
            "esxi-01",
            platform=DevicePlatform.HPE_DL325_GEN10,
            role=DeviceRole.COMPUTE_NODE,
        )
        resetter = HPEServerResetter(inventory=inventory)

        with (
            patch.object(resetter, "_connect") as mock_connect,
            patch.object(resetter, "_reset_bios", return_value=True),
            patch.object(resetter, "_clear_raid", return_value=True),
            patch.object(resetter, "_reset_ilo", return_value=True),
        ):
            mock_client = MagicMock()
            mock_client.get.return_value = {
                "Model": "DL325 Gen10",
                "SerialNumber": "CZ12345678",
            }
            mock_connect.return_value = mock_client
            result = resetter.reset_server(device)

        assert result is True
        assert device.state == DeviceState.RESET_COMPLETE

    def test_reset_server_bios_failure(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.server import HPEServerResetter

        device = _make_device(
            "10.255.0.20",
            "CZ12345678",
            "esxi-01",
            platform=DevicePlatform.HPE_DL325_GEN10,
            role=DeviceRole.COMPUTE_NODE,
        )
        resetter = HPEServerResetter(inventory=inventory)

        with (
            patch.object(resetter, "_connect") as mock_connect,
            patch.object(resetter, "_reset_bios", return_value=False),
        ):
            mock_client = MagicMock()
            mock_client.get.return_value = {"Model": "DL325", "SerialNumber": "CZ12345678"}
            mock_connect.return_value = mock_client
            result = resetter.reset_server(device)

        assert result is False
        assert device.state == DeviceState.FAILED

    def test_reset_server_connection_failure(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.server import HPEServerResetter

        device = _make_device(
            "10.255.0.20",
            "CZ12345678",
            "esxi-01",
            platform=DevicePlatform.HPE_DL325_GEN10,
            role=DeviceRole.COMPUTE_NODE,
        )
        resetter = HPEServerResetter(inventory=inventory)

        with patch.object(resetter, "_connect", return_value=None):
            result = resetter.reset_server(device)

        assert result is False
        assert device.state == DeviceState.FAILED

    def test_connect_tries_production_then_factory(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.server import HPEServerResetter

        resetter = HPEServerResetter(inventory=inventory)
        spec = inventory.get_device_spec("CZ12345678")

        with patch("ztp_forge.resetter.server.RedfishClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            client = resetter._connect("10.255.0.20", spec)

        assert client is not None
        # First call should use production creds
        first_call = mock_cls.call_args_list[0]
        assert first_call.args == ("10.255.0.20", "prodadmin", "prodpass")

    def test_reset_bios_calls_correct_api(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.server import HPEServerResetter

        resetter = HPEServerResetter(inventory=inventory)
        mock_client = MagicMock()

        result = resetter._reset_bios(mock_client, "esxi-01")

        assert result is True
        mock_client.post.assert_called_once_with(
            "/redfish/v1/Systems/1/Bios/Actions/Bios.ResetBios",
        )

    def test_clear_raid_deletes_logical_drives(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.server import HPEServerResetter

        resetter = HPEServerResetter(inventory=inventory)
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            # ArrayControllers
            {"Members": [{"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0"}]},
            # LogicalDrives
            {"Members": [
                {"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1"},
                {"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/2"},
            ]},
        ]

        result = resetter._clear_raid(mock_client, "esxi-01")

        assert result is True
        assert mock_client.delete.call_count == 2


# ── MeinbergResetter tests ───────────────────────────────────────────────


class TestMeinbergResetter:
    def test_reset_device_success(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.meinberg import MeinbergResetter

        device = _make_device(
            "10.255.0.30",
            "MBG20231234",
            "ntp-01",
            platform=DevicePlatform.MEINBERG_LANTIME,
            role=DeviceRole.NTP_SERVER,
        )
        resetter = MeinbergResetter(inventory=inventory)

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response

        with (
            patch.object(resetter, "_connect", return_value=mock_session),
            patch.object(resetter, "_wait_for_device", return_value=True),
        ):
            result = resetter.reset_device(device)

        assert result is True
        assert device.state == DeviceState.RESET_COMPLETE

        # Verify factory-reset API called with preserve_network
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert "factory-reset" in call_args.args[0]
        assert call_args.kwargs["json"] == {"preserve_network": True}

    def test_reset_device_connection_failure(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.meinberg import MeinbergResetter

        device = _make_device(
            "10.255.0.30",
            "MBG20231234",
            "ntp-01",
            platform=DevicePlatform.MEINBERG_LANTIME,
            role=DeviceRole.NTP_SERVER,
        )
        resetter = MeinbergResetter(inventory=inventory)

        with patch.object(resetter, "_connect", return_value=None):
            result = resetter.reset_device(device)

        assert result is False
        assert device.state == DeviceState.FAILED

    def test_reset_device_reboot_timeout(
        self, inventory: DeploymentInventory
    ) -> None:
        from ztp_forge.resetter.meinberg import MeinbergResetter

        device = _make_device(
            "10.255.0.30",
            "MBG20231234",
            "ntp-01",
            platform=DevicePlatform.MEINBERG_LANTIME,
            role=DeviceRole.NTP_SERVER,
        )
        resetter = MeinbergResetter(inventory=inventory)

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response

        with (
            patch.object(resetter, "_connect", return_value=mock_session),
            patch.object(resetter, "_wait_for_device", return_value=False),
            patch("ztp_forge.resetter.meinberg.time.sleep"),
        ):
            result = resetter.reset_device(device)

        assert result is False
        assert device.state == DeviceState.FAILED


# ── Parallel ordering tests ──────────────────────────────────────────────


class TestResetOrdering:
    def test_ascending_depth_sort(self) -> None:
        """Verify inside-out ordering: shallowest depth first."""
        from ztp_forge.common.parallel import group_devices_by_depth

        devices = [
            _make_device("10.0.0.1", "S1", "core", bfs_depth=1),
            _make_device("10.0.0.2", "S2", "dist", bfs_depth=2),
            _make_device("10.0.0.3", "S3", "access", bfs_depth=3),
        ]

        groups = group_devices_by_depth(devices, ascending=True)
        depths = [g[0].bfs_depth for g in groups]
        assert depths == [1, 2, 3]  # Shallowest first (inside-out)

    def test_descending_depth_sort_default(self) -> None:
        """Verify default ordering: deepest depth first (outside-in)."""
        from ztp_forge.common.parallel import group_devices_by_depth

        devices = [
            _make_device("10.0.0.1", "S1", "core", bfs_depth=1),
            _make_device("10.0.0.2", "S2", "dist", bfs_depth=2),
            _make_device("10.0.0.3", "S3", "access", bfs_depth=3),
        ]

        groups = group_devices_by_depth(devices)
        depths = [g[0].bfs_depth for g in groups]
        assert depths == [3, 2, 1]  # Deepest first (outside-in, default)


# ── Model state tests ────────────────────────────────────────────────────


class TestResetStates:
    def test_resetting_state_exists(self) -> None:
        assert DeviceState.RESETTING == "resetting"

    def test_reset_complete_state_exists(self) -> None:
        assert DeviceState.RESET_COMPLETE == "reset_complete"

    def test_factory_reset_phase_exists(self) -> None:
        from ztp_forge.models import DeploymentPhase
        assert DeploymentPhase.FACTORY_RESET == "factory_reset"
