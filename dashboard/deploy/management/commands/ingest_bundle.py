"""
Management command: ingest_bundle

Validates and ingests a deployment bundle from a USB drive (or any path) into
the Django database, creating Deployment + DeploymentPhase + DeploymentDevice
records ready for Sprint 4's phase execution commands.

Usage:
    python manage.py ingest_bundle --path /media/usb/deployment-bundle/
    python manage.py ingest_bundle --path /path/to/bundle --validate-only
    python manage.py ingest_bundle --path /path/to/bundle --force
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import yaml
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from deploy.models import (
    PHASE_NAMES,
    Deployment,
    DeploymentDevice,
    DeploymentPhase,
    DeploymentStatus,
)

logger = logging.getLogger(__name__)

# Expected top-level keys in manifest.yaml
REQUIRED_MANIFEST_KEYS = {"site_name", "site_slug", "template_name", "template_version", "devices"}

# Expected keys per device in the inventory
REQUIRED_DEVICE_KEYS = {"serial_number", "hostname", "role", "platform"}

# Valid device roles and platforms
VALID_ROLES = {
    "core-switch",
    "access-switch",
    "distribution-switch",
    "border-router",
    "perimeter-firewall",
    "compute-node",
    "management-server",
    "ntp-server",
}

VALID_PLATFORMS = {
    "cisco_ios",
    "cisco_iosxe",
    "cisco_asa",
    "cisco_ftd",
    "hpe_dl325_gen10",
    "hpe_dl360_gen10",
    "hpe_dl380_gen10",
    "meinberg_lantime",
}


class BundleValidationError(Exception):
    """Raised when bundle validation fails."""


class Command(BaseCommand):
    help = "Ingest a deployment bundle into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            required=True,
            help="Path to the deployment bundle directory.",
        )
        parser.add_argument(
            "--validate-only",
            action="store_true",
            default=False,
            help="Validate the bundle without writing to the database.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Re-ingest even if a deployment with the same site_slug + template_version exists.",
        )
        parser.add_argument(
            "--operator",
            default=None,
            help="Username of the operator performing the ingestion.",
        )

    def handle(self, *args, **options):
        bundle_path = Path(options["path"]).resolve()
        validate_only: bool = options["validate_only"]
        force: bool = options["force"]
        operator_name: str | None = options["operator"]

        self.stdout.write(f"Bundle path: {bundle_path}")

        # ------------------------------------------------------------------
        # Step 1 — Validate bundle directory exists
        # ------------------------------------------------------------------
        if not bundle_path.is_dir():
            raise CommandError(f"Bundle path does not exist or is not a directory: {bundle_path}")

        # ------------------------------------------------------------------
        # Step 2 — Load and validate checksums.sha256
        # ------------------------------------------------------------------
        self.stdout.write("Validating checksums…")
        try:
            checksums = self._load_checksums(bundle_path)
            self._verify_checksums(bundle_path, checksums)
        except BundleValidationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"  {len(checksums)} files verified OK"))

        # ------------------------------------------------------------------
        # Step 3 — Load and validate manifest.yaml
        # ------------------------------------------------------------------
        self.stdout.write("Loading manifest…")
        manifest_path = bundle_path / "manifest.yaml"
        if not manifest_path.exists():
            raise CommandError("manifest.yaml not found in bundle directory")

        manifest_hash = self._sha256(manifest_path)
        manifest = self._load_yaml(manifest_path)

        try:
            self._validate_manifest_schema(manifest)
        except BundleValidationError as exc:
            raise CommandError(str(exc)) from exc

        site_name: str = manifest["site_name"]
        site_slug: str = manifest["site_slug"]
        template_name: str = manifest["template_name"]
        template_version: str = manifest["template_version"]
        devices: list[dict] = manifest["devices"]

        self.stdout.write(
            self.style.SUCCESS(
                f"  Site: {site_name} | Template: {template_name} v{template_version} | Devices: {len(devices)}"
            )
        )

        # ------------------------------------------------------------------
        # Step 4 — Validate inventory.yaml (if present) matches manifest
        # ------------------------------------------------------------------
        inventory_path = bundle_path / "inventory.yaml"
        if inventory_path.exists():
            self.stdout.write("Validating inventory.yaml…")
            inventory = self._load_yaml(inventory_path)
            try:
                self._validate_inventory_schema(inventory)
            except BundleValidationError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(self.style.SUCCESS("  inventory.yaml schema OK"))

        # ------------------------------------------------------------------
        # Step 5 — Validate artefact paths for each device
        # ------------------------------------------------------------------
        self.stdout.write("Checking device artefacts…")
        artefact_warnings: list[str] = []
        for dev in devices:
            for field in ("config_path", "firmware_path", "os_media_path"):
                rel = dev.get(field, "")
                if rel:
                    full = bundle_path / rel
                    if not full.exists():
                        artefact_warnings.append(f"  WARN: {dev['hostname']} {field} missing: {rel}")

        if artefact_warnings:
            for w in artefact_warnings:
                self.stdout.write(self.style.WARNING(w))
        else:
            self.stdout.write(self.style.SUCCESS("  All device artefacts present"))

        # ------------------------------------------------------------------
        # Step 6 — Validate-only exit
        # ------------------------------------------------------------------
        if validate_only:
            self.stdout.write(self.style.SUCCESS("\nBundle validation passed (--validate-only, not ingested)."))
            return

        # ------------------------------------------------------------------
        # Step 7 — Check for duplicate ingestion
        # ------------------------------------------------------------------
        existing = Deployment.objects.filter(
            site_slug=site_slug,
            template_version=template_version,
        ).exclude(status=DeploymentStatus.ABORTED)

        if existing.exists() and not force:
            raise CommandError(
                f"Deployment for {site_slug} v{template_version} already exists "
                f"(id={existing.first().pk}). Use --force to re-ingest."
            )

        # ------------------------------------------------------------------
        # Step 8 — Resolve operator
        # ------------------------------------------------------------------
        operator: User | None = None
        if operator_name:
            try:
                operator = User.objects.get(username=operator_name)
            except User.DoesNotExist:
                raise CommandError(f"Operator user '{operator_name}' not found.")

        # ------------------------------------------------------------------
        # Step 9 — Write to database
        # ------------------------------------------------------------------
        self.stdout.write("Ingesting into database…")
        with transaction.atomic():
            deployment = Deployment.objects.create(
                site_name=site_name,
                site_slug=site_slug,
                template_name=template_name,
                template_version=template_version,
                bundle_path=str(bundle_path),
                manifest_hash=manifest_hash,
                status=DeploymentStatus.INGESTED,
                operator=operator,
            )

            # Create all 11 phases (0-10) as pending
            for phase_number, phase_name in PHASE_NAMES.items():
                DeploymentPhase.objects.create(
                    deployment=deployment,
                    phase_number=phase_number,
                    phase_name=phase_name,
                )

            # Create one DeploymentDevice per device entry
            for dev in devices:
                DeploymentDevice.objects.create(
                    deployment=deployment,
                    serial_number=dev["serial_number"],
                    hostname=dev["hostname"],
                    role=dev.get("role", ""),
                    platform=dev.get("platform", ""),
                    config_path=dev.get("config_path", ""),
                    firmware_path=dev.get("firmware_path", ""),
                    os_media_path=dev.get("os_media_path", ""),
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDeployment #{deployment.pk} ingested: {site_name} ({site_slug}) "
                f"— {len(devices)} devices, 11 phases created."
            )
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_checksums(self, bundle_path: Path) -> dict[str, str]:
        """Load checksums.sha256 and return {relative_path: expected_sha256}."""
        checksum_file = bundle_path / "checksums.sha256"
        if not checksum_file.exists():
            raise BundleValidationError("checksums.sha256 not found in bundle directory")
        checksums: dict[str, str] = {}
        for line in checksum_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise BundleValidationError(f"Invalid checksum line: {line!r}")
            digest, path = parts
            checksums[path.strip()] = digest.lower()
        return checksums

    def _verify_checksums(self, bundle_path: Path, checksums: dict[str, str]) -> None:
        """SHA-256 verify every file listed in checksums against disk."""
        errors: list[str] = []
        for rel_path, expected in checksums.items():
            file_path = bundle_path / rel_path
            if not file_path.exists():
                errors.append(f"File listed in checksums not found: {rel_path}")
                continue
            actual = self._sha256(file_path)
            if actual != expected:
                errors.append(f"Checksum mismatch for {rel_path}: expected {expected}, got {actual}")
        if errors:
            raise BundleValidationError("Checksum validation failed:\n" + "\n".join(errors))

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        with path.open() as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise BundleValidationError(f"{path.name} must be a YAML mapping, got {type(data).__name__}")
        return data

    @staticmethod
    def _validate_manifest_schema(manifest: dict) -> None:
        missing = REQUIRED_MANIFEST_KEYS - set(manifest.keys())
        if missing:
            raise BundleValidationError(f"manifest.yaml missing required keys: {missing}")

        devices = manifest.get("devices", [])
        if not isinstance(devices, list) or not devices:
            raise BundleValidationError("manifest.yaml 'devices' must be a non-empty list")

        errors: list[str] = []
        for i, dev in enumerate(devices):
            missing_dev = REQUIRED_DEVICE_KEYS - set(dev.keys())
            if missing_dev:
                errors.append(f"Device[{i}] missing keys: {missing_dev}")
                continue
            if dev.get("role") and dev["role"] not in VALID_ROLES:
                errors.append(f"Device[{i}] '{dev.get('hostname')}' has unknown role: {dev['role']}")
            if dev.get("platform") and dev["platform"] not in VALID_PLATFORMS:
                errors.append(
                    f"Device[{i}] '{dev.get('hostname')}' has unknown platform: {dev['platform']}"
                )

        if errors:
            raise BundleValidationError("Manifest device schema errors:\n" + "\n".join(errors))

    @staticmethod
    def _validate_inventory_schema(inventory: dict) -> None:
        """Basic schema check for inventory.yaml."""
        required = {"site_name", "bootstrap_subnet", "devices"}
        missing = required - set(inventory.keys())
        if missing:
            raise BundleValidationError(f"inventory.yaml missing required keys: {missing}")
        if not isinstance(inventory.get("devices"), dict):
            raise BundleValidationError("inventory.yaml 'devices' must be a mapping keyed by serial number")
