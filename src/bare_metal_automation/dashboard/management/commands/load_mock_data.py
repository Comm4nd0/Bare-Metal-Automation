"""Management command to populate the database with mock deployment data."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from bare_metal_automation.dashboard.models import CablingResult, Deployment, DeploymentLog, Device


class Command(BaseCommand):
    help = "Load mock deployment data for dashboard testing"

    def handle(self, *args, **options):
        # Clear existing mock data
        Deployment.objects.filter(name="DC-Rack-42").delete()

        dep = Deployment.objects.create(
            name="DC-Rack-42",
            phase="network_config",
            bootstrap_subnet="10.255.0.0/16",
            laptop_ip="10.255.255.1",
            management_vlan=100,
        )

        devices_data = [
            {
                "ip": "10.255.0.10",
                "mac": "aa:bb:cc:00:10:01",
                "serial": "FOC2145X0AB",
                "platform": "cisco_ios",
                "hostname": "Switch",
                "intended_hostname": "sw-core-01",
                "role": "core-switch",
                "state": "configured",
                "bfs_depth": 1,
                "config_order": 3,
                "management_ip": "192.168.100.1",
            },
            {
                "ip": "10.255.0.20",
                "mac": "aa:bb:cc:00:20:01",
                "serial": "FOC2145X0CD",
                "platform": "cisco_ios",
                "hostname": "Switch",
                "intended_hostname": "sw-access-01",
                "role": "access-switch",
                "state": "configured",
                "bfs_depth": 2,
                "config_order": 1,
                "management_ip": "192.168.100.2",
            },
            {
                "ip": "10.255.0.30",
                "mac": "aa:bb:cc:00:30:01",
                "serial": "FOC2145X0EF",
                "platform": "cisco_ios",
                "hostname": "Switch",
                "intended_hostname": "sw-access-02",
                "role": "access-switch",
                "state": "configuring",
                "bfs_depth": 2,
                "config_order": 2,
                "management_ip": "192.168.100.3",
            },
            {
                "ip": "10.255.0.40",
                "mac": "aa:bb:cc:00:40:01",
                "serial": "JAD1234567",
                "platform": "cisco_ios",
                "hostname": "Router",
                "intended_hostname": "rtr-border-01",
                "role": "border-router",
                "state": "validated",
                "bfs_depth": 3,
                "config_order": 4,
                "management_ip": "192.168.100.10",
            },
            {
                "ip": "10.255.0.50",
                "mac": "aa:bb:cc:00:50:01",
                "serial": "JMX0987654",
                "platform": "cisco_asa",
                "hostname": "Firewall",
                "intended_hostname": "fw-perim-01",
                "role": "perimeter-firewall",
                "state": "validated",
                "bfs_depth": 4,
                "config_order": 5,
                "management_ip": "192.168.100.20",
            },
            {
                "ip": "10.255.0.101",
                "mac": "aa:bb:cc:01:01:01",
                "serial": "CZ12345678",
                "platform": "hpe_dl325_gen10",
                "hostname": "localhost",
                "intended_hostname": "svr-compute-01",
                "role": "compute-node",
                "state": "discovered",
                "bfs_depth": 3,
                "config_order": 6,
                "management_ip": "192.168.100.101",
            },
            {
                "ip": "10.255.0.102",
                "mac": "aa:bb:cc:01:02:01",
                "serial": "CZ12345679",
                "platform": "hpe_dl325_gen10",
                "hostname": "localhost",
                "intended_hostname": "svr-compute-02",
                "role": "compute-node",
                "state": "discovered",
                "bfs_depth": 3,
                "config_order": 7,
                "management_ip": "192.168.100.102",
            },
        ]

        device_objects = {}
        for d in devices_data:
            dev = Device.objects.create(deployment=dep, **d)
            device_objects[d["serial"]] = dev

        # Add cabling results for configured switches
        cabling_data = {
            "FOC2145X0AB": [
                ("Gi1/0/1", "correct", "sw-access-01", "Gi1/0/48", "sw-access-01", "Gi1/0/48", "Uplink verified"),
                ("Gi1/0/2", "correct", "sw-access-02", "Gi1/0/48", "sw-access-02", "Gi1/0/48", "Uplink verified"),
                ("Gi1/0/24", "correct", "rtr-border-01", "Gi0/0", "rtr-border-01", "Gi0/0", "Router link verified"),
                ("Gi1/0/10", "wrong_port", "svr-compute-01", "NIC2", "svr-compute-01", "NIC1", "Wrong NIC connected"),
            ],
            "FOC2145X0CD": [
                ("Gi1/0/48", "correct", "sw-core-01", "Gi1/0/1", "sw-core-01", "Gi1/0/1", "Uplink verified"),
                ("Gi1/0/1", "correct", "svr-compute-01", "NIC1", "svr-compute-01", "NIC1", "Server link OK"),
                ("Gi1/0/2", "missing", None, None, "svr-compute-02", "NIC1", "Expected connection not found"),
            ],
        }

        for serial, results in cabling_data.items():
            dev = device_objects[serial]
            for local_port, status, act_rem, act_port, int_rem, int_port, msg in results:
                CablingResult.objects.create(
                    device=dev,
                    local_port=local_port,
                    status=status,
                    actual_remote=act_rem or "",
                    actual_remote_port=act_port or "",
                    intended_remote=int_rem or "",
                    intended_remote_port=int_port or "",
                    message=msg,
                )

        # Add log entries
        logs = [
            ("INFO", "pre_flight", "Deployment DC-Rack-42 initialized"),
            ("INFO", "discovery", "Collecting DHCP leases on 10.255.0.0/16"),
            ("INFO", "discovery", "Found 7 active leases"),
            ("INFO", "discovery", "Probing devices via SSH..."),
            ("INFO", "discovery", "Matched 7/7 devices to inventory"),
            ("INFO", "topology", "Built topology graph — 7 nodes, 8 edges"),
            ("INFO", "topology", "BFS config order calculated (outside-in)"),
            ("INFO", "cabling_validation", "Validating cabling for sw-core-01: 3 correct, 1 wrong_port"),
            ("WARNING", "cabling_validation", "sw-core-01 Gi1/0/10: wrong NIC — svr-compute-01 NIC2 instead of NIC1"),
            ("INFO", "cabling_validation", "Validating cabling for sw-access-01: 2 correct, 1 missing"),
            ("WARNING", "cabling_validation", "sw-access-01 Gi1/0/2: expected connection to svr-compute-02 not found"),
            ("INFO", "network_config", "Configuring sw-access-01 (depth 2)..."),
            ("INFO", "network_config", "sw-access-01: config pushed, reload-in-5 set"),
            ("INFO", "network_config", "sw-access-01: health check passed — cancelled reload, saved config"),
            ("INFO", "network_config", "Configuring sw-access-02 (depth 2)..."),
            ("INFO", "network_config", "sw-access-02: pushing configuration..."),
        ]

        for level, phase, message in logs:
            DeploymentLog.objects.create(
                deployment=dep,
                level=level,
                phase=phase,
                message=message,
            )

        self.stdout.write(self.style.SUCCESS(
            f"Created mock deployment '{dep.name}' with {len(devices_data)} devices"
        ))
