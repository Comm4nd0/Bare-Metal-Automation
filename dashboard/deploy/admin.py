"""Django admin registration for the deploy app."""

from django.contrib import admin

from .models import (
    Deployment,
    DeploymentDevice,
    DeploymentPhase,
    DeviceLog,
    DeviceResetCertificate,
    FactoryReset,
    ResetPhase,
)


class DeploymentPhaseInline(admin.TabularInline):
    model = DeploymentPhase
    extra = 0
    readonly_fields = ["started_at", "completed_at", "duration_seconds"]
    fields = [
        "phase_number",
        "phase_name",
        "status",
        "started_at",
        "completed_at",
        "duration_seconds",
        "warning_count",
        "error_message",
    ]


class DeploymentDeviceInline(admin.TabularInline):
    model = DeploymentDevice
    extra = 0
    readonly_fields = ["discovered_at", "configured_at", "provisioned_at", "verified_at"]
    fields = [
        "serial_number",
        "hostname",
        "role",
        "platform",
        "status",
        "discovered_ip",
        "error_message",
    ]


@admin.register(Deployment)
class DeploymentAdmin(admin.ModelAdmin):
    list_display = [
        "site_name",
        "site_slug",
        "template_name",
        "template_version",
        "status",
        "ingested_at",
        "started_at",
        "completed_at",
        "operator",
    ]
    list_filter = ["status", "template_name"]
    search_fields = ["site_name", "site_slug", "template_name"]
    readonly_fields = ["ingested_at", "started_at", "completed_at", "manifest_hash"]
    inlines = [DeploymentPhaseInline, DeploymentDeviceInline]


@admin.register(DeploymentPhase)
class DeploymentPhaseAdmin(admin.ModelAdmin):
    list_display = [
        "deployment",
        "phase_number",
        "phase_name",
        "status",
        "started_at",
        "duration_seconds",
        "warning_count",
    ]
    list_filter = ["status", "phase_number"]
    search_fields = ["deployment__site_name", "phase_name"]


@admin.register(DeploymentDevice)
class DeploymentDeviceAdmin(admin.ModelAdmin):
    list_display = [
        "hostname",
        "serial_number",
        "role",
        "platform",
        "status",
        "discovered_ip",
        "deployment",
    ]
    list_filter = ["status", "role", "platform"]
    search_fields = ["hostname", "serial_number"]


@admin.register(DeviceLog)
class DeviceLogAdmin(admin.ModelAdmin):
    list_display = ["timestamp", "level", "device", "phase", "message"]
    list_filter = ["level", "phase"]
    search_fields = ["message", "device__hostname"]
    readonly_fields = ["timestamp"]


class ResetPhaseInline(admin.TabularInline):
    model = ResetPhase
    extra = 0
    readonly_fields = ["started_at", "completed_at"]


class DeviceResetCertificateInline(admin.TabularInline):
    model = DeviceResetCertificate
    extra = 0
    readonly_fields = ["timestamp"]


@admin.register(FactoryReset)
class FactoryResetAdmin(admin.ModelAdmin):
    list_display = [
        "pk",
        "deployment",
        "status",
        "sanitisation_method",
        "started_at",
        "completed_at",
        "operator",
    ]
    list_filter = ["status"]
    readonly_fields = ["started_at", "completed_at"]
    inlines = [ResetPhaseInline, DeviceResetCertificateInline]


@admin.register(DeviceResetCertificate)
class DeviceResetCertificateAdmin(admin.ModelAdmin):
    list_display = ["serial_number", "reset", "sanitisation_method", "verified", "timestamp", "operator"]
    list_filter = ["verified", "sanitisation_method"]
    search_fields = ["serial_number"]
    readonly_fields = ["timestamp"]
