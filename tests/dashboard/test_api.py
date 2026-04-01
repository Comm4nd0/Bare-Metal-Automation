"""Tests for the deploy REST API."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from deploy.models import (
    PHASE_NAMES,
    Deployment,
    DeploymentDevice,
    DeploymentPhase,
    DeploymentStatus,
    FactoryReset,
    PhaseStatus,
    ResetPhase,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def deployment(db):
    d = Deployment.objects.create(
        site_name="API Test Site",
        site_slug="api-test-site",
        template_name="dc-standard",
        template_version="1.0.0",
        bundle_path="/tmp/bundle",
        manifest_hash="deadbeef",
    )
    for num, name in PHASE_NAMES.items():
        DeploymentPhase.objects.create(deployment=d, phase_number=num, phase_name=name)
    DeploymentDevice.objects.create(
        deployment=d,
        serial_number="FCW001",
        hostname="sw-01",
        role="core-switch",
        platform="cisco_iosxe",
    )
    return d


# ---------------------------------------------------------------------------
# Deployment list / detail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeploymentAPI:
    def test_list_deployments(self, api_client, deployment):
        response = api_client.get("/api/deployments/")
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["site_name"] == "API Test Site"

    def test_retrieve_deployment(self, api_client, deployment):
        response = api_client.get(f"/api/deployments/{deployment.pk}/")
        assert response.status_code == 200
        data = response.json()
        assert data["site_slug"] == "api-test-site"
        assert "phases" in data
        assert "devices" in data
        assert len(data["phases"]) == 11
        assert len(data["devices"]) == 1

    def test_deployment_progress_pct(self, api_client, deployment):
        response = api_client.get(f"/api/deployments/{deployment.pk}/")
        assert response.status_code == 200
        assert response.json()["progress_pct"] == 0

    def test_deployment_phases_action(self, api_client, deployment):
        response = api_client.get(f"/api/deployments/{deployment.pk}/phases/")
        assert response.status_code == 200
        phases = response.json()
        assert len(phases) == 11
        assert all(p["status"] == "pending" for p in phases)

    def test_deployment_devices_action(self, api_client, deployment):
        response = api_client.get(f"/api/deployments/{deployment.pk}/devices/")
        assert response.status_code == 200
        devices = response.json()
        assert len(devices) == 1
        assert devices[0]["hostname"] == "sw-01"

    def test_deployment_not_found(self, api_client):
        response = api_client.get("/api/deployments/99999/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Phase API
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPhaseAPI:
    def test_list_phases(self, api_client, deployment):
        response = api_client.get("/api/phases/")
        assert response.status_code == 200
        assert response.json()["results"]

    def test_phase_traffic_light(self, api_client, deployment):
        # Start phase 0 and verify traffic_light changes
        phase = deployment.phases.get(phase_number=0)
        phase.start()

        response = api_client.get(f"/api/phases/{phase.pk}/")
        assert response.status_code == 200
        assert response.json()["traffic_light"] == "blue"


# ---------------------------------------------------------------------------
# Device API
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeviceAPI:
    def test_list_devices(self, api_client, deployment):
        response = api_client.get("/api/devices/")
        assert response.status_code == 200
        assert len(response.json()["results"]) == 1

    def test_device_logs_action(self, api_client, deployment):
        device = deployment.devices.first()
        response = api_client.get(f"/api/devices/{device.pk}/logs/")
        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------------------
# Factory Reset API
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFactoryResetAPI:
    def test_list_resets_empty(self, api_client, deployment):
        response = api_client.get("/api/resets/")
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_list_resets_with_data(self, api_client, deployment):
        FactoryReset.objects.create(deployment=deployment)
        response = api_client.get("/api/resets/")
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_factory_resets_action_on_deployment(self, api_client, deployment):
        FactoryReset.objects.create(deployment=deployment)
        response = api_client.get(f"/api/deployments/{deployment.pk}/factory_resets/")
        assert response.status_code == 200
        assert len(response.json()) == 1
