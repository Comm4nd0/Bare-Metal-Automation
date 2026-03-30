"""Checkpoint persistence — save and restore deployment state to/from disk.

Allows a deployment to be stopped at any point and resumed later. State is
serialized to a JSON file after each phase completes. On resume, the
orchestrator loads the checkpoint and skips already-completed phases.

The default checkpoint location is ```.bma-checkpoint.json`` in the
current working directory. A custom path can be provided.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from bare_metal_automation.models import (
    CablingResult,
    CDPNeighbour,
    DeploymentPhase,
    DeploymentState,
    DevicePlatform,
    DeviceRole,
    DeviceState,
    DiscoveredDevice,
)

logger = logging.getLogger(__name__)

DEFAULT_CHECKPOINT_PATH = Path(".bma-checkpoint.json")


# ── Serialization ──────────────────────────────────────────────────────────


def _serialize_device(device: DiscoveredDevice) -> dict[str, Any]:
    """Convert a DiscoveredDevice to a JSON-safe dict."""
    d = asdict(device)
    # Enum values → their string values for JSON
    d["state"] = device.state.value
    if device.role is not None:
        d["role"] = device.role.value
    if device.device_platform is not None:
        d["device_platform"] = device.device_platform.value
    return d


def _serialize_cabling_result(result: CablingResult) -> dict[str, Any]:
    """Convert a CablingResult to a JSON-safe dict."""
    return asdict(result)


def serialize_state(
    state: DeploymentState,
    inventory_path: str,
    ssh_timeout: int,
) -> dict[str, Any]:
    """Serialize the full deployment state to a JSON-compatible dict.

    Includes metadata needed to reconstruct the Orchestrator on resume:
    inventory path, ssh timeout, and a timestamp.
    """
    return {
        "version": 1,
        "saved_at": datetime.now().isoformat(),
        "inventory_path": str(inventory_path),
        "ssh_timeout": ssh_timeout,
        "phase": state.phase.value,
        "discovered_devices": {
            ip: _serialize_device(device)
            for ip, device in state.discovered_devices.items()
        },
        "topology_order": state.topology_order,
        "cabling_results": {
            serial: [_serialize_cabling_result(r) for r in results]
            for serial, results in state.cabling_results.items()
        },
        "errors": state.errors,
        "warnings": state.warnings,
    }


# ── Deserialization ────────────────────────────────────────────────────────


def _deserialize_cdp_neighbour(data: dict[str, Any]) -> CDPNeighbour:
    return CDPNeighbour(**data)


def _deserialize_device(data: dict[str, Any]) -> DiscoveredDevice:
    """Reconstruct a DiscoveredDevice from a serialized dict."""
    data = dict(data)  # shallow copy to avoid mutating input
    data["state"] = DeviceState(data["state"])
    if data.get("role") is not None:
        data["role"] = DeviceRole(data["role"])
    if data.get("device_platform") is not None:
        data["device_platform"] = DevicePlatform(data["device_platform"])
    data["cdp_neighbours"] = [
        _deserialize_cdp_neighbour(n) for n in data.get("cdp_neighbours", [])
    ]
    return DiscoveredDevice(**data)


def _deserialize_cabling_result(data: dict[str, Any]) -> CablingResult:
    return CablingResult(**data)


def deserialize_state(data: dict[str, Any]) -> DeploymentState:
    """Reconstruct a DeploymentState from a serialized dict."""
    state = DeploymentState()
    state.phase = DeploymentPhase(data["phase"])
    state.discovered_devices = {
        ip: _deserialize_device(device_data)
        for ip, device_data in data.get("discovered_devices", {}).items()
    }
    state.topology_order = data.get("topology_order", [])
    state.cabling_results = {
        serial: [_deserialize_cabling_result(r) for r in results]
        for serial, results in data.get("cabling_results", {}).items()
    }
    state.errors = data.get("errors", [])
    state.warnings = data.get("warnings", [])
    return state


# ── File I/O ───────────────────────────────────────────────────────────────


def save_checkpoint(
    state: DeploymentState,
    inventory_path: str,
    ssh_timeout: int,
    checkpoint_path: Path | str = DEFAULT_CHECKPOINT_PATH,
) -> Path:
    """Write the current deployment state to a JSON checkpoint file.

    Returns the path to the written file.
    """
    path = Path(checkpoint_path)
    payload = serialize_state(state, inventory_path, ssh_timeout)

    # Write atomically: write to temp file then rename
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2))
    tmp_path.rename(path)

    logger.info("Checkpoint saved to %s (phase: %s)", path, state.phase.value)
    return path


def load_checkpoint(
    checkpoint_path: Path | str = DEFAULT_CHECKPOINT_PATH,
) -> dict[str, Any]:
    """Load a checkpoint file and return the raw dict.

    Returns the full checkpoint dict including metadata (inventory_path,
    ssh_timeout) and the serialized state. Raises FileNotFoundError if
    the checkpoint doesn't exist.
    """
    path = Path(checkpoint_path)
    if not path.exists():
        raise FileNotFoundError(f"No checkpoint file found at {path}")

    data: dict[str, Any] = json.loads(path.read_text())
    logger.info(
        "Loaded checkpoint from %s (phase: %s, saved: %s)",
        path,
        data.get("phase"),
        data.get("saved_at"),
    )
    return data


def remove_checkpoint(
    checkpoint_path: Path | str = DEFAULT_CHECKPOINT_PATH,
) -> None:
    """Delete the checkpoint file if it exists."""
    path = Path(checkpoint_path)
    if path.exists():
        path.unlink()
        logger.info("Removed checkpoint file %s", path)
