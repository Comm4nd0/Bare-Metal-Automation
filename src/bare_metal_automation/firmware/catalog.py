"""Firmware catalog — manage a versioned inventory of firmware images.

The catalog is a YAML file that declares every firmware image available for
deployment, grouped by platform.  Each entry includes the target version,
filename, MD5 checksum, minimum compatible version (for safe upgrade paths),
and optional release notes.

Example catalog YAML::

    platforms:
      cisco_ios:
        - version: "15.2(4)M11"
          filename: "c2960x-universalk9-mz.152-4.M11.bin"
          md5: "a1b2c3d4e5f6..."
          min_version: "15.2(4)M7"
          release_notes: "Security fixes for CVE-2024-xxxx"
          recommended: true
        - version: "15.2(4)M7"
          filename: "c2960x-universalk9-mz.152-4.M7.bin"
          md5: "f6e5d4c3b2a1..."
      cisco_iosxe:
        - version: "17.09.04a"
          filename: "cat9k_iosxe.17.09.04a.SPA.bin"
          md5: "..."
          min_version: "17.06.01"
          recommended: true
      hpe_ilo5:
        - version: "2.81"
          filename: "ilo5_281.bin"
          md5: "..."
          recommended: true
      meinberg_lantime:
        - version: "7.08.004"
          filename: "lantime-7.08.004.upd"
          md5: "..."
          recommended: true
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class FirmwareEntry:
    """A single firmware image in the catalog."""

    platform: str
    version: str
    filename: str
    md5: str = ""
    min_version: str = ""
    release_notes: str = ""
    recommended: bool = False

    def is_upgrade_safe(self, current_version: str) -> bool:
        """Return True if upgrading from *current_version* is supported.

        When ``min_version`` is set, the current version must be at or
        above that baseline.  When unset, any version is considered safe.
        """
        if not self.min_version:
            return True
        # Normalize for comparison — strip whitespace
        return current_version.strip() >= self.min_version.strip()


@dataclass
class FirmwareCatalog:
    """An in-memory firmware catalog loaded from YAML.

    The catalog provides lookup methods for finding firmware entries by
    platform, checking whether a device is up-to-date, and resolving the
    recommended image for a given platform.
    """

    entries: dict[str, list[FirmwareEntry]] = field(default_factory=dict)
    source_path: str = ""

    # ── Loading ───────────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str | Path) -> FirmwareCatalog:
        """Load a catalog from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Firmware catalog not found: {path}")

        with open(path) as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}

        catalog = cls(source_path=str(path))

        platforms: dict[str, list[dict[str, Any]]] = raw.get("platforms", {})
        for platform, images in platforms.items():
            catalog.entries[platform] = [
                FirmwareEntry(
                    platform=platform,
                    version=img.get("version", ""),
                    filename=img.get("filename", ""),
                    md5=img.get("md5", ""),
                    min_version=img.get("min_version", ""),
                    release_notes=img.get("release_notes", ""),
                    recommended=bool(img.get("recommended", False)),
                )
                for img in (images or [])
            ]

        total = sum(len(v) for v in catalog.entries.values())
        logger.info(
            "Loaded firmware catalog from %s: %d platform(s), %d image(s)",
            path,
            len(catalog.entries),
            total,
        )
        return catalog

    def to_yaml(self, path: str | Path) -> None:
        """Write the catalog back to a YAML file."""
        data: dict[str, Any] = {"platforms": {}}
        for platform, entries in self.entries.items():
            data["platforms"][platform] = [
                {
                    "version": e.version,
                    "filename": e.filename,
                    "md5": e.md5,
                    "min_version": e.min_version,
                    "release_notes": e.release_notes,
                    "recommended": e.recommended,
                }
                for e in entries
            ]

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)

        logger.info("Wrote firmware catalog to %s", path)

    # ── Queries ───────────────────────────────────────────────────────────

    def get_entries(self, platform: str) -> list[FirmwareEntry]:
        """Return all firmware entries for a platform."""
        return self.entries.get(platform, [])

    def get_recommended(self, platform: str) -> FirmwareEntry | None:
        """Return the recommended firmware for a platform, or None."""
        for entry in self.get_entries(platform):
            if entry.recommended:
                return entry
        return None

    def get_version(self, platform: str, version: str) -> FirmwareEntry | None:
        """Return a specific version entry, or None."""
        for entry in self.get_entries(platform):
            if entry.version == version:
                return entry
        return None

    def is_latest(self, platform: str, current_version: str) -> bool:
        """Return True if *current_version* matches the recommended version."""
        rec = self.get_recommended(platform)
        if rec is None:
            return True  # No recommendation means we can't say it's outdated
        return current_version.strip() == rec.version.strip()

    @property
    def all_platforms(self) -> list[str]:
        """Return all platforms in the catalog."""
        return list(self.entries.keys())

    def add_entry(self, entry: FirmwareEntry) -> None:
        """Add or replace a firmware entry in the catalog."""
        platform_entries = self.entries.setdefault(entry.platform, [])
        # Replace existing entry for the same version
        self.entries[entry.platform] = [
            e for e in platform_entries if e.version != entry.version
        ]
        self.entries[entry.platform].append(entry)
