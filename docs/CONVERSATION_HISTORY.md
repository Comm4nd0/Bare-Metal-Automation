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

### Session — Phase 9 Ansible Roles + deploy_vms Command

**Date**: 2026-04-04
**Branch**: `luma/upbeat-noyce`

**What was done**:
- Created 17 production-quality Ansible roles under `ansible/roles/`:
  - `common` — base Windows OS config: hostname, DNS, NTP, CIS L1 hardening, WinRM HTTPS, Windows Exporter install, domain join
  - `domain-controller` — AD DS, DHCP, OU structure, service accounts (svc_monitoring, svc_backup, svc_sccm, svc_sql), password policy GPO
  - `dns-server` — AD-integrated zones, forwarders, reverse lookup zones, static records, scavenging
  - `certificate-authority` — AD CS root CA (4096-bit RSA/SHA256), certificate template enablement, auto-enrollment GPO
  - `wsus` — install + post-install config, product/classification sync, auto-approval rule for critical/security updates, client GPO
  - `nps-radius` — NPS install, AD registration, RADIUS clients (distribution switches), 802.1X network policy
  - `sccm` — ADK prereqs, unattended install via `.ini` template, site boundaries, device collections
  - `file-server` — DFS namespace (DomainV2), SMB shares, NTFS ACLs, FSRM quotas, home drive GPO
  - `database-server` — MSSQL unattended install via `.ini` template, mixed mode auth, databases, backup job SQL script template
  - `print-server` — Print Server role, TCP/IP ports, printer drivers, shared printers, GPO deployment
  - `application-server` — generic app: per-app config via variables, NSX segment VLAN, app_config.xml and winlogbeat.yml templates
  - `monitoring-server` — Prometheus 2.51 + Grafana 10.4 + SNMP Exporter 0.26; systemd units, alert rules, Grafana LDAP/SMTP config
  - `log-collector` — ELK 8.13 stack (Elasticsearch + Logstash + Kibana); logstash pipeline template, ILM retention policy
  - `backup-server` — Veeam Backup & Replication unattended install, repository, 2 backup job groups (infra + app servers)
  - `bastion` — RD Gateway + CAP/RAP policies, SSL cert binding, session/idle timeouts, audit logging hardening
  - `security` — EDR agent install, Windows Defender cloud protection, WEC syslog forwarding, vulnerability scanner firewall rule
  - `network-device-mgmt` — Cisco IOS: SNMP v3 user, syslog, NTP, AAA/TACACS+, 802.1X global + per-port config
- Created `ansible/playbooks/phase9_software.yml` — 8-step master playbook orchestrating all 21 VMs in dependency order
- Created `ansible/playbooks/network_device_mgmt.yml` — standalone network device management playbook
- Created `ansible/group_vars/all.yml` — global domain/WinRM/NTP/monitoring settings with vault secret placeholders
- Created `ansible/host_vars/dc01.yml` — DC01 host-specific vars
- Implemented `dashboard/deploy/management/commands/deploy_vms.py` — full Phase 9 management command using `ansible-runner`:
  - `--deployment` (required), `--inventory`, `--steps` (selective tag execution), `--dry-run` (passes `--check`), `--vault-password-file`, `--verbosity-ansible`
  - Streams ansible-runner events to stdout, surfaces changed/failed tasks in real time
  - Creates/updates `DeploymentPhase` record (phase 9) with start/complete/fail lifecycle

**Key decisions**:
- Each role uses `win_*` modules (Windows Server VMs), except monitoring-server and log-collector which are Linux (Ubuntu)
- All tasks have `tags:` for selective execution; `notify:` uses named listeners via `listen:` in handlers
- Idempotency guarded in PowerShell blocks via `-ErrorAction SilentlyContinue` checks before creating resources
- `no_log: true` on all tasks handling secrets (RADIUS, MSSQL SA, vault vars)
- `become: true` used at play level or task level as appropriate per OS
- SCCM and MSSQL installs use Jinja2 `.ini` / `.sql` templates (not hardcoded config)
- `deploy_vms` resolves inventory from bundle_path if `--inventory` not specified; logs per-event ansible-runner callbacks
- Phase 9 playbook respects sub-step tags so individual steps can be re-run without full re-deployment

### Session — Phase 7/8, Dashboard Consolidation, Discovery & Reset Gaps

**Date**: 2026-04-04
**Branch**: `luma/upbeat-gagarin`

**What was done**:

#### Phase 7 — vCenter Deployment (`src/bare_metal_automation/vinfra/vcenter/`)
- `deploy.py` — `VCSADeployer`: generates JSON template, runs `vcsa-deploy install --accept-eula --no-ssl-certificate-verification`, polls vCenter HTTPS until ready. `VCSAConfig` dataclass captures all deployment parameters.
- `cluster.py` — `ClusterManager` (pyvmomi): create/find datacenter, create cluster with HA/DRS disabled (enabled later), add ESXi hosts via `AddHost` task.
- `vds.py` — `VDSManager` (pyvmomi): create vDS, add hosts, create port groups for all standard VLANs (100/200/400/500/600/700/800/950) plus per-mission tenant VLANs (1100+N*100, 1110+N*100, 1120+N*100).
- `storage.py` — `StorageManager`: configure vSAN (claim eligible SSDs/capacity disks into disk groups) or enumerate local datastores.
- `ha_drs.py` — `HADRSManager`: enable HA (percentage admission control, VM monitoring), DRS (fully automated), configure vMotion VMkernel adapters per host.
- `content_library.py` — `ContentLibraryManager` (vSphere REST API): create local content library on a datastore, upload OVA/ISO templates via streaming PUT.

