"""URL configuration for ZTP-Forge dashboard."""

from django.urls import path

from . import views

urlpatterns = [
    # ── HTML pages ──────────────────────────────────────────────────────────
    path("", views.dashboard, name="dashboard"),
    path("deployments/", views.deployment_list, name="deployment-list"),
    path("deployments/<int:pk>/", views.deployment_detail, name="deployment-detail"),
    path("devices/<int:pk>/", views.device_detail, name="device-detail"),

    # ── Read API ────────────────────────────────────────────────────────────
    path("api/status/", views.api_status, name="api-status"),
    path("api/devices/<int:pk>/status/", views.api_device_status, name="api-device-status"),

    # ── Write API (called by the automation process) ────────────────────────
    path("api/deployments/", views.api_create_deployment, name="api-create-deployment"),
    path("api/deployments/<int:pk>/update/", views.api_update_deployment, name="api-update-deployment"),
    path(
        "api/deployments/<int:deployment_pk>/devices/",
        views.api_add_device,
        name="api-add-device",
    ),
    path("api/devices/<int:pk>/update/", views.api_update_device, name="api-update-device"),
    path(
        "api/deployments/<int:deployment_pk>/devices/serial/<str:serial>/",
        views.api_update_device_by_serial,
        name="api-update-device-by-serial",
    ),
    path(
        "api/devices/<int:device_pk>/cabling/",
        views.api_add_cabling_results,
        name="api-add-cabling-results",
    ),

    # ── Logs API (GET to read, POST to add) ─────────────────────────────────
    path(
        "api/deployments/<int:deployment_pk>/logs/",
        views.api_logs,
        name="api-logs",
    ),
]
