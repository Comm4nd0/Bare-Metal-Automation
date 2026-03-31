"""Bare Metal Automation CLI — Zero-Touch Provisioning for bare-metal infrastructure."""

import click
from rich.console import Console

console = Console()


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """Bare Metal Automation: Zero-Touch Provisioning for bare-metal infrastructure."""
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
    from bare_metal_automation.orchestrator import Orchestrator

    console.print("[bold blue]Bare Metal Automation[/] — Discovery Phase")
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
    from bare_metal_automation.orchestrator import Orchestrator

    console.print("[bold blue]Bare Metal Automation[/] — Cabling Validation")
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
    from bare_metal_automation.orchestrator import Orchestrator

    console.print("[bold blue]Bare Metal Automation[/] — Network Configuration")
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
    from bare_metal_automation.orchestrator import Orchestrator

    console.print("[bold blue]Bare Metal Automation[/] — Firmware Upgrade")
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
    from bare_metal_automation.orchestrator import Orchestrator

    console.print("[bold blue]Bare Metal Automation[/] — Server Provisioning")
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
    from bare_metal_automation.orchestrator import Orchestrator

    console.print("[bold blue]Bare Metal Automation[/] — NTP Provisioning")
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
    default=".bma-checkpoint.json",
    help="Path to checkpoint file.",
)
def deploy(inventory: str, dry_run: bool, resume: bool, checkpoint: str) -> None:
    """Execute full deployment — discovery through final validation.

    Use --resume to continue a deployment that was previously interrupted.
    The checkpoint file is written automatically after each phase, so
    progress is never lost.
    """
    from bare_metal_automation.orchestrator import Orchestrator

    if resume:
        console.print("[bold blue]Bare Metal Automation[/] — Resuming Deployment")
        try:
            orch = Orchestrator.from_checkpoint(checkpoint_path=checkpoint)
        except FileNotFoundError:
            console.print(
                f"[bold red]No checkpoint file found at {checkpoint}.[/]\n"
                "Run a deployment first, or check the --checkpoint path."
            )
            raise SystemExit(1)
    else:
        console.print("[bold blue]Bare Metal Automation[/] — Full Deployment")
        orch = Orchestrator(
            inventory_path=inventory,
            checkpoint_path=checkpoint,
        )

    orch.run_full_deployment(dry_run=dry_run, resume=resume)


@main.command()
@click.option(
    "--checkpoint", "-c",
    default=".bma-checkpoint.json",
    help="Path to checkpoint file.",
)
def status(checkpoint: str) -> None:
    """Show the status of a saved checkpoint (current phase, devices, etc.)."""
    from bare_metal_automation.common.checkpoint import load_checkpoint

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
    default=".bma-checkpoint.json",
    help="Path to checkpoint file.",
)
def clear_checkpoint(checkpoint: str) -> None:
    """Remove a saved checkpoint file to start fresh."""
    from bare_metal_automation.common.checkpoint import remove_checkpoint

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

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bare_metal_automation.dashboard.settings")

    import django
    django.setup()

    from django.core.management import call_command

    call_command("migrate", "--run-syncdb", verbosity=0)

    console.print("[bold blue]Bare Metal Automation[/] — Simulation Mode")
    console.print(f"Running simulated deployment: [bold]{name}[/]")
    console.print("Press Ctrl+C to stop.\n")

    call_command("run_simulation", name=name)


