"""
Management command: deploy

Master Deployment Command — Runs all deployment phases (0-10) in sequence,
prompting for confirmation at each phase boundary before proceeding.
Individual phases can be selected via --phases; use --dry-run to walk through
the sequence without making any changes.

Sprint 4 implementation pending.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

from deploy.models import Deployment

logger = logging.getLogger(__name__)

ALL_PHASES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

PHASE_LABELS = {
    0: "Pre-flight Checks",
    1: "Discovery",
    2: "Cabling Validation",
    3: "Firmware Transfer",
    4: "Heavy Transfers",
    5: "Network Configuration",
    6: "Laptop Pivot",
    7: "Server Provisioning",
    8: "vCenter Deployment",
    9: "VM Deployment",
    10: "Final Validation",
}


class Command(BaseCommand):
    help = (
        "Master deployment command: runs all phases (0-10) in sequence with "
        "per-phase confirmation prompts. (Sprint 4 — not yet implemented.)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--deployment",
            type=int,
            required=True,
            help="Primary key of the Deployment to operate on.",
        )
        parser.add_argument(
            "--phases",
            type=str,
            default=",".join(str(p) for p in ALL_PHASES),
            help=(
                "Comma-separated list of phase numbers to execute "
                "(default: all phases 0-10). Example: --phases 1,2,3"
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help=(
                "Walk through the phase sequence and log what would be done "
                "without making any changes to devices or records."
            ),
        )

    def handle(self, *args, **options):
        deployment_id: int = options["deployment"]
        dry_run: bool = options["dry_run"]

        try:
            deployment = Deployment.objects.get(pk=deployment_id)
        except Deployment.DoesNotExist:
            raise CommandError(f"Deployment #{deployment_id} not found.")

        try:
            selected_phases = [int(p.strip()) for p in options["phases"].split(",")]
        except ValueError:
            raise CommandError(
                "--phases must be a comma-separated list of integers, e.g. --phases 1,2,5"
            )

        invalid = [p for p in selected_phases if p not in ALL_PHASES]
        if invalid:
            raise CommandError(
                f"Unknown phase number(s): {invalid}. Valid phases are {ALL_PHASES}."
            )

        dry_run_notice = " [DRY RUN — no changes will be made]" if dry_run else ""

        self.stdout.write(
            self.style.WARNING(
                f"Master deploy for '{deployment.site_name}'{dry_run_notice} is not yet implemented.\n"
                f"Selected phases: {selected_phases}\n"
                "This stub will be replaced in Sprint 4."
            )
        )
