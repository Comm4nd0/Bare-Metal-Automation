"""Unit tests for deploy and fleet app models."""

import pytest
from deploy.models import (
    PHASE_NAMES,
    RESET_PHASE_NAMES,
    Deployment,
    DeploymentDevice,
    DeploymentPhase,
    DeploymentStatus,
    DeviceLog,
    DeviceResetCertificate,
    DeviceStatus,
    FactoryReset,
    LogLevel,
    PhaseStatus,
    ResetPhase,
    ResetStatus,
)
from django.contrib.auth.models import User
from fleet.models import FleetScan, SiteComplianceRecord, SiteRecord, TemplateRecord

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="test_operator", password="pw")


@pytest.fixture
def deployment(db, operator):
    d = Deployment.objects.create(
        site_name="Test Site",
        site_slug="test-site",
        template_name="datacenter-v2",
        template_version="2.3.1",
        bundle_path="/tmp/bundle",
        manifest_hash="abc123",
        operator=operator,
    )
    for num, name in PHASE_NAMES.items():
        DeploymentPhase.objects.create(deployment=d, phase_number=num, phase_name=name)
    return d


@pytest.fixture
def device(db, deployment):
    return DeploymentDevice.objects.create(
        deployment=deployment,
        serial_number="FCW2345A001",
        hostname="core-sw-01",
        role="core-switch",
        platform="cisco_iosxe",
    )


# ---------------------------------------------------------------------------
# Deployment tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeployment:
    def test_create_deployment(self, deployment):
        assert deployment.pk is not None
        assert deployment.status == DeploymentStatus.INGESTED
        assert deployment.site_slug == "test-site"

    def test_str(self, deployment):
        assert "Test Site" in str(deployment)
        assert "ingested" in str(deployment)

    def test_phases_created(self, deployment):
        assert deployment.phases.count() == 11
        assert deployment.phases.filter(status=PhaseStatus.PENDING).count() == 11

    def test_progress_pct_all_pending(self, deployment):
        assert deployment.progress_pct == 0

    def test_progress_pct_partial(self, deployment):
        deployment.phases.filter(phase_number__lt=5).update(status=PhaseStatus.COMPLETED)
        # Refresh from DB to bust any cached property
        deployment.refresh_from_db()
        assert deployment.progress_pct == pytest.approx(45, abs=5)

    def test_progress_pct_all_done(self, deployment):
        deployment.phases.all().update(status=PhaseStatus.COMPLETED)
        deployment.refresh_from_db()
        assert deployment.progress_pct == 100

    def test_start(self, deployment):
        deployment.start()
        deployment.refresh_from_db()
        assert deployment.status == DeploymentStatus.RUNNING
        assert deployment.started_at is not None

    def test_complete(self, deployment):
        deployment.start()
        deployment.complete()
        deployment.refresh_from_db()
        assert deployment.status == DeploymentStatus.COMPLETED
        assert deployment.completed_at is not None
        assert deployment.duration_seconds is not None
        assert deployment.duration_seconds >= 0

    def test_fail(self, deployment):
        deployment.fail()
        deployment.refresh_from_db()
        assert deployment.status == DeploymentStatus.FAILED

    def test_current_phase_none_when_all_pending(self, deployment):
        assert deployment.current_phase is None

    def test_current_phase_when_running(self, deployment):
        phase = deployment.phases.get(phase_number=1)
        phase.start()
        assert deployment.current_phase == phase


# ---------------------------------------------------------------------------
# DeploymentPhase tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeploymentPhase:
    def test_traffic_light_pending(self, deployment):
        phase = deployment.phases.get(phase_number=0)
        assert phase.traffic_light == "grey"

    def test_traffic_light_running(self, deployment):
        phase = deployment.phases.get(phase_number=0)
        phase.start()
        phase.refresh_from_db()
        assert phase.traffic_light == "blue"

    def test_traffic_light_completed(self, deployment):
        phase = deployment.phases.get(phase_number=0)
        phase.start()
        phase.complete()
        phase.refresh_from_db()
        assert phase.traffic_light == "green"

    def test_traffic_light_warning(self, deployment):
        phase = deployment.phases.get(phase_number=0)
        phase.start()
        phase.complete(warning_count=2)
        phase.refresh_from_db()
        assert phase.traffic_light == "amber"
        assert phase.warning_count == 2

    def test_traffic_light_failed(self, deployment):
        phase = deployment.phases.get(phase_number=0)
        phase.start()
        phase.fail(error_message="SSH timeout")
        phase.refresh_from_db()
        assert phase.traffic_light == "red"
        assert "SSH timeout" in phase.error_message

    def test_duration_recorded_on_complete(self, deployment):
        phase = deployment.phases.get(phase_number=0)
        phase.start()
        phase.complete()
        phase.refresh_from_db()
        assert phase.duration_seconds is not None
        assert phase.duration_seconds >= 0

    def test_phase_ordering(self, deployment):
        numbers = list(deployment.phases.values_list("phase_number", flat=True))
        assert numbers == sorted(numbers)

    def test_unique_constraint(self, deployment):
        from django.db import IntegrityError
        with pytest.raises(IntegrityError):
            DeploymentPhase.objects.create(
                deployment=deployment,
                phase_number=0,
                phase_name="Duplicate",
            )


