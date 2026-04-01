"""Meinberg vendor drivers — NTP appliance provisioning via REST API."""

from bare_metal_automation.drivers.meinberg.appliance import MeinbergApplianceDriver
from bare_metal_automation.drivers.registry import DriverRegistry

DriverRegistry.register_appliance("meinberg_", MeinbergApplianceDriver)

__all__ = ["MeinbergApplianceDriver"]
