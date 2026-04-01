"""Vendor driver framework for Bare Metal Automation.

Import this package to auto-register all built-in vendor drivers.
"""

from bare_metal_automation.drivers.base import (
    ApplianceDriver,
    DiscoveryDriver,
    NetworkDriver,
    ServerDriver,
)
from bare_metal_automation.drivers.registry import DriverRegistry

__all__ = [
    "ApplianceDriver",
    "DiscoveryDriver",
    "DriverRegistry",
    "NetworkDriver",
    "ServerDriver",
    "load_builtin_drivers",
]


def load_builtin_drivers() -> None:
    """Register all built-in vendor drivers.

    Safe to call multiple times — re-registration is a no-op in the registry.
    This explicitly registers each driver rather than relying solely on
    import side-effects, so it works even after ``DriverRegistry.clear()``.
    """
    from bare_metal_automation.drivers.cisco.discovery import CiscoCDPDiscovery
    from bare_metal_automation.drivers.cisco.network import CiscoNetworkDriver
    from bare_metal_automation.drivers.hpe.server import HPEServerDriver
    from bare_metal_automation.drivers.meinberg.appliance import MeinbergApplianceDriver

    DriverRegistry.register_network("cisco_", CiscoNetworkDriver)
    DriverRegistry.register_discovery("cisco_", CiscoCDPDiscovery)
    DriverRegistry.register_server("hpe_", HPEServerDriver)
    DriverRegistry.register_appliance("meinberg_", MeinbergApplianceDriver)
