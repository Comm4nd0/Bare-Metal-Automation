"""Django models for Bare Metal Automation deployment status tracking.

Consolidated from two separate apps (Sprint 5):
  - src/bare_metal_automation/dashboard/  (primary — this file)
  - dashboard/  at repo root (legacy — see dashboard/README.md)

Unique models from the legacy app (DeploymentPhase, FactoryReset, ResetPhase,
DeviceResetCertificate, and fleet models) have been merged here.  The legacy
app's Deployment/Device counterparts map to the primary Deployment/Device.
"""

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Deployment(models.Model):
    """A deployment run tracking overall state."""

    name = models.CharField(max_length=200)

    # Site / template metadata (merged from legacy deploy app)
    site_name = models.CharField(max_length=200, blank=True)
    site_slug = models.SlugField(max_length=100, blank=True)
    template_name = models.CharField(max_length=200, blank=True)
    template_version = models.CharField(max_length=50, blank=True)
    bundle_path = models.CharField(max_length=500, blank=True)
    manifest_hash = models.CharField(max_length=64, blank=True, help_text="SHA-256 of manifest.yaml")

    # JSON configuration used by Phase 7/8 (vCenter + NSX parameters)
    site_config = models.JSONField(default=dict, blank=True)

    # Operator who triggered the deployment
    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deployments",
    )

    phase = models.CharField(
        max_length=30,
        choices=[
            ("pre_flight", "Pre-Flight"),
            ("discovery", "Discovery"),
            ("topology", "Topology"),
            ("cabling_validation", "Cabling Validation"),
            ("firmware_upgrade", "Firmware Upgrade"),
            ("heavy_transfers", "Heavy Transfers"),
            ("network_config", "Network Configuration"),
            ("laptop_pivot", "Laptop Pivot"),
            ("server_provision", "Server Provisioning"),
            ("ntp_provision", "NTP Provisioning"),
            ("post_install", "Post-Install"),
            ("final_validation", "Final Validation"),
            ("factory_reset", "Factory Reset"),
            ("complete", "Complete"),
            ("failed", "Failed"),
            ("stopped", "Stopped"),
            # Rollback phases
            ("rollback_pre_flight", "Rollback Pre-Flight"),
            ("rollback_ntp_reset", "Rollback NTP Reset"),
            ("rollback_server_reset", "Rollback Server Reset"),
            ("rollback_laptop_pivot", "Rollback Laptop Pivot"),
            ("rollback_network_reset", "Rollback Network Reset"),
            ("rollback_final_check", "Rollback Final Check"),
            ("rollback_complete", "Rollback Complete"),
            ("rollback_failed", "Rollback Failed"),
        ],
        default="pre_flight",
    )
    bootstrap_subnet = models.CharField(max_length=50, blank=True)
    laptop_ip = models.CharField(max_length=50, blank=True)
    management_vlan = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.name} ({self.phase})"

    @property
    def phase_display(self):
        return self.get_phase_display()

    @property
    def device_summary(self):
        total = self.devices.count()
        configured = self.devices.filter(state="configured").count()
        failed = self.devices.filter(state="failed").count()
        return {"total": total, "configured": configured, "failed": failed}

    @property
    def is_rollback(self):
        """Return True if this deployment is in a rollback phase."""
        return self.phase.startswith("rollback_")

    @property
    def phase_progress(self):
        """Return a 0-100 progress percentage based on current phase."""
        if self.phase in ("failed", "stopped", "rollback_failed"):
            return 0

        if self.phase.startswith("rollback_"):
            rollback_order = [
                "rollback_pre_flight", "rollback_ntp_reset",
                "rollback_server_reset", "rollback_laptop_pivot",
                "rollback_network_reset", "rollback_final_check",
                "rollback_complete",
            ]
            try:
                idx = rollback_order.index(self.phase)
                return int((idx / (len(rollback_order) - 1)) * 100)
            except ValueError:
                return 0

        phase_order = [
            "pre_flight", "discovery", "topology", "cabling_validation",
            "firmware_upgrade", "heavy_transfers", "network_config",
            "laptop_pivot", "server_provision", "ntp_provision",
            "post_install", "final_validation", "complete",
        ]
        try:
            idx = phase_order.index(self.phase)
            return int((idx / (len(phase_order) - 1)) * 100)
        except ValueError:
            return 0


