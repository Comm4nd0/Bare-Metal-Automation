"""Media collector — copy firmware, ISOs, and certificates into the bundle.

Copies files from file-server source paths to the bundle staging directory,
then verifies each file's SHA-256 checksum against the catalogue entry.

Thread-safe: uses a per-instance lock when writing the checksum manifest
so multiple threads can collect media in parallel.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .firmware_catalogue import FirmwareEntry

logger = logging.getLogger(__name__)

# Buffer size for streaming hash computation (4 MiB)
_HASH_BUFFER = 4 * 1024 * 1024


@dataclass
class CollectedFile:
    """Record of a successfully collected (copied + verified) file."""

    source: Path
    destination: Path
    sha256: str
    size_bytes: int
    verified: bool


class ChecksumMismatch(Exception):
    """Raised when a copied file's SHA-256 does not match the catalogue."""


class MediaCollector:
    """Copy media files from file-server paths to the bundle directory.

    Args:
        bundle_dir:  Root bundle staging directory.
                     Sub-directories (firmware/, isos/, certs/) are created
                     automatically as needed.
        verify_checksums: Whether to verify SHA-256 after each copy (default True).
    """

    def __init__(
        self,
        bundle_dir: Path,
        verify_checksums: bool = True,
    ) -> None:
        self.bundle_dir = bundle_dir
        self.verify_checksums = verify_checksums
        self._lock = threading.Lock()
        self._collected: list[CollectedFile] = []

        # Create sub-directories
        for sub in ("firmware", "isos", "certs", "configs", "ansible"):
            (bundle_dir / sub).mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────

    def collect_firmware(
        self,
        entry: FirmwareEntry,
    ) -> CollectedFile:
        """Copy a firmware binary to bundle/firmware/.

        Args:
            entry: Resolved FirmwareEntry from FirmwareCatalogue.

        Returns:
            CollectedFile record.

        Raises:
            FileNotFoundError: Source file does not exist.
            ChecksumMismatch:  Post-copy checksum does not match catalogue.
        """
        dest = self.bundle_dir / "firmware" / entry.filename
        return self._copy_and_verify(
            source=entry.full_path,
            destination=dest,
            expected_sha256=entry.sha256,
            label=f"firmware/{entry.filename}",
        )

    def collect_iso(
        self,
        entry: FirmwareEntry,
    ) -> CollectedFile:
        """Copy an ISO image to bundle/isos/."""
        dest = self.bundle_dir / "isos" / entry.filename
        return self._copy_and_verify(
            source=entry.full_path,
            destination=dest,
            expected_sha256=entry.sha256,
            label=f"isos/{entry.filename}",
        )

    def collect_certificate(
        self,
        source: Path,
        filename: str | None = None,
        expected_sha256: str = "",
    ) -> CollectedFile:
        """Copy a certificate/key file to bundle/certs/."""
        dest_name = filename or source.name
        dest = self.bundle_dir / "certs" / dest_name
        return self._copy_and_verify(
            source=source,
            destination=dest,
            expected_sha256=expected_sha256,
            label=f"certs/{dest_name}",
        )

    def collect_arbitrary(
        self,
        source: Path,
        sub_dir: str,
        filename: str | None = None,
        expected_sha256: str = "",
    ) -> CollectedFile:
        """Copy any file to bundle/<sub_dir>/."""
        dest_name = filename or source.name
        dest_dir = self.bundle_dir / sub_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / dest_name
        return self._copy_and_verify(
            source=source,
            destination=dest,
            expected_sha256=expected_sha256,
            label=f"{sub_dir}/{dest_name}",
        )

    def collect_batch(
        self,
        items: list[dict[str, Any]],
    ) -> tuple[list[CollectedFile], list[str]]:
        """Collect a batch of files, tolerating individual failures.

        Each item dict must have:
            source        Path
            sub_dir       str ("firmware", "isos", "certs", …)
            filename      str (optional, defaults to source.name)
            sha256        str (optional)

        Returns:
            (successes, error_messages)
        """
        successes: list[CollectedFile] = []
        errors: list[str] = []

        for item in items:
            source = Path(item["source"])
            sub_dir = item.get("sub_dir", "firmware")
            filename = item.get("filename") or source.name
            sha256 = item.get("sha256", "")

            try:
                cf = self.collect_arbitrary(source, sub_dir, filename, sha256)
                successes.append(cf)
            except (FileNotFoundError, ChecksumMismatch, OSError) as e:
                msg = f"{source}: {e}"
                logger.error("Media collection failed — %s", msg)
                errors.append(msg)

        return successes, errors

    @property
    def collected(self) -> list[CollectedFile]:
        """Return all successfully collected files so far (thread-safe copy)."""
        with self._lock:
            return list(self._collected)

    def write_checksums_file(self, path: Path | None = None) -> Path:
        """Write checksums.sha256 to the bundle directory.

        Format: one ``<sha256>  <relative-path>`` line per file, compatible
        with ``sha256sum --check``.

        Args:
            path: Override output path (defaults to bundle_dir/checksums.sha256).

        Returns:
            Path to written checksums file.
        """
        out = path or self.bundle_dir / "checksums.sha256"
        lines: list[str] = []

        with self._lock:
            for cf in sorted(self._collected, key=lambda c: c.destination):
                rel = cf.destination.relative_to(self.bundle_dir)
                lines.append(f"{cf.sha256}  {rel.as_posix()}")

        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Wrote checksums.sha256 (%d entries) → %s", len(lines), out)
        return out

    # ── Private helpers ───────────────────────────────────────────────────

    def _copy_and_verify(
        self,
        source: Path,
        destination: Path,
        expected_sha256: str,
        label: str,
    ) -> CollectedFile:
        """Copy source → destination, then verify the checksum if provided."""
        if not source.exists():
            raise FileNotFoundError(
                f"Source file not found: {source}",
            )

        destination.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Copying %s → %s", source, destination)
        shutil.copy2(str(source), str(destination))

        actual_sha256 = _sha256_file(destination)
        size_bytes = destination.stat().st_size
        verified = True

        if expected_sha256 and self.verify_checksums:
            if actual_sha256.lower() != expected_sha256.lower():
                # Remove the bad copy so the bundle stays clean
                destination.unlink(missing_ok=True)
                raise ChecksumMismatch(
                    f"Checksum mismatch for {label}:\n"
                    f"  expected: {expected_sha256}\n"
                    f"  actual:   {actual_sha256}",
                )
        elif not expected_sha256:
            # No reference hash — record actual but mark as unverified
            verified = False
            logger.debug("No reference checksum for %s — not verified", label)

        cf = CollectedFile(
            source=source,
            destination=destination,
            sha256=actual_sha256,
            size_bytes=size_bytes,
            verified=verified,
        )

        with self._lock:
            self._collected.append(cf)

        logger.info(
            "Collected %s (%s, sha256=%s…)",
            label,
            _human_bytes(size_bytes),
            actual_sha256[:12],
        )
        return cf


# ── Module-level helpers ──────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file using streaming I/O."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_BUFFER)
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
