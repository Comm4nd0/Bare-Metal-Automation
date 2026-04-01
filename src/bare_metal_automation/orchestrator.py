"""Orchestrator — sequences the deployment phases and manages state."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from bare_metal_automation.common.checkpoint import (
    DEFAULT_CHECKPOINT_PATH,
    deserialize_state,
    load_checkpoint,
    remove_checkpoint,
    save_checkpoint,
)
from bare_metal_automation.drivers import DriverRegistry, load_builtin_drivers
from bare_metal_automation.models import (
    DeploymentInventory,
    DeploymentPhase,
    DeploymentState,
    DeviceState,
    DiscoveredDevice,
)

# Ensure built-in drivers are registered
load_builtin_drivers()

logger = logging.getLogger(__name__)
console = Console()

# Phases in execution order, used to determine which phases to skip on resume.
PHASE_ORDER: list[DeploymentPhase] = [
    DeploymentPhase.PRE_FLIGHT,
    DeploymentPhase.DISCOVERY,
    DeploymentPhase.TOPOLOGY,
    DeploymentPhase.CABLING_VALIDATION,
    DeploymentPhase.FIRMWARE_UPGRADE,
    DeploymentPhase.HEAVY_TRANSFERS,
    DeploymentPhase.NETWORK_CONFIG,
    DeploymentPhase.LAPTOP_PIVOT,
    DeploymentPhase.SERVER_PROVISION,
    DeploymentPhase.NTP_PROVISION,
    DeploymentPhase.POST_INSTALL,
    DeploymentPhase.FINAL_VALIDATION,
    DeploymentPhase.FACTORY_RESET,
    DeploymentPhase.COMPLETE,
]


class Orchestrator:
    """Central controller that drives the deployment through its phases."""

    def __init__(
        self,
        inventory_path: str,
        ssh_timeout: int = 30,
        checkpoint_path: str | Path = DEFAULT_CHECKPOINT_PATH,
        stop_event: threading.Event | None = None,
        on_phase_change: Callable[[str], None] | None = None,
        on_device_discovered: Callable[[DiscoveredDevice], None] | None = None,
        on_device_change: Callable[[DiscoveredDevice, str], None] | None = None,
    ) -> None:
        self.inventory_path = Path(inventory_path)
        self.ssh_timeout = ssh_timeout
        self.checkpoint_path = Path(checkpoint_path)
        self.stop_event = stop_event
        self.on_phase_change = on_phase_change
        # Called once per device after discovery + inventory matching
        self.on_device_discovered = on_device_discovered
        # Called whenever a device's state changes: (device, message)
        self.on_device_change = on_device_change
        self.state = DeploymentState()
        self.inventory: DeploymentInventory | None = None

    # ── Stop / checkpoint helpers ─────────────────────────────────────────

    def _check_stop(self) -> bool:
        """Return True if an external stop has been requested."""
        return self.stop_event is not None and self.stop_event.is_set()

    def _save_checkpoint(self) -> None:
        """Persist current state to the checkpoint file."""
        save_checkpoint(
            state=self.state,
            inventory_path=str(self.inventory_path),
            ssh_timeout=self.ssh_timeout,
            checkpoint_path=self.checkpoint_path,
        )
        console.print(
            f"  [dim]Checkpoint saved (phase: {self.state.phase.value})[/]"
        )
        if self.on_phase_change is not None:
            self.on_phase_change(self.state.phase.value)

    def _remove_checkpoint(self) -> None:
        """Delete the checkpoint file on successful completion."""
        remove_checkpoint(self.checkpoint_path)

    def _emit_device_change(
        self, device: DiscoveredDevice, message: str = ""
    ) -> None:
        """Fire the on_device_change callback if one is registered."""
        if self.on_device_change is not None:
            try:
                self.on_device_change(device, message)
            except Exception as e:
                logger.debug(f"on_device_change callback raised: {e}")

    def _emit_device_discovered(self, device: DiscoveredDevice) -> None:
        """Fire the on_device_discovered callback if one is registered."""
        if self.on_device_discovered is not None:
            try:
                self.on_device_discovered(device)
            except Exception as e:
                logger.debug(f"on_device_discovered callback raised: {e}")

    def _phase_index(self, phase: DeploymentPhase) -> int:
        """Return the position of a phase in PHASE_ORDER."""
        try:
            return PHASE_ORDER.index(phase)
        except ValueError:
            return -1

    def _should_skip(self, phase: DeploymentPhase, resume_after: DeploymentPhase) -> bool:
        """Return True if *phase* was already completed in the checkpoint."""
        return self._phase_index(phase) <= self._phase_index(resume_after)

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str | Path = DEFAULT_CHECKPOINT_PATH,
        stop_event: threading.Event | None = None,
        on_phase_change: Callable[[str], None] | None = None,
        on_device_discovered: Callable[[DiscoveredDevice], None] | None = None,
        on_device_change: Callable[[DiscoveredDevice, str], None] | None = None,
    ) -> Orchestrator:
        """Create an Orchestrator pre-loaded with state from a checkpoint.

        The returned orchestrator is ready to call ``run_full_deployment``
        with ``resume=True``, which will skip phases that already completed.
        """
        data = load_checkpoint(checkpoint_path)
        orch = cls(
            inventory_path=data["inventory_path"],
            ssh_timeout=data.get("ssh_timeout", 30),
            checkpoint_path=checkpoint_path,
            stop_event=stop_event,
            on_phase_change=on_phase_change,
            on_device_discovered=on_device_discovered,
            on_device_change=on_device_change,
        )
        orch.state = deserialize_state(data)

        console.print(
            f"[bold]Resumed from checkpoint[/] — "
            f"phase [cyan]{data['phase']}[/], "
            f"saved at {data.get('saved_at', 'unknown')}"
        )
        console.print(
            f"  {len(orch.state.discovered_devices)} devices, "
            f"{len(orch.state.topology_order)} in topology order"
        )
        return orch

    # ── Inventory ──────────────────────────────────────────────────────────

    def _load_inventory(self) -> DeploymentInventory:
        """Load and validate the inventory YAML."""
        from bare_metal_automation.inventory import load_inventory

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

        from bare_metal_automation.discovery.engine import DiscoveryEngine

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

        # Notify dashboard of each discovered device
        for device in self.state.discovered_devices.values():
            self._emit_device_discovered(device)

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
        from bare_metal_automation.topology.builder import TopologyBuilder

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

        from bare_metal_automation.cabling.validator import CablingValidator

        console.print("\n[bold]Phase 2b — Cabling Validation[/]")

        validator = CablingValidator(inventory=self.inventory)
        self.state.cabling_results = validator.validate_all(self.state.discovered_devices)

        validator.print_report(self.state.cabling_results)
        return self.state

    def _get_devices_by_category(
        self, category: str, *, topology_ordered: bool = False
    ) -> list[DiscoveredDevice]:
        """Return discovered devices matching a driver *category*.

        Args:
            category: One of ``"network"``, ``"server"``, ``"appliance"``.
            topology_ordered: If True, return network devices in topology
                order (outside-in); otherwise return in inventory order.
        """
        if topology_ordered:
            # Walk topology order and filter by category
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
                if DriverRegistry.device_category(platform) == category:
                    devices.append(device)
            return devices

        devices = []
        for serial, spec in self.inventory.devices.items():
            platform = spec.get("platform", "")
            if DriverRegistry.device_category(platform) != category:
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

    def _get_network_devices(self) -> list[DiscoveredDevice]:
        """Return discovered network devices in topology order."""
        return self._get_devices_by_category("network", topology_ordered=True)

    def _get_server_devices(self) -> list[DiscoveredDevice]:
        """Return discovered server devices."""
        return self._get_devices_by_category("server")

    def _get_appliance_devices(self) -> list[DiscoveredDevice]:
        """Return discovered appliance devices."""
        return self._get_devices_by_category("appliance")

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

        from bare_metal_automation.common.parallel import run_parallel_by_depth
        from bare_metal_automation.configurator.network import NetworkConfigurator

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
                self._emit_device_change(device, "Network configuration applied")
                console.print(
                    f"  [green]✓ {device.intended_hostname} configured[/]"
                )
            elif key in results:
                device.state = DeviceState.FAILED
                self._emit_device_change(device, "Configuration failed")
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

        from bare_metal_automation.common.parallel import run_parallel_by_depth
        from bare_metal_automation.configurator.firmware import FirmwareConfigurator

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
                self._emit_device_change(device, "Firmware upgraded")
                console.print(
                    f"  [green]✓ {device.intended_hostname} "
                    f"firmware upgraded[/]"
                )
            elif key in results:
                device.state = DeviceState.FAILED
                self._emit_device_change(device, "Firmware upgrade failed")
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
        """Provision servers via their registered driver (e.g. Redfish for HPE).

        All servers are provisioned in parallel — they are independent
        devices accessed via BMC and don't sit on each other's paths.
        """
        if not self.inventory:
            self.inventory = self._load_inventory()

        from bare_metal_automation.common.parallel import run_independent_parallel

        console.print(
            "\n[bold]Server Provisioning (parallel)[/]"
        )
        self.state.phase = DeploymentPhase.SERVER_PROVISION

        servers = self._get_server_devices()

        if not servers:
            console.print("  No servers to provision")
            return self.state

        for device in servers:
            device.state = DeviceState.PROVISIONING

        def _provision(device: DiscoveredDevice) -> bool:
            spec = self.inventory.get_device_spec(device.serial) or {}
            platform = spec.get("platform", "")
            driver = DriverRegistry.get_server_driver(
                platform, inventory=self.inventory
            )
            if driver is None:
                logger.error("No server driver for platform: %s", platform)
                return False
            return driver.provision_server(device)

        results = run_independent_parallel(
            devices=servers,
            operation=_provision,
            max_workers=len(servers),
        )

        for device in servers:
            key = device.serial or device.ip
            if results.get(key):
                self._emit_device_change(device, "Server provisioned")
                console.print(
                    f"  [green]✓ {device.intended_hostname} "
                    f"provisioned[/]"
                )
            else:
                device.state = DeviceState.FAILED
                self._emit_device_change(device, "Server provisioning failed")
                console.print(
                    f"  [red]✗ {device.intended_hostname} "
                    f"provisioning failed[/]"
                )
                self.state.errors.append(
                    f"Server provisioning failed for "
                    f"{device.intended_hostname}"
                )

        return self.state

    def run_appliance_provisioning(self) -> DeploymentState:
        """Provision appliance devices (NTP, etc.) via their registered driver.

        All appliances run in parallel — they are independent
        devices with their own management interfaces.
        """
        if not self.inventory:
            self.inventory = self._load_inventory()

        from bare_metal_automation.common.parallel import run_independent_parallel

        console.print(
            "\n[bold]Appliance Provisioning (parallel)[/]"
        )
        self.state.phase = DeploymentPhase.NTP_PROVISION

        appliances = self._get_appliance_devices()

        if not appliances:
            console.print("  No appliance devices to provision")
            return self.state

        for device in appliances:
            device.state = DeviceState.PROVISIONING

        def _provision(device: DiscoveredDevice) -> bool:
            spec = self.inventory.get_device_spec(device.serial) or {}
            platform = spec.get("platform", "")
            driver = DriverRegistry.get_appliance_driver(
                platform, inventory=self.inventory
            )
            if driver is None:
                logger.error("No appliance driver for platform: %s", platform)
                return False
            return driver.provision_device(device)

        results = run_independent_parallel(
            devices=appliances,
            operation=_provision,
            max_workers=len(appliances),
        )

        for device in appliances:
            key = device.serial or device.ip
            if results.get(key):
                self._emit_device_change(device, "Appliance provisioned")
                console.print(
                    f"  [green]✓ {device.intended_hostname} "
                    f"provisioned[/]"
                )
            else:
                device.state = DeviceState.FAILED
                self._emit_device_change(device, "Appliance provisioning failed")
                console.print(
                    f"  [red]✗ {device.intended_hostname} "
                    f"provisioning failed[/]"
                )
                self.state.errors.append(
                    f"Appliance provisioning failed for "
                    f"{device.intended_hostname}"
                )

        return self.state

    # Backward-compatible alias
    run_ntp_provisioning = run_appliance_provisioning

    def run_factory_reset(
        self,
        dry_run: bool = False,
        device_types: str = "all",
    ) -> DeploymentState:
        """Factory-reset infrastructure back to a ZTP-ready state.

        Resets devices in an order that preserves management connectivity:
        1. Appliance devices (parallel — leaf devices)
        2. Servers (parallel — independent via BMC)
        3. Network devices (inside-out by ascending BFS depth)

        Args:
            dry_run: Show what would be reset without executing.
            device_types: Filter: ``"all"``, ``"network"``, ``"server"``,
                ``"appliance"``, or a legacy value like ``"cisco"``.
        """
        # Support legacy device_types values
        _legacy_map = {
            "cisco": "network",
            "hpe": "server",
            "meinberg": "appliance",
        }
        device_types = _legacy_map.get(device_types, device_types)

        self.inventory = self._load_inventory()
        self.state.phase = DeploymentPhase.FACTORY_RESET

        console.print("\n[bold red]Factory Reset — returning infrastructure to ZTP-ready state[/]")

        # Discovery and topology are needed for device IPs and ordering
        self.run_discovery()
        self.run_topology()

        reset_appliance = device_types in ("all", "appliance")
        reset_server = device_types in ("all", "server")
        reset_network = device_types in ("all", "network")

        errors: list[str] = []

        # ── Phase 1: Appliance devices ────────────────────────────────
        if reset_appliance:
            appliances = self._get_appliance_devices()
            if appliances:
                if dry_run:
                    for d in appliances:
                        console.print(
                            f"  [yellow]DRY RUN — would reset "
                            f"{d.intended_hostname or d.ip}[/]"
                        )
                else:
                    appliance_errors = self._reset_appliance_devices(appliances)
                    errors.extend(appliance_errors)
            else:
                console.print("  [dim]No appliance devices to reset[/]")

        # ── Phase 2: Servers ──────────────────────────────────────────
        if reset_server:
            servers = self._get_server_devices()
            if servers:
                if dry_run:
                    for d in servers:
                        console.print(
                            f"  [yellow]DRY RUN — would reset "
                            f"{d.intended_hostname or d.ip}[/]"
                        )
                else:
                    server_errors = self._reset_server_devices(servers)
                    errors.extend(server_errors)
            else:
                console.print("  [dim]No servers to reset[/]")

        # ── Phase 3: Network devices (inside-out) ─────────────────────
        if reset_network:
            network_devices = self._get_network_devices()
            if network_devices:
                if dry_run:
                    sorted_devs = sorted(
                        network_devices,
                        key=lambda d: d.bfs_depth if d.bfs_depth is not None else 999,
                    )
                    for d in sorted_devs:
                        console.print(
                            f"  [yellow]DRY RUN — would reset "
                            f"{d.intended_hostname or d.ip} "
                            f"(depth {d.bfs_depth})[/]"
                        )
                else:
                    network_errors = self._reset_network_devices(network_devices)
                    errors.extend(network_errors)
            else:
                console.print("  [dim]No network devices to reset[/]")

        # ── Summary ───────────────────────────────────────────────────
        self.state.errors.extend(errors)

        if errors:
            console.print(f"\n[bold red]Factory reset completed with {len(errors)} error(s):[/]")
            for err in errors:
                console.print(f"  [red]— {err}[/]")
            self.state.phase = DeploymentPhase.FAILED
        elif dry_run:
            console.print("\n[bold yellow]DRY RUN complete — no changes made.[/]")
        else:
            console.print(
                "\n[bold green]Factory reset complete "
                "— all devices returned to defaults.[/]"
            )
            self.state.phase = DeploymentPhase.COMPLETE

        return self.state

    def _reset_appliance_devices(
        self, devices: list[DiscoveredDevice]
    ) -> list[str]:
        """Reset appliance devices in parallel via their registered driver."""
        from bare_metal_automation.common.parallel import run_independent_parallel

        console.print("\n[bold]Resetting Appliance Devices (parallel)[/]")

        def _reset(device: DiscoveredDevice) -> bool:
            spec = self.inventory.get_device_spec(device.serial) or {}
            platform = spec.get("platform", "")
            driver = DriverRegistry.get_appliance_driver(
                platform, inventory=self.inventory
            )
            if driver is None:
                logger.error("No appliance driver for platform: %s", platform)
                return False
            return driver.reset_device(device)

        results = run_independent_parallel(
            devices=devices,
            operation=_reset,
            max_workers=len(devices),
        )

        errors = []
        for device in devices:
            key = device.serial or device.ip
            hostname = device.intended_hostname or device.ip
            if results.get(key):
                console.print(f"  [green]\u2713 {hostname} reset[/]")
            else:
                console.print(f"  [red]\u2717 {hostname} reset failed[/]")
                errors.append(f"Factory reset failed for {hostname}")
        return errors

    def _reset_server_devices(
        self, devices: list[DiscoveredDevice]
    ) -> list[str]:
        """Reset servers in parallel via their registered driver."""
        from bare_metal_automation.common.parallel import run_independent_parallel

        console.print("\n[bold]Resetting Servers (parallel)[/]")

        def _reset(device: DiscoveredDevice) -> bool:
            spec = self.inventory.get_device_spec(device.serial) or {}
            platform = spec.get("platform", "")
            driver = DriverRegistry.get_server_driver(
                platform, inventory=self.inventory
            )
            if driver is None:
                logger.error("No server driver for platform: %s", platform)
                return False
            return driver.reset_server(device)

        results = run_independent_parallel(
            devices=devices,
            operation=_reset,
            max_workers=len(devices),
        )

        errors = []
        for device in devices:
            key = device.serial or device.ip
            hostname = device.intended_hostname or device.ip
            if results.get(key):
                console.print(f"  [green]\u2713 {hostname} reset[/]")
            else:
                console.print(f"  [red]\u2717 {hostname} reset failed[/]")
                errors.append(f"Factory reset failed for {hostname}")
        return errors

    def _reset_network_devices(
        self, devices: list[DiscoveredDevice]
    ) -> list[str]:
        """Reset network devices in inside-out order (ascending depth) via their driver."""
        from bare_metal_automation.common.parallel import run_parallel_by_depth_ascending

        console.print("\n[bold]Resetting Network Devices (inside-out by depth)[/]")

        def _reset(device: DiscoveredDevice) -> bool:
            spec = self.inventory.get_device_spec(device.serial) or {}
            platform = spec.get("platform", "")
            driver = DriverRegistry.get_network_driver(
                platform, inventory=self.inventory, ssh_timeout=self.ssh_timeout
            )
            if driver is None:
                logger.error("No network driver for platform: %s", platform)
                return False
            return driver.reset_device(device)

        results = run_parallel_by_depth_ascending(
            devices=devices,
            operation=_reset,
            max_workers=4,
            stop_on_failure=True,
        )

        errors = []
        for device in devices:
            key = device.serial or device.ip
            hostname = device.intended_hostname or device.ip
            if results.get(key):
                console.print(f"  [green]\u2713 {hostname} reset[/]")
            elif key in results:
                console.print(f"  [red]\u2717 {hostname} reset failed[/]")
                errors.append(f"Factory reset failed for {hostname}")
        return errors

    def run_full_deployment(
        self,
        dry_run: bool = False,
        resume: bool = False,
    ) -> DeploymentState:
        """Execute all phases in sequence.

        When *resume* is True the orchestrator skips phases that were
        already completed according to the current ``self.state.phase``.
        This allows a deployment that was interrupted (Ctrl-C, power loss,
        error) to pick up where it left off.

        A checkpoint file is written after every phase transition so that
        progress is never lost.
        """
        self.inventory = self._load_inventory()

        # The phase recorded in state is the *last completed* phase when
        # resuming.  We use it to decide which phases to skip.
        resume_after = self.state.phase if resume else DeploymentPhase.PRE_FLIGHT

        if resume:
            console.print(
                f"\n[bold yellow]Resuming deployment after "
                f"phase: {resume_after.value}[/]"
            )

        # ── Phase 1: Discovery ─────────────────────────────────────────
        if not self._should_skip(DeploymentPhase.DISCOVERY, resume_after):
            self.run_discovery()
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

        if not self._should_skip(DeploymentPhase.TOPOLOGY, resume_after):
            self.run_topology()
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

        if not self._should_skip(DeploymentPhase.CABLING_VALIDATION, resume_after):
            self.run_validation()
            self.state.phase = DeploymentPhase.CABLING_VALIDATION
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

        if self.state.has_blocking_errors:
            console.print("\n[bold red]Deployment blocked by errors. Review above.[/]")
            self.state.phase = DeploymentPhase.FAILED
            self._save_checkpoint()
            return self.state

        if dry_run:
            console.print("\n[bold yellow]DRY RUN — stopping before configuration.[/]")
            return self.state

        # ── Phase 2: Firmware upgrade ──────────────────────────────────
        if not self._should_skip(DeploymentPhase.FIRMWARE_UPGRADE, resume_after):
            self.run_firmware_upgrade()
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

            if self.state.has_blocking_errors:
                console.print("\n[bold red]Deployment blocked by firmware errors.[/]")
                self.state.phase = DeploymentPhase.FAILED
                self._save_checkpoint()
                return self.state

        # ── Phase 3: Heavy transfers ───────────────────────────────────
        if not self._should_skip(DeploymentPhase.HEAVY_TRANSFERS, resume_after):
            console.print("\n[bold]Phase 3 — Heavy Transfers[/]")
            self.state.phase = DeploymentPhase.HEAVY_TRANSFERS
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

        # ── Phase 4: Network configuration ─────────────────────────────
        if not self._should_skip(DeploymentPhase.NETWORK_CONFIG, resume_after):
            self.run_network_config()
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

            if self.state.has_blocking_errors:
                console.print(
                    "\n[bold red]Deployment blocked by network config errors.[/]"
                )
                self.state.phase = DeploymentPhase.FAILED
                self._save_checkpoint()
                return self.state

        # ── Phase 5: Laptop pivot ──────────────────────────────────────
        if not self._should_skip(DeploymentPhase.LAPTOP_PIVOT, resume_after):
            console.print("\n[bold]Phase 5 — Laptop Pivot[/]")
            self.state.phase = DeploymentPhase.LAPTOP_PIVOT
            console.print("  [yellow]Reconfigure laptop NIC to management VLAN[/]")
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

        # ── Phase 6: Server provisioning ───────────────────────────────
        if not self._should_skip(DeploymentPhase.SERVER_PROVISION, resume_after):
            self.run_server_provisioning()
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

        # ── Phase 7: NTP provisioning ──────────────────────────────────
        if not self._should_skip(DeploymentPhase.NTP_PROVISION, resume_after):
            self.run_ntp_provisioning()
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

        # ── Phase 8: Post-install ──────────────────────────────────────
        if not self._should_skip(DeploymentPhase.POST_INSTALL, resume_after):
            console.print("\n[bold]Phase 8 — Post-Install[/]")
            self.state.phase = DeploymentPhase.POST_INSTALL
            console.print("  [yellow]Post-install tasks (not yet implemented)[/]")
            self._save_checkpoint()
            if self._check_stop():
                console.print("\n[bold yellow]Deployment stopped by user.[/]")
                return self.state

        # ── Phase 9: Final validation ──────────────────────────────────
        if not self._should_skip(DeploymentPhase.FINAL_VALIDATION, resume_after):
            console.print("\n[bold]Phase 9 — Final Validation[/]")
            self.state.phase = DeploymentPhase.FINAL_VALIDATION
            console.print("  [yellow]Final validation (not yet implemented)[/]")
            self._save_checkpoint()

        self.state.phase = DeploymentPhase.COMPLETE
        console.print("\n[bold green]Deployment complete.[/]")

        # Clean up checkpoint on success — nothing left to resume.
        self._remove_checkpoint()

        return self.state
