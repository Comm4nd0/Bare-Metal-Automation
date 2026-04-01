"""Bundle packager — assemble the final deployment bundle.

Collects all generated artefacts (configs, inventory, firmware, ISOs,
ansible files, checksums) and writes a manifest.yaml that describes the
complete bundle.  Optionally creates a .tar.gz archive.

Bundle directory layout after packaging:

    <bundle_name>/
    ├── manifest.yaml          ← describes everything in the bundle
    ├── checksums.sha256       ← sha256sum-compatible checksum file
    ├── inventory.yaml         ← BMA deployment inventory
    ├── configs/
    │   └── <hostname>.cfg     ← rendered device configs
    ├── firmware/
    │   └── *.bin              ← network device firmware images
    ├── isos/
    │   └── *.iso              ← SPP, OS, kickstart ISOs
    ├── certs/
    │   └── *.pem / *.cer      ← TLS certificates
    └── ansible/
        ├── hosts.ini          ← Ansible inventory
        └── group_vars/        ← copied from ansible/ tree
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class BundleManifest:
    """In-memory representation of manifest.yaml."""

    bundle_name: str
    generated_at: str
    deployment_name: str
    site_slug: str = ""
    bma_version: str = "0.1.0"
    configs: list[str] = field(default_factory=list)
    firmware: list[str] = field(default_factory=list)
    isos: list[str] = field(default_factory=list)
    certs: list[str] = field(default_factory=list)
    ansible_files: list[str] = field(default_factory=list)
    device_count: int = 0
    notes: str = ""


class BundlePackager:
    """Assemble artefacts into a validated deployment bundle.

    Args:
        bundle_dir:       Root staging directory for the bundle.
        deployment_name:  Human-readable deployment identifier.
        site_slug:        NetBox site slug (informational).
    """

    def __init__(
        self,
        bundle_dir: Path,
        deployment_name: str,
        site_slug: str = "",
    ) -> None:
        self.bundle_dir = bundle_dir
        self.deployment_name = deployment_name
        self.site_slug = site_slug

        self._manifest = BundleManifest(
            bundle_name=bundle_dir.name,
            generated_at=datetime.datetime.utcnow().isoformat() + "Z",
            deployment_name=deployment_name,
            site_slug=site_slug,
        )

    # ── Public API ────────────────────────────────────────────────────────

    def register_configs(self, config_dir: Path) -> None:
        """Register rendered config files from config_dir."""
        cfgs = sorted(config_dir.glob("*.cfg"))
        self._manifest.configs = [f.name for f in cfgs]
        self._manifest.device_count = len(cfgs)
        logger.info("Registered %d config file(s)", len(cfgs))

    def register_firmware(self, firmware_dir: Path) -> None:
        """Register firmware files from firmware_dir."""
        files = sorted(firmware_dir.glob("*"))
        self._manifest.firmware = [f.name for f in files if f.is_file()]
        logger.info("Registered %d firmware file(s)", len(self._manifest.firmware))

    def register_isos(self, isos_dir: Path) -> None:
        """Register ISO images from isos_dir."""
        files = sorted(isos_dir.glob("*.iso"))
        self._manifest.isos = [f.name for f in files]
        logger.info("Registered %d ISO file(s)", len(self._manifest.isos))

    def register_certs(self, certs_dir: Path) -> None:
        """Register certificate files from certs_dir."""
        files = sorted(certs_dir.glob("*"))
        self._manifest.certs = [f.name for f in files if f.is_file()]
        logger.info("Registered %d certificate file(s)", len(self._manifest.certs))

    def register_ansible(self, ansible_dir: Path) -> None:
        """Register Ansible files from ansible_dir."""
        files = sorted(ansible_dir.rglob("*"))
        self._manifest.ansible_files = [
            str(f.relative_to(ansible_dir)) for f in files if f.is_file()
        ]
        logger.info(
            "Registered %d ansible file(s)", len(self._manifest.ansible_files),
        )

    def write_ansible_inventory(
        self,
        device_specs: dict[str, dict[str, Any]],
    ) -> Path:
        """Generate a minimal Ansible hosts.ini from device specs.

        Groups devices by role.  Each host line uses the hostname and
        ``ansible_host`` variable pointing to the management IP.

        Returns:
            Path to written hosts.ini.
        """
        ansible_dir = self.bundle_dir / "ansible"
        ansible_dir.mkdir(parents=True, exist_ok=True)
        out = ansible_dir / "hosts.ini"

        # Group by role
        by_role: dict[str, list[tuple[str, str]]] = {}
        for _serial, spec in device_specs.items():
            role = spec.get("role", "unknown")
            hostname = spec.get("hostname", "unknown")
            mgmt_ip = spec.get("management_ip", "")
            by_role.setdefault(role, []).append((hostname, mgmt_ip))

        lines: list[str] = []
        for role in sorted(by_role):
            lines.append(f"[{role}]")
            for hostname, ip in sorted(by_role[role]):
                if ip:
                    lines.append(f"{hostname} ansible_host={ip}")
                else:
                    lines.append(hostname)
            lines.append("")

        out.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Wrote ansible/hosts.ini with %d group(s)", len(by_role))
        return out

    def write_manifest(self, notes: str = "") -> Path:
        """Write manifest.yaml to the bundle root.

        Returns:
            Path to written manifest.yaml.
        """
        self._manifest.notes = notes
        manifest_data = {
            "bundle_name": self._manifest.bundle_name,
            "generated_at": self._manifest.generated_at,
            "deployment_name": self._manifest.deployment_name,
            "site_slug": self._manifest.site_slug,
            "bma_version": self._manifest.bma_version,
            "device_count": self._manifest.device_count,
            "contents": {
                "configs": self._manifest.configs,
                "firmware": self._manifest.firmware,
                "isos": self._manifest.isos,
                "certs": self._manifest.certs,
                "ansible": self._manifest.ansible_files,
            },
            "notes": self._manifest.notes,
        }

        out = self.bundle_dir / "manifest.yaml"
        out.write_text(
            yaml.dump(manifest_data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("Wrote manifest.yaml → %s", out)
        return out

    def write_checksums(self) -> Path:
        """Compute and write checksums.sha256 for all bundle files.

        Walks the entire bundle directory (excluding checksums.sha256 itself
        and manifest.yaml to keep them stable) and writes one
        ``<sha256>  <relative_path>`` line per file.

        Returns:
            Path to written checksums.sha256.
        """
        out = self.bundle_dir / "checksums.sha256"
        skip = {out, self.bundle_dir / "manifest.yaml"}

        lines: list[str] = []
        for path in sorted(self.bundle_dir.rglob("*")):
            if not path.is_file():
                continue
            if path in skip:
                continue
            sha256 = _sha256_file(path)
            rel = path.relative_to(self.bundle_dir).as_posix()
            lines.append(f"{sha256}  {rel}")

        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Wrote checksums.sha256 (%d entries)", len(lines))
        return out

    def validate(self) -> list[str]:
        """Validate the bundle for completeness.

        Checks:
        - inventory.yaml exists
        - at least one config file present
        - manifest.yaml exists
        - checksums.sha256 exists

        Returns:
            List of validation error strings (empty if valid).
        """
        errors: list[str] = []

        required = [
            self.bundle_dir / "inventory.yaml",
            self.bundle_dir / "manifest.yaml",
            self.bundle_dir / "checksums.sha256",
        ]
        for req in required:
            if not req.exists():
                errors.append(f"Missing required file: {req.name}")

        config_dir = self.bundle_dir / "configs"
        if not config_dir.exists() or not any(config_dir.glob("*.cfg")):
            errors.append("No rendered config files found in configs/")

        if errors:
            logger.warning(
                "Bundle validation failed with %d issue(s):\n%s",
                len(errors),
                "\n".join(f"  • {e}" for e in errors),
            )
        else:
            logger.info("Bundle validation passed — bundle is complete")

        return errors

    def package_archive(
        self,
        output_dir: Path,
    ) -> Path:
        """Create a compressed .tar.gz archive of the bundle directory.

        Args:
            output_dir: Where to write the archive (outside bundle_dir).

        Returns:
            Path to the .tar.gz file.
        """
        archive_name = f"{self._manifest.bundle_name}.tar.gz"
        archive_path = output_dir / archive_name
        output_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(self.bundle_dir, arcname=self._manifest.bundle_name)

        size = archive_path.stat().st_size
        logger.info(
            "Created bundle archive: %s (%s)",
            archive_path,
            _human_bytes(size),
        )
        return archive_path


# ── Module-level helpers ──────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file using streaming I/O."""
    h = hashlib.sha256()
    buf = 4 * 1024 * 1024  # 4 MiB
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(buf)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _human_bytes(size: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size //= 1024
    return f"{size:.1f} TB"
