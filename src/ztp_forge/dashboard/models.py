"""Django models for ZTP-Forge deployment status tracking."""

from django.db import models


class Deployment(models.Model):
    """A deployment run tracking overall state."""

    name = models.CharField(max_length=200)
    phase = models.CharField(
        max_length=30,
        choices=[
            ("pre_flight", "Pre-Flight"),
            ("discovery", "Discovery"),
            ("topology", "Topology"),
            ("cabling_validation", "Cabling Validation"),
            ("heavy_transfers", "Heavy Transfers"),
            ("network_config", "Network Configuration"),
            ("laptop_pivot", "Laptop Pivot"),
            ("server_provision", "Server Provisioning"),
            ("post_install", "Post-Install"),
            ("final_validation", "Final Validation"),
            ("complete", "Complete"),
            ("failed", "Failed"),
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
    def phase_progress(self):
        """Return a 0-100 progress percentage based on current phase."""
        phase_order = [
            "pre_flight", "discovery", "topology", "cabling_validation",
            "heavy_transfers", "network_config", "laptop_pivot",
            "server_provision", "post_install", "final_validation", "complete",
        ]
        if self.phase == "failed":
            return 0
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
        max_length=30,
        choices=[
            ("cisco_ios", "Cisco IOS"),
            ("cisco_iosxe", "Cisco IOS-XE"),
            ("cisco_asa", "Cisco ASA"),
            ("cisco_ftd", "Cisco FTD"),
            ("hpe_dl325_gen10", "HPE DL325 Gen10"),
        ],
        blank=True,
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
        ],
        blank=True,
    )
    state = models.CharField(
        max_length=20,
        choices=[
            ("unknown", "Unknown"),
            ("discovered", "Discovered"),
            ("identified", "Identified"),
            ("validated", "Validated"),
            ("configuring", "Configuring"),
            ("configured", "Configured"),
            ("provisioning", "Provisioning"),
            ("provisioned", "Provisioned"),
            ("failed", "Failed"),
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
            "configuring": "warning",
            "configured": "success",
            "provisioning": "warning",
            "provisioned": "success",
            "failed": "danger",
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
