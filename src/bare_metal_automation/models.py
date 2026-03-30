"""Core data models for Bare Metal Automation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DevicePlatform(str, Enum):
    CISCO_IOS = "cisco_ios"
    CISCO_IOSXE = "cisco_iosxe"
    CISCO_ASA = "cisco_asa"
    CISCO_FTD = "cisco_ftd"
    HPE_DL325_GEN10 = "hpe_dl325_gen10"
    HPE_DL360_GEN10 = "hpe_dl360_gen10"
    HPE_DL380_GEN10 = "hpe_dl380_gen10"
    MEINBERG_LANTIME = "meinberg_lantime"


class DeviceRole(str, Enum):
    CORE_SWITCH = "core-switch"
    ACCESS_SWITCH = "access-switch"
    DISTRIBUTION_SWITCH = "distribution-switch"
    BORDER_ROUTER = "border-router"
    PERIMETER_FIREWALL = "perimeter-firewall"
    COMPUTE_NODE = "compute-node"
    MANAGEMENT_SERVER = "management-server"
    NTP_SERVER = "ntp-server"


class DeviceState(str, Enum):
    UNKNOWN = "unknown"
    DISCOVERED = "discovered"
    IDENTIFIED = "identified"
    VALIDATED = "validated"
    FIRMWARE_UPGRADING = "firmware_upgrading"
    FIRMWARE_UPGRADED = "firmware_upgraded"
    CONFIGURING = "configuring"
    CONFIGURED = "configured"
    BIOS_CONFIGURING = "bios_configuring"
    BIOS_CONFIGURED = "bios_configured"
    RAID_CONFIGURING = "raid_configuring"
    RAID_CONFIGURED = "raid_configured"
    SPP_INSTALLING = "spp_installing"
    SPP_INSTALLED = "spp_installed"
    OS_INSTALLING = "os_installing"
    OS_INSTALLED = "os_installed"
    OS_CONFIGURING = "os_configuring"
    OS_CONFIGURED = "os_configured"
    ILO_CONFIGURING = "ilo_configuring"
    ILO_CONFIGURED = "ilo_configured"
    PROVISIONING = "provisioning"
    PROVISIONED = "provisioned"
    RESETTING = "resetting"
    FACTORY_RESET = "factory_reset"
    POWERED_OFF = "powered_off"
    FAILED = "failed"


class DeploymentPhase(str, Enum):
    PRE_FLIGHT = "pre_flight"
    DISCOVERY = "discovery"
    TOPOLOGY = "topology"
    CABLING_VALIDATION = "cabling_validation"
    FIRMWARE_UPGRADE = "firmware_upgrade"
    HEAVY_TRANSFERS = "heavy_transfers"
    NETWORK_CONFIG = "network_config"
    LAPTOP_PIVOT = "laptop_pivot"
    SERVER_PROVISION = "server_provision"
    NTP_PROVISION = "ntp_provision"
    POST_INSTALL = "post_install"
    FINAL_VALIDATION = "final_validation"
    COMPLETE = "complete"
    FAILED = "failed"


class RollbackPhase(str, Enum):
    """Phases for rolling back a deployment to factory state."""

    ROLLBACK_PRE_FLIGHT = "rollback_pre_flight"
    ROLLBACK_NTP_RESET = "rollback_ntp_reset"
    ROLLBACK_SERVER_RESET = "rollback_server_reset"
    ROLLBACK_LAPTOP_PIVOT = "rollback_laptop_pivot"
    ROLLBACK_NETWORK_RESET = "rollback_network_reset"
    ROLLBACK_FINAL_CHECK = "rollback_final_check"
    ROLLBACK_COMPLETE = "rollback_complete"
    ROLLBACK_FAILED = "rollback_failed"


@dataclass
class CDPNeighbour:
    """A single CDP neighbour entry as seen from a device."""

    local_port: str  # e.g. "GigabitEthernet1/0/48"
    remote_device_id: str  # e.g. "Switch-B.domain.local"
    remote_port: str  # e.g. "GigabitEthernet1/0/1"
    remote_platform: str  # e.g. "WS-C3850-48T"
    remote_ip: str  # Management IP reported by CDP
    remote_serial: str | None = None  # If extractable


@dataclass
class IntendedConnection:
    """An expected connection parsed from a config template."""

    local_port: str
    remote_hostname: str
    remote_port: str | None = None
    description: str = ""
    is_flexible: bool = False  # True for server access ports that can adapt


@dataclass
class CablingResult:
    """Result of comparing a single connection: actual vs intended."""

    local_port: str
    status: str  # "correct", "wrong_device", "wrong_port", "missing", "unexpected", "adaptable"
    actual_remote: str | None = None
    actual_remote_port: str | None = None
    intended_remote: str | None = None
    intended_remote_port: str | None = None
    message: str = ""


@dataclass
class DiscoveredDevice:
    """A device found on the bootstrap network."""

    ip: str
    mac: str | None = None
    serial: str | None = None
    platform: str | None = None
    hostname: str | None = None
    cdp_neighbours: list[CDPNeighbour] = field(default_factory=list)
    state: DeviceState = DeviceState.UNKNOWN

    # Set after matching to inventory
    role: DeviceRole | None = None
    intended_hostname: str | None = None
    template_path: str | None = None
    device_platform: DevicePlatform | None = None
    bfs_depth: int | None = None
    config_order: int | None = None


@dataclass
class DeploymentInventory:
    """The intended deployment loaded from inventory YAML."""

    name: str
    bootstrap_subnet: str
    laptop_ip: str
    management_vlan: int
    devices: dict[str, dict[str, Any]]  # serial -> device spec

    def get_device_spec(self, serial: str) -> dict[str, Any] | None:
        return self.devices.get(serial)

    @property
    def expected_serials(self) -> set[str]:
        return set(self.devices.keys())


@dataclass
class DeploymentState:
    """Overall state of a deployment run."""

    phase: DeploymentPhase = DeploymentPhase.PRE_FLIGHT
    discovered_devices: dict[str, DiscoveredDevice] = field(default_factory=dict)  # IP -> device
    topology_order: list[str] = field(default_factory=list)  # Serials in config order
    cabling_results: dict[str, list[CablingResult]] = field(default_factory=dict)  # Serial -> results
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def matched_devices(self) -> dict[str, DiscoveredDevice]:
        return {ip: d for ip, d in self.discovered_devices.items() if d.role is not None}

    @property
    def unmatched_devices(self) -> dict[str, DiscoveredDevice]:
        return {ip: d for ip, d in self.discovered_devices.items() if d.role is None}

    @property
    def has_blocking_errors(self) -> bool:
        return len(self.errors) > 0
