# Bare Metal Automation Conversation History

This document tracks the history of AI-assisted development sessions on the Bare Metal Automation project, providing context for future conversations.

---

## Project Overview

- **Repository**: `Comm4nd0/Bare-Metal-Automation`
- **Package name**: `bare-metal-automation`
- **Author**: Marco
- **Python**: 3.11+
- **Build system**: Hatchling
- **License**: MIT

Bare Metal Automation is a zero-touch provisioning tool for bare-metal infrastructure. It automates the full lifecycle from factory-new Cisco switches/routers/firewalls and HPE servers to fully configured production infrastructure, driven from a deployment laptop.

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
- `df1b007` — Initial scaffold: Bare Metal Automation zero-touch provisioning framework
- `74b11e9` — Add version string to bare_metal_automation package

**What was done**:
- Created the full project structure with `pyproject.toml`, `.gitignore`, README, and ROADMAP
- Scaffolded all core modules under `src/bare_metal_automation/`:
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
- Package lives under `src/bare_metal_automation/` (src layout)
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
- Added `bare-metal-automation simulate` CLI command
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
- Fixed `pyproject.toml` — corrected `packages` from `["src"]` to `["src/bare_metal_automation"]` (was preventing editable install from working)
- Created `tests/unit/test_checkpoint.py` with 15 tests covering:
  - Serialization round-trip (state, devices, CDP neighbours, cabling results, enums, None handling)
  - File I/O (save/load, missing file, remove, atomic write, valid JSON)
  - Orchestrator resume (from_checkpoint, should_skip logic, phase order completeness)

**Decisions made**:
- Checkpoint is a single JSON file (`.bma-checkpoint.json` by default) — simple, human-readable, no DB dependency
- State is saved after each phase, not within phases — provides coarse-grained resume points
- On resume, phases are skipped based on the last completed phase in the checkpoint
- Atomic write (tmp + rename) prevents corrupt checkpoints from partial writes
- Checkpoint is removed on successful completion to prevent stale resumes

### Session 7 — Rename to Bare Metal Automation + Laptop Service Status

**Date**: 2026-03-30
**Branch**: main

**What was done**:
- Renamed project from ZTP-Forge to Bare Metal Automation (BMA):
  - `src/ztp_forge/` → `src/bare_metal_automation/` (`git mv`)
  - Package name: `bare-metal-automation`, CLI: `bare-metal-automation`
  - All imports, docstrings, display strings, env vars, config defaults updated
  - Env vars: `ZTP_FORGE_*` → `BMA_*`; checkpoint: `.bma-checkpoint.json`
  - Config defaults: `ztpadmin` → `bmaadmin`, `ztp-monitoring` → `bma-monitoring`
- Added laptop service status card to the dashboard sidebar:
  - New module `common/services.py` — checks DHCP, TFTP, HTTP, SSH via `systemctl is-active`
  - New API endpoint `GET /api/services/` in views.py + urls.py
  - Dashboard `index.html` sidebar now shows a "Laptop Services" card above the activity log
  - JS polls `/api/services/` every 15 seconds and updates the card in-place

**Decisions made**:
- Service detection via systemd (`systemctl is-active`) — handles multiple candidates (e.g. dnsmasq OR isc-dhcp-server for DHCP)
- Service card updates in-place via JS (no full page reload needed for service status changes)
- Poll interval 15s for services (slower than device status at 5s — services change rarely)

### Session 8 — Deployment Control Buttons (Start / Stop / Resume)

**Date**: 2026-03-30
**Branch**: main

**What was done**:
- Added deployment control buttons to the dashboard so deployments can be driven from the browser:
  - **Start Deployment** — launches a real deployment using the configured inventory
  - **Stop After Phase** — graceful stop that halts between phases (never mid-hardware-operation)
  - **Resume** — continues from the last checkpoint file
- Updated `orchestrator.py`:
  - Added `stop_event` (threading.Event) and `on_phase_change` callback params
  - Added `_check_stop()` method checked after every `_save_checkpoint()` call (~10 points)
  - `from_checkpoint()` now accepts `stop_event` and `on_phase_change` kwargs
- Added `"stopped"` phase to `dashboard/models.py` with migration
- Created `dashboard/deployment.py` — background thread runner mirroring `simulation.py` pattern:
  - Module-level thread, lock, stop event, deployment ID
  - `start_deployment()`, `stop_deployment()`, `resume_deployment()`, `deployment_status()`
  - Creates Orchestrator with `stop_event` and `on_phase_change` ORM callback
