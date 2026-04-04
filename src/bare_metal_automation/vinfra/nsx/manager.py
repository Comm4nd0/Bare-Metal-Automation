"""NSX Manager OVA deployment and vCenter registration.

Deploys the NSX Manager OVA onto an ESXi host via the vSphere OVF Tool,
then registers it with the vCenter server so that NSX plug-ins appear in
the vSphere Client.

Typical usage::

    deployer = NSXManagerDeployer(
        ovftool_path="/usr/bin/ovftool",
        config=NSXManagerConfig(
            ova_path="/opt/bma/nsx/nsx-unified-appliance.ova",
            esxi_host="10.100.6.11",
            esxi_username="root",
            esxi_password="VMware1!",
            esxi_datastore="vsanDatastore",
            esxi_network="PG-mgmt",
            nsx_hostname="nsx-mgr-01.mgmt.site",
            nsx_ip="10.100.1.20",
            nsx_gateway="10.100.1.1",
            nsx_netmask="255.255.255.0",
            nsx_dns=["10.100.1.1"],
            nsx_ntp=["10.100.9.1"],
            nsx_password="VMware1!VMware1!",
        ),
    )
    result = deployer.deploy()
    if result.success:
        deployer.register_with_vcenter(
            vcenter_host="10.100.1.10",
            vcenter_username="administrator@vsphere.local",
            vcenter_password="VMware1!",
        )
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_READY_TIMEOUT = 600   # seconds to wait for NSX Manager to come up
_READY_POLL = 20


@dataclass
class NSXManagerConfig:
    """Parameters for NSX Manager OVA deployment."""

    ova_path: str
    esxi_host: str
    esxi_username: str
    esxi_password: str
    esxi_datastore: str
    esxi_network: str

    nsx_hostname: str
    nsx_ip: str
    nsx_gateway: str
    nsx_netmask: str
    nsx_dns: list[str]
    nsx_ntp: list[str]
    nsx_password: str

    # Form factor: small | medium | large
    form_factor: str = "small"


@dataclass
class NSXDeployResult:
    """Outcome of an NSX Manager deployment."""

    success: bool
    nsx_url: str = ""
    error: str = ""


class NSXManagerDeployer:
    """Deploy NSX Manager OVA and register it with vCenter."""

    def __init__(
        self,
        ovftool_path: str | Path,
        config: NSXManagerConfig,
        deploy_timeout: int = 3600,
    ) -> None:
        self.ovftool = Path(ovftool_path)
        self.config = config
        self.deploy_timeout = deploy_timeout

    # ── Public API ─────────────────────────────────────────────────────────

    def deploy(self) -> NSXDeployResult:
        """Deploy the NSX Manager OVA and wait for it to become reachable."""
        if not self.ovftool.exists():
            return NSXDeployResult(
                success=False, error=f"ovftool not found at {self.ovftool}"
            )
        if not Path(self.config.ova_path).exists():
            return NSXDeployResult(
                success=False, error=f"OVA not found at {self.config.ova_path}"
            )

        logger.info(
            f"Deploying NSX Manager '{self.config.nsx_hostname}' "
            f"on ESXi {self.config.esxi_host}"
        )

        ok = self._run_ovftool()
        if not ok:
            return NSXDeployResult(success=False, error="ovftool exited with error")

        nsx_url = f"https://{self.config.nsx_ip}"
        logger.info(f"Waiting for NSX Manager at {nsx_url}")
        ready = self._wait_for_ready(nsx_url)
        if not ready:
            return NSXDeployResult(
                success=False,
                nsx_url=nsx_url,
                error=f"NSX Manager did not respond within {_READY_TIMEOUT}s",
            )

        logger.info(f"NSX Manager ready at {nsx_url}")
        return NSXDeployResult(success=True, nsx_url=nsx_url)

    def register_with_vcenter(
        self,
        vcenter_host: str,
        vcenter_username: str,
        vcenter_password: str,
        vcenter_thumbprint: str = "",
    ) -> bool:
        """Register this NSX Manager with a vCenter server.

        Uses the NSX Policy API to create the compute-manager record.
        """
        cfg = self.config
        url = f"https://{cfg.nsx_ip}/policy/api/v1/fabric/compute-managers"
        payload = {
            "server": vcenter_host,
            "origin_type": "vCenter",
            "credential": {
                "credential_type": "UsernamePasswordLoginCredential",
                "username": vcenter_username,
                "password": vcenter_password,
                "thumbprint": vcenter_thumbprint,
            },
            "display_name": vcenter_host,
            "set_as_oidc_provider": True,
        }
        try:
            resp = requests.post(
                url,
                json=payload,
                auth=(cfg.nsx_ip, cfg.nsx_password),
                verify=False,
                timeout=60,
            )
            if resp.status_code in (200, 201):
                logger.info(f"NSX Manager registered with vCenter {vcenter_host}")
                return True
            logger.error(
                f"NSX ↔ vCenter registration failed: {resp.status_code} {resp.text}"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to register NSX with vCenter: {e}")
            return False

    # ── Internal ───────────────────────────────────────────────────────────

    def _run_ovftool(self) -> bool:
        """Execute ovftool to deploy the NSX Manager OVA."""
        cfg = self.config
        dns_servers = ",".join(cfg.nsx_dns)
        ntp_servers = ",".join(cfg.nsx_ntp)
        target = (
            f"vi://{cfg.esxi_username}:{cfg.esxi_password}"
            f"@{cfg.esxi_host}/{cfg.esxi_datastore}"
        )
        cmd = [
            str(self.ovftool),
            "--acceptAllEulas",
            "--noSSLVerify",
            "--powerOn",
            f"--deploymentOption={cfg.form_factor}",
            f"--name={cfg.nsx_hostname}",
            f"--datastore={cfg.esxi_datastore}",
            f"--network={cfg.esxi_network}",
            f"--prop:nsx_hostname={cfg.nsx_hostname}",
            f"--prop:nsx_ip_0={cfg.nsx_ip}",
            f"--prop:nsx_netmask_0={cfg.nsx_netmask}",
            f"--prop:nsx_gateway_0={cfg.nsx_gateway}",
            f"--prop:nsx_dns1_0={cfg.nsx_dns[0] if cfg.nsx_dns else ''}",
            f"--prop:nsx_domain_0=mgmt.local",
            f"--prop:nsx_ntp_0={ntp_servers}",
            f"--prop:nsx_isSSHEnabled=True",
            f"--prop:nsx_allowSSHRootLogin=True",
            f"--prop:nsx_passwd_0={cfg.nsx_password}",
            f"--prop:nsx_cli_passwd_0={cfg.nsx_password}",
            cfg.ova_path,
            target,
        ]

        logger.info(f"Running ovftool for NSX Manager deployment")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.deploy_timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error("ovftool timed out")
            return False
        except OSError as e:
            logger.error(f"Failed to launch ovftool: {e}")
            return False

        for line in proc.stdout.splitlines():
            logger.info(f"[ovftool] {line}")
        for line in proc.stderr.splitlines():
            logger.warning(f"[ovftool stderr] {line}")

        return proc.returncode == 0

    def _wait_for_ready(self, url: str) -> bool:
        """Poll NSX Manager UI until it returns < 500 or timeout."""
        deadline = time.monotonic() + _READY_TIMEOUT
        while time.monotonic() < deadline:
            try:
                resp = requests.get(url, verify=False, timeout=10, allow_redirects=True)
                if resp.status_code < 500:
                    return True
            except requests.RequestException:
                pass
            time.sleep(_READY_POLL)
        return False
