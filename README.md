# ZTP-Forge

**Zero-Touch Provisioning for bare-metal infrastructure deployments.**

ZTP-Forge automates the complete lifecycle from factory-new hardware to fully configured production infrastructure — triggered by a single button on a deployment dashboard.

## What It Does

A deployment laptop running ZTP-Forge connects to a rack of factory-new Cisco switches, routers, firewalls, and HPE servers. With no out-of-band management network, the system:

1. **Discovers** all devices via DHCP and CDP/LLDP on the factory-default flat network
2. **Identifies** each device by serial number and matches it to its intended role
3. **Maps** the physical topology and calculates safe configuration ordering
4. **Validates** cabling against the intended design before touching any config
5. **Configures** network devices outside-in (furthest from laptop first)
6. **Provisions** servers via HPE iLO Redfish API (firmware, BIOS, OS install)
7. **Hardens** and finalises all devices with post-install configuration

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  ZTP-Forge Dashboard                 │
│         (Flask + WebSocket live status)              │
├─────────────────────────────────────────────────────┤
│                   Orchestrator                       │
│        (Phase sequencing, state machine)             │
├──────────┬──────────┬──────────┬────────────────────┤
│ Discovery│ Topology │ Cabling  │   Configurator     │
│  Engine  │ Builder  │Validator │  (Ansible runner)   │
│          │          │          ├────────────────────┤
│ DHCP/CDP │  Graph   │  Intent  │   Provisioner      │
│  SNMP    │   BFS    │ vs Real  │  (Redfish/iLO)     │
└──────────┴──────────┴──────────┴────────────────────┘
```

## Supported Hardware

| Vendor | Platform | Method |
|--------|----------|--------|
| Cisco  | IOS/IOS-XE Switches | SSH + CDP + Ansible (`cisco.ios`) |
| Cisco  | ASA / Firepower | SSH + Ansible (`cisco.asa` / `cisco.fmc`) |
| Cisco  | IOS/IOS-XE Routers | SSH + CDP + Ansible (`cisco.ios`) |
| HPE    | DL325 Gen10 (iLO 5) | Redfish API |

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
git clone https://github.com/<your-org>/ztp-forge.git
cd ztp-forge
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Configure your deployment
cp configs/inventory/inventory.example.yaml configs/inventory/inventory.yaml
# Edit inventory.yaml with your device serials and roles

# Place firmware/ISOs
# Copy IOS images to configs/firmware/
# Copy OS ISOs to configs/firmware/

# Start the dashboard
ztp-forge serve

# Or run headless
ztp-forge deploy --inventory configs/inventory/inventory.yaml
```

## Project Structure

```
ztp-forge/
├── src/
│   ├── common/           # Shared utilities, logging, config
│   ├── discovery/         # DHCP server, CDP/LLDP collection, device identification
│   ├── topology/          # Graph builder, BFS ordering, topology visualisation
│   ├── cabling/           # Intent parser, CDP-vs-intent diff, adaptation engine
│   ├── configurator/      # Ansible runner, config generation, rollback logic
│   ├── provisioner/       # Redfish client, iLO management, PXE/OS install
│   └── dashboard/         # Flask app, WebSocket status, topology display
├── configs/
│   ├── templates/         # Jinja2 device config templates
│   │   ├── switches/
│   │   ├── routers/
│   │   ├── firewalls/
│   │   └── servers/
│   ├── inventory/         # Device serial-to-role mappings
│   └── firmware/          # IOS images, iLO firmware, OS ISOs (gitignored)
├── ansible/
│   ├── playbooks/         # Device configuration playbooks
│   ├── roles/             # Ansible roles per device type
│   ├── group_vars/        # Group variables
│   └── host_vars/         # Per-host variables (auto-generated)
├── tests/
│   ├── unit/              # Unit tests
│   └── integration/       # Integration tests (with mock devices)
├── docs/                  # Architecture docs, runbooks
└── scripts/               # Helper scripts (DHCP setup, serial console, etc.)
```

## Deployment Phases

### Phase 0 — Pre-flight
Validate inventory file, check firmware images exist, verify laptop NIC configuration, start DHCP/TFTP services.

### Phase 1 — Discovery
Hand out DHCP leases, SSH to each device with factory defaults, pull CDP neighbour tables and serial numbers, match against inventory.

### Phase 2 — Topology & Cabling Validation
Build adjacency graph from CDP data, calculate BFS depth from laptop, parse intended configs for expected connections, diff actual vs intended, present report on dashboard.

### Phase 3 — Heavy Transfers
While network is still on factory defaults (flat L2), transfer all firmware images and OS ISOs to target devices. Mount ISOs via Redfish virtual media. Kick off PXE boots. Wait for all transfers to complete before proceeding.

### Phase 4 — Network Configuration (Outside-In)
Configure devices in reverse BFS order (furthest from laptop first). Each device: apply to running-config → validate → `write mem`. Use `reload in 5` as dead man's switch on each device.

### Phase 5 — Laptop Pivot
Reconfigure laptop NIC onto production management VLAN. Re-establish connectivity to all devices on their production addresses.

### Phase 6 — Server Post-Install
Ansible configures OS-level settings: hardening, packages, domain join, monitoring agents, application deployment.

### Phase 7 — Final Validation
End-to-end connectivity tests, service health checks, generate deployment report.

## Configuration

### Inventory File

```yaml
# configs/inventory/inventory.yaml
deployment:
  name: "DC-Rack-42"
  bootstrap_subnet: "10.255.0.0/16"
  laptop_ip: "10.255.255.1"
  management_vlan: 100

devices:
  FOC2145X0AB:
    role: core-switch
    hostname: sw-core-01
    template: switches/core.j2
    platform: cisco_ios

  FOC2145X0CD:
    role: access-switch
    hostname: sw-access-01
    template: switches/access.j2
    platform: cisco_ios

  JAD1234567:
    role: border-router
    hostname: rtr-border-01
    template: routers/border.j2
    platform: cisco_ios

  JMX0987654:
    role: perimeter-firewall
    hostname: fw-perim-01
    template: firewalls/perimeter.j2
    platform: cisco_asa

  CZ12345678:
    role: compute-node
    hostname: svr-compute-01
    template: servers/compute.j2
    platform: hpe_dl325_gen10
    ilo_firmware: "ilo5_280.bin"
    os_iso: "rhel-9.3-x86_64.iso"
```

## Development

```bash
# Run tests
pytest tests/

# Run linting
ruff check src/
mypy src/

# Run with mock devices (no real hardware needed)
ztp-forge serve --mock
```

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phased development plan.

## Licence

MIT
