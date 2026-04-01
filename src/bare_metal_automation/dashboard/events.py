"""Django Channels event broadcaster — real-time deployment updates.

This module wraps the Django Channels layer so that any thread (orchestrator,
provisioner, etc.) can push events to connected WebSocket clients without
knowing about the WebSocket protocol.

All public functions are synchronous and safe to call from background threads.
They gracefully no-op if Django Channels is not installed or not configured.

Event types
-----------
  phase_started          : A deployment phase has begun
  phase_completed        : A deployment phase finished (success or failure)
  device_status_changed  : A device moved to a new state
  device_log             : A log message associated with a device
  deployment_log         : A deployment-level log message

WebSocket URL: ws://host/ws/deployment/{deployment_id}/

Frontend consumers should listen on the group ``deployment_{id}`` and
dispatch on the ``type`` field in the received JSON payload.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_layer():  # type: ignore[return]
    """Return the configured channel layer, or None if unavailable."""
    try:
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            logger.debug("Channels layer is not configured — events not broadcast")
        return layer
    except ImportError:
        return None


def _send(group: str, event_type: str, payload: dict[str, Any]) -> None:
    """Send a message to all WebSocket clients in *group* (fire-and-forget).

    Safe to call from any thread.  Silently swallows errors so that a
    misconfigured channel layer never breaks the deployment flow.
    """
    layer = _get_layer()
    if layer is None:
        return

    try:
        from asgiref.sync import async_to_sync

        async_to_sync(layer.group_send)(
            group,
            {
                "type": "deployment.event",   # routes to DeploymentConsumer.deployment_event
                "payload": {
                    "type": event_type,
                    **payload,
                },
            },
        )
    except Exception as e:
        logger.debug(f"Channel layer send failed ({group} / {event_type}): {e}")


def _group(deployment_id: int | str) -> str:
    return f"deployment_{deployment_id}"


# ── Public API ─────────────────────────────────────────────────────────────

def phase_started(
    deployment_id: int,
    phase: str,
    message: str = "",
) -> None:
    """Notify clients that a deployment phase has begun."""
    _send(_group(deployment_id), "phase_started", {
        "deployment_id": deployment_id,
        "phase": phase,
        "message": message or f"Phase '{phase}' started",
    })


def phase_completed(
    deployment_id: int,
    phase: str,
    success: bool = True,
    message: str = "",
) -> None:
    """Notify clients that a deployment phase has finished."""
    _send(_group(deployment_id), "phase_completed", {
        "deployment_id": deployment_id,
        "phase": phase,
        "success": success,
        "message": message or (
            f"Phase '{phase}' completed" if success
            else f"Phase '{phase}' failed"
        ),
    })


def device_status_changed(
    deployment_id: int,
    device_serial: str,
    device_hostname: str,
    state: str,
    message: str = "",
) -> None:
    """Notify clients that a device changed state."""
    _send(_group(deployment_id), "device_status_changed", {
        "deployment_id": deployment_id,
        "serial": device_serial,
        "hostname": device_hostname,
        "state": state,
        "message": message,
    })


def device_log(
    deployment_id: int,
    device_serial: str,
    device_hostname: str,
    level: str,
    message: str,
    phase: str = "",
) -> None:
    """Push a device-scoped log message to WebSocket clients."""
    _send(_group(deployment_id), "device_log", {
        "deployment_id": deployment_id,
        "serial": device_serial,
        "hostname": device_hostname,
        "level": level,
        "phase": phase,
        "message": message,
    })


def deployment_log(
    deployment_id: int,
    level: str,
    message: str,
    phase: str = "",
) -> None:
    """Push a deployment-level log entry to WebSocket clients."""
    _send(_group(deployment_id), "deployment_log", {
        "deployment_id": deployment_id,
        "level": level,
        "phase": phase,
        "message": message,
    })


def topology_updated(
    deployment_id: int,
    topology_data: dict[str, Any],
) -> None:
    """Push updated topology JSON (nodes + edges) to WebSocket clients.

    Called after the topology phase completes so the dashboard D3 graph
    re-renders with real device data.
    """
    _send(_group(deployment_id), "topology_updated", {
        "deployment_id": deployment_id,
        "topology": topology_data,
    })
