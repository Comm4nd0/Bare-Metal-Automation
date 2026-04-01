"""Pipeline orchestrator — end-to-end site lifecycle pipeline.

Stages:
  1. Connect — verify NetBox connectivity
  2. Generate or Regenerate — create site if new, fix drift if existing
  3. Validate — run NodeValidator, gate on errors
  4. Export — write BMA inventory YAML for the deployment process
  5. Package — tar.gz bundle of inventory + templates + firmware catalogue

Usage:
    bma-orchestrate \\
        --template medium-site \\
        --site-name "Alpha Site" \\
        --site-slug alpha-site \\
        --octet 200 \\
        --output-dir ./output/alpha-site

    # Re-run on existing site (drift fix + validate + export):
    bma-orchestrate --site-slug alpha-site --output-dir ./output/alpha-site

Environment variables:
    BMA_NETBOX_URL    NetBox base URL
    BMA_NETBOX_TOKEN  NetBox API token
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pynetbox
import yaml
from rich.console import Console
from rich.rule import Rule

from orchestrator.site_generate import SiteGenerator
from orchestrator.site_regenerate import DriftReport, SiteRegenerator, print_drift_report
from orchestrator.validators import NodeValidator, ValidationResult, print_validation_report

logger = logging.getLogger(__name__)
console = Console()

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_TEMPLATES_DIR = REPO_ROOT / "site_templates"
FIRMWARE_CATALOGUE = REPO_ROOT / "firmware_catalogue.yaml"


# ── Inventory exporter ────────────────────────────────────────────────────────


class InventoryExporter:
    """Export a NetBox site to a BMA deployment inventory YAML.

    The generated inventory is consumed by the existing BMA deployment
    orchestrator (src/bare_metal_automation/orchestrator.py).
    """

    def __init__(self, nb: pynetbox.api, site_slug: str) -> None:
        self.nb = nb
        self.site_slug = site_slug

    def export(self, output_path: Path) -> dict[str, Any]:
        """Build inventory dict and write to output_path. Returns the dict."""
        site = self.nb.dcim.sites.get(slug=self.site_slug)
        if site is None:
            raise ValueError(f"Site '{self.site_slug}' not found in NetBox")

        devices_raw = list(self.nb.dcim.devices.filter(site_id=site.id))
        devices: dict[str, Any] = {}

        for dev in devices_raw:
            ips = list(self.nb.ipam.ip_addresses.filter(device_id=dev.id))
            mgmt_ip = ""
            ilo_ip = ""

            for ip in ips:
                iface_name = ""
                if ip.assigned_object:
                    iface_name = str(ip.assigned_object)
                if "ilo" in iface_name.lower():
                    ilo_ip = str(ip.address).split("/")[0]
                elif not mgmt_ip:
                    mgmt_ip = str(ip.address).split("/")[0]

            devices[dev.name] = {
                "hostname": dev.name,
                "role": dev.role.slug if dev.role else "",
                "platform": dev.platform.slug if dev.platform else "",
                "model": dev.device_type.slug if dev.device_type else "",
                "mgmt_ip": mgmt_ip,
                "ilo_ip": ilo_ip,
                "status": str(dev.status),
                "serial": dev.serial or "",
            }

        # Read template info from site custom fields
        cf = site.custom_fields or {}
        template_name = cf.get("template_name", "")
        template_version = cf.get("template_version", "")

        inventory: dict[str, Any] = {
            "name": site.name,
            "site_slug": self.site_slug,
            "template_name": template_name,
            "template_version": template_version,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "bootstrap_subnet": "10.255.0.0/16",
            "laptop_ip": "10.255.0.1",
            "management_vlan": 100,
            "devices": devices,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as fh:
            yaml.dump(inventory, fh, default_flow_style=False, sort_keys=False)

        logger.info("Exported inventory to %s (%d devices)", output_path, len(devices))
        return inventory


# ── Bundle packager ───────────────────────────────────────────────────────────


def _create_bundle(output_dir: Path, site_slug: str, template_name: str) -> Path:
    """Create a deployment bundle tar.gz in output_dir.

    Bundle contents:
      inventory.yaml                — site inventory
      firmware_catalogue.yaml       — firmware catalogue
      site_templates/<template>.yaml
      site_templates/cabling/<template>.yaml
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_name = f"bma-{site_slug}-{ts}.tar.gz"
    bundle_path = output_dir / bundle_name

    with tarfile.open(bundle_path, "w:gz") as tar:
        # Inventory
        inventory_path = output_dir / "inventory.yaml"
        if inventory_path.exists():
            tar.add(inventory_path, arcname="inventory.yaml")

        # Firmware catalogue
        if FIRMWARE_CATALOGUE.exists():
            tar.add(FIRMWARE_CATALOGUE, arcname="firmware_catalogue.yaml")

        # Site template
        tmpl_path = SITE_TEMPLATES_DIR / f"{template_name}.yaml"
        if tmpl_path.exists():
            tar.add(tmpl_path, arcname=f"site_templates/{template_name}.yaml")

        # Cabling rules — infer cabling name from template
        try:
            tmpl = yaml.safe_load(tmpl_path.read_text()) if tmpl_path.exists() else {}
            cabling_name = (tmpl.get("template") or {}).get("cabling_rules", template_name)
            cabling_path = SITE_TEMPLATES_DIR / "cabling" / f"{cabling_name}.yaml"
            if cabling_path.exists():
                tar.add(cabling_path, arcname=f"site_templates/cabling/{cabling_name}.yaml")
        except Exception as exc:
            logger.warning("Could not include cabling YAML in bundle: %s", exc)

    logger.info("Created bundle: %s", bundle_path)
    return bundle_path


