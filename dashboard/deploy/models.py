"""Django ORM models for Sprint 3 deployment tracking and factory reset."""

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------


class DeploymentStatus(models.TextChoices):
    INGESTED = "ingested", "Ingested"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    ABORTED = "aborted", "Aborted"


class PhaseStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    WARNING = "warning", "Warning"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class DeviceStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DISCOVERED = "discovered", "Discovered"
    CABLING_VALIDATED = "cabling_validated", "Cabling Validated"
    FIRMWARE_STAGED = "firmware_staged", "Firmware Staged"
    CONFIGURING = "configuring", "Configuring"
    CONFIGURED = "configured", "Configured"
    PROVISIONING = "provisioning", "Provisioning"
    PROVISIONED = "provisioned", "Provisioned"
    VERIFIED = "verified", "Verified"
    FAILED = "failed", "Failed"
    MISSING = "missing", "Missing"


class LogLevel(models.TextChoices):
    DEBUG = "DEBUG", "Debug"
    INFO = "INFO", "Info"
    WARN = "WARN", "Warning"
    ERROR = "ERROR", "Error"


class ResetStatus(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    ABORTED = "aborted", "Aborted"


# ---------------------------------------------------------------------------
# Phase names — the 11 deployment phases (0-10)
# ---------------------------------------------------------------------------

PHASE_NAMES = {
    0: "Pre-Flight",
    1: "Discovery",
    2: "Cabling Validation",
    3: "Firmware Upgrade",
    4: "Heavy Transfers",
    5: "Network Configuration",
    6: "Laptop Pivot",
    7: "Server Provisioning",
    8: "NTP Provisioning",
    9: "Post-Install",
    10: "Final Validation",
}

RESET_PHASE_NAMES = {
    1: "Pre-Flight",
    2: "Network Device Reset",
    3: "Server Reset",
    4: "NTP Reset",
    5: "Power Down",
    6: "Certificate Generation",
}


# ---------------------------------------------------------------------------
# Deployment models
# ---------------------------------------------------------------------------


class Deployment(models.Model):
    """Top-level record for a single site deployment run."""

    site_name = models.CharField(max_length=200)
    site_slug = models.SlugField(max_length=100)
    template_name = models.CharField(max_length=200)
    template_version = models.CharField(max_length=50)

    bundle_path = models.CharField(max_length=500, blank=True)
    manifest_hash = models.CharField(max_length=64, blank=True, help_text="SHA-256 of manifest.yaml")

    ingested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=DeploymentStatus.choices,
        default=DeploymentStatus.INGESTED,
        db_index=True,
    )

    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deployments",
    )

    class Meta:
        ordering = ["-ingested_at"]

    def __str__(self) -> str:
        return f"{self.site_name} — {self.template_version} [{self.status}]"

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def current_phase(self) -> "DeploymentPhase | None":
        return self.phases.filter(status=PhaseStatus.RUNNING).first()

    @property
    def progress_pct(self) -> int:
        """Percentage of phases completed (0-100)."""
        total = self.phases.count()
        if not total:
            return 0
        done = self.phases.filter(status__in=[PhaseStatus.COMPLETED, PhaseStatus.SKIPPED]).count()
        return int(done / total * 100)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def start(self) -> None:
        self.status = DeploymentStatus.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def complete(self) -> None:
        self.status = DeploymentStatus.COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])

    def fail(self) -> None:
        self.status = DeploymentStatus.FAILED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])


class DeploymentPhase(models.Model):
    """One of the 11 deployment phases for a single deployment."""

    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name="phases")
    phase_number = models.IntegerField()  # 0-10
    phase_name = models.CharField(max_length=100)

    status = models.CharField(
        max_length=20,
        choices=PhaseStatus.choices,
        default=PhaseStatus.PENDING,
        db_index=True,
    )

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    warning_count = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["phase_number"]
        unique_together = [("deployment", "phase_number")]

    def __str__(self) -> str:
        return f"Phase {self.phase_number}: {self.phase_name} [{self.status}]"

    # Traffic light colour for the UI
    @property
    def traffic_light(self) -> str:
        return {
            PhaseStatus.PENDING: "grey",
            PhaseStatus.RUNNING: "blue",
            PhaseStatus.COMPLETED: "green",
            PhaseStatus.WARNING: "amber",
            PhaseStatus.FAILED: "red",
            PhaseStatus.SKIPPED: "grey",
        }.get(self.status, "grey")

    def start(self) -> None:
        self.status = PhaseStatus.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def complete(self, warning_count: int = 0) -> None:
        now = timezone.now()
        self.status = PhaseStatus.WARNING if warning_count else PhaseStatus.COMPLETED
        self.completed_at = now
        self.warning_count = warning_count
        if self.started_at:
            self.duration_seconds = (now - self.started_at).total_seconds()
        self.save(update_fields=["status", "completed_at", "duration_seconds", "warning_count"])

    def fail(self, error_message: str = "") -> None:
        now = timezone.now()
        self.status = PhaseStatus.FAILED
        self.completed_at = now
        self.error_message = error_message
        if self.started_at:
            self.duration_seconds = (now - self.started_at).total_seconds()
        self.save(update_fields=["status", "completed_at", "duration_seconds", "error_message"])


