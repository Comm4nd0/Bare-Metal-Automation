"""NSX segment (logical switch) creation for management and mission tenants.

Creates two categories of segments:
  - Management segments: mgmt-servers, mgmt-infra (always present)
  - Per-mission segments: mN-users, mN-apps, mN-data (one set per mission)

Each segment is connected to the appropriate Tier-1 gateway and placed in
the overlay transport zone.

Segment naming convention::

    mgmt-servers          — management servers (domain controllers, DNS, etc.)
    mgmt-infra            — management infrastructure (vCenter, NSX, backups)
    m1-users, m1-apps, m1-data   — mission 1
    m2-users, m2-apps, m2-data   — mission 2
    …

Usage::

    mgr = SegmentManager(
        nsx_host="10.100.1.20",
        username="admin",
        password="VMware1!VMware1!",
    )
    mgr.setup(
        overlay_tz_name="overlay-tz",
        mgmt_tier1_name="t1-mgmt",
        mission_tier1_names={"m1": "t1-m1", "m2": "t1-m2"},
        mission_subnets={
            "m1": {
                "users": "10.100.11.1/24",
                "apps":  "10.100.111.1/24",
                "data":  "10.100.112.1/24",
            },
        },
        site_octet=100,
    )
"""

from __future__ import annotations

import logging
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Management segment definitions: (name, gateway_cidr, description)
MGMT_SEGMENTS = [
    ("mgmt-servers", "management servers — DC/DNS/AD/CA/WSUS/Print"),
    ("mgmt-infra",   "management infrastructure — vCenter/NSX/IPAM/backup"),
]


def mission_segments(mission_id: str) -> list[tuple[str, str]]:
    """Return (name, description) pairs for a single mission tenant."""
    return [
        (f"{mission_id}-users", f"Mission {mission_id} — user workstations"),
        (f"{mission_id}-apps",  f"Mission {mission_id} — application servers"),
        (f"{mission_id}-data",  f"Mission {mission_id} — data / storage"),
    ]


class SegmentManager:
    """Create NSX overlay segments for management and mission tenants."""

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
        overlay_tz_name: str,
        mgmt_tier1_name: str,
        mission_tier1_names: dict[str, str],
        mgmt_subnets: dict[str, str] | None = None,
        mission_subnets: dict[str, dict[str, str]] | None = None,
    ) -> bool:
        """Create all management and mission segments. Returns True on success."""
        tz_path = self._get_tz_path(overlay_tz_name)
        if tz_path is None:
            logger.error(f"Transport zone '{overlay_tz_name}' not found")
            return False

        all_ok = True

        # Management segments
        for seg_name, description in MGMT_SEGMENTS:
            subnet = (mgmt_subnets or {}).get(seg_name)
            ok = self._ensure_segment(
                name=seg_name,
                tz_path=tz_path,
                tier1_path=f"/infra/tier-1s/{mgmt_tier1_name}",
                description=description,
                gateway_cidr=subnet,
            )
            if not ok:
                all_ok = False

        # Per-mission segments
        for mission_id, tier1_name in mission_tier1_names.items():
            tier1_path = f"/infra/tier-1s/{tier1_name}"
            for seg_name, description in mission_segments(mission_id):
                seg_type = seg_name.rsplit("-", 1)[-1]  # users / apps / data
                subnet = (
                    (mission_subnets or {})
                    .get(mission_id, {})
                    .get(seg_type)
                )
                ok = self._ensure_segment(
                    name=seg_name,
                    tz_path=tz_path,
                    tier1_path=tier1_path,
                    description=description,
                    gateway_cidr=subnet,
                )
                if not ok:
                    all_ok = False

        return all_ok

    # ── Segment management ─────────────────────────────────────────────────

    def _ensure_segment(
        self,
        name: str,
        tz_path: str,
        tier1_path: str,
        description: str = "",
        gateway_cidr: str | None = None,
    ) -> bool:
        """Create the segment if it does not exist."""
        path = f"/infra/segments/{name}"
        existing = self._get(path)
        if existing and existing.get("display_name") == name:
            logger.info(f"Segment '{name}' already exists")
            return True

        logger.info(
            f"Creating segment '{name}' → Tier-1 '{tier1_path.rsplit('/', 1)[-1]}'"
        )
        body: dict[str, Any] = {
            "resource_type": "Segment",
            "display_name": name,
            "description": description,
            "transport_zone_path": tz_path,
            "connectivity_path": tier1_path,
        }
        if gateway_cidr:
            body["subnets"] = [{"gateway_address": gateway_cidr}]

        return bool(self._patch(path, body))

    # ── Transport zone lookup ──────────────────────────────────────────────

    def _get_tz_path(self, name: str) -> str | None:
        path = (
            f"/infra/sites/default/enforcement-points/default/transport-zones/{name}"
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
