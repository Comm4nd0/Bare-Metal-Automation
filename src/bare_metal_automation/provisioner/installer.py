"""OS installation orchestration — mount ISO, boot, poll for completion.

Orchestrates the full OS install sequence via iLO virtual media:
  1. Mount OS ISO (+ optional kickstart ISO for unattended install)
  2. Set one-time boot to CD-ROM
  3. Reboot server
  4. Poll for installation completion (server finishes POST, no VM mounted)
  5. Eject virtual media

The installer does NOT care which OS is being installed — it drives iLO
virtual media and monitors server state via Redfish.  The actual OS choice
is determined by which ISO URL is passed in.
"""

from __future__ import annotations

import logging
import time

from bare_metal_automation.models import DeviceState, DiscoveredDevice
from bare_metal_automation.provisioner import ilo as ilo_ops
from bare_metal_automation.provisioner.redfish import RedfishClient

logger = logging.getLogger(__name__)

# Installation can take up to 45 minutes for a fully unattended build
INSTALL_TIMEOUT = 2700   # 45 minutes
POLL_INTERVAL = 30


class OSInstaller:
    """Orchestrate OS installation via iLO 5 virtual media."""

    def __init__(self, http_server: str, iso_dir: str = "isos") -> None:
        """
        Args:
            http_server: IP / hostname of the laptop HTTP server where ISOs are served.
            iso_dir:     Path prefix on the HTTP server for ISO files.
        """
        self.http_server = http_server
        self.iso_dir = iso_dir

    def install(
        self,
        client: RedfishClient,
        device: DiscoveredDevice,
        os_iso: str,
        kickstart_iso: str | None = None,
    ) -> bool:
        """Run the full OS install sequence.

        Args:
            client:        Open Redfish session to the iLO.
            device:        Target device (for logging and state tracking).
            os_iso:        Filename of the OS ISO on the HTTP server.
            kickstart_iso: Optional kickstart/preseed ISO filename.

        Returns:
            True when the install appears to have completed successfully.
        """
        hostname = device.intended_hostname or device.ip
        logger.info(f"{hostname}: Starting OS installation ({os_iso})")
        device.state = DeviceState.OS_INSTALLING

        os_uri = f"http://{self.http_server}/{self.iso_dir}/{os_iso}"

        # Step 1: Mount OS ISO
        if not ilo_ops.mount_virtual_media(client, os_uri, media_index=0, hostname=hostname):
            device.state = DeviceState.FAILED
            return False

        # Step 2: Mount kickstart ISO if provided
        if kickstart_iso:
            ks_uri = f"http://{self.http_server}/{self.iso_dir}/{kickstart_iso}"
            if not ilo_ops.mount_virtual_media(client, ks_uri, media_index=1, hostname=hostname):
                logger.warning(f"{hostname}: Kickstart ISO mount failed — proceeding without it")

        # Step 3: Set one-time boot to CD
        ilo_ops.set_one_time_boot(client, "Cd")

        # Step 4: Reboot
        logger.info(f"{hostname}: Rebooting to OS installer")
        client.reset_server()

        # Step 5: Wait for the installer to start (give it 5 minutes to POST then launch)
        logger.info(f"{hostname}: Waiting for installer to start (~5 min)...")
        time.sleep(300)

        # Step 6: Poll for completion
        if not self._poll_completion(client, device, hostname):
            device.state = DeviceState.FAILED
            return False

        # Step 7: Eject media
        ilo_ops.unmount_all_virtual_media(client, hostname)

        device.state = DeviceState.OS_INSTALLED
        logger.info(f"{hostname}: OS installation complete")
        return True

    def _poll_completion(
        self,
        client: RedfishClient,
        device: DiscoveredDevice,
        hostname: str,
    ) -> bool:
        """Poll until the server finishes POST without an active virtual-media boot.

        The heuristic is: if the server is On + FinishedPost and no virtual
        media is set to BootOnNextReset, the installer has finished and the OS
        has taken over.
        """
        start = time.time()
        logger.info(
            f"{hostname}: Polling for OS install completion "
            f"(timeout {INSTALL_TIMEOUT // 60} min)"
        )

        while time.time() - start < INSTALL_TIMEOUT:
            try:
                post_state = client.post_state()
                power_state = client.power_state()

                if power_state == "On" and post_state in (
                    "FinishedPost", "InPostDiscoveryComplete"
                ):
                    if not self._has_active_vm_boot(client):
                        logger.info(f"{hostname}: OS installation appears complete")
                        return True
            except Exception:
                pass  # iLO may reboot mid-install
            time.sleep(POLL_INTERVAL)

        logger.warning(
            f"{hostname}: OS install polling timed out after "
            f"{INSTALL_TIMEOUT // 60} minutes"
        )
        return False

    def _has_active_vm_boot(self, client: RedfishClient) -> bool:
        """Return True if any virtual media is set to boot on next reset."""
        try:
            from bare_metal_automation.provisioner.redfish import REDFISH_MANAGERS

            collection = client.get(f"{REDFISH_MANAGERS}/VirtualMedia")
            for member in collection.get("Members", []):
                vm = client.get(member["@odata.id"])
                if vm.get("Inserted") and vm.get("BootOnNextReset"):
                    return True
        except Exception:
            pass
        return False
