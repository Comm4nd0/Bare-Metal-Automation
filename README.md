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
- **Site orchestration** — Generate, validate, drift-fix, and destroy entire sites in NetBox from templates
- **Fleet scanning** — Audit all sites for template version compliance
- **Config generation** — Render device configs from NetBox data + Jinja2 templates, package into deployment bundles
- **vInfra automation** — Deploy vCenter, configure NSX-T (segments, routing, firewall), set up vDS and vSAN
- **Simulation mode** — Run a full deployment lifecycle with 16 simulated devices (no hardware needed)
- **Parallel execution** — Devices at the same BFS depth run concurrently; servers and NTP run fully parallel
- **Dead man's switch** — `reload in 5` before every network config push; auto-rollback on failure
- **Laptop service monitor** — Dashboard sidebar shows DHCP/TFTP/HTTP/SSH service status

## Prerequisites

- Python 3.11+
- Ansible 2.15+
- Docker + Docker Compose v2 (for NetBox deployment)
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
```

### Deploy NetBox

```bash
sudo bash ./scripts/deploy_netbox.sh

# Options:
#   --netbox-version VERSION   NetBox version (default: 4.2.3)
#   --domain DOMAIN            FQDN (default: netbox.local)
#   --port PORT                HTTP port (default: 8080)
#   --superuser USER           Admin username (default: admin)
#   --data-dir DIR             Persistent data dir (default: /opt/netbox-data)
#   --skip-seed                Skip seeding BMA custom fields and device roles
#   --uninstall                Remove NetBox containers and volumes
```

### Generate a Site in NetBox

```bash
# Set NetBox connection
export BMA_NETBOX_URL="http://localhost:8080"
export BMA_NETBOX_TOKEN="your-token"

# Generate a full site from template (creates devices, VLANs, prefixes, cables, cluster)
bma-orchestrate \
  --site-slug d001 \
  --site-name "D001" \
  --template medium-site \
  --octet 100 \
  --output-dir ./output/d001

# Destroy a site and all its objects
bma-orchestrate --site-slug d001 --destroy --yes
```

### Deploy Hardware

```bash
# Option A: Prepare inventory from NetBox
bare-metal-automation prepare --node D001

# Option B: Manual inventory
cp configs/inventory/inventory.example.yaml configs/inventory/inventory.yaml

# Start the dashboard
bare-metal-automation serve

# Or run headless
bare-metal-automation deploy --inventory configs/inventory/inventory.yaml

# Resume a failed deployment
bare-metal-automation deploy --resume

# Factory reset (DESTRUCTIVE)
bare-metal-automation rollback --confirm
```

## CLI Reference

### `bare-metal-automation` — Main deployment CLI

| Subcommand | Description | Key Flags |
|---|---|---|
| `discover` | Discover devices via DHCP/CDP | `-i INVENTORY`, `-t TIMEOUT` |
| `validate` | Validate physical cabling | `-i INVENTORY` |
| `configure-network` | Configure network devices | `-i INVENTORY`, `--dry-run` |
| `upgrade-firmware` | Upgrade device firmware | `-i INVENTORY` |
| `provision-servers` | Provision HPE servers via Redfish | `-i INVENTORY` |
| `provision-ntp` | Provision Meinberg NTP devices | `-i INVENTORY` |
| `deploy` | Full end-to-end deployment | `-i INVENTORY`, `--dry-run`, `--resume` |
| `status` | Show checkpoint status | `-c CHECKPOINT` |
| `clear-checkpoint` | Remove saved checkpoint | `-c CHECKPOINT` |
| `simulate` | Run simulated deployment | `-n NAME` |
| `prepare` | Prepare build from NetBox | `-n NODE`, `--netbox-url`, `--netbox-token` |
| `rollback` | Factory reset all devices | `-i INVENTORY`, `--resume`, `--confirm` |
| `serve` | Start Django dashboard | `--host`, `--port`, `--mock` |

### `bma-orchestrate` — Site lifecycle pipeline

End-to-end pipeline: connect to NetBox, generate/regenerate site, validate, export inventory, package bundle.

```bash
# Create a new site
bma-orchestrate --site-slug alpha --site-name "Alpha Site" --template medium-site --octet 200

# Re-run on existing site (drift fix + validate + re-export)
bma-orchestrate --site-slug alpha --output-dir ./output/alpha

