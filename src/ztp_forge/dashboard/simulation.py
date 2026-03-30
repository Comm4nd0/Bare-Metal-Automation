"""Simulation engine — runs a full deployment lifecycle without real hardware.

Animates a deployment through all 13 phases with realistic device state
transitions, cabling results, and log entries.  The dashboard's existing
5-second polling picks up every change automatically.

Usage from the UI:
    POST /api/simulation/start/   → starts background thread
    POST /api/simulation/stop/    → signals the thread to stop
    GET  /api/simulation/status/  → returns running state

Usage from the CLI:
    python manage.py run_simulation [--name "SIM-Rack-Demo"]
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from django.db import close_old_connections

if TYPE_CHECKING:
    from ztp_forge.dashboard.models import Deployment

logger = logging.getLogger(__name__)

# ── Module-level simulation state ──────────────────────────────────────────

_simulation_thread: threading.Thread | None = None
_simulation_lock = threading.Lock()
_simulation_stop = threading.Event()
_simulation_deployment_id: int | None = None

# ── Device definitions ─────────────────────────────────────────────────────

SIMULATED_DEVICES = [
    {
        "ip": "10.255.0.10",
        "mac": "aa:bb:cc:00:10:01",
        "serial": "FOC2145X0AB",
        "platform": "cisco_ios",
        "hostname": "Switch",
        "intended_hostname": "sw-core-01",
        "role": "core-switch",
        "management_ip": "192.168.100.1",
        "bfs_depth": 1,
        "config_order": 9,
    },
    {
        "ip": "10.255.0.20",
        "mac": "aa:bb:cc:00:20:01",
        "serial": "FOC2250L1AA",
        "platform": "cisco_iosxe",
        "hostname": "Switch",
        "intended_hostname": "sw-dist-01",
        "role": "distribution-switch",
        "management_ip": "192.168.100.2",
        "bfs_depth": 2,
        "config_order": 1,
    },
    {
        "ip": "10.255.0.21",
        "mac": "aa:bb:cc:00:21:01",
        "serial": "FOC2250L1AB",
        "platform": "cisco_iosxe",
        "hostname": "Switch",
        "intended_hostname": "sw-dist-02",
        "role": "distribution-switch",
        "management_ip": "192.168.100.3",
        "bfs_depth": 2,
        "config_order": 2,
    },
    {
        "ip": "10.255.0.22",
        "mac": "aa:bb:cc:00:22:01",
        "serial": "FOC2250L1AC",
        "platform": "cisco_iosxe",
        "hostname": "Switch",
        "intended_hostname": "sw-dist-03",
        "role": "distribution-switch",
        "management_ip": "192.168.100.4",
        "bfs_depth": 2,
        "config_order": 3,
    },
    {
        "ip": "10.255.0.23",
        "mac": "aa:bb:cc:00:23:01",
        "serial": "FOC2250L1AD",
        "platform": "cisco_iosxe",
        "hostname": "Switch",
        "intended_hostname": "sw-dist-04",
        "role": "distribution-switch",
        "management_ip": "192.168.100.5",
        "bfs_depth": 2,
        "config_order": 4,
    },
    {
        "ip": "10.255.0.24",
        "mac": "aa:bb:cc:00:24:01",
        "serial": "FOC2250L1AE",
        "platform": "cisco_iosxe",
        "hostname": "Switch",
        "intended_hostname": "sw-dist-05",
        "role": "distribution-switch",
        "management_ip": "192.168.100.6",
        "bfs_depth": 2,
        "config_order": 5,
    },
    {
        "ip": "10.255.0.30",
        "mac": "aa:bb:cc:00:30:01",
        "serial": "FOC2145X0CD",
        "platform": "cisco_ios",
        "hostname": "Switch",
        "intended_hostname": "sw-access-01",
        "role": "access-switch",
        "management_ip": "192.168.100.7",
        "bfs_depth": 2,
        "config_order": 6,
    },
    {
        "ip": "10.255.0.40",
        "mac": "aa:bb:cc:00:40:01",
        "serial": "JAD2345678A",
        "platform": "cisco_ios",
        "hostname": "Router",
        "intended_hostname": "rtr-border-01",
        "role": "border-router",
        "management_ip": "192.168.100.10",
        "bfs_depth": 2,
        "config_order": 7,
    },
    {
        "ip": "10.255.0.50",
        "mac": "aa:bb:cc:00:50:01",
        "serial": "JMX0987654A",
        "platform": "cisco_asa",
        "hostname": "Firewall",
        "intended_hostname": "fw-perim-01",
        "role": "perimeter-firewall",
        "management_ip": "192.168.100.20",
        "bfs_depth": 2,
        "config_order": 8,
    },
    {
        "ip": "10.255.0.110",
        "mac": "aa:bb:cc:01:10:01",
        "serial": "CZ24100001",
        "platform": "hpe_dl380_gen10",
        "hostname": "localhost",
        "intended_hostname": "bus-backup-01",
        "role": "management-server",
        "management_ip": "192.168.100.50",
        "bfs_depth": 2,
        "config_order": 10,
    },
    {
        "ip": "10.255.0.200",
        "mac": "aa:bb:cc:02:00:01",
        "serial": "MBG00012345",
        "platform": "meinberg_lantime",
        "hostname": "LANTIME",
        "intended_hostname": "ntp-01",
        "role": "ntp-server",
        "management_ip": "192.168.100.60",
        "bfs_depth": 2,
        "config_order": 11,
    },
    {
        "ip": "10.255.0.101",
        "mac": "aa:bb:cc:01:01:01",
        "serial": "CZ23250001",
        "platform": "hpe_dl325_gen10",
        "hostname": "localhost",
        "intended_hostname": "esxi-compute-01",
        "role": "compute-node",
        "management_ip": "192.168.100.101",
        "bfs_depth": 3,
        "config_order": 12,
    },
    {
        "ip": "10.255.0.102",
        "mac": "aa:bb:cc:01:02:01",
        "serial": "CZ23250002",
        "platform": "hpe_dl325_gen10",
        "hostname": "localhost",
        "intended_hostname": "esxi-compute-02",
        "role": "compute-node",
        "management_ip": "192.168.100.102",
        "bfs_depth": 3,
        "config_order": 13,
    },
    {
        "ip": "10.255.0.103",
        "mac": "aa:bb:cc:01:03:01",
        "serial": "CZ23250003",
        "platform": "hpe_dl325_gen10",
        "hostname": "localhost",
        "intended_hostname": "esxi-compute-03",
        "role": "compute-node",
        "management_ip": "192.168.100.103",
        "bfs_depth": 3,
        "config_order": 14,
    },
    {
        "ip": "10.255.0.104",
        "mac": "aa:bb:cc:01:04:01",
        "serial": "CZ23360001",
        "platform": "hpe_dl360_gen10",
        "hostname": "localhost",
        "intended_hostname": "esxi-compute-04",
        "role": "compute-node",
        "management_ip": "192.168.100.104",
        "bfs_depth": 3,
        "config_order": 15,
    },
    {
        "ip": "10.255.0.105",
        "mac": "aa:bb:cc:01:05:01",
        "serial": "CZ23360002",
        "platform": "hpe_dl360_gen10",
        "hostname": "localhost",
        "intended_hostname": "esxi-compute-05",
        "role": "compute-node",
        "management_ip": "192.168.100.105",
        "bfs_depth": 3,
        "config_order": 16,
    },
]

# Cabling: (local_port, status, actual_remote, actual_port,
#           intended_remote, intended_port, message)
_CableRow = tuple[str, str, str, str, str, str, str]
CABLING_DATA: dict[str, list[_CableRow]] = {
    "sw-core-01": [
        ("Gi1/0/1", "correct", "sw-dist-01", "Gi1/0/48",
         "sw-dist-01", "Gi1/0/48", "Uplink OK"),
        ("Gi1/0/2", "correct", "sw-dist-02", "Gi1/0/48",
         "sw-dist-02", "Gi1/0/48", "Uplink OK"),
        ("Gi1/0/3", "correct", "sw-dist-03", "Gi1/0/48",
         "sw-dist-03", "Gi1/0/48", "Uplink OK"),
        ("Gi1/0/4", "correct", "sw-dist-04", "Gi1/0/48",
         "sw-dist-04", "Gi1/0/48", "Uplink OK"),
        ("Gi1/0/5", "correct", "sw-dist-05", "Gi1/0/48",
         "sw-dist-05", "Gi1/0/48", "Uplink OK"),
        ("Gi1/0/6", "correct", "sw-access-01", "Gi1/0/48",
         "sw-access-01", "Gi1/0/48", "Uplink OK"),
        ("Gi1/0/10", "correct", "rtr-border-01", "Gi0/0",
         "rtr-border-01", "Gi0/0", "Router link OK"),
        ("Gi1/0/11", "correct", "fw-perim-01", "Gi0/0",
         "fw-perim-01", "Gi0/0", "Firewall link OK"),
        ("Gi1/0/20", "wrong_port", "bus-backup-01", "NIC2",
         "bus-backup-01", "NIC1", "Wrong NIC — adaptable"),
        ("Gi1/0/22", "correct", "ntp-01", "Eth0",
         "ntp-01", "Eth0", "NTP link OK"),
    ],
    "sw-access-01": [
        ("Gi1/0/48", "correct", "sw-core-01", "Gi1/0/6",
         "sw-core-01", "Gi1/0/6", "Uplink OK"),
        ("Gi1/0/1", "correct", "esxi-compute-01", "NIC1",
         "esxi-compute-01", "NIC1", "Server link OK"),
        ("Gi1/0/2", "correct", "esxi-compute-02", "NIC1",
         "esxi-compute-02", "NIC1", "Server link OK"),
        ("Gi1/0/3", "correct", "esxi-compute-03", "NIC1",
         "esxi-compute-03", "NIC1", "Server link OK"),
        ("Gi1/0/4", "correct", "esxi-compute-04", "NIC1",
         "esxi-compute-04", "NIC1", "Server link OK"),
        ("Gi1/0/5", "missing", "", "",
         "esxi-compute-05", "NIC1", "Cable missing"),
    ],
    "sw-dist-01": [
        ("Gi1/0/48", "correct", "sw-core-01", "Gi1/0/1",
         "sw-core-01", "Gi1/0/1", "Uplink OK"),
    ],
    "sw-dist-02": [
        ("Gi1/0/48", "correct", "sw-core-01", "Gi1/0/2",
         "sw-core-01", "Gi1/0/2", "Uplink OK"),
    ],
    "sw-dist-03": [
        ("Gi1/0/48", "correct", "sw-core-01", "Gi1/0/3",
         "sw-core-01", "Gi1/0/3", "Uplink OK"),
    ],
    "sw-dist-04": [
        ("Gi1/0/48", "correct", "sw-core-01", "Gi1/0/4",
         "sw-core-01", "Gi1/0/4", "Uplink OK"),
    ],
    "sw-dist-05": [
        ("Gi1/0/48", "wrong_device", "sw-dist-04", "Gi1/0/47",
         "sw-core-01", "Gi1/0/5", "Wrong device connected"),
    ],
    "rtr-border-01": [
        ("Gi0/0", "correct", "sw-core-01", "Gi1/0/10",
         "sw-core-01", "Gi1/0/10", "Core link OK"),
    ],
    "fw-perim-01": [
        ("Gi0/0", "correct", "sw-core-01", "Gi1/0/11",
         "sw-core-01", "Gi1/0/11", "Core link OK"),
    ],
}


class SimulationEngine:
    """Drives a simulated deployment through all phases."""

    def __init__(self, deployment_name: str = "SIM-Rack-Demo") -> None:
        self.deployment_name = deployment_name
        self.deployment: Deployment | None = None

    # ── Helpers ─────────────────────────────────────────────────────────────

    @property
    def _dep(self) -> Deployment:
        """Return deployment, raising if not yet created."""
        assert self.deployment is not None
        return self.deployment

    def _sleep(self, seconds: float) -> bool:
        """Sleep in 0.5 s increments, checking the stop event.

        Returns True if the simulation should continue, False if stopped.
        """
        elapsed = 0.0
        while elapsed < seconds:
            if _simulation_stop.is_set():
                return False
            time.sleep(min(0.5, seconds - elapsed))
            elapsed += 0.5
        return True

    def _log(self, level: str, phase: str, message: str) -> None:
        from ztp_forge.dashboard.models import DeploymentLog

        DeploymentLog.objects.create(
            deployment=self._dep,
            level=level,
            phase=phase,
            message=message,
        )

    def _set_phase(self, phase: str) -> None:
        dep = self._dep
        dep.phase = phase
        dep.save(update_fields=["phase", "updated_at"])
        self._log("INFO", phase, f"Phase: {dep.get_phase_display()}")

    def _update_device(self, hostname: str, **fields: object) -> None:
        from ztp_forge.dashboard.models import Device

        Device.objects.filter(
            deployment=self._dep,
            intended_hostname=hostname,
        ).update(**fields)

    def _get_cisco_devices(self) -> list[dict[str, str | int]]:
        return [
            d for d in SIMULATED_DEVICES
            if str(d["platform"]).startswith("cisco")
        ]

    def _get_hpe_devices(self) -> list[dict[str, str | int]]:
        return [
            d for d in SIMULATED_DEVICES
            if str(d["platform"]).startswith("hpe_")
        ]

    def _get_ntp_devices(self) -> list[dict[str, str | int]]:
        return [
            d for d in SIMULATED_DEVICES
            if d["platform"] == "meinberg_lantime"
        ]

    # ── Main entry point ───────────────────────────────────────────────────

    def run(self) -> None:
        """Execute the full simulation.  Call from a thread or synchronously."""
        global _simulation_deployment_id
        try:
            close_old_connections()
            self._run_phases()
        except Exception:
            logger.exception("Simulation failed")
            if self.deployment is not None:
                self.deployment.phase = "failed"
                self.deployment.save(update_fields=["phase", "updated_at"])
                self._log("ERROR", "simulation", "Simulation crashed")
        finally:
            _simulation_deployment_id = None
            close_old_connections()

    def _run_phases(self) -> None:
        global _simulation_deployment_id

        if not self._phase_pre_flight():
            return
        _simulation_deployment_id = self._dep.pk

        phases = [
            self._phase_discovery,
            self._phase_topology,
            self._phase_cabling_validation,
            self._phase_firmware_upgrade,
            self._phase_heavy_transfers,
            self._phase_network_config,
            self._phase_laptop_pivot,
            self._phase_server_provision,
            self._phase_ntp_provision,
            self._phase_post_install,
            self._phase_final_validation,
        ]

        for phase_fn in phases:
            if _simulation_stop.is_set():
                self._log("WARNING", "simulation", "Stopped by user")
                dep = self._dep
                dep.phase = "failed"
                dep.save(update_fields=["phase", "updated_at"])
                return
            if not phase_fn():
                return

        self._set_phase("complete")
        self._log("INFO", "complete", "Deployment complete — all devices provisioned")

    # ── Phase implementations ──────────────────────────────────────────────

    def _phase_pre_flight(self) -> bool:
        from ztp_forge.dashboard.models import Deployment

        self.deployment = Deployment.objects.create(
            name=self.deployment_name,
            phase="pre_flight",
            bootstrap_subnet="10.255.0.0/16",
            laptop_ip="10.255.255.1",
            management_vlan=100,
        )
        self._log("INFO", "pre_flight", f"Deployment {self.deployment_name} initialized")
        self._log("INFO", "pre_flight", "Checking inventory file... OK")
        if not self._sleep(1):
            return False
        self._log("INFO", "pre_flight", "Verifying firmware files on disk... OK")
        if not self._sleep(1):
            return False
        self._log("INFO", "pre_flight", "Checking laptop NIC configuration... OK")
        if not self._sleep(1):
            return False
        self._log("INFO", "pre_flight", "Pre-flight checks passed")
        return True

    def _phase_discovery(self) -> bool:
        from ztp_forge.dashboard.models import Device

        self._set_phase("discovery")
        self._log("INFO", "discovery", "Collecting DHCP leases on 10.255.0.0/16...")
        if not self._sleep(2):
            return False

        self._log("INFO", "discovery", f"Found {len(SIMULATED_DEVICES)} active leases")
        self._log("INFO", "discovery", "Probing devices via SSH...")

        for device_data in SIMULATED_DEVICES:
            if _simulation_stop.is_set():
                return False
            Device.objects.create(
                deployment=self._dep,
                state="discovered",
                **device_data,
            )
            hostname = device_data["intended_hostname"]
            ip = device_data["ip"]
            serial = device_data["serial"]
            self._log(
                "INFO", "discovery",
                f"Discovered {hostname} ({ip}) — serial {serial}",
            )
            if not self._sleep(0.7):
                return False

        self._log(
            "INFO", "discovery",
            f"Matched {len(SIMULATED_DEVICES)}/{len(SIMULATED_DEVICES)} "
            f"devices to inventory",
        )
        return True

    def _phase_topology(self) -> bool:
        self._set_phase("topology")
        self._log("INFO", "topology", "Building CDP adjacency graph...")
        if not self._sleep(1.5):
            return False

        self._log(
            "INFO", "topology",
            f"Built topology graph — {len(SIMULATED_DEVICES)} nodes, 17 edges",
        )

        # Transition all devices to identified
        from ztp_forge.dashboard.models import Device

        Device.objects.filter(deployment=self._dep).update(state="identified")
        if not self._sleep(1):
            return False

        self._log("INFO", "topology", "BFS config order calculated (outside-in)")
        self._log("INFO", "topology", "Configuration order: depth 3 → 2 → 1 (core last)")
        if not self._sleep(1):
            return False
        return True

    def _phase_cabling_validation(self) -> bool:
        from ztp_forge.dashboard.models import CablingResult, Device

        self._set_phase("cabling_validation")
        self._log("INFO", "cabling_validation", "Validating physical cabling against templates...")
        if not self._sleep(1):
            return False

        for hostname, results in CABLING_DATA.items():
            if _simulation_stop.is_set():
                return False

            device = Device.objects.filter(
                deployment=self._dep,
                intended_hostname=hostname,
            ).first()
            if not device:
                continue

            correct = sum(1 for r in results if r[1] == "correct")
            issues = len(results) - correct
            self._log(
                "INFO", "cabling_validation",
                f"Validating {hostname}: {correct} correct"
                + (f", {issues} issue(s)" if issues else ""),
            )

            for local_port, status, act_rem, act_port, int_rem, int_port, msg in results:
                CablingResult.objects.create(
                    device=device,
                    local_port=local_port,
                    status=status,
                    actual_remote=act_rem,
                    actual_remote_port=act_port,
                    intended_remote=int_rem,
                    intended_remote_port=int_port,
                    message=msg,
                )
                if status != "correct":
                    self._log(
                        "WARNING", "cabling_validation",
                        f"{hostname} {local_port}: {msg}",
                    )

            if not self._sleep(0.5):
                return False

        # Transition all devices to validated
        Device.objects.filter(deployment=self._dep).update(state="validated")
        self._log(
            "WARNING", "cabling_validation",
            "3 cabling issues found — review before proceeding",
        )
        if not self._sleep(1):
            return False
        return True

    def _phase_firmware_upgrade(self) -> bool:
        self._set_phase("firmware_upgrade")
        cisco_devices = self._get_cisco_devices()
        self._log(
            "INFO", "firmware_upgrade",
            f"Upgrading firmware on {len(cisco_devices)} network devices...",
        )
        if not self._sleep(1):
            return False

        # Group by depth — higher depth first (outside-in)
        depths = sorted(
            {int(d["bfs_depth"]) for d in cisco_devices}, reverse=True,
        )
        for depth in depths:
            depth_devices = [d for d in cisco_devices if d["bfs_depth"] == depth]
            for device_data in depth_devices:
                if _simulation_stop.is_set():
                    return False
                hostname = str(device_data["intended_hostname"])
                platform = str(device_data["platform"])
                self._update_device(hostname, state="firmware_upgrading")
                self._log(
                    "INFO", "firmware_upgrade",
                    f"{hostname}: uploading firmware via SCP ({platform})...",
                )
                if not self._sleep(1):
                    return False
                self._log(
                    "INFO", "firmware_upgrade",
                    f"{hostname}: MD5 verified, setting boot variable...",
                )
                if not self._sleep(0.5):
                    return False
                self._update_device(hostname, state="firmware_upgraded")
                self._log(
                    "INFO", "firmware_upgrade",
                    f"{hostname}: firmware upgrade complete",
                )
            if not self._sleep(0.5):
                return False

        return True

    def _phase_heavy_transfers(self) -> bool:
        self._set_phase("heavy_transfers")
        self._log("INFO", "heavy_transfers", "Transferring ISO images while network is flat L2...")
        if not self._sleep(1.5):
            return False
        self._log("INFO", "heavy_transfers", "SPP-2024.03.0.iso → bus-backup-01 (3.2 GB)... done")
        if not self._sleep(1):
            return False
        self._log("INFO", "heavy_transfers", "win-srv-2022.iso → bus-backup-01 (5.1 GB)... done")
        if not self._sleep(1):
            return False
        self._log(
            "INFO", "heavy_transfers",
            "esxi-8.0u2.iso → esxi-compute-01..05 (1.2 GB)... done",
        )
        if not self._sleep(1.5):
            return False
        self._log("INFO", "heavy_transfers", "All ISO transfers complete")
        return True

    def _phase_network_config(self) -> bool:
        self._set_phase("network_config")
        cisco_devices = self._get_cisco_devices()
        self._log(
            "INFO", "network_config",
            f"Configuring {len(cisco_devices)} network devices (outside-in)...",
        )
        if not self._sleep(1):
            return False

        # Sort by config_order (outside-in: higher depth first)
        devices_by_depth = sorted(
            {int(d["bfs_depth"]) for d in cisco_devices},
            reverse=True,
        )

        for depth in devices_by_depth:
            depth_devices = [d for d in cisco_devices if d["bfs_depth"] == depth]
            self._log(
                "INFO", "network_config",
                f"Configuring depth {depth} devices "
                f"({len(depth_devices)} device(s))...",
            )

            for device_data in depth_devices:
                if _simulation_stop.is_set():
                    return False
                hostname = str(device_data["intended_hostname"])
                self._update_device(hostname, state="configuring")
                self._log(
                    "INFO", "network_config",
                    f"{hostname}: reload-in-5 set, pushing configuration...",
                )
                if not self._sleep(1.5):
                    return False

                # Simulate occasional transient issue
                if hostname == "sw-dist-03":
                    self._log(
                        "WARNING", "network_config",
                        f"{hostname}: SSH timeout during config push — retrying...",
                    )
                    if not self._sleep(1):
                        return False

                self._log(
                    "INFO", "network_config",
                    f"{hostname}: health check passed — cancelled reload, "
                    f"saved config",
                )
                self._update_device(hostname, state="configured")

            if not self._sleep(0.5):
                return False

        self._log("INFO", "network_config", "All network devices configured successfully")
        return True

    def _phase_laptop_pivot(self) -> bool:
        self._set_phase("laptop_pivot")
        self._log(
            "INFO", "laptop_pivot",
            "Reconfiguring laptop NIC to management VLAN 100...",
        )
        if not self._sleep(1.5):
            return False
        self._log(
            "INFO", "laptop_pivot",
            "Laptop NIC assigned 192.168.100.254/24 on VLAN 100",
        )
        if not self._sleep(1):
            return False
        self._log("INFO", "laptop_pivot", "Management network connectivity verified")
        if not self._sleep(0.5):
            return False
        return True

    def _phase_server_provision(self) -> bool:
        self._set_phase("server_provision")
        hpe_devices = self._get_hpe_devices()
        self._log(
            "INFO", "server_provision",
            f"Provisioning {len(hpe_devices)} HPE servers via Redfish (parallel)...",
        )
        if not self._sleep(1):
            return False

        # Walk all HPE servers through the provisioning sub-states
        server_states = [
            ("bios_configuring", "Configuring BIOS settings"),
            ("bios_configured", "BIOS configured — pending reboot"),
            ("raid_configuring", "Configuring RAID arrays"),
            ("raid_configured", "RAID configured"),
            ("spp_installing", "Installing HPE Service Pack for ProLiant"),
            ("spp_installed", "SPP installation complete"),
            ("os_installing", "Installing OS via virtual media"),
            ("os_installed", None),  # custom logging below
            ("os_configuring", None),  # custom logging below
            ("os_configured", None),  # custom logging below
            ("ilo_configuring", "Configuring iLO production settings"),
            ("ilo_configured", "iLO configured (network, users, SNMP, NTP)"),
            ("provisioned", "Server provisioned"),
        ]

        for state, description in server_states:
            if _simulation_stop.is_set():
                return False

            for device_data in hpe_devices:
                hostname = str(device_data["intended_hostname"])
                self._update_device(hostname, state=state)

            # Log per-state with appropriate detail
            if state in (
                "bios_configuring", "raid_configuring",
                "spp_installing", "ilo_configuring",
            ):
                for device_data in hpe_devices:
                    hostname = str(device_data["intended_hostname"])
                    self._log(
                        "INFO", "server_provision",
                        f"{hostname}: {description}",
                    )
            elif state == "os_installed":
                for device_data in hpe_devices:
                    hostname = str(device_data["intended_hostname"])
                    if hostname.startswith("esxi"):
                        self._log(
                            "INFO", "server_provision",
                            f"{hostname}: VMware ESXi 8.0U2 installed",
                        )
                    else:
                        self._log(
                            "INFO", "server_provision",
                            f"{hostname}: Windows Server 2022 installed",
                        )
            elif state == "os_configuring":
                for device_data in hpe_devices:
                    hostname = str(device_data["intended_hostname"])
                    if hostname.startswith("esxi"):
                        self._log(
                            "INFO", "server_provision",
                            f"{hostname}: configuring ESXi "
                            f"(vSwitch, mgmt network, NTP, SSH)",
                        )
                    else:
                        self._log(
                            "INFO", "server_provision",
                            f"{hostname}: configuring Windows "
                            f"(hostname, domain join, roles)",
                        )
            elif state == "os_configured":
                for device_data in hpe_devices:
                    hostname = str(device_data["intended_hostname"])
                    if hostname.startswith("esxi"):
                        self._log(
                            "INFO", "server_provision",
                            f"{hostname}: ESXi configuration complete",
                        )
                    else:
                        self._log(
                            "INFO", "server_provision",
                            f"{hostname}: Windows configuration complete",
                        )
            elif state == "provisioned":
                for device_data in hpe_devices:
                    hostname = str(device_data["intended_hostname"])
                    self._log(
                        "INFO", "server_provision",
                        f"{hostname}: provisioning complete",
                    )

            if not self._sleep(1.5):
                return False

        return True

    def _phase_ntp_provision(self) -> bool:
        self._set_phase("ntp_provision")
        ntp_devices = self._get_ntp_devices()
        self._log(
            "INFO", "ntp_provision",
            f"Provisioning {len(ntp_devices)} Meinberg NTP device(s)...",
        )
        if not self._sleep(1):
            return False

        for device_data in ntp_devices:
            if _simulation_stop.is_set():
                return False
            hostname = str(device_data["intended_hostname"])
            self._update_device(hostname, state="provisioning")
            self._log("INFO", "ntp_provision", f"{hostname}: configuring network settings...")
            if not self._sleep(1):
                return False
            self._log(
                "INFO", "ntp_provision",
                f"{hostname}: configuring NTP references (GPS, PTP)...",
            )
            if not self._sleep(1):
                return False
            self._log(
                "INFO", "ntp_provision",
                f"{hostname}: configuring NTP service and ACLs...",
            )
            if not self._sleep(1):
                return False
            self._update_device(hostname, state="provisioned")
            self._log("INFO", "ntp_provision", f"{hostname}: provisioned successfully")

        if not self._sleep(1):
            return False
        return True

    def _phase_post_install(self) -> bool:
        self._set_phase("post_install")
        self._log("INFO", "post_install", "Running post-install tasks...")
        if not self._sleep(1):
            return False
        self._log("INFO", "post_install", "Ansible hardening playbook... OK")
        if not self._sleep(1):
            return False
        self._log("INFO", "post_install", "Installing monitoring agents... OK")
        if not self._sleep(1):
            return False
        return True

    def _phase_final_validation(self) -> bool:
        self._set_phase("final_validation")
        self._log("INFO", "final_validation", "Running final connectivity tests...")
        if not self._sleep(1):
            return False
        self._log("INFO", "final_validation", "ICMP reachability to all management IPs... OK")
        if not self._sleep(0.5):
            return False
        self._log("INFO", "final_validation", "SSH access to all network devices... OK")
        if not self._sleep(0.5):
            return False
        self._log("INFO", "final_validation", "Redfish access to all iLO interfaces... OK")
        if not self._sleep(0.5):
            return False
        self._log(
            "WARNING", "final_validation",
            "NTP stratum check: ntp-01 not yet synchronized — "
            "GPS lock may take up to 15 minutes",
        )
        if not self._sleep(0.5):
            return False
        self._log("INFO", "final_validation", "Final validation passed (1 non-blocking warning)")
        return True


# ── Public API ─────────────────────────────────────────────────────────────

def start_simulation(name: str = "SIM-Rack-Demo") -> dict[str, str]:
    """Start a simulation in a background thread."""
    global _simulation_thread

    with _simulation_lock:
        if _simulation_thread is not None and _simulation_thread.is_alive():
            return {"status": "already_running"}

        _simulation_stop.clear()
        engine = SimulationEngine(deployment_name=name)
        _simulation_thread = threading.Thread(
            target=engine.run,
            name="ztp-simulation",
            daemon=True,
        )
        _simulation_thread.start()
        return {"status": "started"}


def stop_simulation() -> dict[str, str]:
    """Signal the running simulation to stop."""
    global _simulation_thread

    with _simulation_lock:
        if _simulation_thread is None or not _simulation_thread.is_alive():
            return {"status": "not_running"}

        _simulation_stop.set()
        return {"status": "stopping"}


def simulation_status() -> dict[str, object]:
    """Return current simulation state."""
    with _simulation_lock:
        running = _simulation_thread is not None and _simulation_thread.is_alive()
        return {
            "running": running,
            "deployment_id": _simulation_deployment_id,
        }