#### Phase 8 — NSX-T Configuration (`src/bare_metal_automation/vinfra/nsx/`)
- `manager.py` — `NSXManagerDeployer`: deploy NSX Manager OVA via ovftool, wait for UI readiness, register with vCenter via Policy API compute-manager endpoint.
- `transport.py` — `TransportManager`: create overlay + VLAN transport zones, TEP IP pool, uplink profile; apply host transport node profile to vSphere cluster.
- `edge.py` — `EdgeManager`: deploy Edge VMs (Policy API), wait for configuration_state=SUCCESS, create edge cluster.
- `routing.py` — `RoutingManager`: create Tier-0 (uplink to firewall, locale service, optional interface), per-tenant Tier-1 gateways linked to T0.
- `segments.py` — `SegmentManager`: create `mgmt-servers`, `mgmt-infra` + `mN-users`, `mN-apps`, `mN-data` segments connected to appropriate T1 gateways.
- `firewall.py` — `FirewallManager`: DFW policies for shared-services ALLOW, intra-mission ALLOW, cross-mission DROP (logged), mission→management DROP (logged), management→any ALLOW.

#### Django management commands (primary dashboard)
- `deploy_vcenter` — 6-step pipeline (vcsa_deploy → cluster → vds → storage → ha_drs → content_library). Resumes from `--start-at-step`, supports `--dry-run`. Updates Deployment.phase and sends WebSocket events via `events.py`.
- `configure_vnet` — 8-step pipeline (manager → transport → host_tnp → edge → tier0 → tier1 → segments → firewall). `--skip-manager-deploy` skips OVA deploy. Reads site config JSON from file or Deployment.site_config.

#### Dashboard consolidation
- Primary `Deployment` model extended with: `site_name`, `site_slug`, `template_name`, `template_version`, `bundle_path`, `manifest_hash`, `site_config` (JSON), `operator` FK.
- New models in primary dashboard: `DeploymentPhase`, `PhaseStatus`, `ResetStatus`, `FactoryReset`, `ResetPhase`, `DeviceResetCertificate` (with `certificate_id` + `checksum` from SanitisationCertificate).
- Fleet models added to primary: `SiteRecord`, `TemplateRecord`, `FleetScan`, `SiteComplianceRecord`.
- Migration `0005_consolidation.py` created.
- Legacy `dashboard/deploy/management/commands/{deploy_vcenter,configure_vnet}.py` replaced with deprecation shims that forward to primary commands.
- `dashboard/README.md` added documenting the deprecation and model mapping table.

#### Discovery — LLDP parsing (`src/bare_metal_automation/discovery/cdp.py`)
- Added `parse_lldp_output()` — parses `show lldp neighbors detail` for IOS-XE and NX-OS. Extracts local port, system name/chassis ID, remote port, system description, management IP.
- Added `LLDPCollector` — SSH + LLDP collection with configurable `device_type` for Netmiko.
- Added `NeighbourCollector` — tries CDP first, then LLDP, merges deduplicated results (keyed by `(local_port, remote_device_id)`).

#### Factory reset certificates (`src/bare_metal_automation/factory_reset/reset.py`)
- `_generate_reset_certificates()` — called after `phase_server_wipe` (method: `redfish-bios-reset`) and `phase_network_reset` (method: `cisco-write-erase`).
- Uses `CertificateGenerator` to write JSON + TXT cert files.
- `_save_certificate_to_db()` — creates `DeviceResetCertificate` DB record if Django ORM is available (no-ops gracefully otherwise), linking to the matching `Device` by serial.

#### NetBox seeding (`scripts/deploy_netbox.sh`)
- Added `Platform` objects for all BMA platforms: cisco-ios, cisco-iosxe, cisco-nxos, cisco-ftd, hpe-ilo, meinberg-ntp (with NAPALM driver where applicable).
- Added 17 `DeviceType` entries: Catalyst 9500/9300/9200, ISR 4331/4351, Firepower 1150/2110, ProLiant DL360/DL380/DL325 Gen10/Gen10 Plus, LANTIME M300/M320.
- Expanded custom fields: added `bma_platform`, `bma_bfs_depth`, `bma_reset_certificate`.
- Added `backup-server` and `management-server` device roles.
- `pyvmomi>=8.0,<9` added to `requirements.txt` and `pyproject.toml`.

**Key decisions**:
- NSX uses Policy API exclusively (not deprecated MP API) — `/policy/api/v1/` for all resources.
- pyvmomi imported lazily inside methods (not at module top) so the package remains importable without pyvmomi installed.
- `_save_certificate_to_db()` is best-effort: certificate files on disk are always written; DB record creation is optional so the reset works in non-Django contexts.
- LLDP + CDP results are merged by `(local_port, remote_device_id)` key — CDP preferred as it carries serial numbers.
- vDS port group naming: `PG-<vlan_name>` for standard VLANs, `PG-m{N}-<type>` for mission VLANs (e.g. `PG-m1-users`).
- NSX DFW policies are sequenced: shared-services (100) → intra-mission (200) → cross-mission (300) → mission→mgmt (400) → mgmt→any (500) — higher sequence = lower priority.
- Legacy dashboard management commands emit deprecation warnings and forward to primary commands via site_slug lookup.

### Session — Review & Fix deploy_netbox.sh for NetBox 4.x Compatibility

**Date**: 2026-04-02
**Branch**: `claude/review-netbox-deploy-6VsK5`

