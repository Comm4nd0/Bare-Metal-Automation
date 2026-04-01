"""Driver registry — maps platform strings to vendor driver classes.

Vendor drivers register themselves on import.  The orchestrator looks up
the appropriate driver by platform string at runtime, eliminating the
need for hard-coded vendor routing.
"""

from __future__ import annotations

import logging
from typing import Any

from bare_metal_automation.drivers.base import (
    ApplianceDriver,
    DiscoveryDriver,
    NetworkDriver,
    ServerDriver,
)

logger = logging.getLogger(__name__)


class DriverRegistry:
    """Central registry for vendor driver classes.

    Drivers are registered with a *platform prefix* (e.g. ``"cisco_"``).
    Lookups match the longest prefix first, so ``"cisco_iosxe"`` beats
    ``"cisco_"`` if both are registered.
    """

    # prefix -> driver class (not instances — instantiation is deferred)
    _network: dict[str, type[NetworkDriver]] = {}
    _server: dict[str, type[ServerDriver]] = {}
    _appliance: dict[str, type[ApplianceDriver]] = {}
    _discovery: dict[str, type[DiscoveryDriver]] = {}

    # prefix -> cached instances (lazily created)
    _network_instances: dict[str, NetworkDriver] = {}
    _server_instances: dict[str, ServerDriver] = {}
    _appliance_instances: dict[str, ApplianceDriver] = {}
    _discovery_instances: dict[str, DiscoveryDriver] = {}

    # Optional factory kwargs per prefix (set via register calls)
    _factory_kwargs: dict[str, dict[str, Any]] = {}

    # ── Registration ─────────────────────────────────────────────────────

    @classmethod
    def register_network(
        cls, prefix: str, driver_cls: type[NetworkDriver]
    ) -> None:
        cls._network[prefix] = driver_cls
        logger.debug("Registered network driver: %s -> %s", prefix, driver_cls.__name__)

    @classmethod
    def register_server(
        cls, prefix: str, driver_cls: type[ServerDriver]
    ) -> None:
        cls._server[prefix] = driver_cls
        logger.debug("Registered server driver: %s -> %s", prefix, driver_cls.__name__)

    @classmethod
    def register_appliance(
        cls, prefix: str, driver_cls: type[ApplianceDriver]
    ) -> None:
        cls._appliance[prefix] = driver_cls
        logger.debug("Registered appliance driver: %s -> %s", prefix, driver_cls.__name__)

    @classmethod
    def register_discovery(
        cls, prefix: str, driver_cls: type[DiscoveryDriver]
    ) -> None:
        cls._discovery[prefix] = driver_cls
        logger.debug("Registered discovery driver: %s -> %s", prefix, driver_cls.__name__)

    # ── Lookup ───────────────────────────────────────────────────────────

    @classmethod
    def _match_prefix(cls, registry: dict[str, Any], platform: str) -> str | None:
        """Return the longest matching prefix for *platform*, or None."""
        best: str | None = None
        for prefix in registry:
            if platform.startswith(prefix):
                if best is None or len(prefix) > len(best):
                    best = prefix
        return best

    @classmethod
    def get_network_driver(cls, platform: str, **kwargs: Any) -> NetworkDriver | None:
        prefix = cls._match_prefix(cls._network, platform)
        if prefix is None:
            return None
        return cls._network[prefix](**kwargs)

    @classmethod
    def get_server_driver(cls, platform: str, **kwargs: Any) -> ServerDriver | None:
        prefix = cls._match_prefix(cls._server, platform)
        if prefix is None:
            return None
        return cls._server[prefix](**kwargs)

    @classmethod
    def get_appliance_driver(cls, platform: str, **kwargs: Any) -> ApplianceDriver | None:
        prefix = cls._match_prefix(cls._appliance, platform)
        if prefix is None:
            return None
        return cls._appliance[prefix](**kwargs)

    @classmethod
    def get_discovery_driver(cls, platform: str, **kwargs: Any) -> DiscoveryDriver | None:
        prefix = cls._match_prefix(cls._discovery, platform)
        if prefix is None:
            return None
        return cls._discovery[prefix](**kwargs)

    # ── Category helpers ─────────────────────────────────────────────────

    @classmethod
    def device_category(cls, platform: str) -> str | None:
        """Return ``"network"``, ``"server"``, ``"appliance"``, or None."""
        if cls._match_prefix(cls._network, platform) is not None:
            return "network"
        if cls._match_prefix(cls._server, platform) is not None:
            return "server"
        if cls._match_prefix(cls._appliance, platform) is not None:
            return "appliance"
        return None

    @classmethod
    def is_network(cls, platform: str) -> bool:
        return cls._match_prefix(cls._network, platform) is not None

    @classmethod
    def is_server(cls, platform: str) -> bool:
        return cls._match_prefix(cls._server, platform) is not None

    @classmethod
    def is_appliance(cls, platform: str) -> bool:
        return cls._match_prefix(cls._appliance, platform) is not None

    # ── Introspection ────────────────────────────────────────────────────

    @classmethod
    def registered_platforms(cls) -> list[tuple[str, str]]:
        """Return a list of ``(prefix, category)`` tuples for all registered drivers."""
        platforms: list[tuple[str, str]] = []
        for prefix in sorted(cls._network):
            platforms.append((prefix, "network"))
        for prefix in sorted(cls._server):
            platforms.append((prefix, "server"))
        for prefix in sorted(cls._appliance):
            platforms.append((prefix, "appliance"))
        return platforms

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (useful for testing)."""
        cls._network.clear()
        cls._server.clear()
        cls._appliance.clear()
        cls._discovery.clear()
        cls._network_instances.clear()
        cls._server_instances.clear()
        cls._appliance_instances.clear()
        cls._discovery_instances.clear()
        cls._factory_kwargs.clear()
