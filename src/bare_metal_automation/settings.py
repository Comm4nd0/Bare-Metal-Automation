"""Centralised configuration for Bare Metal Automation.

All constants that were previously scattered across modules are collected
here and can be overridden via environment variables.  Modules should
import from this file rather than defining their own defaults.
"""

from __future__ import annotations

import os
from typing import Any

# ── Vendor-specific defaults (grouped by vendor) ─────────────────────────────
VENDOR_DEFAULTS: dict[str, dict[str, Any]] = {
    "cisco": {
        "credentials": [
            ("cisco", "cisco"),
            ("admin", "admin"),
            ("admin", ""),
        ],
    },
    "hpe": {
        "ilo_user": os.environ.get("BMA_ILO_USER", "Administrator"),
        "ilo_password": os.environ.get("BMA_ILO_PASSWORD", "admin"),
    },
    "meinberg": {
        "user": os.environ.get("BMA_MEINBERG_USER", "admin"),
        "password": os.environ.get("BMA_MEINBERG_PASSWORD", ""),
    },
}

# ── iLO / Redfish credentials (backward-compatible accessors) ────────────────
ILO_DEFAULT_USER = VENDOR_DEFAULTS["hpe"]["ilo_user"]
ILO_DEFAULT_PASSWORD = VENDOR_DEFAULTS["hpe"]["ilo_password"]

# ── Meinberg NTP credentials (backward-compatible accessors) ─────────────────
MEINBERG_DEFAULT_USER = VENDOR_DEFAULTS["meinberg"]["user"]
MEINBERG_DEFAULT_PASSWORD = VENDOR_DEFAULTS["meinberg"]["password"]

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
    DEFAULT_CREDENTIALS = VENDOR_DEFAULTS["cisco"]["credentials"]

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
