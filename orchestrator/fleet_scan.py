"""Fleet scan — report template versions across all BMA-managed sites.

Queries NetBox for all sites with the 'template_name' custom field set,
compares each site's template_version against the current template on disk,
and reports which sites are outdated.

Exit codes:
  0 — all sites are up-to-date (or no sites found)
  1 — one or more sites are behind the current template version

Usage:
    bma-fleet-scan
    bma-fleet-scan --template medium-site
    bma-fleet-scan --format json

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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pynetbox
import yaml
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_TEMPLATES_DIR = REPO_ROOT / "site_templates"


# ── Version comparison ─────────────────────────────────────────────────────────


def _parse_semver(version_str: str) -> tuple[int, int]:
    """Parse 'MAJOR.MINOR' semver string. Returns (major, minor)."""
    parts = (version_str or "0.0").split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return 0, 0


def _is_outdated(site_version: str, template_version: str) -> bool:
    """Return True if site_version < template_version."""
    return _parse_semver(site_version) < _parse_semver(template_version)


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class SiteScanResult:
    site_slug: str
    site_name: str
    template_name: str
    site_version: str        # version stored in NetBox custom field
    current_version: str     # version in the on-disk template YAML
    last_synced: str         # template_last_synced custom field
    outdated: bool
    template_file_missing: bool = False


# ── Scanner ───────────────────────────────────────────────────────────────────


class FleetScanner:
    """Scan all NetBox sites managed by BMA and report version drift."""

    def __init__(self, nb: pynetbox.api) -> None:
        self.nb = nb

    def scan(self, filter_template: str | None = None) -> list[SiteScanResult]:
        """Scan all BMA sites. Optionally filter by template name.

        Args:
            filter_template: If given, only scan sites with this template_name.

        Returns:
            List of SiteScanResult sorted by (template_name, site_slug).
        """
        results: list[SiteScanResult] = []

        # Fetch all sites that have template_name set (non-empty)
        try:
            all_sites = list(self.nb.dcim.sites.all())
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch sites from NetBox: {exc}") from exc

        # Cache loaded template versions to avoid repeated disk reads
        template_version_cache: dict[str, str] = {}

        for site in all_sites:
            cf = site.custom_fields or {}
            template_name: str = cf.get("template_name", "") or ""
            if not template_name:
                continue  # Not a BMA-managed site

            if filter_template and template_name != filter_template:
                continue

            site_version: str = cf.get("template_version", "") or ""
            last_synced: str = cf.get("template_last_synced", "") or ""

            # Load current template version from disk
            if template_name not in template_version_cache:
                template_path = SITE_TEMPLATES_DIR / f"{template_name}.yaml"
                if template_path.exists():
                    data = yaml.safe_load(template_path.read_text())
                    template_version_cache[template_name] = data["template"]["version"]
                else:
                    template_version_cache[template_name] = ""

            current_version = template_version_cache[template_name]
            template_missing = current_version == ""

            results.append(
                SiteScanResult(
                    site_slug=site.slug,
                    site_name=site.name,
                    template_name=template_name,
                    site_version=site_version,
                    current_version=current_version,
                    last_synced=last_synced,
                    outdated=not template_missing and _is_outdated(site_version, current_version),
                    template_file_missing=template_missing,
                )
            )

        results.sort(key=lambda r: (r.template_name, r.site_slug))
        return results

    def available_templates(self) -> dict[str, str]:
        """Return {template_name: version} for all on-disk templates."""
        templates: dict[str, str] = {}
        for yaml_path in sorted(SITE_TEMPLATES_DIR.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_path.read_text())
                name = data["template"]["name"]
                version = data["template"]["version"]
                templates[name] = version
            except (KeyError, yaml.YAMLError):
                logger.warning("Skipping malformed template: %s", yaml_path)
        return templates


# ── Console report ─────────────────────────────────────────────────────────────


def print_fleet_report(results: list[SiteScanResult]) -> None:
    if not results:
        console.print("[dim]No BMA-managed sites found in NetBox.[/]")
        return

    table = Table(title="BMA Fleet — Template Version Report", show_lines=False)
    table.add_column("Site", style="bold")
    table.add_column("Template")
    table.add_column("Site Ver", justify="center")
    table.add_column("Current", justify="center")
    table.add_column("Status", justify="center")
    table.add_column("Last Synced", style="dim")

    outdated_count = 0
    for r in results:
        if r.template_file_missing:
            status = "[yellow]? NO TEMPLATE[/]"
        elif r.outdated:
            status = "[red]OUTDATED[/]"
            outdated_count += 1
        else:
            status = "[green]✓ current[/]"

        site_ver_display = r.site_version or "[dim](none)[/]"
        current_ver_display = r.current_version or "[dim](missing)[/]"

        table.add_row(
            f"{r.site_name}\n[dim]{r.site_slug}[/]",
            r.template_name,
            site_ver_display,
            current_ver_display,
            status,
            r.last_synced[:19] if r.last_synced else "—",
        )

    console.print(table)

    total = len(results)
    up_to_date = total - outdated_count
    console.print(
        f"\n  Total sites : {total}\n"
        f"  Up-to-date  : [green]{up_to_date}[/]\n"
        f"  Outdated    : [{'red' if outdated_count else 'green'}]{outdated_count}[/]"
    )

    if outdated_count:
        console.print(
            "\nTo update an outdated site, run:\n"
            "  [bold]bma-site-regenerate --site <slug> --mode fix[/]"
        )


def print_templates_report(templates: dict[str, str]) -> None:
    table = Table(title="Available BMA Templates")
    table.add_column("Template Name")
    table.add_column("Version", justify="center")
    for name, version in templates.items():
        table.add_row(name, version)
    console.print(table)


# ── CLI entry point ────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scan all BMA-managed NetBox sites and report template version drift",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--template",
        default=None,
        help="Filter by template name (e.g. medium-site). Default: all templates.",
    )
    p.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    p.add_argument(
        "--list-templates",
        action="store_true",
        help="List all available on-disk templates and their versions, then exit",
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
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.list_templates:
        scanner = FleetScanner(nb=None)  # type: ignore[arg-type]
        print_templates_report(scanner.available_templates())
        return 0

    if not args.netbox_url or not args.netbox_token:
        console.print(
            "[red]Error:[/] --netbox-url and --netbox-token are required "
            "(or set BMA_NETBOX_URL / BMA_NETBOX_TOKEN)"
        )
        return 1

    nb = pynetbox.api(args.netbox_url.rstrip("/"), token=args.netbox_token)
    scanner = FleetScanner(nb)

    try:
        results = scanner.scan(filter_template=args.template)
    except RuntimeError as exc:
        console.print(f"[red]Error:[/] {exc}")
        return 1

    if args.format == "json":
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print_fleet_report(results)

    outdated = any(r.outdated for r in results)
    return 1 if outdated else 0


if __name__ == "__main__":
    sys.exit(main())
