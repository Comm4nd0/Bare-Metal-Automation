"""HPE iLO 5 operations — firmware, BIOS, RAID, and virtual media.

Higher-level operations built on top of :class:`~.redfish.RedfishClient`.
Each function takes an open ``RedfishClient`` and performs one discrete
iLO task, logging progress and returning a bool success flag.
"""

from __future__ import annotations

import logging
import time

from bare_metal_automation.provisioner.redfish import (
    REDFISH_MANAGERS,
    REDFISH_SYSTEMS,
    REDFISH_UPDATE_SVC,
    RedfishClient,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30
LONG_TIMEOUT = 3600  # 1 hour for SPP / OS installs


# ── Firmware ───────────────────────────────────────────────────────────────

def upload_and_flash_firmware(
    client: RedfishClient,
    firmware_uri: str,
    target_uri: str | None = None,
    hostname: str = "",
) -> bool:
    """Upload and flash iLO / BIOS / NIC firmware via UpdateService.SimpleUpdate.

    Args:
        client:       Open Redfish session to the iLO.
        firmware_uri: HTTP URI of the firmware image on the laptop HTTP server.
        target_uri:   Redfish target component URI (defaults to iLO manager).
        hostname:     Device name for log messages.

    Returns:
        True on success; False on failure.
    """
    target = target_uri or REDFISH_MANAGERS
    logger.info(f"{hostname}: Uploading firmware from {firmware_uri}")

    try:
        resp = client.post(
            f"{REDFISH_UPDATE_SVC}/Actions/UpdateService.SimpleUpdate",
            {"ImageURI": firmware_uri, "Targets": [target]},
        )
        task_uri = resp.headers.get("Location")
        if task_uri:
            success = _wait_for_task(client, task_uri, hostname, "firmware flash")
        else:
            logger.info(f"{hostname}: No task URI returned — waiting 120s for flash")
            time.sleep(120)
            success = True

        if success:
            logger.info(f"{hostname}: Waiting for iLO to come back after firmware update")
            client.wait_for_ilo()
            logger.info(f"{hostname}: Firmware update complete")
        return success

    except Exception as e:
        logger.error(f"{hostname}: Firmware update failed: {e}")
        return False


# ── BIOS ───────────────────────────────────────────────────────────────────

def configure_bios(
    client: RedfishClient,
    settings: dict,
    hostname: str = "",
) -> bool:
    """Apply BIOS attribute changes and reboot.

    Only patches attributes that differ from current values (idempotent).
    Triggers a reboot and waits for POST to complete.
    """
    logger.info(f"{hostname}: Checking BIOS settings")

    try:
        current = client.get(f"{REDFISH_SYSTEMS}/Bios").get("Attributes", {})
        changes = {k: v for k, v in settings.items() if current.get(k) != v}

        if not changes:
            logger.info(f"{hostname}: BIOS already at desired settings — skipping")
            return True

        logger.info(f"{hostname}: Applying {len(changes)} BIOS change(s)")
        client.patch(f"{REDFISH_SYSTEMS}/Bios/Settings", {"Attributes": changes})

        # Reboot to apply
        client.reset_server()
        return client.wait_for_post()

    except Exception as e:
        logger.error(f"{hostname}: BIOS configuration failed: {e}")
        return False


# ── RAID / Smart Storage ───────────────────────────────────────────────────

def configure_raid(
    client: RedfishClient,
    raid_config: dict,
    hostname: str = "",
) -> bool:
    """Create RAID arrays via Redfish Smart Storage.

    ``raid_config`` schema::

        clear_existing: true
        logical_drives:
          - name: "OS Drive"
            raid_level: "Raid1"
            drives: ["1I:1:1", "1I:1:2"]
            spare_drives: []
    """
    logger.info(f"{hostname}: Configuring RAID storage")

    try:
        storage_path = f"{REDFISH_SYSTEMS}/SmartStorage/ArrayControllers"
        controllers = client.get(storage_path)
        members = controllers.get("Members", [])
        if not members:
            logger.error(f"{hostname}: No storage controllers found")
            return False

        ctrl_uri = members[0]["@odata.id"]

        if raid_config.get("clear_existing"):
            _clear_logical_drives(client, ctrl_uri, hostname)

        for ld in raid_config.get("logical_drives", []):
            _create_logical_drive(client, ctrl_uri, ld, hostname)

        client.reset_server()
        return client.wait_for_post()

    except Exception as e:
        logger.error(f"{hostname}: RAID configuration failed: {e}")
        return False


def _clear_logical_drives(
    client: RedfishClient, ctrl_uri: str, hostname: str
) -> None:
    collection = client.get(f"{ctrl_uri}/LogicalDrives")
    for member in collection.get("Members", []):
        logger.info(f"{hostname}: Deleting logical drive {member['@odata.id']}")
        client.delete(member["@odata.id"])


def _create_logical_drive(
    client: RedfishClient,
    ctrl_uri: str,
    ld_config: dict,
    hostname: str,
) -> None:
    payload: dict = {
        "LogicalDriveName": ld_config.get("name", "LogicalDrive"),
        "Raid": ld_config.get("raid_level", "Raid1"),
        "DataDrives": ld_config.get("drives", []),
    }
    if ld_config.get("spare_drives"):
        payload["SpareDrives"] = ld_config["spare_drives"]
    if "strip_size_kb" in ld_config:
        payload["StripSizeBytes"] = ld_config["strip_size_kb"] * 1024
    if "accelerator" in ld_config:
        payload["Accelerator"] = ld_config["accelerator"]

    logger.info(
        f"{hostname}: Creating {payload['Raid']} array "
        f"'{payload['LogicalDriveName']}' with {len(payload['DataDrives'])} drives"
    )
    client.post(f"{ctrl_uri}/LogicalDrives", data=payload)


# ── Virtual media ──────────────────────────────────────────────────────────

def mount_virtual_media(
    client: RedfishClient,
    iso_uri: str,
    media_index: int = 0,
    boot_on_reset: bool = True,
    hostname: str = "",
) -> bool:
    """Mount an ISO via iLO virtual media (DVD index *media_index*)."""
    vm_path = f"{REDFISH_MANAGERS}/VirtualMedia/{media_index + 2}"
    try:
        client.patch(vm_path, {
            "Image": iso_uri,
            "Oem": {"Hpe": {"BootOnNextServerReset": boot_on_reset}},
        })
        logger.info(f"{hostname}: Mounted virtual media [{media_index}]: {iso_uri}")
        return True
    except Exception as e:
        logger.error(f"{hostname}: Failed to mount virtual media: {e}")
        return False


def unmount_all_virtual_media(
    client: RedfishClient, hostname: str = ""
) -> None:
    """Eject all currently mounted virtual media images."""
    try:
        collection = client.get(f"{REDFISH_MANAGERS}/VirtualMedia")
        for member in collection.get("Members", []):
            vm = client.get(member["@odata.id"])
            if vm.get("Inserted"):
                client.patch(member["@odata.id"], {"Image": ""})
        logger.info(f"{hostname}: All virtual media ejected")
    except Exception as e:
        logger.warning(f"{hostname}: Virtual media unmount failed: {e}")


def set_one_time_boot(
    client: RedfishClient, boot_target: str = "Cd"
) -> None:
    """Set a one-time boot override (e.g. 'Cd', 'Pxe', 'Hdd')."""
    client.patch(REDFISH_SYSTEMS, {
        "Boot": {
            "BootSourceOverrideTarget": boot_target,
            "BootSourceOverrideEnabled": "Once",
        }
    })


def set_boot_order(
    client: RedfishClient,
    boot_order: list[str],
    hostname: str = "",
) -> bool:
    """Set the permanent UEFI boot order.

    *boot_order* should be a list of Redfish BootOptionReference strings,
    e.g. ``["HD.Slot.1.1", "NIC.LOM.1.1"]``.
    """
    try:
        client.patch(REDFISH_SYSTEMS, {
            "Boot": {"BootOrder": boot_order}
        })
        logger.info(f"{hostname}: Boot order set: {boot_order}")
        return True
    except Exception as e:
        logger.error(f"{hostname}: Failed to set boot order: {e}")
        return False


# ── Helpers ────────────────────────────────────────────────────────────────

def _wait_for_task(
    client: RedfishClient,
    task_uri: str,
    hostname: str,
    description: str,
    timeout: int = LONG_TIMEOUT,
) -> bool:
    """Poll a Redfish Task resource until it completes or times out."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            task = client.get(task_uri)
            state = task.get("TaskState", "")
            if state == "Completed":
                logger.info(f"{hostname}: {description} completed")
                return True
            if state in ("Exception", "Killed"):
                logger.error(
                    f"{hostname}: {description} failed — {task.get('Messages')}"
                )
                return False
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)

    logger.warning(f"{hostname}: {description} timed out after {timeout}s")
    return False