- Added 4 API endpoints in `views.py` + `urls.py`:
  - `POST /api/deployment/start/`, `stop/`, `resume/`
  - `GET /api/deployment/status/`
- Updated `dashboard()` view and `api_status()` to include `deployment_control` context
- Updated `index.html`:
  - Context-aware buttons in header (Start / Stop / Resume based on state)
  - JS functions `startDeployment()`, `stopDeployment()`, `resumeDeployment()`
  - Polling now detects deployment running state changes and reloads page
- Updated `no_deployment.html` — added "Start Deployment" button alongside simulation
- Updated `simulation.py` — mutual exclusion: `start_simulation()` checks `deployment_status()["running"]`

**Decisions made**:
- Deployment and simulation are mutually exclusive (cannot run simultaneously)
- Graceful stop only — sets a threading.Event, checked at phase boundaries after checkpoint saves
- Mirrors the simulation.py threading pattern exactly (proven, simple, no Celery needed)
- Buttons are server-rendered based on state, with JS polling for dynamic updates

### Session 9 — Rollback to Factory (Full Lifecycle Support)

**Date**: 2026-03-30
**Branch**: main

**What was done**:
- Implemented full "Rollback to Factory" capability for the deployable infrastructure kit lifecycle (Build → Ship → Deploy → Mission → Return → Rollback → Repeat)
- New `RollbackPhase` enum with 8 phases: pre_flight, ntp_reset, server_reset, laptop_pivot, network_reset, final_check, complete, failed
- New `DeviceState` values: `resetting`, `factory_reset`, `powered_off`
- Created `rollback/` package with 4 modules:
  - `network.py` — `NetworkResetter`: SSH `write erase` + `reload` for Cisco IOS/IOS-XE/ASA
  - `server.py` — `HPEServerResetter`: Redfish BIOS reset, RAID delete, virtual media eject, iLO factory reset (preserves network), power off
  - `meinberg.py` — `MeinbergResetter`: factory reset via API or manual config revert + reboot
  - `orchestrator.py` — `RollbackOrchestrator`: phase sequencer with checkpoint/resume/stop, reads deployment checkpoint to discover devices
- Created `dashboard/rollback.py` — background thread runner (mirrors deployment.py pattern)
- Added 4 API endpoints: `POST /api/rollback/start|stop|resume/`, `GET /api/rollback/status/`
- Dashboard UI:
  - "Rollback to Factory" button appears when deployment is `complete`
  - Safety confirmation modal: operator must type deployment name to confirm
  - Rollback progress bar with orange/red color scheme
  - Device states show resetting/factory_reset/powered_off with appropriate badges
  - Stop/Resume buttons during rollback
- Simulation now runs the full lifecycle: all 13 deployment phases followed by 6 rollback phases
- CLI: `bare-metal-automation rollback` command with `--resume` and confirmation prompt
- Triple mutual exclusion: deployment, simulation, and rollback cannot run simultaneously

**Decisions made**:
- Factory resets (not snapshots) — deterministic, simple, matches operational intent of "clean slate for next build"
- Rollback order: NTP → Servers (via management VLAN) → laptop pivot back to bootstrap → network devices (outside-in, core last)
- iLO factory reset uses `ResetType: "Default"` to preserve network access during reset
- Operator must type deployment name to confirm rollback (prevent accidental triggers by non-technical operators)
- Own rollback checkpoint file (`.bma-rollback-checkpoint.json`) — both checkpoints deleted on successful rollback

### Session 10 — NetBox Integration + Prepare Build

**Date**: 2026-03-30
**Branch**: main

**What was done**:
- Implemented NetBox as single source of truth for deployable node configurations
- Created `netbox/` package with 4 modules:
  - `client.py` — `NetBoxClient` wrapping pynetbox with operator-friendly error handling
  - `mapper.py` — Pure mapping functions: NetBox device/config context → BMA inventory spec format
  - `loader.py` — `NetBoxLoader`: queries NetBox, maps data, returns identical `DeploymentInventory`
  - `git.py` — `GitRepoManager`: auto clone/pull templates and firmware from a git repo
