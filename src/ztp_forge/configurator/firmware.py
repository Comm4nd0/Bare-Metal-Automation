"""Network device firmware configurator — upgrade IOS/ASA images via SCP/TFTP."""

from __future__ import annotations

import logging
import time

from ztp_forge.models import (
    DeploymentInventory,
    DevicePlatform,
    DeviceState,
    DiscoveredDevice,
)

logger = logging.getLogger(__name__)

# Supported firmware upgrade methods per platform
PLATFORM_TRANSFER_METHOD = {
    DevicePlatform.CISCO_IOS: "scp",
    DevicePlatform.CISCO_IOSXE: "scp",
    DevicePlatform.CISCO_ASA: "scp",
    DevicePlatform.CISCO_FTD: "scp",
}

# Expected boot variable format per platform
PLATFORM_BOOT_CMD = {
    DevicePlatform.CISCO_IOS: "boot system flash:{filename}",
    DevicePlatform.CISCO_IOSXE: "boot system bootflash:{filename}",
    DevicePlatform.CISCO_ASA: "boot system disk0:/{filename}",
    DevicePlatform.CISCO_FTD: "boot system disk0:/{filename}",
}

# Flash filesystem name per platform
PLATFORM_FLASH = {
    DevicePlatform.CISCO_IOS: "flash:",
    DevicePlatform.CISCO_IOSXE: "bootflash:",
    DevicePlatform.CISCO_ASA: "disk0:",
    DevicePlatform.CISCO_FTD: "disk0:",
}


