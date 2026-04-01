"""HPE vendor drivers — server provisioning via iLO 5 / Redfish API."""

from bare_metal_automation.drivers.hpe.server import HPEServerDriver
from bare_metal_automation.drivers.registry import DriverRegistry

DriverRegistry.register_server("hpe_", HPEServerDriver)

__all__ = ["HPEServerDriver"]
