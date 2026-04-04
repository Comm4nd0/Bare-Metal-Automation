"""VCSA deployment — drives the vCenter Server Appliance installer (vcsa-deploy).

The vcsa-deploy CLI ships with the VCSA ISO and supports headless installation
via a JSON template.  This module:

  1. Generates the deployment JSON from BMA inventory data.
  2. Invokes ``vcsa-deploy install`` as a subprocess.
  3. Polls the target URL until vCenter responds or the timeout expires.

Typical usage::

    deployer = VCSADeployer(
        vcsa_deploy_path="/mnt/vcsa/vcsa-cli-installer/lin64/vcsa-deploy",
        config=VCSAConfig(
            vcenter_hostname="vcenter.mgmt.site",
            vcenter_ip="10.100.1.10",
            vcenter_gateway="10.100.1.1",
            vcenter_netmask="255.255.255.0",
            vcenter_dns=["10.100.1.1"],
            vcenter_password="VMware1!",
            esxi_host="10.100.6.11",
            esxi_username="root",
            esxi_password="VMware1!",
            esxi_datastore="datastore1",
            ntp_servers=["10.100.9.1"],
            sso_domain="vsphere.local",
            sso_password="VMware1!",
            deployment_size="small",
            datacenter_name="BMA-DC",
            cluster_name="BMA-Cluster",
        ),
    )
    result = deployer.deploy()
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# How long to wait for vCenter to come up after vcsa-deploy exits
_VCENTER_READY_TIMEOUT = 600  # seconds
_VCENTER_READY_POLL = 20       # seconds between polls


@dataclass
class VCSAConfig:
    """All parameters needed to deploy a VCSA appliance."""

    # Target ESXi host that will host the vCenter VM
    esxi_host: str
    esxi_username: str
    esxi_password: str
    esxi_datastore: str

    # vCenter identity
    vcenter_hostname: str
    vcenter_ip: str
    vcenter_gateway: str
    vcenter_netmask: str
    vcenter_dns: list[str]
    vcenter_password: str

    # SSO / identity source
    sso_domain: str = "vsphere.local"
    sso_password: str = ""

    # Sizing
    deployment_size: str = "small"  # tiny | small | medium | large | xlarge

    # Naming (used during cluster setup, stored here for convenience)
    datacenter_name: str = "BMA-DC"
    cluster_name: str = "BMA-Cluster"

    # NTP
    ntp_servers: list[str] = field(default_factory=list)

    # Network label on the ESXi host for the vCenter management NIC
    esxi_network: str = "VM Network"

    def __post_init__(self) -> None:
        if not self.sso_password:
            self.sso_password = self.vcenter_password


@dataclass
class DeployResult:
    """Outcome of a VCSA deployment run."""

    success: bool
    vcenter_url: str = ""
    error: str = ""
    duration_seconds: float = 0.0


class VCSADeployer:
    """Deploy a vCenter Server Appliance via the vcsa-deploy CLI."""

    def __init__(
        self,
        vcsa_deploy_path: str | Path,
        config: VCSAConfig,
        deploy_timeout: int = 3600,
        skip_ssl_verify: bool = True,
    ) -> None:
        self.vcsa_deploy = Path(vcsa_deploy_path)
        self.config = config
        self.deploy_timeout = deploy_timeout
        self.skip_ssl_verify = skip_ssl_verify

    # ── Public API ─────────────────────────────────────────────────────────

    def deploy(self) -> DeployResult:
        """Run the full VCSA deployment sequence.

        Returns a :class:`DeployResult` regardless of outcome.
        """
        start = time.monotonic()
        logger.info(
            f"Starting VCSA deployment: {self.config.vcenter_hostname} "
            f"on ESXi host {self.config.esxi_host}"
        )

        if not self.vcsa_deploy.exists():
            msg = f"vcsa-deploy not found at {self.vcsa_deploy}"
            logger.error(msg)
            return DeployResult(success=False, error=msg)

        template = self._build_template()
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as fh:
            json.dump(template, fh, indent=2)
            template_path = fh.name

        logger.info(f"VCSA deployment template written to {template_path}")

        try:
            ok = self._run_vcsa_deploy(template_path)
        finally:
            Path(template_path).unlink(missing_ok=True)

        if not ok:
            return DeployResult(
                success=False,
                error="vcsa-deploy exited with non-zero status",
                duration_seconds=time.monotonic() - start,
            )

        vcenter_url = f"https://{self.config.vcenter_ip}"
        logger.info(f"vcsa-deploy finished — polling {vcenter_url} for readiness")
        ready = self._wait_for_vcenter(vcenter_url)

        duration = time.monotonic() - start
        if ready:
            logger.info(
                f"vCenter ready at {vcenter_url} after {duration:.0f}s"
            )
            return DeployResult(
                success=True, vcenter_url=vcenter_url, duration_seconds=duration
            )

        msg = f"vCenter did not respond within {_VCENTER_READY_TIMEOUT}s"
        logger.error(msg)
        return DeployResult(
            success=False, vcenter_url=vcenter_url, error=msg,
            duration_seconds=duration,
        )

    # ── Internal helpers ───────────────────────────────────────────────────

    def _build_template(self) -> dict[str, Any]:
        """Return the vcsa-deploy JSON template for an embedded PSC deployment."""
        cfg = self.config
        return {
            "__version": "2.13.0",
            "__comments": "Generated by Bare Metal Automation",
            "new_vcsa": {
                "esxi": {
                    "hostname": cfg.esxi_host,
                    "username": cfg.esxi_username,
                    "password": cfg.esxi_password,
                    "deployment_network": cfg.esxi_network,
                    "datastore": cfg.esxi_datastore,
                },
                "appliance": {
                    "thin_disk_mode": True,
                    "deployment_option": cfg.deployment_size,
                    "name": cfg.vcenter_hostname,
                },
                "network": {
                    "ip_family": "ipv4",
                    "mode": "static",
                    "ip": cfg.vcenter_ip,
                    "dns_servers": cfg.vcenter_dns,
                    "prefix": _netmask_to_prefix(cfg.vcenter_netmask),
                    "gateway": cfg.vcenter_gateway,
                    "system_name": cfg.vcenter_hostname,
                },
                "os": {
                    "password": cfg.vcenter_password,
                    "ntp_servers": cfg.ntp_servers,
                    "ssh_enable": False,
                },
                "sso": {
                    "password": cfg.sso_password,
                    "domain_name": cfg.sso_domain,
                },
            },
            "ceip": {
                "settings": {
                    "ceip_enabled": False,
                }
            },
        }

    def _run_vcsa_deploy(self, template_path: str) -> bool:
        """Execute vcsa-deploy and return True on exit code 0."""
        cmd: list[str] = [
            str(self.vcsa_deploy),
            "install",
            "--accept-eula",
            "--no-ssl-certificate-verification",
            "--template-file", template_path,
        ]

        logger.info(f"Running: {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.deploy_timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error(
                f"vcsa-deploy timed out after {self.deploy_timeout}s"
            )
            return False
        except OSError as e:
            logger.error(f"Failed to launch vcsa-deploy: {e}")
            return False

        if proc.stdout:
            for line in proc.stdout.splitlines():
                logger.info(f"[vcsa-deploy] {line}")
        if proc.stderr:
            for line in proc.stderr.splitlines():
                logger.warning(f"[vcsa-deploy stderr] {line}")

        if proc.returncode != 0:
            logger.error(
                f"vcsa-deploy exited {proc.returncode}"
            )
            return False

        return True

    def _wait_for_vcenter(self, url: str) -> bool:
        """Poll the vCenter HTTPS endpoint until it returns 200/302 or timeout."""
        deadline = time.monotonic() + _VCENTER_READY_TIMEOUT
        while time.monotonic() < deadline:
            try:
                resp = requests.get(
                    url, verify=False, timeout=10, allow_redirects=True
                )
                if resp.status_code < 500:
                    return True
            except requests.RequestException:
                pass
            logger.debug(
                f"vCenter not yet ready — retrying in {_VCENTER_READY_POLL}s"
            )
            time.sleep(_VCENTER_READY_POLL)
        return False


# ── Utilities ──────────────────────────────────────────────────────────────

def _netmask_to_prefix(netmask: str) -> int:
    """Convert dotted-decimal netmask (e.g. '255.255.255.0') to prefix length."""
    return sum(bin(int(o)).count("1") for o in netmask.split("."))
