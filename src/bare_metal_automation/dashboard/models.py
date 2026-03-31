"""Django models for Bare Metal Automation deployment status tracking."""

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
        max_length=30,
        choices=[
            ("cisco_ios", "Cisco IOS"),
            ("cisco_iosxe", "Cisco IOS-XE"),
            ("cisco_asa", "Cisco ASA"),
            ("cisco_ftd", "Cisco FTD"),
            ("hpe_dl325_gen10", "HPE DL325 Gen10"),
            ("hpe_dl360_gen10", "HPE DL360 Gen10"),
            ("hpe_dl380_gen10", "HPE DL380 Gen10"),
            ("meinberg_lantime", "Meinberg LANTIME"),
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
            ("ntp-server", "NTP Server"),
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
            ("firmware_upgrading", "Firmware Upgrading"),
            ("firmware_upgraded", "Firmware Upgraded"),
            ("configuring", "Configuring"),
            ("configured", "Configured"),
            ("bios_configuring", "BIOS Configuring"),
            ("bios_configured", "BIOS Configured"),
            ("raid_configuring", "RAID Configuring"),
            ("raid_configured", "RAID Configured"),
            ("spp_installing", "SPP Installing"),
            ("spp_installed", "SPP Installed"),
            ("os_installing", "OS Installing"),
            ("os_installed", "OS Installed"),
            ("os_configuring", "OS Configuring"),
            ("os_configured", "OS Configured"),
            ("ilo_configuring", "iLO Configuring"),
            ("ilo_configured", "iLO Configured"),
            ("provisioning", "Provisioning"),
            ("provisioned", "Provisioned"),
            ("resetting", "Resetting"),
            ("reset_complete", "Reset Complete"),
            ("factory_reset", "Factory Reset"),
            ("powered_off", "Powered Off"),
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
            "firmware_upgrading": "warning",
            "firmware_upgraded": "primary",
            "configuring": "warning",
            "configured": "success",
            "bios_configuring": "warning",
            "bios_configured": "primary",
            "raid_configuring": "warning",
            "raid_configured": "primary",
            "spp_installing": "warning",
            "spp_installed": "primary",
            "os_installing": "warning",
            "os_installed": "primary",
            "os_configuring": "warning",
            "os_configured": "primary",
            "ilo_configuring": "warning",
            "ilo_configured": "primary",
            "provisioning": "warning",
            "provisioned": "success",
            "resetting": "warning",
            "reset_complete": "info",
            "factory_reset": "secondary",
            "powered_off": "dark",
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
