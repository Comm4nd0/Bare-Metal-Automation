"""HA and DRS configuration for a vSphere cluster.

Enables:
  - HA (vSphere High Availability) with admission control policy set to
    "percentage of cluster resources" (default 25 %).
  - DRS (Distributed Resource Scheduler) in fully-automated mode.
  - vMotion on each host using the port group created by vds.py.

Usage::

    mgr = HADRSManager(
        vcenter_host="10.100.1.10",
        username="administrator@vsphere.local",
        password="VMware1!",
    )
    mgr.configure(
        datacenter_name="BMA-DC",
        cluster_name="BMA-Cluster",
        vmotion_portgroup="PG-vmotion",
        esxi_hosts=[
            ("10.100.6.11", "10.100.7.11", "255.255.255.0"),
            ("10.100.6.12", "10.100.7.12", "255.255.255.0"),
        ],
        ha_admission_control_pct=25,
    )
"""

from __future__ import annotations

import logging
import ssl
import time
from typing import Any

logger = logging.getLogger(__name__)


class HADRSManager:
    """Enable HA and DRS on a vSphere cluster."""

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

    def configure(
        self,
        datacenter_name: str,
        cluster_name: str,
        vmotion_portgroup: str = "PG-vmotion",
        esxi_hosts: list[tuple[str, str, str]] | None = None,
        ha_admission_control_pct: int = 25,
    ) -> bool:
        """Enable HA + DRS and configure vMotion VMkernel interfaces.

        *esxi_hosts* is a list of (mgmt_ip, vmotion_ip, netmask) tuples.
        Returns True on success.
        """
        try:
            self._connect()
        except Exception as e:
            logger.error(f"Cannot connect to vCenter: {e}")
            return False

        try:
            cluster = self._find_cluster(datacenter_name, cluster_name)
            if cluster is None:
                logger.error(
                    f"Cluster '{cluster_name}' not found in '{datacenter_name}'"
                )
                return False

            ok_ha = self._enable_ha(cluster, ha_admission_control_pct)
            ok_drs = self._enable_drs(cluster)

            ok_vmotion = True
            if esxi_hosts:
                for mgmt_ip, vmotion_ip, netmask in esxi_hosts:
                    if not self._configure_vmotion(
                        cluster, mgmt_ip, vmotion_portgroup, vmotion_ip, netmask
                    ):
                        ok_vmotion = False

            return ok_ha and ok_drs and ok_vmotion
        except Exception as e:
            logger.exception(f"HA/DRS configuration error: {e}")
            return False
        finally:
            self._disconnect()

    # ── HA ─────────────────────────────────────────────────────────────────

    def _enable_ha(self, cluster, admission_control_pct: int) -> bool:
        """Enable HA with percentage-based admission control."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        logger.info(
            f"Enabling HA on cluster '{cluster.name}' "
            f"(admission control {admission_control_pct}%)"
        )
        das_config = vim.cluster.DasConfigInfo(
            enabled=True,
            hostMonitoring=vim.cluster.DasConfigInfo.ServiceState.enabled,
            vmMonitoring=vim.cluster.DasVmSettingsVmMonitoringState.vmMonitoringOnly,
            admissionControlEnabled=True,
            admissionControlPolicy=vim.cluster.FailoverResourcesAdmissionControlPolicy(
                cpuFailoverResourcesPercent=admission_control_pct,
                memoryFailoverResourcesPercent=admission_control_pct,
            ),
            defaultVmSettings=vim.cluster.DasVmSettings(
                restartPriority=vim.cluster.DasVmSettings.RestartPriority.medium,
                isolationResponse=vim.cluster.DasVmSettings.IsolationResponse.none,
            ),
        )
        spec = vim.cluster.ConfigSpecEx(dasConfig=das_config)
        task = cluster.ReconfigureComputeResource_Task(spec=spec, modify=True)
        ok = self._wait_for_task(task)
        if ok:
            logger.info("HA enabled successfully")
        else:
            logger.error("Failed to enable HA")
        return ok

    # ── DRS ────────────────────────────────────────────────────────────────

    def _enable_drs(self, cluster) -> bool:
        """Enable DRS in fully automated mode."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        logger.info(f"Enabling DRS (fully automated) on cluster '{cluster.name}'")
        drs_config = vim.cluster.DrsConfigInfo(
            enabled=True,
            defaultVmBehavior=vim.cluster.DrsConfigInfo.DrsBehavior.fullyAutomated,
            vmotionRate=3,    # 1 (conservative) – 5 (aggressive)
        )
        spec = vim.cluster.ConfigSpecEx(drsConfig=drs_config)
        task = cluster.ReconfigureComputeResource_Task(spec=spec, modify=True)
        ok = self._wait_for_task(task)
        if ok:
            logger.info("DRS enabled successfully")
        else:
            logger.error("Failed to enable DRS")
        return ok

    # ── vMotion VMkernel ───────────────────────────────────────────────────

    def _configure_vmotion(
        self,
        cluster,
        host_mgmt_ip: str,
        portgroup_name: str,
        vmotion_ip: str,
        netmask: str,
    ) -> bool:
        """Add a vMotion VMkernel adapter on the host if it does not exist."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        host = self._find_host_in_cluster(cluster, host_mgmt_ip)
        if host is None:
            logger.warning(f"Host {host_mgmt_ip} not found in cluster — skipping vMotion")
            return False

        # Check if vMotion vmkernel already exists
        net_config = host.config.network
        for vnic in net_config.vnic:
            if vnic.spec.ipConfig.ipAddress == vmotion_ip:
                logger.info(
                    f"vMotion vmkernel {vmotion_ip} already on {host_mgmt_ip}"
                )
                return True

        logger.info(
            f"Adding vMotion vmkernel {vmotion_ip} on {host_mgmt_ip} "
            f"via port group '{portgroup_name}'"
        )
        network_system = host.configManager.networkSystem
        vnic_spec = vim.host.VirtualNic.Specification(
            ip=vim.host.IpConfig(
                dhcp=False,
                ipAddress=vmotion_ip,
                subnetMask=netmask,
            ),
            distributedVirtualPort=self._find_dvport_spec(host, portgroup_name),
        )
        try:
            network_system.AddVirtualNic("", vnic_spec)
            # Enable vMotion on the new vmkernel
            vmotion_system = host.configManager.vmotionSystem
            vmotion_system.SelectVnic(vmotion_ip)
            logger.info(f"vMotion enabled on {host_mgmt_ip}")
            return True
        except Exception as e:
            logger.error(f"Failed to add vMotion vmkernel on {host_mgmt_ip}: {e}")
            return False

    def _find_dvport_spec(self, host, portgroup_name: str):
        """Return a DistributedVirtualSwitchPortConnection spec for *portgroup_name*."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        for pg in host.network:
            if isinstance(pg, vim.dvs.DistributedVirtualPortgroup) and pg.name == portgroup_name:
                return vim.dvs.PortConnection(
                    switchUuid=pg.config.distributedVirtualSwitch.uuid,
                    portgroupKey=pg.key,
                )
        raise RuntimeError(
            f"Distributed port group '{portgroup_name}' not found on host"
        )

    # ── Lookup helpers ─────────────────────────────────────────────────────

    def _find_cluster(self, dc_name: str, cluster_name: str):
        from pyVmomi import vim  # type: ignore[import-untyped]

        content = self._si.RetrieveContent()
        for dc in content.rootFolder.childEntity:
            if dc.name == dc_name:
                for child in dc.hostFolder.childEntity:
                    if (
                        isinstance(child, vim.ClusterComputeResource)
                        and child.name == cluster_name
                    ):
                        return child
        return None

    def _find_host_in_cluster(self, cluster, ip: str):
        for host in cluster.host:
            if host.name == ip:
                return host
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
