"""Cisco vendor drivers — network automation via SSH/Netmiko."""

from bare_metal_automation.drivers.cisco.discovery import CiscoCDPDiscovery
from bare_metal_automation.drivers.cisco.network import CiscoNetworkDriver
from bare_metal_automation.drivers.registry import DriverRegistry

DriverRegistry.register_network("cisco_", CiscoNetworkDriver)
DriverRegistry.register_discovery("cisco_", CiscoCDPDiscovery)

__all__ = ["CiscoCDPDiscovery", "CiscoNetworkDriver"]
