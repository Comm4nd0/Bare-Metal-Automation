# Bare Metal Automation

**Zero-touch provisioning for bare-metal infrastructure — from factory-new hardware to fully operational site.**

Bare Metal Automation (BMA) is **Pillar 3** in a five-pillar pipeline that takes a site from an empty rack to running services. BMA handles the physical build: it takes a deployment bundle (rendered configs, firmware, ISOs) and a rack of factory-new hardware, and produces configured network devices, provisioned servers, and installed operating systems — all driven from a single deployment laptop with no out-of-band management network.

```
NetBox (Pillar 1)  →  Config & Media (Pillar 2)  →  BMA (Pillar 3)  →  vInfra (Pillar 4)  →  Software (Pillar 5)
  Design the site       Generate artefacts          Physical build       vCenter, NSX, VDS      VMs, apps, services
```

## What It Does

A deployment laptop connects to a rack of factory-new Cisco switches, routers, firewalls, HPE servers, and Meinberg NTP appliances. Operating entirely on the factory-default flat L2 network, the system:

1. **Ingests** the deployment bundle and validates all files against checksums
2. **Discovers** all devices via DHCP and CDP on the bootstrap network
3. **Identifies** each device by serial number and matches it to its intended role
4. **Maps** the physical topology (NetworkX graph + BFS ordering) and validates cabling against the intended design
5. **Transfers** firmware and OS ISOs while the network is still flat L2 (no ACLs/QoS restrictions)
6. **Configures** network devices outside-in (furthest from laptop first) with dead man's switch rollback (`reload in 5`)
7. **Pivots** the laptop onto the production management VLAN
8. **Provisions** servers via HPE iLO 5 Redfish API (BIOS, RAID, SPP, OS install) and Meinberg NTP appliances via REST API
9. **Validates** end-to-end connectivity and generates a deployment report

BMA also operates in reverse — **factory reset mode** rolls a deployed site back to factory-new state for redeployment.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│              Django Dashboard + REST API              │
│    (Deployment control, live status, simulation)     │
├──────────────────────────────────────────────────────┤
│                    Orchestrator                       │
│   (Phase sequencing, checkpoint/resume, stop event)  │
├──────────┬───────────┬──────────┬────────────────────┤
│ Discovery│ Topology  │ Cabling  │   Configurator     │
│  Engine  │ Builder   │Validator │  (Ansible runner)   │
│          │           │          │  (Firmware SCP)     │
│ DHCP/CDP │ NetworkX  │  Intent  ├────────────────────┤
│ Netmiko  │   BFS     │ vs Real  │   Provisioner      │
│          │           │          │  (HPE Redfish/iLO)  │
│          │           │          │  (Meinberg REST)    │
├──────────┴───────────┴──────────┼────────────────────┤
│        Rollback / Factory Reset │  NetBox Integration │
│  (Reverse teardown to ZTP-ready)│  (Prepare Build)    │
├─────────────────────────────────┴────────────────────┤
│  Common: Parallel executor (BFS-depth grouping),     │
│  Checkpoint/resume, Laptop service monitor           │
└──────────────────────────────────────────────────────┘
```

## Supported Hardware

| Vendor   | Platform              | Method                                    |
|----------|-----------------------|-------------------------------------------|
| Cisco    | IOS/IOS-XE Switches   | SSH + CDP + Netmiko/Ansible (`cisco.ios`) |
| Cisco    | IOS/IOS-XE Routers    | SSH + CDP + Netmiko/Ansible (`cisco.ios`) |
| Cisco    | ASA / Firepower       | SSH + Netmiko/Ansible (`cisco.asa`)       |
| HPE      | DL325/DL360/DL380 Gen10 (iLO 5) | Redfish API                      |
| Meinberg | LANTIME NTP           | REST API (v1)                             |

## Key Features

- **Dashboard-driven** — Start, stop, and resume deployments from the browser
- **Checkpoint/resume** — Deployment state saved after every phase; survives laptop reboots
- **Factory reset** — Roll back all devices to factory-new state with safety confirmation
- **NetBox integration** — Pull device inventory from NetBox as single source of truth (optional; manual YAML still works)
- **Simulation mode** — Run a full deployment lifecycle with 16 simulated devices (no hardware needed)
- **Parallel execution** — Devices at the same BFS depth run concurrently; servers and NTP run fully parallel
- **Dead man's switch** — `reload in 5` before every network config push; auto-rollback on failure
- **Laptop service monitor** — Dashboard sidebar shows DHCP/TFTP/HTTP/SSH service status

## Prerequisites

- Python 3.11+
- Ansible 2.15+
- Deployment laptop with:
  - Ethernet NIC (for bootstrap network)
  - USB-to-serial adapter (fallback for console recovery)
  - Sufficient storage for firmware images and OS ISOs

## Quick Start

```bash
# Clone and install
git clone https://github.com/Comm4nd0/Bare-Metal-Automation.git
cd Bare-Metal-Automation
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Option A: Prepare from NetBox (if configured)
export BMA_NETBOX_URL="https://netbox.example.com"
export BMA_NETBOX_TOKEN="your-token"
bare-metal-automation prepare --node D001