class FirmwareConfigurator:
    """Upgrades firmware on Cisco network devices via SCP transfer and reload."""

    def __init__(
        self,
        inventory: DeploymentInventory,
        firmware_dir: str = "configs/firmware",
        tftp_server: str | None = None,
        ssh_timeout: int = 30,
    ) -> None:
        self.inventory = inventory
        self.firmware_dir = firmware_dir
        self.tftp_server = tftp_server or inventory.laptop_ip
        self.ssh_timeout = ssh_timeout

    def upgrade_device(self, device: DiscoveredDevice) -> bool:
        """Upgrade firmware on a single network device.

        Sequence:
        1. Check current firmware version
        2. Compare against target version from inventory
        3. If upgrade needed: transfer image, verify MD5, set boot var, reload
        4. Wait for device to come back online
        5. Verify new firmware version
        """
        spec = self.inventory.get_device_spec(device.serial) or {}
        target_firmware = spec.get("firmware_image")
        target_version = spec.get("firmware_version")

        if not target_firmware:
            logger.info(
                f"{device.intended_hostname}: No firmware_image specified — skipping"
            )
            return True

        try:
            device.state = DeviceState.FIRMWARE_UPGRADING

            connection = self._connect(device)
            if connection is None:
                return False

            # Step 1: Check current version
            current_version = self._get_current_version(connection, device)
            logger.info(
                f"{device.intended_hostname}: Current firmware: {current_version}"
            )

            # Step 2: Compare versions
            if target_version and current_version == target_version:
                logger.info(
                    f"{device.intended_hostname}: Already at target version "
                    f"{target_version} — skipping upgrade"
                )
                device.state = DeviceState.FIRMWARE_UPGRADED
                connection.disconnect()
                return True

            # Step 3: Check available space
            if not self._check_flash_space(connection, device, target_firmware):
                connection.disconnect()
                return False

            # Step 4: Transfer firmware image
            logger.info(
                f"{device.intended_hostname}: Transferring {target_firmware}..."
            )
            if not self._transfer_firmware(connection, device, target_firmware):
                connection.disconnect()
                return False

            # Step 5: Verify MD5 checksum
            expected_md5 = spec.get("firmware_md5")
            if expected_md5:
                if not self._verify_md5(
                    connection, device, target_firmware, expected_md5
                ):
                    connection.disconnect()
                    return False

            # Step 6: Set boot variable
            self._set_boot_variable(connection, device, target_firmware)

            # Step 7: Save and reload
            logger.info(f"{device.intended_hostname}: Saving config and reloading...")
            connection.send_command("write memory", read_timeout=30)
            self._reload_device(connection)
            connection.disconnect()

            # Step 8: Wait for device to come back
            if not self._wait_for_reload(device.ip):
                device.state = DeviceState.FAILED
                return False

            # Step 9: Verify new version
            verify_conn = self._connect(device)
            if verify_conn is None:
                device.state = DeviceState.FAILED
                return False

            new_version = self._get_current_version(verify_conn, device)
            verify_conn.disconnect()

            if target_version and new_version != target_version:
                logger.error(
                    f"{device.intended_hostname}: Version mismatch after upgrade — "
                    f"expected {target_version}, got {new_version}"
                )
                device.state = DeviceState.FAILED
                return False

            logger.info(
                f"{device.intended_hostname}: Firmware upgraded to {new_version}"
            )
            device.state = DeviceState.FIRMWARE_UPGRADED
            return True

        except Exception as e:
            logger.error(
                f"Firmware upgrade failed for {device.intended_hostname}: {e}"
            )
            device.state = DeviceState.FAILED
            return False

    def _connect(self, device: DiscoveredDevice):
        """Connect to device via SSH using Netmiko."""
        from netmiko import ConnectHandler

        device_type_map = {
            "cisco_ios": "cisco_ios",
            "cisco_iosxe": "cisco_xe",
            "cisco_asa": "cisco_asa",
            "cisco_ftd": "cisco_ftd",
        }

        platform = device_type_map.get(
            device.device_platform.value if device.device_platform else "cisco_ios",
            "cisco_ios",
        )

        try:
            return ConnectHandler(
                device_type=platform,
                host=device.ip,
                username="cisco",
                password="cisco",
                timeout=self.ssh_timeout,
            )
        except Exception as e:
            logger.error(f"SSH connection failed to {device.ip}: {e}")
            return None

    def _get_current_version(
        self, connection, device: DiscoveredDevice
    ) -> str:
        """Extract the running firmware version from the device."""
        output = connection.send_command("show version")

        # Parse version from show version output
        for line in output.splitlines():
            line_lower = line.lower()
            if "version" in line_lower and (
                "ios" in line_lower
                or "adaptive security" in line_lower
                or "software" in line_lower
            ):
                # Extract version string (e.g., "15.2(4)M7" or "9.16(3)")
                parts = line.split("Version")
                if len(parts) > 1:
                    version = parts[1].strip().split(",")[0].split()[0]
                    return version

        return "unknown"

    def _check_flash_space(
        self,
        connection,
        device: DiscoveredDevice,
        firmware_file: str,
    ) -> bool:
        """Verify sufficient flash space for the firmware image."""
        flash = PLATFORM_FLASH.get(device.device_platform, "flash:")
        output = connection.send_command(f"dir {flash}")

        # Parse free bytes from dir output
        for line in output.splitlines():
            if "bytes free" in line.lower() or "bytes available" in line.lower():
                # Extract the free bytes number
                parts = line.split()
                for i, part in enumerate(parts):
                    if "free" in part.lower() or "available" in part.lower():
                        try:
                            free_bytes = int(
                                parts[i - 1]
                                .replace("(", "")
                                .replace(")", "")
                                .replace(",", "")
                            )
                            # Require at least 50MB free beyond the image
                            if free_bytes < 50_000_000:
                                logger.warning(
                                    f"{device.intended_hostname}: Low flash space "
                                    f"({free_bytes} bytes free)"
                                )
                            return True
                        except (ValueError, IndexError):
                            continue

        logger.warning(
            f"{device.intended_hostname}: Could not determine flash space — proceeding"
        )
        return True

    def _transfer_firmware(
        self,
        connection,
        device: DiscoveredDevice,
        firmware_file: str,
    ) -> bool:
        """Transfer firmware image to device via SCP."""
        flash = PLATFORM_FLASH.get(device.device_platform, "flash:")

        # Enable SCP server on the device
        connection.send_config_set(["ip scp server enable"])

        # Use Netmiko's file transfer
        from netmiko import file_transfer

        try:
            transfer_result = file_transfer(
                connection,
                source_file=f"{self.firmware_dir}/{firmware_file}",
                dest_file=firmware_file,
                file_system=flash,
                direction="put",
                overwrite_file=True,
            )

            if transfer_result.get("file_transferred"):
                logger.info(
                    f"{device.intended_hostname}: Firmware transfer complete"
                )
                return True
            elif transfer_result.get("file_exists"):
                logger.info(
                    f"{device.intended_hostname}: Firmware already on device"
                )
                return True
            else:
                logger.error(
                    f"{device.intended_hostname}: Firmware transfer failed"
                )
                return False

        except Exception as e:
            logger.error(
                f"{device.intended_hostname}: SCP transfer failed: {e}"
            )
            return False

    def _verify_md5(
        self,
        connection,
        device: DiscoveredDevice,
        firmware_file: str,
        expected_md5: str,
    ) -> bool:
        """Verify MD5 checksum of transferred firmware."""
        flash = PLATFORM_FLASH.get(device.device_platform, "flash:")

        output = connection.send_command(
            f"verify /md5 {flash}{firmware_file}",
            read_timeout=300,  # MD5 can take several minutes
        )

        if expected_md5.lower() in output.lower():
            logger.info(f"{device.intended_hostname}: MD5 verified")
            return True

        logger.error(
            f"{device.intended_hostname}: MD5 mismatch — "
            f"expected {expected_md5}, got: {output}"
        )
        return False

    def _set_boot_variable(
        self,
        connection,
        device: DiscoveredDevice,
        firmware_file: str,
    ) -> None:
        """Set the boot system variable to the new firmware image."""
        boot_cmd_template = PLATFORM_BOOT_CMD.get(
            device.device_platform,
            "boot system flash:{filename}",
        )
        boot_cmd = boot_cmd_template.format(filename=firmware_file)

        config_commands = [
            "no boot system",  # Clear existing boot variable
            boot_cmd,
        ]
        connection.send_config_set(config_commands)
        logger.info(
            f"{device.intended_hostname}: Boot variable set to {firmware_file}"
        )

    def _reload_device(self, connection) -> None:
        """Initiate a device reload."""
        connection.send_command_timing("reload", strip_prompt=False)
        # Confirm the reload prompt (save config? / proceed?)
        time.sleep(1)
        connection.send_command_timing("\n", strip_prompt=False)
        time.sleep(1)
        connection.send_command_timing("\n", strip_prompt=False)

    def _wait_for_reload(
        self, ip: str, timeout: int = 900, interval: int = 20
    ) -> bool:
        """Wait for a device to come back online after a firmware reload."""
        import socket

        logger.info(f"Waiting for {ip} to reload (timeout {timeout}s)...")
        # Initial wait — device needs time to shut down
        time.sleep(60)

        start = time.time()
        while time.time() - start < timeout:
            try:
                sock = socket.create_connection((ip, 22), timeout=5)
                sock.close()
                logger.info(f"{ip} is back online after firmware upgrade")
                time.sleep(30)  # Extra time for full IOS boot
                return True
            except (TimeoutError, ConnectionRefusedError, OSError):
                time.sleep(interval)

        logger.error(f"{ip} did not come back within {timeout}s after firmware upgrade")
        return False
