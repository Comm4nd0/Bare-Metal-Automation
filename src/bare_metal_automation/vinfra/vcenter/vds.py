"""Distributed vSwitch (vDS) creation and port-group configuration.

Creates one vDS per site, adds all ESXi hosts, then creates port groups for
every VLAN defined in the site template — including the fixed management
VLANs and per-mission tenant VLANs.

Fixed VLANs
-----------
  100  mgmt          out-of-band management
  200  servers       server OS management
  400  guest         visitor wireless
  500  voip          VoIP telephony
  600  ilo           HPE iLO
  700  vmotion       VMware vMotion
  800  vsan          vSAN storage
  950  backup        backup replication

Per-mission VLANs (for N missions, 0-indexed)
----------------------------------------------
  1100 + N*100   mN-users
  1110 + N*100   mN-apps
  1120 + N*100   mN-data

Usage::

    mgr = VDSManager(vcenter_host="10.100.1.10",
                     username="administrator@vsphere.local",
                     password="VMware1!")
    mgr.setup(
        datacenter_name="BMA-DC",
        vds_name="BMA-vDS",
        esxi_hosts=["10.100.6.11", "10.100.6.12"],
        mission_count=2,
        uplink_count=2,
    )
"""

from __future__ import annotations

import logging
import ssl
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Standard BMA VLANs (vid, name, description)
STANDARD_PORT_GROUPS: list[tuple[int, str, str]] = [
    (100,  "PG-mgmt",    "Out-of-band management"),
    (200,  "PG-servers", "Server OS management"),
    (400,  "PG-guest",   "Guest / visitor wireless"),
    (500,  "PG-voip",    "VoIP telephony"),
    (600,  "PG-ilo",     "HPE iLO out-of-band"),
    (700,  "PG-vmotion", "VMware vMotion"),
    (800,  "PG-vsan",    "VMware vSAN"),
    (950,  "PG-backup",  "Backup replication"),
]


def mission_port_groups(mission_count: int) -> list[tuple[int, str, str]]:
    """Return port-group tuples for all mission tenants."""
    groups: list[tuple[int, str, str]] = []
    for n in range(mission_count):
        m = n + 1  # human-readable mission number (1-indexed)
        groups += [
            (1100 + n * 100, f"PG-m{m}-users", f"Mission {m} user workstations"),
            (1110 + n * 100, f"PG-m{m}-apps",  f"Mission {m} application servers"),
            (1120 + n * 100, f"PG-m{m}-data",  f"Mission {m} data / storage"),
        ]
    return groups