**What was done**:
- Reviewed `scripts/deploy_netbox.sh` for correctness against NetBox 4.2
- Fixed `CustomField.content_types` → `CustomField.object_types` (renamed in NetBox 4.0+)
- Removed `vm_role` parameter from `DeviceRole` creation (field removed in NetBox 4.0+)
- Changed default NetBox version from `4.2` to `4.2.3` (Docker image tags require full semver)
- Quoted `ALLOWED_HOSTS` value in `.env` to prevent space-parsing issues in Docker Compose

**Key decisions**:
- Targeted fixes only for actual breaking issues; no unnecessary refactoring

### Session — Firmware Upgrade Testing Framework

**Date**: 2026-04-01
**Branch**: `claude/firmware-upgrade-testing-p9rRZ`

**What was done**:
- Created `firmware/` package (`src/bare_metal_automation/firmware/`) with three modules:
  - `catalog.py` — `FirmwareCatalog` and `FirmwareEntry`: YAML-based firmware manifest declaring all available firmware images per platform, with versions, MD5 checksums, minimum upgrade path versions, release notes, and recommended flags. Supports load/save YAML roundtrip, platform queries, and safe upgrade path checking.
  - `tester.py` — `FirmwareTestRunner`: full upgrade-and-verify test pipeline that snapshots current firmware + running config, runs pre-upgrade validation, performs the firmware upgrade, re-applies the saved configuration, waits for protocol convergence, runs post-upgrade validation (role-specific health checks), and optionally rolls back on failure. Devices are tested sequentially to limit blast radius — stops on first failure.
  - `compliance.py` — `ComplianceChecker`: scans devices (via SSH) and compares running firmware against catalog recommendations. Produces a `ComplianceReport` with per-device status (compliant, upgrade_available, upgrade_blocked, unreachable) and fleet-wide compliance percentage.
- Added Django dashboard models:
  - `FirmwareImage` — tracks catalog entries in the DB (platform, version, filename, md5, min_version, recommended)
  - `FirmwareTestRun` — records test results (outcome, phases, validation results, duration, findings)
  - `FirmwareComplianceSnapshot` — point-in-time fleet compliance snapshot
- Created `dashboard/firmware_views.py` with 7 API endpoints:
  - `GET /api/firmware/catalog/` — list firmware images
  - `POST /api/firmware/catalog/sync/` — sync DB from YAML catalog
  - `GET /api/firmware/tests/` — list test runs
  - `POST /api/firmware/tests/record/` — record test result
  - `GET /api/firmware/tests/<id>/` — test run detail
  - `GET /api/firmware/compliance/` — latest compliance snapshot
  - `POST /api/firmware/compliance/record/` — record compliance snapshot
- Added `FIRMWARE_CATALOG` setting to `settings.py` (env var: `BMA_FIRMWARE_CATALOG`)
- Created sample `configs/firmware/catalog.yaml` with entries for all supported platforms (cisco_ios, cisco_iosxe, cisco_asa, cisco_ftd, hpe_ilo5, meinberg_lantime)
- Added 32 unit tests in `tests/unit/test_firmware.py` covering catalog CRUD, upgrade path safety, compliance checking, test runner phases, and failure/rollback scenarios

**Key decisions**:
- Sequential device testing (not parallel) to limit blast radius — if a firmware breaks configs, stop before affecting more devices
- Rollback capability: if post-upgrade validation fails, the runner can downgrade to the previous version (if it exists in the catalog) and re-apply the saved config
- The test runner composes existing `FirmwareConfigurator` (for the actual upgrade) and `ConfigValidator` (for health checks) rather than duplicating their logic
- Safe upgrade paths enforced via `min_version` in catalog entries — devices below the minimum cannot be upgraded (prevents bricked devices from unsupported jumps)
- 30-second convergence wait after config re-apply before post-validation — allows STP, OSPF, HSRP to reconverge

### Session — NetBox Deployment Script

**Date**: 2026-04-01
**Branch**: `claude/netbox-deployment-script-fvFtR`

**What was done**:
- Created `scripts/deploy_netbox.sh` — a complete deployment script for standing up NetBox via Docker Compose
- Script deploys NetBox (configurable version, default 4.2) with PostgreSQL 16 and Redis 7
- Includes NetBox worker and housekeeping containers for background jobs
- Auto-generates secure secrets (DB password, Redis password, Django secret key)
- Creates a superuser and generates an API token for BMA integration
- Seeds BMA-specific data into NetBox: manufacturers (Cisco, HPE, Meinberg), device roles (core-switch, distribution-switch, access-switch, wan-router, distribution-router, perimeter-firewall, compute-server, ntp-server), and custom fields (bma_serial, bma_firmware_version, bma_provisioning_status, bma_last_deployed)
- Outputs a `.env.netbox` file with `BMA_NETBOX_URL` and `BMA_NETBOX_TOKEN` ready for BMA usage
- Supports `--uninstall` for clean teardown
- Added `.env.netbox` to `.gitignore` to prevent leaking secrets

**Key decisions**:
- Used `netboxcommunity/netbox` Docker image (official community image) rather than building from source
- Default data directory at `/opt/netbox-data` keeps persistent data outside the repo
- Seeding BMA roles/fields on first deploy ensures NetBox is immediately usable with the orchestrator scripts

### Session 6 — Sprint 3: Django Dashboard & Bundle Ingestion (PR TBD)

**Date**: 2026-04-01
**Branch**: `luma/affectionate-dijkstra`
**Base**: `luma/objective-bhabha` (Sprint 2 — config & media generation)

