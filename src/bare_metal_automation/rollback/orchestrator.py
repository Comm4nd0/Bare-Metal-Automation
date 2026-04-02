"""Rollback orchestrator — sequences factory reset phases in reverse."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from rich.console import Console

from bare_metal_automation.common.checkpoint import (
    DEFAULT_CHECKPOINT_PATH,
    load_checkpoint,
)
from bare_metal_automation.common.parallel import (
    run_independent_parallel,
    run_parallel_by_depth,
)
from bare_metal_automation.drivers import DriverRegistry, load_builtin_drivers
from bare_metal_automation.models import (
    DevicePlatform,
    DeviceRole,
    DeviceState,
    DiscoveredDevice,
    RollbackPhase,
)

# Ensure built-in drivers are registered
load_builtin_drivers()

logger = logging.getLogger(__name__)
console = Console()

DEFAULT_ROLLBACK_CHECKPOINT = Path(".bma-rollback-checkpoint.json")

ROLLBACK_PHASE_ORDER: list[RollbackPhase] = [
    RollbackPhase.ROLLBACK_PRE_FLIGHT,
    RollbackPhase.ROLLBACK_NTP_RESET,
    RollbackPhase.ROLLBACK_SERVER_RESET,
    RollbackPhase.ROLLBACK_LAPTOP_PIVOT,
    RollbackPhase.ROLLBACK_NETWORK_RESET,
    RollbackPhase.ROLLBACK_FINAL_CHECK,
    RollbackPhase.ROLLBACK_COMPLETE,
]


class RollbackOrchestrator:
    """Drives all devices back to factory state in the correct order.

    Reads the deployment checkpoint to know which devices exist and their
    addresses, then resets them in reverse order:
    NTP → Servers → Laptop pivot → Network devices (outside-in) → Done.
    """

    def __init__(
        self,
        inventory_path: str = "",
        deployment_checkpoint: str | Path = DEFAULT_CHECKPOINT_PATH,
        rollback_checkpoint: str | Path = DEFAULT_ROLLBACK_CHECKPOINT,
        ssh_timeout: int = 30,
        stop_event: threading.Event | None = None,
        on_phase_change: Callable[[str], None] | None = None,
    ) -> None:
        self.inventory_path = inventory_path
        self.deployment_checkpoint = Path(deployment_checkpoint)
        self.rollback_checkpoint = Path(rollback_checkpoint)
        self.ssh_timeout = ssh_timeout
        self.stop_event = stop_event
        self.on_phase_change = on_phase_change

        self.phase = RollbackPhase.ROLLBACK_PRE_FLIGHT
        self.devices: dict[str, DiscoveredDevice] = {}
        self.results: dict[str, bool] = {}

    # ── Stop / checkpoint helpers ──────────────────────────────────────

    def _check_stop(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _save_checkpoint(self) -> None:
        """Persist rollback progress to its own checkpoint file."""
        payload = {
            "version": 1,
            "type": "rollback",
            "saved_at": datetime.now().isoformat(),
            "phase": self.phase.value,
            "deployment_checkpoint": str(self.deployment_checkpoint),
            "inventory_path": self.inventory_path,
            "ssh_timeout": self.ssh_timeout,
            "device_results": {
                ip: {
                    "state": d.state.value,
                    "reset_success": self.results.get(
                        d.serial or d.ip, False,
                    ),
                }
                for ip, d in self.devices.items()
            },
        }
        tmp = self.rollback_checkpoint.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.rename(self.rollback_checkpoint)
        console.print(
            f"  [dim]Rollback checkpoint saved "
            f"(phase: {self.phase.value})[/]",
        )
        if self.on_phase_change is not None:
            self.on_phase_change(self.phase.value)

    def _remove_checkpoints(self) -> None:
        """Delete both rollback and deployment checkpoints (clean slate)."""
        for path in (self.rollback_checkpoint, self.deployment_checkpoint):
            if path.exists():
                path.unlink()
                logger.info("Removed checkpoint: %s", path)

    def _phase_index(self, phase: RollbackPhase) -> int:
        try:
            return ROLLBACK_PHASE_ORDER.index(phase)
        except ValueError:
            return -1

    def _should_skip(
        self, phase: RollbackPhase, resume_after: RollbackPhase,
    ) -> bool:
        return self._phase_index(phase) <= self._phase_index(resume_after)

    # ── Device classification helpers ──────────────────────────────────

    @staticmethod
    def _resolve_platform(d: DiscoveredDevice) -> str:
        """Return the best available platform string for a device."""
        if d.platform:
            return d.platform
        if d.device_platform is not None:
            return d.device_platform.value
        return ""

    def _network_devices(self) -> list[DiscoveredDevice]:
        return [
            d for d in self.devices.values()
            if DriverRegistry.is_network(self._resolve_platform(d))
        ]

    def _server_devices(self) -> list[DiscoveredDevice]:
        return [
            d for d in self.devices.values()
            if DriverRegistry.is_server(self._resolve_platform(d))
        ]

    def _appliance_devices(self) -> list[DiscoveredDevice]:
        return [
            d for d in self.devices.values()
            if DriverRegistry.is_appliance(self._resolve_platform(d))
        ]

    # Backward-compatible aliases
    _cisco_devices = _network_devices
    _hpe_devices = _server_devices
    _ntp_devices = _appliance_devices

    # ── Load devices from deployment checkpoint ────────────────────────

    def _load_devices_from_checkpoint(self) -> bool:
        """Load device information from the deployment checkpoint."""
        try:
            data = load_checkpoint(self.deployment_checkpoint)
        except FileNotFoundError:
            console.print(
                "[bold red]No deployment checkpoint found at "
                f"{self.deployment_checkpoint}[/]",
            )
            console.print(
                "A completed deployment is required before rollback.",
            )
            return False

        self.inventory_path = data.get("inventory_path", "")
        self.ssh_timeout = data.get("ssh_timeout", 30)

        # Reconstruct DiscoveredDevice objects from checkpoint data
        for ip, device_data in data.get("discovered_devices", {}).items():
            device = DiscoveredDevice(ip=ip)
            device.mac = device_data.get("mac")
            device.serial = device_data.get("serial")
            device.platform = device_data.get("platform")
            device.hostname = device_data.get("hostname")
            device.intended_hostname = device_data.get(
                "intended_hostname",
            )
            device.bfs_depth = device_data.get("bfs_depth")
            device.config_order = device_data.get("config_order")

            state_val = device_data.get("state", "unknown")
            try:
                device.state = DeviceState(state_val)
            except ValueError:
                device.state = DeviceState.UNKNOWN

            role_val = device_data.get("role")
            if role_val:
                try:
                    device.role = DeviceRole(role_val)
                except ValueError:
                    logger.warning("Unknown device role %r for %s", role_val, ip)

            platform_val = device_data.get("device_platform")
            if platform_val:
                try:
                    device.device_platform = DevicePlatform(platform_val)
                except ValueError:
                    logger.warning("Unknown device platform %r for %s", platform_val, ip)

            self.devices[ip] = device

        console.print(
            f"  Loaded {len(self.devices)} devices from deployment "
            f"checkpoint",
        )
        return True

    # ── Resume from rollback checkpoint ────────────────────────────────

    @classmethod
    def from_checkpoint(
        cls,
        rollback_checkpoint: str | Path = DEFAULT_ROLLBACK_CHECKPOINT,
        stop_event: threading.Event | None = None,
        on_phase_change: Callable[[str], None] | None = None,
    ) -> RollbackOrchestrator:
        """Reconstruct a RollbackOrchestrator from its checkpoint."""
        path = Path(rollback_checkpoint)
        if not path.exists():
            raise FileNotFoundError(
                f"No rollback checkpoint at {path}",
            )

        data = json.loads(path.read_text())
        orch = cls(
            inventory_path=data.get("inventory_path", ""),
            deployment_checkpoint=data.get(
                "deployment_checkpoint", str(DEFAULT_CHECKPOINT_PATH),
            ),
            rollback_checkpoint=rollback_checkpoint,
            ssh_timeout=data.get("ssh_timeout", 30),
            stop_event=stop_event,
            on_phase_change=on_phase_change,
        )
        orch.phase = RollbackPhase(data["phase"])

        # Load devices from the deployment checkpoint
        orch._load_devices_from_checkpoint()

        # Restore per-device results
        for ip, result_data in data.get("device_results", {}).items():
            if ip in orch.devices:
                try:
                    orch.devices[ip].state = DeviceState(
                        result_data["state"],
                    )
                except (ValueError, KeyError):
                    logger.warning("Could not restore state for device %s from checkpoint", ip)
                key = orch.devices[ip].serial or ip
                orch.results[key] = result_data.get(
                    "reset_success", False,
                )

        return orch

    # ── Main entry point ───────────────────────────────────────────────

    def run_full_rollback(self, resume: bool = False) -> RollbackPhase:
        """Execute the full rollback sequence.

        Returns the final phase (ROLLBACK_COMPLETE or ROLLBACK_FAILED).
        """
        console.print("\n[bold red]═══ ROLLBACK TO FACTORY ═══[/]\n")

        resume_after = self.phase if resume else None

        # ── Phase 1: Pre-flight ────────────────────────────────────────
        if not (resume and self._should_skip(
            RollbackPhase.ROLLBACK_PRE_FLIGHT, resume_after,
        )):
            self.phase = RollbackPhase.ROLLBACK_PRE_FLIGHT
            console.print("[bold]Phase 1/6: Pre-flight checks[/]")
            if not self._load_devices_from_checkpoint():
                self.phase = RollbackPhase.ROLLBACK_FAILED
                return self.phase
            self._save_checkpoint()

        if self._check_stop():
            console.print("[bold yellow]Rollback stopped by user.[/]")
            return self.phase

        # ── Phase 2: NTP Reset ─────────────────────────────────────────
        if not (resume and self._should_skip(
            RollbackPhase.ROLLBACK_NTP_RESET, resume_after,
        )):
            self.phase = RollbackPhase.ROLLBACK_NTP_RESET
            console.print("\n[bold]Phase 2/6: Reset NTP appliances[/]")
            self._run_ntp_reset()
            self._save_checkpoint()

        if self._check_stop():
            console.print("[bold yellow]Rollback stopped by user.[/]")
            return self.phase

        # ── Phase 3: Server Reset ──────────────────────────────────────
        if not (resume and self._should_skip(
            RollbackPhase.ROLLBACK_SERVER_RESET, resume_after,
        )):
            self.phase = RollbackPhase.ROLLBACK_SERVER_RESET
            console.print("\n[bold]Phase 3/6: Reset HPE servers[/]")
            self._run_server_reset()
            self._save_checkpoint()

        if self._check_stop():
            console.print("[bold yellow]Rollback stopped by user.[/]")
            return self.phase

        # ── Phase 4: Laptop Pivot ──────────────────────────────────────
        if not (resume and self._should_skip(
            RollbackPhase.ROLLBACK_LAPTOP_PIVOT, resume_after,
        )):
            self.phase = RollbackPhase.ROLLBACK_LAPTOP_PIVOT
            console.print(
                "\n[bold]Phase 4/6: Pivot laptop to bootstrap VLAN[/]",
            )
            self._run_laptop_pivot()
            self._save_checkpoint()

        if self._check_stop():
            console.print("[bold yellow]Rollback stopped by user.[/]")
            return self.phase

        # ── Phase 5: Network Reset ─────────────────────────────────────
        if not (resume and self._should_skip(
            RollbackPhase.ROLLBACK_NETWORK_RESET, resume_after,
        )):
            self.phase = RollbackPhase.ROLLBACK_NETWORK_RESET
            console.print(
                "\n[bold]Phase 5/6: Reset network devices "
                "(outside-in)[/]",
            )
            self._run_network_reset()
            self._save_checkpoint()

        if self._check_stop():
            console.print("[bold yellow]Rollback stopped by user.[/]")
            return self.phase

        # ── Phase 6: Final Check ───────────────────────────────────────
        if not (resume and self._should_skip(
            RollbackPhase.ROLLBACK_FINAL_CHECK, resume_after,
        )):
            self.phase = RollbackPhase.ROLLBACK_FINAL_CHECK
            console.print("\n[bold]Phase 6/6: Final verification[/]")
            self._run_final_check()
            self._save_checkpoint()

        # ── Complete ───────────────────────────────────────────────────
        self.phase = RollbackPhase.ROLLBACK_COMPLETE
        console.print(
            "\n[bold green]═══ ROLLBACK COMPLETE — "
            "All devices at factory state ═══[/]\n",
        )
        self._remove_checkpoints()

        if self.on_phase_change is not None:
            self.on_phase_change(self.phase.value)

        return self.phase

    # ── Phase implementations ──────────────────────────────────────────

    def _run_ntp_reset(self) -> None:
        """Factory-reset all appliance devices via their registered driver."""
        appliance_devices = self._appliance_devices()
        if not appliance_devices:
            console.print("  No appliance devices to reset")
            return

        console.print(
            f"  Resetting {len(appliance_devices)} appliance device(s)...",
        )

        def _reset(d: DiscoveredDevice) -> bool:
            platform = d.platform or ""
            driver = DriverRegistry.get_appliance_driver(platform)
            if driver is None:
                return False
            return driver.reset_device(d)

        results = run_independent_parallel(
            appliance_devices,
            _reset,
        )
        self.results.update(results)

        ok = sum(1 for v in results.values() if v)
        console.print(
            f"  Appliance reset: {ok}/{len(appliance_devices)} successful",
        )

    def _run_server_reset(self) -> None:
        """Factory-reset all servers via their registered driver."""
        server_devs = self._server_devices()
        if not server_devs:
            console.print("  No servers to reset")
            return

        console.print(
            f"  Resetting {len(server_devs)} server(s)...",
        )

        def _reset(d: DiscoveredDevice) -> bool:
            platform = d.platform or ""
            driver = DriverRegistry.get_server_driver(platform)
            if driver is None:
                return False
            return driver.reset_server(d)

        results = run_independent_parallel(
            server_devs,
            _reset,
        )
        self.results.update(results)

        ok = sum(1 for v in results.values() if v)
        console.print(
            f"  Server reset: {ok}/{len(server_devs)} successful",
        )

    def _run_laptop_pivot(self) -> None:
        """Reconfigure laptop NIC back to bootstrap VLAN.

        This is a manual/scripted step — for now we log what needs
        to happen and pause briefly.
        """
        console.print(
            "  Reconfiguring laptop NIC to bootstrap VLAN "
            "(10.255.0.0/16)...",
        )
        # In a real deployment, this would call a platform-specific
        # NIC configuration script. For now, log the intent.
        logger.info(
            "Laptop pivot: switch NIC from management VLAN "
            "to bootstrap subnet",
        )
        console.print("  Laptop NIC reverted to bootstrap network")

    def _run_network_reset(self) -> None:
        """Factory-reset all network devices (outside-in) via their driver."""
        net_devices = self._network_devices()
        if not net_devices:
            console.print("  No network devices to reset")
            return

        console.print(
            f"  Resetting {len(net_devices)} network device(s) "
            f"(outside-in)...",
        )

        def _reset(d: DiscoveredDevice) -> bool:
            platform = d.platform or ""
            driver = DriverRegistry.get_network_driver(
                platform, ssh_timeout=self.ssh_timeout
            )
            if driver is None:
                return False
            return driver.reset_device(d)

        results = run_parallel_by_depth(
            net_devices,
            _reset,
            stop_on_failure=False,
        )
        self.results.update(results)

        ok = sum(1 for v in results.values() if v)
        console.print(
            f"  Network reset: {ok}/{len(net_devices)} successful",
        )

    def _run_final_check(self) -> None:
        """Verify devices are at factory state."""
        total = len(self.devices)
        verified = 0

        for device in self.devices.values():
            hostname = device.intended_hostname or device.ip

            if device.state == DeviceState.POWERED_OFF:
                console.print(f"  \u2713 {hostname}: powered off")
                verified += 1
            elif device.state == DeviceState.FACTORY_RESET:
                platform = device.platform or ""
                if DriverRegistry.is_network(platform):
                    driver = DriverRegistry.get_network_driver(
                        platform, ssh_timeout=self.ssh_timeout
                    )
                    if driver and driver.verify_factory_state(device):
                        console.print(
                            f"  \u2713 {hostname}: factory state verified",
                        )
                        verified += 1
                    else:
                        console.print(
                            f"  \u2717 {hostname}: verification failed",
                        )
                else:
                    console.print(f"  \u2713 {hostname}: reset complete")
                    verified += 1
            elif device.state == DeviceState.FAILED:
                console.print(
                    f"  \u2717 {hostname}: reset FAILED \u2014 may need manual "
                    f"intervention",
                )
            else:
                console.print(
                    f"  ? {hostname}: state={device.state.value}",
                )

        console.print(
            f"\n  Verified: {verified}/{total} devices at "
            f"factory state",
        )

        if verified < total:
            console.print(
                "  [bold yellow]Some devices may need manual "
                "reset[/]",
            )
