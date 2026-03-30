"""ZTP-Forge CLI — Zero-Touch Provisioning for bare-metal infrastructure."""

import click
from rich.console import Console

console = Console()


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """ZTP-Forge: Zero-Touch Provisioning for bare-metal infrastructure."""
    pass


@main.command()
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
@click.option("--timeout", "-t", default=30, help="SSH timeout in seconds.")
def discover(inventory: str, timeout: int) -> None:
    """Discover all devices on the bootstrap network via DHCP and CDP."""
    from ztp_forge.orchestrator import Orchestrator

    console.print("[bold blue]ZTP-Forge[/] — Discovery Phase")
    orch = Orchestrator(inventory_path=inventory, ssh_timeout=timeout)
    orch.run_discovery()


@main.command()
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
def validate(inventory: str) -> None:
    """Validate physical cabling against intended configuration."""
    from ztp_forge.orchestrator import Orchestrator

    console.print("[bold blue]ZTP-Forge[/] — Cabling Validation")
    orch = Orchestrator(inventory_path=inventory)
    orch.run_validation()


@main.command(name="configure-network")
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be configured without applying.")
def configure_network(inventory: str, dry_run: bool) -> None:
    """Configure all network devices in safe outside-in order."""
    from ztp_forge.orchestrator import Orchestrator

    console.print("[bold blue]ZTP-Forge[/] — Network Configuration")
    orch = Orchestrator(inventory_path=inventory)
    orch.run_network_config(dry_run=dry_run)


@main.command(name="upgrade-firmware")
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
def upgrade_firmware(inventory: str) -> None:
    """Upgrade firmware on network devices (Cisco IOS/ASA images)."""
    from ztp_forge.orchestrator import Orchestrator

    console.print("[bold blue]ZTP-Forge[/] — Firmware Upgrade")
    orch = Orchestrator(inventory_path=inventory)
    orch.run_firmware_upgrade()


@main.command(name="provision-servers")
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
def provision_servers(inventory: str) -> None:
    """Provision HPE servers via Redfish (BIOS, RAID, SPP, OS install, iLO)."""
    from ztp_forge.orchestrator import Orchestrator

    console.print("[bold blue]ZTP-Forge[/] — Server Provisioning")
    orch = Orchestrator(inventory_path=inventory)
    orch.run_server_provisioning()


@main.command(name="provision-ntp")
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
def provision_ntp(inventory: str) -> None:
    """Provision Meinberg NTP devices (OS install and configuration)."""
    from ztp_forge.orchestrator import Orchestrator

    console.print("[bold blue]ZTP-Forge[/] — NTP Provisioning")
    orch = Orchestrator(inventory_path=inventory)
    orch.run_ntp_provisioning()


@main.command()
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
@click.option("--dry-run", is_flag=True, help="Run discovery and validation only.")
@click.option(
    "--resume", is_flag=True,
    help="Resume a previously interrupted deployment from its last checkpoint.",
)
@click.option(
    "--checkpoint", "-c",
    default=".ztp-checkpoint.json",
    help="Path to checkpoint file.",
)
def deploy(inventory: str, dry_run: bool, resume: bool, checkpoint: str) -> None:
    """Execute full deployment — discovery through final validation.

    Use --resume to continue a deployment that was previously interrupted.
    The checkpoint file is written automatically after each phase, so
    progress is never lost.
    """
    from ztp_forge.orchestrator import Orchestrator

    if resume:
        console.print("[bold blue]ZTP-Forge[/] — Resuming Deployment")
        try:
            orch = Orchestrator.from_checkpoint(checkpoint_path=checkpoint)
        except FileNotFoundError:
            console.print(
                f"[bold red]No checkpoint file found at {checkpoint}.[/]\n"
                "Run a deployment first, or check the --checkpoint path."
            )
            raise SystemExit(1)
    else:
        console.print("[bold blue]ZTP-Forge[/] — Full Deployment")
        orch = Orchestrator(
            inventory_path=inventory,
            checkpoint_path=checkpoint,
        )

    orch.run_full_deployment(dry_run=dry_run, resume=resume)


@main.command()
@click.option(
    "--checkpoint", "-c",
    default=".ztp-checkpoint.json",
    help="Path to checkpoint file.",
)
def status(checkpoint: str) -> None:
    """Show the status of a saved checkpoint (current phase, devices, etc.)."""
    from ztp_forge.common.checkpoint import load_checkpoint

    try:
        data = load_checkpoint(checkpoint)
    except FileNotFoundError:
        console.print(f"[dim]No checkpoint found at {checkpoint}[/]")
        return

    console.print(f"[bold]Checkpoint:[/] {checkpoint}")
    console.print(f"  Phase:     [cyan]{data['phase']}[/]")
    console.print(f"  Saved at:  {data.get('saved_at', 'unknown')}")
    console.print(f"  Inventory: {data.get('inventory_path', 'unknown')}")
    devices = data.get("discovered_devices", {})
    console.print(f"  Devices:   {len(devices)}")
    topo = data.get("topology_order", [])
    console.print(f"  Topology:  {len(topo)} devices in order")
    errors = data.get("errors", [])
    if errors:
        console.print(f"  [red]Errors:    {len(errors)}[/]")
        for err in errors:
            console.print(f"    — {err}")


@main.command(name="clear-checkpoint")
@click.option(
    "--checkpoint", "-c",
    default=".ztp-checkpoint.json",
    help="Path to checkpoint file.",
)
def clear_checkpoint(checkpoint: str) -> None:
    """Remove a saved checkpoint file to start fresh."""
    from ztp_forge.common.checkpoint import remove_checkpoint

    remove_checkpoint(checkpoint)
    console.print(f"[dim]Checkpoint cleared: {checkpoint}[/]")


@main.command()
@click.option(
    "--name", "-n",
    default="SIM-Rack-Demo",
    help="Name for the simulated deployment.",
)
def simulate(name: str) -> None:
    """Run a simulated deployment through all phases (no hardware required)."""
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ztp_forge.dashboard.settings")

    import django
    django.setup()

    from django.core.management import call_command

    call_command("migrate", "--run-syncdb", verbosity=0)

    console.print("[bold blue]ZTP-Forge[/] — Simulation Mode")
    console.print(f"Running simulated deployment: [bold]{name}[/]")
    console.print("Press Ctrl+C to stop.\n")

    call_command("run_simulation", name=name)


@main.command()
@click.option("--host", default="0.0.0.0", help="Dashboard bind address.")
@click.option("--port", default=8080, help="Dashboard port.")
@click.option("--mock", is_flag=True, help="Populate mock devices for testing.")
def serve(host: str, port: int, mock: bool) -> None:
    """Start the ZTP-Forge dashboard (Django)."""
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ztp_forge.dashboard.settings")

    import django
    django.setup()

    from django.core.management import call_command

    # Run migrations automatically
    call_command("migrate", "--run-syncdb", verbosity=0)

    if mock:
        console.print("[dim]Loading mock deployment data...[/]")
        call_command("load_mock_data")

    console.print(f"[bold blue]ZTP-Forge[/] — Dashboard at http://{host}:{port}")
    call_command("runserver", f"{host}:{port}")


if __name__ == "__main__":
    main()
