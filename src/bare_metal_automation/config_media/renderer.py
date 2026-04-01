"""Jinja2 rendering engine for network device configuration files.

Takes a NetBox device record (enriched with interfaces, VLANs, IPs, and
config context), selects the appropriate template based on the device's
``config_template`` custom field (falling back to the role default), and
renders the result to a .cfg file under the output directory.

Variable context injected into every template:

    hostname         str          device name (from NetBox)
    domain_name      str          e.g. "dc1.example.mil"
    mgmt_ip          str          management IPv4 address
    mgmt_mask        str          dotted-decimal subnet mask
    mgmt_gateway     str          default gateway for management
    site_slug        str          NetBox site slug
    site_size        str          "small" | "medium" | "large"
    serial           str          device serial number

    ntp_servers      list[str]    NTP server IPs
    dns_servers      list[str]    DNS server IPs
    syslog_server    str
    dhcp_server      str
    ad_server        str          Active Directory / LDAP
    nps_server       str          RADIUS / NPS server IP
    ca_server        str          PKI CA server IP
    wsus_server      str
    print_server     str

    vlans            list[VlanContext]
    interfaces       list[InterfaceContext]
    mission_tenants  list[MissionTenant]

    # Ansible-Vault-encrypted secrets (referenced by name, not value)
    enable_secret    str          vault reference, e.g. "{{ vault_enable_secret }}"
    snmp_community_ro str
    tacacs_key       str
    radius_key       str
    hsrp_key         str
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    TemplateSyntaxError,
    UndefinedError,
)

logger = logging.getLogger(__name__)

# ── Context dataclasses ───────────────────────────────────────────────────


@dataclass
class VlanContext:
    """Single VLAN entry for template injection."""

    vid: int
    name: str
    description: str = ""
    is_management: bool = False
    is_mission: bool = False
    tenant_index: int = 0  # which mission tenant this belongs to (0 = none)


@dataclass
class InterfaceContext:
    """Single interface entry for template injection."""

    name: str
    description: str = ""
    mode: str = "access"       # "access" | "trunk" | "routed" | "lag" | "unused"
    access_vlan: int = 1
    trunk_vlans: str = "all"   # "all" or comma-separated VLAN list
    native_vlan: int = 1
    ip_address: str = ""
    ip_mask: str = ""
    shutdown: bool = False
    portfast: bool = False
    dot1x: bool = False
    port_security: bool = False
    lag_group: int = 0         # port-channel number (0 = not a LAG member)
    lag_mode: str = "active"   # LACP mode


@dataclass
class MissionTenant:
    """Per-mission tenant VLAN block for multi-tenancy templates."""

    index: int           # tenant number (1, 2, 3, …)
    name: str            # e.g. "MISSION_1"
    user_vlan: int       # N00
    apps_vlan: int       # N10
    data_vlan: int       # N20
    user_subnet: str     # e.g. "10.1.10.0"
    user_mask: str
    user_gateway: str
    apps_subnet: str
    apps_mask: str
    apps_gateway: str
    data_subnet: str
    data_mask: str
    data_gateway: str


@dataclass
class RenderContext:
    """Complete variable context passed to every template."""

    # ── Identity
    hostname: str
    serial: str
    domain_name: str = ""
    site_slug: str = ""
    site_size: str = "small"  # "small" | "medium" | "large"

    # ── Management network
    mgmt_ip: str = ""
    mgmt_mask: str = "255.255.255.0"
    mgmt_gateway: str = ""

    # ── Infrastructure services
    ntp_servers: list[str] = field(default_factory=list)
    dns_servers: list[str] = field(default_factory=list)
    syslog_server: str = ""
    dhcp_server: str = ""
    ad_server: str = ""
    nps_server: str = ""
    ca_server: str = ""
    wsus_server: str = ""
    print_server: str = ""

    # ── Network data
    vlans: list[VlanContext] = field(default_factory=list)
    interfaces: list[InterfaceContext] = field(default_factory=list)
    mission_tenants: list[MissionTenant] = field(default_factory=list)

    # ── Ansible Vault secret references (never the actual values)
    enable_secret: str = "{{ vault_enable_secret }}"
    snmp_community_ro: str = "{{ vault_snmp_community_ro }}"
    tacacs_key: str = "{{ vault_tacacs_key }}"
    radius_key: str = "{{ vault_radius_key }}"
    hsrp_key: str = "{{ vault_hsrp_key }}"


# ── Renderer ──────────────────────────────────────────────────────────────


class ConfigRenderer:
    """Render device configuration files from Jinja2 templates + NetBox data.

    Args:
        templates_dir: Root directory containing Jinja2 templates.
        output_dir:    Directory where rendered .cfg files are written.
        strict:        If True (default), raise on undefined template vars.
    """

    def __init__(
        self,
        templates_dir: Path,
        output_dir: Path,
        strict: bool = True,
    ) -> None:
        self.templates_dir = templates_dir
        self.output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            undefined=StrictUndefined if strict else UndefinedError,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    # ── Public API ────────────────────────────────────────────────────────

    def render_device(
        self,
        template_path: str,
        context: RenderContext,
    ) -> Path:
        """Render a single device config and write it to output_dir.

        Args:
            template_path: Relative path inside templates_dir, e.g.
                           "switches/core.j2".
            context:       Populated RenderContext for this device.

        Returns:
            Path to the written .cfg file.

        Raises:
            TemplateNotFound:   template_path does not exist.
            TemplateSyntaxError: template has a Jinja2 syntax error.
            UndefinedError:     template references a variable not in context.
            OSError:            output directory is not writable.
        """
        try:
            template = self._env.get_template(template_path)
        except TemplateNotFound:
            raise TemplateNotFound(
                f"Template '{template_path}' not found in {self.templates_dir}",
            )
        except TemplateSyntaxError as e:
            raise TemplateSyntaxError(
                message=f"Syntax error in '{template_path}': {e.message}",
                lineno=e.lineno,
                name=e.name,
                filename=e.filename,
            ) from e

        ctx_dict = self._context_to_dict(context)

        try:
            rendered = template.render(**ctx_dict)
        except UndefinedError as e:
            raise UndefinedError(
                f"Template '{template_path}' references undefined variable: {e.message}",
            ) from e

        # Sanitise hostname for use as filename
        safe_name = re.sub(r"[^\w\-.]", "_", context.hostname)
        out_path = self.output_dir / f"{safe_name}.cfg"
        out_path.write_text(rendered, encoding="utf-8")

        logger.info(
            "Rendered '%s' → %s (%d bytes)",
            template_path,
            out_path,
            len(rendered),
        )
        return out_path

    def render_all(
        self,
        devices: list[tuple[str, RenderContext]],
    ) -> dict[str, Path]:
        """Render configs for a list of (template_path, context) pairs.

        Returns:
            Dict mapping hostname → output Path for successfully rendered devices.

        Raises:
            RuntimeError: if any device fails to render (with aggregated errors).
        """
        results: dict[str, Path] = {}
        errors: list[str] = []

        for template_path, ctx in devices:
            try:
                out_path = self.render_device(template_path, ctx)
                results[ctx.hostname] = out_path
            except (TemplateNotFound, TemplateSyntaxError, UndefinedError, OSError) as e:
                msg = f"{ctx.hostname} ({template_path}): {e}"
                logger.error("Render failed — %s", msg)
                errors.append(msg)

        if errors:
            raise RuntimeError(
                f"Config rendering failed for {len(errors)} device(s):\n"
                + "\n".join(f"  • {e}" for e in errors),
            )

        return results

    # ── Context builder (from raw NetBox data) ────────────────────────────

    @staticmethod
    def build_context(
        device: Any,
        config_context: dict[str, Any],
        ip_addresses: list[dict[str, Any]],
        interfaces: list[dict[str, Any]],
        vlans: list[dict[str, Any]],
    ) -> tuple[str, RenderContext]:
        """Build a RenderContext from raw NetBox records.

        Args:
            device:         pynetbox Device record.
            config_context: Merged config context dict from NetBox.
            ip_addresses:   List of IP address dicts.
            interfaces:     List of interface dicts (with mode, VLANs, etc.)
            vlans:          List of VLAN dicts tagged to this deployment node.

        Returns:
            Tuple of (template_path, RenderContext).
        """
        # --- Template path (custom field → fallback default) ---
        custom_fields = dict(device.custom_fields or {})
        template_path: str = custom_fields.get(
            "config_template",
            _default_template_for_role(
                str(device.device_role.slug) if device.device_role else "",
            ),
        )

        # --- Management IP ---
        mgmt_ip = ""
        mgmt_mask = ""
        if ip_addresses:
            addr_str = ip_addresses[0]["address"]
            if "/" in addr_str:
                ip_part, plen = addr_str.split("/")
                mgmt_ip = ip_part
                mgmt_mask = _prefix_to_netmask(int(plen))

        # --- Services from config context ---
        svc = config_context.get("services", {})

        # --- VLANs ---
        vlan_contexts = _build_vlan_contexts(vlans, config_context)

        # --- Mission tenants ---
        mission_tenants = _build_mission_tenants(config_context)

        # --- Interfaces ---
        iface_contexts = _build_interface_contexts(interfaces)

        ctx = RenderContext(
            hostname=device.name,
            serial=device.serial or "",
            domain_name=config_context.get("domain_name", ""),
            site_slug=str(device.site.slug) if device.site else "",
            site_size=config_context.get("site_size", "small"),
            mgmt_ip=mgmt_ip,
            mgmt_mask=mgmt_mask,
            mgmt_gateway=config_context.get("mgmt_gateway", ""),
            ntp_servers=svc.get("ntp_servers", config_context.get("ntp_servers", [])),
            dns_servers=svc.get("dns_servers", config_context.get("dns_servers", [])),
            syslog_server=svc.get("syslog_server", config_context.get("syslog_server", "")),
            dhcp_server=svc.get("dhcp_server", config_context.get("dhcp_server", "")),
            ad_server=svc.get("ad_server", config_context.get("ad_server", "")),
            nps_server=svc.get("nps_server", config_context.get("nps_server", "")),
            ca_server=svc.get("ca_server", config_context.get("ca_server", "")),
            wsus_server=svc.get("wsus_server", config_context.get("wsus_server", "")),
            print_server=svc.get("print_server", config_context.get("print_server", "")),
            vlans=vlan_contexts,
            interfaces=iface_contexts,
            mission_tenants=mission_tenants,
        )

        return template_path, ctx

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _context_to_dict(ctx: RenderContext) -> dict[str, Any]:
        """Flatten RenderContext to a plain dict for Jinja2."""
        return {
            "hostname": ctx.hostname,
            "serial": ctx.serial,
            "domain_name": ctx.domain_name,
            "site_slug": ctx.site_slug,
            "site_size": ctx.site_size,
            "mgmt_ip": ctx.mgmt_ip,
            "mgmt_mask": ctx.mgmt_mask,
            "mgmt_gateway": ctx.mgmt_gateway,
            "ntp_servers": ctx.ntp_servers,
            "dns_servers": ctx.dns_servers,
            "syslog_server": ctx.syslog_server,
            "dhcp_server": ctx.dhcp_server,
            "ad_server": ctx.ad_server,
            "nps_server": ctx.nps_server,
            "ca_server": ctx.ca_server,
            "wsus_server": ctx.wsus_server,
            "print_server": ctx.print_server,
            "vlans": ctx.vlans,
            "interfaces": ctx.interfaces,
            "mission_tenants": ctx.mission_tenants,
            "enable_secret": ctx.enable_secret,
            "snmp_community_ro": ctx.snmp_community_ro,
            "tacacs_key": ctx.tacacs_key,
            "radius_key": ctx.radius_key,
            "hsrp_key": ctx.hsrp_key,
        }


# ── Module-level helpers ──────────────────────────────────────────────────

# Management VLAN IDs (never assigned to mission tenants)
MGMT_VLANS: frozenset[int] = frozenset(
    [100, 200, 400, 500, 600, 700, 800, 900, 950],
)

# Mission tenant VLAN blocks start at 1100, each block is 100 wide.
# Within a block: N00=users, N10=apps, N20=data
MISSION_VLAN_BASE = 1100
MISSION_BLOCK_SIZE = 100


def _build_vlan_contexts(
    vlans: list[dict[str, Any]],
    config_context: dict[str, Any],
) -> list[VlanContext]:
    """Build VlanContext list from NetBox VLANs + config context."""
    seen: set[int] = set()
    result: list[VlanContext] = []

    # Always include hard-coded management VLANs first (from MGMT_VLANS)
    mgmt_names: dict[int, str] = {
        100: "MGMT",
        200: "VOICE",
        400: "SERVERS",
        500: "PRINTERS",
        600: "ILO",
        700: "AD",
        800: "WIRELESS",
        900: "STORAGE",
        950: "QUARANTINE",
    }
    for vid, name in sorted(mgmt_names.items()):
        result.append(VlanContext(vid=vid, name=name, is_management=True))
        seen.add(vid)

    # Add mission tenant VLANs from config context
    mission_tenants = config_context.get("mission_tenants", [])
    for i, tenant in enumerate(mission_tenants, start=1):
        base = MISSION_VLAN_BASE + (i - 1) * MISSION_BLOCK_SIZE
        for offset, suffix in [(0, "USERS"), (10, "APPS"), (20, "DATA")]:
            vid = base + offset
            if vid not in seen:
                result.append(VlanContext(
                    vid=vid,
                    name=f"MISSION{i}_{suffix}",
                    is_mission=True,
                    tenant_index=i,
                ))
                seen.add(vid)

    # Merge any extra VLANs from NetBox that aren't already present
    for v in vlans:
        vid = v["vid"]
        if vid not in seen:
            result.append(VlanContext(
                vid=vid,
                name=v.get("name", f"VLAN{vid}"),
                description=v.get("description", ""),
            ))
            seen.add(vid)

    return sorted(result, key=lambda v: v.vid)


def _build_mission_tenants(
    config_context: dict[str, Any],
) -> list[MissionTenant]:
    """Build MissionTenant list from config context."""
    raw = config_context.get("mission_tenants", [])
    tenants: list[MissionTenant] = []

    for i, t in enumerate(raw, start=1):
        base_vlan = MISSION_VLAN_BASE + (i - 1) * MISSION_BLOCK_SIZE
        tenants.append(MissionTenant(
            index=i,
            name=t.get("name", f"MISSION_{i}"),
            user_vlan=t.get("user_vlan", base_vlan),
            apps_vlan=t.get("apps_vlan", base_vlan + 10),
            data_vlan=t.get("data_vlan", base_vlan + 20),
            user_subnet=t.get("user_subnet", ""),
            user_mask=t.get("user_mask", "255.255.255.0"),
            user_gateway=t.get("user_gateway", ""),
            apps_subnet=t.get("apps_subnet", ""),
            apps_mask=t.get("apps_mask", "255.255.255.0"),
            apps_gateway=t.get("apps_gateway", ""),
            data_subnet=t.get("data_subnet", ""),
            data_mask=t.get("data_mask", "255.255.255.0"),
            data_gateway=t.get("data_gateway", ""),
        ))

    return tenants


def _build_interface_contexts(
    interfaces: list[dict[str, Any]],
) -> list[InterfaceContext]:
    """Map NetBox interface records to InterfaceContext list."""
    result: list[InterfaceContext] = []
    for iface in interfaces:
        mode_raw = (iface.get("mode") or {})
        mode_val = mode_raw.get("value", "access") if isinstance(mode_raw, dict) else str(mode_raw)
        mode_map = {
            "access": "access",
            "tagged": "trunk",
            "tagged-all": "trunk",
        }
        mode = mode_map.get(mode_val, "access")

        # Tagged VLANs list → comma-separated string
        tagged = iface.get("tagged_vlans", [])
        trunk_vlans = (
            ",".join(str(v["vid"]) for v in tagged)
            if tagged
            else "all"
        )

        untagged = iface.get("untagged_vlan") or {}
        access_vlan = untagged.get("vid", 1) if isinstance(untagged, dict) else 1

        result.append(InterfaceContext(
            name=iface.get("name", ""),
            description=iface.get("description", ""),
            mode=mode,
            access_vlan=access_vlan,
            trunk_vlans=trunk_vlans,
            shutdown=not iface.get("enabled", True),
            portfast=bool(iface.get("custom_fields", {}).get("portfast", False)),
            dot1x=bool(iface.get("custom_fields", {}).get("dot1x", False)),
            lag_group=iface.get("lag", {}).get("id", 0) if isinstance(iface.get("lag"), dict) else 0,
        ))

    return result


def _default_template_for_role(role_slug: str) -> str:
    """Return the default template path for a given NetBox role slug."""
    mapping: dict[str, str] = {
        "core-switch": "switches/core.j2",
        "access-switch": "switches/access.j2",
        "distribution-switch": "switches/distribution.j2",
        "border-router": "firewalls/perimeter-router.j2",
        "perimeter-firewall": "firewalls/perimeter-router.j2",
    }
    return mapping.get(role_slug, f"{role_slug}.j2")


def _prefix_to_netmask(prefix_len: int) -> str:
    """Convert CIDR prefix length to dotted-decimal netmask."""
    mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    return ".".join(str((mask >> (8 * i)) & 0xFF) for i in range(3, -1, -1))
