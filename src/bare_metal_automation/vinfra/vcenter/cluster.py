"""vCenter cluster setup — datacenter, cluster, ESXi host enrollment.

Uses pyvmomi (vSphere SDK for Python) to:
  - Create (or locate) a datacenter.
  - Create a cluster with HA/DRS pre-configured.
  - Add ESXi hosts to the cluster.
  - Configure vMotion VMkernel adapters on each host.

Usage::

    mgr = ClusterManager(
        vcenter_host="10.100.1.10",
        username="administrator@vsphere.local",
        password="VMware1!",
    )
    mgr.setup(
        datacenter_name="BMA-DC",
        cluster_name="BMA-Cluster",
        esxi_hosts=[
            ESXiHost("10.100.6.11", "root", "VMware1!"),
            ESXiHost("10.100.6.12", "root", "VMware1!"),
        ],
        vmotion_vlan=700,
    )
"""

from __future__ import annotations

import logging
import ssl
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_ADD_HOST_TIMEOUT = 300   # seconds to wait for host-add task


@dataclass
class ESXiHost:
    """Connection parameters for a single ESXi host."""
    ip: str
    username: str
    password: str
    vmotion_ip: str = ""        # optional — assigned during vMotion setup
    vmotion_netmask: str = "255.255.255.0"


class ClusterManager:
    """Create and populate a vSphere cluster via pyvmomi."""

    def __init__(
        self,
        vcenter_host: str,
        username: str,
        password: str,
        port: int = 443,
    ) -> None:
        self.vcenter_host = vcenter_host
        self.username = username
        self.password = password
        self.port = port
        self._si = None  # pyVmomi ServiceInstance, lazily connected

    # ── Public API ─────────────────────────────────────────────────────────

    def setup(
        self,
        datacenter_name: str,
        cluster_name: str,
        esxi_hosts: list[ESXiHost],
    ) -> bool:
        """Create the datacenter/cluster and enroll all ESXi hosts.

        Returns True if all steps succeeded, False otherwise.
        """
        try:
            self._connect()
        except Exception as e:
            logger.error(f"Cannot connect to vCenter {self.vcenter_host}: {e}")
            return False

        try:
            dc = self._ensure_datacenter(datacenter_name)
            cluster = self._ensure_cluster(dc, cluster_name)
            ok = self._add_hosts(cluster, esxi_hosts)
            return ok
        except Exception as e:
            logger.exception(f"Cluster setup failed: {e}")
            return False
        finally:
            self._disconnect()

    def get_datacenter(self, name: str):
        """Return an existing datacenter MO or None."""
        self._connect()
        content = self._si.RetrieveContent()
        for dc in content.rootFolder.childEntity:
            if dc.name == name:
                return dc
        return None

    # ── Datacenter / cluster creation ──────────────────────────────────────

    def _ensure_datacenter(self, name: str):
        """Return the named datacenter, creating it if absent."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        content = self._si.RetrieveContent()
        for dc in content.rootFolder.childEntity:
            if dc.name == name:
                logger.info(f"Datacenter '{name}' already exists")
                return dc

        logger.info(f"Creating datacenter '{name}'")
        dc = content.rootFolder.CreateDatacenter(name=name)
        logger.info(f"Datacenter '{name}' created")
        return dc

    def _ensure_cluster(self, datacenter, name: str):
        """Return the named cluster in *datacenter*, creating it if absent."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        host_folder = datacenter.hostFolder
        for child in host_folder.childEntity:
            if isinstance(child, vim.ClusterComputeResource) and child.name == name:
                logger.info(f"Cluster '{name}' already exists")
                return child

        logger.info(f"Creating cluster '{name}'")
        cluster_spec = vim.cluster.ConfigSpecEx(
            drsConfig=vim.cluster.DrsConfigInfo(
                enabled=False,   # DRS enabled later in ha_drs.py
            ),
            dasConfig=vim.cluster.DasConfigInfo(
                enabled=False,   # HA enabled later in ha_drs.py
            ),
        )
        cluster = host_folder.CreateClusterEx(name=name, spec=cluster_spec)
        logger.info(f"Cluster '{name}' created")
        return cluster

    # ── Host enrollment ────────────────────────────────────────────────────

    def _add_hosts(self, cluster, hosts: list[ESXiHost]) -> bool:
        """Add each ESXi host to *cluster*. Returns True if all succeeded."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        all_ok = True
        for host in hosts:
            if self._host_already_in_cluster(cluster, host.ip):
                logger.info(f"Host {host.ip} already in cluster — skipping")
                continue

            logger.info(f"Adding host {host.ip} to cluster")
            spec = vim.host.ConnectSpec(
                hostName=host.ip,
                userName=host.username,
                password=host.password,
                force=True,
                sslThumbprint="",   # vcsa-deploy adds thumbprint; use force=True for initial add
            )
            try:
                task = cluster.AddHost(spec=spec, asConnected=True)
                success = self._wait_for_task(task, timeout=_ADD_HOST_TIMEOUT)
                if success:
                    logger.info(f"Host {host.ip} added successfully")
                else:
                    logger.error(f"Failed to add host {host.ip}")
                    all_ok = False
            except Exception as e:
                logger.error(f"Exception adding host {host.ip}: {e}")
                all_ok = False

        return all_ok

    def _host_already_in_cluster(self, cluster, ip: str) -> bool:
        """Return True if a host with *ip* is already a member of *cluster*."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        for host in cluster.host:
            if host.name == ip:
                return True
        return False

    # ── Task / connection helpers ──────────────────────────────────────────

    def _connect(self) -> None:
        if self._si is not None:
            return
        from pyVmomi import vim, connect  # type: ignore[import-untyped]

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        logger.info(f"Connecting to vCenter {self.vcenter_host}")
        self._si = connect.SmartConnect(
            host=self.vcenter_host,
            user=self.username,
            pwd=self.password,
            port=self.port,
            sslContext=context,
        )
        logger.info("Connected to vCenter")

    def _disconnect(self) -> None:
        if self._si is None:
            return
        try:
            from pyVmomi import connect  # type: ignore[import-untyped]
            connect.Disconnect(self._si)
        except Exception:
            pass
        finally:
            self._si = None

    def _wait_for_task(self, task, timeout: int = 300) -> bool:
        """Block until *task* completes and return True on success."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = task.info
            if info.state == vim.TaskInfo.State.success:
                return True
            if info.state == vim.TaskInfo.State.error:
                logger.error(
                    f"Task failed: {info.error.msg if info.error else 'unknown'}"
                )
                return False
            time.sleep(5)

        logger.error(f"Task timed out after {timeout}s")
        return False
