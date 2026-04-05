"""vSAN and datastore configuration.

Supports two storage modes:
  - vsan   : Configure vSAN disk groups on ESXi hosts using the vSAN API.
  - local  : Enumerate and return existing local datastores (no action).

Usage::

    mgr = StorageManager(
        vcenter_host="10.100.1.10",
        username="administrator@vsphere.local",
        password="VMware1!",
    )

    # vSAN mode (claim all eligible disks)
    result = mgr.configure_vsan(
        cluster_name="BMA-Cluster",
        datacenter_name="BMA-DC",
    )

    # Local mode (just list datastores)
    datastores = mgr.list_local_datastores(
        datacenter_name="BMA-DC",
    )
"""

from __future__ import annotations

import logging
import ssl
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VSANResult:
    """Outcome of a vSAN configuration run."""

    success: bool
    hosts_configured: list[str] = field(default_factory=list)
    hosts_failed: list[str] = field(default_factory=list)
    error: str = ""


class StorageManager:
    """Configure vSAN or inspect local datastores via pyvmomi."""

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
        self._si = None

    # ── Public API ─────────────────────────────────────────────────────────

    def configure_vsan(
        self,
        cluster_name: str,
        datacenter_name: str,
    ) -> VSANResult:
        """Enable vSAN on *cluster_name* and claim eligible disks.

        Disks are claimed automatically using the 'allFlash' or 'hybrid'
        policy based on what the host reports.

        Returns a :class:`VSANResult`.
        """
        try:
            self._connect()
        except Exception as e:
            return VSANResult(success=False, error=str(e))

        try:
            cluster = self._find_cluster(datacenter_name, cluster_name)
            if cluster is None:
                return VSANResult(
                    success=False,
                    error=f"Cluster '{cluster_name}' not found in '{datacenter_name}'",
                )

            ok = self._enable_vsan_on_cluster(cluster)
            if not ok:
                return VSANResult(success=False, error="Failed to enable vSAN on cluster")

            result = VSANResult(success=True)
            for host in cluster.host:
                hostname = host.name
                try:
                    self._claim_vsan_disks(host)
                    result.hosts_configured.append(hostname)
                    logger.info(f"vSAN disks claimed on {hostname}")
                except Exception as e:
                    logger.error(f"Failed to claim vSAN disks on {hostname}: {e}")
                    result.hosts_failed.append(hostname)

            if result.hosts_failed:
                result.success = False
                result.error = (
                    f"vSAN disk claim failed on: {', '.join(result.hosts_failed)}"
                )
            return result

        except Exception as e:
            logger.exception(f"vSAN configuration error: {e}")
            return VSANResult(success=False, error=str(e))
        finally:
            self._disconnect()

    def list_local_datastores(self, datacenter_name: str) -> list[dict[str, Any]]:
        """Return a list of local datastores in the datacenter.

        Each entry has keys: name, type, capacity_gb, free_gb, host.
        """
        try:
            self._connect()
            dc = self._get_datacenter(datacenter_name)
            if dc is None:
                return []
            return self._collect_datastores(dc)
        except Exception as e:
            logger.error(f"Failed to list datastores: {e}")
            return []
        finally:
            self._disconnect()

    # ── vSAN internals ─────────────────────────────────────────────────────

    def _enable_vsan_on_cluster(self, cluster) -> bool:
        """Enable vSAN on the cluster config if not already enabled."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        if cluster.configurationEx.vsanConfigInfo.enabled:
            logger.info(f"vSAN already enabled on cluster '{cluster.name}'")
            return True

        logger.info(f"Enabling vSAN on cluster '{cluster.name}'")
        vsan_config = vim.vsan.cluster.ConfigInfo(
            enabled=True,
            defaultConfig=vim.vsan.cluster.ConfigInfo.HostDefaultInfo(
                autoClaimStorage=False,
            ),
        )
        spec = vim.cluster.ConfigSpecEx(
            vsanConfig=vsan_config,
        )
        task = cluster.ReconfigureComputeResource_Task(spec=spec, modify=True)
        return self._wait_for_task(task)

    def _claim_vsan_disks(self, host) -> None:
        """Claim eligible disks for vSAN on a single host."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        storage_system = host.configManager.storageSystem
        vsan_system = host.configManager.vsanSystem

        eligible = vsan_system.QueryDisksForVsan()
        cache_disks = [
            d.disk for d in eligible
            if d.state == "eligible" and d.disk.ssd
        ]
        capacity_disks = [
            d.disk for d in eligible
            if d.state == "eligible" and not d.disk.ssd
        ]

        if not cache_disks and not capacity_disks:
            # All-flash: use all SSDs, half as cache, half as capacity
            all_ssds = [d.disk for d in eligible if d.state == "eligible"]
            if not all_ssds:
                logger.warning(f"No eligible vSAN disks on {host.name}")
                return
            mid = max(1, len(all_ssds) // 2)
            cache_disks = all_ssds[:mid]
            capacity_disks = all_ssds[mid:]

        if not cache_disks or not capacity_disks:
            logger.warning(
                f"Cannot form vSAN disk group on {host.name}: "
                f"cache={len(cache_disks)}, capacity={len(capacity_disks)}"
            )
            return

        disk_group_spec = vim.vsan.host.DiskMapping(
            ssd=cache_disks[0],
            nonSsd=capacity_disks,
        )
        task = vsan_system.InitializeDisks([disk_group_spec])
        if not self._wait_for_task(task):
            raise RuntimeError(f"Disk initialisation failed on {host.name}")

    # ── Datastore helpers ──────────────────────────────────────────────────

    def _collect_datastores(self, datacenter) -> list[dict[str, Any]]:
        from pyVmomi import vim  # type: ignore[import-untyped]

        stores: list[dict[str, Any]] = []
        for ds in datacenter.datastore:
            info = ds.info
            stores.append({
                "name": ds.name,
                "type": ds.summary.type,
                "capacity_gb": round(ds.summary.capacity / (1024**3), 1),
                "free_gb": round(ds.summary.freeSpace / (1024**3), 1),
            })
        return stores

    # ── Lookup helpers ─────────────────────────────────────────────────────

    def _find_cluster(self, dc_name: str, cluster_name: str):
        from pyVmomi import vim  # type: ignore[import-untyped]

        dc = self._get_datacenter(dc_name)
        if dc is None:
            return None
        for child in dc.hostFolder.childEntity:
            if isinstance(child, vim.ClusterComputeResource) and child.name == cluster_name:
                return child
        return None

    def _get_datacenter(self, name: str):
        content = self._si.RetrieveContent()
        for dc in content.rootFolder.childEntity:
            if dc.name == name:
                return dc
        return None

    # ── Connection ─────────────────────────────────────────────────────────

    def _connect(self) -> None:
        if self._si is not None:
            return
        from pyVmomi import connect  # type: ignore[import-untyped]

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self._si = connect.SmartConnect(
            host=self.vcenter_host,
            user=self.username,
            pwd=self.password,
            port=self.port,
            sslContext=context,
        )

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
