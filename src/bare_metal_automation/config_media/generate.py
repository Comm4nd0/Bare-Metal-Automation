"""Config & Media generation CLI — Pillar 2 entry point.

Orchestrates the full config generation pipeline:

    1. Connect to NetBox and query all devices for the target node tag
    2. Render Jinja2 configs for every network device
    3. Export BMA inventory.yaml from NetBox data
    4. Resolve firmware and ISO paths from firmware_catalogue.yaml
    5. Collect (copy + verify) media files into the bundle staging area
    6. Generate Ansible inventory
    7. Write manifest.yaml and checksums.sha256
    8. Validate bundle completeness
    9. Optionally archive to .tar.gz

Usage:
    bma-generate --tag D001 --netbox-url https://netbox.example.com \\
                 --netbox-token <TOKEN> \\
                 --templates-dir configs/templates \\
                 --catalogue configs/firmware_catalogue.yaml \\
                 --output-dir /tmp/bundles

Environment variables (override CLI flags):
    BMA_NETBOX_URL    NetBox base URL
    BMA_NETBOX_TOKEN  NetBox API token
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.logging import RichHandler

from bare_metal_automation import __version__
from bare_metal_automation.config_media.bundle_packager import BundlePackager
from bare_metal_automation.config_media.firmware_catalogue import (
    CatalogueError,
    FirmwareCatalogue,
)
from bare_metal_automation.config_media.inventory_export import InventoryExporter
from bare_metal_automation.config_media.media_collector import MediaCollector
from bare_metal_automation.config_media.renderer import ConfigRenderer

console = Console()

# ── Logging setup ─────────────────────────────────────────────────────────


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[
            RichHandler(
                console=console,
                show_path=verbose,
                rich_tracebacks=True,
            ),
        ],
    )


# ── CLI definition ────────────────────────────────────────────────────────


@click.command("bma-generate")
@click.version_option(__version__, prog_name="bma-generate")
@click.option(
    "--tag",
    required=True,
    metavar="TAG",
    help="NetBox node tag to generate configs for (e.g. D001).",
)
@click.option(
    "--netbox-url",
    envvar="BMA_NETBOX_URL",
    default="",
    metavar="URL",
    help="NetBox base URL.  Also: BMA_NETBOX_URL env var.",
)
@click.option(
    "--netbox-token",
    envvar="BMA_NETBOX_TOKEN",
    default="",
    metavar="TOKEN",
    help="NetBox API token.  Also: BMA_NETBOX_TOKEN env var.",
)
@click.option(
    "--templates-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("configs/templates"),
    show_default=True,
    help="Root directory of Jinja2 templates.",
)
@click.option(
    "--catalogue",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to firmware_catalogue.yaml (skips media collection if omitted).",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("bundles"),
    show_default=True,
    help="Root directory for output bundles.",
)
@click.option(
    "--laptop-ip",
    default="",
    metavar="IP",
    help="Override laptop IP in the generated inventory.yaml.",
)
@click.option(
    "--archive/--no-archive",
    default=False,
    show_default=True,
    help="Create a .tar.gz archive of the finished bundle.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Render configs and print paths without writing files.",
)
@click.option(
    "--skip-media",
    is_flag=True,
    default=False,
    help="Skip media collection (configs + inventory only).",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging.",
)
def main(
    tag: str,
    netbox_url: str,
    netbox_token: str,
    templates_dir: Path,
    catalogue: Path | None,
    output_dir: Path,
    laptop_ip: str,
    archive: bool,
    dry_run: bool,
    skip_media: bool,
    verbose: bool,
) -> None:
    """Generate a complete deployment bundle from NetBox data."""
    _setup_logging(verbose)
    log = logging.getLogger(__name__)

    if dry_run:
        console.print("[yellow]DRY RUN — no files will be written[/yellow]")

    # ── Resolve NetBox credentials ─────────────────────────────────────
    netbox_url = netbox_url or os.environ.get("BMA_NETBOX_URL", "")
    netbox_token = netbox_token or os.environ.get("BMA_NETBOX_TOKEN", "")

    if not netbox_url or not netbox_token:
        console.print(
            "[red]ERROR:[/red] NetBox URL and token are required. "
            "Set --netbox-url / --netbox-token or BMA_NETBOX_URL / BMA_NETBOX_TOKEN.",
        )
        sys.exit(1)

    # ── Setup bundle directory ─────────────────────────────────────────
    bundle_dir = output_dir / tag.upper()
    if not dry_run:
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "configs").mkdir(exist_ok=True)

    console.print(f"[bold]BMA Config Generation[/bold] — tag=[cyan]{tag}[/cyan]")
    console.print(f"  Bundle directory : {bundle_dir}")
    console.print(f"  Templates        : {templates_dir}")

    # ── Step 1: Connect to NetBox ──────────────────────────────────────
    console.rule("[bold]1 / 9  Connect to NetBox")
    from bare_metal_automation.netbox import client as netbox_client_mod
    from bare_metal_automation.netbox import mapper as mapper_mod

    try:
        nb_client = netbox_client_mod.NetBoxClient(url=netbox_url, token=netbox_token)
        status = nb_client.ping()
        log.info("NetBox version: %s", status.get("netbox-version", "unknown"))
    except Exception as e:
        console.print(f"[red]NetBox connection failed:[/red] {e}")
        sys.exit(1)

    # ── Step 2: Fetch NetBox data ──────────────────────────────────────
    console.rule("[bold]2 / 9  Fetch devices from NetBox")
    try:
        devices = nb_client.get_devices_by_tag(tag.lower())
        console.print(f"  Found [green]{len(devices)}[/green] device(s) with tag '{tag}'")
    except Exception as e:
        console.print(f"[red]Failed to fetch devices:[/red] {e}")
        sys.exit(1)

    # Build per-device context
    device_context_pairs: list[tuple[str, Any]] = []
    device_specs: dict[str, dict[str, Any]] = {}

    for device in devices:
        try:
            config_context = nb_client.get_config_context(device.id)
            ip_addresses = nb_client.get_device_ips(device.id)
            interfaces: list[dict[str, Any]] = nb_client.get_interfaces(device.id)
            vlans_raw = nb_client.get_vlans_by_tag(tag.lower())

            template_path, ctx = ConfigRenderer.build_context(
                device, config_context, ip_addresses, interfaces, vlans_raw,
            )
            device_context_pairs.append((template_path, ctx))

            from bare_metal_automation.netbox.mapper import map_device_to_spec
            serial, spec = map_device_to_spec(device, config_context, ip_addresses)
            device_specs[serial] = spec

            log.debug("Prepared context for %s → %s", device.name, template_path)
        except Exception as e:
            log.warning("Skipping device '%s': %s", device.name, e)

    # ── Step 3: Render configs ─────────────────────────────────────────
    console.rule("[bold]3 / 9  Render device configs")
    rendered_paths: dict[str, Path] = {}

    if dry_run:
        for template_path, ctx in device_context_pairs:
            console.print(f"  [dim]would render[/dim] {ctx.hostname} → {template_path}")
    else:
        renderer = ConfigRenderer(
            templates_dir=templates_dir,
            output_dir=bundle_dir / "configs",
        )
        try:
            rendered_paths = renderer.render_all(device_context_pairs)
            console.print(f"  Rendered [green]{len(rendered_paths)}[/green] config(s)")
        except RuntimeError as e:
            console.print(f"[red]Config rendering errors:[/red]\n{e}")
            sys.exit(1)

    # Map serial → config filename for inventory export
    config_file_map: dict[str, str] = {}
    for serial, spec in device_specs.items():
        hostname = spec.get("hostname", "")
        cfg_path = rendered_paths.get(hostname)
        if cfg_path:
            config_file_map[serial] = cfg_path.name

    # ── Step 4: Export inventory.yaml ─────────────────────────────────
    console.rule("[bold]4 / 9  Export inventory.yaml")
    prefixes = nb_client.get_prefixes_by_tag(tag.lower())
    vlans_raw = nb_client.get_vlans_by_tag(tag.lower())
    deployment_meta = mapper_mod.map_deployment_metadata(
        tag, prefixes, vlans_raw, laptop_ip=laptop_ip,
    )

    if not dry_run:
        exporter = InventoryExporter(bundle_dir)
        inventory_path = exporter.export(
            deployment_meta=deployment_meta,
            device_specs=device_specs,
            config_file_map=config_file_map,
        )
        console.print(f"  Wrote {inventory_path}")

    # ── Step 5: Firmware catalogue ────────────────────────────────────
    firmware_entries = []
    iso_entries = []

    if catalogue and not skip_media and not dry_run:
        console.rule("[bold]5 / 9  Load firmware catalogue")
        try:
            fw_catalogue = FirmwareCatalogue(catalogue)
            console.print(
                f"  Loaded catalogue with platforms: "
                f"{', '.join(fw_catalogue.list_platforms())}",
            )

            # Resolve entries for each device spec
            for serial, spec in device_specs.items():
                platform = spec.get("platform", "")
                fw_version = spec.get("firmware_version")

                if platform.startswith("cisco"):
                    try:
                        entry = fw_catalogue.resolve_network_firmware(platform, fw_version)
                        firmware_entries.append(entry)
                        spec["firmware_image"] = entry.filename
                    except CatalogueError as e:
                        log.warning("No firmware for %s (%s): %s", serial, platform, e)

                elif platform.startswith("hpe"):
                    for resolve_fn, entry_list in [
                        (fw_catalogue.resolve_spp_iso, iso_entries),
                        (fw_catalogue.resolve_ilo_firmware, firmware_entries),
                    ]:
                        try:
                            entry = resolve_fn(platform)  # type: ignore[operator]
                            entry_list.append(entry)
                        except CatalogueError as e:
                            log.warning(
                                "No %s for %s: %s",
                                "SPP" if entry_list is iso_entries else "iLO fw",
                                serial,
                                e,
                            )

        except CatalogueError as e:
            console.print(f"[red]Firmware catalogue error:[/red] {e}")
            sys.exit(1)
    else:
        console.rule("[bold]5 / 9  Firmware catalogue [dim](skipped)[/dim]")

    # ── Step 6: Collect media ─────────────────────────────────────────
    if not skip_media and not dry_run and (firmware_entries or iso_entries):
        console.rule("[bold]6 / 9  Collect media")
        collector = MediaCollector(bundle_dir)
        errors: list[str] = []

        for entry in firmware_entries:
            try:
                collector.collect_firmware(entry)
            except Exception as e:
                log.error("Failed to collect firmware %s: %s", entry.filename, e)
                errors.append(str(e))

        for entry in iso_entries:
            try:
                collector.collect_iso(entry)
            except Exception as e:
                log.error("Failed to collect ISO %s: %s", entry.filename, e)
                errors.append(str(e))

        console.print(
            f"  Collected [green]{len(collector.collected)}[/green] file(s), "
            f"[red]{len(errors)}[/red] error(s)",
        )
    else:
        console.rule("[bold]6 / 9  Collect media [dim](skipped)[/dim]")

    # ── Step 7: Ansible inventory ─────────────────────────────────────
    console.rule("[bold]7 / 9  Generate Ansible inventory")
    packager = BundlePackager(
        bundle_dir=bundle_dir,
        deployment_name=deployment_meta.get("name", tag),
        site_slug=deployment_meta.get("site_slug", ""),
    )

    if not dry_run:
        hosts_path = packager.write_ansible_inventory(device_specs)
        console.print(f"  Wrote {hosts_path}")

    # ── Step 8: Manifest + checksums ──────────────────────────────────
    console.rule("[bold]8 / 9  Write manifest & checksums")

    if not dry_run:
        packager.register_configs(bundle_dir / "configs")
        packager.register_firmware(bundle_dir / "firmware")
        packager.register_isos(bundle_dir / "isos")
        packager.register_certs(bundle_dir / "certs")
        packager.register_ansible(bundle_dir / "ansible")

        checksums_path = packager.write_checksums()
        manifest_path = packager.write_manifest()
        console.print(f"  Wrote {checksums_path}")
        console.print(f"  Wrote {manifest_path}")

    # ── Step 9: Validate ──────────────────────────────────────────────
    console.rule("[bold]9 / 9  Validate bundle")

    if not dry_run:
        errors_v = packager.validate()
        if errors_v:
            console.print(
                f"[yellow]Bundle has {len(errors_v)} validation issue(s):[/yellow]",
            )
            for err in errors_v:
                console.print(f"  [yellow]•[/yellow] {err}")
        else:
            console.print("  [green]✓ Bundle is complete and valid[/green]")

        # Optional archive
        if archive:
            archive_path = packager.package_archive(output_dir)
            console.print(f"  [green]Archive:[/green] {archive_path}")

    console.print()
    console.print(
        f"[bold green]Done.[/bold green]  Bundle ready at: [cyan]{bundle_dir}[/cyan]",
    )


if __name__ == "__main__":
    main()
