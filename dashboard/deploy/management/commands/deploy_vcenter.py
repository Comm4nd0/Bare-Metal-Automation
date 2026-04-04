"""Management command: deploy_vcenter (legacy shim)

This command now delegates to the primary implementation in
``src/bare_metal_automation/dashboard/management/commands/deploy_vcenter.py``.

The legacy ``deploy.Deployment`` model PK is accepted for backwards
compatibility, but the command internally looks up the corresponding primary
``dashboard.Deployment`` record by site_slug.

See ``dashboard/README.md`` for migration guidance.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Phase 7: vCenter Deployment. "
        "[DEPRECATED — use the primary dashboard management command instead]"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--deployment",
            type=int,
            required=True,
            help="Primary key of the legacy Deployment to operate on.",
        )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                "⚠  This command is deprecated. "
                "Use the primary dashboard's deploy_vcenter command instead:\n"
                "  python -m bare_metal_automation.dashboard.manage deploy_vcenter "
                f"--deployment {options['deployment']}\n\n"
                "The legacy dashboard/ app will be removed in a future release. "
                "See dashboard/README.md for details."
            )
        )

        # Attempt to forward to primary app if available
        try:
            from bare_metal_automation.dashboard.management.commands.deploy_vcenter import (
                Command as PrimaryCommand,
            )

            primary_cmd = PrimaryCommand()
            primary_cmd.stdout = self.stdout
            primary_cmd.stderr = self.stderr
            primary_cmd.style = self.style

            # Try to find a matching primary Deployment by legacy site_name
            from deploy.models import Deployment as LegacyDeployment
            from bare_metal_automation.dashboard.models import Deployment as PrimaryDeployment

            legacy = LegacyDeployment.objects.get(pk=options["deployment"])
            primary = PrimaryDeployment.objects.filter(
                site_slug=legacy.site_slug
            ).first()
            if primary is None:
                raise CommandError(
                    f"No primary Deployment found with site_slug='{legacy.site_slug}'. "
                    "Create one in the primary dashboard first."
                )

            primary_cmd.handle(deployment=primary.pk, config="", start_at_step="vcsa_deploy",
                               dry_run=False, vcsa_deploy_path=(
                                   "/mnt/vcsa/vcsa-cli-installer/lin64/vcsa-deploy"
                               ))
        except ImportError:
            raise CommandError(
                "Primary dashboard app is not available. "
                "Ensure bare_metal_automation is installed."
            )