@main.command()
@click.option(
    "--node", "-n",
    required=True,
    help="Node tag in NetBox (e.g. D001).",
)
@click.option(
    "--output", "-o",
    default="configs/inventory/inventory.yaml",
    help="Output path for generated inventory file.",
)
@click.option(
    "--netbox-url",
    envvar="BMA_NETBOX_URL",
    required=True,
    help="NetBox URL (or set BMA_NETBOX_URL env var).",
)
@click.option(
    "--netbox-token",
    envvar="BMA_NETBOX_TOKEN",
    required=True,
    help="NetBox API token (or set BMA_NETBOX_TOKEN env var).",
)
@click.option(
    "--git-repo",
    envvar="BMA_GIT_REPO_URL",
    default="",
    help="Git repo URL for templates/firmware.",
)
@click.option(
    "--git-branch",
    envvar="BMA_GIT_REPO_BRANCH",
    default="main",
    help="Git branch to use.",
)
@click.option(
    "--git-path",
    envvar="BMA_GIT_REPO_PATH",
    default="configs",
    help="Local path for git repo clone.",
)
def prepare(
    node: str,
    output: str,
    netbox_url: str,
    netbox_token: str,
    git_repo: str,
    git_branch: str,
    git_path: str,
) -> None:
    """Prepare a build from NetBox — pull configs and stage files."""
    from bare_metal_automation.netbox.client import NetBoxClient
    from bare_metal_automation.netbox.loader import NetBoxLoader

    console.print(
        "[bold blue]Bare Metal Automation[/] — "
        f"Prepare Build for [bold]{node}[/]",
    )

    # Connect to NetBox
    console.print(f"  Connecting to NetBox at {netbox_url}...")
    try:
        client = NetBoxClient(netbox_url, netbox_token)
        status = client.ping()
        console.print(
            f"  Connected (NetBox "
            f"{status.get('netbox-version', 'unknown')})",
        )
    except Exception as e:
        console.print(f"[bold red]Cannot connect to NetBox:[/] {e}")
        raise SystemExit(1)

    # Load node from NetBox
    console.print(f"  Loading node {node}...")
    loader = NetBoxLoader(client)
    try:
        inventory = loader.load_node(node)
        console.print(
            f"  Loaded {len(inventory.devices)} devices "
            f"(subnet={inventory.bootstrap_subnet}, "
            f"vlan={inventory.management_vlan})",
        )
    except Exception as e:
        console.print(f"[bold red]Failed to load node:[/] {e}")
        raise SystemExit(1)

    # Sync git repo if configured
    if git_repo:
        console.print(f"  Syncing git repo ({git_branch})...")
        from bare_metal_automation.netbox.git import GitRepoManager

        try:
            git = GitRepoManager(git_repo, git_path, git_branch)
            result = git.sync()
            console.print(
                f"  Repo {result['status']} "
                f"(commit {result['commit']})",
            )

            # Verify files
            missing = git.verify_files(inventory)
            if missing:
                console.print(
                    f"\n[bold yellow]Warning: "
                    f"{len(missing)} file(s) missing:[/]",
                )
                for m in missing:
                    console.print(f"    — {m}")
        except Exception as e:
            console.print(f"[bold red]Git sync failed:[/] {e}")
            raise SystemExit(1)

    # Save inventory YAML
    console.print(f"  Saving inventory to {output}...")
    loader.save_inventory_yaml(inventory, output)
    console.print(
        f"\n[bold green]Preparation complete![/] "
        f"{len(inventory.devices)} devices ready.",
    )
    console.print(
        f"  Inventory: [cyan]{output}[/]",
    )
    console.print(
        f"  Deploy with: "
        f"[dim]bare-metal-automation deploy -i {output}[/]",
    )


@main.command()
@click.option(
    "--inventory", "-i",
    default="configs/inventory/inventory.yaml",
    help="Path to inventory file.",
)
@click.option(
    "--resume", is_flag=True,
    help="Resume a previously interrupted rollback.",
)
@click.option(
    "--checkpoint", "-c",
    default=".bma-rollback-checkpoint.json",
    help="Path to rollback checkpoint file.",
)
@click.option(
    "--deploy-checkpoint",
    default=".bma-checkpoint.json",
    help="Path to deployment checkpoint file.",
)
@click.confirmation_option(
    prompt="This will FACTORY RESET all devices. Are you sure?",
)
def rollback(
    inventory: str,
    resume: bool,
    checkpoint: str,
    deploy_checkpoint: str,
) -> None:
    """Reset all devices to factory defaults (DESTRUCTIVE).

    Reads the deployment checkpoint to find provisioned devices, then
    resets them all to factory state: NTP first, then servers, then
    network devices (outside-in, core switch last).
    """
    from bare_metal_automation.rollback.orchestrator import (
        RollbackOrchestrator,
    )

    if resume:
        console.print(
            "[bold red]Bare Metal Automation[/] — "
            "Resuming Factory Rollback",
        )
        try:
            orch = RollbackOrchestrator.from_checkpoint(
                rollback_checkpoint=checkpoint,
            )
        except FileNotFoundError:
            console.print(
                f"[bold red]No rollback checkpoint found at "
                f"{checkpoint}.[/]\n"
                "Start a new rollback instead.",
            )
            raise SystemExit(1)
    else:
        console.print(
            "[bold red]Bare Metal Automation[/] — "
            "Factory Rollback",
        )
        orch = RollbackOrchestrator(
            inventory_path=inventory,
            deployment_checkpoint=deploy_checkpoint,
            rollback_checkpoint=checkpoint,
        )

    orch.run_full_rollback(resume=resume)


@main.command()
@click.option("--host", default="0.0.0.0", help="Dashboard bind address.")
@click.option("--port", default=8080, help="Dashboard port.")
@click.option("--mock", is_flag=True, help="Populate mock devices for testing.")
def serve(host: str, port: int, mock: bool) -> None:
    """Start the Bare Metal Automation dashboard (Django)."""
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bare_metal_automation.dashboard.settings")

    import django
    django.setup()

    from django.core.management import call_command

    # Run migrations automatically
    call_command("migrate", "--run-syncdb", verbosity=0)

    if mock:
        console.print("[dim]Loading mock deployment data...[/]")
        call_command("load_mock_data")

    console.print(f"[bold blue]Bare Metal Automation[/] — Dashboard at http://{host}:{port}")
    call_command("runserver", f"{host}:{port}")


if __name__ == "__main__":
    main()