# ---------------------------------------------------------------------------
# DeploymentDevice tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeploymentDevice:
    def test_create_device(self, device):
        assert device.pk is not None
        assert device.status == DeviceStatus.PENDING
        assert device.status_colour == "grey"

    def test_status_colour_mapping(self, device):
        for status, expected_colour in [
            (DeviceStatus.DISCOVERED, "blue"),
            (DeviceStatus.CONFIGURED, "green"),
            (DeviceStatus.FAILED, "red"),
            (DeviceStatus.MISSING, "amber"),
        ]:
            device.status = status
            device.save()
            device.refresh_from_db()
            assert device.status_colour == expected_colour

    def test_str(self, device):
        assert "core-sw-01" in str(device)
        assert "FCW2345A001" in str(device)

    def test_device_log(self, device, deployment):
        phase = deployment.phases.get(phase_number=1)
        log = DeviceLog.objects.create(
            device=device,
            phase=phase,
            level=LogLevel.INFO,
            message="Discovery complete",
        )
        assert log.pk is not None
        assert device.logs.count() == 1
        assert "core-sw-01" in str(log)


# ---------------------------------------------------------------------------
# FactoryReset tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFactoryReset:
    def test_create_reset(self, deployment, operator):
        reset = FactoryReset.objects.create(
            deployment=deployment,
            operator=operator,
            sanitisation_method="write-erase",
        )
        for num, name in RESET_PHASE_NAMES.items():
            ResetPhase.objects.create(reset=reset, phase_number=num, phase_name=name)

        assert reset.pk is not None
        assert reset.status == ResetStatus.RUNNING
        assert reset.phases.count() == 6

    def test_certificate(self, deployment, operator, device):
        reset = FactoryReset.objects.create(deployment=deployment, operator=operator)
        cert = DeviceResetCertificate.objects.create(
            reset=reset,
            device=device,
            serial_number=device.serial_number,
            sanitisation_method="write-erase",
            verified=True,
            operator=operator,
        )
        assert cert.verified is True
        assert "verified" in str(cert)

    def test_duration_none_when_incomplete(self, deployment):
        reset = FactoryReset.objects.create(deployment=deployment)
        assert reset.duration_seconds is None


# ---------------------------------------------------------------------------
# Fleet model tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFleetModels:
    def test_site_record(self, deployment):
        site = SiteRecord.objects.create(
            site_name="Test Site",
            site_slug="test-site",
            last_deployment=deployment,
        )
        assert str(site) == "Test Site"

    def test_template_record(self):
        tmpl = TemplateRecord.objects.create(
            name="datacenter-v2",
            current_version="2.3.1",
        )
        assert "datacenter-v2" in str(tmpl)
        assert "2.3.1" in str(tmpl)

    def test_fleet_scan_compliance_pct(self):
        scan = FleetScan.objects.create(
            site_count=10,
            compliant_count=7,
            outdated_count=2,
            unknown_count=1,
        )
        assert scan.compliance_pct == 70

    def test_fleet_scan_compliance_pct_zero_sites(self):
        scan = FleetScan.objects.create(site_count=0)
        assert scan.compliance_pct == 0

    def test_site_compliance_record(self, deployment):
        site = SiteRecord.objects.create(site_name="S1", site_slug="s1")
        scan = FleetScan.objects.create(site_count=1, compliant_count=1)
        rec = SiteComplianceRecord.objects.create(
            scan=scan,
            site=site,
            template_name="datacenter-v2",
            deployed_version="2.3.0",
            current_version="2.3.1",
            status=SiteComplianceRecord.ComplianceStatus.OUTDATED,
        )
        assert "outdated" in str(rec)
