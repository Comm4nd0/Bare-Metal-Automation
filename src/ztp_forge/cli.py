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


@main.command(name="provision-servers")
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
def provision_servers(inventory: str) -> None:
    """Provision servers via Redfish (firmware, BIOS, OS install)."""
    from ztp_forge.orchestrator import Orchestrator

    console.print("[bold blue]ZTP-Forge[/] — Server Provisioning")
    orch = Orchestrator(inventory_path=inventory)
    orch.run_server_provisioning()


@main.command()
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
@click.option("--dry-run", is_flag=True, help="Run discovery and validation only.")
def deploy(inventory: str, dry_run: bool) -> None:
    """Execute full deployment — discovery through final validation."""
    from ztp_forge.orchestrator import Orchestrator

    console.print("[bold blue]ZTP-Forge[/] — Full Deployment")
    orch = Orchestrator(inventory_path=inventory)
    orch.run_full_deployment(dry_run=dry_run)


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
