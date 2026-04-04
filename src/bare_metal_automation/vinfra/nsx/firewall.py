"""NSX Distributed Firewall (DFW) rules for tenant isolation.

Rule policy:
  1. Any mission VM → DNS/NTP/AD/CA/WSUS/Print (shared services)  = ALLOW
  2. Intra-mission traffic                                          = ALLOW
  3. Cross-mission traffic                                          = DROP  (logged)
  4. Mission → Management (except shared services above)           = DROP  (logged)
  5. Management → Any                                              = ALLOW

Rules are applied as DFW Gateway Policies and Security Policies on the
NSX Policy API.  Groups are created per-tenant to scope the rules.

Usage::

    mgr = FirewallManager(
        nsx_host="10.100.1.20",
        username="admin",
        password="VMware1!VMware1!",
    )
    mgr.setup(
        mission_ids=["m1", "m2"],
        shared_services={
            "dns":   [("10.100.1.1",  53,  "UDP")],
            "ntp":   [("10.100.9.1",  123, "UDP")],
            "ad":    [("10.100.1.5",  389, "TCP"), ("10.100.1.5", 636, "TCP")],
            "ca":    [("10.100.1.6",  443, "TCP"), ("10.100.1.6", 80, "TCP")],
            "wsus":  [("10.100.1.7",  8530, "TCP")],
            "print": [("10.100.1.8",  9100, "TCP")],
        },
    )
"""

from __future__ import annotations

import logging
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Well-known port services used for shared-services rules
_SHARED_SERVICE_PORTS: dict[str, list[tuple[int, str]]] = {
    "dns":   [(53, "UDP"), (53, "TCP")],
    "ntp":   [(123, "UDP")],
    "ad":    [(88, "TCP"), (88, "UDP"), (389, "TCP"), (389, "UDP"),
              (636, "TCP"), (3268, "TCP"), (3269, "TCP")],
    "ca":    [(80, "TCP"), (443, "TCP")],
    "wsus":  [(8530, "TCP"), (8531, "TCP")],
    "print": [(9100, "TCP"), (515, "TCP"), (631, "TCP")],
}


