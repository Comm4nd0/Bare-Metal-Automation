"""Site regeneration script — detect and optionally fix drift between a NetBox
site and its BMA template.

Three modes:
  report   — Print a drift report, exit 1 if drift found, exit 0 if clean.
  fix      — Create/update missing or drifted objects (does NOT delete extras).
  rebuild  — Delete the entire site and regenerate from scratch.

Usage:
    bma-site-regenerate --site alpha-site --mode report
    bma-site-regenerate --site alpha-site --mode fix
    bma-site-regenerate --site alpha-site --mode rebuild --confirm

Environment variables:
    BMA_NETBOX_URL    NetBox base URL
    BMA_NETBOX_TOKEN  NetBox API token
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pynetbox
import yaml
from rich.console import Console
from rich.table import Table

from orchestrator.site_generate import SiteGenerator, _get_or_create, _iface_type, _resolve_cable_color

logger = logging.getLogger(__name__)
console = Console()

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_TEMPLATES_DIR = REPO_ROOT / "site_templates"


# ── Drift result dataclasses ──────────────────────────────────────────────────


@dataclass
class DriftItem:
    category: str   # "device" | "vlan" | "prefix" | "cable" | "cluster" | "custom_field"
    action: str     # "missing" | "wrong_model" | "wrong_platform" | "stale_version" | etc.
    name: str
    expected: str = ""
    actual: str = ""


@dataclass
class DriftReport:
    site_slug: str
    template_name: str
    template_version: str
    items: list[DriftItem] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return len(self.items) > 0

    def add(self, category: str, action: str, name: str, expected: str = "", actual: str = "") -> None:
        self.items.append(DriftItem(category, action, name, expected, actual))


# ── Regenerator ───────────────────────────────────────────────────────────────


class SiteRegenerator:
    """Compare a NetBox site against its BMA template and optionally reconcile.

    Workflow:
      1.  Load site from NetBox, read template_name custom field.
      2.  Load template YAML + cabling YAML.
      3.  Diff each resource class (devices, VLANs, prefixes, cables, cluster).
      4.  In 'report' mode: print DriftReport, return.
         In 'fix' mode: create/update drifted objects.
         In 'rebuild' mode: delete site + all objects, then call SiteGenerator.
    """

    def __init__(self, nb: pynetbox.api, site_slug: str) -> None:
        self.nb = nb
        self.site_slug = site_slug

        self._site: Any = None
        self._template: dict[str, Any] = {}
        self._cabling: dict[str, Any] = {}
        self._site_octet: int = 0

    # ── Public entry points ────────────────────────────────────────────────

    def report(self) -> DriftReport:
        """Build and return a DriftReport without making any changes."""
        self._load()
        return self._build_drift_report()

    def fix(self) -> DriftReport:
        """Apply fixes for all drifted items. Returns the pre-fix drift report."""
        self._load()
        drift = self._build_drift_report()
        if not drift.has_drift:
            console.print(f"[green]No drift detected for '{self.site_slug}' — nothing to fix.[/]")
            return drift
        self._apply_fixes(drift)
        return drift

    def rebuild(self) -> None:
        """Delete the site and recreate from template. Destructive — requires caller confirmation."""
        self._load()
        console.print(f"[bold red]Rebuilding site '{self.site_slug}'…[/]")
        self._delete_site()
        site_name = self._site.name  # captured before deletion
        site_octet = self._site_octet
        generator = SiteGenerator(
            nb=self.nb,
            template=self._template,
            cabling=self._cabling,
            site_name=site_name,
            site_slug=self.site_slug,
            site_octet=site_octet,
        )
        generator.run()

    # ── Internal helpers ───────────────────────────────────────────────────

    def _load(self) -> None:
        """Fetch site from NetBox and load its template."""
        self._site = self.nb.dcim.sites.get(slug=self.site_slug)
        if self._site is None:
            raise ValueError(f"Site '{self.site_slug}' not found in NetBox")

        template_name = (self._site.custom_fields or {}).get("template_name")
        if not template_name:
            raise ValueError(
                f"Site '{self.site_slug}' has no 'template_name' custom field. "
                "Was it created with bma-site-generate?"
            )

        template_path = SITE_TEMPLATES_DIR / f"{template_name}.yaml"
        if not template_path.exists():
            raise FileNotFoundError(f"Template file not found: {template_path}")

        self._template = yaml.safe_load(template_path.read_text())
        cabling_name = self._template["template"]["cabling_rules"]
        cabling_path = SITE_TEMPLATES_DIR / "cabling" / f"{cabling_name}.yaml"
        self._cabling = (
            yaml.safe_load(cabling_path.read_text()) if cabling_path.exists() else {"cables": []}
        )
        self._site_octet = self._template["network"]["default_site_octet"]

    def _build_drift_report(self) -> DriftReport:
        template_meta = self._template["template"]
        drift = DriftReport(
            site_slug=self.site_slug,
            template_name=template_meta["name"],
            template_version=template_meta["version"],
        )

        self._diff_custom_fields(drift)
        self._diff_devices(drift)
        self._diff_vlans(drift)
        self._diff_prefixes(drift)
        self._diff_cables(drift)
        self._diff_cluster(drift)

        return drift

    # ── Diff methods ───────────────────────────────────────────────────────

    def _diff_custom_fields(self, drift: DriftReport) -> None:
        cf = self._site.custom_fields or {}
        actual_version = cf.get("template_version", "")
        expected_version = self._template["template"]["version"]
        if actual_version != expected_version:
            drift.add(
                "custom_field",
                "stale_version",
                "template_version",
                expected=expected_version,
                actual=actual_version or "(not set)",
            )

    def _diff_devices(self, drift: DriftReport) -> None:
        existing: dict[str, Any] = {
            d.name: d
            for d in self.nb.dcim.devices.filter(site_id=self._site.id)
        }
        for spec in self._template["devices"]:
            for n in range(1, spec["count"] + 1):
                name = spec["name_pattern"].format(n=n)
                if name not in existing:
                    drift.add(
                        "device",
                        "missing",
                        name,
                        expected=f"model={spec['model_slug']} platform={spec['platform']}",
                    )
                    continue

                dev = existing[name]
                actual_model = dev.device_type.slug if dev.device_type else ""
                actual_platform = dev.platform.slug if dev.platform else ""

                if actual_model != spec["model_slug"]:
                    drift.add(
                        "device",
                        "wrong_model",
                        name,
                        expected=spec["model_slug"],
                        actual=actual_model,
                    )
                if actual_platform != spec["platform"]:
                    drift.add(
                        "device",
                        "wrong_platform",
                        name,
                        expected=spec["platform"],
                        actual=actual_platform,
                    )

    def _diff_vlans(self, drift: DriftReport) -> None:
        existing_vids: set[int] = {
            v.vid for v in self.nb.ipam.vlans.filter(site_id=self._site.id)
        }

        for spec in self._template["vlans"]["management"]:
            if spec["vid"] not in existing_vids:
                drift.add("vlan", "missing", str(spec["vid"]), expected=spec["name"])

        mission_count: int = self._template["missions"]["count"]
        for n in range(mission_count):
            for offset, label in [(0, "users"), (10, "apps"), (20, "data")]:
                vid = 1100 + n * 100 + offset
                if vid not in existing_vids:
                    drift.add("vlan", "missing", str(vid), expected=f"mission-{n+1:02d}-{label}")

    def _diff_prefixes(self, drift: DriftReport) -> None:
        X = self._site_octet
        existing: set[str] = {
            str(p.prefix)
            for p in self.nb.ipam.prefixes.filter(site_id=self._site.id)
        }

        def _rendered(tmpl: str) -> str:
            return tmpl.replace("{X}", str(X))

        for spec in self._template["vlans"]["management"]:
            prefix = _rendered(spec["prefix_template"])
            if prefix not in existing:
                drift.add("prefix", "missing", prefix, expected=spec["name"])

        mission_count: int = self._template["missions"]["count"]
        for n in range(mission_count):
            m = n + 1
            for prefix, label in [
                (f"10.{X}.{11 + n}.0/24", f"mission-{m:02d}-users"),
                (f"10.{X}.{111 + n * 10}.0/24", f"mission-{m:02d}-apps"),
                (f"10.{X}.{112 + n * 10}.0/24", f"mission-{m:02d}-data"),
            ]:
                if prefix not in existing:
                    drift.add("prefix", "missing", prefix, expected=label)

    def _diff_cables(self, drift: DriftReport) -> None:
        # Build set of connected (device, interface) pairs
        connected: set[tuple[str, str]] = set()
        for cable in self.nb.dcim.cables.filter(site_id=self._site.id):
            for term_list in (cable.a_terminations or [], cable.b_terminations or []):
                for term in (term_list if isinstance(term_list, list) else [term_list]):
                    obj = term.get("object") if isinstance(term, dict) else None
                    if obj:
                        dev = (obj.get("device") or {}).get("name", "")
                        iface = obj.get("name", "")
                        if dev and iface:
                            connected.add((dev, iface))

        for cable_spec in self._cabling.get("cables", []):
            a, b = cable_spec["a"], cable_spec["b"]
            if (a["device"], a["interface"]) not in connected:
                drift.add(
                    "cable",
                    "missing",
                    cable_spec.get("description", f"{a['device']}:{a['interface']}"),
                    expected=f"{a['device']}:{a['interface']} ↔ {b['device']}:{b['interface']}",
                )

    def _diff_cluster(self, drift: DriftReport) -> None:
        cluster_conf = self._template.get("cluster", {})
        if not cluster_conf:
            return

        cluster_name = cluster_conf["name_pattern"].format(site_slug=self.site_slug)
        cluster = self.nb.virtualization.clusters.get(name=cluster_name)
        if cluster is None:
            drift.add("cluster", "missing", cluster_name)

    # ── Fix methods ────────────────────────────────────────────────────────

    def _apply_fixes(self, drift: DriftReport) -> None:
        """Create/update all drifted items."""
        console.print(f"\n[bold]Fixing {len(drift.items)} drifted item(s)…[/]")

        devices: dict[str, Any] = {
            d.name: d
            for d in self.nb.dcim.devices.filter(site_id=self._site.id)
        }
        interfaces: dict[str, Any] = {}

        for item in drift.items:
            try:
                if item.category == "custom_field" and item.action == "stale_version":
                    self._fix_custom_fields()

                elif item.category == "device" and item.action == "missing":
                    dev = self._fix_missing_device(item.name)
                    if dev:
                        devices[item.name] = dev

                elif item.category == "vlan" and item.action == "missing":
                    self._fix_missing_vlan(int(item.name))

                elif item.category == "prefix" and item.action == "missing":
                    self._fix_missing_prefix(item.name)

                elif item.category == "cable" and item.action == "missing":
                    self._fix_missing_cable(item, devices, interfaces)

                elif item.category == "cluster" and item.action == "missing":
                    self._fix_missing_cluster(devices)

                console.print(f"  [green]✓[/] Fixed {item.category} '{item.name}'")
            except Exception as exc:
                console.print(f"  [red]✗[/] Failed to fix {item.category} '{item.name}': {exc}")
                logger.exception("Fix failed for %s/%s", item.category, item.name)

        # Bump template_last_synced
        self._site.custom_fields = {
            "template_last_synced": datetime.now(timezone.utc).isoformat(),
        }
        self._site.save()

    def _fix_custom_fields(self) -> None:
        self._site.custom_fields = {
            "template_version": self._template["template"]["version"],
            "template_last_synced": datetime.now(timezone.utc).isoformat(),
        }
        self._site.save()

    def _fix_missing_device(self, device_name: str) -> Any | None:
        """Locate the device spec and create the device."""
        for spec in self._template["devices"]:
            for n in range(1, spec["count"] + 1):
                if spec["name_pattern"].format(n=n) == device_name:
                    device_type = self.nb.dcim.device_types.get(slug=spec["model_slug"])
                    device_role = self.nb.dcim.device_roles.get(slug=spec["role_slug"])
                    platform = self.nb.dcim.platforms.get(slug=spec["platform"])
                    rack = next(
                        iter(self.nb.dcim.racks.filter(site_id=self._site.id)), None
                    )

                    dev = self.nb.dcim.devices.create({
                        "name": device_name,
                        "site": self._site.id,
                        **({"rack": rack.id} if rack else {}),
                        "position": spec.get("rack_unit", 1) + (n - 1) * 2,
                        "face": "front",
                        "device_type": device_type.id,
                        "role": device_role.id,
                        "platform": platform.id,
                        "status": "planned",
                    })
                    return dev
        return None

    def _fix_missing_vlan(self, vid: int) -> None:
        vlan_group = next(
            iter(self.nb.ipam.vlan_groups.filter(slug=f"{self.site_slug}-vlans")), None
        )
        # Determine name from template
        name = f"vlan-{vid}"
        for spec in self._template["vlans"]["management"]:
            if spec["vid"] == vid:
                name = spec["name"]
                break
        self.nb.ipam.vlans.create({
            "site": self._site.id,
            **({"group": vlan_group.id} if vlan_group else {}),
            "vid": vid,
            "name": name,
            "status": "active",
        })

    def _fix_missing_prefix(self, prefix_str: str) -> None:
        self.nb.ipam.prefixes.create({
            "prefix": prefix_str,
            "scope_type": "dcim.site",
            "scope_id": self._site.id,
            "status": "active",
        })

    def _fix_missing_cable(
        self,
        item: DriftItem,
        devices: dict[str, Any],
        interfaces: dict[str, Any],
    ) -> None:
        # Find the cable spec that matches
        for cable_spec in self._cabling.get("cables", []):
            if cable_spec.get("description", "") == item.name:
                a_ep, b_ep = cable_spec["a"], cable_spec["b"]
                a_iface = self._ensure_interface_cached(
                    a_ep["device"], a_ep["interface"], devices, interfaces
                )
                b_iface = self._ensure_interface_cached(
                    b_ep["device"], b_ep["interface"], devices, interfaces
                )
                if a_iface and b_iface:
                    self.nb.dcim.cables.create({
                        "a_terminations": [
                            {"object_type": "dcim.interface", "object_id": a_iface.id}
                        ],
                        "b_terminations": [
                            {"object_type": "dcim.interface", "object_id": b_iface.id}
                        ],
                        "type": cable_spec.get("type", "cat6"),
                        "color": _resolve_cable_color(cable_spec.get("color", "")),
                        "label": cable_spec.get("description", ""),
                        "status": "planned",
                    })
                break

    def _ensure_interface_cached(
        self,
        device_name: str,
        iface_name: str,
        devices: dict[str, Any],
        interfaces: dict[str, Any],
    ) -> Any | None:
        key = f"{device_name}:{iface_name}"
        if key in interfaces:
            return interfaces[key]
        device = devices.get(device_name)
        if device is None:
            return None
        iface, _ = _get_or_create(
            self.nb.dcim.interfaces,
            filter_kwargs={"device_id": device.id, "name": iface_name},
            create_data={
                "device": device.id,
                "name": iface_name,
                "type": _iface_type(device.device_type.slug, iface_name),
            },
        )
        interfaces[key] = iface
        return iface

    def _fix_missing_cluster(self, devices: dict[str, Any]) -> None:
        cluster_conf = self._template.get("cluster", {})
        if not cluster_conf:
            return

        cluster_type, _ = _get_or_create(
            self.nb.virtualization.cluster_types,
            filter_kwargs={"slug": cluster_conf["type_slug"]},
            create_data={"name": cluster_conf["type"], "slug": cluster_conf["type_slug"]},
        )
        cluster_name = cluster_conf["name_pattern"].format(site_slug=self.site_slug)
        cluster = self.nb.virtualization.clusters.create({
            "name": cluster_name,
            "type": cluster_type.id,
            "site": self._site.id,
            "status": "planned",
        })
        compute_role = cluster_conf["compute_role_slug"]
        for dev in devices.values():
            if dev.role.slug == compute_role:
                dev.cluster = cluster.id
                dev.save()

    # ── Rebuild helpers ────────────────────────────────────────────────────

    def _delete_site(self) -> None:
        """Delete all NetBox objects associated with the site."""
        console.print(f"  Deleting cables at site {self.site_slug}…")
        for cable in list(self.nb.dcim.cables.filter(site_id=self._site.id)):
            cable.delete()

        console.print(f"  Deleting devices at site {self.site_slug}…")
        for device in list(self.nb.dcim.devices.filter(site_id=self._site.id)):
            device.delete()

        console.print(f"  Deleting VLANs at site {self.site_slug}…")
        for vlan in list(self.nb.ipam.vlans.filter(site_id=self._site.id)):
            vlan.delete()

        console.print(f"  Deleting prefixes at site {self.site_slug}…")
        for prefix in list(self.nb.ipam.prefixes.filter(site_id=self._site.id)):
            prefix.delete()

        console.print(f"  Deleting clusters at site {self.site_slug}…")
        for cluster in list(self.nb.virtualization.clusters.filter(site_id=self._site.id)):
            cluster.delete()

        console.print(f"  Deleting site {self.site_slug}…")
        self._site.delete()


# ── Console report ────────────────────────────────────────────────────────────


def print_drift_report(drift: DriftReport) -> None:
    if not drift.has_drift:
        console.print(
            f"[bold green]✓ Site '{drift.site_slug}' is in sync with "
            f"template '{drift.template_name}' v{drift.template_version}[/]"
        )
        return

    table = Table(
        title=f"Drift Report — {drift.site_slug} "
              f"(template: {drift.template_name} v{drift.template_version})",
        show_lines=True,
    )
    table.add_column("Category", width=14)
    table.add_column("Action", width=14)
    table.add_column("Name")
    table.add_column("Expected", style="green")
    table.add_column("Actual", style="red")

    for item in drift.items:
        table.add_row(item.category, item.action, item.name, item.expected, item.actual)

    console.print(table)
    console.print(
        f"\n  [bold yellow]{len(drift.items)} drifted item(s) found.[/]  "
        f"Run with [bold]--mode fix[/] to reconcile."
    )


# ── CLI entry point ───────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Detect and optionally fix drift between a NetBox site and its BMA template",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--site", required=True, help="Site slug in NetBox")
    p.add_argument(
        "--mode",
        choices=["report", "fix", "rebuild"],
        default="report",
        help="report=drift only | fix=create missing | rebuild=full recreate",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Required for --mode rebuild (destructive)",
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

    if not args.netbox_url or not args.netbox_token:
        console.print(
            "[red]Error:[/] --netbox-url and --netbox-token are required "
            "(or set BMA_NETBOX_URL / BMA_NETBOX_TOKEN)"
        )
        return 1

    if args.mode == "rebuild" and not args.confirm:
        console.print(
            "[red]Error:[/] --mode rebuild is destructive. "
            "Re-run with --confirm to proceed."
        )
        return 1

    nb = pynetbox.api(args.netbox_url.rstrip("/"), token=args.netbox_token)
    regen = SiteRegenerator(nb, args.site)

    try:
        if args.mode == "report":
            drift = regen.report()
            print_drift_report(drift)
            return 1 if drift.has_drift else 0

        elif args.mode == "fix":
            drift = regen.fix()
            print_drift_report(drift)
            return 0

        elif args.mode == "rebuild":
            regen.rebuild()
            return 0

    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]Error:[/] {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