**What was done**:
- Built a complete production-quality Django dashboard project at `dashboard/` (root level), separate from the Python library in `src/`
- Added Sprint 3 dependencies to `pyproject.toml`: `channels>=4.0`, `channels-redis>=4.2`, `daphne>=4.1`, `djangorestframework>=3.15`, `django-filter>=24.1`, `pytest-django>=4.8`

**Django project structure** (`dashboard/`):
- `config/` — Django project settings, URLs, ASGI (Channels), WSGI; SQLite default, PostgreSQL via `BMA_DB_ENGINE=postgresql` env var; in-memory channel layer default, Redis via `BMA_CHANNEL_BACKEND=redis`
- `deploy/` — Deploy tracking app
- `fleet/` — Fleet compliance app
- `static/js/`, `static/css/` — Vanilla JS + CSS static assets
- `templates/` — Base template

**Deploy app models** (`dashboard/deploy/models.py`):
- `Deployment` — site_name/slug, template_name/version, bundle_path, manifest_hash, ingested_at/started_at/completed_at, status (ingested/running/completed/failed/aborted), operator FK; `start()`, `complete()`, `fail()` helpers; `progress_pct`, `current_phase`, `duration_seconds` properties
- `DeploymentPhase` — 11 phases (0-10), status with traffic_light property (grey/blue/green/amber/red), `start()`, `complete(warning_count)`, `fail(error_message)` helpers, duration tracking
- `DeploymentDevice` — per-device state (11 statuses), artefact paths, timestamps, `status_colour` property
- `DeviceLog` — timestamped per-device logs with level (DEBUG/INFO/WARN/ERROR)
- `FactoryReset`, `ResetPhase`, `DeviceResetCertificate` — factory reset workflow models

**Fleet app models** (`dashboard/fleet/models.py`):
- `SiteRecord` — registered sites with last_deployment FK
- `TemplateRecord` — versioned templates with previous_versions JSON
- `FleetScan` — compliance scan results with `compliance_pct` property
- `SiteComplianceRecord` — per-site result within a scan (compliant/outdated/unknown/never_deployed)

**Bundle ingestion** (`deploy/management/commands/ingest_bundle.py`):
- `python manage.py ingest_bundle --path /media/usb/bundle/`
- Validates `checksums.sha256` (SHA-256 of every file), validates `manifest.yaml` schema (required keys, valid roles/platforms), validates `inventory.yaml` if present, warns on missing artefact files
- Creates Deployment + 11 DeploymentPhase records (all pending) + DeploymentDevice per device entry
- Flags: `--validate-only` (no DB writes), `--force` (re-ingest), `--operator <username>`

**Phase management command stubs** (Sprint 4 implementation):
- `discover`, `validate_cabling`, `transfer_firmware`, `configure_network`, `pivot`, `provision_servers`, `deploy_vcenter`, `configure_vnet`, `deploy_vms`, `validate_deployment`
- `deploy` — master command with `--phases` (comma-separated) and `--dry-run`
- `factory_reset` — requires `--confirm` flag, `--sanitisation-method` option

**REST API** (`deploy/api/`):
- DRF viewsets: `DeploymentViewSet` (list/detail + `/phases/`, `/devices/`, `/factory_resets/` actions), `DeploymentPhaseViewSet`, `DeploymentDeviceViewSet` (with `/logs/` action), `FactoryResetViewSet`
- DefaultRouter at `/api/`
- Serializers include computed fields: `traffic_light`, `status_colour`, `progress_pct`, `duration_seconds`

**WebSocket consumers** (`deploy/consumers.py`):
- `DeploymentConsumer` — subscribes to `deployment_<id>` channel group
- Events: `phase.started`, `phase.completed`, `phase.failed`, `device.status_changed`, `device.log`, `deployment.completed`, `deployment.failed`
- Helper async push functions for each event type (used by Sprint 4 phase commands)
- WebSocket URL: `ws/deployments/<id>/`

**Dashboard views and templates**:
- `deploy/index.html` — deployment list table with status badges, progress bars
- `deploy/deployment_detail.html` — 11-phase traffic light pipeline (animated pulse on running phase) + device grid table; WebSocket-connected via `initPhaseTracker()` and `initDeviceGrid()`
- `deploy/phase_detail.html` — per-device status within a phase, expandable device logs, phase navigation
- `fleet/index.html` — sites grouped by template, compliance bars, summary cards
- `fleet/site_detail.html` — compliance history for a single site
- `fleet/scan_detail.html` — all site results for a fleet scan
- Dark theme base template (`templates/base.html`) with nav, card, table, badge, progress bar styles

**Static assets**:
- `static/css/traffic-lights.css` — `.tl-<colour>` classes, `.status-dot-*`, device row highlight classes
- `static/js/phase-tracker.js` — WebSocket client with exponential back-off reconnection, updates phase pipeline traffic lights in real time, shows toast banner on completion/failure
- `static/js/device-grid.js` — WebSocket client for device status row updates, per-device log buffer, flash animation on state changes

**Tests** (`tests/dashboard/`):
- `test_models.py` — 25 tests covering Deployment lifecycle (start/complete/fail), phase traffic lights, device status colours, FactoryReset + certificate, fleet models, compliance percentage
- `test_ingest_bundle.py` — 13 tests: valid ingestion, validate-only, duplicate guard, --force, checksum mismatch, missing files, invalid role/platform, schema errors, manifest hash storage
- `test_api.py` — 13 tests: deployment list/detail, phases/devices/factory_resets actions, 404 handling, phase traffic_light in API, device log action

