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
from bare_metal_automation.models import (
    DeploymentInventory,
    DeploymentPhase,
    DeploymentState,
    DeviceState,
    DiscoveredDevice,
)

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
    ) -> None:
        self.inventory_path = Path(inventory_path)
        self.ssh_timeout = ssh_timeout
        self.checkpoint_path = Path(checkpoint_path)
        self.stop_event = stop_event
        self.on_phase_change = on_phase_change
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

        from bare_metal_automation.common.parallel import run_independent_parallel
        from bare_metal_automation.provisioner.server import HPEServerProvisioner

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

        from bare_metal_automation.common.parallel import run_independent_parallel
        from bare_metal_automation.provisioner.meinberg import MeinbergProvisioner

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

    def run_factory_reset(
        self,
        dry_run: bool = False,
        device_types: str = "all",
    ) -> DeploymentState:
        """Factory-reset infrastructure back to a ZTP-ready state.

        Resets devices in an order that preserves management connectivity:
        1. Meinberg NTP devices (parallel — leaf devices)
        2. HPE servers (parallel — independent via iLO)
        3. Cisco network devices (inside-out by ascending BFS depth)

        Args:
            dry_run: Show what would be reset without executing.
            device_types: Filter: "all", "cisco", "hpe", or "meinberg".
        """
        self.inventory = self._load_inventory()
        self.state.phase = DeploymentPhase.FACTORY_RESET

        console.print("\n[bold red]Factory Reset — returning infrastructure to ZTP-ready state[/]")

        # Discovery and topology are needed for device IPs and ordering
        self.run_discovery()
        self.run_topology()

        reset_cisco = device_types in ("all", "cisco")
        reset_hpe = device_types in ("all", "hpe")
        reset_meinberg = device_types in ("all", "meinberg")

        errors: list[str] = []

        # ── Phase 1: Meinberg NTP ─────────────────────────────────────
        if reset_meinberg:
            ntp_devices = self._get_devices_by_platform("meinberg_lantime")
            if ntp_devices:
                if dry_run:
                    for d in ntp_devices:
                        console.print(
                            f"  [yellow]DRY RUN — would reset "
                            f"{d.intended_hostname or d.ip}[/]"
                        )
                else:
                    ntp_errors = self._reset_meinberg_devices(ntp_devices)
                    errors.extend(ntp_errors)
            else:
                console.print("  [dim]No Meinberg NTP devices to reset[/]")

        # ── Phase 2: HPE servers ──────────────────────────────────────
        if reset_hpe:
            servers = self._get_devices_by_platform_prefix("hpe_")
            if servers:
                if dry_run:
                    for d in servers:
                        console.print(
                            f"  [yellow]DRY RUN — would reset "
                            f"{d.intended_hostname or d.ip}[/]"
                        )
                else:
                    server_errors = self._reset_hpe_servers(servers)
                    errors.extend(server_errors)
            else:
                console.print("  [dim]No HPE servers to reset[/]")

        # ── Phase 3: Cisco network devices (inside-out) ───────────────
        if reset_cisco:
            network_devices = self._get_network_devices()
            if network_devices:
                if dry_run:
                    # Show in inside-out order (ascending depth)
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
                    cisco_errors = self._reset_network_devices(network_devices)
                    errors.extend(cisco_errors)
            else:
                console.print("  [dim]No Cisco network devices to reset[/]")

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

    def _reset_meinberg_devices(
        self, devices: list[DiscoveredDevice]
    ) -> list[str]:
        """Reset Meinberg NTP devices in parallel."""
        from bare_metal_automation.common.parallel import run_independent_parallel
        from bare_metal_automation.resetter.meinberg import MeinbergResetter

        console.print("\n[bold]Resetting Meinberg NTP Devices (parallel)[/]")
        resetter = MeinbergResetter(inventory=self.inventory)

        results = run_independent_parallel(
            devices=devices,
            operation=resetter.reset_device,
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

    def _reset_hpe_servers(
        self, devices: list[DiscoveredDevice]
    ) -> list[str]:
        """Reset HPE servers in parallel."""
        from bare_metal_automation.common.parallel import run_independent_parallel
        from bare_metal_automation.resetter.server import HPEServerResetter

        console.print("\n[bold]Resetting HPE Servers (parallel)[/]")
        resetter = HPEServerResetter(inventory=self.inventory)

        results = run_independent_parallel(
            devices=devices,
            operation=resetter.reset_server,
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
        """Reset Cisco network devices in inside-out order (ascending depth)."""
        from bare_metal_automation.common.parallel import run_parallel_by_depth_ascending
        from bare_metal_automation.resetter.network import NetworkResetter

        console.print("\n[bold]Resetting Network Devices (inside-out by depth)[/]")
        resetter = NetworkResetter(
            inventory=self.inventory,
            ssh_timeout=self.ssh_timeout,
        )

        results = run_parallel_by_depth_ascending(
            devices=devices,
            operation=resetter.reset_device,
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
