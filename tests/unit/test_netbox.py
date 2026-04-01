"""Tests for NetBox integration — client, mapper, and loader."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bare_metal_automation.netbox.client import (
    NetBoxAuthError,
    NetBoxClient,
    NetBoxConnectionError,
    NetBoxNotFoundError,
)
from bare_metal_automation.netbox.loader import NetBoxLoader
from bare_metal_automation.netbox.mapper import (
    PLATFORM_MAP,
    ROLE_MAP,
    _default_template,
    _derive_laptop_ip,
    _prefix_to_netmask,
    map_deployment_metadata,
    map_device_to_spec,
)

# ── NetBoxClient tests ───────────────────────────────────────────────────


class TestNetBoxClient:
    @patch("pynetbox.api")
    def test_ping_success(self, mock_api_fn: MagicMock) -> None:
        mock_api = MagicMock()
        mock_api.status.return_value = {"netbox-version": "3.7.0"}
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")
        result = client.ping()

        assert result["netbox-version"] == "3.7.0"

    @patch("pynetbox.api")
    def test_ping_auth_error(self, mock_api_fn: MagicMock) -> None:
        import pynetbox

        mock_api = MagicMock()
        mock_api.status.side_effect = pynetbox.RequestError(
            MagicMock(status_code=403, content=b"403 Forbidden")
        )
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "bad-token")

        with pytest.raises(NetBoxAuthError):
            client.ping()

    @patch("pynetbox.api")
    def test_ping_connection_error(self, mock_api_fn: MagicMock) -> None:
        mock_api = MagicMock()
        mock_api.status.side_effect = Exception("unreachable")
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")

        with pytest.raises(NetBoxConnectionError):
            client.ping()

    @patch("pynetbox.api")
    def test_get_devices_by_tag(self, mock_api_fn: MagicMock) -> None:
        mock_device = MagicMock()
        mock_device.name = "sw-core-01"

        mock_api = MagicMock()
        mock_api.dcim.devices.filter.return_value = [mock_device]
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")
        devices = client.get_devices_by_tag("d001")

        assert len(devices) == 1
        assert devices[0].name == "sw-core-01"

    @patch("pynetbox.api")
    def test_get_devices_by_tag_not_found(
        self, mock_api_fn: MagicMock
    ) -> None:
        mock_api = MagicMock()
        mock_api.dcim.devices.filter.return_value = []
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")

        with pytest.raises(NetBoxNotFoundError):
            client.get_devices_by_tag("nonexistent")

    @patch("pynetbox.api")
    def test_get_config_context(self, mock_api_fn: MagicMock) -> None:
        mock_device = MagicMock()
        mock_device.config_context = {"bios_settings": {"key": "val"}}

        mock_api = MagicMock()
        mock_api.dcim.devices.get.return_value = mock_device
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")
        ctx = client.get_config_context(42)

        assert ctx == {"bios_settings": {"key": "val"}}

    @patch("pynetbox.api")
    def test_get_config_context_device_not_found(
        self, mock_api_fn: MagicMock
    ) -> None:
        mock_api = MagicMock()
        mock_api.dcim.devices.get.return_value = None
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")

        with pytest.raises(NetBoxNotFoundError):
            client.get_config_context(999)

    @patch("pynetbox.api")
    def test_get_device_ips(self, mock_api_fn: MagicMock) -> None:
        mock_ip = MagicMock()
        mock_ip.address = "10.0.100.1/24"
        mock_ip.assigned_object = "GigabitEthernet1/0/1"
        mock_ip.role = "management"
        mock_ip.status = "active"

        mock_api = MagicMock()
        mock_api.ipam.ip_addresses.filter.return_value = [mock_ip]
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")
        ips = client.get_device_ips(42)

        assert len(ips) == 1
        assert ips[0]["address"] == "10.0.100.1/24"
        assert ips[0]["role"] == "management"

    @patch("pynetbox.api")
    def test_get_prefixes_by_tag(self, mock_api_fn: MagicMock) -> None:
        mock_prefix = MagicMock()
        mock_prefix.prefix = "10.255.0.0/16"
        mock_prefix.description = "Bootstrap network"
        mock_prefix.role = None
        mock_prefix.vlan = None

        mock_api = MagicMock()
        mock_api.ipam.prefixes.filter.return_value = [mock_prefix]
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")
        prefixes = client.get_prefixes_by_tag("d001")

        assert len(prefixes) == 1
        assert prefixes[0]["prefix"] == "10.255.0.0/16"

    @patch("pynetbox.api")
    def test_get_vlans_by_tag(self, mock_api_fn: MagicMock) -> None:
        mock_vlan = MagicMock()
        mock_vlan.vid = 100
        mock_vlan.name = "Management"
        mock_vlan.description = "Management VLAN"
        mock_vlan.role = None

        mock_api = MagicMock()
        mock_api.ipam.vlans.filter.return_value = [mock_vlan]
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")
        vlans = client.get_vlans_by_tag("d001")

        assert len(vlans) == 1
        assert vlans[0]["vid"] == 100
        assert vlans[0]["name"] == "Management"

    @patch("pynetbox.api")
    def test_list_node_tags(self, mock_api_fn: MagicMock) -> None:
        mock_tag_d001 = MagicMock()
        mock_tag_d001.name = "D001"
        mock_tag_d001.slug = "d001"
        mock_tag_d001.description = "Node 1"

        mock_tag_other = MagicMock()
        mock_tag_other.name = "production"
        mock_tag_other.slug = "production"
        mock_tag_other.description = ""

        mock_api = MagicMock()
        mock_api.extras.tags.all.return_value = [mock_tag_d001, mock_tag_other]
        mock_api.dcim.devices.filter.return_value = [MagicMock(), MagicMock()]
        mock_api_fn.return_value = mock_api

        client = NetBoxClient("https://netbox.example.com", "token123")
        tags = client.list_node_tags()

        assert len(tags) == 1
        assert tags[0]["name"] == "D001"
        assert tags[0]["device_count"] == 2


# ── Mapper tests ─────────────────────────────────────────────────────────


class TestMapper:
    def test_map_device_to_spec_basic(self) -> None:
        device = MagicMock()
        device.serial = "FOC2145X0AB"
        device.name = "sw-core-01"
        device.device_role.slug = "core-switch"
        device.platform.slug = "cisco_ios"
        device.device_type.slug = "ws-c3850"

        config_ctx = {"template": "switches/custom-core.j2"}
        ips = [{"address": "10.0.100.1/24", "interface": "Gi1/0/1", "role": "", "status": "active"}]

        serial, spec = map_device_to_spec(device, config_ctx, ips)

        assert serial == "FOC2145X0AB"
        assert spec["role"] == "core-switch"
        assert spec["hostname"] == "sw-core-01"
        assert spec["platform"] == "cisco_ios"
        assert spec["management_ip"] == "10.0.100.1"
        assert spec["management_subnet"] == "255.255.255.0"
        assert spec["template"] == "switches/custom-core.j2"

    def test_map_device_missing_serial_raises(self) -> None:
        device = MagicMock()
        device.serial = ""
        device.name = "bad-device"

        with pytest.raises(ValueError, match="no serial number"):
            map_device_to_spec(device, {}, [])

    def test_map_device_unmapped_role_raises(self) -> None:
        device = MagicMock()
        device.serial = "S123"
        device.name = "test"
        device.device_role.slug = "unknown-role"

        with pytest.raises(ValueError, match="unmapped role"):
            map_device_to_spec(device, {}, [])

    def test_map_device_unmapped_platform_raises(self) -> None:
        device = MagicMock()
        device.serial = "S123"
        device.name = "test"
        device.device_role.slug = "core-switch"
        device.platform.slug = "unknown-platform"
        device.device_type.slug = "also-unknown"

        with pytest.raises(ValueError, match="unmapped platform"):
            map_device_to_spec(device, {}, [])

    def test_map_device_platform_falls_back_to_device_type(self) -> None:
        device = MagicMock()
        device.serial = "S123"
        device.name = "test"
        device.device_role.slug = "core-switch"
        device.platform.slug = "unknown-slug"
        device.device_type.slug = "cisco-ios"

        serial, spec = map_device_to_spec(device, {}, [])

        assert spec["platform"] == "cisco_ios"

    def test_map_device_no_ips(self) -> None:
        device = MagicMock()
        device.serial = "S123"
        device.name = "test"
        device.device_role.slug = "core-switch"
        device.platform.slug = "cisco_ios"

        serial, spec = map_device_to_spec(device, {}, [])

        assert "management_ip" not in spec

    def test_map_device_default_template(self) -> None:
        device = MagicMock()
        device.serial = "S123"
        device.name = "test"
        device.device_role.slug = "compute-node"
        device.platform.slug = "hpe_dl325_gen10"

        serial, spec = map_device_to_spec(device, {}, [])

        assert spec["template"] == "servers/compute.j2"

    def test_map_deployment_metadata(self) -> None:
        prefixes = [
            {"prefix": "10.255.0.0/16", "description": "Bootstrap network", "role": ""},
        ]
        vlans = [
            {"vid": 100, "name": "Management", "description": "mgmt vlan", "role": ""},
        ]

        result = map_deployment_metadata("D001", prefixes, vlans)

        assert result["name"] == "D001"
        assert result["bootstrap_subnet"] == "10.255.0.0/16"
        assert result["management_vlan"] == 100
        assert result["laptop_ip"] == "10.255.255.1"

    def test_map_deployment_metadata_no_bootstrap_uses_first(self) -> None:
        prefixes = [
            {"prefix": "192.168.1.0/24", "description": "LAN", "role": ""},
        ]

        result = map_deployment_metadata("D001", prefixes, [])

        assert result["bootstrap_subnet"] == "192.168.1.0/24"

    def test_map_deployment_metadata_no_mgmt_vlan_uses_first(self) -> None:
        vlans = [
            {"vid": 42, "name": "data", "description": "", "role": ""},
        ]

        result = map_deployment_metadata("D001", [], vlans)

        assert result["management_vlan"] == 42

    def test_map_deployment_metadata_empty(self) -> None:
        result = map_deployment_metadata("D001", [], [])

        assert result["bootstrap_subnet"] == ""
        assert result["management_vlan"] == 0
        assert result["laptop_ip"] == ""


class TestMapperHelpers:
    def test_prefix_to_netmask_24(self) -> None:
        assert _prefix_to_netmask(24) == "255.255.255.0"

    def test_prefix_to_netmask_16(self) -> None:
        assert _prefix_to_netmask(16) == "255.255.0.0"

    def test_prefix_to_netmask_32(self) -> None:
        assert _prefix_to_netmask(32) == "255.255.255.255"

    def test_derive_laptop_ip_16(self) -> None:
        assert _derive_laptop_ip("10.255.0.0/16") == "10.255.255.1"

    def test_derive_laptop_ip_24(self) -> None:
        assert _derive_laptop_ip("192.168.1.0/24") == "192.168.1.1"

    def test_derive_laptop_ip_no_prefix(self) -> None:
        assert _derive_laptop_ip("10.0.0.1") == ""

    def test_default_template_known_role(self) -> None:
        assert _default_template("core-switch") == "switches/core.j2"
        assert _default_template("ntp-server") == "ntp/lantime.j2"

    def test_default_template_unknown_role(self) -> None:
        assert _default_template("custom-role") == "custom-role.j2"

    def test_role_map_has_common_entries(self) -> None:
        assert "core-switch" in ROLE_MAP
        assert "compute-node" in ROLE_MAP
        assert "ntp-server" in ROLE_MAP

    def test_platform_map_has_common_entries(self) -> None:
        assert "cisco_ios" in PLATFORM_MAP
        assert "hpe_dl325_gen10" in PLATFORM_MAP
        assert "meinberg_lantime" in PLATFORM_MAP


# ── Loader tests ─────────────────────────────────────────────────────────


class TestNetBoxLoader:
    def test_load_node_success(self) -> None:
        mock_client = MagicMock(spec=NetBoxClient)

        # Mock device
        mock_device = MagicMock()
        mock_device.id = 1
        mock_device.serial = "FOC2145X0AB"
        mock_device.name = "sw-core-01"
        mock_device.device_role.slug = "core-switch"
        mock_device.platform.slug = "cisco_ios"
        mock_device.device_type.slug = ""
        mock_device.config_context = {}

        mock_client.get_devices_by_tag.return_value = [mock_device]
        mock_client.get_config_context.return_value = {}
        mock_client.get_device_ips.return_value = [
            {"address": "10.0.100.1/24", "interface": "Gi1/0/1", "role": "", "status": "active"},
        ]
        mock_client.get_prefixes_by_tag.return_value = [
            {"prefix": "10.255.0.0/16", "description": "Bootstrap", "role": ""},
        ]
        mock_client.get_vlans_by_tag.return_value = [
            {"vid": 100, "name": "Management", "description": "", "role": ""},
        ]

        loader = NetBoxLoader(mock_client)
        inventory = loader.load_node("D001")

        assert inventory.name == "D001"
        assert "FOC2145X0AB" in inventory.devices
        assert inventory.bootstrap_subnet == "10.255.0.0/16"
        assert inventory.management_vlan == 100

    def test_load_node_mapping_error(self) -> None:
        from bare_metal_automation.netbox.client import NetBoxMappingError

        mock_client = MagicMock(spec=NetBoxClient)

        mock_device = MagicMock()
        mock_device.id = 1
        mock_device.serial = ""  # Missing serial triggers ValueError
        mock_device.name = "bad-device"

        mock_client.get_devices_by_tag.return_value = [mock_device]
        mock_client.get_config_context.return_value = {}
        mock_client.get_device_ips.return_value = []

        loader = NetBoxLoader(mock_client)

        with pytest.raises(NetBoxMappingError):
            loader.load_node("D001")

    def test_list_available_nodes(self) -> None:
        mock_client = MagicMock(spec=NetBoxClient)
        mock_client.list_node_tags.return_value = [
            {"name": "D001", "slug": "d001", "description": "", "device_count": 5},
        ]

        loader = NetBoxLoader(mock_client)
        nodes = loader.list_available_nodes()

        assert len(nodes) == 1
        assert nodes[0]["name"] == "D001"

    def test_save_inventory_yaml(self, tmp_path) -> None:
        from bare_metal_automation.models import DeploymentInventory

        inventory = DeploymentInventory(
            name="D001",
            bootstrap_subnet="10.255.0.0/16",
            laptop_ip="10.255.255.1",
            management_vlan=100,
            devices={
                "S123": {"role": "core-switch", "hostname": "sw-01", "platform": "cisco_ios"},
            },
        )

        path = NetBoxLoader.save_inventory_yaml(inventory, tmp_path / "inv.yaml")

        assert path.exists()
        import yaml
        data = yaml.safe_load(path.read_text())
        assert data["deployment"]["name"] == "D001"
        assert "S123" in data["devices"]
