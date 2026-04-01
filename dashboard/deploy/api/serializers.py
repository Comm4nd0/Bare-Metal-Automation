"""DRF serializers for the deploy app."""

from rest_framework import serializers

from deploy.models import (
    Deployment,
    DeploymentDevice,
    DeploymentPhase,
    DeviceLog,
    DeviceResetCertificate,
    FactoryReset,
    ResetPhase,
)


class DeploymentPhaseSerializer(serializers.ModelSerializer):
    traffic_light = serializers.ReadOnlyField()

    class Meta:
        model = DeploymentPhase
        fields = [
            "id",
            "phase_number",
            "phase_name",
            "status",
            "traffic_light",
            "started_at",
            "completed_at",
            "duration_seconds",
            "warning_count",
            "error_message",
        ]


class DeploymentDeviceSerializer(serializers.ModelSerializer):
    status_colour = serializers.ReadOnlyField()
    current_phase_name = serializers.SerializerMethodField()

    class Meta:
        model = DeploymentDevice
        fields = [
            "id",
            "serial_number",
            "hostname",
            "role",
            "platform",
            "status",
            "status_colour",
            "discovered_ip",
            "discovered_at",
            "configured_at",
            "provisioned_at",
            "verified_at",
            "current_phase",
            "current_phase_name",
            "error_message",
        ]

    def get_current_phase_name(self, obj: DeploymentDevice) -> str | None:
        if obj.current_phase:
            return obj.current_phase.phase_name
        return None


class DeploymentListSerializer(serializers.ModelSerializer):
    progress_pct = serializers.ReadOnlyField()
    current_phase_name = serializers.SerializerMethodField()
    device_count = serializers.SerializerMethodField()

    class Meta:
        model = Deployment
        fields = [
            "id",
            "site_name",
            "site_slug",
            "template_name",
            "template_version",
            "status",
            "progress_pct",
            "current_phase_name",
            "device_count",
            "ingested_at",
            "started_at",
            "completed_at",
            "operator",
        ]

    def get_current_phase_name(self, obj: Deployment) -> str | None:
        phase = obj.current_phase
        return phase.phase_name if phase else None

    def get_device_count(self, obj: Deployment) -> int:
        return obj.devices.count()


class DeploymentDetailSerializer(serializers.ModelSerializer):
    phases = DeploymentPhaseSerializer(many=True, read_only=True)
    devices = DeploymentDeviceSerializer(many=True, read_only=True)
    progress_pct = serializers.ReadOnlyField()
    duration_seconds = serializers.ReadOnlyField()

    class Meta:
        model = Deployment
        fields = [
            "id",
            "site_name",
            "site_slug",
            "template_name",
            "template_version",
            "bundle_path",
            "manifest_hash",
            "status",
            "progress_pct",
            "duration_seconds",
            "ingested_at",
            "started_at",
            "completed_at",
            "operator",
            "phases",
            "devices",
        ]


class DeviceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceLog
        fields = ["id", "device", "phase", "timestamp", "level", "message"]


class ResetPhaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResetPhase
        fields = [
            "id",
            "phase_number",
            "phase_name",
            "status",
            "started_at",
            "completed_at",
            "devices_reset",
            "devices_total",
            "log_output",
        ]


class DeviceResetCertificateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceResetCertificate
        fields = [
            "id",
            "serial_number",
            "sanitisation_method",
            "verified",
            "timestamp",
            "operator",
        ]


class FactoryResetSerializer(serializers.ModelSerializer):
    phases = ResetPhaseSerializer(many=True, read_only=True)
    certificates = DeviceResetCertificateSerializer(many=True, read_only=True)
    duration_seconds = serializers.ReadOnlyField()

    class Meta:
        model = FactoryReset
        fields = [
            "id",
            "deployment",
            "status",
            "sanitisation_method",
            "started_at",
            "completed_at",
            "duration_seconds",
            "operator",
            "report",
            "phases",
            "certificates",
        ]
