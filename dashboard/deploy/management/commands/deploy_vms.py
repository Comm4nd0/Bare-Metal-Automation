"""
Management command: deploy_vms

Phase 9: VM Software Deployment — Orchestrates the 8-step software configuration
sequence across all 21 management VMs using ansible-runner to invoke the
phase9_software.yml playbook.

Steps:
  1. DC + DNS (AD forest, DNS zones, DHCP, OUs, service accounts, GPOs)
  2. Core infrastructure (CA, WSUS, log collector)
  3. Management (NPS/RADIUS, monitoring, SCCM, backup, bastion)
  4. File & database (DFS, MSSQL, print server)
  5. Application servers (8 servers, per-tenant NSX segments)
  6. Security (EDR, Defender, SIEM forwarder)
  7. Network device management (SNMP, syslog, AAA, 802.1X)
  8. Physical server domain join and agent install
"""

from __future__ import annotations

import logging
import os
import pathlib
import threading
from typing import Any

import ansible_runner
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from deploy.models import Deployment, DeploymentPhase, PhaseStatus

logger = logging.getLogger(__name__)

PHASE_NUMBER = 9
PHASE_NAME = "VM Software Deployment"

# Tags in the playbook that map to each of the 8 sub-steps
STEP_TAGS: list[tuple[str, str]] = [
    ("step1", "DC + DNS"),
    ("step2", "Core Infrastructure (CA, WSUS, Log Collector)"),
    ("step3", "Management (NPS, Monitoring, SCCM, Backup, Bastion)"),
    ("step4", "File & Database Servers"),
    ("step5", "Application Servers"),
    ("step6", "Security (EDR, Defender, SIEM)"),
    ("step7", "Network Device Management"),
    ("step8", "Physical Server Domain Join"),
]

# Path to the Ansible project root (relative to the repository root)
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[7]
ANSIBLE_DIR = _REPO_ROOT / "ansible"
PLAYBOOK_PATH = ANSIBLE_DIR / "playbooks" / "phase9_software.yml"