**Key decisions**:
- Dashboard lives at root-level `dashboard/` as a standalone Django project, not inside the `src/` package — cleaner separation between the Python library and the web app
- SQLite by default for laptop deployment; PostgreSQL via `BMA_DB_ENGINE=postgresql` env var
- In-memory channel layer by default; Redis via `BMA_CHANNEL_BACKEND=redis`
- `unique_together` constraints on (deployment, phase_number), (deployment, serial_number), (reset, phase_number) enforced at DB level
- Phase stubs intentionally print a clear "Sprint 4 not implemented" warning rather than silently no-oping

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

### Session 13 — Sprint 4: BMA Engine — Phase Implementation

**Date**: 2026-04-01
**Branch**: `luma/nice-galileo`

**What was done**:

Implemented the full BMA Engine Sprint 4, filling in module stubs and adding new sub-modules across every layer of the stack.

**Discovery sub-modules** (`discovery/`):
- `dhcp.py` — `DhcpServer` class: writes dnsmasq config, starts/stops process, parses lease file, `wait_for_leases()` with timeout
- `cdp.py` — `CDPCollector` class + `parse_cdp_output()` standalone parser: SSH-based CDP neighbour collection with credential fallback
- `serial.py` — `parse_inventory()` / `collect_serial()`: extracts serial (SN) and PID from `show inventory`, `pid_to_platform()` maps PID → BMA platform string
- `matcher.py` — `InventoryMatcher` class + `MatchResult` dataclass: reconciles discovered vs. expected serials, mutates `DiscoveredDevice` objects, `update_db()` creates/updates Django `Device` ORM records

**Topology sub-modules** (`topology/`):
- `graph.py` — `build_graph()`: nodes keyed by serial number (stable across DHCP renewals), edges = physical cables with port labels from CDP
- `ordering.py` — `outside_in_order()` / `calculate_bfs_depths()`: BFS from laptop serial, mutates `bfs_depth` and `config_order` on devices
- `visualise.py` — `export_for_d3()`: D3.js force-graph JSON (`{nodes, edges, metadata}`) with group-by-role for colour coding

**Cabling sub-modules** (`cabling/`):
- `intent.py` — `CablingRule` dataclass + `load_cabling_rules()`: loads YAML cabling rules file, `CablingIntent.for_device()` / `port_map()` helpers
- `diff.py` — `diff_device()` / `cdp_to_actual()`: compares port-indexed intent vs. actual CDP; categories: correct, adaptable, mismatched, missing, unexpected
- `adapter.py` — `ConfigAdapter.adapt()`: rewrites config lines for `adaptable` ports, patches `description` to reference actual remote device
- `report.py` — `ValidationReport` + `generate_report()`: structured report with JSON and human-readable output, `blocking` property gates deployment

**Configurator** (`configurator/`):
- `validator.py` — `ConfigValidator` + `ValidationResult`: post-config checks per role; STP root, trunk status, OSPF adjacencies, HSRP state, management-IP TCP/22 reachability

**Provisioner sub-modules** (`provisioner/`):
- `redfish.py` — `RedfishClient`: extracted from `server.py`, adds session-token auth, automatic retries (503/504), `wait_for_post()`, `wait_for_ilo()`, context-manager support
- `ilo.py` — discrete iLO operation functions: `upload_and_flash_firmware()`, `configure_bios()`, `configure_raid()`, `mount_virtual_media()`, `set_boot_order()`, `unmount_all_virtual_media()`
- `installer.py` — `OSInstaller`: mounts OS + kickstart ISOs, sets one-time boot, reboots, polls for completion via virtual-media-not-active heuristic
- `pxe.py` — `PXEServer`: dnsmasq TFTP-mode wrapper, `serve_pxe_files()`, `add_host_entry()` / `remove_host_entry()` (per-MAC pxelinux.cfg)

**Factory Reset module** (`factory_reset/`):
- `reset.py` — `FactoryResetOrchestrator`: 6-phase reset sequence (VM teardown → NSX teardown → vCenter teardown → server wipe → network reset → validation). Phases 1–3 are stubbed with TODO for VMware API sprint; phases 4–6 delegate to existing `resetter/` modules
- `sanitise.py` — `DataSanitiser`: SED cryptographic erase via Redfish `Drive.SecureErase`, Cisco `write erase` via SSH, VM disk zeroing (TODO), `verify_erasure()` checks no logical drives remain
- `certificate.py` — `SanitisationCertificate` + `CertificateGenerator`: UUID-keyed JSON + human-readable text certificates with SHA-256 tamper-evidence checksum, `generate_batch()` for bulk ops

**Dashboard WebSocket layer** (`dashboard/`):
- `events.py` — `phase_started()`, `phase_completed()`, `device_status_changed()`, `device_log()`, `deployment_log()`, `topology_updated()`: wraps Django Channels `group_send()`, gracefully no-ops if Channels not installed
- `consumers.py` — `DeploymentConsumer(AsyncWebsocketConsumer)`: relays channel group messages to browser, URL: `ws://host/ws/deployment/{id}/`
- `routing.py` — `ProtocolTypeRouter`: HTTP → Django ASGI, WebSocket → `DeploymentConsumer`
- `asgi.py` — ASGI entry point for Daphne/Uvicorn

**Settings / deps**:
- `settings.py` — added `"channels"` to `INSTALLED_APPS`, `ASGI_APPLICATION`, `CHANNEL_LAYERS` with `InMemoryChannelLayer`
- `pyproject.toml` — added `channels>=4.0` and `daphne>=4.0`

