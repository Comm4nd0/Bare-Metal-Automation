"""
Management command: factory_reset

Factory Reset — Drive the 6-phase reset sequence to return all devices in a
deployment to a clean, ZTP-ready state.  Requires explicit --confirm to prevent
accidental data loss.  The --sanitisation-method flag controls how device
configurations are erased (default: write-erase).

Sprint 4 implementation pending.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

from deploy.models import Deployment, FactoryReset, RESET_PHASE_NAMES

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Factory Reset: drives the 6-phase reset sequence to return infrastructure "
        "to ZTP-ready state. Requires --confirm. (Sprint 4 — not yet implemented.)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--deployment",
            type=int,
            required=True,
            help="Primary key of the Deployment to operate on.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help=(
                "Must be passed explicitly to acknowledge that this operation is "
                "destructive and will erase all device configurations."
            ),
        )
        parser.add_argument(
            "--sanitisation-method",
            dest="sanitisation_method",
            type=str,
            default="write-erase",
            choices=["write-erase", "secure-erase", "reload-in"],
            help=(
                "Method used to erase device configurations. "
                "Choices: write-erase (default), secure-erase, reload-in."
            ),
        )

    def handle(self, *args, **options):
        deployment_id: int = options["deployment"]
        confirmed: bool = options["confirm"]
        sanitisation_method: str = options["sanitisation_method"]

        if not confirmed:
            raise CommandError(
                "Factory reset is a destructive operation. "
                "Pass --confirm to acknowledge and proceed."
            )

        try:
            deployment = Deployment.objects.get(pk=deployment_id)
        except Deployment.DoesNotExist:
            raise CommandError(f"Deployment #{deployment_id} not found.")

        self.stdout.write(
            self.style.WARNING(
                f"Factory Reset for '{deployment.site_name}' "
                f"(sanitisation-method={sanitisation_method}) is not yet implemented.\n"
                f"Reset phases: {list(RESET_PHASE_NAMES.values())}\n"
                "This stub will be replaced in Sprint 4."
            )
        )