class DeploymentDevice(models.Model):
    """Per-device state within a deployment."""

    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name="devices")
    serial_number = models.CharField(max_length=100)
    hostname = models.CharField(max_length=200)
    role = models.CharField(max_length=50)
    platform = models.CharField(max_length=50)

    # Paths to artefacts within the bundle
    config_path = models.CharField(max_length=500, blank=True)
    firmware_path = models.CharField(max_length=500, blank=True)
    os_media_path = models.CharField(max_length=500, blank=True)

    status = models.CharField(
        max_length=30,
        choices=DeviceStatus.choices,
        default=DeviceStatus.PENDING,
        db_index=True,
    )

    discovered_ip = models.GenericIPAddressField(null=True, blank=True)
    discovered_at = models.DateTimeField(null=True, blank=True)
    configured_at = models.DateTimeField(null=True, blank=True)
    provisioned_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    current_phase = models.ForeignKey(
        DeploymentPhase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_devices",
    )
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["hostname"]
        unique_together = [("deployment", "serial_number")]

    def __str__(self) -> str:
        return f"{self.hostname} ({self.serial_number})"

    @property
    def status_colour(self) -> str:
        return {
            DeviceStatus.PENDING: "grey",
            DeviceStatus.DISCOVERED: "blue",
            DeviceStatus.CABLING_VALIDATED: "teal",
            DeviceStatus.FIRMWARE_STAGED: "cyan",
            DeviceStatus.CONFIGURING: "blue",
            DeviceStatus.CONFIGURED: "green",
            DeviceStatus.PROVISIONING: "blue",
            DeviceStatus.PROVISIONED: "green",
            DeviceStatus.VERIFIED: "green",
            DeviceStatus.FAILED: "red",
            DeviceStatus.MISSING: "amber",
        }.get(self.status, "grey")


class DeviceLog(models.Model):
    """Timestamped log message attached to a device + phase."""

    device = models.ForeignKey(DeploymentDevice, on_delete=models.CASCADE, related_name="logs")
    phase = models.ForeignKey(
        DeploymentPhase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="device_logs",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    level = models.CharField(max_length=10, choices=LogLevel.choices, default=LogLevel.INFO)
    message = models.TextField()

    class Meta:
        ordering = ["timestamp"]

    def __str__(self) -> str:
        return f"[{self.level}] {self.device.hostname}: {self.message[:60]}"


# ---------------------------------------------------------------------------
# Factory Reset models
# ---------------------------------------------------------------------------


class FactoryReset(models.Model):
    """A factory-reset run against a deployment's devices."""

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name="factory_resets",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=ResetStatus.choices,
        default=ResetStatus.RUNNING,
        db_index=True,
    )

    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="factory_resets",
    )
    sanitisation_method = models.CharField(max_length=100, default="write-erase")
    report = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"FactoryReset #{self.pk} for {self.deployment} [{self.status}]"

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class ResetPhase(models.Model):
    """One of the 6 factory-reset phases."""

    reset = models.ForeignKey(FactoryReset, on_delete=models.CASCADE, related_name="phases")
    phase_number = models.IntegerField()  # 1-6
    phase_name = models.CharField(max_length=100)

    status = models.CharField(
        max_length=20,
        choices=PhaseStatus.choices,
        default=PhaseStatus.PENDING,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    devices_reset = models.IntegerField(default=0)
    devices_total = models.IntegerField(default=0)
    log_output = models.TextField(blank=True)

    class Meta:
        ordering = ["phase_number"]
        unique_together = [("reset", "phase_number")]

    def __str__(self) -> str:
        return f"ResetPhase {self.phase_number}: {self.phase_name} [{self.status}]"


class DeviceResetCertificate(models.Model):
    """Sanitisation certificate issued per device at end of factory reset."""

    reset = models.ForeignKey(FactoryReset, on_delete=models.CASCADE, related_name="certificates")
    device = models.ForeignKey(
        DeploymentDevice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reset_certificates",
    )
    serial_number = models.CharField(max_length=100)
    sanitisation_method = models.CharField(max_length=100)
    verified = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)
    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_certificates",
    )

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        status = "verified" if self.verified else "unverified"
        return f"Certificate {self.serial_number} ({status})"
