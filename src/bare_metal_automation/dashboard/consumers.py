"""Django Channels WebSocket consumers for real-time deployment updates.

Client connects to:  ws://host/ws/deployment/{deployment_id}/

The consumer joins the channel group ``deployment_{deployment_id}`` and
relays any message the server pushes to that group directly to the browser.

Message flow
------------
  Background thread
      → events.py (channel layer group_send)
          → Channels worker
              → DeploymentConsumer.deployment_event()
                  → WebSocket frame to browser

The consumer is read-only from the client's perspective — the browser
only receives, it does not send commands over this WebSocket.
"""

from __future__ import annotations

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer  # type: ignore[import]

logger = logging.getLogger(__name__)


class DeploymentConsumer(AsyncWebsocketConsumer):
    """Relay deployment events to a single connected WebSocket client."""

    async def connect(self) -> None:
        deployment_id = self.scope["url_route"]["kwargs"]["deployment_id"]
        self.group_name = f"deployment_{deployment_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        logger.debug(
            f"WebSocket connected: deployment {deployment_id}, "
            f"channel {self.channel_name}"
        )

    async def disconnect(self, close_code: int) -> None:
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        logger.debug(
            f"WebSocket disconnected: group {self.group_name}, "
            f"code {close_code}"
        )

    # ── Receive from channel layer ─────────────────────────────────────────

    async def deployment_event(self, event: dict) -> None:
        """Forward any deployment event payload to the connected browser.

        The *event* dict contains a ``payload`` key produced by ``events.py``.
        """
        payload = event.get("payload", {})
        await self.send(text_data=json.dumps(payload))

    # ── Receive from browser (ignored — read-only consumer) ────────────────

    async def receive(self, text_data: str = "", bytes_data: bytes = b"") -> None:
        # Clients don't send commands over this channel — ignore silently.
        pass