**Orchestrator + deployment integration**:
- `orchestrator.py` — new `on_device_discovered` and `on_device_change` callbacks; `_emit_device_discovered()` / `_emit_device_change()` helpers; wired into all phase result loops (network config, firmware, server provisioning, NTP provisioning)
- `deployment.py` — `_on_device_discovered()` creates/upserts `Device` ORM records; `_on_device_change()` updates `Device.state`, creates `DeploymentLog` entry, and broadcasts via `events.device_status_changed()`

**Decisions made**:
- Discovery sub-modules decompose `engine.py` concerns into single-responsibility modules while leaving `engine.py` intact as the high-level coordinator
- Topology nodes keyed by serial (not IP) — stable across DHCP lease renewals
- `CablingIntent` can load from YAML rules file (new) or continue using the template-parsing approach in `validator.py` — both paths supported
- `RedfishClient` uses `InMemoryChannelLayer` (no Redis required) — operators can swap to `channels_redis` for HA deployments
- WebSocket events use fire-and-forget (`async_to_sync` + exception swallowed) so channel layer misconfiguration never breaks hardware operations
- Factory reset `FactoryResetOrchestrator` phases 1–3 (VMware) are intentional stubs — flagged clearly as TODOs for a dedicated VMware sprint
- Sanitisation certificates use SHA-256 checksum over payload for tamper evidence — no PKI required for field use
- Device callbacks use a simple `Callable` pattern (not signals) to keep the orchestrator free of Django imports

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
- **Django Dashboard (Sprint 3)** — Full standalone Django project at `dashboard/`; deploy app (Deployment/Phase/Device/Log/FactoryReset models), fleet app (SiteRecord/TemplateRecord/FleetScan models), `ingest_bundle` management command (checksum validation + DB creation), 12 phase command stubs, DRF REST API, Django Channels WebSocket consumers, traffic-light phase pipeline UI, device grid, fleet compliance views, vanilla JS real-time updates

### What still needs to be built (from ROADMAP)

- **Milestone 1 (Foundation/MVP)**: ~~DHCP server wrapper~~, ~~CDP collector~~, ~~serial collector~~, ~~device matcher~~, ~~mock device simulator~~, ~~unit tests for new sub-modules~~
- **Milestone 2 (Cabling Validation)**: ~~Intent parser~~, ~~cabling diff engine~~, ~~adaptation engine~~, ~~report generator~~
- **Milestone 3 (Network Config)**: ~~Config renderer~~, ~~dead man's switch~~, ~~post-config validator~~, ~~router templates~~, Ansible playbooks, rollback handler
- **Milestone 4 (Server Provisioning)**: ~~Redfish client~~, ~~iLO operations~~, ~~firmware update~~, ~~BIOS config~~, ~~virtual media~~, ~~OS installer~~, ~~PXE fallback~~, ~~server config templates (iLO + BIOS)~~
- **Milestone 5 (Dashboard)**: ~~WebSocket events (server-side)~~, D3.js topology renderer (frontend JS), log viewer enhancements, ~~deploy button~~, ~~simulation mode~~
- **Milestone 6 (Hardening)**: Serial console fallback, retry logic, ~~state persistence~~, multi-NIC, ~~LLDP~~, config drift detection
- **CI/CD**: ~~GitHub Actions workflow~~, ~~pre-commit hooks~~, ~~Makefile~~, ~~Dockerfile + docker-compose~~
- **VMware / NSX (Phase 7/8)**: ~~vCenter deployment (deploy.py, cluster.py, vds.py, storage.py, ha_drs.py, content_library.py)~~, ~~NSX-T configuration (manager, transport, edge, routing, segments, firewall)~~, ~~management commands (deploy_vcenter, configure_vnet)~~, ~~factory reset VMware phases (still NotImplementedError — requires live vCenter)~~
- **Dashboard consolidation**: ~~Unique legacy models ported~~, ~~migration 0005~~, ~~legacy shims~~, ~~deprecation README~~, data migration script (site-specific, not provided)
- **Integration tests**: End-to-end pipeline tests with mock hardware
- **Security**: ~~XSS fixes~~, ~~CSRF protection for UI endpoints~~, ~~API input validation~~, dashboard authentication, API rate limiting

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

### Session 14 — Gap Analysis & Hardening

**Date**: 2026-04-01
**Branch**: `claude/review-automation-gaps-XHGoz`

**What was done**:
- Full gap analysis of the entire automation codebase (88 Python files, ~16,900 LOC)
- **Centralized settings** — Created `src/bare_metal_automation/settings.py`: all credentials, timeouts, file paths, SSL verification, and API paths extracted from 8+ modules into a single env-var-configurable module. Modules updated to import from settings.
- **SSL verification configurable** — Added `BMA_VERIFY_SSL` env var (default `False` for lab/field self-signed certs, set to `1`/`true`/`yes` for production)
- **VMware guards** — Replaced silent `pass`/`return True` stubs in `factory_reset/reset.py` phases 1-3 and `factory_reset/sanitise.py` VM disk zeroing with explicit `NotImplementedError` (dry_run mode still works)
- **CI/CD pipeline** — Created `.pre-commit-config.yaml` (ruff + mypy + standard hooks), `Makefile` (lint/format/typecheck/test/clean targets), `.github/workflows/ci.yml` (Python 3.11, lint + typecheck + test on push/PR)
- **Docker** — Created `Dockerfile` (python:3.11-slim + daphne ASGI) and `docker-compose.yml` (web + Redis for Channels)
- **Router config templates** — Created `configs/templates/routers/wan-router.j2` (BGP, WAN interfaces, route-maps, NAT) and `distribution-router.j2` (OSPF, HSRP, DHCP relay, inter-VLAN routing)
- **Server config templates** — Created `configs/templates/servers/ilo-config.j2` (iLO network, users, SNMP, NTP) and `bios-config.j2` (boot order, performance, virtualization, power)
- **Unit tests** — Added 7 new test files (126 new tests, 149 total passing):
  - `test_settings.py` — env var overrides, credential parsing, defaults
  - `test_discovery.py` — DHCP lease parsing, SSH probing, CDP neighbor extraction
  - `test_topology.py` — graph construction, BFS depth, config ordering
  - `test_cabling.py` — connection matching, report generation
  - `test_provisioner.py` — Redfish client, provisioning sequence, error handling
  - `test_rollback.py` — server/meinberg/orchestrator rollback
  - `test_netbox.py` — API client, inventory mapping, error handling