# Destroy site and all dependent objects
bma-orchestrate --site-slug alpha --destroy [--yes]
```

| Flag | Description |
|---|---|
| `--site-slug` | NetBox site slug (required) |
| `--site-name` | Human-readable name (for new sites) |
| `--template` | Template name: `small-site`, `medium-site`, `large-site` (required for new sites) |
| `--octet` | IP octet override — each site needs a unique octet (`10.{octet}.x.x`) |
| `--destroy` | Delete site and ALL dependent objects |
| `--yes` | Skip confirmation prompt |
| `--output-dir` | Output directory (default: `./output`) |
| `--netbox-url` | NetBox URL (env: `BMA_NETBOX_URL`) |
| `--netbox-token` | NetBox API token (env: `BMA_NETBOX_TOKEN`) |

### `bma-site-generate` — Generate a site from template

Lower-level command for site generation only (no validate/export/package).

```bash
bma-site-generate --template medium-site --site-name "Alpha" --site-slug alpha --octet 200
```

### `bma-site-regenerate` — Detect and fix drift

```bash
bma-site-regenerate --site alpha --mode report    # Report drift only
bma-site-regenerate --site alpha --mode fix        # Create/update missing objects
bma-site-regenerate --site alpha --mode rebuild --confirm  # Full rebuild
```

### `bma-fleet-scan` — Fleet compliance audit

```bash
bma-fleet-scan                          # Scan all sites
bma-fleet-scan --template medium-site   # Filter by template
bma-fleet-scan --format json            # JSON output
```

Exit code 0 = all current, 1 = outdated sites found.

### `bma-generate` — Config & media generation (Pillar 2)

Render device configs from NetBox data + Jinja2 templates and package into a deployment bundle.

```bash
bma-generate --tag D001 --output-dir bundles --archive
```

| Flag | Description |
|---|---|
| `--tag` | NetBox node tag (required, e.g., `D001`) |
| `--templates-dir` | Jinja2 templates directory (default: `configs/templates`) |
| `--catalogue` | Firmware catalogue YAML path |
| `--output-dir` | Output directory (default: `bundles`) |
| `--archive` | Create tar.gz bundle |
| `--dry-run` | Render without writing files |
| `--skip-media` | Configs + inventory only, skip firmware/ISOs |

## Site Templates

Templates define the full infrastructure for a site: devices, VLANs, IP prefixes, cabling, and vSphere clustering.

| Template | Devices | Missions | VMs | Default Octet |
|---|---|---|---|---|
| `small-site` | 9 | 3 | 9 | 100 |
| `medium-site` | 15 | 5 | 21 | 200 |
| `large-site` | 24+ | 10 | 50+ | 200 |

Each template generates:
- Manufacturers, device types, device roles, platforms
- Site, rack, devices with rack positions
- Management VLANs (mgmt, servers, guest, VoIP, iLO, vMotion, vSAN, NTP, backup)
- Mission VLANs (users, apps, data per mission)
- IP prefixes scoped to the site
- Physical cables between devices
- vSphere cluster with compute node membership

Cabling rules are in `site_templates/cabling/` with cable types (cat6a, mmf-om4) and color coding.

## Deployment Phases

| Phase | Name | Description |
|---|---|---|
| 0 | Pre-flight | Validate inventory, check checksums, verify laptop NIC, start DHCP/TFTP |
| 1 | Discovery | Start dnsmasq, SSH to devices with factory defaults, collect CDP + serials |
| 2 | Cabling Validation | Build NetworkX graph from CDP, calculate BFS depth, compare actual vs intended |
| 3 | Firmware Upgrade | Push firmware via SCP, verify MD5, set boot vars, reload (parallel by BFS depth) |
| 4 | Heavy Transfers | Transfer firmware/ISOs while network is still flat L2 |
| 5 | Network Config | Push configs in reverse BFS order with dead man's switch (`reload in 5`) |
| 6 | Laptop Pivot | Reconfigure laptop NIC onto production management VLAN |
| 7 | Server Provisioning | HPE iLO 5 Redfish: BIOS, RAID, SPP, OS install via virtual media (parallel) |
| 8 | NTP Provisioning | Meinberg LANTIME: firmware, network, NTP sources, access control |
| 9 | Post-Install | Ansible OS-level hardening, packages, domain join, monitoring agents |
| 10 | Final Validation | End-to-end connectivity, service health checks, deployment report |

## Virtual Infrastructure (Pillar 4)

BMA includes modules for post-deployment virtual infrastructure setup:

**vCenter** (`src/bare_metal_automation/vinfra/vcenter/`):
- VCSA deployment via `vcsa-deploy` CLI
- Datacenter, cluster, and ESXi host enrollment
- vDS (distributed vSwitch) creation and port-group configuration
- vSAN and datastore configuration
- HA and DRS enablement
- Content library setup and template upload

**NSX-T** (`src/bare_metal_automation/vinfra/nsx/`):
- NSX Manager OVA deployment and vCenter registration
- Edge node deployment and edge cluster creation
- Segment creation for management and mission tenants
- Tier-0 and Tier-1 gateway routing
- Distributed Firewall (DFW) rules for multi-tenant isolation
- Transport zone configuration

## Ansible Roles (Pillar 5)

Post-provisioning software deployment via `ansible/playbooks/phase9_software.yml`:

| Role | Description |
|---|---|
| `common` | Base OS hardening, packages, NTP, monitoring agent |
| `security` | Firewall rules, audit policy, security baselines |
| `bastion` | Jump host configuration with MFA |
| `domain-controller` | Active Directory domain services |
| `dns-server` | DNS server configuration |
| `certificate-authority` | PKI / certificate authority setup |
| `database-server` | MSSQL installation and backup jobs |
| `file-server` | File shares and DFS configuration |
| `backup-server` | Backup infrastructure setup |
| `application-server` | Application server deployment |
| `monitoring-server` | Prometheus, Grafana, SNMP exporter |
| `log-collector` | Elasticsearch, Logstash, Kibana (ELK) |
| `print-server` | Print services |
| `nps-radius` | NPS/RADIUS for network authentication |
| `wsus` | Windows Server Update Services |
| `sccm` | System Center Configuration Manager |
| `network-device-mgmt` | Network device management integration |

## Project Structure

```
bare-metal-automation/
├── src/bare_metal_automation/
│   ├── cli.py                   # Main CLI entry point
│   ├── models.py                # Pydantic data models + enums
│   ├── inventory.py             # YAML inventory loader + validator
│   ├── orchestrator.py          # Phase sequencer with checkpoint/resume/stop
│   ├── common/                  # Checkpoint, parallel executor, service monitor
│   ├── discovery/               # DHCP, CDP, serial matching
│   ├── topology/                # NetworkX graph, BFS ordering, visualisation
│   ├── cabling/                 # Intent vs actual cabling validation
│   ├── configurator/            # SSH config push, firmware SCP, post-validation
│   ├── provisioner/             # HPE Redfish, iLO, PXE, Meinberg REST
│   ├── config_media/            # Pillar 2: config rendering, bundle packaging
│   ├── factory_reset/           # Factory reset, sanitisation, certification
│   ├── rollback/                # Reverse teardown orchestration
│   ├── netbox/                  # NetBox API client, loader, mapper
│   ├── vinfra/                  # Pillar 4: vCenter + NSX-T automation
│   │   ├── vcenter/             # VCSA deploy, cluster, vDS, vSAN, HA/DRS
│   │   └── nsx/                 # NSX Manager, edge, segments, routing, firewall
│   └── dashboard/               # Django app (WebSocket, deployment control)
├── dashboard/                   # Standalone Django project
│   ├── deploy/                  # Deployment tracking (phases, device states)
│   └── fleet/                   # Fleet compliance scanning
├── orchestrator/                # Pipeline orchestration
│   ├── orchestrate.py           # End-to-end pipeline (generate → validate → export → package)
│   ├── site_generate.py         # Create sites in NetBox from templates
│   ├── site_regenerate.py       # Drift detection and remediation
│   ├── fleet_scan.py            # Fleet-wide template compliance audit
│   └── validators.py            # Site validation against templates
├── site_templates/              # Site template definitions
│   ├── small-site.yaml          # 9 devices, 3 missions
│   ├── medium-site.yaml         # 15 devices, 5 missions
│   ├── large-site.yaml          # 24+ devices, 10 missions
│   └── cabling/                 # Physical cable connection rules
├── configs/
│   ├── templates/               # Jinja2 device config templates
│   ├── inventory/               # Device serial-to-role mappings
│   └── firmware/                # IOS images, iLO firmware, OS ISOs (gitignored)
├── ansible/                     # Pillar 5: playbooks and roles
│   ├── playbooks/               # phase9_software.yml, network_device_mgmt.yml
│   └── roles/                   # 17 roles (common, security, domain-controller, etc.)
├── scripts/
│   └── deploy_netbox.sh         # Docker-based NetBox deployment
├── tests/                       # Unit, integration, and dashboard tests
├── firmware_catalogue.yaml      # Firmware versions and checksums
└── docs/                        # Architecture docs, roadmap, conversation history
```

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
