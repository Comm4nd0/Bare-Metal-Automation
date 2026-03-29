"""Orchestrator — sequences the deployment phases and manages state."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console

from ztp_forge.models import DeploymentInventory, DeploymentPhase, DeploymentState

logger = logging.getLogger(__name__)
console = Console()


class Orchestrator:
    """Central controller that drives the deployment through its phases."""

    def __init__(self, inventory_path: str, ssh_timeout: int = 30) -> None:
        self.inventory_path = Path(inventory_path)
        self.ssh_timeout = ssh_timeout
        self.state = DeploymentState()
        self.inventory: DeploymentInventory | None = None

    def _load_inventory(self) -> DeploymentInventory:
        """Load and validate the inventory YAML."""
        from ztp_forge.inventory import load_inventory

        console.print("[dim]Loading inventory...[/]")
        inv = load_inventory(self.inventory_path)
        console.print(
            f"  Loaded deployment [bold]{inv.name}[/] — "
            f"{len(inv.devices)} devices expected"
        )
        return inv

    def run_discovery(self) -> DeploymentState:
        """Phase 1: Discover all devices on the bootstrap network."""
        self.inventory = self._load_inventory()
        self.state.phase = DeploymentPhase.DISCOVERY

        from ztp_forge.discovery.engine import DiscoveryEngine

        engine = DiscoveryEngine(
            bootstrap_subnet=self.inventory.bootstrap_subnet,
            laptop_ip=self.inventory.laptop_ip,
            ssh_timeout=self.ssh_timeout,
        )

        console.print("\n[bold]Phase 1 — Discovery[/]")

        # Step 1: Collect DHCP leases
        console.print("  [dim]Collecting DHCP leases...[/]")
        leases = engine.get_dhcp_leases()
        console.print(f"  Found {len(leases)} active leases")

        # Step 2: Probe each device (SSH + CDP + serial)
        console.print("  [dim]Probing devices...[/]")
        for ip, mac in leases.items():
            device = engine.probe_device(ip, mac)
            self.state.discovered_devices[ip] = device

        # Step 3: Match serials to inventory
        console.print("  [dim]Matching to inventory...[/]")
        engine.match_to_inventory(self.state.discovered_devices, self.inventory)

        matched = len(self.state.matched_devices)
        unmatched = len(self.state.unmatched_devices)
        expected = len(self.inventory.expected_serials)
        missing = expected - matched

        console.print(f"\n  [green]Matched:[/] {matched}/{expected}")
        if unmatched:
            console.print(f"  [yellow]Unknown devices:[/] {unmatched}")
        if missing:
            console.print(f"  [red]Missing devices:[/] {missing}")
            for serial in self.inventory.expected_serials:
                found = any(
                    d.serial == serial for d in self.state.discovered_devices.values()
                )
                if not found:
                    spec = self.inventory.devices[serial]
                    console.print(f"    — {spec.get('hostname', serial)} ({serial})")

        return self.state

    def run_topology(self) -> DeploymentState:
        """Phase 2a: Build topology graph and calculate config order."""
        from ztp_forge.topology.builder import TopologyBuilder

        console.print("\n[bold]Phase 2a — Topology Mapping[/]")

        builder = TopologyBuilder()
        graph = builder.build_graph(self.state.discovered_devices)
        order = builder.calculate_config_order(graph, root_ip=self.inventory.laptop_ip)

        self.state.topology_order = order

        console.print("  Configuration order (outside-in):")
        for i, serial in enumerate(order, 1):
            device = next(
                d for d in self.state.discovered_devices.values() if d.serial == serial
            )
            console.print(
                f"    {i}. {device.intended_hostname or device.hostname} "
                f"(depth {device.bfs_depth})"
            )

        return self.state

    def run_validation(self) -> DeploymentState:
        """Phase 2b: Validate cabling against intended config."""
        if not self.state.discovered_devices:
            self.run_discovery()
            self.run_topology()

        from ztp_forge.cabling.validator import CablingValidator

        console.print("\n[bold]Phase 2b — Cabling Validation[/]")

        validator = CablingValidator(inventory=self.inventory)
        self.state.cabling_results = validator.validate_all(self.state.discovered_devices)

        validator.print_report(self.state.cabling_results)
        return self.state

    def run_network_config(self, dry_run: bool = False) -> DeploymentState:
        """Phase 4: Configure network devices in outside-in order."""
        if not self.state.topology_order:
            self.run_discovery()
            self.run_topology()
            self.run_validation()

        if self.state.has_blocking_errors:
            console.print("[bold red]Cannot proceed — blocking errors exist.[/]")
            return self.state

        from ztp_forge.configurator.network import NetworkConfigurator

        console.print("\n[bold]Phase 4 — Network Configuration[/]")

        configurator = NetworkConfigurator(
            inventory=self.inventory,
            ssh_timeout=self.ssh_timeout,
        )

        for serial in self.state.topology_order:
            device = next(
                d for d in self.state.discovered_devices.values() if d.serial == serial
            )
            console.print(
                f"\n  Configuring {device.intended_hostname} "
                f"(depth {device.bfs_depth})..."
            )

            if dry_run:
                console.print("    [yellow]DRY RUN — skipping[/]")
                continue

            success = configurator.configure_device(device)
            if success:
                device.state = "configured"
                console.print(f"    [green]✓ Configured and validated[/]")
            else:
                device.state = "failed"
                console.print(f"    [red]✗ Configuration failed — rollback triggered[/]")
                self.state.errors.append(f"Failed to configure {device.intended_hostname}")
                break  # Stop — don't configure devices closer to laptop if a further one failed

        return self.state

    def run_server_provisioning(self) -> DeploymentState:
        """Phase 5-6: Provision servers via Redfish."""
        console.print("\n[bold]Phase 5 — Server Provisioning[/]")
        # TODO: Implement Redfish provisioning
        console.print("  [yellow]Not yet implemented[/]")
        return self.state

    def run_full_deployment(self, dry_run: bool = False) -> DeploymentState:
        """Execute all phases in sequence."""
        self.inventory = self._load_inventory()

        self.run_discovery()
        self.run_topology()
        self.run_validation()

        if self.state.has_blocking_errors:
            console.print("\n[bold red]Deployment blocked by errors. Review above.[/]")
            return self.state

        if dry_run:
            console.print("\n[bold yellow]DRY RUN — stopping before configuration.[/]")
            return self.state

        # Phase 3: Heavy transfers (ISOs, firmware) while network is still dumb
        console.print("\n[bold]Phase 3 — Heavy Transfers[/]")
        self.run_server_provisioning()  # Kicks off ISO mounts

        # Phase 4: Network configuration
        self.run_network_config()

        # Phase 5: Laptop pivot
        console.print("\n[bold]Phase 5 — Laptop Pivot[/]")
        console.print("  [yellow]Reconfigure laptop NIC to management VLAN[/]")

        # Phase 6: Server post-install
        console.print("\n[bold]Phase 6 — Server Post-Install[/]")
        console.print("  [yellow]Not yet implemented[/]")

        # Phase 7: Final validation
        console.print("\n[bold]Phase 7 — Final Validation[/]")
        console.print("  [yellow]Not yet implemented[/]")

        self.state.phase = DeploymentPhase.COMPLETE
        console.print("\n[bold green]Deployment complete.[/]")
        return self.state
