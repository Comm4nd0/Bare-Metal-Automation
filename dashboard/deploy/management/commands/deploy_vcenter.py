"""
Management command: deploy_vcenter

Phase 8: vCenter Deployment — Drive the vSphere installation onto the
designated management server using the VCSA installer in headless mode.

Sprint 4 implementation pending.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

from deploy.models import Deployment

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Phase 8: vCenter Deployment. (Sprint 4 — not yet implemented.)"

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
                f"Phase 8 (vCenter Deployment) for '{deployment.site_name}' is not yet implemented.\n"
                "This stub will be replaced in Sprint 4."
            )
        )
