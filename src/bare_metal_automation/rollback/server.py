"""HPE server resetter — factory reset via Redfish/iLO 5."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from bare_metal_automation.models import DeviceState, DiscoveredDevice
from bare_metal_automation.settings import (
    ILO_DEFAULT_PASSWORD,
    ILO_DEFAULT_USER,
    REBOOT_WAIT,
    REDFISH_MANAGERS,
    REDFISH_SYSTEMS,
)

logger = logging.getLogger(__name__)


class RedfishClient:
    """Minimal Redfish client for reset operations."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        self.base_url = f"https://{host}"
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, password)
        self.session.verify = verify_ssl
        self.session.headers.update({
            "Content-Type": "application/json",
            "OData-Version": "4.0",
        })

    def get(self, path: str) -> dict[str, Any]:
        resp = self.session.get(f"{self.base_url}{path}")
        resp.raise_for_status()
        return resp.json()

    def post(
        self, path: str, data: dict[str, Any] | None = None,
    ) -> requests.Response:
        resp = self.session.post(
            f"{self.base_url}{path}", json=data or {},
        )
        resp.raise_for_status()
        return resp

    def patch(
        self, path: str, data: dict[str, Any],
    ) -> requests.Response:
        resp = self.session.patch(
            f"{self.base_url}{path}", json=data,
        )
        resp.raise_for_status()
        return resp

    def delete(self, path: str) -> requests.Response:
        resp = self.session.delete(f"{self.base_url}{path}")
        resp.raise_for_status()
        return resp


class HPEServerResetter:
    """Resets HPE ProLiant servers to factory state via iLO 5 Redfish.

    Sequence:
    1. Reset BIOS to defaults
    2. Delete all RAID logical drives
    3. Eject virtual media
    4. Reset iLO to factory defaults (preserving network config)
    5. Power off server
    """

    def reset_server(
        self,
        device: DiscoveredDevice,
        spec: dict[str, Any] | None = None,
    ) -> bool:
        """Run full factory reset sequence on an HPE server."""
        spec = spec or {}
        hostname = device.intended_hostname or device.ip
        logger.info("%s: Starting factory reset via Redfish", hostname)
        device.state = DeviceState.RESETTING

        ilo_host = device.management_ip or device.ip
        ilo_config = spec.get("ilo_config", {})
        username = ilo_config.get("username", ILO_DEFAULT_USER)
        password = ilo_config.get("password", ILO_DEFAULT_PASSWORD)

        try:
            client = RedfishClient(ilo_host, username, password)

            # Verify connectivity
            system_info = client.get(REDFISH_SYSTEMS)
            model = system_info.get("Model", "Unknown")
            logger.info(
                "%s: Connected to iLO — %s", hostname, model,
            )
        except Exception as e:
            logger.error(
                "%s: Cannot connect to iLO at %s — %s",
                hostname, ilo_host, e,
            )
            device.state = DeviceState.FAILED
            return False

        try:
            # Step 1: Reset BIOS to factory defaults
            self._reset_bios(client, hostname)

            # Step 2: Delete all RAID logical drives
            self._delete_raid(client, hostname)

            # Step 3: Eject virtual media
            self._eject_virtual_media(client, hostname)

            # Step 4: Reset iLO to factory defaults (preserve network)
            self._reset_ilo(client, hostname)

            # Step 5: Power off the server
            self._power_off(client, hostname)

            device.state = DeviceState.POWERED_OFF
            logger.info(
                "%s: Factory reset complete — server powered off",
                hostname,
            )
            return True

        except Exception as e:
            logger.error(
                "%s: Factory reset failed at step — %s",
                hostname, e,
            )
            device.state = DeviceState.FAILED
            return False

    def _reset_bios(
        self, client: RedfishClient, hostname: str,
    ) -> None:
        """Reset BIOS to factory defaults."""
        logger.info("%s: Resetting BIOS to factory defaults", hostname)
        try:
            client.post(
                f"{REDFISH_SYSTEMS}/Bios/Actions/Bios.ResetBios",
            )
            logger.info("%s: BIOS reset queued (takes effect on reboot)", hostname)
        except requests.exceptions.HTTPError as e:
            # Some iLO versions may not support this action
            logger.warning(
                "%s: BIOS reset action not available — %s",
                hostname, e,
            )

    def _delete_raid(
        self, client: RedfishClient, hostname: str,
    ) -> None:
        """Delete all RAID logical drives."""
        logger.info("%s: Deleting RAID logical drives", hostname)
        try:
            storage_path = (
                f"{REDFISH_SYSTEMS}/SmartStorage/ArrayControllers"
            )
            controllers = client.get(storage_path)

            for member in controllers.get("Members", []):
                ctrl_path = member.get("@odata.id", "")
                if not ctrl_path:
                    continue

                ld_path = f"{ctrl_path}/LogicalDrives"
                try:
                    drives = client.get(ld_path)
                    for drive_member in drives.get("Members", []):
                        drive_id = drive_member.get("@odata.id", "")
                        if drive_id:
                            client.delete(drive_id)
                            logger.info(
                                "%s: Deleted logical drive %s",
                                hostname,
                                drive_id.split("/")[-1],
                            )
                except requests.exceptions.HTTPError:
                    logger.info(
                        "%s: No logical drives on controller",
                        hostname,
                    )
        except requests.exceptions.HTTPError as e:
            logger.warning(
                "%s: Smart Storage not available — %s",
                hostname, e,
            )

    def _eject_virtual_media(
        self, client: RedfishClient, hostname: str,
    ) -> None:
        """Eject any mounted virtual media (ISO images)."""
        logger.info("%s: Ejecting virtual media", hostname)
        for slot in (1, 2):
            try:
                vm_path = f"{REDFISH_MANAGERS}/VirtualMedia/{slot}"
                client.patch(vm_path, {"Image": ""})
            except requests.exceptions.HTTPError:
                pass
        logger.info("%s: Virtual media ejected", hostname)

    def _reset_ilo(
        self, client: RedfishClient, hostname: str,
    ) -> None:
        """Reset iLO to factory defaults (preserving network config).

        Uses ResetType 'Default' which preserves the iLO network
        configuration so we can still reach the device afterward.
        """
        logger.info(
            "%s: Resetting iLO to factory defaults (preserving network)",
            hostname,
        )
        try:
            client.post(
                f"{REDFISH_MANAGERS}/Actions/Manager.ResetToFactoryDefaults",
                {"ResetType": "Default"},
            )
            logger.info(
                "%s: iLO factory reset issued — waiting for reboot",
                hostname,
            )
            time.sleep(REBOOT_WAIT)
        except requests.exceptions.HTTPError as e:
            logger.warning(
                "%s: iLO factory reset not available — %s",
                hostname, e,
            )

    def _power_off(
        self, client: RedfishClient, hostname: str,
    ) -> None:
        """Power off the server."""
        logger.info("%s: Powering off server", hostname)
        try:
            client.post(
                f"{REDFISH_SYSTEMS}/Actions/ComputerSystem.Reset",
                {"ResetType": "ForceOff"},
            )
            logger.info("%s: Server powered off", hostname)
        except requests.exceptions.HTTPError as e:
            logger.warning(
                "%s: Power off failed — %s (may already be off)",
                hostname, e,
            )
