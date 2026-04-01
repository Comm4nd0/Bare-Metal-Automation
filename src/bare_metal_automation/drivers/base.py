"""Abstract base classes for vendor drivers.

Each driver category defines the interface that vendor-specific implementations
must satisfy.  The orchestrator dispatches to drivers through these interfaces,
making the core pipeline vendor-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bare_metal_automation.models import DiscoveredDevice


class NetworkDriver(ABC):
    """Automates network device lifecycle: configure, firmware upgrade, reset."""

    @abstractmethod
    def configure_device(self, device: DiscoveredDevice) -> bool:
        """Push rendered configuration to a network device."""

    @abstractmethod
    def upgrade_firmware(self, device: DiscoveredDevice) -> bool:
        """Upgrade firmware on a network device."""

    @abstractmethod
    def reset_device(self, device: DiscoveredDevice) -> bool:
        """Factory-reset a network device."""

    @abstractmethod
    def verify_factory_state(self, device: DiscoveredDevice) -> bool:
        """Check whether a device is in factory-default state."""


class ServerDriver(ABC):
    """Automates server lifecycle: provision BMC/BIOS/RAID/OS, reset."""

    @abstractmethod
    def provision_server(self, device: DiscoveredDevice) -> bool:
        """Run full provisioning sequence on a server."""

    @abstractmethod
    def reset_server(self, device: DiscoveredDevice) -> bool:
        """Factory-reset a server (BIOS, RAID, BMC)."""


class ApplianceDriver(ABC):
    """Automates appliance lifecycle: provision, reset."""

    @abstractmethod
    def provision_device(self, device: DiscoveredDevice) -> bool:
        """Run full provisioning sequence on an appliance."""

    @abstractmethod
    def reset_device(self, device: DiscoveredDevice) -> bool:
        """Factory-reset an appliance."""


class DiscoveryDriver(ABC):
    """Discovers neighbour devices and identifies platforms."""

    @abstractmethod
    def discover_neighbours(
        self,
        ip: str,
        credentials: list[tuple[str, str]] | None = None,
    ) -> list[Any]:
        """Discover neighbour devices reachable from *ip*."""

    @abstractmethod
    def identify_platform(self, device_info: dict[str, Any]) -> str | None:
        """Return a BMA platform string for *device_info*, or None."""
