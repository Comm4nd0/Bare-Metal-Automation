"""Meinberg appliance driver — wraps MeinbergProvisioner and MeinbergResetter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bare_metal_automation.drivers.base import ApplianceDriver

if TYPE_CHECKING:
    from bare_metal_automation.models import DeploymentInventory, DiscoveredDevice

logger = logging.getLogger(__name__)


class MeinbergApplianceDriver(ApplianceDriver):
    """ApplianceDriver implementation for Meinberg LANTIME NTP appliances.

    Delegates to the existing ``MeinbergProvisioner`` and
    ``MeinbergResetter`` classes.
    """

    def __init__(
        self,
        inventory: DeploymentInventory | None = None,
        **kwargs: Any,
    ) -> None:
        self.inventory = inventory

    def provision_device(self, device: DiscoveredDevice) -> bool:
        from bare_metal_automation.provisioner.meinberg import MeinbergProvisioner

        provisioner = MeinbergProvisioner(inventory=self.inventory)
        return provisioner.provision_device(device)

    def reset_device(self, device: DiscoveredDevice) -> bool:
        from bare_metal_automation.rollback.meinberg import MeinbergResetter

        resetter = MeinbergResetter()
        return resetter.reset_device(device)
