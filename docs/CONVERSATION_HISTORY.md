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

---

## Current State of the Project

### What exists (implemented)

- Full project scaffold with all module stubs
- Django dashboard with models, views, templates, and API
- Example inventory and core switch config template
- Development tooling config (ruff, mypy, pytest)

### What still needs to be built (from ROADMAP)

- **Milestone 1 (Foundation/MVP)**: DHCP server wrapper, CDP collector, serial collector, device matcher, mock device simulator, unit tests
- **Milestone 2 (Cabling Validation)**: Intent parser, cabling diff engine, adaptation engine
- **Milestone 3 (Network Config)**: Config renderer, Ansible dynamic inventory, playbooks, dead man's switch implementation, rollback handler
- **Milestone 4 (Server Provisioning)**: Redfish client, iLO discovery, firmware update, BIOS config, virtual media, PXE
- **Milestone 5 (Dashboard)**: WebSocket live updates, topology visualisation (D3.js/vis.js), deploy button, log viewer
- **Milestone 6 (Hardening)**: Serial console fallback, retry logic, state persistence, multi-NIC, LLDP

### Known issues / open items

- No open GitHub issues
- No open pull requests
- All roadmap tasks are still unchecked
- The `dashboard/` was changed from Flask to Django but the README architecture diagram still references "Flask + WebSocket" — may want to update this

---

## Architecture Notes for Future Sessions

### Source layout
```
src/ztp_forge/
├── __init__.py          # Version string
├── cli.py               # Click CLI (discover, validate, configure, provision, serve)
├── models.py            # Pydantic models (DeviceInfo, TopologyNode, CablingReport, etc.)
├── inventory.py         # YAML inventory loader + validator
├── orchestrator.py      # Phase-based state machine
├── common/              # Shared utilities (stub)
├── discovery/engine.py  # DHCP + CDP + SNMP discovery
├── topology/builder.py  # NetworkX graph + BFS
├── cabling/validator.py # CDP vs intent diff
├── configurator/network.py  # Ansible runner + dead man's switch
├── provisioner/         # Redfish/iLO (stub)
└── dashboard/           # Django app (models, views, API, templates)
```

### Deployment phases (in order)
0. Pre-flight — validate inventory, check firmware, verify NIC
1. Discovery — DHCP leases, SSH, CDP, serial matching
2. Topology & Cabling — build graph, BFS, validate against intent
3. Heavy Transfers — firmware/ISO push while network is flat L2
4. Network Config — outside-in config push with dead man's switch
5. Laptop Pivot — reconfigure laptop NIC to production VLAN
6. Server Post-Install — OS hardening, packages, domain join
7. Final Validation — end-to-end tests, health checks, report

### Supported hardware
- Cisco IOS/IOS-XE switches and routers (SSH + CDP + Ansible)
- Cisco ASA / Firepower firewalls (SSH + Ansible)
- HPE DL325 Gen10 servers (iLO 5 Redfish API)
