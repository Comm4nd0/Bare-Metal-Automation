"""Meinberg NTP resetter — restore factory defaults via web API."""

from __future__ import annotations

import logging
import time

import requests
from requests.auth import HTTPBasicAuth

from ztp_forge.models import (
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
)
from ztp_forge.provisioner.meinberg import (
    MEINBERG_API_BASE,
    MEINBERG_DEFAULT_PASSWORD,
    MEINBERG_DEFAULT_USER,
)

logger = logging.getLogger(__name__)


class MeinbergResetter:
    """Resets Meinberg LANTIME NTP appliances to factory defaults.

    Issues a factory reset via the REST API, preserving network settings
    so the device remains reachable.  After reset the device will be
    accessible with factory-default credentials (admin / empty password)
    and ready for ZTP re-provisioning.
    """

    def __init__(self, inventory: DeploymentInventory) -> None:
        self.inventory = inventory

    def reset_device(self, device: DiscoveredDevice) -> bool:
        """Factory-reset a single Meinberg NTP device."""
        spec = self.inventory.get_device_spec(device.serial) or {}
        hostname = device.intended_hostname or device.hostname or device.ip
        host = device.ip

        logger.info(f"{hostname}: Starting factory reset")
        device.state = DeviceState.RESETTING

        session = self._connect(host, spec)
        if session is None:
            logger.error(f"{hostname}: Cannot connect to Meinberg at {host}")
            device.state = DeviceState.FAILED
            return False

        try:
            # Issue factory reset (preserve network so device stays reachable)
            logger.info(f"{hostname}: Issuing factory reset command")
            resp = session.post(
                f"https://{host}{MEINBERG_API_BASE}/system/factory-reset",
                json={"preserve_network": True},
            )
            resp.raise_for_status()

            # Wait for device to reboot
            logger.info(f"{hostname}: Waiting for device to reboot...")
            time.sleep(30)

            if not self._wait_for_device(host):
                logger.error(f"{hostname}: Device did not come back after factory reset")
                device.state = DeviceState.FAILED
                return False

            device.state = DeviceState.RESET_COMPLETE
            logger.info(f"{hostname}: Factory reset complete")
            return True

        except Exception as e:
            logger.error(f"{hostname}: Factory reset failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _connect(self, host: str, spec: dict) -> requests.Session | None:
        """Create an authenticated session, trying production then factory creds."""
        cred_list = []

        # Production credentials from inventory
        prod_user = spec.get("username")
        prod_pass = spec.get("password")
        if prod_user is not None and prod_pass is not None:
            cred_list.append((prod_user, prod_pass))

        # Users configured during provisioning
        for user_config in spec.get("users", []):
            cred_list.append((user_config["username"], user_config["password"]))

        # Factory defaults
        cred_list.append((MEINBERG_DEFAULT_USER, MEINBERG_DEFAULT_PASSWORD))

        for username, password in cred_list:
            try:
                session = requests.Session()
                session.auth = HTTPBasicAuth(username, password)
                session.verify = False
                session.headers.update({"Content-Type": "application/json"})

                resp = session.get(
                    f"https://{host}{MEINBERG_API_BASE}/status",
                    timeout=10,
                )
                resp.raise_for_status()
                logger.info(f"Connected to Meinberg at {host} ({username})")
                return session
            except Exception:
                continue

        return None

    def _wait_for_device(self, host: str, timeout: int = 300) -> bool:
        """Wait for the Meinberg device to become responsive after reset."""
        # After factory reset, device uses default credentials
        session = requests.Session()
        session.auth = HTTPBasicAuth(MEINBERG_DEFAULT_USER, MEINBERG_DEFAULT_PASSWORD)
        session.verify = False

        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = session.get(
                    f"https://{host}{MEINBERG_API_BASE}/status",
                    timeout=10,
                )
                if resp.status_code == 200:
                    logger.info(f"Meinberg at {host} is back online")
                    return True
            except Exception:
                pass
            time.sleep(15)

        return False
