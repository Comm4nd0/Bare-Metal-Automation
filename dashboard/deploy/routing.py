"""WebSocket URL routing for the deploy app."""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/deployments/(?P<deployment_id>\d+)/$", consumers.DeploymentConsumer.as_asgi()),
]
