"""Firmware catalogue loader and resolver.

Loads firmware_catalogue.yaml, resolves (platform, version) pairs to
absolute file paths on the file server, and verifies that the files
actually exist before the media collection phase starts.

Expected firmware_catalogue.yaml structure:

    firmware:
      cisco_ios:
        default: "15.2.7E8"
        versions:
          "15.2.7E8":
            filename: c2960cx-universalk9-mz.152-7.E8.bin
            sha256: abc123...
            platforms: [cisco_ios]
      hpe_dl325_gen10:
        spp:
          default: "2024.03.0"
          versions:
            "2024.03.0":
              filename: P58473_001_spp-2024.03.0-SPP2024030.2024_0313.11.iso
              sha256: def456...
        ilo_firmware:
          default: "2.99"
          versions:
            "2.99":
              filename: ilo5_299.bin
              sha256: ghi789...

    paths:
      firmware_root: /mnt/fileserver/firmware
      iso_root: /mnt/fileserver/isos
      certs_root: /mnt/fileserver/certs
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class CatalogueError(Exception):
    """Raised when the firmware catalogue is missing, malformed, or incomplete."""


@dataclass
class FirmwareEntry:
    """A resolved firmware file entry."""

    platform: str
    version: str
    filename: str
    sha256: str
    full_path: Path
    exists: bool


class FirmwareCatalogue:
    """Load firmware_catalogue.yaml and resolve file paths.

    Args:
        catalogue_path: Path to firmware_catalogue.yaml.
        firmware_root:  Override for the root directory (overrides catalogue value).
        iso_root:       Override for ISO root directory.
        certs_root:     Override for certificates root directory.
    """

    def __init__(
        self,
        catalogue_path: Path,
        firmware_root: Path | None = None,
        iso_root: Path | None = None,
        certs_root: Path | None = None,
    ) -> None:
        self.catalogue_path = catalogue_path
        self._data = self._load(catalogue_path)

        paths_cfg = self._data.get("paths", {})
        self.firmware_root = firmware_root or Path(
            paths_cfg.get("firmware_root", "/mnt/fileserver/firmware"),
        )
        self.iso_root = iso_root or Path(
            paths_cfg.get("iso_root", "/mnt/fileserver/isos"),
        )
        self.certs_root = certs_root or Path(
            paths_cfg.get("certs_root", "/mnt/fileserver/certs"),
        )

    # ── Public API ────────────────────────────────────────────────────────

    def resolve_network_firmware(
        self,
        platform: str,
        version: str | None = None,
    ) -> FirmwareEntry:
        """Resolve firmware file for a network device platform.

        Args:
            platform: BMA platform string, e.g. "cisco_ios".
            version:  Specific version string; uses catalogue default if None.

        Returns:
            FirmwareEntry with full path and existence check.

        Raises:
            CatalogueError: platform or version not found in catalogue.
        """
        fw_section = self._data.get("firmware", {})
        plat_data = fw_section.get(platform)
        if not plat_data:
            raise CatalogueError(
                f"Platform '{platform}' not found in firmware catalogue",
            )

        resolved_version = version or plat_data.get("default")
        if not resolved_version:
            raise CatalogueError(
                f"No default version for platform '{platform}' and no version specified",
            )

        versions = plat_data.get("versions", {})
        entry_data = versions.get(str(resolved_version))
        if not entry_data:
            available = list(versions.keys())
            raise CatalogueError(
                f"Version '{resolved_version}' not found for platform '{platform}'. "
                f"Available: {available}",
            )

        filename = entry_data.get("filename", "")
        sha256 = entry_data.get("sha256", "")
        full_path = self.firmware_root / filename

        return FirmwareEntry(
            platform=platform,
            version=str(resolved_version),
            filename=filename,
            sha256=sha256,
            full_path=full_path,
            exists=full_path.exists(),
        )

    def resolve_spp_iso(
        self,
        platform: str,
        version: str | None = None,
    ) -> FirmwareEntry:
        """Resolve the HPE SPP ISO for a server platform."""
        return self._resolve_server_component(platform, "spp", version)

    def resolve_ilo_firmware(
        self,
        platform: str,
        version: str | None = None,
    ) -> FirmwareEntry:
        """Resolve the HPE iLO firmware for a server platform."""
        return self._resolve_server_component(platform, "ilo_firmware", version)

    def resolve_os_iso(
        self,
        platform: str,
        os_name: str,
        version: str | None = None,
    ) -> FirmwareEntry:
        """Resolve an OS installation ISO.

        Args:
            platform:  BMA platform string (e.g. "hpe_dl325_gen10").
            os_name:   OS identifier, e.g. "rhel9" or "windows2022".
            version:   Specific version; uses default if None.
        """
        iso_section = self._data.get("os_isos", {})
        os_data = iso_section.get(os_name)
        if not os_data:
            raise CatalogueError(
                f"OS '{os_name}' not found in firmware catalogue os_isos section",
            )

        resolved_version = version or os_data.get("default")
        if not resolved_version:
            raise CatalogueError(f"No default version for OS '{os_name}'")

        versions = os_data.get("versions", {})
        entry_data = versions.get(str(resolved_version))
        if not entry_data:
            raise CatalogueError(
                f"Version '{resolved_version}' not found for OS '{os_name}'",
            )

        filename = entry_data.get("filename", "")
        sha256 = entry_data.get("sha256", "")
        full_path = self.iso_root / filename

        return FirmwareEntry(
            platform=platform,
            version=str(resolved_version),
            filename=filename,
            sha256=sha256,
            full_path=full_path,
            exists=full_path.exists(),
        )

    def verify_all(
        self,
        entries: list[FirmwareEntry],
        *,
        strict: bool = True,
    ) -> list[FirmwareEntry]:
        """Check that all resolved entries actually exist on disk.

        Args:
            entries: List of FirmwareEntry to check.
            strict:  If True, raises CatalogueError on any missing file.

        Returns:
            List of missing entries (empty if all present).

        Raises:
            CatalogueError: If strict=True and any files are missing.
        """
        missing: list[FirmwareEntry] = []
        for entry in entries:
            entry.exists = entry.full_path.exists()
            if not entry.exists:
                logger.warning("Missing firmware file: %s", entry.full_path)
                missing.append(entry)
            else:
                logger.debug("Verified: %s", entry.full_path)

        if missing and strict:
            paths = "\n".join(f"  • {e.full_path}" for e in missing)
            raise CatalogueError(
                f"{len(missing)} firmware file(s) missing from file server:\n{paths}",
            )

        return missing

    def list_platforms(self) -> list[str]:
        """Return all platform keys in the firmware catalogue."""
        return list(self._data.get("firmware", {}).keys())

    # ── Private helpers ───────────────────────────────────────────────────

    def _resolve_server_component(
        self,
        platform: str,
        component: str,
        version: str | None,
    ) -> FirmwareEntry:
        """Resolve a named server firmware component (spp, ilo_firmware)."""
        fw_section = self._data.get("firmware", {})
        plat_data = fw_section.get(platform)
        if not plat_data:
            raise CatalogueError(
                f"Platform '{platform}' not found in firmware catalogue",
            )

        comp_data = plat_data.get(component)
        if not comp_data:
            raise CatalogueError(
                f"Component '{component}' not found for platform '{platform}'",
            )

        resolved_version = version or comp_data.get("default")
        if not resolved_version:
            raise CatalogueError(
                f"No default version for '{platform}.{component}'",
            )

        versions = comp_data.get("versions", {})
        entry_data = versions.get(str(resolved_version))
        if not entry_data:
            available = list(versions.keys())
            raise CatalogueError(
                f"Version '{resolved_version}' not found for "
                f"'{platform}.{component}'. Available: {available}",
            )

        filename = entry_data.get("filename", "")
        sha256 = entry_data.get("sha256", "")
        # SPP ISOs live in iso_root, firmware binaries in firmware_root
        root = self.iso_root if component == "spp" else self.firmware_root
        full_path = root / filename

        return FirmwareEntry(
            platform=platform,
            version=str(resolved_version),
            filename=filename,
            sha256=sha256,
            full_path=full_path,
            exists=full_path.exists(),
        )

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        """Load and validate the YAML catalogue file."""
        if not path.exists():
            raise CatalogueError(
                f"Firmware catalogue not found: {path}",
            )
        try:
            with path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except yaml.YAMLError as e:
            raise CatalogueError(
                f"Failed to parse firmware catalogue '{path}': {e}",
            ) from e

        if not isinstance(data, dict):
            raise CatalogueError(
                f"Firmware catalogue '{path}' must be a YAML mapping at the top level",
            )

        return data
