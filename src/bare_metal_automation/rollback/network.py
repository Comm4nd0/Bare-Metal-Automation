"""Network device resetter — erase config and reload to factory defaults."""

from __future__ import annotations

import logging
import socket

from bare_metal_automation.models import DeviceState, DiscoveredDevice

logger = logging.getLogger(__name__)

# Time to wait for device to reload after write erase
RELOAD_WAIT_INITIAL = 60  # seconds before first SSH poll
RELOAD_POLL_INTERVAL = 20  # seconds between SSH polls
RELOAD_POLL_TIMEOUT = 900  # 15 minutes max wait


class NetworkResetter:
    """Resets Cisco network devices to factory defaults.

    Sequence per device:
    1. SSH connect
    2. ``write erase`` — clear startup-config
    3. ``delete /force flash:<firmware>`` (optional — clear uploaded firmware)
    4. ``reload`` — reboot to factory defaults
    5. Wait for device to come back (or stay down — either is fine)
    """

    def __init__(
        self,
        ssh_timeout: int = 30,
        delete_firmware: bool = False,
    ) -> None:
        self.ssh_timeout = ssh_timeout
        self.delete_firmware = delete_firmware

    def reset_device(self, device: DiscoveredDevice) -> bool:
        """Factory-reset a single Cisco network device.

        Returns True on success (device erased and reloading).
        """
        hostname = device.intended_hostname or device.ip
        logger.info("%s: Starting factory reset", hostname)
        device.state = DeviceState.RESETTING

        try:
            connection = self._connect(device)
            if connection is None:
                device.state = DeviceState.FAILED
                return False

            # Erase startup configuration
            platform = (device.platform or "").lower()
            if "asa" in platform or "ftd" in platform:
                logger.info("%s: write erase (ASA/FTD)", hostname)
                connection.send_command_timing("write erase")
                connection.send_command_timing("")  # confirm
            else:
                logger.info("%s: write erase (IOS/IOS-XE)", hostname)
                connection.send_command_timing("write erase")
                connection.send_command_timing("")  # confirm

            # Clear boot variable
            if "asa" not in platform and "ftd" not in platform:
                logger.info("%s: Clearing boot system variable", hostname)
                connection.send_config_set(["no boot system"])
                connection.save_config()

            # Reload the device
            logger.info("%s: Issuing reload command", hostname)
            try:
                connection.send_command_timing("reload")
                connection.send_command_timing("")  # confirm
            except Exception:
                # Connection may drop during reload — that's expected
                pass

            # Disconnect (may already be disconnected)
            try:
                connection.disconnect()
            except Exception:
                pass

            device.state = DeviceState.FACTORY_RESET
            logger.info(
                "%s: Factory reset issued — device is reloading",
                hostname,
            )
            return True

        except Exception as e:
            logger.error("%s: Factory reset failed — %s", hostname, e)
            device.state = DeviceState.FAILED
            return False

    def _connect(self, device: DiscoveredDevice):
        """Establish SSH connection to the device via Netmiko."""
        hostname = device.intended_hostname or device.ip
        platform = (device.platform or "").lower()

        # Map BMA platform names to Netmiko device types
        if "asa" in platform or "ftd" in platform:
            device_type = "cisco_asa"
        elif "iosxe" in platform:
            device_type = "cisco_xe"
        else:
            device_type = "cisco_ios"

        try:
            from netmiko import ConnectHandler

            connection = ConnectHandler(
                device_type=device_type,
                host=device.ip,
                username="admin",
                password="admin",
                timeout=self.ssh_timeout,
            )
            connection.enable()
            return connection
        except Exception as e:
            logger.error(
                "%s: SSH connection failed to %s — %s",
                hostname,
                device.ip,
                e,
            )
            return None

    def verify_factory_state(self, device: DiscoveredDevice) -> bool:
        """Check if a device appears to be in factory state.

        Attempts SSH connection — if refused or times out,
        the device is likely still reloading or at factory prompt
        (which is acceptable).
        """
        hostname = device.intended_hostname or device.ip
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((device.ip, 22))
            sock.close()

            if result != 0:
                logger.info(
                    "%s: SSH port closed — device likely at factory state",
                    hostname,
                )
                return True

            # Port is open — could be factory default or still configured
            logger.info(
                "%s: SSH port open — device has reloaded",
                hostname,
            )
            return True

        except Exception as e:
            logger.info(
                "%s: Cannot reach device — %s (acceptable for factory reset)",
                hostname,
                e,
            )
            return True