- **Lint fixes** — Auto-fixed import sorting, unused imports, deprecated patterns across all modified files

**Decisions made**:
- SSL defaults to `False` (not `True`) because BMA operates on isolated bootstrap networks with self-signed iLO/Meinberg certs; production users can enable via env var
- VMware stubs now raise `NotImplementedError` instead of silently passing — prevents accidental use in production; dry_run mode still skips these phases
- Credentials remain in env vars (not a secrets manager) to keep field deployment simple — operators set vars in their shell profile or `.env` file
- Test coverage increased from ~1% to ~15%; priority was given to modules with highest blast radius (provisioner, rollback, discovery)
- Pipeline exports inventory compatible with existing `bare_metal_automation/inventory.py` loader

### Known issues / open items

- The `dashboard/` was changed from Flask to Django but the README architecture diagram still references "Flask + WebSocket" — may want to update this
- No unit tests for the new provisioning modules yet
- Meinberg API paths are based on the LANTIME REST API spec — may need adjustment for specific firmware versions
- `firmware_catalogue.yaml` MD5 hashes are blank — must be populated before deployment (`md5sum <file>`)
- Cable duplicate-check in `site_generate.py` uses `termination_a_id/b_id` filter — NetBox API response format for `a_terminations`/`b_terminations` changed in v3.7; may need adjustment for specific NetBox versions

### Session 15 — Vendor-Agnostic Refactoring

**Date**: 2026-04-01
**Branch**: `claude/vendor-agnostic-refactor-bFDxt`

**What was done**:
- Introduced a driver/plugin architecture to make BMA vendor-agnostic
- Created abstract base classes: `NetworkDriver`, `ServerDriver`, `ApplianceDriver`, `DiscoveryDriver` in `drivers/base.py`
- Created `DriverRegistry` in `drivers/registry.py` — maps platform prefixes to driver classes, supports longest-prefix matching
- Wrapped existing Cisco, HPE, and Meinberg vendor code as built-in drivers (`drivers/cisco/`, `drivers/hpe/`, `drivers/meinberg/`)
- Refactored `orchestrator.py` to use `DriverRegistry.device_category()` instead of string matching (`platform.startswith("cisco")`)
- Refactored `rollback/orchestrator.py` to use driver-based device classification and reset operations
- Generalized vendor-specific state names: `ILO_CONFIGURING` → `BMC_CONFIGURING`, `SPP_INSTALLING` → `DRIVER_PACK_INSTALLING`
- Added `DeviceCategory` enum (network/server/appliance) and `DevicePlatform.from_string()` classmethod
- Added `DeviceState.from_string()` with legacy name mapping for backward compatibility
- Added `vendor_config` field to `DeviceSpec` with auto-migration of legacy vendor-specific fields
- Moved Cisco platform constants (`PID_PLATFORM_MAP`, `NETMIKO_DEVICE_TYPE`) to `drivers/cisco/platforms.py`
- Updated `netbox/mapper.py` with `resolve_platform()` that falls through to driver registry
- Updated `dashboard/models.py` — platform field is now a free-text CharField, state choices updated
- Grouped vendor credentials in `settings.py` under `VENDOR_DEFAULTS` dict (backward-compatible)
- Added 18 new tests in `tests/unit/test_driver_registry.py`

**Key decisions**:
- Drivers are thin wrappers over existing vendor code (no rewrite of vendor logic)
- `DevicePlatform` enum kept for backward compatibility but orchestrator no longer requires it
- Registry uses prefix-based matching (e.g. `"cisco_"` matches `"cisco_ios"`, `"cisco_iosxe"`)
- New vendors only need to implement a driver ABC and register it — no core code changes needed

**Files created**:
- `src/bare_metal_automation/drivers/__init__.py`
- `src/bare_metal_automation/drivers/base.py`
- `src/bare_metal_automation/drivers/registry.py`
- `src/bare_metal_automation/drivers/cisco/__init__.py`
- `src/bare_metal_automation/drivers/cisco/network.py`
- `src/bare_metal_automation/drivers/cisco/discovery.py`
- `src/bare_metal_automation/drivers/cisco/platforms.py`
- `src/bare_metal_automation/drivers/hpe/__init__.py`
- `src/bare_metal_automation/drivers/hpe/server.py`
- `src/bare_metal_automation/drivers/hpe/platforms.py`
- `src/bare_metal_automation/drivers/meinberg/__init__.py`
- `src/bare_metal_automation/drivers/meinberg/appliance.py`
- `tests/unit/test_driver_registry.py`

