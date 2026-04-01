"""PXE / TFTP fallback boot server.

Some infrastructure scenarios require PXE booting rather than (or in addition
to) virtual-media ISO installation — e.g. when iLO virtual media is not
available or when a bare-metal image must be streamed over PXE.

This module manages a tftpd-hpa (or dnsmasq TFTP) process and serves standard
PXE boot files (pxelinux.0, GRUB EFI, etc.) from a configurable TFTP root.

Architecture
------------
- TFTP server process wrapper (start / stop / health check)
- PXE boot file serving (copies files into TFTP root)
- Per-MAC host entry management (generates pxelinux.cfg/01-xx-xx-xx-xx-xx)

The DHCP server (see ``discovery/dhcp.py``) must be configured to announce
the TFTP server IP and the boot filename — that wiring is the caller's
responsibility.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TFTP_ROOT = "/srv/tftp"
DEFAULT_PXE_CONFIG_DIR = "pxelinux.cfg"

# Default BIOS / UEFI PXE filenames
PXELINUX_BIOS_FILE = "pxelinux.0"
PXELINUX_EFI_FILE = "bootx64.efi"

# tftpd-hpa config path
TFTPD_CONFIG = "/etc/default/tftpd-hpa"


class PXEServer:
    """Wrapper around a TFTP server process for PXE/network-boot scenarios."""

    def __init__(
        self,
        tftp_root: str = DEFAULT_TFTP_ROOT,
        interface: str = "",
        listen_address: str = "",
    ) -> None:
        self.tftp_root = Path(tftp_root)
        self.interface = interface
        self.listen_address = listen_address
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the TFTP server using dnsmasq in TFTP-only mode.

        Returns True on success.
        """
        if self.is_running():
            logger.info("TFTP server already running")
            return True

        self.tftp_root.mkdir(parents=True, exist_ok=True)

        cmd = [
            "dnsmasq",
            "--no-daemon",
            "--port=0",              # disable DNS
            "--enable-tftp",
            f"--tftp-root={self.tftp_root}",
        ]
        if self.interface:
            cmd += [f"--interface={self.interface}"]
        if self.listen_address:
            cmd += [f"--listen-address={self.listen_address}"]

        try:
            self._process = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(1)
            if self._process.poll() is not None:
                _, err = self._process.communicate()
                logger.error(f"TFTP server failed to start: {err.decode()}")
                return False

            logger.info(
                f"TFTP server started (PID {self._process.pid}), "
                f"serving from {self.tftp_root}"
            )
            return True

        except FileNotFoundError:
            logger.error(
                "dnsmasq not found — install with: apt-get install dnsmasq"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to start TFTP server: {e}")
            return False

    def stop(self) -> None:
        """Stop the managed TFTP server process."""
        if self._process is not None:
            logger.info("Stopping TFTP server")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def is_running(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None

    # ── PXE file management ────────────────────────────────────────────────

    def serve_pxe_files(
        self,
        pxe_source_dir: str | Path,
        include_efi: bool = True,
    ) -> bool:
        """Copy PXE boot files into the TFTP root.

        ``pxe_source_dir`` should contain:
          - ``pxelinux.0``         (BIOS PXE loader)
          - ``bootx64.efi``        (UEFI PXE loader, optional)
          - ``ldlinux.c32``        (syslinux dependency)
          - Any additional modules

        Returns True if at least the BIOS loader was copied.
        """
        src = Path(pxe_source_dir)
        if not src.exists():
            logger.error(f"PXE source directory not found: {src}")
            return False

        self.tftp_root.mkdir(parents=True, exist_ok=True)
        copied_any = False

        for fname in src.iterdir():
            dst = self.tftp_root / fname.name
            try:
                shutil.copy2(fname, dst)
                logger.debug(f"PXE: copied {fname.name} → {dst}")
                copied_any = True
            except OSError as e:
                logger.warning(f"PXE: failed to copy {fname.name}: {e}")

        if not copied_any:
            logger.error("PXE: no files were copied")
        return copied_any

    def add_host_entry(
        self,
        mac: str,
        config_content: str,
    ) -> Path:
        """Write a pxelinux.cfg per-MAC entry for a specific host.

        The config file is written to::

            {tftp_root}/pxelinux.cfg/01-{mac-with-dashes}

        This is the standard pxelinux MAC-address lookup path.

        Args:
            mac:            MAC address (any common separator format).
            config_content: Full content of the pxelinux config file.

        Returns:
            Path to the written config file.
        """
        # Normalise MAC to lowercase dash-separated
        normalised = re.sub(r"[:\-\s]", "-", mac).lower().strip("-")
        cfg_dir = self.tftp_root / DEFAULT_PXE_CONFIG_DIR
        cfg_dir.mkdir(parents=True, exist_ok=True)

        cfg_path = cfg_dir / f"01-{normalised}"
        cfg_path.write_text(config_content)
        logger.info(f"PXE host entry written: {cfg_path}")
        return cfg_path

    def remove_host_entry(self, mac: str) -> None:
        """Remove the per-MAC pxelinux config entry."""
        normalised = re.sub(r"[:\-\s]", "-", mac).lower().strip("-")
        cfg_path = self.tftp_root / DEFAULT_PXE_CONFIG_DIR / f"01-{normalised}"
        try:
            cfg_path.unlink(missing_ok=True)
        except OSError:
            pass

    def default_pxe_config(
        self,
        kernel: str,
        initrd: str,
        kernel_args: str = "",
        label: str = "auto",
    ) -> str:
        """Return a minimal pxelinux config string for unattended boot."""
        return (
            f"DEFAULT {label}\n"
            f"LABEL {label}\n"
            f"  KERNEL {kernel}\n"
            f"  INITRD {initrd}\n"
            f"  APPEND {kernel_args}\n"
        )
