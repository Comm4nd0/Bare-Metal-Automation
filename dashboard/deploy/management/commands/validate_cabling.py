"""
Management command: validate_cabling

Phase 2: Cabling Validation — Diff live CDP neighbour tables against the
intended topology from the deployment bundle and surface any mismatches.

Sprint 4 implementation pending.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

from deploy.models import Deployment

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Phase 2: Cabling Validation. (Sprint 4 — not yet implemented.)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--deployment",
            type=int,
            required=True,
            help="Primary key of the Deployment to operate on.",
        )

    def handle(self, *args, **options):
        deployment_id: int = options["deployment"]
        try:
            deployment = Deployment.objects.get(pk=deployment_id)
        except Deployment.DoesNotExist:
            raise CommandError(f"Deployment #{deployment_id} not found.")

        self.stdout.write(
            self.style.WARNING(
                f"Phase 2 (Cabling Validation) for '{deployment.site_name}' is not yet implemented.\n"
                "This stub will be replaced in Sprint 4."
            )
        )
