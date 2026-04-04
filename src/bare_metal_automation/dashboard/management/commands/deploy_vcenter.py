"""Management command: deploy_vcenter

Phase 7 — vCenter Deployment.

Orchestrates the full vCenter + vSphere configuration sequence:
  1. Deploy VCSA appliance (vcsa-deploy CLI)
  2. Create datacenter and cluster (pyvmomi)
  3. Configure vDS and port groups
  4. Configure vSAN / local storage
  5. Enable HA and DRS, configure vMotion VMkernel adapters
  6. Create content library and upload VM templates

All parameters are sourced from the active Deployment's site_config JSON field
(populated when the bundle is ingested) or can be overridden via CLI options.

Usage::

    python manage.py deploy_vcenter --deployment 1
    python manage.py deploy_vcenter --deployment 1 --dry-run
    python manage.py deploy_vcenter --deployment 1 --start-at-step cluster
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bare_metal_automation.dashboard.events import deployment_log, phase_completed, phase_started
from bare_metal_automation.dashboard.models import Deployment, DeploymentLog

logger = logging.getLogger(__name__)

# Ordered pipeline steps — can be resumed from any step via --start-at-step
STEPS = [
    "vcsa_deploy",
    "cluster",
    "vds",
    "storage",
    "ha_drs",
    "content_library",
]


class Command(BaseCommand):
    help = "Phase 7: vCenter Deployment — VCSA install, cluster, vDS, vSAN, HA/DRS, content library."

    def add_arguments(self, parser):
        parser.add_argument(
            "--deployment", type=int, required=True,
            help="PK of the Deployment to operate on.",
        )
        parser.add_argument(
            "--config", type=str, default="",
            help="Path to a JSON file with vCenter config (overrides bundle defaults).",
        )
        parser.add_argument(
            "--start-at-step", type=str, choices=STEPS, default=STEPS[0],
            help="Resume deployment from this step (default: vcsa_deploy).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Log what would be done without making any API calls.",
        )
        parser.add_argument(
            "--vcsa-deploy-path", type=str,
            default="/mnt/vcsa/vcsa-cli-installer/lin64/vcsa-deploy",
            help="Path to the vcsa-deploy binary.",
        )

    def handle(self, *args, **options):
        deployment_id: int = options["deployment"]
        dry_run: bool = options["dry_run"]
        start_step: str = options["start_at_step"]
        vcsa_deploy_path: str = options["vcsa_deploy_path"]

        try:
            deployment = Deployment.objects.get(pk=deployment_id)
        except Deployment.DoesNotExist:
            raise CommandError(f"Deployment #{deployment_id} not found.")

        # Load vCenter config
        cfg = self._load_config(deployment, options["config"])

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Phase 7: vCenter Deployment for '{deployment.name}'"
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("  [DRY RUN] No changes will be made."))

        # Update deployment phase
        deployment.phase = "vcenter_deploy"
        deployment.save(update_fields=["phase"])
        phase_started(deployment_id, "vcenter_deploy", "Phase 7: vCenter Deployment started")
        self._log(deployment, "Phase 7: vCenter Deployment started")

        steps_to_run = STEPS[STEPS.index(start_step):]
        success = True

        for step in steps_to_run:
            self.stdout.write(f"  → {step} …")
            try:
                step_ok = self._run_step(step, cfg, vcsa_deploy_path, dry_run, deployment)
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
            deployment.phase = "vcenter_deploy_complete"
            deployment.save(update_fields=["phase"])
            phase_completed(deployment_id, "vcenter_deploy", success=True,
                            message="Phase 7: vCenter Deployment complete")
            self._log(deployment, "Phase 7: vCenter Deployment complete")
            self.stdout.write(self.style.SUCCESS("Phase 7 complete."))
        else:
            deployment.phase = "failed"
            deployment.save(update_fields=["phase"])
            phase_completed(deployment_id, "vcenter_deploy", success=False,
                            message="Phase 7: vCenter Deployment FAILED")
            raise CommandError("Phase 7 (vCenter Deployment) failed — see logs above.")

    # ── Step dispatch ──────────────────────────────────────────────────────

    def _run_step(
        self,
        step: str,
        cfg: dict,
        vcsa_deploy_path: str,
        dry_run: bool,
        deployment: Deployment,
    ) -> bool:
        if dry_run:
            logger.info(f"[DRY RUN] Would execute step: {step}")
            return True

        vc = cfg.get("vcenter", {})
        esxi_hosts = cfg.get("esxi_hosts", [])

        if step == "vcsa_deploy":
            return self._step_vcsa_deploy(vc, vcsa_deploy_path)

        if step == "cluster":
            return self._step_cluster(vc, esxi_hosts)

        if step == "vds":
            return self._step_vds(vc, esxi_hosts, cfg)

        if step == "storage":
            return self._step_storage(vc, cfg)

        if step == "ha_drs":
            return self._step_ha_drs(vc, esxi_hosts, cfg)

        if step == "content_library":
            return self._step_content_library(vc, cfg)

        logger.warning(f"Unknown step '{step}' — skipping")
        return True

    # ── Individual steps ───────────────────────────────────────────────────

    def _step_vcsa_deploy(self, vc: dict, vcsa_deploy_path: str) -> bool:
        from bare_metal_automation.vinfra.vcenter.deploy import VCSAConfig, VCSADeployer

        config = VCSAConfig(
            esxi_host=vc["esxi_host"],
            esxi_username=vc.get("esxi_username", "root"),
            esxi_password=vc["esxi_password"],
            esxi_datastore=vc.get("esxi_datastore", "datastore1"),
            vcenter_hostname=vc["hostname"],
            vcenter_ip=vc["ip"],
            vcenter_gateway=vc["gateway"],
            vcenter_netmask=vc.get("netmask", "255.255.255.0"),
            vcenter_dns=vc.get("dns", []),
            vcenter_password=vc["password"],
            sso_domain=vc.get("sso_domain", "vsphere.local"),
            sso_password=vc.get("sso_password", vc["password"]),
            deployment_size=vc.get("deployment_size", "small"),
            datacenter_name=vc.get("datacenter_name", "BMA-DC"),
            cluster_name=vc.get("cluster_name", "BMA-Cluster"),
            ntp_servers=vc.get("ntp_servers", []),
        )
        deployer = VCSADeployer(vcsa_deploy_path=vcsa_deploy_path, config=config)
        result = deployer.deploy()
        if not result.success:
            logger.error(f"VCSA deploy failed: {result.error}")
        return result.success

    def _step_cluster(self, vc: dict, esxi_hosts: list[dict]) -> bool:
        from bare_metal_automation.vinfra.vcenter.cluster import ClusterManager, ESXiHost

        mgr = ClusterManager(
            vcenter_host=vc["ip"],
            username=vc.get("username", f"administrator@{vc.get('sso_domain', 'vsphere.local')}"),
            password=vc["password"],
        )
        hosts = [
            ESXiHost(ip=h["ip"], username=h.get("username", "root"), password=h["password"])
            for h in esxi_hosts
        ]
        return mgr.setup(
            datacenter_name=vc.get("datacenter_name", "BMA-DC"),
            cluster_name=vc.get("cluster_name", "BMA-Cluster"),
            esxi_hosts=hosts,
        )

    def _step_vds(self, vc: dict, esxi_hosts: list[dict], cfg: dict) -> bool:
        from bare_metal_automation.vinfra.vcenter.vds import VDSManager

        mgr = VDSManager(
            vcenter_host=vc["ip"],
            username=vc.get("username", f"administrator@{vc.get('sso_domain', 'vsphere.local')}"),
            password=vc["password"],
        )
        return mgr.setup(
            datacenter_name=vc.get("datacenter_name", "BMA-DC"),
            vds_name=cfg.get("vds_name", "BMA-vDS"),
            esxi_host_ips=[h["ip"] for h in esxi_hosts],
            mission_count=cfg.get("mission_count", 2),
            mtu=cfg.get("vds_mtu", 9000),
        )

    def _step_storage(self, vc: dict, cfg: dict) -> bool:
        from bare_metal_automation.vinfra.vcenter.storage import StorageManager

        mgr = StorageManager(
            vcenter_host=vc["ip"],
            username=vc.get("username", f"administrator@{vc.get('sso_domain', 'vsphere.local')}"),
            password=vc["password"],
        )
        storage_type = cfg.get("storage_type", "vsan")
        if storage_type == "vsan":
            result = mgr.configure_vsan(
                cluster_name=vc.get("cluster_name", "BMA-Cluster"),
                datacenter_name=vc.get("datacenter_name", "BMA-DC"),
            )
            return result.success
        else:
            datastores = mgr.list_local_datastores(
                datacenter_name=vc.get("datacenter_name", "BMA-DC"),
            )
            logger.info(f"Local datastores: {[d['name'] for d in datastores]}")
            return True

    def _step_ha_drs(self, vc: dict, esxi_hosts: list[dict], cfg: dict) -> bool:
        from bare_metal_automation.vinfra.vcenter.ha_drs import HADRSManager

        mgr = HADRSManager(
            vcenter_host=vc["ip"],
            username=vc.get("username", f"administrator@{vc.get('sso_domain', 'vsphere.local')}"),
            password=vc["password"],
        )
        vmotion_hosts = [
            (h["ip"], h.get("vmotion_ip", ""), h.get("vmotion_netmask", "255.255.255.0"))
            for h in esxi_hosts
            if h.get("vmotion_ip")
        ]
        return mgr.configure(
            datacenter_name=vc.get("datacenter_name", "BMA-DC"),
            cluster_name=vc.get("cluster_name", "BMA-Cluster"),
            esxi_hosts=vmotion_hosts or None,
            ha_admission_control_pct=cfg.get("ha_admission_control_pct", 25),
        )

    def _step_content_library(self, vc: dict, cfg: dict) -> bool:
        from bare_metal_automation.vinfra.vcenter.content_library import (
            ContentLibraryManager,
            LibraryTemplate,
        )

        mgr = ContentLibraryManager(
            vcenter_host=vc["ip"],
            username=vc.get("username", f"administrator@{vc.get('sso_domain', 'vsphere.local')}"),
            password=vc["password"],
        )
        templates_cfg = cfg.get("templates", [])
        templates = [
            LibraryTemplate(
                name=t["name"],
                local_path=t["path"],
                description=t.get("description", ""),
                item_type=t.get("type", "ovf"),
            )
            for t in templates_cfg
        ]
        library_name = cfg.get("content_library_name", "BMA-Templates")
        datastore_name = cfg.get("content_library_datastore", "vsanDatastore")
        return mgr.setup(
            library_name=library_name,
            datastore_name=datastore_name,
            templates=templates,
        )

    # ── Config loading ─────────────────────────────────────────────────────

    def _load_config(self, deployment: Deployment, config_path: str) -> dict:
        """Load vCenter config from a JSON file or fall back to deployment site_config."""
        if config_path and Path(config_path).exists():
            with open(config_path) as fh:
                return json.load(fh)

        # Try to pull from deployment.site_config if it exists
        if hasattr(deployment, "site_config") and deployment.site_config:
            cfg = deployment.site_config
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            return cfg

        # Return a minimal stub so the command can still report the error clearly
        logger.warning(
            "No vCenter config found — supply --config or ensure the "
            "deployment bundle includes a vcenter section in its manifest."
        )
        return {}

    # ── Logging helpers ────────────────────────────────────────────────────

    def _log(self, deployment: Deployment, message: str, level: str = "INFO") -> None:
        DeploymentLog.objects.create(
            deployment=deployment,
            level=level,
            phase="vcenter_deploy",
            message=message,
        )
        deployment_log(deployment.pk, level, message, phase="vcenter_deploy")
