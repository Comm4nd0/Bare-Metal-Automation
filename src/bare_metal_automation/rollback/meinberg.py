"""Meinberg NTP resetter — factory reset via REST API."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from bare_metal_automation.models import DeviceState, DiscoveredDevice
from bare_metal_automation.settings import (
    MEINBERG_API_BASE,
    MEINBERG_DEFAULT_PASSWORD,
    MEINBERG_DEFAULT_USER,
    REBOOT_WAIT,
)

logger = logging.getLogger(__name__)


class MeinbergResetter:
    """Resets Meinberg LANTIME NTP appliances to factory defaults.

    Attempts factory reset via API. If the factory-reset endpoint is
    not available, reverts each configuration section to defaults and
    reboots.
    """

    def reset_device(
        self,
        device: DiscoveredDevice,
        spec: dict[str, Any] | None = None,
    ) -> bool:
        """Run factory reset on a Meinberg NTP appliance."""
        spec = spec or {}
        hostname = device.intended_hostname or device.ip
        logger.info("%s: Starting factory reset", hostname)
        device.state = DeviceState.RESETTING

        host = device.management_ip or device.ip
        username = spec.get("username", MEINBERG_DEFAULT_USER)
        password = spec.get("password", MEINBERG_DEFAULT_PASSWORD)

        session = self._create_session(host, username, password)
        if session is None:
            logger.error(
                "%s: Cannot connect to Meinberg at %s",
                hostname, host,
            )
            device.state = DeviceState.FAILED
            return False

        try:
            # Try the factory-reset endpoint first
            if self._try_factory_reset(session, host, hostname):
                device.state = DeviceState.FACTORY_RESET
                return True

            # Fall back to manual config revert
            logger.info(
                "%s: Factory reset endpoint not available — "
                "reverting config sections manually",
                hostname,
            )
            self._revert_ntp_service(session, host, hostname)
            self._revert_ntp_references(session, host, hostname)
            self._revert_network(session, host, hostname)
            self._revert_system(session, host, hostname)

            # Reboot the device
            self._reboot(session, host, hostname)

            device.state = DeviceState.FACTORY_RESET
            logger.info(
                "%s: Configuration reverted and device rebooted",
                hostname,
            )
            return True

        except Exception as e:
            logger.error(
                "%s: Factory reset failed — %s", hostname, e,
            )
            device.state = DeviceState.FAILED
            return False

    def _create_session(
        self, host: str, username: str, password: str,
    ) -> requests.Session | None:
        """Create an authenticated session to the Meinberg API."""
        session = requests.Session()
        session.auth = HTTPBasicAuth(username, password)
        session.verify = False
        session.headers.update({"Content-Type": "application/json"})

        try:
            resp = session.get(
                f"https://{host}{MEINBERG_API_BASE}/status",
                timeout=10,
            )
            resp.raise_for_status()
            return session
        except Exception:
            return None

    def _try_factory_reset(
        self,
        session: requests.Session,
        host: str,
        hostname: str,
    ) -> bool:
        """Attempt factory reset via dedicated API endpoint."""
        try:
            resp = session.post(
                f"https://{host}{MEINBERG_API_BASE}/system/factory-reset",
                json={"confirm": True},
                timeout=30,
            )
            if resp.status_code in (200, 202):
                logger.info(
                    "%s: Factory reset accepted — waiting for reboot",
                    hostname,
                )
                time.sleep(REBOOT_WAIT)
                return True
            return False
        except requests.exceptions.HTTPError:
            return False
        except requests.exceptions.ConnectionError:
            # Connection dropped — device may be rebooting
            logger.info(
                "%s: Connection lost after factory reset (expected)",
                hostname,
            )
            time.sleep(REBOOT_WAIT)
            return True

    def _revert_ntp_service(
        self,
        session: requests.Session,
        host: str,
        hostname: str,
    ) -> None:
        """Reset NTP service to defaults."""
        logger.info("%s: Reverting NTP service config", hostname)
        try:
            session.put(
                f"https://{host}{MEINBERG_API_BASE}/config/ntp/service",
                json={
                    "enabled": True,
                    "local_stratum": 12,
                    "access_control": [],
                    "broadcast": False,
                    "authentication": False,
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(
                "%s: NTP service revert failed — %s", hostname, e,
            )

    def _revert_ntp_references(
        self,
        session: requests.Session,
        host: str,
        hostname: str,
    ) -> None:
        """Reset NTP references to defaults (GPS only)."""
        logger.info("%s: Reverting NTP reference config", hostname)
        try:
            session.put(
                f"https://{host}{MEINBERG_API_BASE}/config/ntp/references",
                json={
                    "gps": {"enabled": True},
                    "ptp": {"enabled": False},
                    "external_ntp": [],
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(
                "%s: NTP references revert failed — %s", hostname, e,
            )

    def _revert_network(
        self,
        session: requests.Session,
        host: str,
        hostname: str,
    ) -> None:
        """Reset network to DHCP."""
        logger.info("%s: Reverting network config to DHCP", hostname)
        try:
            session.put(
                f"https://{host}{MEINBERG_API_BASE}/config/network",
                json={
                    "hostname": "LANTIME",
                    "ipv4": {"mode": "dhcp"},
                    "dns": [],
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(
                "%s: Network revert failed — %s", hostname, e,
            )

    def _revert_system(
        self,
        session: requests.Session,
        host: str,
        hostname: str,
    ) -> None:
        """Reset system settings to defaults."""
        logger.info("%s: Reverting system settings", hostname)
        try:
            session.put(
                f"https://{host}{MEINBERG_API_BASE}/config/system",
                json={
                    "timezone": "UTC",
                    "syslog": [],
                    "snmp": {"enabled": False},
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(
                "%s: System revert failed — %s", hostname, e,
            )

    def _reboot(
        self,
        session: requests.Session,
        host: str,
        hostname: str,
    ) -> None:
        """Reboot the Meinberg device."""
        logger.info("%s: Rebooting device", hostname)
        try:
            session.post(
                f"https://{host}{MEINBERG_API_BASE}/system/reboot",
                json={"confirm": True},
                timeout=10,
            )
        except requests.exceptions.ConnectionError:
            # Expected — device is rebooting
            pass
        logger.info(
            "%s: Reboot issued — waiting %ds", hostname, REBOOT_WAIT,
        )
        time.sleep(REBOOT_WAIT)