class Device(models.Model):
    """A discovered device in a deployment."""

    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name="devices")
    ip = models.GenericIPAddressField()
    mac = models.CharField(max_length=17, blank=True)
    serial = models.CharField(max_length=100, blank=True)
    platform = models.CharField(
        max_length=60,
        blank=True,
        help_text="Platform identifier (e.g. cisco_iosxe, hpe_dl360_gen10). "
                  "New vendors are auto-discovered via the driver registry.",
    )
    hostname = models.CharField(max_length=200, blank=True)
    intended_hostname = models.CharField(max_length=200, blank=True)
    role = models.CharField(
        max_length=30,
        choices=[
            ("core-switch", "Core Switch"),
            ("access-switch", "Access Switch"),
            ("distribution-switch", "Distribution Switch"),
            ("border-router", "Border Router"),
            ("perimeter-firewall", "Perimeter Firewall"),
            ("compute-node", "Compute Node"),
            ("management-server", "Management Server"),
            ("ntp-server", "NTP Server"),
        ],
        blank=True,
    )
    state = models.CharField(
        max_length=30,
        choices=[
            ("unknown", "Unknown"),
            ("discovered", "Discovered"),
            ("identified", "Identified"),
            ("validated", "Validated"),
            ("firmware_upgrading", "Firmware Upgrading"),
            ("firmware_upgraded", "Firmware Upgraded"),
            ("configuring", "Configuring"),
            ("configured", "Configured"),
            ("bios_configuring", "BIOS Configuring"),
            ("bios_configured", "BIOS Configured"),
            ("raid_configuring", "RAID Configuring"),
            ("raid_configured", "RAID Configured"),
            ("driver_pack_installing", "Driver Pack Installing"),
            ("driver_pack_installed", "Driver Pack Installed"),
            ("os_installing", "OS Installing"),
            ("os_installed", "OS Installed"),
            ("os_configuring", "OS Configuring"),
            ("os_configured", "OS Configured"),
            ("bmc_configuring", "BMC Configuring"),
            ("bmc_configured", "BMC Configured"),
            ("provisioning", "Provisioning"),
            ("provisioned", "Provisioned"),
            ("resetting", "Resetting"),
            ("reset_complete", "Reset Complete"),
            ("factory_reset", "Factory Reset"),
            ("powered_off", "Powered Off"),
            ("failed", "Failed"),
            ("vendor_step", "Vendor Step"),
        ],
        default="unknown",
    )
    bfs_depth = models.IntegerField(null=True, blank=True)
    config_order = models.IntegerField(null=True, blank=True)
    management_ip = models.CharField(max_length=50, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["config_order", "intended_hostname"]

    def __str__(self):
        return self.intended_hostname or self.hostname or self.ip

    @property
    def state_css_class(self):
        return {
            "unknown": "secondary",
            "discovered": "info",
            "identified": "info",
            "validated": "primary",
            "firmware_upgrading": "warning",
            "firmware_upgraded": "primary",
            "configuring": "warning",
            "configured": "success",
            "bios_configuring": "warning",
            "bios_configured": "primary",
            "raid_configuring": "warning",
            "raid_configured": "primary",
            "driver_pack_installing": "warning",
            "driver_pack_installed": "primary",
            "os_installing": "warning",
            "os_installed": "primary",
            "os_configuring": "warning",
            "os_configured": "primary",
            "bmc_configuring": "warning",
            "bmc_configured": "primary",
            "provisioning": "warning",
            "provisioned": "success",
            "resetting": "warning",
            "reset_complete": "info",
            "factory_reset": "secondary",
            "powered_off": "dark",
            "failed": "danger",
            "vendor_step": "warning",
        }.get(self.state, "secondary")

    @property
    def role_icon(self):
        return {
            "core-switch": "diagram-3",
            "access-switch": "ethernet",
            "distribution-switch": "diagram-2",
            "border-router": "router",
            "perimeter-firewall": "shield-lock",
            "compute-node": "cpu",
            "management-server": "server",
            "ntp-server": "clock",
        }.get(self.role, "device-hdd")


class CablingResult(models.Model):
    """Result of a cabling validation check for a single port."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="cabling_results")
    local_port = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20,
        choices=[
            ("correct", "Correct"),
            ("wrong_device", "Wrong Device"),
            ("wrong_port", "Wrong Port"),
            ("missing", "Missing"),
            ("unexpected", "Unexpected"),
            ("adaptable", "Adaptable"),
        ],
    )
    actual_remote = models.CharField(max_length=200, blank=True)
    actual_remote_port = models.CharField(max_length=100, blank=True)
    intended_remote = models.CharField(max_length=200, blank=True)
    intended_remote_port = models.CharField(max_length=100, blank=True)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["local_port"]

    def __str__(self):
        return f"{self.local_port}: {self.status}"

    @property
    def status_css_class(self):
        return {
            "correct": "success",
            "wrong_device": "danger",
            "wrong_port": "warning",
            "missing": "danger",
            "unexpected": "warning",
            "adaptable": "info",
        }.get(self.status, "secondary")


class FirmwareImage(models.Model):
    """A firmware image tracked in the catalog."""

    platform = models.CharField(
        max_length=60,
        help_text="Platform identifier (e.g. cisco_ios, cisco_iosxe, hpe_ilo5).",
    )
    version = models.CharField(max_length=100)
    filename = models.CharField(max_length=255)
    md5 = models.CharField(max_length=64, blank=True)
    min_version = models.CharField(
        max_length=100,
        blank=True,
        help_text="Minimum current version required for safe upgrade.",
    )
    release_notes = models.TextField(blank=True)
    recommended = models.BooleanField(
        default=False,
        help_text="Whether this is the recommended version for its platform.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["platform", "-recommended", "-version"]
        unique_together = [("platform", "version")]

    def __str__(self):
        suffix = " (recommended)" if self.recommended else ""
        return f"{self.platform} {self.version}{suffix}"


class FirmwareTestRun(models.Model):
    """Record of a firmware upgrade test against a device."""

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name="firmware_tests",
        null=True,
        blank=True,
    )
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name="firmware_tests",
        null=True,
        blank=True,
    )
    firmware_image = models.ForeignKey(
        FirmwareImage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="test_runs",
    )
    device_hostname = models.CharField(max_length=200)
    device_ip = models.GenericIPAddressField()
    platform = models.CharField(max_length=60)
    previous_version = models.CharField(max_length=100, blank=True)
    target_version = models.CharField(max_length=100)
    final_version = models.CharField(max_length=100, blank=True)
    outcome = models.CharField(
        max_length=20,
        choices=[
            ("passed", "Passed"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
            ("rolled_back", "Rolled Back"),
            ("error", "Error"),
        ],
        default="skipped",
    )
    phase_reached = models.CharField(
        max_length=20,
        choices=[
            ("snapshot", "Snapshot"),
            ("pre_validation", "Pre-Validation"),
            ("upgrade", "Upgrade"),
            ("config_reapply", "Config Re-apply"),
            ("post_validation", "Post-Validation"),
            ("rollback", "Rollback"),
            ("complete", "Complete"),
        ],
        default="snapshot",
    )
    config_reapply_success = models.BooleanField(default=False)
    pre_validation_passed = models.BooleanField(null=True, blank=True)
    post_validation_passed = models.BooleanField(null=True, blank=True)
    duration_seconds = models.FloatField(default=0.0)
    error_message = models.TextField(blank=True)
    findings = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return (
            f"{self.device_hostname}: "
            f"{self.previous_version} -> {self.target_version} "
            f"({self.outcome})"
        )

    @property
    def outcome_css_class(self):
        return {
            "passed": "success",
            "failed": "danger",
            "skipped": "secondary",
            "rolled_back": "warning",
            "error": "danger",
        }.get(self.outcome, "secondary")


class FirmwareComplianceSnapshot(models.Model):
    """Point-in-time firmware compliance snapshot for the fleet."""

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name="compliance_snapshots",
        null=True,
        blank=True,
    )
    total_devices = models.IntegerField(default=0)
    compliant_count = models.IntegerField(default=0)
    upgrade_available_count = models.IntegerField(default=0)
    blocked_count = models.IntegerField(default=0)
    unreachable_count = models.IntegerField(default=0)
    compliance_percentage = models.FloatField(default=0.0)
    details = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Compliance {self.compliance_percentage:.1f}% "
            f"({self.created_at:%Y-%m-%d %H:%M})"
        )


class DeploymentLog(models.Model):
    """Log entries for a deployment."""

    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name="logs")
    level = models.CharField(
        max_length=10,
        choices=[
            ("INFO", "Info"),
            ("WARNING", "Warning"),
            ("ERROR", "Error"),
        ],
        default="INFO",
    )
    phase = models.CharField(max_length=30, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.level}] {self.message[:80]}"

    @property
    def level_css_class(self):
        return {
            "INFO": "info",
            "WARNING": "warning",
            "ERROR": "danger",
        }.get(self.level, "secondary")


# ── Deployment phase tracking (merged from legacy deploy app) ──────────────

class PhaseStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    WARNING = "warning", "Warning"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class DeploymentPhase(models.Model):
    """Structured tracking record for a single numbered deployment phase."""

    deployment = models.ForeignKey(
        Deployment, on_delete=models.CASCADE, related_name="phases"
    )
    phase_number = models.IntegerField()
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


# ── Factory reset models (merged from legacy deploy app) ──────────────────

class ResetStatus(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    ABORTED = "aborted", "Aborted"


class FactoryReset(models.Model):
    """A factory-reset run against a deployment's devices."""

    deployment = models.ForeignKey(
        Deployment, on_delete=models.CASCADE, related_name="factory_resets"
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
    """One of the factory-reset phases (mirrors the 6-phase orchestrator)."""

    RESET_PHASE_NAMES = {
        1: "VM Teardown",
        2: "NSX Teardown",
        3: "vCenter Teardown",
        4: "Server Wipe",
        5: "Network Reset",
        6: "Validation",
    }

    reset = models.ForeignKey(FactoryReset, on_delete=models.CASCADE, related_name="phases")
    phase_number = models.IntegerField()
    phase_name = models.CharField(max_length=100)

    status = models.CharField(
        max_length=20, choices=PhaseStatus.choices, default=PhaseStatus.PENDING
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

    reset = models.ForeignKey(
        FactoryReset, on_delete=models.CASCADE, related_name="certificates"
    )
    device = models.ForeignKey(
        Device,
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
    certificate_id = models.CharField(max_length=36, blank=True, help_text="UUID from SanitisationCertificate")
    checksum = models.CharField(max_length=64, blank=True, help_text="SHA-256 of certificate payload")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        status = "verified" if self.verified else "unverified"
        return f"Certificate {self.serial_number} ({status})"


# ── Fleet compliance models (merged from legacy fleet app) ────────────────

class SiteRecord(models.Model):
    """A known site in the fleet inventory."""

    site_name = models.CharField(max_length=200)
    site_slug = models.SlugField(max_length=100, unique=True)
    location = models.CharField(max_length=200, blank=True)
    contact = models.EmailField(blank=True)

    last_deployment = models.ForeignKey(
        Deployment,
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
        return (
            f"FleetScan {self.scanned_at:%Y-%m-%d %H:%M} "
            f"— {self.compliant_count}/{self.site_count} compliant"
        )

    @property
    def compliance_pct(self) -> int:
        if not self.site_count:
            return 0
        return int(self.compliant_count / self.site_count * 100)


class SiteComplianceRecord(models.Model):
    """Per-site compliance result within a fleet scan."""

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
