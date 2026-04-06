"""Site generation script — create a new site in NetBox from a BMA template.

Idempotent: exits cleanly if the site already exists (use site_regenerate.py
to update an existing site).

Usage:
    bma-site-generate \\
        --template medium-site \\
        --site-name "Alpha Site" \\
        --site-slug alpha-site \\
        --octet 200 \\
        --netbox-url http://netbox:8080

Environment variables:
    BMA_NETBOX_URL    NetBox base URL
    BMA_NETBOX_TOKEN  NetBox API token
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pynetbox
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

logger = logging.getLogger(__name__)
console = Console()

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_TEMPLATES_DIR = REPO_ROOT / "site_templates"

# ── Cable colour name → hex mapping (NetBox requires 6-char hex, no '#') ────────
_CABLE_COLOR_MAP: dict[str, str] = {
    "red": "f44336",
    "blue": "2196f3",
    "green": "4caf50",
    "yellow": "ffeb3b",
    "orange": "ff9800",
    "gray": "9e9e9e",
    "grey": "9e9e9e",
    "black": "111111",
    "white": "fafafa",
    "purple": "9c27b0",
    "pink": "e91e63",
    "brown": "795548",
    "cyan": "00bcd4",
}


def _resolve_cable_color(color: str) -> str:
    """Convert a colour name to a 6-char hex code, or pass through if already hex."""
    if not color:
        return ""
    return _CABLE_COLOR_MAP.get(color.lower(), color)


# ── NetBox interface type map (model_slug → default interface type) ────────────
# Used when creating interfaces that don't already exist.
_IFACE_TYPE_MAP: dict[str, str] = {
    "c9500-48y4c": "10gbase-x-sfpp",
    "c9300-48p": "1000base-t",
    "c9200-48p": "1000base-t",
    "fp1150": "1000base-t",
    "dl360-gen10-plus": "1000base-t",
    "m300": "1000base-t",
}

_TE_PREFIX = ("Te", "TenGigabitEthernet")
_GI_PREFIX = ("Gi", "GigabitEthernet")
_HU_PREFIX = ("Hu", "HundredGigE")


def _iface_type(device_model_slug: str, iface_name: str) -> str:
    """Guess a NetBox interface type from device model + interface name."""
    if iface_name.upper().startswith(("TE", "TENGIG")):
        return "10gbase-x-sfpp"
    if iface_name.upper().startswith(("HU", "HUNDREDGIGE")):
        return "100gbase-x-qsfp28"
    if iface_name.upper().startswith("ILO"):
        return "1000base-t"
    return _IFACE_TYPE_MAP.get(device_model_slug, "1000base-t")


# ── YAML helpers ──────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as fh:
        return yaml.safe_load(fh)


# ── NetBox get-or-create helpers ──────────────────────────────────────────────


def _get_or_create(
    endpoint: Any,
    filter_kwargs: dict[str, Any],
    create_data: dict[str, Any],
    label: str = "",
) -> tuple[Any, bool]:
    """Fetch an existing NetBox object or create it. Returns (obj, created)."""
    obj = endpoint.get(**filter_kwargs)
    if obj is not None:
        return obj, False
    obj = endpoint.create(create_data)
    if label:
        logger.debug("Created %s: %s", label, obj)
    return obj, True


# ── Main generator class ──────────────────────────────────────────────────────


class SiteGenerator:
    """Create a complete site in NetBox from a BMA site template YAML.

    Steps (in order):
      1.  Ensure custom field definitions exist on Site objects
      2.  Ensure manufacturers exist
      3.  Ensure device types exist
      4.  Ensure device roles exist
      5.  Ensure platforms exist
      6.  Create site (with template custom fields)
      7.  Create rack
      8.  Create VLAN group + management VLANs
      9.  Create mission tenant VLANs
      10. Create IPAM prefixes
      11. Create devices
      12. Create interfaces (from cabling YAML, only ports that appear in cables)
      13. Create cables
      14. Create vSphere cluster + register compute nodes
    """

    def __init__(
        self,
        nb: pynetbox.api,
        template: dict[str, Any],
        cabling: dict[str, Any],
        site_name: str,
        site_slug: str,
        site_octet: int,
    ) -> None:
        self.nb = nb
        self.template = template
        self.cabling = cabling
        self.site_name = site_name
        self.site_slug = site_slug
        self.site_octet = site_octet
        self.template_meta = template["template"]

        # Populated during run, used by later steps
        self._site: Any = None
        self._rack: Any = None
        self._devices: dict[str, Any] = {}     # name → pynetbox Device
        self._interfaces: dict[str, Any] = {}  # "device:iface" → pynetbox Interface

        self._created: dict[str, int] = {}     # label → count of created objects

    # ── Public entry point ─────────────────────────────────────────────────

    def run(self, progress: Progress | None = None) -> None:
        """Execute the full site generation pipeline."""

        def _step(msg: str) -> None:
            if progress:
                progress.print(f"  [cyan]{msg}[/]")
            else:
                console.print(f"  [cyan]{msg}[/]")

        _step("Ensuring custom field definitions…")
        self._ensure_custom_fields()

        _step("Ensuring manufacturers…")
        self._ensure_manufacturers()

        _step("Ensuring device types…")
        self._ensure_device_types()

        _step("Ensuring device roles…")
        self._ensure_device_roles()

        _step("Ensuring platforms…")
        self._ensure_platforms()

        _step(f"Creating site '{self.site_name}'…")
        self._create_site()

        _step("Creating rack…")
        self._create_rack()

        _step("Creating VLANs…")
        self._create_vlans()

        _step("Creating IPAM prefixes…")
        self._create_prefixes()

        _step("Creating devices…")
        self._create_devices()

        _step("Creating interfaces…")
        self._create_interfaces()

        _step("Creating cables…")
        self._create_cables()

        _step("Creating vSphere cluster…")
        self._create_cluster()

        # Summary
        console.print("\n[bold green]✓ Site generation complete[/]")
        for label, count in self._created.items():
            if count:
                console.print(f"  Created {count:3d} {label}")

    # ── Step implementations ───────────────────────────────────────────────

    def _bump(self, label: str, n: int = 1) -> None:
        self._created[label] = self._created.get(label, 0) + n

    def _ensure_custom_fields(self) -> None:
        """Ensure template_name, template_version, template_last_synced exist on Site."""
        fields = [
            {
                "name": "template_name",
                "label": "Template Name",
                "type": "text",
                "object_types": ["dcim.site"],
                "required": False,
                "description": "BMA site template used to generate this site",
            },
            {
                "name": "template_version",
                "label": "Template Version",
                "type": "text",
                "object_types": ["dcim.site"],
                "required": False,
                "description": "Semver version of the template at generation time",
            },
            {
                "name": "template_last_synced",
                "label": "Template Last Synced",
                "type": "text",
                "object_types": ["dcim.site"],
                "required": False,
                "description": "ISO-8601 timestamp of last template sync",
            },
            {
                "name": "site_octet",
                "label": "Site Octet",
                "type": "integer",
                "object_types": ["dcim.site"],
                "required": False,
                "description": "IP addressing octet used during site generation (10.{octet}.x.x)",
            },
        ]
        for field_data in fields:
            _, created = _get_or_create(
                self.nb.extras.custom_fields,
                filter_kwargs={"name": field_data["name"]},
                create_data=field_data,
                label="custom_field",
            )
            if created:
                self._bump("custom_fields")

    def _ensure_manufacturers(self) -> None:
        seen: set[str] = set()
        for spec in self.template["devices"]:
            slug = spec["manufacturer_slug"]
            if slug in seen:
                continue
            seen.add(slug)
            _, created = _get_or_create(
                self.nb.dcim.manufacturers,
                filter_kwargs={"slug": slug},
                create_data={"name": spec["manufacturer"], "slug": slug},
                label="manufacturer",
            )
            if created:
                self._bump("manufacturers")

    def _ensure_device_types(self) -> None:
        seen: set[str] = set()
        for spec in self.template["devices"]:
            model_slug = spec["model_slug"]
            if model_slug in seen:
                continue
            seen.add(model_slug)
            manufacturer = self.nb.dcim.manufacturers.get(slug=spec["manufacturer_slug"])
            _, created = _get_or_create(
                self.nb.dcim.device_types,
                filter_kwargs={"slug": model_slug},
                create_data={
                    "manufacturer": manufacturer.id,
                    "model": spec["model"],
                    "slug": model_slug,
                    "u_height": 2,
                },
                label="device_type",
            )
            if created:
                self._bump("device_types")

    def _ensure_device_roles(self) -> None:
        role_colors = {
            "core-switch": "aa1409",
            "access-switch": "f44336",
            "distribution-switch": "e91e63",
            "perimeter-firewall": "9c27b0",
            "compute-node": "2196f3",
            "backup-server": "009688",
            "ntp-server": "795548",
        }
        seen: set[str] = set()
        for spec in self.template["devices"]:
            slug = spec["role_slug"]
            if slug in seen:
                continue
            seen.add(slug)
            _, created = _get_or_create(
                self.nb.dcim.device_roles,
                filter_kwargs={"slug": slug},
                create_data={
                    "name": spec["role"],
                    "slug": slug,
                    "color": role_colors.get(slug, "607d8b"),
                    "vm_role": slug in ("compute-node", "backup-server"),
                },
                label="device_role",
            )
            if created:
                self._bump("device_roles")

    def _ensure_platforms(self) -> None:
        platform_labels = {
            "cisco_iosxe": "Cisco IOS-XE",
            "cisco_ftd": "Cisco FTD",
            "hpe_ilo": "HPE iLO",
            "meinberg_ntp": "Meinberg LANTIME",
        }
        seen: set[str] = set()
        for spec in self.template["devices"]:
            slug = spec["platform"]
            if slug in seen:
                continue
            seen.add(slug)
            _, created = _get_or_create(
                self.nb.dcim.platforms,
                filter_kwargs={"slug": slug},
                create_data={
                    "name": platform_labels.get(slug, slug),
                    "slug": slug,
                },
                label="platform",
            )
            if created:
                self._bump("platforms")

    def _create_site(self) -> None:
        self._site = self.nb.dcim.sites.create({
            "name": self.site_name,
            "slug": self.site_slug,
            "status": "active",
            "custom_fields": {
                "template_name": self.template_meta["name"],
                "template_version": self.template_meta["version"],
                "template_last_synced": datetime.now(timezone.utc).isoformat(),
                "site_octet": self.site_octet,
            },
        })
        self._bump("sites")
        logger.info("Created site '%s' (id=%d)", self.site_name, self._site.id)

    def _create_rack(self) -> None:
        self._rack, created = _get_or_create(
            self.nb.dcim.racks,
            filter_kwargs={"site_id": self._site.id, "name": f"{self.site_slug}-rack-01"},
            create_data={
                "site": self._site.id,
                "name": f"{self.site_slug}-rack-01",
                "status": "active",
                "u_height": 42,
            },
            label="rack",
        )
        if created:
            self._bump("racks")

    def _create_vlans(self) -> None:
        # VLAN group for the site
        vlan_group, created = _get_or_create(
            self.nb.ipam.vlan_groups,
            filter_kwargs={"slug": f"{self.site_slug}-vlans"},
            create_data={
                "name": f"{self.site_slug.upper()} VLANs",
                "slug": f"{self.site_slug}-vlans",
                "scope_type": "dcim.site",
                "scope_id": self._site.id,
            },
            label="vlan_group",
        )
        if created:
            self._bump("vlan_groups")

        def _make_vlan(vid: int, name: str, role_name: str, description: str) -> None:
            role = self.nb.ipam.roles.get(slug=role_name) if role_name else None
            _get_or_create(
                self.nb.ipam.vlans,
                filter_kwargs={"site_id": self._site.id, "vid": vid},
                create_data={
                    "site": self._site.id,
                    "group": vlan_group.id,
                    "vid": vid,
                    "name": name,
                    "status": "active",
                    **({"role": role.id} if role else {}),
                    "description": description,
                },
                label="vlan",
            )
            self._bump("vlans")

        # Management VLANs
        for spec in self.template["vlans"]["management"]:
            _make_vlan(
                spec["vid"],
                spec["name"],
                spec.get("role", ""),
                spec.get("description", ""),
            )

        # Mission VLANs
        mission_count: int = self.template["missions"]["count"]
        for n in range(mission_count):
            m = n + 1  # 1-indexed mission label
            for offset, label_suffix in [(0, "users"), (10, "apps"), (20, "data")]:
                vid = 1100 + n * 100 + offset
                _make_vlan(vid, f"mission-{m:02d}-{label_suffix}", "network", f"Mission {m} {label_suffix}")

    def _create_prefixes(self) -> None:
        X = self.site_octet

        def _rendered(tmpl: str) -> str:
            return tmpl.replace("{X}", str(X))

        def _make_prefix(prefix_str: str, description: str, vlan_vid: int | None = None) -> None:
            vlan = (
                self.nb.ipam.vlans.get(site_id=self._site.id, vid=vlan_vid)
                if vlan_vid is not None
                else None
            )
            _get_or_create(
                self.nb.ipam.prefixes,
                filter_kwargs={"prefix": prefix_str, "vlan_id": vlan.id if vlan else None},
                create_data={
                    "prefix": prefix_str,
                    "scope_type": "dcim.site",
                    "scope_id": self._site.id,
                    "status": "active",
                    "description": description,
                    **({"vlan": vlan.id} if vlan else {}),
                },
                label="prefix",
            )
            self._bump("prefixes")

        # Management VLANs
        for spec in self.template["vlans"]["management"]:
            prefix = _rendered(spec["prefix_template"])
            _make_prefix(prefix, spec.get("description", spec["name"]), spec["vid"])

        # Mission prefixes
        mission_count: int = self.template["missions"]["count"]
        for n in range(mission_count):
            m = n + 1
            _make_prefix(
                f"10.{X}.{11 + n}.0/24",
                f"Mission {m} users",
                1100 + n * 100,
            )
            _make_prefix(
                f"10.{X}.{111 + n * 10}.0/24",
                f"Mission {m} apps",
                1110 + n * 100,
            )
            _make_prefix(
                f"10.{X}.{112 + n * 10}.0/24",
                f"Mission {m} data",
                1120 + n * 100,
            )

    def _create_devices(self) -> None:
        for spec in self.template["devices"]:
            device_type = self.nb.dcim.device_types.get(slug=spec["model_slug"])
            device_role = self.nb.dcim.device_roles.get(slug=spec["role_slug"])
            platform = self.nb.dcim.platforms.get(slug=spec["platform"])

            for n in range(1, spec["count"] + 1):
                name = spec["name_pattern"].format(n=n)
                dev, created = _get_or_create(
                    self.nb.dcim.devices,
                    filter_kwargs={"name": name, "site_id": self._site.id},
                    create_data={
                        "name": name,
                        "site": self._site.id,
                        "rack": self._rack.id,
                        "position": spec.get("rack_unit", 1) + (n - 1) * 2,
                        "face": "front",
                        "device_type": device_type.id,
                        "role": device_role.id,
                        "platform": platform.id,
                        "status": "planned",
                    },
                    label="device",
                )
                if created:
                    self._bump("devices")
                self._devices[name] = dev

    def _ensure_interface(self, device_name: str, iface_name: str) -> Any:
        """Get or create an interface on a device. Returns the interface object."""
        key = f"{device_name}:{iface_name}"
        if key in self._interfaces:
            return self._interfaces[key]

        device = self._devices.get(device_name)
        if device is None:
            raise ValueError(f"Device '{device_name}' not found — was it created?")

        iface, created = _get_or_create(
            self.nb.dcim.interfaces,
            filter_kwargs={"device_id": device.id, "name": iface_name},
            create_data={
                "device": device.id,
                "name": iface_name,
                "type": _iface_type(device.device_type.slug, iface_name),
            },
            label="interface",
        )
        if created:
            self._bump("interfaces")
        self._interfaces[key] = iface
        return iface

    def _create_interfaces(self) -> None:
        """Pre-create all interfaces referenced in the cabling rules."""
        for cable_spec in self.cabling.get("cables", []):
            for endpoint_key in ("a", "b"):
                ep = cable_spec[endpoint_key]
                try:
                    self._ensure_interface(ep["device"], ep["interface"])
                except ValueError as exc:
                    logger.warning("Skipping interface pre-create: %s", exc)

    def _create_cables(self) -> None:
        for cable_spec in self.cabling.get("cables", []):
            a_ep = cable_spec["a"]
            b_ep = cable_spec["b"]

            try:
                a_iface = self._ensure_interface(a_ep["device"], a_ep["interface"])
                b_iface = self._ensure_interface(b_ep["device"], b_ep["interface"])
            except ValueError as exc:
                logger.warning("Skipping cable '%s': %s", cable_spec.get("description", ""), exc)
                continue

            # Check if cable already exists between these two interfaces
            existing = self.nb.dcim.cables.filter(
                termination_a_type="dcim.interface",
                termination_a_id=a_iface.id,
                termination_b_type="dcim.interface",
                termination_b_id=b_iface.id,
            )
            if list(existing):
                continue

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
            self._bump("cables")

    def _create_cluster(self) -> None:
        cluster_conf = self.template.get("cluster", {})
        if not cluster_conf:
            return

        # Ensure cluster type
        cluster_type, created = _get_or_create(
            self.nb.virtualization.cluster_types,
            filter_kwargs={"slug": cluster_conf["type_slug"]},
            create_data={
                "name": cluster_conf["type"],
                "slug": cluster_conf["type_slug"],
            },
            label="cluster_type",
        )
        if created:
            self._bump("cluster_types")

        # Create cluster
        cluster_name = cluster_conf["name_pattern"].format(site_slug=self.site_slug)
        cluster, created = _get_or_create(
            self.nb.virtualization.clusters,
            filter_kwargs={"name": cluster_name},
            create_data={
                "name": cluster_name,
                "type": cluster_type.id,
                "site": self._site.id,
                "status": "planned",
            },
            label="cluster",
        )
        if created:
            self._bump("clusters")

        # Assign compute nodes to cluster
        compute_role_slug: str = cluster_conf["compute_role_slug"]
        for device_name, device in self._devices.items():
            if device.role.slug == compute_role_slug:
                if device.cluster is None or device.cluster.id != cluster.id:
                    device.cluster = cluster.id
                    device.save()
                    self._bump("cluster_members")


# ── CLI entry point ───────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate a new BMA site in NetBox from a template",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--template",
        required=True,
        help="Template name (e.g. small-site, medium-site, large-site)",
    )
    p.add_argument("--site-name", required=True, help="Human-readable site name")
    p.add_argument("--site-slug", required=True, help="NetBox site slug (lowercase, hyphenated)")
    p.add_argument(
        "--octet",
        type=int,
        default=None,
        help="IP addressing site octet (overrides template default_site_octet)",
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

    # Load template
    template_path = SITE_TEMPLATES_DIR / f"{args.template}.yaml"
    if not template_path.exists():
        console.print(f"[red]Error:[/] Template not found: {template_path}")
        return 1

    template = yaml.safe_load(template_path.read_text())
    cabling_name = template["template"]["cabling_rules"]
    cabling_path = SITE_TEMPLATES_DIR / "cabling" / f"{cabling_name}.yaml"
    cabling: dict[str, Any] = (
        yaml.safe_load(cabling_path.read_text()) if cabling_path.exists() else {"cables": []}
    )

    site_octet = args.octet or template["network"]["default_site_octet"]

    # Connect to NetBox
    nb = pynetbox.api(args.netbox_url.rstrip("/"), token=args.netbox_token)

    # Idempotency guard
    existing = nb.dcim.sites.get(slug=args.site_slug)
    if existing is not None:
        console.print(
            f"[yellow]Site '{args.site_slug}' already exists in NetBox (id={existing.id}).[/]\n"
            f"To update drift, run: [bold]bma-site-regenerate --site {args.site_slug}[/]"
        )
        return 0

    console.print(
        f"\n[bold]Generating site:[/] {args.site_name} ([dim]{args.site_slug}[/])\n"
        f"  Template : [cyan]{args.template}[/] v{template['template']['version']}\n"
        f"  IP octet : {site_octet} (10.{site_octet}.x.x)\n"
        f"  NetBox   : {args.netbox_url}\n"
    )

    generator = SiteGenerator(
        nb=nb,
        template=template,
        cabling=cabling,
        site_name=args.site_name,
        site_slug=args.site_slug,
        site_octet=site_octet,
    )

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True) as progress:
        task = progress.add_task("Generating…", total=None)
        generator.run(progress=None)
        progress.update(task, completed=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
