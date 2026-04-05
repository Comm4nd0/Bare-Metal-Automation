"""Management command: configure_vnet (legacy shim)

This command now delegates to the primary implementation in
``src/bare_metal_automation/dashboard/management/commands/configure_vnet.py``.

See ``dashboard/README.md`` for migration guidance.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Phase 8b: Virtual Network Configuration. "
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
                "Use the primary dashboard's configure_vnet command instead:\n"
                "  python -m bare_metal_automation.dashboard.manage configure_vnet "
                f"--deployment {options['deployment']}\n\n"
                "The legacy dashboard/ app will be removed in a future release. "
                "See dashboard/README.md for details."
            )
        )

        try:
            from bare_metal_automation.dashboard.management.commands.configure_vnet import (
                Command as PrimaryCommand,
            )

            primary_cmd = PrimaryCommand()
            primary_cmd.stdout = self.stdout
            primary_cmd.stderr = self.stderr
            primary_cmd.style = self.style

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

            primary_cmd.handle(deployment=primary.pk, config="", start_at_step="manager",
                               skip_manager_deploy=False, dry_run=False,
                               ovftool_path="/usr/bin/ovftool")
        except ImportError:
            raise CommandError(
                "Primary dashboard app is not available. "
                "Ensure bare_metal_automation is installed."
            )
