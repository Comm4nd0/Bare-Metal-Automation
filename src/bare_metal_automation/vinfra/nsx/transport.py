"""NSX-T transport layer configuration.

Creates the building blocks needed before any logical networking:
  - Overlay transport zone (for Geneve-encapsulated segments)
  - VLAN transport zone (for NSX segments bridged to physical VLANs)
  - Host transport node profile (applied to all ESXi hosts)
  - TEP (Tunnel Endpoint) IP pool used for host tunnel interfaces

All calls target the NSX Policy API at ``/policy/api/v1/``.

Usage::

    mgr = TransportManager(
        nsx_host="10.100.1.20",
        username="admin",
        password="VMware1!VMware1!",
    )
    mgr.setup(
        overlay_tz_name="overlay-tz",
        vlan_tz_name="vlan-tz",
        uplink_profile_name="bma-uplink-profile",
        tep_pool_name="bma-tep-pool",
        tep_pool_cidr="169.254.100.0/24",
        tep_pool_range_start="169.254.100.10",
        tep_pool_range_end="169.254.100.200",
        tep_pool_gateway="169.254.100.1",
        host_switch_name="nsxvswitch",
        transport_vlan=0,
    )
"""

from __future__ import annotations

import logging
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class TransportManager:
    """Configure NSX transport zones, uplink profiles, and TEP IP pools."""

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
        overlay_tz_name: str = "overlay-tz",
        vlan_tz_name: str = "vlan-tz",
        uplink_profile_name: str = "bma-uplink-profile",
        tep_pool_name: str = "bma-tep-pool",
        tep_pool_cidr: str = "169.254.100.0/24",
        tep_pool_range_start: str = "169.254.100.10",
        tep_pool_range_end: str = "169.254.100.200",
        tep_pool_gateway: str = "169.254.100.1",
        host_switch_name: str = "nsxvswitch",
        transport_vlan: int = 0,
        mtu: int = 9000,
    ) -> bool:
        """Create all transport prerequisites. Returns True on success."""
        try:
            ok_overlay = self._ensure_transport_zone(
                overlay_tz_name, "OVERLAY", host_switch_name
            )
            ok_vlan = self._ensure_transport_zone(
                vlan_tz_name, "VLAN", host_switch_name
            )
            ok_pool = self._ensure_ip_pool(
                tep_pool_name,
                tep_pool_cidr,
                tep_pool_range_start,
                tep_pool_range_end,
                tep_pool_gateway,
            )
            ok_profile = self._ensure_uplink_profile(
                uplink_profile_name,
                transport_vlan=transport_vlan,
                mtu=mtu,
            )
            return all([ok_overlay, ok_vlan, ok_pool, ok_profile])
        except Exception as e:
            logger.exception(f"Transport setup failed: {e}")
            return False

    def apply_host_transport_node_profile(
        self,
        profile_name: str,
        cluster_id: str,
        overlay_tz_name: str = "overlay-tz",
        uplink_profile_name: str = "bma-uplink-profile",
        tep_pool_name: str = "bma-tep-pool",
        host_switch_name: str = "nsxvswitch",
    ) -> bool:
        """Create and apply a Host Transport Node Profile to an ESXi cluster."""
        try:
            overlay_tz_id = self._get_tz_id(overlay_tz_name)
            uplink_profile_id = self._get_uplink_profile_id(uplink_profile_name)
            tep_pool_id = self._get_ip_pool_id(tep_pool_name)

            if None in (overlay_tz_id, uplink_profile_id, tep_pool_id):
                logger.error("Cannot find required transport prerequisites")
                return False

            profile_path = f"/infra/host-transport-node-profiles/{profile_name}"
            body: dict[str, Any] = {
                "resource_type": "PolicyHostTransportNodeProfile",
                "display_name": profile_name,
                "host_switch_spec": {
                    "resource_type": "StandardHostSwitchSpec",
                    "host_switches": [
                        {
                            "host_switch_name": host_switch_name,
                            "host_switch_mode": "STANDARD",
                            "host_switch_type": "VDS",
                            "transport_zone_endpoints": [
                                {"transport_zone_id": overlay_tz_id},
                            ],
                            "uplinks": [
                                {
                                    "uplink_name": "uplink1",
                                    "vds_uplink_name": "uplink1",
                                }
                            ],
                            "ip_assignment_spec": {
                                "resource_type": "StaticIpPoolSpec",
                                "ip_pool_id": tep_pool_id,
                            },
                            "uplink_profile_id": uplink_profile_id,
                        }
                    ],
                },
            }
            resp = self._patch(profile_path, body)
            if not resp:
                return False

            # Apply profile to the cluster
            apply_path = (
                f"/infra/sites/default/enforcement-points/default"
                f"/transport-node-collections/{profile_name}"
            )
            apply_body = {
                "resource_type": "HostTransportNodeCollection",
                "display_name": profile_name,
                "compute_collection_id": cluster_id,
                "transport_node_profile_id": profile_path,
            }
            return bool(self._patch(apply_path, apply_body))

        except Exception as e:
            logger.exception(f"Failed to apply transport node profile: {e}")
            return False

    # ── Transport zones ────────────────────────────────────────────────────

    def _ensure_transport_zone(
        self, name: str, tz_type: str, host_switch_name: str
    ) -> bool:
        """Create an overlay or VLAN transport zone if it does not exist."""
        path = f"/infra/sites/default/enforcement-points/default/transport-zones/{name}"
        existing = self._get(path)
        if existing and existing.get("display_name") == name:
            logger.info(f"Transport zone '{name}' ({tz_type}) already exists")
            return True

        logger.info(f"Creating transport zone '{name}' ({tz_type})")
        body = {
            "resource_type": "PolicyTransportZone",
            "display_name": name,
            "tz_type": tz_type,
            "host_switch_name": host_switch_name,
        }
        return bool(self._patch(path, body))

    def _get_tz_id(self, name: str) -> str | None:
        path = f"/infra/sites/default/enforcement-points/default/transport-zones/{name}"
        data = self._get(path)
        return data.get("id") if data else None

    # ── IP pools ───────────────────────────────────────────────────────────

    def _ensure_ip_pool(
        self,
        name: str,
        cidr: str,
        range_start: str,
        range_end: str,
        gateway: str,
    ) -> bool:
        """Create the TEP IP pool if it does not exist."""
        path = f"/infra/ip-pools/{name}"
        existing = self._get(path)
        if existing and existing.get("display_name") == name:
            logger.info(f"IP pool '{name}' already exists")
            return True

        logger.info(f"Creating IP pool '{name}' ({cidr})")
        body = {
            "resource_type": "IpAddressPool",
            "display_name": name,
            "subnets": [
                {
                    "resource_type": "IpAddressPoolStaticSubnet",
                    "cidr": cidr,
                    "gateway_ip": gateway,
                    "allocation_ranges": [
                        {"start": range_start, "end": range_end}
                    ],
                }
            ],
        }
        return bool(self._patch(path, body))

    def _get_ip_pool_id(self, name: str) -> str | None:
        data = self._get(f"/infra/ip-pools/{name}")
        return data.get("id") if data else None

    # ── Uplink profiles ────────────────────────────────────────────────────

    def _ensure_uplink_profile(
        self, name: str, transport_vlan: int = 0, mtu: int = 9000
    ) -> bool:
        """Create a vDS uplink profile if it does not exist."""
        path = f"/infra/host-switch-profiles/{name}"
        existing = self._get(path)
        if existing and existing.get("display_name") == name:
            logger.info(f"Uplink profile '{name}' already exists")
            return True

        logger.info(f"Creating uplink profile '{name}'")
        body = {
            "resource_type": "PolicyUplinkHostSwitchProfile",
            "display_name": name,
            "mtu": mtu,
            "transport_vlan": transport_vlan,
            "teaming": {
                "active_list": [
                    {"uplink_name": "uplink1", "uplink_type": "PNIC"}
                ],
                "policy": "FAILOVER_ORDER",
            },
        }
        return bool(self._patch(path, body))

    def _get_uplink_profile_id(self, name: str) -> str | None:
        data = self._get(f"/infra/host-switch-profiles/{name}")
        return data.get("id") if data else None

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
                data = resp.json() if resp.content else {}
                logger.debug(f"PATCH {path} → {resp.status_code}")
                return data
            logger.error(f"PATCH {path} → {resp.status_code}: {resp.text}")
            return None
        except Exception as e:
            logger.error(f"PATCH {path} failed: {e}")
            return None