# Option B: Manual inventory
cp configs/inventory/inventory.example.yaml configs/inventory/inventory.yaml
# Edit inventory.yaml with your device serials and roles

# Start the dashboard
bare-metal-automation serve

# Or run headless
bare-metal-automation deploy --inventory configs/inventory/inventory.yaml

# Resume a failed deployment
bare-metal-automation deploy --resume

# Factory reset
bare-metal-automation rollback --confirm
```

## Project Structure

```
bare-metal-automation/
├── src/bare_metal_automation/
│   ├── cli.py                # Click CLI entry point
│   ├── models.py             # Pydantic data models + enums
│   ├── inventory.py          # YAML inventory loader + validator
│   ├── orchestrator.py       # Phase sequencer with checkpoint/resume/stop
│   ├── common/
│   │   ├── checkpoint.py     # State serialisation (atomic JSON save/load)
│   │   ├── parallel.py       # ThreadPoolExecutor grouped by BFS depth
│   │   └── services.py       # Laptop service status (systemctl checks)
│   ├── discovery/
│   │   ├── engine.py         # Discovery orchestration
│   │   ├── dhcp.py           # dnsmasq wrapper (start/stop/parse leases)
│   │   ├── cdp.py            # CDP neighbour collection + parsing
│   │   ├── serial.py         # Serial number collection (show inventory)
│   │   └── matcher.py        # Serial-to-role device matching
│   ├── topology/
│   │   ├── builder.py        # Legacy topology builder
│   │   ├── graph.py          # NetworkX graph construction from CDP
│   │   ├── ordering.py       # BFS depth + outside-in config ordering
│   │   └── visualise.py      # Topology visualisation export
│   ├── cabling/
│   │   ├── validator.py      # Cabling validation orchestration
│   │   ├── intent.py         # Parse intended cabling from templates
│   │   ├── diff.py           # CDP actual vs intent comparison
│   │   ├── adapter.py        # Flexible port adaptation engine
│   │   └── report.py         # Structured cabling validation report
│   ├── configurator/
│   │   ├── network.py        # SSH config push with dead man's switch
│   │   ├── firmware.py       # SCP firmware upgrade + MD5 verify
│   │   └── validator.py      # Post-config validation (STP, trunks, routing)
│   ├── provisioner/
│   │   ├── server.py         # HPE Redfish provisioning (BIOS, RAID, SPP, OS, iLO)
│   │   ├── redfish.py        # Low-level Redfish API client
│   │   ├── ilo.py            # iLO-specific operations
│   │   ├── installer.py      # OS installation via virtual media
│   │   ├── pxe.py            # PXE boot server management
│   │   └── meinberg.py       # Meinberg LANTIME REST API provisioning
│   ├── config_media/         # Pillar 2: Config & Media Generation
│   │   ├── generate.py       # End-to-end generation orchestrator
│   │   ├── renderer.py       # Jinja2 config rendering from NetBox data
│   │   ├── inventory_export.py  # BMA inventory YAML generation
│   │   ├── firmware_catalogue.py # Firmware version/checksum catalogue
│   │   ├── media_collector.py   # Firmware + ISO collection
│   │   └── bundle_packager.py   # Self-contained deployment bundle packaging
│   ├── factory_reset/
│   │   ├── reset.py          # Factory reset orchestration
│   │   ├── sanitise.py       # Data sanitisation verification
│   │   └── certificate.py    # Reset certification/audit trail
│   ├── rollback/
│   │   ├── orchestrator.py   # Rollback phase sequencer
│   │   ├── network.py        # Cisco factory reset (write erase + reload)
│   │   ├── server.py         # HPE factory reset via Redfish
│   │   └── meinberg.py       # Meinberg factory reset via REST API
│   ├── netbox/
│   │   ├── client.py         # NetBox API client (pynetbox wrapper)
│   │   ├── loader.py         # Load node inventory from NetBox
│   │   ├── mapper.py         # Transform NetBox data to BMA spec
│   │   └── git.py            # Git repo clone/pull for templates + firmware
│   └── dashboard/            # Django app (WebSocket via Channels)
│       ├── models.py         # Deployment, Device, CablingResult, ActivityLog
│       ├── views.py          # HTML views + REST API endpoints
│       ├── consumers.py      # WebSocket consumers for live updates
│       ├── events.py         # Event system for real-time notifications
│       ├── routing.py        # ASGI + WebSocket routing
│       ├── deployment.py     # Background deployment thread runner
│       ├── rollback.py       # Background rollback thread runner
│       ├── simulation.py     # Simulated deployment lifecycle
│       ├── prepare.py        # NetBox "Prepare Build" workflow
│       └── api_client.py     # Python client for dashboard API
├── dashboard/                # Standalone Django project (deploy + fleet apps)
│   ├── deploy/               # Deployment management app (DRF API, WebSocket)
│   └── fleet/                # Fleet management app (site scanning, inventory)
├── orchestrator/             # Pipeline orchestration (site generation, fleet scan)
├── site_templates/           # Site template definitions (small/medium/large)
├── configs/
│   ├── templates/            # Jinja2 device config templates
│   ├── inventory/            # Device serial-to-role mappings
│   └── firmware/             # IOS images, iLO firmware, OS ISOs (gitignored)
├── ansible/                  # Playbooks, roles, group/host vars
├── tests/                    # Unit, integration, and dashboard tests
├── docs/                     # Architecture docs, roadmap, conversation history
└── scripts/                  # Helper scripts
```

## Deployment Phases

### Phase 0 — Bundle Ingestion & Pre-flight
Validate inventory, check firmware/ISO checksums, verify laptop NIC, start DHCP/TFTP services.

### Phase 1 — Discovery
Start dnsmasq on bootstrap subnet. SSH to discovered devices with factory defaults. Collect CDP neighbours and serial numbers. Match against inventory.

### Phase 2 — Topology & Cabling Validation
Build NetworkX adjacency graph from CDP data. Calculate BFS depth. Compare actual cabling against intended design. Categorise connections as correct, adaptable, mismatched, missing, or unexpected.

### Phase 3 — Firmware Upgrade
Push Cisco IOS/IOS-XE/ASA firmware via SCP. Verify MD5 checksums. Set boot variables. Reload. Parallel by BFS depth (outside-in).

### Phase 4 — Heavy Transfers
Transfer remaining firmware and OS ISOs while the network is still flat L2. HPE iLO firmware via Redfish. Stage ESXi/Windows ISOs for virtual media mount.

### Phase 5 — Network Configuration (Outside-In)
Push fully rendered configs in reverse BFS order (furthest from laptop first). Dead man's switch: `reload in 5` before applying, `reload cancel` on success. Post-config validation (STP root, trunks, routing adjacencies).

### Phase 6 — Laptop Pivot
Reconfigure laptop NIC onto production management VLAN. Verify connectivity to all device management IPs and iLO addresses.

### Phase 7 — Server Provisioning
HPE servers via iLO 5 Redfish: BIOS config, RAID setup, SPP install, OS install via virtual media. All run fully parallel.

### Phase 8 — NTP Provisioning
Meinberg LANTIME configuration: firmware update, network settings, NTP reference sources, access control, system settings.

### Phase 9 — Post-Install
Ansible OS-level hardening, packages, domain join, monitoring agents.

### Phase 10 — Final Validation
End-to-end connectivity tests, service health checks, deployment report stored in Django database.

## Development

```bash
# Run tests
pytest tests/

# Lint and type check
ruff check src/
mypy src/

# Run simulation (no real hardware needed)
bare-metal-automation simulate
```

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phased development plan.

## Licence

MIT
