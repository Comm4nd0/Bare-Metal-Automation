"""Git repo manager — clone/pull templates and firmware from a git repo."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GitRepoError(Exception):
    """Git operation failed."""


class GitRepoManager:
    """Manages a local clone of the templates/firmware git repository.

    Uses subprocess to call git directly — no gitpython dependency
    needed. The deployment laptop always has git installed.
    """

    def __init__(
        self,
        repo_url: str,
        local_path: Path | str,
        branch: str = "main",
    ) -> None:
        self.repo_url = repo_url
        self.local_path = Path(local_path)
        self.branch = branch

    def sync(self) -> dict[str, str]:
        """Clone or pull the repository.

        If the local path doesn't exist, clones the repo.
        If it exists, pulls the latest changes.

        Returns:
            Dict with 'status' ('cloned' or 'pulled') and 'commit' (SHA).

        Raises:
            GitRepoError: If the git operation fails.
        """
        if not self.repo_url:
            raise GitRepoError(
                "No git repo URL configured — "
                "set BMA_GIT_REPO_URL",
            )

        if self.local_path.exists() and (self.local_path / ".git").exists():
            return self._pull()
        else:
            return self._clone()

    def _clone(self) -> dict[str, str]:
        """Clone the repository."""
        logger.info(
            "Cloning %s (branch: %s) to %s",
            self.repo_url, self.branch, self.local_path,
        )

        self.local_path.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [
                "git", "clone",
                "--branch", self.branch,
                "--depth", "1",
                self.repo_url,
                str(self.local_path),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            raise GitRepoError(
                f"Git clone failed: {result.stderr.strip()}",
            )

        commit = self._get_commit()
        logger.info("Cloned repo at commit %s", commit)
        return {"status": "cloned", "commit": commit}

    def _pull(self) -> dict[str, str]:
        """Pull latest changes."""
        logger.info(
            "Pulling latest changes in %s (branch: %s)",
            self.local_path, self.branch,
        )

        result = subprocess.run(
            ["git", "pull", "origin", self.branch],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.local_path),
        )

        if result.returncode != 0:
            raise GitRepoError(
                f"Git pull failed: {result.stderr.strip()}",
            )

        commit = self._get_commit()
        logger.info("Pulled repo to commit %s", commit)
        return {"status": "pulled", "commit": commit}

    def _get_commit(self) -> str:
        """Get the current HEAD commit SHA."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.local_path),
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"

    def verify_files(
        self,
        inventory: Any,
    ) -> list[str]:
        """Check that all files referenced by the inventory exist.

        Scans all device specs for template, firmware_image,
        firmware_md5, spp_iso, os_iso, kickstart_iso, and
        ilo_firmware references. Returns a list of missing file
        descriptions.

        Args:
            inventory: DeploymentInventory instance.

        Returns:
            List of missing file descriptions (empty = all OK).
        """
        missing: list[str] = []

        templates_dir = self.local_path / "templates"
        firmware_dir = self.local_path / "firmware"
        iso_dir = self.local_path / "iso"

        for serial, spec in inventory.devices.items():
            hostname = spec.get("hostname", serial)

            # Check template
            template = spec.get("template")
            if template:
                template_path = templates_dir / template
                if not template_path.exists():
                    missing.append(
                        f"{hostname}: template '{template}' "
                        f"not found at {template_path}",
                    )

            # Check firmware files
            for field, subdir in [
                ("firmware_image", firmware_dir),
                ("ilo_firmware", firmware_dir),
                ("spp_iso", iso_dir),
                ("os_iso", iso_dir),
                ("kickstart_iso", iso_dir),
            ]:
                filename = spec.get(field)
                if filename:
                    file_path = subdir / filename
                    if not file_path.exists():
                        missing.append(
                            f"{hostname}: {field} '{filename}' "
                            f"not found at {file_path}",
                        )

        if missing:
            logger.warning(
                "%d file(s) missing from repo", len(missing),
            )
        else:
            logger.info("All referenced files verified in repo")

        return missing
