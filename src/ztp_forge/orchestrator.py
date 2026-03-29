"""Orchestrator — sequences the deployment phases and manages state."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console

from ztp_forge.models import (
    DeploymentInventory,
    DeploymentPhase,
    DeploymentState,
    DeviceState,
    DiscoveredDevice,
)

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

    def _get_network_devices(self) -> list[DiscoveredDevice]:
        """Return discovered Cisco network devices in topology order."""
        devices = []
        for serial in self.state.topology_order:
            device = next(
                (d for d in self.state.discovered_devices.values()
                 if d.serial == serial),
                None,
            )
            if device is None:
                continue
            spec = self.inventory.get_device_spec(serial) or {}
            platform = spec.get("platform", "")
            if platform.startswith("cisco"):
                devices.append(device)
        return devices

    def _get_devices_by_platform_prefix(
        self, prefix: str
    ) -> list[DiscoveredDevice]:
        """Return discovered devices whose platform starts with prefix."""
        devices = []
        for serial, spec in self.inventory.devices.items():
            if not spec.get("platform", "").startswith(prefix):
                continue
            device = next(
                (d for d in self.state.discovered_devices.values()
                 if d.serial == serial),
                None,
            )
            if device is not None:
                devices.append(device)
            else:
                hostname = spec.get("hostname", serial)
                console.print(
                    f"  [yellow]Skipping {hostname} — "
                    f"not discovered[/]"
                )
        return devices

    def _get_devices_by_platform(
        self, platform_value: str
    ) -> list[DiscoveredDevice]:
        """Return discovered devices with an exact platform match."""
        devices = []
        for serial, spec in self.inventory.devices.items():
            if spec.get("platform") != platform_value:
                continue
            device = next(
                (d for d in self.state.discovered_devices.values()
                 if d.serial == serial),
                None,
            )
            if device is not None:
                devices.append(device)
            else:
                hostname = spec.get("hostname", serial)
                console.print(
                    f"  [yellow]Skipping {hostname} — "
                    f"not discovered[/]"
                )
        return devices

    def run_network_config(self, dry_run: bool = False) -> DeploymentState:
        """Configure network devices in outside-in order.

        Devices at the same BFS depth are configured in parallel.
        Each depth group must finish before the next (closer) group
        starts, preserving the outside-in safety constraint.
        """
        if not self.state.topology_order:
            self.run_discovery()
            self.run_topology()
            self.run_validation()

        if self.state.has_blocking_errors:
            console.print(
                "[bold red]Cannot proceed — blocking errors exist.[/]"
            )
            return self.state

        from ztp_forge.common.parallel import run_parallel_by_depth
        from ztp_forge.configurator.network import NetworkConfigurator

        console.print("\n[bold]Network Configuration (parallel by depth)[/]")

        configurator = NetworkConfigurator(
            inventory=self.inventory,
            ssh_timeout=self.ssh_timeout,
        )

        network_devices = self._get_network_devices()

        if dry_run:
            for d in network_devices:
                console.print(
                    f"  [yellow]DRY RUN — would configure "
                    f"{d.intended_hostname} (depth {d.bfs_depth})[/]"
                )
            return self.state

        results = run_parallel_by_depth(
            devices=network_devices,
            operation=configurator.configure_device,
            max_workers=4,
            stop_on_failure=True,
        )

        for device in network_devices:
            key = device.serial or device.ip
            if results.get(key):
                device.state = DeviceState.CONFIGURED
                console.print(
                    f"  [green]✓ {device.intended_hostname} configured[/]"
                )
            elif key in results:
                device.state = DeviceState.FAILED
                console.print(
                    f"  [red]✗ {device.intended_hostname} failed[/]"
                )
                self.state.errors.append(
                    f"Failed to configure {device.intended_hostname}"
                )

        return self.state

    def run_firmware_upgrade(self) -> DeploymentState:
        """Upgrade firmware on network devices.

        Devices at the same BFS depth are upgraded in parallel.
        Stops if any device at a given depth fails — we don't want
        to upgrade closer devices when a further one is in a bad state.
        """
        if not self.inventory:
            self.inventory = self._load_inventory()

        if not self.state.discovered_devices:
            self.run_discovery()
            self.run_topology()

        from ztp_forge.common.parallel import run_parallel_by_depth
        from ztp_forge.configurator.firmware import FirmwareConfigurator

        console.print(
            "\n[bold]Firmware Upgrade (parallel by depth)[/]"
        )
        self.state.phase = DeploymentPhase.FIRMWARE_UPGRADE

        configurator = FirmwareConfigurator(inventory=self.inventory)

        # Filter to Cisco devices that have firmware specified
        fw_devices = []
        for device in self._get_network_devices():
            spec = self.inventory.get_device_spec(device.serial) or {}
            if spec.get("firmware_image"):
                fw_devices.append(device)

        if not fw_devices:
            console.print("  No firmware upgrades needed")
            return self.state

        results = run_parallel_by_depth(
            devices=fw_devices,
            operation=configurator.upgrade_device,
            max_workers=4,
            stop_on_failure=True,
        )

        for device in fw_devices:
            key = device.serial or device.ip
            if results.get(key):
                console.print(
                    f"  [green]✓ {device.intended_hostname} "
                    f"firmware upgraded[/]"
                )
            elif key in results:
                console.print(
                    f"  [red]✗ {device.intended_hostname} "
                    f"firmware upgrade failed[/]"
                )
                self.state.errors.append(
                    f"Firmware upgrade failed for "
                    f"{device.intended_hostname}"
                )

        return self.state

    def run_server_provisioning(self) -> DeploymentState:
        """Provision HPE servers via Redfish (BIOS, RAID, SPP, OS, iLO).

        All servers are provisioned in parallel — they are independent
        devices accessed via iLO and don't sit on each other's paths.
        """
        if not self.inventory:
            self.inventory = self._load_inventory()

        from ztp_forge.common.parallel import run_independent_parallel
        from ztp_forge.provisioner.server import HPEServerProvisioner

        console.print(
            "\n[bold]Server Provisioning (parallel)[/]"
        )
        self.state.phase = DeploymentPhase.SERVER_PROVISION

        provisioner = HPEServerProvisioner(inventory=self.inventory)
        servers = self._get_devices_by_platform_prefix("hpe_")

        if not servers:
            console.print("  No HPE servers to provision")
            return self.state

        for device in servers:
            device.state = DeviceState.PROVISIONING

        results = run_independent_parallel(
            devices=servers,
            operation=provisioner.provision_server,
            max_workers=len(servers),
        )

        for device in servers:
            key = device.serial or device.ip
            if results.get(key):
                console.print(
                    f"  [green]✓ {device.intended_hostname} "
                    f"provisioned[/]"
                )
            else:
                console.print(
                    f"  [red]✗ {device.intended_hostname} "
                    f"provisioning failed[/]"
                )
                self.state.errors.append(
                    f"Server provisioning failed for "
                    f"{device.intended_hostname}"
                )

        return self.state

    def run_ntp_provisioning(self) -> DeploymentState:
        """Provision Meinberg NTP devices.

        All NTP devices run in parallel — they are independent
        appliances with their own management interfaces.
        """
        if not self.inventory:
            self.inventory = self._load_inventory()

        from ztp_forge.common.parallel import run_independent_parallel
        from ztp_forge.provisioner.meinberg import MeinbergProvisioner

        console.print(
            "\n[bold]NTP Provisioning (parallel)[/]"
        )
        self.state.phase = DeploymentPhase.NTP_PROVISION

        provisioner = MeinbergProvisioner(inventory=self.inventory)
        ntp_devices = self._get_devices_by_platform("meinberg_lantime")

        if not ntp_devices:
            console.print("  No Meinberg NTP devices to provision")
            return self.state

        for device in ntp_devices:
            device.state = DeviceState.PROVISIONING

        results = run_independent_parallel(
            devices=ntp_devices,
            operation=provisioner.provision_device,
            max_workers=len(ntp_devices),
        )

        for device in ntp_devices:
            key = device.serial or device.ip
            if results.get(key):
                console.print(
                    f"  [green]✓ {device.intended_hostname} "
                    f"provisioned[/]"
                )
            else:
                console.print(
                    f"  [red]✗ {device.intended_hostname} "
                    f"provisioning failed[/]"
                )
                self.state.errors.append(
                    f"NTP provisioning failed for "
                    f"{device.intended_hostname}"
                )

        return self.state

    def run_full_deployment(self, dry_run: bool = False) -> DeploymentState:
        """Execute all phases in sequence."""
        self.inventory = self._load_inventory()

        # Phase 1: Discovery
        self.run_discovery()
        self.run_topology()
        self.run_validation()

        if self.state.has_blocking_errors:
            console.print("\n[bold red]Deployment blocked by errors. Review above.[/]")
            return self.state

        if dry_run:
            console.print("\n[bold yellow]DRY RUN — stopping before configuration.[/]")
            return self.state

        # Phase 2: Firmware upgrade on network devices (before config)
        self.run_firmware_upgrade()
        if self.state.has_blocking_errors:
            console.print("\n[bold red]Deployment blocked by firmware errors.[/]")
            return self.state

        # Phase 3: Heavy transfers (ISOs, firmware) while network is still flat L2
        console.print("\n[bold]Phase 3 — Heavy Transfers[/]")
        self.state.phase = DeploymentPhase.HEAVY_TRANSFERS

        # Phase 4: Network configuration (outside-in with dead man's switch)
        self.run_network_config()
        if self.state.has_blocking_errors:
            console.print("\n[bold red]Deployment blocked by network config errors.[/]")
            return self.state

        # Phase 5: Laptop pivot
        console.print("\n[bold]Phase 5 — Laptop Pivot[/]")
        self.state.phase = DeploymentPhase.LAPTOP_PIVOT
        console.print("  [yellow]Reconfigure laptop NIC to management VLAN[/]")

        # Phase 6: Server provisioning (BIOS, RAID, SPP, OS, iLO)
        self.run_server_provisioning()

        # Phase 7: NTP provisioning (Meinberg)
        self.run_ntp_provisioning()

        # Phase 8: Post-install
        console.print("\n[bold]Phase 8 — Post-Install[/]")
        self.state.phase = DeploymentPhase.POST_INSTALL
        console.print("  [yellow]Post-install tasks (not yet implemented)[/]")

        # Phase 9: Final validation
        console.print("\n[bold]Phase 9 — Final Validation[/]")
        self.state.phase = DeploymentPhase.FINAL_VALIDATION
        console.print("  [yellow]Final validation (not yet implemented)[/]")

        self.state.phase = DeploymentPhase.COMPLETE
        console.print("\n[bold green]Deployment complete.[/]")
        return self.state
