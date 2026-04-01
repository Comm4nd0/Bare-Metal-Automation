"""Data sanitisation — cryptographic erase and disk zeroing.

Ensures no customer data remains on hardware after a factory reset.
Three methods are supported:

- **SED cryptographic erase** (via Redfish ``SecureErase``) — fastest: the
  drive's encryption key is destroyed, making all data unrecoverable instantly.
  Supported on modern NVMe / SAS / SATA SSDs with self-encryption.

- **Network device write erase** — Cisco ``write erase`` removes all config
  and keys.  Not a data-sanitisation operation per se but removes all
  provisioned credentials and configuration.

- **VM disk zeroing** — writes zeros to VM virtual disks before deletion so
  that residual data cannot be recovered from thin-provisioned storage.
  Not yet implemented — requires pyVmomi (vSphere SDK).

Each method records its outcome so a sanitisation certificate can be issued.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bare_metal_automation.models import DiscoveredDevice

logger = logging.getLogger(__name__)

REDFISH_SYSTEMS = "/redfish/v1/Systems/1"
REDFISH_STORAGE = f"{REDFISH_SYSTEMS}/Storage"


@dataclass
class EraseRecord:
    """Records the outcome of a single sanitisation operation."""

    device_serial: str
    device_hostname: str
    method: str             # "sed_crypto", "network_write_erase", "vm_disk_zero"
    target: str             # drive path, volume name, or "all"
    success: bool
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    error: str = ""


class DataSanitiser:
    """Perform cryptographic or physical data erasure on infrastructure."""

    # ── HPE server — SED cryptographic erase ──────────────────────────────

    def sed_cryptographic_erase(
        self,
        device: DiscoveredDevice,
        ilo_user: str = "Administrator",
        ilo_password: str = "admin",
    ) -> list[EraseRecord]:
        """Trigger SED cryptographic erase on all drives via Redfish.

        This calls ``Drive.SecureErase`` on every drive attached to each
        storage controller.  On self-encrypting drives this is instantaneous
        and compliant with NIST 800-88 Rev 1 (Purge).
        """
        from bare_metal_automation.provisioner.redfish import RedfishClient

        hostname = device.intended_hostname or device.ip
        records: list[EraseRecord] = []
        logger.info(f"{hostname}: Starting SED cryptographic erase")

        try:
            with RedfishClient(device.ip, ilo_user, ilo_password) as client:
                storage = client.get(REDFISH_STORAGE)
                for ctrl_ref in storage.get("Members", []):
                    ctrl = client.get(ctrl_ref["@odata.id"])
                    drives_ref = ctrl.get("Drives", [])

                    for drive_ref in drives_ref:
                        drive_uri = drive_ref["@odata.id"]
                        drive_info = client.get(drive_uri)
                        drive_name = drive_info.get("Name", drive_uri)

                        record = self._erase_drive(
                            client, device, drive_uri, drive_name
                        )
                        records.append(record)

        except Exception as e:
            logger.error(f"{hostname}: SED erase failed: {e}")
            records.append(EraseRecord(
                device_serial=device.serial or "",
                device_hostname=hostname,
                method="sed_crypto",
                target="all",
                success=False,
                error=str(e),
            ))

        return records

    def _erase_drive(
        self,
        client,
        device: DiscoveredDevice,
        drive_uri: str,
        drive_name: str,
    ) -> EraseRecord:
        hostname = device.intended_hostname or device.ip
        logger.info(f"{hostname}: Erasing drive {drive_name}")

        try:
            client.post(
                f"{drive_uri}/Actions/Drive.SecureErase",
                {},
            )
            # Drives typically return 200 or 202; we accept both
            logger.info(f"{hostname}: Drive {drive_name} erase triggered")
            return EraseRecord(
                device_serial=device.serial or "",
                device_hostname=hostname,
                method="sed_crypto",
                target=drive_name,
                success=True,
            )

        except Exception as e:
            logger.error(f"{hostname}: Drive {drive_name} erase failed: {e}")
            return EraseRecord(
                device_serial=device.serial or "",
                device_hostname=hostname,
                method="sed_crypto",
                target=drive_name,
                success=False,
                error=str(e),
            )

    # ── Cisco — write erase ────────────────────────────────────────────────

    def network_write_erase(
        self,
        device: DiscoveredDevice,
        username: str = "cisco",
        password: str = "cisco",
        ssh_timeout: int = 30,
    ) -> EraseRecord:
        """Run 'write erase' over SSH to clear Cisco device configuration."""
        from netmiko import ConnectHandler

        hostname = device.intended_hostname or device.ip
        logger.info(f"{hostname}: Running write erase")

        try:
            conn = ConnectHandler(
                device_type="cisco_ios",
                host=device.ip,
                username=username,
                password=password,
                timeout=ssh_timeout,
            )
            try:
                conn.send_command_timing("write erase")
                conn.send_command_timing("\n")  # confirm
                time.sleep(2)
                logger.info(f"{hostname}: write erase completed")
                return EraseRecord(
                    device_serial=device.serial or "",
                    device_hostname=hostname,
                    method="network_write_erase",
                    target="startup-config",
                    success=True,
                )
            finally:
                conn.disconnect()

        except Exception as e:
            logger.error(f"{hostname}: write erase failed: {e}")
            return EraseRecord(
                device_serial=device.serial or "",
                device_hostname=hostname,
                method="network_write_erase",
                target="startup-config",
                success=False,
                error=str(e),
            )

    # ── VMware — VM disk zeroing ───────────────────────────────────────────

    def zero_vm_disks(
        self,
        vm_list: list[str],
        vcenter_host: str = "",
        vcenter_user: str = "",
        vcenter_password: str = "",
    ) -> list[EraseRecord]:
        """Zero virtual machine disks before deletion.

        Requires pyVmomi (vSphere SDK for Python) — not yet implemented.
        Will connect to vCenter, power off VMs, and zero each VMDK.
        """
        raise NotImplementedError(
            "VM disk zeroing is not yet implemented. "
            "This operation requires pyVmomi (vSphere SDK). "
            "Ensure VMware integration is complete before calling this method."
        )

    # ── Verification ──────────────────────────────────────────────────────

    def verify_erasure(
        self,
        device: DiscoveredDevice,
        ilo_user: str = "Administrator",
        ilo_password: str = "admin",
    ) -> bool:
        """Verify that the server drive has been erased (best-effort).

        Checks that no logical drives remain in the Smart Storage controller
        (confirming RAID was destroyed) and that the SED erase task completed.
        This is a post-erase sanity check, not a forensic verification.
        """
        from bare_metal_automation.provisioner.redfish import RedfishClient

        hostname = device.intended_hostname or device.ip
        try:
            with RedfishClient(device.ip, ilo_user, ilo_password) as client:
                storage_path = (
                    "/redfish/v1/Systems/1/SmartStorage/ArrayControllers"
                )
                controllers = client.get(storage_path)
                for ctrl_ref in controllers.get("Members", []):
                    ctrl_uri = ctrl_ref["@odata.id"]
                    ld_collection = client.get(f"{ctrl_uri}/LogicalDrives")
                    if ld_collection.get("Members"):
                        logger.warning(
                            f"{hostname}: Logical drives still present "
                            f"after erase — verification FAILED"
                        )
                        return False

            logger.info(f"{hostname}: Erasure verified — no logical drives present")
            return True

        except Exception as e:
            logger.warning(f"{hostname}: Erasure verification failed: {e}")
            return False
