# Bare Metal Automation Development Roadmap

## Milestone 1 — Foundation (MVP)

The goal is a working CLI that can discover devices on a flat network and build a topology map. No configuration push yet — just observe and report.

### Tasks

- [x] **Project scaffolding** — pyproject.toml, CI pipeline, pre-commit hooks
- [x] **DHCP lease parser** — Parse dnsmasq lease file *(Note: programmatic dnsmasq start/stop not yet implemented)*
- [x] **CDP collector** — SSH to discovered IPs, run `show cdp neighbors detail`, parse output
- [x] **Serial collector** — SSH to discovered IPs, run `show inventory`, extract serial/PID
- [x] **Inventory loader** — Parse inventory YAML, validate schema
- [x] **Device matcher** — Match discovered serials to inventory roles
- [x] **Topology graph builder** — Build NetworkX graph from CDP data
- [x] **BFS ordering** — Calculate configuration order from graph
- [x] **CLI entry point** — `bare-metal-automation discover` command
- [ ] **Mock device simulator** — Fake SSH responder for testing without hardware
- [x] **Unit tests** for all parsers

### Definition of Done
Running `bare-metal-automation discover` against a rack of factory-new kit produces a JSON topology map with each device identified by serial, role, depth, and neighbours.

---

## Milestone 2 — Cabling Validation

### Tasks

- [x] **Intent parser** — Extract expected connections from Jinja2 config templates (interface descriptions, port-channel members)
- [x] **Cabling diff engine** — Compare CDP actual vs template intent
- [x] **Adaptation engine** — For flexible ports (server access ports), generate modified configs matching actual cabling
- [x] **Validation report** — Structured output: correct / adaptable / mismatched / missing / unexpected
- [x] **CLI command** — `bare-metal-automation validate`
- [x] **Unit tests** for intent parsing and diff logic

### Definition of Done
Running `bare-metal-automation validate` produces a clear report showing every connection that matches, mismatches, or is missing, with suggested adaptations for flexible ports.

---

## Milestone 3 — Network Configuration Engine

### Tasks

- [x] **Config renderer** — Jinja2 template rendering with inventory variables
- [ ] **Ansible dynamic inventory** — Generate Ansible inventory from discovered topology
- [ ] **Ansible playbooks** — Per-platform configuration playbooks (IOS switch, IOS router, ASA firewall)
- [x] **Dead man's switch** — Implement `reload in N` / `reload cancel` logic around config pushes
- [x] **Rollback handler** — Detect failed validation, trigger rollback
- [x] **Outside-in orchestrator** — Execute config push in BFS reverse order
- [ ] **Post-config validation** — Per-device health checks (STP root, trunk status, routing adjacency) *(basic health checks exist, advanced checks not yet implemented)*
- [x] **CLI command** — `bare-metal-automation configure-network`
- [ ] **Integration tests** with mock devices

### Definition of Done
Running `bare-metal-automation configure-network` configures all network devices in the correct order with rollback protection, and all post-config validations pass.

---

## Milestone 4 — Server Provisioning

### Tasks

- [x] **Redfish client** — Wrapper around HPE iLO 5 Redfish API
- [x] **iLO discovery** — Find and authenticate to iLO endpoints via DHCP leases
- [x] **Firmware update** — Upload and apply iLO firmware via Redfish
- [x] **BIOS configuration** — Set boot order, virtualisation, RAID config via Redfish
- [x] **Virtual media mount** — Mount OS ISO via Redfish, trigger boot
- [x] **Install monitor** — Poll Redfish for OS install progress
- [x] **PXE fallback** — TFTP/PXE boot for cases where virtual media isn't suitable
- [ ] **Post-install Ansible** — OS hardening, packages, agents, domain join
- [x] **CLI command** — `bare-metal-automation provision-servers`

### Definition of Done
Running `bare-metal-automation provision-servers` takes factory-new DL325s through to a hardened, domain-joined OS with monitoring agents running.

---

## Milestone 5 — Dashboard

### Tasks

- [x] **Django application** — Core web app *(authentication not yet implemented)*
- [x] **WebSocket status stream** — Real-time phase and device status updates via Django Channels
- [ ] **Topology visualisation** — Interactive network graph (D3.js or vis.js)
- [ ] **Cabling validation view** — Visual diff of actual vs intended connections
- [x] **Deploy button** — Single-action deployment trigger with confirmation gate
- [x] **Discover-only mode** — Run discovery and validation without pushing config *(simulation mode)*
- [x] **Log viewer** — Streaming log output per device
- [ ] **Deployment report** — Final summary with pass/fail per device

### Definition of Done
A user can open the dashboard in a browser, see the discovered topology, review cabling validation, and press a single button to execute the full deployment with live progress updates.

---

## Milestone 6 — Hardening & Reliability

### Tasks

- [ ] **Serial console fallback** — pyserial-based factory reset for unresponsive devices *(partially stubbed)*
- [x] **Retry logic** — Configurable retry with backoff for SSH/Redfish failures
- [x] **Partial deployment** — Resume from last successful phase after a failure
- [x] **State persistence** — Save deployment state to disk so the process survives a laptop reboot
- [ ] **Multi-NIC support** — Handle laptops with multiple interfaces (USB ethernet adapters)
- [ ] **LLDP support** — For mixed-vendor environments where CDP isn't available
- [x] **Deployment history** — Log past deployments for audit trail *(DeploymentLog model exists)*
- [ ] **Config drift detection** — Post-deployment periodic validation

### Definition of Done
Bare Metal Automation can handle real-world failure scenarios gracefully — devices that don't respond, partial deployments, laptop reboots mid-deploy — and recover without manual intervention.

---

## Known Incomplete Features

These items exist as stubs or partial implementations:

- **VMware factory reset** — Phases 1-3 (VM teardown, NSX teardown, vCenter teardown) raise `NotImplementedError` in `factory_reset/reset.py`. Requires pyVmomi (vSphere SDK).
- **VMware sanitisation** — `factory_reset/sanitise.py` raises `NotImplementedError` for VMware certificate removal.
- **Dashboard authentication** — No `@login_required` or user management. Critical for production use.
- **DHCP server management** — Only reads dnsmasq lease file; does not start/stop dnsmasq programmatically.

---

## Future Considerations

- **Vendor expansion** — Arista, Juniper, Dell switches; Dell/Lenovo servers
- **Ansible-free mode** — Direct Netmiko/NAPALM for lighter-weight deployments
- **Cloud integration** — Register deployed devices with a CMDB or monitoring platform
- **Secure boot chain** — Verify firmware signatures before applying updates
- **Air-gapped mode** — Package all dependencies for environments with no internet
- **API rate limiting** — Add rate limiting to dashboard API endpoints
- **Dependency version pinning** — Pin dependency version ranges in pyproject.toml
