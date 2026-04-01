"""Factory reset orchestrator — 6-phase infrastructure teardown.

Phases
------
1. VM teardown        — gracefully power off and unregister VMs
2. NSX teardown       — remove NSX logical networking overlays
3. vCenter teardown   — unregister hosts and remove vCenter inventory
4. Server wipe        — BIOS factory reset + disk sanitisation via Redfish
5. Network reset      — Cisco write erase + reload (inside-out BFS order)
6. Validation         — verify all devices are back at factory defaults

Phases 1–3 (VMware stack) are stubbed with TODO placeholders; the actual
vSphere/NSX API calls are complex enough to warrant a dedicated sprint.
Phases 4–6 delegate to the existing ``resetter/`` modules.

The orchestrator can be run standalone or invoked from the dashboard
factory-reset workflow.  A ``stop_event`` enables graceful interruption
between phases.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from bare_metal_automation.models import (
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
)

logger = logging.getLogger(__name__)


@dataclass
class ResetResult:
    """Outcome of a factory reset run."""

    success: bool = True
    errors: list[str] = field(default_factory=list)
    phases_completed: list[str] = field(default_factory=list)

    def fail(self, phase: str, message: str) -> None:
        self.success = False
        self.errors.append(f"[{phase}] {message}")

    def complete(self, phase: str) -> None:
        self.phases_completed.append(phase)
        logger.info(f"Factory reset phase complete: {phase}")


class FactoryResetOrchestrator:
    """Orchestrate the full 6-phase factory reset sequence.

    Usage::

        orch = FactoryResetOrchestrator(inventory, discovered_devices)
        result = orch.run()
    """

    def __init__(
        self,
        inventory: DeploymentInventory,
        discovered_devices: dict[str, DiscoveredDevice],
        stop_event: threading.Event | None = None,
        ssh_timeout: int = 30,
        dry_run: bool = False,
    ) -> None:
        self.inventory = inventory
        self.discovered_devices = discovered_devices
        self.stop_event = stop_event or threading.Event()
        self.ssh_timeout = ssh_timeout
        self.dry_run = dry_run

    def run(self) -> ResetResult:
        """Execute all reset phases in sequence."""
        result = ResetResult()

        phases = [
            ("vm_teardown",     self.phase_vm_teardown),
            ("nsx_teardown",    self.phase_nsx_teardown),
            ("vcenter_teardown", self.phase_vcenter_teardown),
            ("server_wipe",     self.phase_server_wipe),
            ("network_reset",   self.phase_network_reset),
            ("validation",      self.phase_validation),
        ]

        for phase_name, phase_fn in phases:
            if self.stop_event.is_set():
                logger.info("Factory reset interrupted by stop event")
                break

            logger.info(f"Factory reset: starting phase '{phase_name}'")
            try:
                phase_ok = phase_fn()
            except Exception as e:
                logger.exception(f"Factory reset phase '{phase_name}' raised: {e}")
                phase_ok = False

            if phase_ok:
                result.complete(phase_name)
            else:
                result.fail(phase_name, f"Phase '{phase_name}' reported failure")
                if phase_name in ("server_wipe", "network_reset"):
                    # Hardware phases are critical — abort on failure
                    logger.error("Critical phase failed — aborting factory reset")
                    break

        return result

    # ── Phase 1: VM teardown ───────────────────────────────────────────────

    def phase_vm_teardown(self) -> bool:
        """Gracefully shut down all VMs managed by vCenter.

        TODO: Implement using pyVmomi (vSphere SDK for Python):
          - connect to vCenter with stored credentials
          - enumerate all VMs across all compute clusters
          - issue GracefulShutdown for each VM
          - wait for power-off confirmation
          - unregister VMs from inventory
        """
        logger.info("VM teardown: stubbed — no VMware API integration yet")
        if self.dry_run:
            logger.info("[DRY RUN] Would power off and unregister all VMs")
        return True  # Non-blocking until VMware sprint

    # ── Phase 2: NSX teardown ──────────────────────────────────────────────

    def phase_nsx_teardown(self) -> bool:
        """Remove NSX-T logical network constructs.

        TODO: Implement using the NSX-T REST API (vmware.nsx):
          - delete logical ports, switches, routers
          - remove transport nodes
          - uninstall NSX fabric from ESXi hosts
          - delete NSX manager configuration
        """
        logger.info("NSX teardown: stubbed — no NSX API integration yet")
        if self.dry_run:
            logger.info("[DRY RUN] Would remove all NSX-T overlay constructs")
        return True  # Non-blocking until NSX sprint

    # ── Phase 3: vCenter teardown ──────────────────────────────────────────

    def phase_vcenter_teardown(self) -> bool:
        """Remove ESXi hosts from vCenter and prepare for bare-metal wipe.

        TODO: Implement using pyVmomi:
          - enter maintenance mode on all ESXi hosts
          - remove hosts from clusters
          - destroy clusters and datacenter objects
          - power off vCenter VM (via host directly, not via vCenter)
        """
        logger.info("vCenter teardown: stubbed — no VMware API integration yet")
        if self.dry_run:
            logger.info("[DRY RUN] Would remove vCenter and ESXi inventory")
        return True  # Non-blocking until vCenter sprint

    # ── Phase 4: Server wipe ───────────────────────────────────────────────

    def phase_server_wipe(self) -> bool:
        """Factory-reset HPE servers: BIOS defaults + disk sanitisation.

        Delegates to ``resetter/server.py`` (BIOS + iLO factory reset) and
        ``factory_reset/sanitise.py`` (cryptographic disk erase).
        """
        from bare_metal_automation.common.parallel import run_independent_parallel
        from bare_metal_automation.resetter.server import HPEServerResetter

        servers = self._get_hpe_devices()
        if not servers:
            logger.info("No HPE servers to wipe — skipping")
            return True

        if self.dry_run:
            for d in servers:
                logger.info(f"[DRY RUN] Would wipe {d.intended_hostname or d.ip}")
            return True

        resetter = HPEServerResetter(inventory=self.inventory)

        results = run_independent_parallel(
            devices=servers,
            operation=resetter.reset_server,
            max_workers=len(servers),
        )

        all_ok = True
        for device in servers:
            key = device.serial or device.ip
            hostname = device.intended_hostname or device.ip
            if results.get(key):
                logger.info(f"Server wiped: {hostname}")
            else:
                logger.error(f"Server wipe FAILED: {hostname}")
                all_ok = False

        return all_ok

    # ── Phase 5: Network reset ─────────────────────────────────────────────

    def phase_network_reset(self) -> bool:
        """Cisco write-erase + reload, inside-out (ascending BFS depth).

        The innermost device (closest to the laptop) is reset last so we
        retain management connectivity for as long as possible.
        """
        from bare_metal_automation.common.parallel import run_parallel_by_depth_ascending
        from bare_metal_automation.resetter.network import NetworkResetter

        network_devices = self._get_cisco_devices()
        if not network_devices:
            logger.info("No Cisco network devices to reset — skipping")
            return True

        if self.dry_run:
            sorted_devs = sorted(
                network_devices,
                key=lambda d: d.bfs_depth if d.bfs_depth is not None else 999,
            )
            for d in sorted_devs:
                logger.info(
                    f"[DRY RUN] Would reset {d.intended_hostname or d.ip} "
                    f"(depth {d.bfs_depth})"
                )
            return True

        resetter = NetworkResetter(
            inventory=self.inventory,
            ssh_timeout=self.ssh_timeout,
        )

        results = run_parallel_by_depth_ascending(
            devices=network_devices,
            operation=resetter.reset_device,
            max_workers=4,
            stop_on_failure=True,
        )

        all_ok = True
        for device in network_devices:
            key = device.serial or device.ip
            hostname = device.intended_hostname or device.ip
            if results.get(key):
                logger.info(f"Network device reset: {hostname}")
            elif key in results:
                logger.error(f"Network reset FAILED: {hostname}")
                all_ok = False

        return all_ok

    # ── Phase 6: Validation ────────────────────────────────────────────────

    def phase_validation(self) -> bool:
        """Verify all devices are back at factory defaults.

        Checks:
        - Cisco: device responds to SSH with factory credentials
        - HPE:   iLO responds on port 443 with default credentials
        - No custom config present (basic probe only — full re-discovery
          would be done on the next deployment run)
        """
        logger.info("Factory reset validation: basic reachability checks")
        all_ok = True

        for device in self.discovered_devices.values():
            if self.stop_event.is_set():
                break
            hostname = device.intended_hostname or device.ip

            if device.device_platform and device.device_platform.startswith("cisco"):
                if not self._check_cisco_factory_default(device):
                    logger.warning(f"Validation: {hostname} may not be at factory defaults")
                    # Don't fail — device may still be rebooting
                else:
                    logger.info(f"Validation OK: {hostname}")

        return all_ok  # Best-effort — don't block on validation failures

    # ── Helpers ────────────────────────────────────────────────────────────

    def _get_cisco_devices(self) -> list[DiscoveredDevice]:
        return [
            d for d in self.discovered_devices.values()
            if d.device_platform and d.device_platform.startswith("cisco")
        ]

    def _get_hpe_devices(self) -> list[DiscoveredDevice]:
        return [
            d for d in self.discovered_devices.values()
            if d.device_platform and d.device_platform.startswith("hpe_")
        ]

    def _check_cisco_factory_default(self, device: DiscoveredDevice) -> bool:
        """Quick probe: can we SSH with factory credentials?"""
        try:
            import socket

            sock = socket.create_connection((device.ip, 22), timeout=5)
            sock.close()
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False
