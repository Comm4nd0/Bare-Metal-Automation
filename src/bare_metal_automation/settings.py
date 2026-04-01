"""Centralised configuration for Bare Metal Automation.

All constants that were previously scattered across modules are collected
here and can be overridden via environment variables.  Modules should
import from this file rather than defining their own defaults.
"""

from __future__ import annotations

import os

# ── iLO / Redfish credentials ────────────────────────────────────────────────
ILO_DEFAULT_USER = os.environ.get("BMA_ILO_USER", "Administrator")
ILO_DEFAULT_PASSWORD = os.environ.get("BMA_ILO_PASSWORD", "admin")

# ── Meinberg NTP credentials ─────────────────────────────────────────────────
MEINBERG_DEFAULT_USER = os.environ.get("BMA_MEINBERG_USER", "admin")
MEINBERG_DEFAULT_PASSWORD = os.environ.get("BMA_MEINBERG_PASSWORD", "")

# ── Discovery credentials (tried in order) ────────────────────────────────────
# Override with a comma-separated list of user:password pairs
# e.g. BMA_DISCOVERY_CREDENTIALS="cisco:cisco,admin:admin,admin:"
_cred_env = os.environ.get("BMA_DISCOVERY_CREDENTIALS")
if _cred_env:
    DEFAULT_CREDENTIALS: list[tuple[str, str]] = [
        tuple(pair.split(":", 1))  # type: ignore[misc]
        for pair in _cred_env.split(",")
        if ":" in pair
    ]
else:
    DEFAULT_CREDENTIALS = [
        ("cisco", "cisco"),
        ("admin", "admin"),
        ("admin", ""),
    ]

# ── Redfish API paths ────────────────────────────────────────────────────────
REDFISH_BASE = "/redfish/v1"
REDFISH_SYSTEMS = f"{REDFISH_BASE}/Systems/1"
REDFISH_MANAGERS = f"{REDFISH_BASE}/Managers/1"
REDFISH_UPDATE = f"{REDFISH_BASE}/UpdateService"

# ── Meinberg API paths ───────────────────────────────────────────────────────
MEINBERG_API_BASE = "/api/v1"

# ── Timeouts (seconds) ───────────────────────────────────────────────────────
LONG_OP_TIMEOUT = int(os.environ.get("BMA_LONG_OP_TIMEOUT", "3600"))
POLL_INTERVAL = int(os.environ.get("BMA_POLL_INTERVAL", "30"))
SSH_TIMEOUT = int(os.environ.get("BMA_SSH_TIMEOUT", "30"))
RELOAD_TIMER_MINUTES = int(os.environ.get("BMA_RELOAD_TIMER_MINUTES", "5"))
REBOOT_WAIT = int(os.environ.get("BMA_REBOOT_WAIT", "120"))
POLL_TIMEOUT = int(os.environ.get("BMA_POLL_TIMEOUT", "300"))

# ── SSL verification ─────────────────────────────────────────────────────────
# Default to False for lab/field use (self-signed certs on iLO/Meinberg).
# Set BMA_VERIFY_SSL=1 in production if proper certs are deployed.
VERIFY_SSL = os.environ.get("BMA_VERIFY_SSL", "0").lower() in ("1", "true", "yes")

# ── File paths ────────────────────────────────────────────────────────────────
FIRMWARE_DIR = os.environ.get("BMA_FIRMWARE_DIR", "configs/firmware")
TEMPLATE_DIR = os.environ.get("BMA_TEMPLATE_DIR", "configs/templates")
ISO_DIR = os.environ.get("BMA_ISO_DIR", "configs/iso")
INVENTORY_DIR = os.environ.get("BMA_INVENTORY_DIR", "configs/inventory")
LEASE_FILE = os.environ.get("BMA_LEASE_FILE", "/var/lib/misc/dnsmasq.leases")