# ── Pipeline orchestrator ─────────────────────────────────────────────────────


class PipelineOrchestrator:
    """End-to-end BMA site pipeline.

    Stages:
      1. connect     — Verify NetBox is reachable + authenticated
      2. provision   — Generate site if new, or regenerate (fix drift) if existing
      3. validate    — Run NodeValidator; gate on errors
      4. export      — Write inventory YAML
      5. package     — Create deployment tar.gz bundle
    """

    def __init__(
        self,
        nb: pynetbox.api,
        site_slug: str,
        output_dir: Path,
        template_name: str | None = None,
        site_name: str | None = None,
        site_octet: int | None = None,
    ) -> None:
        self.nb = nb
        self.site_slug = site_slug
        self.output_dir = output_dir
        self.template_name = template_name
        self.site_name = site_name or site_slug
        self.site_octet = site_octet

        self._stage_results: dict[str, Any] = {}

    def run(self) -> bool:
        """Run all pipeline stages. Returns True on success."""
        console.print(Rule(f"[bold]BMA Pipeline — {self.site_slug}[/]"))

        stages = [
            ("connect", self._stage_connect),
            ("provision", self._stage_provision),
            ("validate", self._stage_validate),
            ("export", self._stage_export),
            ("package", self._stage_package),
        ]

        for stage_name, stage_fn in stages:
            console.print(f"\n[bold blue]▶ Stage: {stage_name.upper()}[/]")
            try:
                success = stage_fn()
                if not success:
                    console.print(
                        f"\n[bold red]✗ Pipeline halted at stage '{stage_name}'.[/]"
                    )
                    return False
                console.print(f"[green]  ✓ {stage_name} passed[/]")
            except Exception as exc:
                console.print(
                    f"[bold red]✗ Stage '{stage_name}' raised an unexpected error: {exc}[/]"
                )
                logger.exception("Pipeline stage '%s' failed", stage_name)
                return False

        console.print(Rule("[bold green]Pipeline complete[/]"))
        bundle = self._stage_results.get("bundle_path")
        if bundle:
            console.print(f"\n  Bundle : [bold]{bundle}[/]")
        return True

    # ── Stage implementations ──────────────────────────────────────────────

    def _stage_connect(self) -> bool:
        try:
            status = self.nb.status()
            console.print(
                f"  NetBox {status.get('netbox-version', '?')} "
                f"@ {self.nb.base_url}"
            )
            return True
        except Exception as exc:
            console.print(f"  [red]Cannot reach NetBox: {exc}[/]")
            return False

    def _stage_provision(self) -> bool:
        existing_site = self.nb.dcim.sites.get(slug=self.site_slug)

        if existing_site is None:
            # New site — generate from template
            if not self.template_name:
                console.print(
                    "  [red]Site does not exist and no --template provided.[/]\n"
                    "  Pass --template <name> to generate a new site."
                )
                return False

            template_path = SITE_TEMPLATES_DIR / f"{self.template_name}.yaml"
            if not template_path.exists():
                console.print(f"  [red]Template not found: {template_path}[/]")
                return False

            template = yaml.safe_load(template_path.read_text())
            cabling_name = template["template"]["cabling_rules"]
            cabling_path = SITE_TEMPLATES_DIR / "cabling" / f"{cabling_name}.yaml"
            cabling = (
                yaml.safe_load(cabling_path.read_text())
                if cabling_path.exists()
                else {"cables": []}
            )
            site_octet = self.site_octet or template["network"]["default_site_octet"]

            console.print(
                f"  Generating new site from template [cyan]{self.template_name}[/] "
                f"v{template['template']['version']}…"
            )
            generator = SiteGenerator(
                nb=self.nb,
                template=template,
                cabling=cabling,
                site_name=self.site_name,
                site_slug=self.site_slug,
                site_octet=site_octet,
            )
            generator.run()
            self._stage_results["provisioned"] = "generated"
        else:
            # Existing site — check drift and fix
            console.print(
                f"  Site '{self.site_slug}' exists. "
                "Checking for drift and applying fixes…"
            )
            regen = SiteRegenerator(self.nb, self.site_slug)
            drift: DriftReport = regen.fix()
            self._stage_results["drift_items"] = len(drift.items)
            self._stage_results["provisioned"] = "regenerated"
            if drift.has_drift:
                console.print(
                    f"  [yellow]Fixed {len(drift.items)} drifted item(s).[/]"
                )
            else:
                console.print("  No drift detected.")

        return True

    def _stage_validate(self) -> bool:
        validator = NodeValidator(self.nb, self.site_slug)
        result: ValidationResult = validator.run()
        print_validation_report(result)
        self._stage_results["validation_errors"] = len(result.errors)
        self._stage_results["validation_warnings"] = len(result.warnings)
        return result.passed

    def _stage_export(self) -> bool:
        inventory_path = self.output_dir / "inventory.yaml"
        exporter = InventoryExporter(self.nb, self.site_slug)
        try:
            inventory = exporter.export(inventory_path)
            device_count = len(inventory.get("devices", {}))
            console.print(
                f"  Exported inventory: {inventory_path} "
                f"({device_count} devices)"
            )
            self._stage_results["inventory_path"] = str(inventory_path)
            return True
        except ValueError as exc:
            console.print(f"  [red]Export failed: {exc}[/]")
            return False

    def _stage_package(self) -> bool:
        # Determine template name from NetBox (may differ from init arg)
        site = self.nb.dcim.sites.get(slug=self.site_slug)
        tmpl_name = (
            (site.custom_fields or {}).get("template_name", "")
            if site
            else (self.template_name or "")
        )
        bundle_path = _create_bundle(self.output_dir, self.site_slug, tmpl_name)
        console.print(f"  Bundle written: {bundle_path}")
        self._stage_results["bundle_path"] = str(bundle_path)
        return True


