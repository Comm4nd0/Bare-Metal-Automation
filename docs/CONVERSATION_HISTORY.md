# ZTP-Forge Conversation History

This document tracks the history of AI-assisted development sessions on the ZTP-Forge project, providing context for future conversations.

---

## Project Overview

- **Repository**: `Comm4nd0/Bare-Metal-Automation`
- **Package name**: `ztp-forge`
- **Author**: Marco
- **Python**: 3.11+
- **Build system**: Hatchling
- **License**: MIT

ZTP-Forge is a zero-touch provisioning tool for bare-metal infrastructure. It automates the full lifecycle from factory-new Cisco switches/routers/firewalls and HPE servers to fully configured production infrastructure, driven from a deployment laptop.

### Key technical choices

- **CLI**: Click
- **Dashboard**: Django 5.0 (originally planned as Flask, changed to Django in PR #1)
- **Network automation**: Netmiko + Ansible (cisco.ios, cisco.asa, cisco.fmc collections)
- **Server provisioning**: HPE iLO 5 via Redfish API
- **Topology**: NetworkX graph with BFS ordering
- **Config templates**: Jinja2
- **Linting**: Ruff, mypy (strict mode)
- **Testing**: pytest with coverage

---

## Session Log

### Session 1 — Initial Scaffold

**Date**: 2026-03-29
**Commits**:
- `df1b007` — Initial scaffold: ZTP-Forge zero-touch provisioning framework
- `74b11e9` — Add version string to ztp_forge package

**What was done**:
- Created the full project structure with `pyproject.toml`, `.gitignore`, README, and ROADMAP
- Scaffolded all core modules under `src/ztp_forge/`:
  - `cli.py` — Click-based CLI entry point
  - `models.py` — Pydantic data models
  - `inventory.py` — Inventory YAML loader
  - `orchestrator.py` — Phase-based deployment orchestrator
  - `discovery/engine.py` — DHCP/CDP/SNMP device discovery
  - `topology/builder.py` — NetworkX graph + BFS ordering
  - `cabling/validator.py` — CDP-vs-intent cabling validation
  - `configurator/network.py` — Ansible-based config push with dead man's switch
  - `provisioner/` — Redfish/iLO server provisioning (stub)
  - `dashboard/` — Dashboard app (initially Flask stub)
- Created example inventory at `configs/inventory/inventory.example.yaml`
- Created core switch Jinja2 template at `configs/templates/switches/core.j2`
- Set up Ansible directory structure with playbooks, roles, group/host vars
- Wrote the development ROADMAP with 6 milestones

**Decisions made**:
- Package lives under `src/ztp_forge/` (src layout)
- 7-phase deployment model: Pre-flight → Discovery → Topology/Cabling → Heavy Transfers → Network Config → Laptop Pivot → Server Post-Install → Final Validation
- Configuration push uses "outside-in" ordering (furthest device from laptop first)
- `reload in 5` used as dead man's switch during config pushes
- Bootstrap network uses 10.255.0.0/16 subnet

---

### Session 2 — Django Dashboard (PR #1)

**Date**: 2026-03-29
**Branch**: `claude/django-automation-status-ui-KDdM1`
**PR**: #1 (merged)
**Commits**:
- `d0d108f` — Add Django dashboard for automation status with read/write API

**What was done**:
- Replaced the Flask dashboard stub with a full Django application
- Created Django models for:
  - `Deployment` — tracks deployment name, status, phases, timestamps
  - `Device` — per-device status with serial, role, hostname, IP, platform
  - `CablingResult` — stores cabling validation diffs
  - `ActivityLog` — event log with severity levels
- Built HTML templates using a clean base layout with:
  - Deployment list and detail views
  - Device detail view
  - No-deployment placeholder page
- Created a REST-style API (Django views, not DRF) for the automation process to push updates:
  - `POST /api/deployments/` — create deployment
  - `POST /api/deployments/<id>/devices/` — register device
  - `PUT /api/devices/<id>/status/` — update device status
  - `POST /api/deployments/<id>/cabling/` — submit cabling results
  - `POST /api/deployments/<id>/logs/` — submit log entries
  - `PUT /api/deployments/<id>/phase/` — update deployment phase
- Added `api_client.py` — Python client for the automation code to call the dashboard API
- Added `load_mock_data` management command for testing
- Added Django to `pyproject.toml` dependencies

**Decisions made**:
- Chose Django over Flask for the dashboard (more batteries-included for models/admin/ORM)
- No Django REST Framework — kept it simple with plain JSON views
- Dashboard uses SQLite by default (sufficient for single-laptop deployment)
- API is designed to be called by the orchestrator during deployments (push model)

---

### Session 3 — Conversation History Doc

**Date**: 2026-03-29
**Branch**: `claude/add-conversation-history-doc-3FMn6`

**What was done**:
- Created this document (`docs/CONVERSATION_HISTORY.md`) to track project history across AI sessions

### Session 4 — Device Firmware, OS & Provisioning

**Date**: 2026-03-29
**Branch**: `claude/configure-device-firmware-os-MN9dJ`

**What was done**:
- Created `configurator/firmware.py` — Cisco network device firmware upgrade via SCP (version check, transfer, MD5 verify, boot var, reload, post-verify)
- Created `provisioner/server.py` — HPE server provisioning via Redfish/iLO 5:
  - iLO firmware update
  - BIOS configuration (diff-based, only applies changes)
  - RAID/Smart Storage configuration (logical drive creation, clear existing)
  - HPE SPP installation via virtual media
  - OS installation via virtual media (with kickstart support)
  - iLO production config (networking, users, SNMP, NTP)
- Created `provisioner/meinberg.py` — Meinberg LANTIME NTP provisioning:
  - Firmware/OS upload and install
  - Network configuration (static IP, VLAN, DNS)
  - NTP reference sources (GPS, PTP, external NTP)
  - NTP service config (access control, stratum, authentication)
  - System settings (timezone, syslog, SNMP)
  - User account management
- Created `common/parallel.py` — parallel execution engine:
  - Groups devices by BFS depth for outside-in parallel processing
  - Network devices at same depth run concurrently (safe — no dependency)
  - Stops on failure to prevent configuring closer devices when further ones fail
  - Independent devices (servers, NTP) all run fully in parallel
- Updated `models.py`:
  - Added platforms: HPE DL360/DL380 Gen10, Meinberg LANTIME
  - Added role: ntp-server
  - Added granular device states: firmware_upgrading/upgraded, bios_configuring/configured, raid_configuring/configured, spp_installing/installed, os_installing/installed, ilo_configuring/configured
  - Added deployment phases: firmware_upgrade, ntp_provision
- Updated `inventory.py` — expanded DeviceSpec with firmware, BIOS, RAID, SPP, iLO, NTP fields
- Updated `dashboard/models.py` — all new platform/role/state/phase choices with CSS classes and icons
- Updated `orchestrator.py` — wired in all new phases with parallel execution
- Updated `cli.py` — added `upgrade-firmware` and `provision-ntp` commands
- Updated `inventory.example.yaml` — comprehensive examples for all device types with full config
- Created Django migration `0002_alter_deployment_phase_alter_device_platform_and_more.py`

**Decisions made**:
- Parallel execution uses ThreadPoolExecutor grouped by BFS depth — devices at the same depth can safely run concurrently since they don't sit on each other's management paths
- Network device firmware and config respect outside-in ordering (stop on failure at any depth)
- Server and NTP provisioning run fully parallel (independent devices, accessed via iLO / management API)
- Redfish client is a thin wrapper around requests — no external iLO library dependency
- Meinberg provisioning uses the LANTIME REST API (v1)

### Session 5 — Simulation Mode

**Date**: 2026-03-29
**Branch**: `claude/add-simulation-mode-b1VNQ`

**What was done**:
- Added full simulation mode to the dashboard — runs a complete deployment lifecycle without real hardware
- Created `dashboard/simulation.py` — core simulation engine:
  - Runs in a background thread, writes directly to Django ORM
  - Progresses through all 13 deployment phases with realistic timing (~2 min total)
  - Simulates 16 devices: 1 core switch (IOS), 5 dist switches (IOS-XE), 1 access switch (IOS), 1 border router (IOS), 1 firewall (ASA), 5 ESXi compute servers (3x DL325, 2x DL360), 1 Windows BUS backup server (DL380), 1 Meinberg NTP
  - Topology: laptop → core → {dist switches, access switch, router, firewall, BUS, NTP} → {ESXi servers via access switch}
  - Generates realistic cabling validation results (correct, wrong_port, wrong_device, missing)
  - HPE servers walk through full state lifecycle: bios → raid → spp → os → ilo → provisioned
  - Includes simulated warnings (SSH timeout retry, cabling issues, NTP GPS lock delay)
  - Start/stop/status API with thread-safe controls
- Created `management/commands/run_simulation.py` — CLI entry point (`python manage.py run_simulation`)
- Added `ztp-forge simulate` CLI command
- Added 3 API endpoints:
  - `POST /api/simulation/start/` — start a simulation
  - `POST /api/simulation/stop/` — stop running simulation
  - `GET /api/simulation/status/` — check if simulation is running
- Updated `no_deployment.html` — "Start Simulation" button on empty dashboard
- Updated `base.html` — navbar indicator with pulsing dot when simulation is running, stop button
- Updated `index.html` — auto-refresh now reloads page on phase/state changes (not just badge text), simulation badge next to phase badge

**Decisions made**:
- Background thread (not Celery/Channels) — simplest approach, existing 5s polling picks up all changes
- Direct ORM writes from thread (not HTTP API calls) — faster, no network round-trip needed
- 16 devices covering all platform types: Cisco IOS, IOS-XE, ASA, HPE DL325/DL360/DL380, Meinberg
- Stop event checked every 0.5s via interruptible sleep helper
- Double-start prevented (returns 409 Conflict)

### Session 6 — Checkpoint/Resume

**Date**: 2026-03-30
**Branch**: `claude/add-checkpoint-resume-fyaWE`

**What was done**:
- Created `common/checkpoint.py` — state serialization/deserialization module:
  - Serializes `DeploymentState` (devices, topology, cabling results, errors, warnings) to JSON
  - Deserializes all models back including enums (DeviceState, DeviceRole, DevicePlatform)
  - Atomic file writes (write to `.tmp` then rename) to prevent corruption on power loss
  - Save/load/remove checkpoint file operations
- Updated `orchestrator.py`:
  - Added `PHASE_ORDER` constant listing all phases in execution order
  - Added `_save_checkpoint()` after every phase transition in `run_full_deployment()`
  - Added `_should_skip()` logic to skip already-completed phases on resume
  - Added `from_checkpoint()` class method to reconstruct Orchestrator from a checkpoint file
  - Added `resume` parameter to `run_full_deployment()` — skips phases up to the last checkpoint
  - Checkpoint is automatically deleted on successful deployment completion
  - On failure, checkpoint is saved with `FAILED` phase so the user can inspect and retry
- Updated `cli.py`:
  - Added `--resume` flag and `--checkpoint` option to `deploy` command
  - Added `status` command to inspect a saved checkpoint
  - Added `clear-checkpoint` command to remove a checkpoint file
- Fixed `pyproject.toml` — corrected `packages` from `["src"]` to `["src/ztp_forge"]` (was preventing editable install from working)
- Created `tests/unit/test_checkpoint.py` with 15 tests covering:
  - Serialization round-trip (state, devices, CDP neighbours, cabling results, enums, None handling)
  - File I/O (save/load, missing file, remove, atomic write, valid JSON)
  - Orchestrator resume (from_checkpoint, should_skip logic, phase order completeness)

**Decisions made**:
- Checkpoint is a single JSON file (`.ztp-checkpoint.json` by default) — simple, human-readable, no DB dependency
- State is saved after each phase, not within phases — provides coarse-grained resume points
- On resume, phases are skipped based on the last completed phase in the checkpoint
- Atomic write (tmp + rename) prevents corrupt checkpoints from partial writes
- Checkpoint is removed on successful completion to prevent stale resumes

### Session 7 — Factory Reset Automation

**Date**: 2026-03-30
**Branch**: `claude/factory-reset-automation-dyjGO`

**What was done**:
- Created `resetter/` module with three device-type-specific resetters:
  - `resetter/network.py` — Cisco network device factory reset via SSH (`write erase` + `reload`)
    - Tries production credentials first, falls back to factory defaults
    - Handles interactive prompts (confirm, save prompt)
  - `resetter/server.py` — HPE server factory reset via Redfish/iLO 5
    - Resets BIOS to factory defaults (`Bios.ResetBios` action)
    - Clears all RAID logical drives (DELETE via SmartStorage API)
    - Resets iLO to factory defaults (`HpeiLO.ResetToFactoryDefaults` with `ResetType: Default` to preserve network)
    - Reboots server to apply changes
  - `resetter/meinberg.py` — Meinberg NTP factory reset via REST API
    - Issues factory reset with `preserve_network: true` to keep device reachable
    - Waits for device reboot with factory-default credentials
- Updated `common/parallel.py`:
  - Added `ascending` parameter to `group_devices_by_depth()` for inside-out ordering
  - Added `run_parallel_by_depth_ascending()` for reset operations (shallowest depth first)
- Updated `orchestrator.py`:
  - Added `run_factory_reset()` method with 3-phase reset sequence: NTP → Servers → Network
  - Added helper methods: `_reset_meinberg_devices()`, `_reset_hpe_servers()`, `_reset_network_devices()`
  - Added `FACTORY_RESET` to `PHASE_ORDER`
- Updated `cli.py`:
  - Added `factory-reset` command with `--inventory`, `--dry-run`, `--device-type`, `--confirm`, `--timeout` options
  - Interactive confirmation prompt for safety (skippable with `--confirm`)
  - Device type filtering: `all`, `cisco`, `hpe`, `meinberg`
- Updated `models.py`:
  - Added `RESETTING` and `RESET_COMPLETE` to `DeviceState` enum
  - Added `FACTORY_RESET` to `DeploymentPhase` enum
- Updated `dashboard/models.py`:
  - Added new state/phase choices and CSS classes for dashboard display
- Created Django migration `0003_alter_deployment_phase_alter_device_state.py`
- Created `tests/unit/test_resetter.py` with 20 tests covering:
  - Network resetter: success, connection failure, exception handling, credential fallback, save prompt handling
  - HPE server resetter: success, BIOS failure, connection failure, credential ordering, BIOS API, RAID clearing
  - Meinberg resetter: success, connection failure, reboot timeout
  - Parallel ordering: ascending depth sort (inside-out), descending default (outside-in)
  - Model states: RESETTING, RESET_COMPLETE, FACTORY_RESET phase existence

**Decisions made**:
- Reset order: NTP and servers first (leaf devices, don't carry management traffic), then network devices inside-out (ascending BFS depth — closest to laptop first, furthest last)
- Inside-out ordering is the reverse of provisioning's outside-in ordering — preserves management connectivity throughout the reset process
- iLO reset uses `ResetType: Default` (not `All`) to preserve network settings, keeping iLO reachable after reset
- Meinberg factory reset preserves network settings for the same reason
- Interactive confirmation required by default for safety (destructive operation); `--confirm` flag skips the prompt for scripted use
- Device type filtering (`--device-type`) allows selective reset of only certain infrastructure components

---

## Current State of the Project

### What exists (implemented)

- Full project scaffold with all module stubs
- Django dashboard with models, views, templates, and API
- Example inventory and core switch config template
- Development tooling config (ruff, mypy, pytest)
- Network device firmware upgrade (SCP + reload + verify)
- HPE server provisioning (BIOS, RAID, SPP, OS, iLO via Redfish)
- Meinberg NTP provisioning (firmware, network, NTP config, system settings)
- Parallel execution engine respecting BFS depth constraints
- **Checkpoint/resume** — deployment can be stopped and restarted at any phase boundary
- **Factory reset** — reverse provisioning to return all devices to factory defaults (Cisco write erase, HPE BIOS/RAID/iLO reset, Meinberg factory reset)

### What still needs to be built (from ROADMAP)

- **Milestone 1 (Foundation/MVP)**: DHCP server wrapper, CDP collector, serial collector, device matcher, ~~mock device simulator~~, unit tests
- **Milestone 2 (Cabling Validation)**: Intent parser, cabling diff engine, adaptation engine
- **Milestone 3 (Network Config)**: Config renderer, Ansible dynamic inventory, playbooks, dead man's switch implementation, rollback handler
- **Milestone 4 (Server Provisioning)**: ~~Redfish client~~, ~~iLO discovery~~, ~~firmware update~~, ~~BIOS config~~, ~~virtual media~~, PXE (partially done — virtual media boot implemented)
- **Milestone 5 (Dashboard)**: WebSocket live updates, topology visualisation (D3.js/vis.js), deploy button, log viewer, ~~simulation mode~~
- **Milestone 6 (Hardening)**: Serial console fallback, retry logic, ~~state persistence~~, multi-NIC, LLDP

### Known issues / open items

- The `dashboard/` was changed from Flask to Django but the README architecture diagram still references "Flask + WebSocket" — may want to update this
- No unit tests for the new provisioning modules yet
- Meinberg API paths are based on the LANTIME REST API spec — may need adjustment for specific firmware versions

---

## Architecture Notes for Future Sessions

### Source layout
```
src/ztp_forge/
├── __init__.py              # Version string
├── cli.py                   # Click CLI (discover, validate, configure, provision, serve)
├── models.py                # Dataclass models + enums
├── inventory.py             # YAML inventory loader + validator
├── orchestrator.py          # Phase-based state machine with parallel execution
├── common/
│   └── parallel.py          # ThreadPoolExecutor grouped by BFS depth
├── discovery/engine.py      # DHCP + CDP + SNMP discovery
├── topology/builder.py      # NetworkX graph + BFS
├── cabling/validator.py     # CDP vs intent diff
├── configurator/
│   ├── network.py           # SSH config push with dead man's switch
│   └── firmware.py          # SCP firmware upgrade + verify
├── provisioner/
│   ├── server.py            # HPE Redfish provisioning (BIOS/RAID/SPP/OS/iLO)
│   └── meinberg.py          # Meinberg NTP REST API provisioning
└── dashboard/               # Django app (models, views, API, templates, simulation)
```

### Deployment phases (in order)
0. Pre-flight — validate inventory, check firmware, verify NIC
1. Discovery — DHCP leases, SSH, CDP, serial matching
2. Topology & Cabling — build graph, BFS, validate against intent
3. Firmware Upgrade — network device IOS/ASA images (parallel by depth)
4. Heavy Transfers — firmware/ISO push while network is flat L2
5. Network Config — outside-in config push with dead man's switch (parallel by depth)
6. Laptop Pivot — reconfigure laptop NIC to production VLAN
7. Server Provisioning — HPE BIOS/RAID/SPP/OS/iLO via Redfish (fully parallel)
8. NTP Provisioning — Meinberg firmware/config via REST API (fully parallel)
9. Post-Install — OS hardening, packages, domain join
10. Final Validation — end-to-end tests, health checks, report

### Supported hardware
- Cisco IOS/IOS-XE switches and routers (SSH + CDP + Netmiko)
- Cisco ASA / Firepower firewalls (SSH + Netmiko)
- HPE DL325/DL360/DL380 Gen10 servers (iLO 5 Redfish API)
- Meinberg LANTIME NTP appliances (REST API)
