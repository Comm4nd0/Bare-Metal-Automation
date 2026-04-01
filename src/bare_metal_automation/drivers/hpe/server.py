"""HPE server driver — wraps HPEServerProvisioner and HPEServerResetter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bare_metal_automation.drivers.base import ServerDriver

if TYPE_CHECKING:
    from bare_metal_automation.models import DeploymentInventory, DiscoveredDevice

logger = logging.getLogger(__name__)


class HPEServerDriver(ServerDriver):
    """ServerDriver implementation for HPE ProLiant servers via iLO 5 Redfish.

    Delegates to the existing ``HPEServerProvisioner`` and
    ``HPEServerResetter`` classes.
    """

    def __init__(
        self,
        inventory: DeploymentInventory | None = None,
        **kwargs: Any,
    ) -> None:
        self.inventory = inventory

    def provision_server(self, device: DiscoveredDevice) -> bool:
        from bare_metal_automation.provisioner.server import HPEServerProvisioner

        provisioner = HPEServerProvisioner(inventory=self.inventory)
        return provisioner.provision_server(device)

    def reset_server(self, device: DiscoveredDevice) -> bool:
        from bare_metal_automation.rollback.server import HPEServerResetter

        resetter = HPEServerResetter()
        return resetter.reset_server(device)