# ── CLI entry point ───────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="End-to-end BMA pipeline: generate → validate → export → package",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--site-slug",
        required=True,
        help="NetBox site slug (created if it doesn't exist)",
    )
    p.add_argument(
        "--site-name",
        default=None,
        help="Human-readable site name (only needed when creating a new site)",
    )
    p.add_argument(
        "--template",
        default=None,
        help="Template name — required when creating a new site",
    )
    p.add_argument(
        "--octet",
        type=int,
        default=None,
        help="IP addressing site octet (overrides template default)",
    )
    p.add_argument(
        "--output-dir",
        default="./output",
        help="Directory where inventory.yaml and bundle are written",
    )
    p.add_argument(
        "--netbox-url",
        default=os.environ.get("BMA_NETBOX_URL", ""),
        help="NetBox base URL (env: BMA_NETBOX_URL)",
    )
    p.add_argument(
        "--netbox-token",
        default=os.environ.get("BMA_NETBOX_TOKEN", ""),
        help="NetBox API token (env: BMA_NETBOX_TOKEN)",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.netbox_url or not args.netbox_token:
        console.print(
            "[red]Error:[/] --netbox-url and --netbox-token are required "
            "(or set BMA_NETBOX_URL / BMA_NETBOX_TOKEN)"
        )
        return 1

    nb = pynetbox.api(args.netbox_url.rstrip("/"), token=args.netbox_token)
    output_dir = Path(args.output_dir)

    pipeline = PipelineOrchestrator(
        nb=nb,
        site_slug=args.site_slug,
        output_dir=output_dir,
        template_name=args.template,
        site_name=args.site_name,
        site_octet=args.octet,
    )

    success = pipeline.run()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
