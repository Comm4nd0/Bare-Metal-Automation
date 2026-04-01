"""Tests for the ingest_bundle management command."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from deploy.models import PHASE_NAMES, Deployment
from django.core.management import call_command
from django.core.management.base import CommandError

# ---------------------------------------------------------------------------
# Helpers for building a valid bundle on disk
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_bundle(tmp_path: Path, *, devices: list[dict] | None = None) -> Path:
    """
    Write a minimal but valid deployment bundle to tmp_path.
    Returns the bundle directory path.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    if devices is None:
        devices = [
            {
                "serial_number": "FCW2345A001",
                "hostname": "core-sw-01",
                "role": "core-switch",
                "platform": "cisco_iosxe",
                "config_path": "configs/core-sw-01.cfg",
            },
            {
                "serial_number": "FCW2345A002",
                "hostname": "access-sw-01",
                "role": "access-switch",
                "platform": "cisco_iosxe",
                "config_path": "configs/access-sw-01.cfg",
            },
        ]

    manifest = {
        "site_name": "Test Site",
        "site_slug": "test-site",
        "template_name": "datacenter-v2",
        "template_version": "2.3.1",
        "devices": devices,
    }

    # Write manifest
    manifest_path = bundle / "manifest.yaml"
    manifest_path.write_text(yaml.dump(manifest))

    # Write config files referenced by devices
    (bundle / "configs").mkdir(exist_ok=True)
    for dev in devices:
        if "config_path" in dev:
            (bundle / dev["config_path"]).write_text(f"! config for {dev['hostname']}\n")

    # Build checksums.sha256
    checksums_lines = []
    for f in bundle.rglob("*"):
        if f.is_file() and f.name != "checksums.sha256":
            rel = f.relative_to(bundle)
            checksums_lines.append(f"{sha256_file(f)}  {rel}")

    checksum_file = bundle / "checksums.sha256"
    checksum_file.write_text("\n".join(checksums_lines) + "\n")

    return bundle


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIngestBundle:
    def test_basic_ingestion(self, tmp_path):
        bundle = make_bundle(tmp_path)
        call_command("ingest_bundle", path=str(bundle))

        assert Deployment.objects.count() == 1
        deployment = Deployment.objects.first()
        assert deployment.site_name == "Test Site"
        assert deployment.site_slug == "test-site"
        assert deployment.template_version == "2.3.1"
        assert deployment.status == "ingested"

    def test_phases_created(self, tmp_path):
        bundle = make_bundle(tmp_path)
        call_command("ingest_bundle", path=str(bundle))

        deployment = Deployment.objects.first()
        assert deployment.phases.count() == 11
        phase_names = set(deployment.phases.values_list("phase_name", flat=True))
        assert phase_names == set(PHASE_NAMES.values())

    def test_devices_created(self, tmp_path):
        bundle = make_bundle(tmp_path)
        call_command("ingest_bundle", path=str(bundle))

        deployment = Deployment.objects.first()
        assert deployment.devices.count() == 2
        hostnames = set(deployment.devices.values_list("hostname", flat=True))
        assert hostnames == {"core-sw-01", "access-sw-01"}

    def test_validate_only_does_not_write(self, tmp_path):
        bundle = make_bundle(tmp_path)
        call_command("ingest_bundle", path=str(bundle), validate_only=True)
        assert Deployment.objects.count() == 0

    def test_missing_bundle_path_raises(self, tmp_path):
        with pytest.raises(CommandError, match="does not exist"):
            call_command("ingest_bundle", path=str(tmp_path / "nonexistent"))

    def test_missing_checksums_raises(self, tmp_path):
        bundle = make_bundle(tmp_path)
        (bundle / "checksums.sha256").unlink()
        with pytest.raises(CommandError, match="checksums.sha256"):
            call_command("ingest_bundle", path=str(bundle))

    def test_checksum_mismatch_raises(self, tmp_path):
        bundle = make_bundle(tmp_path)
        # Corrupt one config file after checksums were written
        (bundle / "configs" / "core-sw-01.cfg").write_text("tampered content!")
        with pytest.raises(CommandError, match="Checksum mismatch"):
            call_command("ingest_bundle", path=str(bundle))

    def test_missing_manifest_raises(self, tmp_path):
        bundle = make_bundle(tmp_path)
        (bundle / "manifest.yaml").unlink()
        # Rebuild checksums without manifest
        (bundle / "checksums.sha256").write_text("")
        with pytest.raises(CommandError, match="manifest.yaml"):
            call_command("ingest_bundle", path=str(bundle))

    def test_duplicate_ingestion_blocked(self, tmp_path):
        bundle = make_bundle(tmp_path)
        call_command("ingest_bundle", path=str(bundle))
        with pytest.raises(CommandError, match="already exists"):
            call_command("ingest_bundle", path=str(bundle))

    def test_force_allows_re_ingestion(self, tmp_path):
        bundle = make_bundle(tmp_path)
        call_command("ingest_bundle", path=str(bundle))
        call_command("ingest_bundle", path=str(bundle), force=True)
        assert Deployment.objects.count() == 2

    def test_invalid_role_raises(self, tmp_path):
        bundle = make_bundle(
            tmp_path,
            devices=[{
                "serial_number": "FCW001",
                "hostname": "sw-01",
                "role": "not-a-real-role",
                "platform": "cisco_iosxe",
            }],
        )
        with pytest.raises(CommandError, match="unknown role"):
            call_command("ingest_bundle", path=str(bundle))

    def test_invalid_platform_raises(self, tmp_path):
        bundle = make_bundle(
            tmp_path,
            devices=[{
                "serial_number": "FCW001",
                "hostname": "sw-01",
                "role": "core-switch",
                "platform": "not-a-real-platform",
            }],
        )
        with pytest.raises(CommandError, match="unknown platform"):
            call_command("ingest_bundle", path=str(bundle))

    def test_missing_manifest_keys_raises(self, tmp_path):
        bundle = tmp_path / "bad-bundle"
        bundle.mkdir()
        # Write manifest without required keys
        (bundle / "manifest.yaml").write_text(yaml.dump({"site_name": "X"}))
        checksum_path = bundle / "checksums.sha256"
        manifest_hash = sha256_file(bundle / "manifest.yaml")
        checksum_path.write_text(f"{manifest_hash}  manifest.yaml\n")

        with pytest.raises(CommandError, match="missing required keys"):
            call_command("ingest_bundle", path=str(bundle))

    def test_manifest_hash_stored(self, tmp_path):
        bundle = make_bundle(tmp_path)
        call_command("ingest_bundle", path=str(bundle))
        deployment = Deployment.objects.first()
        expected = sha256_file(bundle / "manifest.yaml")
        assert deployment.manifest_hash == expected
