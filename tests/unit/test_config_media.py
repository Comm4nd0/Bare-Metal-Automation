"""Unit tests for the config_media package (Sprint 2).

Tests cover:
- ConfigRenderer.build_context — variable extraction from mock NetBox data
- ConfigRenderer.render_device — template rendering to .cfg files
- InventoryExporter.export — YAML output structure
- FirmwareCatalogue — load, resolve, verify
- MediaCollector — copy + checksum verification
- BundlePackager — manifest, checksums, validation
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from bare_metal_automation.config_media.bundle_packager import BundlePackager
from bare_metal_automation.config_media.firmware_catalogue import (
    CatalogueError,
    FirmwareCatalogue,
)
from bare_metal_automation.config_media.inventory_export import InventoryExporter
from bare_metal_automation.config_media.media_collector import (
    ChecksumMismatch,
    MediaCollector,
)
from bare_metal_automation.config_media.renderer import (
    ConfigRenderer,
    InterfaceContext,
    MissionTenant,
    RenderContext,
    VlanContext,
    _build_mission_tenants,
    _build_vlan_contexts,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def minimal_context() -> RenderContext:
    """Minimal RenderContext suitable for template testing."""
    return RenderContext(
        hostname="sw-core-01",
        serial="FOC2145X0AB",
        domain_name="dc1.example.mil",
        site_slug="dc1",
        site_size="small",
        mgmt_ip="10.0.100.10",
        mgmt_mask="255.255.255.0",
        mgmt_gateway="10.0.100.1",
        ntp_servers=["10.0.100.20", "10.0.100.21"],
        dns_servers=["10.0.100.30", "10.0.100.31"],
        syslog_server="10.0.100.40",
        ad_server="10.0.100.50",
        nps_server="10.0.100.51",
        vlans=[
            VlanContext(vid=100, name="MGMT", is_management=True),
            VlanContext(vid=600, name="ILO", is_management=True),
        ],
        interfaces=[
            InterfaceContext(
                name="GigabitEthernet1/0/47",
                description="Uplink to core",
                mode="trunk",
                trunk_vlans="all",
            ),
            InterfaceContext(
                name="GigabitEthernet1/0/1",
                description="Server iLO",
                mode="access",
                access_vlan=600,
                portfast=True,
            ),
        ],
        mission_tenants=[
            MissionTenant(
                index=1,
                name="MISSION_1",
                user_vlan=1100,
                apps_vlan=1110,
                data_vlan=1120,
                user_subnet="10.1.10.0",
                user_mask="255.255.255.0",
                user_gateway="10.1.10.1",
                apps_subnet="10.1.11.0",
                apps_mask="255.255.255.0",
                apps_gateway="10.1.11.1",
                data_subnet="10.1.12.0",
                data_mask="255.255.255.0",
                data_gateway="10.1.12.1",
            ),
        ],
        enable_secret="{{ vault_enable_secret }}",
        tacacs_key="{{ vault_tacacs_key }}",
        radius_key="{{ vault_radius_key }}",
        snmp_community_ro="{{ vault_snmp_community_ro }}",
        hsrp_key="{{ vault_hsrp_key }}",
    )


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    """Create a minimal template tree for renderer tests."""
    switches_common = tmp_path / "switches" / "common"
    switches_common.mkdir(parents=True)
    (tmp_path / "firewalls").mkdir()

    # Minimal base template
    (switches_common / "base.j2").write_text(
        "hostname {{ hostname }}\nip domain name {{ domain_name }}\n",
    )
    (switches_common / "vlans.j2").write_text(
        "{% for vlan in vlans %}vlan {{ vlan.vid }}\n name {{ vlan.name }}\n!\n{% endfor %}\n",
    )
    (switches_common / "stp.j2").write_text("spanning-tree mode rapid-pvst\n")
    (switches_common / "interfaces.j2").write_text(
        "{% for iface in interfaces %}interface {{ iface.name }}\n!\n{% endfor %}\n",
    )
    (switches_common / "security.j2").write_text("ip dhcp snooping\n")

    # Minimal core template
    (tmp_path / "switches").mkdir(exist_ok=True)
    (tmp_path / "switches" / "core.j2").write_text(
        textwrap.dedent("""\
        {% include "switches/common/base.j2" %}
        {% include "switches/common/vlans.j2" %}
        interface Vlan100
         ip address {{ mgmt_ip }} {{ mgmt_mask }}
        !
        {% include "switches/common/stp.j2" %}
        {% include "switches/common/interfaces.j2" %}
        {% include "switches/common/security.j2" %}
        end
        """),
    )

    return tmp_path


@pytest.fixture
def catalogue_yaml(tmp_path: Path) -> Path:
    """Write a minimal firmware_catalogue.yaml for testing."""
    data = {
        "paths": {
            "firmware_root": str(tmp_path / "firmware"),
            "iso_root": str(tmp_path / "isos"),
            "certs_root": str(tmp_path / "certs"),
        },
        "firmware": {
            "cisco_ios": {
                "default": "15.2.7E8",
                "versions": {
                    "15.2.7E8": {
                        "filename": "c2960cx-k8.bin",
                        "sha256": "abc123",
                    },
                },
            },
            "hpe_dl325_gen10": {
                "spp": {
                    "default": "2024.03.0",
                    "versions": {
                        "2024.03.0": {
                            "filename": "spp-2024.iso",
                            "sha256": "def456",
                        },
                    },
                },
                "ilo_firmware": {
                    "default": "2.99",
                    "versions": {
                        "2.99": {
                            "filename": "ilo5_299.bin",
                            "sha256": "ghi789",
                        },
                    },
                },
            },
        },
    }
    path = tmp_path / "firmware_catalogue.yaml"
    path.write_text(yaml.dump(data))
    return path


# ── Renderer tests ────────────────────────────────────────────────────────


class TestConfigRenderer:
    def test_render_device_creates_cfg_file(
        self,
        templates_dir: Path,
        tmp_path: Path,
        minimal_context: RenderContext,
    ) -> None:
        output_dir = tmp_path / "configs"
        renderer = ConfigRenderer(templates_dir, output_dir)
        out = renderer.render_device("switches/core.j2", minimal_context)

        assert out.exists()
        assert out.suffix == ".cfg"
        content = out.read_text()
        assert "hostname sw-core-01" in content
        assert "ip domain name dc1.example.mil" in content
        assert "ip address 10.0.100.10 255.255.255.0" in content

    def test_render_device_sanitises_hostname(
        self,
        templates_dir: Path,
        tmp_path: Path,
        minimal_context: RenderContext,
    ) -> None:
        minimal_context.hostname = "sw-core/01 test"
        renderer = ConfigRenderer(templates_dir, tmp_path / "out")
        out = renderer.render_device("switches/core.j2", minimal_context)
        # Slashes and spaces replaced
        assert "/" not in out.name
        assert " " not in out.name

    def test_render_all_returns_dict(
        self,
        templates_dir: Path,
        tmp_path: Path,
        minimal_context: RenderContext,
    ) -> None:
        renderer = ConfigRenderer(templates_dir, tmp_path / "out")
        results = renderer.render_all([("switches/core.j2", minimal_context)])
        assert "sw-core-01" in results
        assert results["sw-core-01"].exists()

    def test_render_all_raises_on_missing_template(
        self,
        templates_dir: Path,
        tmp_path: Path,
        minimal_context: RenderContext,
    ) -> None:
        renderer = ConfigRenderer(templates_dir, tmp_path / "out")
        with pytest.raises(RuntimeError, match="sw-core-01"):
            renderer.render_all([("switches/nonexistent.j2", minimal_context)])


# ── VLAN + tenant context builders ───────────────────────────────────────


class TestContextBuilders:
    def test_build_vlan_contexts_includes_mgmt_vlans(self) -> None:
        vlans = _build_vlan_contexts([], {})
        vids = {v.vid for v in vlans}
        assert 100 in vids
        assert 600 in vids
        assert 999 not in vids  # blackhole added in template, not context

    def test_build_vlan_contexts_adds_mission_vlans(self) -> None:
        ctx = {
            "mission_tenants": [
                {"name": "MISSION_1"},
            ],
        }
        vlans = _build_vlan_contexts([], ctx)
        vids = {v.vid for v in vlans}
        assert 1100 in vids  # users
        assert 1110 in vids  # apps
        assert 1120 in vids  # data

    def test_build_vlan_contexts_merges_netbox_vlans(self) -> None:
        nb_vlans = [{"vid": 999, "name": "EXTRA", "description": ""}]
        vlans = _build_vlan_contexts(nb_vlans, {})
        vids = {v.vid for v in vlans}
        assert 999 in vids

    def test_build_mission_tenants_defaults(self) -> None:
        ctx: dict[str, Any] = {
            "mission_tenants": [
                {"name": "ALPHA"},
                {"name": "BRAVO"},
            ],
        }
        tenants = _build_mission_tenants(ctx)
        assert len(tenants) == 2
        assert tenants[0].user_vlan == 1100
        assert tenants[1].user_vlan == 1200

    def test_build_mission_tenants_overrides(self) -> None:
        ctx: dict[str, Any] = {
            "mission_tenants": [
                {
                    "name": "CUSTOM",
                    "user_vlan": 2000,
                    "apps_vlan": 2010,
                    "data_vlan": 2020,
                    "user_subnet": "192.168.1.0",
                    "user_gateway": "192.168.1.1",
                },
            ],
        }
        tenants = _build_mission_tenants(ctx)
        assert tenants[0].user_vlan == 2000
        assert tenants[0].user_subnet == "192.168.1.0"


# ── InventoryExporter tests ───────────────────────────────────────────────


class TestInventoryExporter:
    def test_export_writes_yaml(self, tmp_path: Path) -> None:
        exporter = InventoryExporter(tmp_path)
        meta = {
            "name": "D001",
            "bootstrap_subnet": "10.255.0.0/16",
            "laptop_ip": "10.255.255.1",
            "management_vlan": 100,
        }
        specs = {
            "FOC001": {
                "role": "core-switch",
                "hostname": "sw-core-01",
                "platform": "cisco_ios",
                "management_ip": "10.0.100.10",
                "management_subnet": "255.255.255.0",
            },
        }
        out = exporter.export(meta, specs)
        assert out.exists()
        data = yaml.safe_load(out.read_text())
        assert data["deployment"]["name"] == "D001"
        assert "FOC001" in data["devices"]

    def test_export_adds_config_file(self, tmp_path: Path) -> None:
        exporter = InventoryExporter(tmp_path)
        out = exporter.export(
            {"name": "test", "bootstrap_subnet": "", "laptop_ip": "", "management_vlan": 100},
            {"S001": {"role": "core-switch", "hostname": "sw-01"}},
            config_file_map={"S001": "sw-01.cfg"},
        )
        data = yaml.safe_load(out.read_text())
        assert data["devices"]["S001"]["config_file"] == "sw-01.cfg"


# ── FirmwareCatalogue tests ───────────────────────────────────────────────


class TestFirmwareCatalogue:
    def test_load_valid_catalogue(self, catalogue_yaml: Path) -> None:
        cat = FirmwareCatalogue(catalogue_yaml)
        assert "cisco_ios" in cat.list_platforms()

    def test_missing_catalogue_raises(self, tmp_path: Path) -> None:
        with pytest.raises(CatalogueError, match="not found"):
            FirmwareCatalogue(tmp_path / "nonexistent.yaml")

    def test_resolve_network_firmware_default(self, catalogue_yaml: Path) -> None:
        cat = FirmwareCatalogue(catalogue_yaml)
        entry = cat.resolve_network_firmware("cisco_ios")
        assert entry.filename == "c2960cx-k8.bin"
        assert entry.version == "15.2.7E8"

    def test_resolve_network_firmware_unknown_platform(
        self, catalogue_yaml: Path,
    ) -> None:
        cat = FirmwareCatalogue(catalogue_yaml)
        with pytest.raises(CatalogueError, match="cisco_iosxe"):
            cat.resolve_network_firmware("cisco_iosxe")

    def test_resolve_network_firmware_unknown_version(
        self, catalogue_yaml: Path,
    ) -> None:
        cat = FirmwareCatalogue(catalogue_yaml)
        with pytest.raises(CatalogueError, match="99.0"):
            cat.resolve_network_firmware("cisco_ios", version="99.0")

    def test_resolve_spp_iso(self, catalogue_yaml: Path) -> None:
        cat = FirmwareCatalogue(catalogue_yaml)
        entry = cat.resolve_spp_iso("hpe_dl325_gen10")
        assert entry.filename == "spp-2024.iso"

    def test_resolve_ilo_firmware(self, catalogue_yaml: Path) -> None:
        cat = FirmwareCatalogue(catalogue_yaml)
        entry = cat.resolve_ilo_firmware("hpe_dl325_gen10")
        assert entry.filename == "ilo5_299.bin"

    def test_verify_all_missing_strict(self, catalogue_yaml: Path) -> None:
        cat = FirmwareCatalogue(catalogue_yaml)
        entry = cat.resolve_network_firmware("cisco_ios")
        # File doesn't exist on disk
        with pytest.raises(CatalogueError, match="missing"):
            cat.verify_all([entry], strict=True)

    def test_verify_all_missing_non_strict(self, catalogue_yaml: Path) -> None:
        cat = FirmwareCatalogue(catalogue_yaml)
        entry = cat.resolve_network_firmware("cisco_ios")
        missing = cat.verify_all([entry], strict=False)
        assert len(missing) == 1


# ── MediaCollector tests ──────────────────────────────────────────────────


class TestMediaCollector:
    def _make_file(self, path: Path, content: bytes = b"test firmware data") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_collect_arbitrary_copies_file(self, tmp_path: Path) -> None:
        src = self._make_file(tmp_path / "src" / "fw.bin")
        collector = MediaCollector(tmp_path / "bundle")
        cf = collector.collect_arbitrary(src, "firmware")
        assert cf.destination.exists()
        assert cf.destination.name == "fw.bin"

    def test_collect_verifies_correct_checksum(self, tmp_path: Path) -> None:
        content = b"real firmware bytes"
        sha = hashlib.sha256(content).hexdigest()
        src = self._make_file(tmp_path / "fw.bin", content)
        collector = MediaCollector(tmp_path / "bundle")
        cf = collector.collect_arbitrary(src, "firmware", expected_sha256=sha)
        assert cf.verified

    def test_collect_raises_on_checksum_mismatch(self, tmp_path: Path) -> None:
        src = self._make_file(tmp_path / "fw.bin", b"data")
        collector = MediaCollector(tmp_path / "bundle")
        with pytest.raises(ChecksumMismatch):
            collector.collect_arbitrary(src, "firmware", expected_sha256="wrong")

    def test_collect_raises_on_missing_source(self, tmp_path: Path) -> None:
        collector = MediaCollector(tmp_path / "bundle")
        with pytest.raises(FileNotFoundError):
            collector.collect_arbitrary(tmp_path / "nonexistent.bin", "firmware")

    def test_write_checksums_file(self, tmp_path: Path) -> None:
        src = self._make_file(tmp_path / "fw.bin")
        bundle_dir = tmp_path / "bundle"
        collector = MediaCollector(bundle_dir)
        collector.collect_arbitrary(src, "firmware")
        cksum_path = collector.write_checksums_file()
        assert cksum_path.exists()
        content = cksum_path.read_text()
        assert "firmware/fw.bin" in content

    def test_collect_batch_tolerates_failures(self, tmp_path: Path) -> None:
        collector = MediaCollector(tmp_path / "bundle")
        items = [
            {"source": str(tmp_path / "missing.bin"), "sub_dir": "firmware"},
        ]
        successes, errors = collector.collect_batch(items)
        assert len(successes) == 0
        assert len(errors) == 1


# ── BundlePackager tests ──────────────────────────────────────────────────


class TestBundlePackager:
    def _setup_bundle(self, bundle_dir: Path) -> None:
        """Create minimal bundle structure for packager tests."""
        (bundle_dir / "configs").mkdir(parents=True)
        (bundle_dir / "firmware").mkdir()
        (bundle_dir / "isos").mkdir()
        (bundle_dir / "certs").mkdir()
        (bundle_dir / "ansible").mkdir()
        (bundle_dir / "configs" / "sw-core-01.cfg").write_text("hostname sw-core-01\n")
        (bundle_dir / "inventory.yaml").write_text("deployment:\n  name: test\n")

    def test_write_manifest(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "D001"
        self._setup_bundle(bundle_dir)
        packager = BundlePackager(bundle_dir, "D001")
        packager.register_configs(bundle_dir / "configs")
        manifest = packager.write_manifest(notes="test run")
        data = yaml.safe_load(manifest.read_text())
        assert data["deployment_name"] == "D001"
        assert "sw-core-01.cfg" in data["contents"]["configs"]

    def test_write_checksums(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "D001"
        self._setup_bundle(bundle_dir)
        packager = BundlePackager(bundle_dir, "D001")
        cksum = packager.write_checksums()
        assert cksum.exists()
        lines = [l for l in cksum.read_text().splitlines() if l]
        assert any("configs/sw-core-01.cfg" in l for l in lines)

    def test_validate_complete_bundle(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "D001"
        self._setup_bundle(bundle_dir)
        packager = BundlePackager(bundle_dir, "D001")
        packager.write_manifest()
        packager.write_checksums()
        errors = packager.validate()
        assert errors == []

    def test_validate_missing_inventory(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "D001"
        self._setup_bundle(bundle_dir)
        (bundle_dir / "inventory.yaml").unlink()
        packager = BundlePackager(bundle_dir, "D001")
        packager.write_manifest()
        packager.write_checksums()
        errors = packager.validate()
        assert any("inventory" in e.lower() for e in errors)

    def test_write_ansible_inventory(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "D001"
        bundle_dir.mkdir()
        packager = BundlePackager(bundle_dir, "D001")
        specs = {
            "SER001": {
                "role": "core-switch",
                "hostname": "sw-core-01",
                "management_ip": "10.0.100.10",
            },
            "SER002": {
                "role": "access-switch",
                "hostname": "sw-acc-01",
                "management_ip": "10.0.100.11",
            },
        }
        hosts = packager.write_ansible_inventory(specs)
        content = hosts.read_text()
        assert "[core-switch]" in content
        assert "sw-core-01 ansible_host=10.0.100.10" in content
        assert "[access-switch]" in content

    def test_package_archive(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "D001"
        self._setup_bundle(bundle_dir)
        packager = BundlePackager(bundle_dir, "D001")
        packager.write_manifest()
        packager.write_checksums()
        archive = packager.package_archive(tmp_path / "archives")
        assert archive.exists()
        assert archive.suffix == ".gz"
