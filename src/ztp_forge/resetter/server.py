"""HPE server resetter — restore BIOS, RAID, and iLO to factory defaults via Redfish."""

from __future__ import annotations

import logging
import time

import requests

from ztp_forge.models import (
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
)
from ztp_forge.provisioner.server import (
    ILO_DEFAULT_PASSWORD,
    ILO_DEFAULT_USER,
    POLL_INTERVAL,
    REDFISH_BASE,
    REDFISH_MANAGERS,
    REDFISH_SYSTEMS,
    RedfishClient,
)

logger = logging.getLogger(__name__)


class HPEServerResetter:
    """Resets HPE ProLiant servers to factory defaults via iLO 5 Redfish API.

    Reset sequence:
    1. Reset BIOS to factory defaults
    2. Clear all RAID logical drives
    3. Reset iLO to factory defaults (preserving network for connectivity)
    4. Reboot server

    After reset, the server will have factory-default BIOS settings, no
    logical drives, and iLO accessible with default credentials
    (Administrator/admin).
    """

    def __init__(self, inventory: DeploymentInventory) -> None:
        self.inventory = inventory

    def reset_server(self, device: DiscoveredDevice) -> bool:
        """Run the full factory reset sequence for an HPE server."""
        spec = self.inventory.get_device_spec(device.serial) or {}
        hostname = device.intended_hostname or device.hostname or device.ip

        ilo_ip = device.ip
        device.state = DeviceState.RESETTING

        # Try production credentials first, then factory defaults
        client = self._connect(ilo_ip, spec)
        if client is None:
            logger.error(f"{hostname}: Cannot reach iLO at {ilo_ip}")
            device.state = DeviceState.FAILED
            return False

        try:
            system_info = client.get(REDFISH_SYSTEMS)
            logger.info(
                f"{hostname}: Connected to iLO — "
                f"{system_info.get('Model', 'Unknown')} "
                f"(Serial: {system_info.get('SerialNumber', 'N/A')})"
            )

            # Step 1: Reset BIOS to factory defaults
            if not self._reset_bios(client, hostname):
                device.state = DeviceState.FAILED
                return False

            # Step 2: Clear all RAID logical drives
            if not self._clear_raid(client, hostname):
                device.state = DeviceState.FAILED
                return False

            # Step 3: Reset iLO to factory defaults
            if not self._reset_ilo(client, hostname, ilo_ip):
                device.state = DeviceState.FAILED
                return False

            device.state = DeviceState.RESET_COMPLETE
            logger.info(f"{hostname}: Server factory reset complete")
            return True

        except requests.exceptions.ConnectionError:
            logger.error(f"{hostname}: Lost connection to iLO at {ilo_ip}")
            device.state = DeviceState.FAILED
            return False
        except Exception as e:
            logger.error(f"{hostname}: Factory reset failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _connect(self, ilo_ip: str, spec: dict) -> RedfishClient | None:
        """Connect to iLO, trying production credentials then factory defaults."""
        cred_list = []
        prod_user = spec.get("ilo_username")
        prod_pass = spec.get("ilo_password")
        if prod_user and prod_pass:
            cred_list.append((prod_user, prod_pass))

        # Also try credentials from ilo_config.users if present
        ilo_config = spec.get("ilo_config", {})
        for user_config in ilo_config.get("users", []):
            cred_list.append((user_config["username"], user_config["password"]))

        # Factory defaults last
        cred_list.append((ILO_DEFAULT_USER, ILO_DEFAULT_PASSWORD))

        for username, password in cred_list:
            try:
                client = RedfishClient(ilo_ip, username, password)
                client.get(REDFISH_BASE)
                logger.info(f"Connected to iLO at {ilo_ip} ({username})")
                return client
            except Exception:
                continue

        return None

    def _reset_bios(self, client: RedfishClient, hostname: str) -> bool:
        """Reset BIOS settings to factory defaults."""
        logger.info(f"{hostname}: Resetting BIOS to factory defaults")
        try:
            client.post(
                f"{REDFISH_SYSTEMS}/Bios/Actions/Bios.ResetBios",
            )
            logger.info(f"{hostname}: BIOS reset to defaults (pending reboot)")
            return True
        except Exception as e:
            logger.error(f"{hostname}: BIOS reset failed: {e}")
            return False

    def _clear_raid(self, client: RedfishClient, hostname: str) -> bool:
        """Delete all logical drives from all storage controllers."""
        logger.info(f"{hostname}: Clearing RAID logical drives")
        try:
            storage_path = f"{REDFISH_SYSTEMS}/SmartStorage/ArrayControllers"
            controllers = client.get(storage_path)

            members = controllers.get("Members", [])
            if not members:
                logger.info(f"{hostname}: No storage controllers found — skipping RAID clear")
                return True

            for controller_ref in members:
                controller_uri = controller_ref.get("@odata.id")
                ld_collection = client.get(f"{controller_uri}/LogicalDrives")
                for member in ld_collection.get("Members", []):
                    ld_uri = member.get("@odata.id")
                    logger.info(f"{hostname}: Deleting logical drive {ld_uri}")
                    client.delete(ld_uri)

            logger.info(f"{hostname}: All logical drives cleared")
            return True

        except Exception as e:
            logger.error(f"{hostname}: RAID clear failed: {e}")
            return False

    def _reset_ilo(
        self, client: RedfishClient, hostname: str, ilo_ip: str
    ) -> bool:
        """Reset iLO to factory defaults, preserving network settings.

        Uses ResetType "Default" which resets iLO configuration but keeps
        network settings intact so the iLO remains reachable.
        """
        logger.info(f"{hostname}: Resetting iLO to factory defaults")
        try:
            client.post(
                f"{REDFISH_MANAGERS}/Actions/Oem/Hpe/HpeiLO.ResetToFactoryDefaults",
                data={"ResetType": "Default"},
            )

            # iLO will reset — wait for it to come back
            logger.info(f"{hostname}: Waiting for iLO to restart...")
            time.sleep(30)

            # Poll for iLO responsiveness with factory default credentials
            factory_client = RedfishClient(ilo_ip, ILO_DEFAULT_USER, ILO_DEFAULT_PASSWORD)
            start = time.time()
            while time.time() - start < 300:
                try:
                    factory_client.get(REDFISH_BASE)
                    logger.info(f"{hostname}: iLO is back online with factory defaults")

                    # Reboot the server to apply BIOS defaults
                    self._reboot_server(factory_client, hostname)
                    return True
                except Exception:
                    time.sleep(POLL_INTERVAL)

            logger.error(f"{hostname}: iLO did not respond within 300s after reset")
            return False

        except Exception as e:
            logger.error(f"{hostname}: iLO factory reset failed: {e}")
            return False

    def _reboot_server(self, client: RedfishClient, hostname: str) -> None:
        """Reboot the server to apply pending BIOS changes."""
        try:
            client.post(
                f"{REDFISH_SYSTEMS}/Actions/ComputerSystem.Reset",
                data={"ResetType": "GracefulRestart"},
            )
            logger.info(f"{hostname}: Server rebooting to apply factory defaults")
        except requests.exceptions.HTTPError:
            try:
                client.post(
                    f"{REDFISH_SYSTEMS}/Actions/ComputerSystem.Reset",
                    data={"ResetType": "ForceRestart"},
                )
                logger.info(f"{hostname}: Server force-rebooting")
            except Exception as e:
                logger.warning(f"{hostname}: Could not reboot server: {e}")
