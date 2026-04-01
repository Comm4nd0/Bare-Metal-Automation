"""
Django Channels WebSocket consumers for real-time deployment updates.

Each connected browser subscribes to a deployment group and receives JSON
events pushed by the backend as phases run and devices change state.

Event types:
    phase.started          — a phase has begun
    phase.completed        — a phase finished (with optional warning)
    phase.failed           — a phase failed
    device.status_changed  — a device's status field changed
    device.log             — a new DeviceLog entry was created
    deployment.completed   — the full deployment finished
    deployment.failed      — the deployment failed or was aborted
"""

from __future__ import annotations

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


def deployment_group(deployment_id: int) -> str:
    """Return the channel group name for a deployment."""
    return f"deployment_{deployment_id}"


class DeploymentConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for a single deployment."""

    async def connect(self) -> None:
        self.deployment_id: int = int(self.scope["url_route"]["kwargs"]["deployment_id"])
        self.group_name: str = deployment_group(self.deployment_id)

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.info("WS connect: deployment=%s channel=%s", self.deployment_id, self.channel_name)

    async def disconnect(self, code: int) -> None:
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        logger.info("WS disconnect: deployment=%s code=%s", self.deployment_id, code)

    async def receive(self, text_data: str = "", bytes_data: bytes = b"") -> None:
        """Clients may send a ping; we echo back a pong."""
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return
        if data.get("type") == "ping":
            await self.send(text_data=json.dumps({"type": "pong"}))

    # ------------------------------------------------------------------
    # Handlers for events pushed from the channel layer
    # Each handler name maps from the channel layer message type
    # (dots replaced with underscores by Channels).
    # ------------------------------------------------------------------

    async def phase_started(self, event: dict) -> None:
        await self.send(text_data=json.dumps(event))

    async def phase_completed(self, event: dict) -> None:
        await self.send(text_data=json.dumps(event))

    async def phase_failed(self, event: dict) -> None:
        await self.send(text_data=json.dumps(event))

    async def device_status_changed(self, event: dict) -> None:
        await self.send(text_data=json.dumps(event))

    async def device_log(self, event: dict) -> None:
        await self.send(text_data=json.dumps(event))

    async def deployment_completed(self, event: dict) -> None:
        await self.send(text_data=json.dumps(event))

    async def deployment_failed(self, event: dict) -> None:
        await self.send(text_data=json.dumps(event))


# ---------------------------------------------------------------------------
# Helpers — call these from management commands / async tasks to push events
# ---------------------------------------------------------------------------


async def push_phase_started(channel_layer, deployment_id: int, phase_number: int, phase_name: str) -> None:
    await channel_layer.group_send(
        deployment_group(deployment_id),
        {
            "type": "phase.started",
            "phase_number": phase_number,
            "phase_name": phase_name,
        },
    )


async def push_phase_completed(
    channel_layer,
    deployment_id: int,
    phase_number: int,
    phase_name: str,
    warning_count: int = 0,
    duration_seconds: float | None = None,
) -> None:
    await channel_layer.group_send(
        deployment_group(deployment_id),
        {
            "type": "phase.completed",
            "phase_number": phase_number,
            "phase_name": phase_name,
            "warning_count": warning_count,
            "duration_seconds": duration_seconds,
        },
    )


async def push_phase_failed(
    channel_layer,
    deployment_id: int,
    phase_number: int,
    phase_name: str,
    error_message: str = "",
) -> None:
    await channel_layer.group_send(
        deployment_group(deployment_id),
        {
            "type": "phase.failed",
            "phase_number": phase_number,
            "phase_name": phase_name,
            "error_message": error_message,
        },
    )


async def push_device_status_changed(
    channel_layer,
    deployment_id: int,
    device_id: int,
    hostname: str,
    serial_number: str,
    status: str,
    status_colour: str,
) -> None:
    await channel_layer.group_send(
        deployment_group(deployment_id),
        {
            "type": "device.status_changed",
            "device_id": device_id,
            "hostname": hostname,
            "serial_number": serial_number,
            "status": status,
            "status_colour": status_colour,
        },
    )


async def push_device_log(
    channel_layer,
    deployment_id: int,
    device_id: int,
    hostname: str,
    level: str,
    message: str,
    timestamp: str,
) -> None:
    await channel_layer.group_send(
        deployment_group(deployment_id),
        {
            "type": "device.log",
            "device_id": device_id,
            "hostname": hostname,
            "level": level,
            "message": message,
            "timestamp": timestamp,
        },
    )


async def push_deployment_completed(channel_layer, deployment_id: int, site_name: str) -> None:
    await channel_layer.group_send(
        deployment_group(deployment_id),
        {
            "type": "deployment.completed",
            "deployment_id": deployment_id,
            "site_name": site_name,
        },
    )


async def push_deployment_failed(
    channel_layer, deployment_id: int, site_name: str, error_message: str = ""
) -> None:
    await channel_layer.group_send(
        deployment_group(deployment_id),
        {
            "type": "deployment.failed",
            "deployment_id": deployment_id,
            "site_name": site_name,
            "error_message": error_message,
        },
    )
