"""URL configuration for Bare Metal Automation dashboard."""

from django.urls import path

from . import firmware_views, views

urlpatterns = [
    # ── HTML pages ──────────────────────────────────────────────────────────
    path("", views.dashboard, name="dashboard"),
    path("deployments/", views.deployment_list, name="deployment-list"),
    path("deployments/<int:pk>/", views.deployment_detail, name="deployment-detail"),
    path("devices/<int:pk>/", views.device_detail, name="device-detail"),

    path("validate/", views.validate_inventory_page, name="validate-inventory"),

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

    # ── Inventory Validation API ──────────────────────────────────────────────
    path("api/inventory/validate/", views.api_validate_inventory, name="api-validate-inventory"),

    # ── Logs API (GET to read, POST to add) ─────────────────────────────────
    path(
        "api/deployments/<int:deployment_pk>/logs/",
        views.api_logs,
        name="api-logs",
    ),

    # ── Services API ────────────────────────────────────────────────────────
    path("api/services/", views.api_services, name="api-services"),

    # ── Deployment Control API ────────────────────────────────────────────
    path("api/deployment/start/", views.api_start_deployment, name="api-deployment-start"),
    path("api/deployment/stop/", views.api_stop_deployment, name="api-deployment-stop"),
    path("api/deployment/resume/", views.api_resume_deployment, name="api-deployment-resume"),
    path(
        "api/deployment/status/",
        views.api_deployment_control_status,
        name="api-deployment-status",
    ),

    # ── Rollback Control API ───────────────────────────────────────────────
    path(
        "api/rollback/start/",
        views.api_start_rollback,
        name="api-rollback-start",
    ),
    path(
        "api/rollback/stop/",
        views.api_stop_rollback,
        name="api-rollback-stop",
    ),
    path(
        "api/rollback/resume/",
        views.api_resume_rollback,
        name="api-rollback-resume",
    ),
    path(
        "api/rollback/status/",
        views.api_rollback_control_status,
        name="api-rollback-status",
    ),

    # ── Prepare Build API ───────────────────────────────────────────────────
    path(
        "api/prepare/nodes/",
        views.api_prepare_nodes,
        name="api-prepare-nodes",
    ),
    path(
        "api/prepare/start/",
        views.api_start_prepare,
        name="api-prepare-start",
    ),
    path(
        "api/prepare/stop/",
        views.api_stop_prepare,
        name="api-prepare-stop",
    ),
    path(
        "api/prepare/status/",
        views.api_prepare_status,
        name="api-prepare-status",
    ),

    # ── Simulation API ─────────────────────────────────────────────────────
    path(
        "api/simulation/start/",
        views.api_start_simulation,
        name="api-simulation-start",
    ),
    path(
        "api/simulation/stop/",
        views.api_stop_simulation,
        name="api-simulation-stop",
    ),
    path(
        "api/simulation/status/",
        views.api_simulation_status,
        name="api-simulation-status",
    ),

    # ── Firmware Management API ────────────────────────────────────────────
    path(
        "api/firmware/catalog/",
        firmware_views.api_firmware_catalog,
        name="api-firmware-catalog",
    ),
    path(
        "api/firmware/catalog/sync/",
        firmware_views.api_firmware_catalog_sync,
        name="api-firmware-catalog-sync",
    ),
    path(
        "api/firmware/tests/",
        firmware_views.api_firmware_tests,
        name="api-firmware-tests",
    ),
    path(
        "api/firmware/tests/record/",
        firmware_views.api_record_firmware_test,
        name="api-firmware-test-record",
    ),
    path(
        "api/firmware/tests/<int:pk>/",
        firmware_views.api_firmware_test_detail,
        name="api-firmware-test-detail",
    ),
    path(
        "api/firmware/compliance/",
        firmware_views.api_firmware_compliance,
        name="api-firmware-compliance",
    ),
    path(
        "api/firmware/compliance/record/",
        firmware_views.api_record_compliance,
        name="api-firmware-compliance-record",
    ),
]
