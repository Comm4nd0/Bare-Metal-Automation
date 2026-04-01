"""ASGI application with WebSocket routing for Django Channels.

This module replaces the plain WSGI ``application`` from ``wsgi.py`` when
serving with an ASGI server (Daphne, Uvicorn, Hypercorn).

URL patterns
------------
  ws://host/ws/deployment/{id}/   → DeploymentConsumer

HTTP traffic is handled by Django's standard ASGI application, so all
existing REST/HTML views continue to work unchanged.
"""

from __future__ import annotations

import os

from channels.auth import AuthMiddlewareStack  # type: ignore[import]
from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import]
from django.core.asgi import get_asgi_application
from django.urls import re_path

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "bare_metal_automation.dashboard.settings",
)

# Django ASGI application must be loaded before importing Channels consumers
# so that the app registry is ready.
_django_asgi_app = get_asgi_application()

from bare_metal_automation.dashboard.consumers import DeploymentConsumer  # noqa: E402

websocket_urlpatterns = [
    re_path(
        r"^ws/deployment/(?P<deployment_id>\d+)/$",
        DeploymentConsumer.as_asgi(),
    ),
]

application = ProtocolTypeRouter({
    "http": _django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