- Created `dashboard/prepare.py` — background thread runner for "Prepare Build" (8 phases: connect → fetch devices → fetch configs → fetch IPAM → map → sync git → verify files → generate YAML)
- Added 4 API endpoints: `GET /api/prepare/nodes/`, `POST /api/prepare/start|stop/`, `GET /api/prepare/status/`
- Dashboard UI: "Prepare Build from NetBox" card on no_deployment page with node dropdown, progress bar, error/success display
- CLI: `bare-metal-automation prepare --node D001` command with NetBox URL/token options
- Added `pynetbox>=7.3` dependency
- NetBox settings: `BMA_NETBOX_URL`, `BMA_NETBOX_TOKEN`, `BMA_NETBOX_TAG_PATTERN`, `BMA_GIT_REPO_URL`, `BMA_GIT_REPO_BRANCH`, `BMA_GIT_REPO_PATH`
- NetBox feature is optional — when `BMA_NETBOX_URL` is empty, Prepare Build is hidden, manual YAML workflow still works

**Decisions made**:
- `DeploymentInventory` is the contract boundary — NetBox loader produces identical output to YAML loader, zero downstream changes
- Devices in NetBox tagged with prefix per kit (D001, D002, D003); config contexts hold all structured config as JSON
- Templates and firmware live in a git repo, auto-cloned/pulled during preparation
- ROLE_MAP and PLATFORM_MAP in mapper.py translate NetBox slugs to BMA values
- Generated `inventory.yaml` written to disk for debugging and as fallback
- Operator flow: Prepare Build → Start Deployment → Rollback — all from dashboard buttons
- Quad mutual exclusion: prepare, deployment, simulation, rollback cannot run simultaneously

### Session 11 — Factory Reset Automation (standalone resetter module)

**Date**: 2026-03-30
**Branch**: `claude/factory-reset-automation-dyjGO`

**What was done**:
- Created `resetter/` module with three device-type-specific resetters:
  - `resetter/network.py` — Cisco network device factory reset via SSH (`write erase` + `reload`)
  - `resetter/server.py` — HPE server factory reset via Redfish/iLO 5
  - `resetter/meinberg.py` — Meinberg NTP factory reset via REST API
- Updated `common/parallel.py` with ascending depth ordering for reset operations
- Updated `orchestrator.py` with `run_factory_reset()` method
- Updated `cli.py` with `factory-reset` command
- Added `RESETTING`, `RESET_COMPLETE` device states and `FACTORY_RESET` phase
- Created `tests/unit/test_resetter.py` with 20 tests

**Note**: Session 9 (Rollback to Factory) on main implemented a more complete version of this functionality with dashboard integration, checkpoint/resume, and simulation support. This PR's `resetter/` module overlaps with `rollback/` — needs reconciliation.

### Session 12 — Config & Media Generation (Sprint 2 / Pillar 2)

**Date**: 2026-04-01
**Branch**: `luma/objective-bhabha`

**What was done**:
- Built the complete Config & Media Generation layer (Pillar 2) — transforms NetBox device data into a deployment bundle ready for offline provisioning.
- Created `src/bare_metal_automation/config_media/` package with 6 modules:
  - `renderer.py` — `ConfigRenderer`: Jinja2 rendering engine. Selects template from device's `config_template` custom field (falls back to role default). `build_context()` factory maps raw pynetbox records to `RenderContext`. Includes `VlanContext`, `InterfaceContext`, `MissionTenant` dataclasses. Strict-mode Jinja2 (raises on undefined vars). `render_all()` collects errors and raises aggregated `RuntimeError`.
  - `inventory_export.py` — `InventoryExporter`: generates `inventory.yaml` from NetBox device specs + deployment metadata. `from_netbox()` factory wires together `NetBoxClient` + `mapper`. Enriches specs with config filename, firmware filename, media paths.
  - `firmware_catalogue.py` — `FirmwareCatalogue`: loads `configs/firmware_catalogue.yaml`, resolves `(platform, version)` → `FirmwareEntry` with full path. Handles network firmware, HPE SPP ISO, iLO firmware, OS ISOs. `verify_all()` checks files exist before collection (strict/non-strict mode).
  - `media_collector.py` — `MediaCollector`: copies firmware/ISOs/certs to bundle staging dir, verifies SHA-256 after each copy. Thread-safe. `collect_batch()` tolerates individual failures. `write_checksums_file()` writes sha256sum-compatible manifest.
  - `bundle_packager.py` — `BundlePackager`: assembles manifest.yaml, checksums.sha256, ansible hosts.ini. `validate()` checks for required files. `package_archive()` creates `.tar.gz` from bundle dir.
  - `generate.py` — `bma-generate` CLI: 9-step pipeline (connect NetBox → fetch devices → render configs → export inventory → load catalogue → collect media → write ansible inventory → write manifest/checksums → validate). All steps are guarded with proper error handling and `--dry-run` support.
