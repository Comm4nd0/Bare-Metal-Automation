"""Cisco network driver — wraps NetworkConfigurator, FirmwareConfigurator, and resetters."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bare_metal_automation.drivers.base import NetworkDriver

if TYPE_CHECKING:
    from bare_metal_automation.models import DeploymentInventory, DiscoveredDevice

logger = logging.getLogger(__name__)


class CiscoNetworkDriver(NetworkDriver):
    """NetworkDriver implementation for Cisco IOS/IOS-XE/ASA/FTD devices.

    Delegates to the existing ``NetworkConfigurator``, ``FirmwareConfigurator``,
    and ``NetworkResetter`` classes, keeping all Cisco-specific logic intact.
    """

    def __init__(
        self,
        inventory: DeploymentInventory | None = None,
        ssh_timeout: int = 30,
        **kwargs: Any,
    ) -> None:
        self.inventory = inventory
        self.ssh_timeout = ssh_timeout

    def configure_device(self, device: DiscoveredDevice) -> bool:
        from bare_metal_automation.configurator.network import NetworkConfigurator

        configurator = NetworkConfigurator(
            inventory=self.inventory,
            ssh_timeout=self.ssh_timeout,
        )
        return configurator.configure_device(device)

    def upgrade_firmware(self, device: DiscoveredDevice) -> bool:
        from bare_metal_automation.configurator.firmware import FirmwareConfigurator

        configurator = FirmwareConfigurator(
            inventory=self.inventory,
            ssh_timeout=self.ssh_timeout,
        )
        return configurator.upgrade_device(device)

    def reset_device(self, device: DiscoveredDevice) -> bool:
        from bare_metal_automation.rollback.network import NetworkResetter

        resetter = NetworkResetter(ssh_timeout=self.ssh_timeout)
        return resetter.reset_device(device)

    def verify_factory_state(self, device: DiscoveredDevice) -> bool:
        from bare_metal_automation.rollback.network import NetworkResetter

        resetter = NetworkResetter(ssh_timeout=self.ssh_timeout)
        return resetter.verify_factory_state(device)