class Command(BaseCommand):
    help = (
        "Phase 9: VM Software Deployment. Runs ansible-runner to invoke "
        "phase9_software.yml across all 21 management VMs in dependency order."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--deployment",
            type=int,
            required=True,
            help="Primary key of the Deployment to operate on.",
        )
        parser.add_argument(
            "--inventory",
            type=str,
            default="",
            help=(
                "Path to Ansible inventory file. "
                "Defaults to ansible/inventory/<site_slug>.yml inside the bundle path."
            ),
        )
        parser.add_argument(
            "--steps",
            type=str,
            default="",
            help=(
                "Comma-separated sub-step tags to run, e.g. --steps step1,step2. "
                "Default: all steps in order."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Pass --check to ansible-runner (no changes made to hosts).",
        )
        parser.add_argument(
            "--vault-password-file",
            type=str,
            default="",
            help="Path to the ansible-vault password file.",
        )
        parser.add_argument(
            "--verbosity-ansible",
            type=int,
            default=1,
            choices=[0, 1, 2, 3, 4],
            help="Ansible verbosity level (0-4). Default: 1.",
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        deployment_id: int = options["deployment"]
        dry_run: bool = options["dry_run"]
        vault_pw_file: str = options["vault_password_file"]
        ansible_verbosity: int = options["verbosity_ansible"]

        try:
            deployment = Deployment.objects.get(pk=deployment_id)
        except Deployment.DoesNotExist:
            raise CommandError(f"Deployment #{deployment_id} not found.")

        # Resolve inventory path
        inventory = self._resolve_inventory(options["inventory"], deployment)

        # Validate the playbook exists
        if not PLAYBOOK_PATH.exists():
            raise CommandError(
                f"Phase 9 playbook not found at {PLAYBOOK_PATH}. "
                "Ensure the ansible/ directory is present."
            )

        # Determine which steps to run
        requested_steps = self._parse_steps(options["steps"])

        # Locate or create the DeploymentPhase record
        phase = self._get_or_create_phase(deployment)

        dry_run_label = " [DRY RUN]" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Phase 9 — VM Software Deployment{dry_run_label}\n"
                f"  Deployment : {deployment.site_name} (#{deployment_id})\n"
                f"  Inventory  : {inventory}\n"
                f"  Playbook   : {PLAYBOOK_PATH}\n"
                f"  Steps      : {[s for s, _ in requested_steps]}\n"
            )
        )

        phase.start()

        warning_count = 0
        try:
            for tag, label in requested_steps:
                self.stdout.write(f"  → Running {tag}: {label}")
                rc, warnings = self._run_step(
                    tag=tag,
                    inventory=inventory,
                    dry_run=dry_run,
                    vault_pw_file=vault_pw_file,
                    verbosity=ansible_verbosity,
                )
                warning_count += warnings
                if rc != 0:
                    error_msg = f"ansible-runner exited with rc={rc} during {tag} ({label})"
                    phase.fail(error_message=error_msg)
                    raise CommandError(error_msg)
                self.stdout.write(self.style.SUCCESS(f"    ✓ {tag} completed (warnings={warnings})"))
        except CommandError:
            raise
        except Exception as exc:  # pragma: no cover
            phase.fail(error_message=str(exc))
            raise CommandError(f"Unexpected error in Phase 9: {exc}") from exc

        phase.complete(warning_count=warning_count)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nPhase 9 complete{dry_run_label} — "
                f"{len(requested_steps)} steps, {warning_count} warnings."
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_inventory(self, inventory_arg: str, deployment: Deployment) -> str:
        """Return a usable inventory path, falling back to the bundle path."""
        if inventory_arg:
            if not os.path.exists(inventory_arg):
                raise CommandError(f"Inventory file not found: {inventory_arg}")
            return inventory_arg

        # Try to derive from bundle_path
        if deployment.bundle_path:
            candidate = os.path.join(
                deployment.bundle_path,
                "ansible",
                "inventory",
                f"{deployment.site_slug}.yml",
            )
            if os.path.exists(candidate):
                return candidate

        # Fall back to example inventory
        fallback = str(ANSIBLE_DIR / "inventory" / "inventory.example.yaml")
        logger.warning("No inventory found — using fallback %s", fallback)
        return fallback

    def _parse_steps(self, steps_arg: str) -> list[tuple[str, str]]:
        """Validate and return the ordered list of (tag, label) tuples to run."""
        all_tags = {tag for tag, _ in STEP_TAGS}
        if not steps_arg:
            return STEP_TAGS

        requested: list[tuple[str, str]] = []
        for raw in steps_arg.split(","):
            tag = raw.strip()
            if tag not in all_tags:
                raise CommandError(
                    f"Unknown step tag '{tag}'. Valid tags: {sorted(all_tags)}"
                )
            # Preserve original ordering
            for t, label in STEP_TAGS:
                if t == tag:
                    requested.append((t, label))
                    break
        return requested

    def _get_or_create_phase(self, deployment: Deployment) -> DeploymentPhase:
        """Get or create the Phase 9 record for this deployment."""
        phase, _ = DeploymentPhase.objects.get_or_create(
            deployment=deployment,
            phase_number=PHASE_NUMBER,
            defaults={"phase_name": PHASE_NAME, "status": PhaseStatus.PENDING},
        )
        if phase.status == PhaseStatus.COMPLETED:
            # Allow re-runs by resetting
            phase.status = PhaseStatus.PENDING
            phase.started_at = None
            phase.completed_at = None
            phase.error_message = ""
            phase.warning_count = 0
            phase.save()
        return phase

    def _run_step(
        self,
        tag: str,
        inventory: str,
        dry_run: bool,
        vault_pw_file: str,
        verbosity: int,
    ) -> tuple[int, int]:
        """
        Invoke ansible-runner for a single step tag.

        Returns (return_code, warning_count).
        """
        cmdline_args: list[str] = [
            "--tags", tag,
            "-v" * verbosity if verbosity else "",
        ]
        if dry_run:
            cmdline_args.append("--check")

        # Strip empty args
        cmdline_args = [a for a in cmdline_args if a]

        runner_config: dict[str, Any] = {
            "private_data_dir": str(ANSIBLE_DIR),
            "playbook": str(PLAYBOOK_PATH.relative_to(ANSIBLE_DIR)),
            "inventory": inventory,
            "cmdline": " ".join(cmdline_args),
            "quiet": False,
            "rotate_artifacts": 10,
        }

        if vault_pw_file:
            runner_config["passwords"] = {}
            runner_config["cmdline"] += f" --vault-password-file {vault_pw_file}"

        # Stream event output to our stdout
        warning_count = 0
        stdout_lock = threading.Lock()

        def _event_handler(event: dict[str, Any]) -> None:
            nonlocal warning_count
            event_data = event.get("event_data", {})
            res = event_data.get("res", {})

            if event.get("event") == "runner_on_ok":
                changed = res.get("changed", False)
                if changed:
                    host = event_data.get("host", "unknown")
                    task = event_data.get("task", "")
                    with stdout_lock:
                        self.stdout.write(f"    [changed] {host}: {task}")

            elif event.get("event") == "runner_on_failed":
                warning_count += 1
                host = event_data.get("host", "unknown")
                task = event_data.get("task", "")
                msg = res.get("msg", "")
                with stdout_lock:
                    self.stdout.write(
                        self.style.WARNING(f"    [failed] {host}: {task} — {msg}")
                    )

            elif event.get("event") == "runner_on_skipped":
                pass  # suppress skip noise at default verbosity

        result = ansible_runner.run(
            **runner_config,
            event_handler=_event_handler,
        )

        return result.rc, warning_count