- Created production-quality Jinja2 template tree under `configs/templates/`:
  - `switches/common/base.j2` — hostname, AAA/TACACS+, SSH, NTP, DNS, syslog, SNMP, banners, VTY/console
  - `switches/common/vlans.j2` — management VLANs (100/200/400/500/600/700/800/900/950) + mission tenant VLAN blocks
  - `switches/common/stp.j2` — rapid-PVST, loopguard, BPDUguard defaults, per-VLAN priorities
  - `switches/common/interfaces.j2` — trunk/access/routed/LAG modes, 802.1X, portfast, shutdown
  - `switches/common/security.j2` — DHCP snooping, Dynamic ARP Inspection, storm control, IP source guard
  - `switches/core.j2` — L3 core: SVIs for all mgmt + mission VLANs, OSPF area 0, DHCP relay, per-tenant egress ACLs (deny cross-mission, allow DNS/NTP/AD)
  - `switches/core-ha.j2` — extends core.j2 with HSRP v2 on every SVI, WAN uplink tracking
  - `switches/distribution.j2` — L2 distribution: 802.1X/MAB user ports, RADIUS via NPS, IP verify source, storm control
  - `switches/access.j2` — infrastructure access: iLO ports (VLAN 600), server data trunks, NTP port, mgmt laptop port, unused shutdown on VLAN 999
  - `firewalls/perimeter-router.j2` — zone-based firewall: inside/outside, OSPF, NAT/PAT, one zone per mission tenant, zone pairs (mission→mgmt DNS/NTP/AD only, mission→mission deny, mission→outside web, mgmt→any inspect)
  - `firewalls/perimeter-router-ha.j2` — extends perimeter-router.j2 with HSRP on LAN sub-interfaces, stateful NAT HA (ip nat stateful), WAN tracking
- Created `configs/firmware_catalogue.yaml` — example with Cisco IOS/IOS-XE/ASA/FTD, HPE DL325/DL360/DL380 SPP + iLO, RHEL9 + Windows Server 2022 OS ISOs
- Added `bma-generate` entry point to `pyproject.toml`
- Created `tests/unit/test_config_media.py` with 32 tests covering all 5 non-CLI modules

**Decisions made**:
- Management VLANs (100/200/400/500/600/700/800/900/950) are hard-coded constants in renderer.py — operators override via NetBox VLANs, but the set never changes per design
- Mission tenant VLAN blocks: base 1100, stride 100 (1100 users/1110 apps/1120 data, 1200/1210/1220, …) — matches firewall zone naming and ACL numbering
- Secrets are injected as Ansible Vault references (`{{ vault_enable_secret }}`) — the template renders vault references, not plaintext secrets
- `ConfigRenderer` uses Jinja2 `StrictUndefined` by default — fails loudly on missing variables rather than silently rendering empty strings
- `perimeter-router.j2` is used for both `border-router` and `perimeter-firewall` roles — operator sets `config_template` custom field in NetBox to differentiate if needed
- Bundle layout: `configs/`, `firmware/`, `isos/`, `certs/`, `ansible/` + `inventory.yaml`, `manifest.yaml`, `checksums.sha256`
- `bma-generate --dry-run` skips all file writes but prints what would be rendered — safe to run against prod NetBox

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
- **Laptop service status** — dashboard sidebar shows DHCP/TFTP/HTTP/SSH status via systemd, polls every 15s
- **Deployment control buttons** — Start/Stop/Resume from the dashboard UI, mutual exclusion with simulation
- **Rollback to Factory** — Full factory reset of all devices (network, servers, NTP) from dashboard or CLI, with checkpoint/resume, safety confirmation, and simulation support
- **NetBox integration** — Single source of truth for node configs; "Prepare Build" dashboard button pulls from NetBox + git repo, generates inventory; optional (backward-compatible with manual YAML)
- **Config & Media Generation (Pillar 2)** — `bma-generate` CLI: renders Jinja2 configs from NetBox data, exports inventory.yaml, resolves firmware catalogue, collects media with checksum verification, packages complete deployment bundle with manifest + checksums; 11 production templates (core/core-ha/distribution/access switches, perimeter-router/HA firewall, 5 common includes)

