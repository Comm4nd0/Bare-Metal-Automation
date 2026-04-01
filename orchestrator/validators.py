"""Node validators — check that a NetBox site matches its template.

Used by orchestrate.py as a post-generate gate before config generation.
Can also be called standalone:

    python -m orchestrator.validators --site mysite --netbox-url http://netbox:8080
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pynetbox
import yaml
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

# ── Repo root helpers ──────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_TEMPLATES_DIR = REPO_ROOT / "site_templates"


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as fh:
        return yaml.safe_load(fh)


# ── Result dataclasses ────────────────────────────────────────────────────────


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    category: str  # "device" | "vlan" | "prefix" | "cable" | "cluster" | "custom_field"
    message: str
    detail: str = ""


@dataclass
class ValidationResult:
    site_slug: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, category: str, message: str, detail: str = "") -> None:
        self.issues.append(ValidationIssue("error", category, message, detail))

    def add_warning(self, category: str, message: str, detail: str = "") -> None:
        self.issues.append(ValidationIssue("warning", category, message, detail))


# ── Validator ─────────────────────────────────────────────────────────────────


class NodeValidator:
    """Validate that a NetBox site matches its declared site template.

    Checks:
    - All expected devices exist with correct model and platform
    - All management VLANs exist with correct VIDs
    - All mission VLANs exist
    - All IPAM prefixes exist
    - All cables are present
    - vSphere cluster exists with correct member count
    - Custom fields template_name / template_version are set
    """

    def __init__(self, nb: pynetbox.api, site_slug: str) -> None:
        self.nb = nb
        self.site_slug = site_slug

    def run(self) -> ValidationResult:
        """Run all validation checks. Returns a ValidationResult."""
        result = ValidationResult(site_slug=self.site_slug)

        site = self.nb.dcim.sites.get(slug=self.site_slug)
        if site is None:
            result.add_error("site", f"Site '{self.site_slug}' not found in NetBox")
            return result

        template_name = (site.custom_fields or {}).get("template_name")
        template_version = (site.custom_fields or {}).get("template_version")

        if not template_name:
            result.add_error(
                "custom_field",
                "custom_field 'template_name' is not set on site",
                detail="Run site_generate.py to populate custom fields",
            )
            return result

        template_path = SITE_TEMPLATES_DIR / f"{template_name}.yaml"
        if not template_path.exists():
            result.add_error(
                "custom_field",
                f"Template file not found: {template_path}",
                detail=f"template_name={template_name}",
            )
            return result

        template = _load_yaml(template_path)
        cabling_name = template["template"]["cabling_rules"]
        cabling_path = SITE_TEMPLATES_DIR / "cabling" / f"{cabling_name}.yaml"
        cabling = _load_yaml(cabling_path) if cabling_path.exists() else {"cables": []}

        site_octet = template["network"]["default_site_octet"]

        logger.info(
            "Validating site '%s' against template '%s' v%s",
            self.site_slug, template_name, template_version or "?",
        )

        self._validate_devices(result, site, template)
        self._validate_vlans(result, site, template, site_octet)
        self._validate_prefixes(result, site, template, site_octet)
        self._validate_cables(result, site, cabling)
        self._validate_cluster(result, site, template)

        return result

    # ── Per-check methods ──────────────────────────────────────────────────

    def _validate_devices(
        self,
        result: ValidationResult,
        site: Any,
        template: dict[str, Any],
    ) -> None:
        existing: dict[str, Any] = {
            d.name: d
            for d in self.nb.dcim.devices.filter(site_id=site.id)
        }

        for spec in template["devices"]:
            count: int = spec["count"]
            pattern: str = spec["name_pattern"]
            model_slug: str = spec["model_slug"]
            platform_slug: str = spec["platform"]

            for n in range(1, count + 1):
                name = pattern.format(n=n)
                if name not in existing:
                    result.add_error(
                        "device",
                        f"Device missing: {name}",
                        detail=f"Expected model={spec['model']}, platform={platform_slug}",
                    )
                    continue

                dev = existing[name]
                actual_model = dev.device_type.slug if dev.device_type else ""
                actual_platform = dev.platform.slug if dev.platform else ""

                if actual_model != model_slug:
                    result.add_error(
                        "device",
                        f"Device {name}: wrong model",
                        detail=f"expected={model_slug}, actual={actual_model}",
                    )
                if actual_platform != platform_slug:
                    result.add_error(
                        "device",
                        f"Device {name}: wrong platform",
                        detail=f"expected={platform_slug}, actual={actual_platform}",
                    )

    def _validate_vlans(
        self,
        result: ValidationResult,
        site: Any,
        template: dict[str, Any],
        site_octet: int,
    ) -> None:
        existing_vids: set[int] = {
            v.vid for v in self.nb.ipam.vlans.filter(site_id=site.id)
        }

        # Management VLANs
        for vlan_spec in template["vlans"]["management"]:
            vid: int = vlan_spec["vid"]
            if vid not in existing_vids:
                result.add_error(
                    "vlan",
                    f"Management VLAN missing: VID {vid} ({vlan_spec['name']})",
                )

        # Mission VLANs
        mission_count: int = template["missions"]["count"]
        for n in range(mission_count):
            for offset, label in [(0, "users"), (10, "apps"), (20, "data")]:
                vid = 1100 + n * 100 + offset
                if vid not in existing_vids:
                    result.add_error(
                        "vlan",
                        f"Mission {n + 1} VLAN missing: VID {vid} ({label})",
                    )

        # HA heartbeat VLAN (large site)
        ha_vlan_spec = next(
            (v for v in template["vlans"]["management"] if v.get("vid") == 999), None
        )
        if ha_vlan_spec and 999 not in existing_vids:
            result.add_error("vlan", "HA heartbeat VLAN 999 missing")

    def _validate_prefixes(
        self,
        result: ValidationResult,
        site: Any,
        template: dict[str, Any],
        site_octet: int,
    ) -> None:
        existing_prefixes: set[str] = {
            str(p.prefix)
            for p in self.nb.ipam.prefixes.filter(site_id=site.id)
        }

        def _rendered(template_str: str) -> str:
            return template_str.replace("{X}", str(site_octet))

        for vlan_spec in template["vlans"]["management"]:
            prefix = _rendered(vlan_spec["prefix_template"])
            if prefix not in existing_prefixes:
                result.add_error(
                    "prefix",
                    f"Prefix missing: {prefix} (VLAN {vlan_spec['vid']} {vlan_spec['name']})",
                )

        mission_count: int = template["missions"]["count"]
        for n in range(mission_count):
            prefixes = [
                f"10.{site_octet}.{11 + n}.0/24",
                f"10.{site_octet}.{111 + n * 10}.0/24",
                f"10.{site_octet}.{112 + n * 10}.0/24",
            ]
            labels = ["users", "apps", "data"]
            for prefix, label in zip(prefixes, labels):
                if prefix not in existing_prefixes:
                    result.add_error(
                        "prefix",
                        f"Mission {n + 1} {label} prefix missing: {prefix}",
                    )

    def _validate_cables(
        self,
        result: ValidationResult,
        site: Any,
        cabling: dict[str, Any],
    ) -> None:
        # Build set of (device_name, interface_name) pairs connected at this site
        connected_pairs: set[tuple[str, str]] = set()
        for cable in self.nb.dcim.cables.filter(site_id=site.id):
            for term in (cable.a_terminations or []) + (cable.b_terminations or []):
                obj = term.get("object") if isinstance(term, dict) else None
                if obj:
                    dev_name = (obj.get("device") or {}).get("name", "")
                    iface_name = obj.get("name", "")
                    if dev_name and iface_name:
                        connected_pairs.add((dev_name, iface_name))

        for cable_spec in cabling.get("cables", []):
            a = cable_spec["a"]
            b = cable_spec["b"]
            a_pair = (a["device"], a["interface"])
            b_pair = (b["device"], b["interface"])

            if a_pair not in connected_pairs:
                result.add_error(
                    "cable",
                    f"Cable endpoint missing: {a['device']} {a['interface']}",
                    detail=cable_spec.get("description", ""),
                )
            if b_pair not in connected_pairs:
                result.add_error(
                    "cable",
                    f"Cable endpoint missing: {b['device']} {b['interface']}",
                    detail=cable_spec.get("description", ""),
                )

    def _validate_cluster(
        self,
        result: ValidationResult,
        site: Any,
        template: dict[str, Any],
    ) -> None:
        cluster_conf = template.get("cluster", {})
        if not cluster_conf:
            return

        cluster_name = cluster_conf["name_pattern"].format(site_slug=self.site_slug)
        cluster = self.nb.virtualization.clusters.get(name=cluster_name)

        if cluster is None:
            result.add_error(
                "cluster",
                f"vSphere cluster missing: {cluster_name}",
            )
            return

        compute_role_slug: str = cluster_conf["compute_role_slug"]
        compute_devices = list(
            self.nb.dcim.devices.filter(
                site_id=site.id,
                role=compute_role_slug,
            )
        )
        members = list(
            self.nb.virtualization.cluster_members.filter(cluster_id=cluster.id)
            if hasattr(self.nb.virtualization, "cluster_members")
            else []
        )
        expected_count = len(compute_devices)
        actual_count = len(members)

        if actual_count != expected_count:
            result.add_warning(
                "cluster",
                f"Cluster '{cluster_name}' has {actual_count} members, expected {expected_count}",
            )


# ── Console report ────────────────────────────────────────────────────────────


def print_validation_report(result: ValidationResult) -> None:
    """Render a rich table report of validation results."""
    if not result.issues:
        console.print(
            f"[bold green]✓ Site '{result.site_slug}' passed all validation checks[/]"
        )
        return

    table = Table(
        title=f"Validation Report — {result.site_slug}",
        show_lines=True,
    )
    table.add_column("Severity", style="bold", width=9)
    table.add_column("Category", width=14)
    table.add_column("Message")
    table.add_column("Detail", style="dim")

    for issue in result.issues:
        sev_style = "[red]ERROR[/]" if issue.severity == "error" else "[yellow]WARN[/]"
        table.add_row(sev_style, issue.category, issue.message, issue.detail)

    console.print(table)
    console.print(
        f"\n  Errors: [bold red]{len(result.errors)}[/]   "
        f"Warnings: [bold yellow]{len(result.warnings)}[/]"
    )
    if result.passed:
        console.print("  [bold green]PASS[/] — no blocking errors")
    else:
        console.print("  [bold red]FAIL[/] — fix errors before proceeding")


# ── CLI entry point ───────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Validate a NetBox site against its BMA template",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--site", required=True, help="Site slug in NetBox")
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

    if not args.netbox_url or not args.netbox_token:
        console.print(
            "[red]Error:[/] --netbox-url and --netbox-token are required "
            "(or set BMA_NETBOX_URL / BMA_NETBOX_TOKEN)"
        )
        return 1

    nb = pynetbox.api(args.netbox_url.rstrip("/"), token=args.netbox_token)
    validator = NodeValidator(nb, args.site)
    result = validator.run()
    print_validation_report(result)
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