**Files modified**:
- `src/bare_metal_automation/models.py`
- `src/bare_metal_automation/inventory.py`
- `src/bare_metal_automation/orchestrator.py`
- `src/bare_metal_automation/rollback/orchestrator.py`
- `src/bare_metal_automation/settings.py`
- `src/bare_metal_automation/discovery/serial.py`
- `src/bare_metal_automation/netbox/mapper.py`
- `src/bare_metal_automation/dashboard/models.py`
- `src/bare_metal_automation/provisioner/server.py`

---

## Architecture Notes for Future Sessions

### Source layout
```
src/bare_metal_automation/
├── __init__.py              # Version string
├── cli.py                   # Click CLI (discover, validate, configure, provision, serve)
├── models.py                # Dataclass models + enums (DeviceCategory, DevicePlatform, etc.)
├── inventory.py             # YAML inventory loader + validator (vendor_config migration)
├── orchestrator.py          # Phase-based state machine with driver-based dispatch
├── drivers/                 # Vendor driver framework
│   ├── __init__.py          # load_builtin_drivers() + exports
│   ├── base.py              # ABCs: NetworkDriver, ServerDriver, ApplianceDriver, DiscoveryDriver
│   ├── registry.py          # DriverRegistry — prefix-based platform → driver mapping
│   ├── cisco/               # Cisco network driver (IOS/IOS-XE/ASA/FTD)
│   │   ├── network.py       # CiscoNetworkDriver wrapping NetworkConfigurator + FirmwareConfigurator
│   │   ├── discovery.py     # CiscoCDPDiscovery wrapping CDPCollector
│   │   └── platforms.py     # PID maps, Netmiko types, flash paths, boot commands
│   ├── hpe/                 # HPE server driver (iLO 5 / Redfish)
│   │   ├── server.py        # HPEServerDriver wrapping HPEServerProvisioner + HPEServerResetter
│   │   └── platforms.py     # HPE platform constants
│   └── meinberg/            # Meinberg appliance driver (LANTIME NTP)
│       └── appliance.py     # MeinbergApplianceDriver wrapping MeinbergProvisioner + MeinbergResetter
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

### Supported hardware (built-in drivers)
- **Network** (Cisco): IOS/IOS-XE switches and routers, ASA / Firepower firewalls (SSH + CDP + Netmiko)
- **Server** (HPE): DL325/DL360/DL380 Gen10 servers (iLO 5 Redfish API)
- **Appliance** (Meinberg): LANTIME NTP appliances (REST API)
- New vendors can be added by implementing a driver ABC and registering with `DriverRegistry` — no core code changes needed

### Session 16 — Codebase Review & Security Audit

**Date**: 2026-04-02
**Branch**: `claude/codebase-review-audit-T8aZA`

**What was done**:
- Comprehensive codebase audit covering security, bugs, missing features, and code quality
- **Security fixes**:
  - Fixed stored XSS vulnerability in `index.html` — deployment name injected into JS without escaping (added `|escapejs` filter)
  - Fixed DOM-based XSS in service status rendering — raw template literals replaced with `escapeHtml()` helper
  - Removed `@csrf_exempt` from 10 browser-facing endpoints (deployment/rollback/simulation/prepare control), added CSRF token support via meta tag and `postWithCsrf()` JS helper
  - Kept `@csrf_exempt` on 7 machine-to-machine automation API endpoints (device registration, cabling results, logs, firmware sync)
- **Input validation added** to all write API endpoints:
  - Phase values validated against `Deployment.PHASE_CHOICES`
  - Device state validated against `Device.state` choices
  - Device role validated against `Device.role` choices
  - Cabling status validated against `CablingResult.STATUS_CHOICES`
  - Log level validated against standard Python log levels
  - Log message length capped at 10,000 characters
- **Bug fix**: `api_update_device_by_serial()` field whitelist was missing `"serial"` and `"mac"` fields, inconsistent with `api_update_device()` — now consistent
- **Silent exception handling**: Added `logger.warning()` / `logger.debug()` to bare `pass` blocks in rollback orchestrator, provisioner polling loops, and dashboard deployment/rollback crash handlers
- **NetBox `get_interfaces()` method**: Added to `netbox/client.py` and wired up in `config_media/generate.py` (was a TODO placeholder)
- **ROADMAP.md updated**: Marked completed tasks, fixed "Flask" → "Django", added "Known Incomplete Features" section

**Key decisions**:
- Dashboard authentication/authorization was flagged as critical but NOT implemented — too large for this session, documented as known gap
- CSRF split: browser-triggered endpoints now require CSRF token; automation API endpoints remain exempt for CLI/orchestrator compatibility
- URL namespace not added — `urls.py` is the ROOT_URLCONF, so `app_name` would break existing `{% url %}` tags

**Files modified**:
- `src/bare_metal_automation/dashboard/templates/dashboard/index.html` — XSS fixes, CSRF fetch calls
- `src/bare_metal_automation/dashboard/templates/dashboard/base.html` — CSRF meta tag, `postWithCsrf()` helper
- `src/bare_metal_automation/dashboard/views.py` — input validation, CSRF exempt removal
- `src/bare_metal_automation/netbox/client.py` — added `get_interfaces()` method
- `src/bare_metal_automation/config_media/generate.py` — wired up interface fetching
- `src/bare_metal_automation/rollback/orchestrator.py` — added logging to bare pass blocks
- `src/bare_metal_automation/provisioner/server.py` — added debug logging to polling loops
- `src/bare_metal_automation/dashboard/deployment.py` — added debug logging
- `src/bare_metal_automation/dashboard/rollback.py` — added debug logging
- `docs/ROADMAP.md` — updated completion status, fixed Flask→Django, added known gaps