class VDSManager:
    """Create a vDS, attach hosts, and provision port groups."""

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

    def setup(
        self,
        datacenter_name: str,
        vds_name: str,
        esxi_host_ips: list[str],
        mission_count: int = 2,
        uplink_count: int = 2,
        mtu: int = 9000,
    ) -> bool:
        """Create the vDS and all port groups. Returns True on success."""
        try:
            self._connect()
        except Exception as e:
            logger.error(f"Cannot connect to vCenter: {e}")
            return False

        try:
            dc = self._get_datacenter(datacenter_name)
            if dc is None:
                logger.error(f"Datacenter '{datacenter_name}' not found")
                return False

            vds = self._ensure_vds(dc, vds_name, uplink_count, mtu)
            self._add_hosts_to_vds(vds, dc, esxi_host_ips)

            all_groups = STANDARD_PORT_GROUPS + mission_port_groups(mission_count)
            self._ensure_port_groups(vds, all_groups)
            return True
        except Exception as e:
            logger.exception(f"VDS setup failed: {e}")
            return False
        finally:
            self._disconnect()

    # ── vDS creation ───────────────────────────────────────────────────────

    def _ensure_vds(self, datacenter, name: str, uplink_count: int, mtu: int):
        """Return the named vDS, creating it if absent."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        network_folder = datacenter.networkFolder
        for obj in network_folder.childEntity:
            if isinstance(obj, vim.dvs.VmwareDistributedVirtualSwitch) and obj.name == name:
                logger.info(f"vDS '{name}' already exists")
                return obj

        logger.info(f"Creating vDS '{name}' (uplinks={uplink_count}, mtu={mtu})")
        uplink_names = [f"uplink{i+1}" for i in range(uplink_count)]
        spec = vim.dvs.VmwareDistributedVirtualSwitch.CreateSpec(
            configSpec=vim.dvs.VmwareDistributedVirtualSwitch.ConfigSpec(
                name=name,
                numUplinkPorts=uplink_count,
                maxPorts=512,
                maxMtu=mtu,
                uplinkPortPolicy=vim.dvs.NameArrayUplinkPortPolicy(
                    uplinkPortName=uplink_names,
                ),
            )
        )
        task = network_folder.CreateDVS(spec=spec)
        if not self._wait_for_task(task):
            raise RuntimeError(f"Failed to create vDS '{name}'")

        # Re-fetch after creation
        for obj in network_folder.childEntity:
            if isinstance(obj, vim.dvs.VmwareDistributedVirtualSwitch) and obj.name == name:
                logger.info(f"vDS '{name}' created")
                return obj

        raise RuntimeError(f"vDS '{name}' not found after creation")

    # ── Host attachment ────────────────────────────────────────────────────

    def _add_hosts_to_vds(self, vds, datacenter, host_ips: list[str]) -> None:
        """Add each ESXi host to the vDS if not already a member."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        # Build a map of IP → host MO
        host_map = self._collect_hosts(datacenter)

        member_keys = {
            m.config.host.name
            for m in vds.config.host
        }

        member_specs: list[vim.dvs.HostMember.ConfigSpec] = []
        for ip in host_ips:
            host_mo = host_map.get(ip)
            if host_mo is None:
                logger.warning(f"Host {ip} not found in datacenter — skipping vDS attachment")
                continue
            if ip in member_keys:
                logger.info(f"Host {ip} already on vDS — skipping")
                continue

            member_specs.append(
                vim.dvs.HostMember.ConfigSpec(
                    operation=vim.ConfigSpecOperation.add,
                    host=host_mo,
                    backing=vim.dvs.HostMember.PnicBacking(
                        pnicSpec=[],   # operator will configure uplinks post-deploy
                    ),
                )
            )

        if not member_specs:
            return

        reconfig_spec = vim.dvs.VmwareDistributedVirtualSwitch.ConfigSpec(
            configVersion=vds.config.configVersion,
            host=member_specs,
        )
        task = vds.ReconfigureDvs_Task(spec=reconfig_spec)
        if not self._wait_for_task(task):
            logger.error("Failed to add hosts to vDS")

    # ── Port group management ──────────────────────────────────────────────

    def _ensure_port_groups(
        self,
        vds,
        groups: list[tuple[int, str, str]],
    ) -> None:
        """Create missing port groups on *vds*."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        existing_names = {
            pg.name
            for pg in vds.portgroup
        }

        for vid, pg_name, description in groups:
            if pg_name in existing_names:
                logger.debug(f"Port group '{pg_name}' already exists — skipping")
                continue

            logger.info(f"Creating port group '{pg_name}' (VLAN {vid})")
            spec = vim.dvs.DistributedVirtualPortgroup.ConfigSpec(
                name=pg_name,
                numPorts=128,
                type=vim.dvs.DistributedVirtualPortgroup.PortgroupType.earlyBinding,
                defaultPortConfig=vim.dvs.VmwareDistributedVirtualPort.Setting(
                    vlan=vim.dvs.VmwareDistributedVirtualPort.VlanIdSpec(
                        inherited=False,
                        vlanId=vid,
                    ),
                ),
                description=description,
            )
            task = vds.portgroup[0].GetDistributedVirtualSwitch().AddDVPortgroup_Task(
                spec=[spec]
            ) if False else vds.AddDVPortgroup_Task(spec=[spec])
            if not self._wait_for_task(task):
                logger.error(f"Failed to create port group '{pg_name}'")

    # ── Helpers ────────────────────────────────────────────────────────────

    def _get_datacenter(self, name: str):
        from pyVmomi import vim  # type: ignore[import-untyped]

        content = self._si.RetrieveContent()
        for dc in content.rootFolder.childEntity:
            if dc.name == name:
                return dc
        return None

    def _collect_hosts(self, datacenter) -> dict[str, object]:
        """Return {ip: host_mo} for all hosts in the datacenter."""
        from pyVmomi import vim  # type: ignore[import-untyped]

        hosts: dict[str, object] = {}
        self._recurse_hosts(datacenter.hostFolder, hosts)
        return hosts

    def _recurse_hosts(self, folder, hosts: dict) -> None:
        from pyVmomi import vim  # type: ignore[import-untyped]

        for child in folder.childEntity:
            if isinstance(child, vim.HostSystem):
                hosts[child.name] = child
            elif isinstance(child, (vim.Folder, vim.ClusterComputeResource, vim.ComputeResource)):
                if hasattr(child, "childEntity"):
                    self._recurse_hosts(child, hosts)
                elif hasattr(child, "host"):
                    for h in child.host:
                        hosts[h.name] = h

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
