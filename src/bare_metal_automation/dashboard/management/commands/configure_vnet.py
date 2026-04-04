"""Management command: configure_vnet

Phase 8 — Virtual Network Configuration (NSX-T).

Orchestrates the NSX multi-tenant networking setup:
  1. Deploy NSX Manager OVA (optional — may already be running)
  2. Configure transport layer (transport zones, TEP pool, uplink profile)
  3. Apply host transport node profile to the ESXi cluster
  4. Deploy edge VMs and create edge cluster
  5. Create Tier-0 gateway (uplink to perimeter firewall)
  6. Create per-tenant Tier-1 gateways
  7. Create segments (mgmt + per-mission)
  8. Apply DFW security policies

All parameters are sourced from the active Deployment's site_config JSON field
or supplied via --config.

Usage::

    python manage.py configure_vnet --deployment 1
    python manage.py configure_vnet --deployment 1 --dry-run
    python manage.py configure_vnet --deployment 1 --start-at-step transport
    python manage.py configure_vnet --deployment 1 --skip-manager-deploy
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from bare_metal_automation.dashboard.events import deployment_log, phase_completed, phase_started
from bare_metal_automation.dashboard.models import Deployment, DeploymentLog

logger = logging.getLogger(__name__)

STEPS = [
    "manager",
    "transport",
    "host_tnp",
    "edge",
    "tier0",
    "tier1",
    "segments",
    "firewall",
]


class Command(BaseCommand):
    help = "Phase 8: Virtual Network Configuration — NSX-T transport, routing, segments, DFW."

    def add_arguments(self, parser):
        parser.add_argument(
            "--deployment", type=int, required=True,
            help="PK of the Deployment to operate on.",
        )
        parser.add_argument(
            "--config", type=str, default="",
            help="Path to JSON file with NSX config (overrides bundle defaults).",
        )
        parser.add_argument(
            "--start-at-step", type=str, choices=STEPS, default=STEPS[0],
            help="Resume deployment from this step.",
        )
        parser.add_argument(
            "--skip-manager-deploy", action="store_true",
            help="Skip NSX Manager OVA deployment (assume it is already running).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Log what would be done without making any API calls.",
        )
        parser.add_argument(
            "--ovftool-path", type=str, default="/usr/bin/ovftool",
            help="Path to ovftool binary (used for NSX Manager OVA deploy).",
        )

    def handle(self, *args, **options):
        deployment_id: int = options["deployment"]
        dry_run: bool = options["dry_run"]
        start_step: str = options["start_at_step"]
        skip_manager: bool = options["skip_manager_deploy"]
        ovftool_path: str = options["ovftool_path"]

        try:
            deployment = Deployment.objects.get(pk=deployment_id)
        except Deployment.DoesNotExist:
            raise CommandError(f"Deployment #{deployment_id} not found.")

        cfg = self._load_config(deployment, options["config"])

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Phase 8: Virtual Network Configuration for '{deployment.name}'"
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("  [DRY RUN] No changes will be made."))

        deployment.phase = "vnet_config"
        deployment.save(update_fields=["phase"])
        phase_started(deployment_id, "vnet_config", "Phase 8: Virtual Network Configuration started")
        self._log(deployment, "Phase 8: Virtual Network Configuration started")

        steps_to_run = STEPS[STEPS.index(start_step):]
        if skip_manager and "manager" in steps_to_run:
            steps_to_run = [s for s in steps_to_run if s != "manager"]
            logger.info("Skipping NSX Manager deployment (--skip-manager-deploy)")

        success = True
        for step in steps_to_run:
            self.stdout.write(f"  → {step} …")
            try:
                step_ok = self._run_step(step, cfg, ovftool_path, dry_run)
            except Exception as e:
                logger.exception(f"Step '{step}' raised: {e}")
                step_ok = False

            if step_ok:
                self.stdout.write(self.style.SUCCESS(f"    ✓ {step} complete"))
                self._log(deployment, f"Step '{step}' completed", "INFO")
            else:
                self.stdout.write(self.style.ERROR(f"    ✗ {step} FAILED"))
                self._log(deployment, f"Step '{step}' FAILED", "ERROR")
                success = False
                break

        if success:
            deployment.phase = "vnet_config_complete"
            deployment.save(update_fields=["phase"])
            phase_completed(deployment_id, "vnet_config", success=True,
                            message="Phase 8: Virtual Network Configuration complete")
            self._log(deployment, "Phase 8: Virtual Network Configuration complete")
            self.stdout.write(self.style.SUCCESS("Phase 8 complete."))
        else:
            deployment.phase = "failed"
            deployment.save(update_fields=["phase"])
            phase_completed(deployment_id, "vnet_config", success=False,
                            message="Phase 8: Virtual Network Configuration FAILED")
            raise CommandError("Phase 8 (Virtual Network Configuration) failed.")

    # ── Step dispatch ──────────────────────────────────────────────────────

    def _run_step(
        self, step: str, cfg: dict, ovftool_path: str, dry_run: bool
    ) -> bool:
        if dry_run:
            logger.info(f"[DRY RUN] Would execute NSX step: {step}")
            return True

        nsx = cfg.get("nsx", {})

        if step == "manager":
            return self._step_manager(nsx, ovftool_path)
        if step == "transport":
            return self._step_transport(nsx, cfg)
        if step == "host_tnp":
            return self._step_host_tnp(nsx, cfg)
        if step == "edge":
            return self._step_edge(nsx, cfg)
        if step == "tier0":
            return self._step_tier0(nsx, cfg)
        if step == "tier1":
            return self._step_tier1(nsx, cfg)
        if step == "segments":
            return self._step_segments(nsx, cfg)
        if step == "firewall":
            return self._step_firewall(nsx, cfg)

        logger.warning(f"Unknown step '{step}' — skipping")
        return True

    # ── Individual steps ───────────────────────────────────────────────────

    def _step_manager(self, nsx: dict, ovftool_path: str) -> bool:
        from bare_metal_automation.vinfra.nsx.manager import NSXManagerConfig, NSXManagerDeployer

        config = NSXManagerConfig(
            ova_path=nsx.get("ova_path", ""),
            esxi_host=nsx.get("esxi_host", ""),
            esxi_username=nsx.get("esxi_username", "root"),
            esxi_password=nsx.get("esxi_password", ""),
            esxi_datastore=nsx.get("esxi_datastore", "datastore1"),
            esxi_network=nsx.get("esxi_network", "PG-mgmt"),
            nsx_hostname=nsx.get("hostname", ""),
            nsx_ip=nsx.get("ip", ""),
            nsx_gateway=nsx.get("gateway", ""),
            nsx_netmask=nsx.get("netmask", "255.255.255.0"),
            nsx_dns=nsx.get("dns", []),
            nsx_ntp=nsx.get("ntp", []),
            nsx_password=nsx.get("password", ""),
            form_factor=nsx.get("form_factor", "small"),
        )
        deployer = NSXManagerDeployer(ovftool_path=ovftool_path, config=config)
        result = deployer.deploy()
        if not result.success:
            logger.error(f"NSX Manager deploy failed: {result.error}")
            return False

        # Register with vCenter if configured
        vc = nsx.get("vcenter", {})
        if vc:
            deployer.register_with_vcenter(
                vcenter_host=vc.get("ip", ""),
                vcenter_username=vc.get("username", ""),
                vcenter_password=vc.get("password", ""),
                vcenter_thumbprint=vc.get("thumbprint", ""),
            )
        return True

    def _step_transport(self, nsx: dict, cfg: dict) -> bool:
        from bare_metal_automation.vinfra.nsx.transport import TransportManager

        mgr = TransportManager(
            nsx_host=nsx["ip"],
            username=nsx.get("username", "admin"),
            password=nsx["password"],
        )
        transport_cfg = cfg.get("transport", {})
        return mgr.setup(
            overlay_tz_name=transport_cfg.get("overlay_tz", "overlay-tz"),
            vlan_tz_name=transport_cfg.get("vlan_tz", "vlan-tz"),
            uplink_profile_name=transport_cfg.get("uplink_profile", "bma-uplink-profile"),
            tep_pool_name=transport_cfg.get("tep_pool", "bma-tep-pool"),
            tep_pool_cidr=transport_cfg.get("tep_cidr", "169.254.100.0/24"),
            tep_pool_range_start=transport_cfg.get("tep_start", "169.254.100.10"),
            tep_pool_range_end=transport_cfg.get("tep_end", "169.254.100.200"),
            tep_pool_gateway=transport_cfg.get("tep_gw", "169.254.100.1"),
            host_switch_name=transport_cfg.get("host_switch", "nsxvswitch"),
            transport_vlan=transport_cfg.get("transport_vlan", 0),
            mtu=transport_cfg.get("mtu", 9000),
        )

    def _step_host_tnp(self, nsx: dict, cfg: dict) -> bool:
        from bare_metal_automation.vinfra.nsx.transport import TransportManager

        mgr = TransportManager(
            nsx_host=nsx["ip"],
            username=nsx.get("username", "admin"),
            password=nsx["password"],
        )
        transport_cfg = cfg.get("transport", {})
        cluster_id = cfg.get("vsphere_cluster_id", "")
        if not cluster_id:
            logger.warning("No vsphere_cluster_id in config — skipping host TNP")
            return True
        return mgr.apply_host_transport_node_profile(
            profile_name="bma-host-tnp",
            cluster_id=cluster_id,
            overlay_tz_name=transport_cfg.get("overlay_tz", "overlay-tz"),
            uplink_profile_name=transport_cfg.get("uplink_profile", "bma-uplink-profile"),
            tep_pool_name=transport_cfg.get("tep_pool", "bma-tep-pool"),
            host_switch_name=transport_cfg.get("host_switch", "nsxvswitch"),
        )

    def _step_edge(self, nsx: dict, cfg: dict) -> bool:
        from bare_metal_automation.vinfra.nsx.edge import EdgeManager, EdgeNodeSpec

        mgr = EdgeManager(
            nsx_host=nsx["ip"],
            username=nsx.get("username", "admin"),
            password=nsx["password"],
        )
        edge_nodes_cfg = cfg.get("edge_nodes", [])
        transport_cfg = cfg.get("transport", {})
        nodes = [
            EdgeNodeSpec(
                name=e["name"],
                hostname=e.get("hostname", e["name"]),
                password=e.get("password", nsx["password"]),
                form_factor=e.get("form_factor", "SMALL"),
                mgmt_ip=e["mgmt_ip"],
                mgmt_netmask=e.get("mgmt_netmask", "255.255.255.0"),
                mgmt_gateway=e.get("mgmt_gateway", nsx.get("gateway", "")),
                mgmt_dns=e.get("dns", nsx.get("dns", [])),
                mgmt_ntp=e.get("ntp", nsx.get("ntp", [])),
                vcenter_host=e.get("vcenter_host", cfg.get("vcenter", {}).get("ip", "")),
                vcenter_username=e.get("vcenter_username", ""),
                vcenter_password=e.get("vcenter_password", ""),
                compute_id=e.get("compute_id", ""),
                storage_id=e.get("storage_id", ""),
                mgmt_network_id=e.get("mgmt_network_id", ""),
                overlay_tz_id=e.get("overlay_tz_id", transport_cfg.get("overlay_tz", "overlay-tz")),
                vlan_tz_id=e.get("vlan_tz_id", transport_cfg.get("vlan_tz", "vlan-tz")),
                uplink_profile_id=e.get("uplink_profile_id",
                                        transport_cfg.get("uplink_profile", "bma-uplink-profile")),
                tep_pool_id=e.get("tep_pool_id", transport_cfg.get("tep_pool", "bma-tep-pool")),
                host_switch_name=transport_cfg.get("host_switch", "nsxvswitch"),
            )
            for e in edge_nodes_cfg
        ]
        if not nodes:
            logger.warning("No edge_nodes in config — skipping edge deployment")
            return True
        return mgr.setup(
            edge_nodes=nodes,
            cluster_name=cfg.get("edge_cluster_name", "bma-edge-cluster"),
        )

    def _step_tier0(self, nsx: dict, cfg: dict) -> bool:
        from bare_metal_automation.vinfra.nsx.routing import RoutingManager

        mgr = RoutingManager(
            nsx_host=nsx["ip"],
            username=nsx.get("username", "admin"),
            password=nsx["password"],
        )
        t0_cfg = cfg.get("tier0", {})
        return mgr.setup_tier0(
            name=t0_cfg.get("name", "t0-site"),
            edge_cluster_name=cfg.get("edge_cluster_name", "bma-edge-cluster"),
            ha_mode=t0_cfg.get("ha_mode", "ACTIVE_STANDBY"),
            uplink_segment_name=t0_cfg.get("uplink_segment"),
            uplink_ip=t0_cfg.get("uplink_ip"),
        )

    def _step_tier1(self, nsx: dict, cfg: dict) -> bool:
        from bare_metal_automation.vinfra.nsx.routing import RoutingManager

        mgr = RoutingManager(
            nsx_host=nsx["ip"],
            username=nsx.get("username", "admin"),
            password=nsx["password"],
        )
        t0_name = cfg.get("tier0", {}).get("name", "t0-site")
        edge_cluster = cfg.get("edge_cluster_name", "bma-edge-cluster")
        mission_count = cfg.get("mission_count", 2)
        all_ok = True

        # Management Tier-1
        ok = mgr.setup_tier1(name="t1-mgmt", tier0_name=t0_name, edge_cluster_name=edge_cluster)
        all_ok = all_ok and ok

        # Per-mission Tier-1s
        for n in range(mission_count):
            mission_id = f"m{n + 1}"
            ok = mgr.setup_tier1(
                name=f"t1-{mission_id}",
                tier0_name=t0_name,
                edge_cluster_name=edge_cluster,
            )
            all_ok = all_ok and ok

        return all_ok

    def _step_segments(self, nsx: dict, cfg: dict) -> bool:
        from bare_metal_automation.vinfra.nsx.segments import SegmentManager

        mgr = SegmentManager(
            nsx_host=nsx["ip"],
            username=nsx.get("username", "admin"),
            password=nsx["password"],
        )
        transport_cfg = cfg.get("transport", {})
        mission_count = cfg.get("mission_count", 2)
        mission_ids = [f"m{n + 1}" for n in range(mission_count)]
        mission_tier1_names = {m: f"t1-{m}" for m in mission_ids}

        return mgr.setup(
            overlay_tz_name=transport_cfg.get("overlay_tz", "overlay-tz"),
            mgmt_tier1_name="t1-mgmt",
            mission_tier1_names=mission_tier1_names,
            mgmt_subnets=cfg.get("mgmt_subnets"),
            mission_subnets=cfg.get("mission_subnets"),
        )

    def _step_firewall(self, nsx: dict, cfg: dict) -> bool:
        from bare_metal_automation.vinfra.nsx.firewall import FirewallManager

        mgr = FirewallManager(
            nsx_host=nsx["ip"],
            username=nsx.get("username", "admin"),
            password=nsx["password"],
        )
        mission_count = cfg.get("mission_count", 2)
        mission_ids = [f"m{n + 1}" for n in range(mission_count)]
        shared_services = cfg.get("shared_services", {})
        return mgr.setup(mission_ids=mission_ids, shared_services=shared_services)

    # ── Config loading ─────────────────────────────────────────────────────

    def _load_config(self, deployment: Deployment, config_path: str) -> dict:
        if config_path and Path(config_path).exists():
            with open(config_path) as fh:
                return json.load(fh)

        if hasattr(deployment, "site_config") and deployment.site_config:
            cfg = deployment.site_config
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            return cfg

        logger.warning("No NSX config found — supply --config or ensure bundle includes nsx section.")
        return {}

    # ── Logging ────────────────────────────────────────────────────────────

    def _log(self, deployment: Deployment, message: str, level: str = "INFO") -> None:
        DeploymentLog.objects.create(
            deployment=deployment,
            level=level,
            phase="vnet_config",
            message=message,
        )
        deployment_log(deployment.pk, level, message, phase="vnet_config")