### What exists (Sprint 1 — NetBox Site Lifecycle)

- **Site templates** — `site_templates/small-site.yaml`, `medium-site.yaml`, `large-site.yaml`
- **Cabling rules** — `site_templates/cabling/{small,medium,large}-site.yaml` (12/25/48 cables)
- **Firmware catalogue** — `firmware_catalogue.yaml` (Cisco IOS-XE, FTD, HPE iLO/BIOS/SPP, Meinberg)
- **NetBox site generation** — `orchestrator/site_generate.py` (idempotent, full object tree)
- **NetBox site regeneration** — `orchestrator/site_regenerate.py` (report/fix/rebuild modes)
- **Fleet scan** — `orchestrator/fleet_scan.py` (version drift report, JSON/table output)
- **Pipeline orchestrator** — `orchestrator/orchestrate.py` (5-stage pipeline, inventory export, bundle)
- **Node validators** — `orchestrator/validators.py` (device/VLAN/prefix/cable/cluster checks)

### What still needs to be built (from ROADMAP)

- **Milestone 1 (Foundation/MVP)**: DHCP server wrapper, CDP collector, serial collector, device matcher, ~~mock device simulator~~, unit tests
- **Milestone 2 (Cabling Validation)**: Intent parser, cabling diff engine, adaptation engine
- **Milestone 3 (Network Config)**: ~~Config renderer (Jinja2 rendering engine, all templates)~~, ~~Ansible dynamic inventory (hosts.ini generation)~~, playbooks, dead man's switch implementation, rollback handler
- **Milestone 4 (Server Provisioning)**: ~~Redfish client~~, ~~iLO discovery~~, ~~firmware update~~, ~~BIOS config~~, ~~virtual media~~, PXE (partially done — virtual media boot implemented)
- **Milestone 5 (Dashboard)**: WebSocket live updates, topology visualisation (D3.js/vis.js), ~~deploy button~~, log viewer, ~~simulation mode~~
- **Milestone 6 (Hardening)**: Serial console fallback, retry logic, ~~state persistence~~, multi-NIC, LLDP

### Session 12 — Sprint 1: NetBox Site Lifecycle Foundation

**Date**: 2026-04-01
**Branch**: `luma/tender-franklin`

**What was done**:
- Created `firmware_catalogue.yaml` — maps platform/version to file paths + MD5 hashes for cisco_iosxe, cisco_ftd, hpe_ilo, hpe_bios, hpe_spp, meinberg_ntp
- Created `site_templates/small-site.yaml`, `medium-site.yaml`, `large-site.yaml` — declarative site definitions covering device counts, VLAN specs, mission tenant config, IP addressing, cluster config, and firmware references
- Created `site_templates/cabling/small-site.yaml` (12 cables), `medium-site.yaml` (25 cables), `large-site.yaml` (48 cables) — explicit per-cable definitions with device/interface endpoints, cable type, and color
- Created `orchestrator/` package with 5 Python modules:
  - `validators.py` — `NodeValidator`: validates a NetBox site against its template (devices, VLANs, prefixes, cables, cluster); standalone CLI + importable
  - `site_generate.py` — `SiteGenerator`: idempotent NetBox site creation from template (manufacturers, device types, roles, platforms, site, rack, VLANs, mission VLANs, prefixes, devices, interfaces, cables, vSphere cluster)
  - `site_regenerate.py` — `SiteRegenerator`: 3-mode drift management (report/fix/rebuild) — compares devices, VLANs, prefixes, cables, cluster, custom fields vs template
  - `fleet_scan.py` — `FleetScanner`: scans all NetBox sites with `template_name` custom field, compares stored version vs on-disk template version, table/JSON output
  - `orchestrate.py` — `PipelineOrchestrator`: end-to-end 5-stage pipeline (connect → provision → validate → export → package); exports `inventory.yaml` + creates `bma-<site>-<ts>.tar.gz` bundle
- Updated `pyproject.toml`: added `tabulate>=0.9`, `semver>=3.0` dependencies; added 4 new CLI entry points (`bma-site-generate`, `bma-site-regenerate`, `bma-fleet-scan`, `bma-orchestrate`); added `orchestrator` to hatchling build targets
- Created `requirements.txt` for pip-based installs

