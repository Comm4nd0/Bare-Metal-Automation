"""dnsmasq DHCP server wrapper for the bootstrap network.

Manages a dnsmasq process on the laptop's bootstrap interface:
  - Writes a minimal dnsmasq config for the bootstrap subnet
  - Starts / stops the process
  - Parses the lease file to discover who got an address
  - Optionally waits until a target number of devices have appeared
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Default lease file location (dnsmasq writes here)
DEFAULT_LEASE_FILE = "/var/lib/misc/dnsmasq.leases"

# Config file written by this module
DEFAULT_CONFIG_FILE = "/tmp/bma-dnsmasq.conf"

# PID file used to track the process
DEFAULT_PID_FILE = "/tmp/bma-dnsmasq.pid"


class DhcpServer:
    """Lightweight wrapper around dnsmasq for the bootstrap subnet.

    Typical usage::

        server = DhcpServer(
            interface="eth0",
            subnet="10.255.0.0/16",
            range_start="10.255.1.1",
            range_end="10.255.254.254",
            laptop_ip="10.255.0.1",
        )
        server.start()
        leases = server.wait_for_leases(expected_count=5, timeout=300)
        server.stop()
    """

    def __init__(
        self,
        interface: str,
        subnet: str,
        range_start: str,
        range_end: str,
        laptop_ip: str,
        lease_time: str = "1h",
        lease_file: str = DEFAULT_LEASE_FILE,
        config_file: str = DEFAULT_CONFIG_FILE,
        pid_file: str = DEFAULT_PID_FILE,
    ) -> None:
        self.interface = interface
        self.subnet = subnet
        self.range_start = range_start
        self.range_end = range_end
        self.laptop_ip = laptop_ip
        self.lease_time = lease_time
        self.lease_file = Path(lease_file)
        self.config_file = Path(config_file)
        self.pid_file = Path(pid_file)
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Write config and start dnsmasq.  Returns True on success."""
        if self.is_running():
            logger.info("dnsmasq already running — skipping start")
            return True

        self._write_config()

        try:
            self._process = subprocess.Popen(  # noqa: S603
                [
                    "dnsmasq",
                    "--conf-file", str(self.config_file),
                    "--no-daemon",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Give it a moment to bind to the interface
            time.sleep(1)

            if self._process.poll() is not None:
                _, stderr = self._process.communicate()
                logger.error(f"dnsmasq exited immediately: {stderr.decode()}")
                return False

            logger.info(f"dnsmasq started (PID {self._process.pid}) on {self.interface}")
            return True

        except FileNotFoundError:
            logger.error("dnsmasq not found — install with: apt-get install dnsmasq")
            return False
        except Exception as e:
            logger.error(f"Failed to start dnsmasq: {e}")
            return False

    def stop(self) -> None:
        """Stop the managed dnsmasq process."""
        if self._process is not None:
            logger.info("Stopping dnsmasq")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

        # Clean up temp files
        for path in (self.config_file, self.pid_file):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def is_running(self) -> bool:
        """Return True if our dnsmasq process is alive."""
        if self._process is None:
            return False
        return self._process.poll() is None

    # ── Lease file ─────────────────────────────────────────────────────────

    def parse_leases(self) -> dict[str, str]:
        """Parse dnsmasq lease file.  Returns ``{ip: mac}``."""
        leases: dict[str, str] = {}

        if not self.lease_file.exists():
            logger.debug(f"Lease file not yet present: {self.lease_file}")
            return leases

        with open(self.lease_file) as f:
            for line in f:
                # dnsmasq lease format: timestamp  mac  ip  hostname  client-id
                parts = line.strip().split()
                if len(parts) >= 3:
                    mac, ip = parts[1], parts[2]
                    if ip != self.laptop_ip:
                        leases[ip] = mac

        return leases

    def wait_for_leases(
        self,
        expected_count: int,
        timeout: int = 300,
        poll_interval: int = 5,
    ) -> dict[str, str]:
        """Block until *expected_count* devices appear in the lease file.

        Returns whatever leases are present when the timeout or count is reached.
        """
        logger.info(
            f"Waiting for {expected_count} DHCP lease(s) "
            f"(timeout {timeout}s, polling every {poll_interval}s)"
        )
        start = time.time()

        while time.time() - start < timeout:
            leases = self.parse_leases()
            logger.debug(f"Leases so far: {len(leases)}/{expected_count}")

            if len(leases) >= expected_count:
                logger.info(f"All {expected_count} device(s) acquired DHCP leases")
                return leases

            time.sleep(poll_interval)

        leases = self.parse_leases()
        logger.warning(
            f"Timeout waiting for leases — got {len(leases)}/{expected_count}"
        )
        return leases

    # ── Internal ───────────────────────────────────────────────────────────

    def _write_config(self) -> None:
        """Write a minimal dnsmasq config for the bootstrap subnet."""
        config = f"""\
# BMA bootstrap DHCP config — auto-generated, do not edit manually
interface={self.interface}
bind-interfaces

# DHCP range for ZTP discovery
dhcp-range={self.range_start},{self.range_end},{self.lease_time}

# Lease file
dhcp-leasefile={self.lease_file}

# Disable DNS (pure DHCP mode)
port=0

# Verbose logging
log-dhcp
"""
        self.config_file.write_text(config)
        logger.debug(f"dnsmasq config written to {self.config_file}")
