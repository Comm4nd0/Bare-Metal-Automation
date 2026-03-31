# Bare Metal Automation Development Roadmap

## Milestone 1 — Foundation (MVP)

The goal is a working CLI that can discover devices on a flat network and build a topology map. No configuration push yet — just observe and report.

### Tasks

- [ ] **Project scaffolding** — pyproject.toml, CI pipeline, pre-commit hooks
- [ ] **DHCP server wrapper** — Start/stop dnsmasq programmatically, parse lease file
- [ ] **CDP collector** — SSH to discovered IPs, run `show cdp neighbors detail`, parse output
- [ ] **Serial collector** — SSH to discovered IPs, run `show inventory`, extract serial/PID
- [ ] **Inventory loader** — Parse inventory YAML, validate schema
- [ ] **Device matcher** — Match discovered serials to inventory roles
- [ ] **Topology graph builder** — Build NetworkX graph from CDP data
- [ ] **BFS ordering** — Calculate configuration order from graph
- [ ] **CLI entry point** — `bare-metal-automation discover` command
- [ ] **Mock device simulator** — Fake SSH responder for testing without hardware
- [ ] **Unit tests** for all parsers

### Definition of Done
Running `bare-metal-automation discover` against a rack of factory-new kit produces a JSON topology map with each device identified by serial, role, depth, and neighbours.

---

## Milestone 2 — Cabling Validation

### Tasks

- [ ] **Intent parser** — Extract expected connections from Jinja2 config templates (interface descriptions, port-channel members)
- [ ] **Cabling diff engine** — Compare CDP actual vs template intent
- [ ] **Adaptation engine** — For flexible ports (server access ports), generate modified configs matching actual cabling
- [ ] **Validation report** — Structured output: correct / adaptable / mismatched / missing / unexpected
- [ ] **CLI command** — `bare-metal-automation validate`
- [ ] **Unit tests** for intent parsing and diff logic

### Definition of Done
Running `bare-metal-automation validate` produces a clear report showing every connection that matches, mismatches, or is missing, with suggested adaptations for flexible ports.

---

## Milestone 3 — Network Configuration Engine

### Tasks

- [ ] **Config renderer** — Jinja2 template rendering with inventory variables
- [ ] **Ansible dynamic inventory** — Generate Ansible inventory from discovered topology
- [ ] **Ansible playbooks** — Per-platform configuration playbooks (IOS switch, IOS router, ASA firewall)
- [ ] **Dead man's switch** — Implement `reload in N` / `reload cancel` logic around config pushes
- [ ] **Rollback handler** — Detect failed validation, trigger rollback
- [ ] **Outside-in orchestrator** — Execute config push in BFS reverse order
- [ ] **Post-config validation** — Per-device health checks (STP root, trunk status, routing adjacency)
- [ ] **CLI command** — `bare-metal-automation configure-network`
- [ ] **Integration tests** with mock devices

### Definition of Done
Running `bare-metal-automation configure-network` configures all network devices in the correct order with rollback protection, and all post-config validations pass.

---

## Milestone 4 — Server Provisioning

### Tasks

- [ ] **Redfish client** — Wrapper around HPE iLO 5 Redfish API
- [ ] **iLO discovery** — Find and authenticate to iLO endpoints via DHCP leases
- [ ] **Firmware update** — Upload and apply iLO firmware via Redfish
- [ ] **BIOS configuration** — Set boot order, virtualisation, RAID config via Redfish
- [ ] **Virtual media mount** — Mount OS ISO via Redfish, trigger boot
- [ ] **Install monitor** — Poll Redfish for OS install progress
- [ ] **PXE fallback** — TFTP/PXE boot for cases where virtual media isn't suitable
- [ ] **Post-install Ansible** — OS hardening, packages, agents, domain join
- [ ] **CLI command** — `bare-metal-automation provision-servers`

### Definition of Done
Running `bare-metal-automation provision-servers` takes factory-new DL325s through to a hardened, domain-joined OS with monitoring agents running.

---

## Milestone 5 — Dashboard

### Tasks

- [ ] **Flask application** — Core web app with authentication
- [ ] **WebSocket status stream** — Real-time phase and device status updates
- [ ] **Topology visualisation** — Interactive network graph (D3.js or vis.js)
- [ ] **Cabling validation view** — Visual diff of actual vs intended connections
- [ ] **Deploy button** — Single-action deployment trigger with confirmation gate
- [ ] **Discover-only mode** — Run discovery and validation without pushing config
- [ ] **Log viewer** — Streaming log output per device
- [ ] **Deployment report** — Final summary with pass/fail per device

### Definition of Done
A user can open the dashboard in a browser, see the discovered topology, review cabling validation, and press a single button to execute the full deployment with live progress updates.

---

## Milestone 6 — Hardening & Reliability

### Tasks

- [ ] **Serial console fallback** — pyserial-based factory reset for unresponsive devices
- [ ] **Retry logic** — Configurable retry with backoff for SSH/Redfish failures
- [ ] **Partial deployment** — Resume from last successful phase after a failure
- [ ] **State persistence** — Save deployment state to disk so the process survives a laptop reboot
- [ ] **Multi-NIC support** — Handle laptops with multiple interfaces (USB ethernet adapters)
- [ ] **LLDP support** — For mixed-vendor environments where CDP isn't available
- [ ] **Deployment history** — Log past deployments for audit trail
- [ ] **Config drift detection** — Post-deployment periodic validation

### Definition of Done
Bare Metal Automation can handle real-world failure scenarios gracefully — devices that don't respond, partial deployments, laptop reboots mid-deploy — and recover without manual intervention.

---

## Future Considerations

- **Vendor expansion** — Arista, Juniper, Dell switches; Dell/Lenovo servers
- **Ansible-free mode** — Direct Netmiko/NAPALM for lighter-weight deployments
- **Cloud integration** — Register deployed devices with a CMDB or monitoring platform
- **Secure boot chain** — Verify firmware signatures before applying updates
- **Air-gapped mode** — Package all dependencies for environments with no internet