**Decisions made**:
- Site templates use `default_site_octet` (100/200/300 for small/medium/large) overridable at generation time via `--octet`
- VLAN/prefix addressing formula: users `10.{X}.{11+N}.0/24`, apps `10.{X}.{111+N*10}.0/24`, data `10.{X}.{112+N*10}.0/24` where N is 0-indexed mission number
- Mission VLANs: users=`1100+N*100`, apps=`1110+N*100`, data=`1120+N*100`
- Cabling YAML is explicit (no template expansion) for clarity and auditability
- Large site has HA: 2 cores (VSS heartbeat Te1/0/46), 2 FWs (HA heartbeat Gi0/2 VLAN 999), dual-homed access switch
- SiteGenerator.run() is fully idempotent — `_get_or_create` pattern throughout
- `site_regenerate --mode fix` creates missing objects only (never deletes extras)
- `site_regenerate --mode rebuild` requires `--confirm` flag (destructive)
- Fleet scan exit code 1 if any site is outdated (useful for CI gates)
- Pipeline exports inventory compatible with existing `bare_metal_automation/inventory.py` loader

### Known issues / open items

- The `dashboard/` was changed from Flask to Django but the README architecture diagram still references "Flask + WebSocket" — may want to update this
- No unit tests for the new provisioning modules yet
- Meinberg API paths are based on the LANTIME REST API spec — may need adjustment for specific firmware versions
- `firmware_catalogue.yaml` MD5 hashes are blank — must be populated before deployment (`md5sum <file>`)
- Cable duplicate-check in `site_generate.py` uses `termination_a_id/b_id` filter — NetBox API response format for `a_terminations`/`b_terminations` changed in v3.7; may need adjustment for specific NetBox versions

---

## Architecture Notes for Future Sessions

### Source layout
```
src/bare_metal_automation/
├── __init__.py              # Version string
├── cli.py                   # Click CLI (discover, validate, configure, provision, serve)
├── models.py                # Dataclass models + enums
├── inventory.py             # YAML inventory loader + validator
├── orchestrator.py          # Phase-based state machine with parallel execution
├── common/
│   ├── parallel.py          # ThreadPoolExecutor grouped by BFS depth
│   └── services.py          # Laptop service status checks (systemctl)
├── discovery/engine.py      # DHCP + CDP + SNMP discovery
├── topology/builder.py      # NetworkX graph + BFS
├── cabling/validator.py     # CDP vs intent diff
├── configurator/
│   ├── network.py           # SSH config push with dead man's switch
│   └── firmware.py          # SCP firmware upgrade + verify
├── provisioner/
│   ├── server.py            # HPE Redfish provisioning (BIOS/RAID/SPP/OS/iLO)
│   └── meinberg.py          # Meinberg NTP REST API provisioning
├── rollback/
│   ├── orchestrator.py      # Rollback phase sequencer with checkpoint/resume
│   ├── network.py           # Cisco factory reset (write erase + reload)
│   ├── server.py            # HPE factory reset via Redfish (BIOS/RAID/iLO/power off)
│   └── meinberg.py          # Meinberg factory reset via REST API
├── netbox/
│   ├── client.py            # NetBox API client (pynetbox wrapper)
│   ├── loader.py            # Load node from NetBox → DeploymentInventory
│   ├── mapper.py            # Transform NetBox data to BMA spec format
│   └── git.py               # Git repo clone/pull for templates + firmware
├── config_media/
│   ├── renderer.py          # Jinja2 rendering engine (ConfigRenderer, RenderContext, VLAN/iface/tenant builders)
│   ├── inventory_export.py  # Generate inventory.yaml from NetBox device specs
│   ├── firmware_catalogue.py # Load/resolve firmware_catalogue.yaml (network fw, SPP, iLO, OS ISOs)
│   ├── media_collector.py   # Copy + SHA-256 verify firmware/ISOs/certs into bundle staging dir
│   ├── bundle_packager.py   # Assemble manifest.yaml, checksums.sha256, ansible hosts.ini, .tar.gz
│   └── generate.py          # bma-generate CLI (9-step pipeline: NetBox → configs → inventory → media → bundle)
└── dashboard/               # Django app (models, views, API, templates, sim, rollback, prepare)
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
