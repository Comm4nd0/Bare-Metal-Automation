"""Django admin registration for the fleet app."""

from django.contrib import admin

from .models import FleetScan, SiteComplianceRecord, SiteRecord, TemplateRecord


class SiteComplianceRecordInline(admin.TabularInline):
    model = SiteComplianceRecord
    extra = 0
    readonly_fields = ["deployed_at"]
    fields = ["site", "template_name", "deployed_version", "current_version", "status", "deployed_at"]


@admin.register(SiteRecord)
class SiteRecordAdmin(admin.ModelAdmin):
    list_display = ["site_name", "site_slug", "location", "contact", "last_deployment", "updated_at"]
    search_fields = ["site_name", "site_slug", "location"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(TemplateRecord)
class TemplateRecordAdmin(admin.ModelAdmin):
    list_display = ["name", "current_version", "released_at", "updated_at"]
    search_fields = ["name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(FleetScan)
class FleetScanAdmin(admin.ModelAdmin):
    list_display = [
        "scanned_at",
        "site_count",
        "compliant_count",
        "outdated_count",
        "unknown_count",
        "compliance_pct",
    ]
    readonly_fields = ["scanned_at"]
    inlines = [SiteComplianceRecordInline]

    @admin.display(description="Compliance %")
    def compliance_pct(self, obj: FleetScan) -> str:
        return f"{obj.compliance_pct}%"


@admin.register(SiteComplianceRecord)
class SiteComplianceRecordAdmin(admin.ModelAdmin):
    list_display = ["site", "scan", "template_name", "deployed_version", "current_version", "status"]
    list_filter = ["status", "template_name"]
    search_fields = ["site__site_name", "template_name"]
