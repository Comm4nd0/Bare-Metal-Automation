"""NSX Tier-0 and Tier-1 gateway configuration.

Creates:
  - One Tier-0 gateway (per-site) with an uplink to the perimeter firewall.
  - One Tier-1 gateway per tenant (mission) linked to the Tier-0.

The Tier-0 handles north-south routing between the site and external networks.
Each Tier-1 provides tenant isolation — routing within the tenant and
advertising prefixes up to Tier-0.

All calls use the NSX Policy API.

Usage::

    mgr = RoutingManager(
        nsx_host="10.100.1.20",
        username="admin",
        password="VMware1!VMware1!",
    )
    mgr.setup_tier0(
        name="t0-site",
        edge_cluster_name="bma-edge-cluster",
        uplink_segment_name="seg-t0-uplink",
        uplink_ip="172.16.0.2/30",
        ha_mode="ACTIVE_STANDBY",
    )
    for mission in ["m1", "m2"]:
        mgr.setup_tier1(
            name=f"t1-{mission}",
            tier0_name="t0-site",
            edge_cluster_name="bma-edge-cluster",
        )
"""

from __future__ import annotations

import logging
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class RoutingManager:
    """Create and configure Tier-0 and Tier-1 gateways."""

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

    # ── Tier-0 ─────────────────────────────────────────────────────────────

    def setup_tier0(
        self,
        name: str,
        edge_cluster_name: str,
        ha_mode: str = "ACTIVE_STANDBY",
        uplink_segment_name: str | None = None,
        uplink_ip: str | None = None,
    ) -> bool:
        """Create the Tier-0 gateway and optionally add an uplink interface.

        *uplink_ip* should be in CIDR notation, e.g. ``172.16.0.2/30``.
        """
        path = f"/infra/tier-0s/{name}"
        existing = self._get(path)
        if existing and existing.get("display_name") == name:
            logger.info(f"Tier-0 '{name}' already exists")
        else:
            edge_cluster_path = self._edge_cluster_path(edge_cluster_name)
            body: dict[str, Any] = {
                "resource_type": "Tier0",
                "display_name": name,
                "ha_mode": ha_mode,
                "failover_mode": "NON_PREEMPTIVE",
            }
            if edge_cluster_path:
                body["edge_cluster_path"] = edge_cluster_path

            logger.info(f"Creating Tier-0 '{name}' (ha_mode={ha_mode})")
            if not self._patch(path, body):
                return False

        # Create locale service (needed before interface can be added)
        ls_ok = self._ensure_locale_service(f"{name}/locale-services/default", edge_cluster_name)
        if not ls_ok:
            return False

        if uplink_segment_name and uplink_ip:
            return self._add_t0_interface(
                tier0_name=name,
                interface_name="uplink-to-firewall",
                segment_name=uplink_segment_name,
                ip_cidr=uplink_ip,
            )

        return True

    def setup_tier1(
        self,
        name: str,
        tier0_name: str,
        edge_cluster_name: str,
        route_advertisement: list[str] | None = None,
    ) -> bool:
        """Create a Tier-1 gateway linked to *tier0_name*.

        *route_advertisement* defaults to advertising connected segments and
        static routes.
        """
        if route_advertisement is None:
            route_advertisement = [
                "TIER1_CONNECTED",
                "TIER1_STATIC_ROUTES",
            ]

        path = f"/infra/tier-1s/{name}"
        existing = self._get(path)
        if existing and existing.get("display_name") == name:
            logger.info(f"Tier-1 '{name}' already exists")
            return True

        edge_cluster_path = self._edge_cluster_path(edge_cluster_name)
        body: dict[str, Any] = {
            "resource_type": "Tier1",
            "display_name": name,
            "tier0_path": f"/infra/tier-0s/{tier0_name}",
            "route_advertisement_types": route_advertisement,
            "failover_mode": "NON_PREEMPTIVE",
        }
        if edge_cluster_path:
            body["edge_cluster_path"] = edge_cluster_path

        logger.info(f"Creating Tier-1 '{name}' linked to Tier-0 '{tier0_name}'")
        return bool(self._patch(path, body))

    # ── Locale service (required for T0 interfaces) ────────────────────────

    def _ensure_locale_service(
        self, relative_path: str, edge_cluster_name: str
    ) -> bool:
        full_path = f"/infra/tier-0s/{relative_path}"
        existing = self._get(full_path)
        if existing:
            return True

        edge_cluster_path = self._edge_cluster_path(edge_cluster_name)
        body: dict[str, Any] = {
            "resource_type": "LocaleServices",
            "display_name": "default",
        }
        if edge_cluster_path:
            body["edge_cluster_path"] = edge_cluster_path

        return bool(self._patch(full_path, body))

    # ── Tier-0 interface ───────────────────────────────────────────────────

    def _add_t0_interface(
        self,
        tier0_name: str,
        interface_name: str,
        segment_name: str,
        ip_cidr: str,
    ) -> bool:
        """Add an uplink interface to the Tier-0 gateway."""
        path = (
            f"/infra/tier-0s/{tier0_name}/locale-services/default"
            f"/interfaces/{interface_name}"
        )
        existing = self._get(path)
        if existing and existing.get("display_name") == interface_name:
            logger.info(f"T0 interface '{interface_name}' already exists")
            return True

        segment_path = f"/infra/segments/{segment_name}"
        body = {
            "resource_type": "Tier0Interface",
            "display_name": interface_name,
            "type": "EXTERNAL",
            "segment_path": segment_path,
            "subnets": [{"ip_addresses": [ip_cidr.split("/")[0]], "prefix_len": int(ip_cidr.split("/")[1])}],
        }
        logger.info(
            f"Adding T0 uplink interface '{interface_name}' ({ip_cidr}) "
            f"on segment '{segment_name}'"
        )
        return bool(self._patch(path, body))

    # ── Edge cluster lookup ────────────────────────────────────────────────

    def _edge_cluster_path(self, name: str) -> str | None:
        path = (
            f"/infra/sites/default/enforcement-points/default/edge-clusters/{name}"
        )
        data = self._get(path)
        return data.get("path") if data else None

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
