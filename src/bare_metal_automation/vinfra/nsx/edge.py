"""NSX Edge node deployment and edge cluster creation.

Deploys Edge VM(s) onto ESXi hosts, then groups them into an Edge Cluster
that the Tier-0 gateway can use for north-south routing.

Uses the NSX Policy API (REST).

Usage::

    mgr = EdgeManager(
        nsx_host="10.100.1.20",
        username="admin",
        password="VMware1!VMware1!",
    )
    ok = mgr.setup(
        edge_nodes=[
            EdgeNodeSpec(
                name="edge-01",
                form_factor="SMALL",
                mgmt_ip="10.100.1.21",
                mgmt_netmask="255.255.255.0",
                mgmt_gateway="10.100.1.1",
                mgmt_dns=["10.100.1.1"],
                mgmt_ntp=["10.100.9.1"],
                hostname="edge-01.mgmt.site",
                password="VMware1!VMware1!",
                vcenter_host="10.100.1.10",
                vcenter_username="administrator@vsphere.local",
                vcenter_password="VMware1!",
                compute_id="domain-c8",        # vSphere cluster MO ID
                storage_id="datastore-13",
                mgmt_network_id="network-100", # PG-mgmt portgroup MO ID
                overlay_tz_id="...",
                vlan_tz_id="...",
                uplink_profile_id="...",
                tep_pool_id="...",
            )
        ],
        cluster_name="bma-edge-cluster",
    )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

_EDGE_READY_TIMEOUT = 600  # seconds
_EDGE_READY_POLL = 20


@dataclass
class EdgeNodeSpec:
    """Specification for a single NSX Edge node."""

    name: str
    hostname: str
    password: str
    form_factor: str  # SMALL | MEDIUM | LARGE | XLARGE

    # Management interface
    mgmt_ip: str
    mgmt_netmask: str
    mgmt_gateway: str
    mgmt_dns: list[str]
    mgmt_ntp: list[str]

    # vSphere placement
    vcenter_host: str
    vcenter_username: str
    vcenter_password: str
    compute_id: str      # vSphere cluster or resource pool managed object ID
    storage_id: str      # datastore managed object ID
    mgmt_network_id: str # portgroup managed object ID for management NIC

    # Transport
    overlay_tz_id: str
    vlan_tz_id: str
    uplink_profile_id: str
    tep_pool_id: str
    host_switch_name: str = "nsxvswitch"


class EdgeManager:
    """Deploy NSX Edge VMs and create an edge cluster."""

    def __init__(
        self,
        nsx_host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        self.base_url = f"https://{nsx_host}/policy/api/v1"
        self.auth = (username, password)
        self.verify_ssl = verify_ssl

    # ── Public API ─────────────────────────────────────────────────────────

    def setup(
        self,
        edge_nodes: list[EdgeNodeSpec],
        cluster_name: str = "bma-edge-cluster",
    ) -> bool:
        """Deploy all edge nodes and create the edge cluster."""
        node_paths: list[str] = []
        for spec in edge_nodes:
            path = self._deploy_edge_node(spec)
            if path is None:
                logger.error(f"Failed to deploy edge node '{spec.name}'")
                return False
            node_paths.append(path)

        # Wait for all nodes to be deployed/ready
        if not self._wait_for_nodes(node_paths):
            logger.error("Edge nodes did not reach READY state in time")
            return False

        return self._ensure_edge_cluster(cluster_name, node_paths)

    # ── Edge node deployment ───────────────────────────────────────────────

    def _deploy_edge_node(self, spec: EdgeNodeSpec) -> str | None:
        """Create an edge transport node. Returns the Policy path or None."""
        path = f"/infra/sites/default/enforcement-points/default/edge-transport-nodes/{spec.name}"

        # Check if already exists
        existing = self._get(path)
        if existing and existing.get("display_name") == spec.name:
            logger.info(f"Edge node '{spec.name}' already exists")
            return path

        logger.info(f"Deploying edge node '{spec.name}' ({spec.form_factor})")
        body: dict[str, Any] = {
            "resource_type": "PolicyEdgeNode",
            "display_name": spec.name,
            "deployment_config": {
                "form_factor": spec.form_factor,
                "node_user_settings": {
                    "cli_password": spec.password,
                    "root_password": spec.password,
                },
                "vm_deployment_config": {
                    "placement_type": "VsphereDeploymentConfig",
                    "vc_id": spec.vcenter_host,
                    "compute_id": spec.compute_id,
                    "storage_id": spec.storage_id,
                    "management_network_id": spec.mgmt_network_id,
                    "hostname": spec.hostname,
                    "data_network_ids": [],
                },
            },
            "node_settings": {
                "hostname": spec.hostname,
                "ntp_servers": spec.mgmt_ntp,
                "dns_servers": spec.mgmt_dns,
                "enable_ssh": True,
            },
            "host_switch_spec": {
                "resource_type": "StandardHostSwitchSpec",
                "host_switches": [
                    {
                        "host_switch_name": spec.host_switch_name,
                        "host_switch_mode": "STANDARD",
                        "transport_zone_endpoints": [
                            {"transport_zone_id": spec.overlay_tz_id},
                            {"transport_zone_id": spec.vlan_tz_id},
                        ],
                        "uplinks": [
                            {"uplink_name": "uplink1", "vds_uplink_name": "uplink1"}
                        ],
                        "ip_assignment_spec": {
                            "resource_type": "StaticIpPoolSpec",
                            "ip_pool_id": spec.tep_pool_id,
                        },
                        "uplink_profile_id": spec.uplink_profile_id,
                    }
                ],
            },
        }

        result = self._patch(path, body)
        if result is None:
            return None
        return path

    def _wait_for_nodes(self, paths: list[str]) -> bool:
        """Wait until all edge nodes report configuration_state = SUCCESS."""
        deadline = time.monotonic() + _EDGE_READY_TIMEOUT
        while time.monotonic() < deadline:
            all_ready = True
            for path in paths:
                data = self._get(path)
                state = (data or {}).get("configuration_state", {}).get("state", "")
                if state != "SUCCESS":
                    all_ready = False
                    break
            if all_ready:
                return True
            logger.debug(f"Edge nodes not yet ready — retrying in {_EDGE_READY_POLL}s")
            time.sleep(_EDGE_READY_POLL)
        return False

    # ── Edge cluster ───────────────────────────────────────────────────────

    def _ensure_edge_cluster(self, name: str, node_paths: list[str]) -> bool:
        """Create or update the edge cluster with the given nodes."""
        path = f"/infra/sites/default/enforcement-points/default/edge-clusters/{name}"
        existing = self._get(path)
        if existing and existing.get("display_name") == name:
            logger.info(f"Edge cluster '{name}' already exists")
            return True

        logger.info(f"Creating edge cluster '{name}' with {len(node_paths)} nodes")
        members = [
            {"transport_node_id": p.rsplit("/", 1)[-1]}
            for p in node_paths
        ]
        body = {
            "resource_type": "PolicyEdgeCluster",
            "display_name": name,
            "members": members,
        }
        return bool(self._patch(path, body))

    # ── HTTP helpers ───────────────────────────────────────────────────────

    def _get(self, path: str) -> dict[str, Any] | None:
        try:
            resp = requests.get(
                f"{self.base_url}{path}",
                auth=self.auth,
                verify=self.verify_ssl,
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            logger.warning(f"GET {path} → {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"GET {path} failed: {e}")
            return None

    def _patch(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        try:
            resp = requests.patch(
                f"{self.base_url}{path}",
                json=body,
                auth=self.auth,
                verify=self.verify_ssl,
                timeout=60,
            )
            if resp.status_code in (200, 201):
                return resp.json() if resp.content else {}
            logger.error(f"PATCH {path} → {resp.status_code}: {resp.text}")
            return None
        except Exception as e:
            logger.error(f"PATCH {path} failed: {e}")
            return None
