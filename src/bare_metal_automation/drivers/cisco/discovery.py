"""Cisco CDP discovery driver — wraps CDPCollector and serial parsing."""

from __future__ import annotations

import logging
from typing import Any

from bare_metal_automation.drivers.base import DiscoveryDriver
from bare_metal_automation.drivers.cisco.platforms import pid_to_platform

logger = logging.getLogger(__name__)


class CiscoCDPDiscovery(DiscoveryDriver):
    """DiscoveryDriver implementation for Cisco CDP-capable devices."""

    def __init__(self, ssh_timeout: int = 30, **kwargs: Any) -> None:
        self.ssh_timeout = ssh_timeout

    def discover_neighbours(
        self,
        ip: str,
        credentials: list[tuple[str, str]] | None = None,
    ) -> list[Any]:
        from bare_metal_automation.discovery.cdp import CDPCollector

        collector = CDPCollector(ssh_timeout=self.ssh_timeout)
        return collector.collect(ip, credentials)

    def identify_platform(self, device_info: dict[str, Any]) -> str | None:
        """Identify platform from a PID string in device_info."""
        pid = device_info.get("pid", "")
        return pid_to_platform(pid)