class FirewallManager:
    """Configure NSX DFW policies for multi-tenant isolation."""

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
        mission_ids: list[str],
        shared_services: dict[str, list[tuple[str, int, str]]] | None = None,
    ) -> bool:
        """Create all DFW groups and policies. Returns True on success."""
        try:
            # 1. Create groups
            ok_groups = self._create_groups(mission_ids)

            # 2. Create shared-services rules
            ok_shared = self._create_shared_services_policy(
                mission_ids, shared_services or {}
            )

            # 3. Intra-mission allow rules
            ok_intra = self._create_intra_mission_policy(mission_ids)

            # 4. Cross-mission drop rules
            ok_cross = self._create_cross_mission_policy(mission_ids)

            # 5. Mission → management drop (catch-all after shared services)
            ok_m2m = self._create_mission_to_mgmt_policy(mission_ids)

            # 6. Management → any allow
            ok_mgmt = self._create_mgmt_allow_policy()

            return all([ok_groups, ok_shared, ok_intra, ok_cross, ok_m2m, ok_mgmt])
        except Exception as e:
            logger.exception(f"Firewall setup failed: {e}")
            return False

    # ── Groups ─────────────────────────────────────────────────────────────

    def _create_groups(self, mission_ids: list[str]) -> bool:
        """Create one NSX group per mission plus a management group."""
        all_ok = True

        # Management group — tag-based (VMs tagged with "role:management")
        ok = self._ensure_group(
            "grp-management",
            criteria=[{
                "resource_type": "Condition",
                "member_type": "VirtualMachine",
                "key": "Tag",
                "operator": "EQUALS",
                "value": "role|management",
            }],
        )
        all_ok = all_ok and ok

        for mission_id in mission_ids:
            ok = self._ensure_group(
                f"grp-{mission_id}",
                criteria=[{
                    "resource_type": "Condition",
                    "member_type": "VirtualMachine",
                    "key": "Tag",
                    "operator": "EQUALS",
                    "value": f"mission|{mission_id}",
                }],
            )
            all_ok = all_ok and ok

        return all_ok

    def _ensure_group(
        self, name: str, criteria: list[dict[str, Any]]
    ) -> bool:
        path = f"/infra/domains/default/groups/{name}"
        existing = self._get(path)
        if existing and existing.get("display_name") == name:
            logger.info(f"Group '{name}' already exists")
            return True

        body = {
            "resource_type": "Group",
            "display_name": name,
            "expression": criteria,
        }
        logger.info(f"Creating group '{name}'")
        return bool(self._patch(path, body))

    # ── Policy: Shared services ────────────────────────────────────────────

    def _create_shared_services_policy(
        self,
        mission_ids: list[str],
        shared_services: dict[str, list[tuple[str, int, str]]],
    ) -> bool:
        """ALLOW all missions → shared service IPs on known ports."""
        policy_name = "pol-shared-services"
        path = f"/infra/domains/default/security-policies/{policy_name}"
        existing = self._get(path)
        if existing:
            logger.info(f"Policy '{policy_name}' already exists")
            return True

        rules: list[dict[str, Any]] = []
        rule_seq = 100

        for service_name, endpoints in shared_services.items():
            if not endpoints:
                continue
            dest_ips = list({ep[0] for ep in endpoints})
            services = self._get_or_create_services(service_name, endpoints)

            rules.append({
                "id": f"allow-{service_name}",
                "display_name": f"Allow → {service_name}",
                "source_groups": [
                    f"/infra/domains/default/groups/grp-{m}"
                    for m in mission_ids
                ],
                "destination_groups": dest_ips,
                "services": services,
                "action": "ALLOW",
                "sequence_number": rule_seq,
                "logged": False,
            })
            rule_seq += 10

        # Use port-based services from _SHARED_SERVICE_PORTS as fallback
        if not rules:
            for svc_name, port_specs in _SHARED_SERVICE_PORTS.items():
                svc_paths = self._get_or_create_services(svc_name, [("ANY", p, proto) for p, proto in port_specs])
                rules.append({
                    "id": f"allow-{svc_name}",
                    "display_name": f"Allow → {svc_name}",
                    "source_groups": [
                        f"/infra/domains/default/groups/grp-{m}"
                        for m in mission_ids
                    ],
                    "destination_groups": ["ANY"],
                    "services": svc_paths,
                    "action": "ALLOW",
                    "sequence_number": rule_seq,
                    "logged": False,
                })
                rule_seq += 10

        return self._put_policy(policy_name, "Shared Services Allow", rules, sequence=100)

    # ── Policy: Intra-mission ──────────────────────────────────────────────

    def _create_intra_mission_policy(self, mission_ids: list[str]) -> bool:
        """ALLOW traffic within the same mission."""
        policy_name = "pol-intra-mission"
        path = f"/infra/domains/default/security-policies/{policy_name}"
        if self._get(path):
            logger.info(f"Policy '{policy_name}' already exists")
            return True

        rules: list[dict[str, Any]] = []
        for i, mission_id in enumerate(mission_ids):
            grp = f"/infra/domains/default/groups/grp-{mission_id}"
            rules.append({
                "id": f"allow-intra-{mission_id}",
                "display_name": f"Allow intra-mission {mission_id}",
                "source_groups": [grp],
                "destination_groups": [grp],
                "services": ["ANY"],
                "action": "ALLOW",
                "sequence_number": 100 + i * 10,
                "logged": False,
            })

        return self._put_policy(policy_name, "Intra-Mission Allow", rules, sequence=200)

    # ── Policy: Cross-mission drop ─────────────────────────────────────────

    def _create_cross_mission_policy(self, mission_ids: list[str]) -> bool:
        """DROP and log traffic between different missions."""
        policy_name = "pol-cross-mission-drop"
        path = f"/infra/domains/default/security-policies/{policy_name}"
        if self._get(path):
            logger.info(f"Policy '{policy_name}' already exists")
            return True

        all_mission_groups = [
            f"/infra/domains/default/groups/grp-{m}" for m in mission_ids
        ]

        rules: list[dict[str, Any]] = []
        seq = 100
        for i, src_mission in enumerate(mission_ids):
            for j, dst_mission in enumerate(mission_ids):
                if src_mission == dst_mission:
                    continue
                rules.append({
                    "id": f"drop-{src_mission}-to-{dst_mission}",
                    "display_name": f"DROP {src_mission} → {dst_mission}",
                    "source_groups": [
                        f"/infra/domains/default/groups/grp-{src_mission}"
                    ],
                    "destination_groups": [
                        f"/infra/domains/default/groups/grp-{dst_mission}"
                    ],
                    "services": ["ANY"],
                    "action": "DROP",
                    "sequence_number": seq,
                    "logged": True,
                })
                seq += 10

        return self._put_policy(
            policy_name, "Cross-Mission DROP (logged)", rules, sequence=300
        )

    # ── Policy: Mission → management drop ─────────────────────────────────

    def _create_mission_to_mgmt_policy(self, mission_ids: list[str]) -> bool:
        """DROP mission traffic to management (catch-all after shared-services)."""
        policy_name = "pol-mission-to-mgmt-drop"
        path = f"/infra/domains/default/security-policies/{policy_name}"
        if self._get(path):
            logger.info(f"Policy '{policy_name}' already exists")
            return True

        rules: list[dict[str, Any]] = [{
            "id": "drop-mission-to-mgmt",
            "display_name": "DROP mission → management (catch-all)",
            "source_groups": [
                f"/infra/domains/default/groups/grp-{m}" for m in mission_ids
            ],
            "destination_groups": ["/infra/domains/default/groups/grp-management"],
            "services": ["ANY"],
            "action": "DROP",
            "sequence_number": 100,
            "logged": True,
        }]

        return self._put_policy(
            policy_name, "Mission → Management DROP (logged)", rules, sequence=400
        )

    # ── Policy: Management allow all ──────────────────────────────────────

    def _create_mgmt_allow_policy(self) -> bool:
        """ALLOW all outbound traffic from management VMs."""
        policy_name = "pol-mgmt-allow-all"
        path = f"/infra/domains/default/security-policies/{policy_name}"
        if self._get(path):
            logger.info(f"Policy '{policy_name}' already exists")
            return True

        rules: list[dict[str, Any]] = [{
            "id": "allow-mgmt-any",
            "display_name": "Allow management → any",
            "source_groups": ["/infra/domains/default/groups/grp-management"],
            "destination_groups": ["ANY"],
            "services": ["ANY"],
            "action": "ALLOW",
            "sequence_number": 100,
            "logged": False,
        }]

        return self._put_policy(
            policy_name, "Management → Any ALLOW", rules, sequence=500
        )

    # ── Service helpers ────────────────────────────────────────────────────

    def _get_or_create_services(
        self, name: str, endpoints: list[tuple[str, int, str]]
    ) -> list[str]:
        """Return Policy paths for the named service, creating if needed."""
        svc_name = f"svc-bma-{name}"
        path = f"/infra/services/{svc_name}"
        existing = self._get(path)
        if existing:
            return [path]

        entries = []
        for _, port, proto in endpoints:
            if proto.upper() not in ("TCP", "UDP"):
                continue
            entries.append({
                "resource_type": (
                    "L4PortSetServiceEntry"
                ),
                "display_name": f"{name}-{proto.lower()}-{port}",
                "l4_protocol": proto.upper(),
                "destination_ports": [str(port)],
            })

        if not entries:
            return ["ANY"]

        body = {
            "resource_type": "Service",
            "display_name": svc_name,
            "service_entries": entries,
        }
        result = self._patch(path, body)
        return [path] if result is not None else ["ANY"]

    # ── Policy PUT helper ──────────────────────────────────────────────────

    def _put_policy(
        self,
        name: str,
        description: str,
        rules: list[dict[str, Any]],
        sequence: int = 100,
    ) -> bool:
        """Create or replace a Security Policy."""
        path = f"/infra/domains/default/security-policies/{name}"
        body: dict[str, Any] = {
            "resource_type": "SecurityPolicy",
            "display_name": name,
            "description": description,
            "sequence_number": sequence,
            "rules": rules,
        }
        logger.info(
            f"Creating security policy '{name}' ({len(rules)} rules)"
        )
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
            return resp.json() if resp.status_code == 200 else None
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
