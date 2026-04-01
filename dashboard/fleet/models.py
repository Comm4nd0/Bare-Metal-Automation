"""Fleet compliance models for template version tracking across sites."""

from django.db import models
from django.utils import timezone


class SiteRecord(models.Model):
    """A known site entry in the fleet inventory."""

    site_name = models.CharField(max_length=200)
    site_slug = models.SlugField(max_length=100, unique=True)
    location = models.CharField(max_length=200, blank=True)
    contact = models.EmailField(blank=True)

    # Link back to the most recent deployment for this site
    last_deployment = models.ForeignKey(
        "deploy.Deployment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fleet_site",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["site_name"]

    def __str__(self) -> str:
        return self.site_name

    @property
    def current_template(self) -> "TemplateRecord | None":
        if self.last_deployment:
            return TemplateRecord.objects.filter(
                name=self.last_deployment.template_name
            ).first()
        return None


class TemplateRecord(models.Model):
    """A versioned site template tracked for fleet compliance."""

    name = models.CharField(max_length=200)
    current_version = models.CharField(max_length=50)
    previous_versions = models.JSONField(default=list, blank=True)
    changelog = models.TextField(blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("name", "current_version")]

    def __str__(self) -> str:
        return f"{self.name} v{self.current_version}"


class FleetScan(models.Model):
    """Result of a full fleet compliance scan."""

    scanned_at = models.DateTimeField(auto_now_add=True)
    site_count = models.IntegerField(default=0)
    compliant_count = models.IntegerField(default=0)
    outdated_count = models.IntegerField(default=0)
    unknown_count = models.IntegerField(default=0)
    scan_report = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-scanned_at"]

    def __str__(self) -> str:
        return f"FleetScan {self.scanned_at:%Y-%m-%d %H:%M} — {self.compliant_count}/{self.site_count} compliant"

    @property
    def compliance_pct(self) -> int:
        if not self.site_count:
            return 0
        return int(self.compliant_count / self.site_count * 100)


class SiteComplianceRecord(models.Model):
    """Per-site result within a fleet scan."""

    class ComplianceStatus(models.TextChoices):
        COMPLIANT = "compliant", "Compliant"
        OUTDATED = "outdated", "Outdated"
        UNKNOWN = "unknown", "Unknown"
        NEVER_DEPLOYED = "never_deployed", "Never Deployed"

    scan = models.ForeignKey(FleetScan, on_delete=models.CASCADE, related_name="site_results")
    site = models.ForeignKey(SiteRecord, on_delete=models.CASCADE, related_name="compliance_records")
    template_name = models.CharField(max_length=200)
    deployed_version = models.CharField(max_length=50, blank=True)
    current_version = models.CharField(max_length=50, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ComplianceStatus.choices,
        default=ComplianceStatus.UNKNOWN,
        db_index=True,
    )
    deployed_at = models.DateTimeField(null=True, blank=True)
    drift_details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["site__site_name"]
        unique_together = [("scan", "site")]

    def __str__(self) -> str:
        return f"{self.site} — {self.status}"
