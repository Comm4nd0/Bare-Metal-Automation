"""Tests for the HPE server provisioner — Redfish client and provisioning sequence."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from bare_metal_automation.models import (
    DeploymentInventory,
    DevicePlatform,
    DeviceRole,
    DeviceState,
    DiscoveredDevice,
)
from bare_metal_automation.provisioner.server import (
    HPEServerProvisioner,
    RedfishClient,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def inventory() -> DeploymentInventory:
    return DeploymentInventory(
        name="Test Provision",
        bootstrap_subnet="10.255.0.0/16",
        laptop_ip="10.255.0.1",
        management_vlan=100,
        devices={
            "CZ12345678": {
                "role": "compute-node",
                "hostname": "esxi-01",
                "platform": "hpe_dl325_gen10",
                "ilo_username": "admin",
                "ilo_password": "secret",
                "bios_settings": {
                    "WorkloadProfile": "Virtualization-MaxPerformance",
                    "IntelHyperThread": "Enabled",
                },
                "raid_config": {
                    "clear_existing": True,
                    "logical_drives": [
                        {
                            "name": "OS",
                            "raid_level": "Raid1",
                            "drives": ["1I:1:1", "1I:1:2"],
                        }
                    ],
                },
            },
        },
    )


@pytest.fixture
def provisioner(inventory: DeploymentInventory) -> HPEServerProvisioner:
    return HPEServerProvisioner(
        inventory=inventory,
        http_server="10.255.0.1",
    )


@pytest.fixture
def device() -> DiscoveredDevice:
    return DiscoveredDevice(
        ip="10.255.0.20",
        mac="aa:bb:cc:dd:ee:ff",
        serial="CZ12345678",
        platform="DL325 Gen10",
        hostname="esxi-01",
        intended_hostname="esxi-01",
        state=DeviceState.IDENTIFIED,
        role=DeviceRole.COMPUTE_NODE,
        device_platform=DevicePlatform.HPE_DL325_GEN10,
    )


# ── RedfishClient tests ─────────────────────────────────────────────────


class TestRedfishClient:
    def test_get_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"PowerState": "On"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            mock_session_cls.return_value = mock_session

            client = RedfishClient("10.0.0.1", "admin", "pass")
            result = client.get("/redfish/v1/Systems/1")

        assert result == {"PowerState": "On"}

    def test_get_raises_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("404")

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            mock_session_cls.return_value = mock_session

            client = RedfishClient("10.0.0.1", "admin", "pass")

            with pytest.raises(requests.exceptions.HTTPError):
                client.get("/redfish/v1/Systems/1")

    def test_post_sends_json(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.post.return_value = mock_resp
            mock_session_cls.return_value = mock_session

            client = RedfishClient("10.0.0.1", "admin", "pass")
            client.post("/redfish/v1/test", data={"key": "value"})

        mock_session.post.assert_called_once()
        call_kwargs = mock_session.post.call_args
        assert call_kwargs.kwargs.get("json") == {"key": "value"}

    def test_patch_sends_json(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.patch.return_value = mock_resp
            mock_session_cls.return_value = mock_session

            client = RedfishClient("10.0.0.1", "admin", "pass")
            client.patch("/redfish/v1/test", data={"key": "value"})

        mock_session.patch.assert_called_once()

    def test_delete_calls_endpoint(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.delete.return_value = mock_resp
            mock_session_cls.return_value = mock_session

            client = RedfishClient("10.0.0.1", "admin", "pass")
            client.delete("/redfish/v1/test/1")

        mock_session.delete.assert_called_once()


# ── Provisioning sequence ────────────────────────────────────────────────


class TestProvisioningSequence:
    def test_provision_server_full_success(
        self,
        provisioner: HPEServerProvisioner,
        device: DiscoveredDevice,
    ) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "Model": "DL325 Gen10",
            "SerialNumber": "CZ12345678",
        }

        with (
            patch.object(
                provisioner, "_update_ilo_firmware", return_value=True,
            ),
            patch.object(
                provisioner, "_configure_bios", return_value=True,
            ),
            patch.object(
                provisioner, "_configure_raid", return_value=True,
            ),
            patch.object(
                provisioner, "_install_spp", return_value=True,
            ),
            patch.object(
                provisioner, "_install_os", return_value=True,
            ),
            patch.object(
                provisioner, "_configure_ilo", return_value=True,
            ),
            patch(
                "bare_metal_automation.provisioner.server.RedfishClient",
                return_value=mock_client,
            ),
        ):
            result = provisioner.provision_server(device)

        assert result is True
        assert device.state == DeviceState.PROVISIONED

    def test_provision_server_bios_failure_stops(
        self,
        provisioner: HPEServerProvisioner,
        device: DiscoveredDevice,
    ) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "Model": "DL325 Gen10",
            "SerialNumber": "CZ12345678",
        }

        with (
            patch.object(
                provisioner, "_configure_bios", return_value=False,
            ),
            patch(
                "bare_metal_automation.provisioner.server.RedfishClient",
                return_value=mock_client,
            ),
        ):
            result = provisioner.provision_server(device)

        assert result is False

    def test_provision_connection_error(
        self,
        provisioner: HPEServerProvisioner,
        device: DiscoveredDevice,
    ) -> None:
        with patch(
            "bare_metal_automation.provisioner.server.RedfishClient",
        ) as mock_cls:
            mock_client = MagicMock()
            mock_client.get.side_effect = requests.exceptions.ConnectionError(
                "Connection refused"
            )
            mock_cls.return_value = mock_client

            result = provisioner.provision_server(device)

        assert result is False
        assert device.state == DeviceState.FAILED

    def test_provision_generic_exception(
        self,
        provisioner: HPEServerProvisioner,
        device: DiscoveredDevice,
    ) -> None:
        with patch(
            "bare_metal_automation.provisioner.server.RedfishClient",
        ) as mock_cls:
            mock_client = MagicMock()
            mock_client.get.side_effect = RuntimeError("unexpected")
            mock_cls.return_value = mock_client

            result = provisioner.provision_server(device)

        assert result is False
        assert device.state == DeviceState.FAILED


# ── Firmware update polling ──────────────────────────────────────────────


class TestFirmwareUpdate:
    def test_wait_for_task_completed(
        self, provisioner: HPEServerProvisioner, device: DiscoveredDevice
    ) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {"TaskState": "Completed"}

        with patch("bare_metal_automation.provisioner.server.time.sleep"):
            result = provisioner._wait_for_task(
                mock_client, device, "/task/1", "test task", timeout=60
            )

        assert result is True

    def test_wait_for_task_exception_state(
        self, provisioner: HPEServerProvisioner, device: DiscoveredDevice
    ) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "TaskState": "Exception",
            "Messages": ["Something went wrong"],
        }

        with patch("bare_metal_automation.provisioner.server.time.sleep"):
            result = provisioner._wait_for_task(
                mock_client, device, "/task/1", "test task", timeout=60
            )

        assert result is False

    def test_wait_for_task_timeout(
        self, provisioner: HPEServerProvisioner, device: DiscoveredDevice
    ) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {"TaskState": "Running"}

        with patch("bare_metal_automation.provisioner.server.time") as mock_time:
            # Simulate time passing beyond timeout
            mock_time.time.side_effect = [0, 0, 100]
            mock_time.sleep = MagicMock()

            result = provisioner._wait_for_task(
                mock_client, device, "/task/1", "test task", timeout=10
            )

        assert result is False

    def test_wait_for_server_post_success(
        self, provisioner: HPEServerProvisioner, device: DiscoveredDevice
    ) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "PowerState": "On",
            "Oem": {"Hpe": {"PostState": "FinishedPost"}},
        }

        with patch("bare_metal_automation.provisioner.server.time.sleep"):
            result = provisioner._wait_for_server_post(
                mock_client, device, timeout=60
            )

        assert result is True

    def test_wait_for_ilo_success(
        self, provisioner: HPEServerProvisioner
    ) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {}

        with patch("bare_metal_automation.provisioner.server.time.sleep"):
            result = provisioner._wait_for_ilo(mock_client, "10.0.0.1", timeout=60)

        assert result is True

    def test_wait_for_ilo_timeout(
        self, provisioner: HPEServerProvisioner
    ) -> None:
        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("unreachable")

        with patch("bare_metal_automation.provisioner.server.time") as mock_time:
            mock_time.time.side_effect = [0, 0, 400]
            mock_time.sleep = MagicMock()

            result = provisioner._wait_for_ilo(mock_client, "10.0.0.1", timeout=10)

        assert result is False


# ── Helper methods ───────────────────────────────────────────────────────


class TestHelperMethods:
    def test_reboot_server_graceful(
        self, provisioner: HPEServerProvisioner, device: DiscoveredDevice
    ) -> None:
        mock_client = MagicMock()

        provisioner._reboot_server(mock_client, device)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        # post(path, data=payload) — path is positional, data is keyword
        assert "ComputerSystem.Reset" in call_args.args[0]
        payload = call_args.kwargs.get("data") or (call_args.args[1] if len(call_args.args) > 1 else {})
        assert payload.get("ResetType") == "GracefulRestart"

    def test_reboot_server_fallback_to_force(
        self, provisioner: HPEServerProvisioner, device: DiscoveredDevice
    ) -> None:
        mock_client = MagicMock()
        mock_client.post.side_effect = [
            requests.exceptions.HTTPError("Graceful failed"),
            MagicMock(),  # ForceRestart succeeds
        ]

        provisioner._reboot_server(mock_client, device)

        assert mock_client.post.call_count == 2

    def test_set_one_time_boot(
        self, provisioner: HPEServerProvisioner
    ) -> None:
        mock_client = MagicMock()

        provisioner._set_one_time_boot(mock_client, "Cd")

        mock_client.patch.assert_called_once()
        call_args = mock_client.patch.call_args
        call_data = call_args.kwargs.get("data") or (call_args.args[1] if len(call_args.args) > 1 else {})
        assert call_data["Boot"]["BootSourceOverrideTarget"] == "Cd"
        assert call_data["Boot"]["BootSourceOverrideEnabled"] == "Once"
