"""Meinberg LANTIME NTP provisioner — OS install and configuration via web API."""

from __future__ import annotations

import logging
import time

import requests
from requests.auth import HTTPBasicAuth

from bare_metal_automation.models import (
    DeploymentInventory,
    DeviceState,
    DiscoveredDevice,
)
from bare_metal_automation.settings import (
    FIRMWARE_DIR,
    MEINBERG_API_BASE,
    MEINBERG_DEFAULT_PASSWORD,
    MEINBERG_DEFAULT_USER,
)

logger = logging.getLogger(__name__)


class MeinbergProvisioner:
    """Provisions Meinberg LANTIME NTP appliances.

    Provisioning sequence:
    1. Firmware/OS update (if specified)
    2. Network configuration (management IP, VLAN, DNS)
    3. NTP reference source configuration (GPS, PTP, etc.)
    4. NTP service configuration (clients, access control, stratum)
    5. System settings (hostname, timezone, syslog, SNMP)
    6. User account configuration
    """

    def __init__(
        self,
        inventory: DeploymentInventory,
        firmware_dir: str = FIRMWARE_DIR,
        http_server: str | None = None,
    ) -> None:
        self.inventory = inventory
        self.firmware_dir = firmware_dir
        self.http_server = http_server or inventory.laptop_ip

    def provision_device(self, device: DiscoveredDevice) -> bool:
        """Run the full provisioning sequence for a Meinberg NTP device."""
        spec = self.inventory.get_device_spec(device.serial) or {}

        host = device.ip
        username = spec.get("username", MEINBERG_DEFAULT_USER)
        password = spec.get("password", MEINBERG_DEFAULT_PASSWORD)

        session = self._create_session(host, username, password)
        if session is None:
            logger.error(f"{device.intended_hostname}: Cannot connect to Meinberg at {host}")
            device.state = DeviceState.FAILED
            return False

        try:
            # Verify connectivity
            device_info = self._get_device_info(session, host)
            if device_info:
                logger.info(
                    f"{device.intended_hostname}: Connected to Meinberg — "
                    f"{device_info.get('model', 'Unknown')} "
                    f"(Serial: {device_info.get('serial', 'N/A')}, "
                    f"Firmware: {device_info.get('firmware_version', 'N/A')})"
                )

            # Step 1: Firmware/OS update
            firmware_file = spec.get("firmware_image")
            if firmware_file:
                if not self._update_firmware(session, host, device, firmware_file):
                    return False

            # Step 2: Network configuration
            network_config = spec.get("network_config", {})
            if network_config:
                if not self._configure_network(session, host, device, network_config):
                    return False

            # Step 3: NTP reference source configuration
            ref_config = spec.get("ntp_references", {})
            if ref_config:
                if not self._configure_ntp_references(session, host, device, ref_config):
                    return False

            # Step 4: NTP service configuration
            ntp_config = spec.get("ntp_config", {})
            if ntp_config:
                if not self._configure_ntp_service(session, host, device, ntp_config):
                    return False

            # Step 5: System settings
            system_config = spec.get("system_config", {})
            if system_config:
                if not self._configure_system(session, host, device, system_config):
                    return False

            # Step 6: User accounts
            users = spec.get("users", [])
            for user_config in users:
                self._configure_user(session, host, device, user_config)

            device.state = DeviceState.PROVISIONED
            logger.info(f"{device.intended_hostname}: Meinberg NTP provisioning complete")
            return True

        except Exception as e:
            logger.error(f"{device.intended_hostname}: Meinberg provisioning failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _create_session(
        self, host: str, username: str, password: str
    ) -> requests.Session | None:
        """Create an authenticated HTTP session to the Meinberg device."""
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
        except Exception as e:
            logger.error(f"Cannot connect to Meinberg at {host}: {e}")
            return None

    def _get_device_info(self, session: requests.Session, host: str) -> dict | None:
        """Retrieve device identification information."""
        try:
            resp = session.get(f"https://{host}{MEINBERG_API_BASE}/device/info")
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def _update_firmware(
        self,
        session: requests.Session,
        host: str,
        device: DiscoveredDevice,
        firmware_file: str,
    ) -> bool:
        """Upload and install firmware update on the Meinberg device."""
        logger.info(f"{device.intended_hostname}: Updating Meinberg firmware ({firmware_file})")
        device.state = DeviceState.FIRMWARE_UPGRADING

        try:
            firmware_path = f"{self.firmware_dir}/{firmware_file}"

            # Upload firmware file
            with open(firmware_path, "rb") as f:
                resp = session.post(
                    f"https://{host}{MEINBERG_API_BASE}/firmware/upload",
                    files={"firmware": (firmware_file, f, "application/octet-stream")},
                    headers={"Content-Type": None},  # Let requests set multipart boundary
                )
                resp.raise_for_status()

            upload_result = resp.json()
            logger.info(
                f"{device.intended_hostname}: Firmware uploaded — "
                f"{upload_result.get('message', 'OK')}"
            )

            # Trigger firmware installation
            resp = session.post(
                f"https://{host}{MEINBERG_API_BASE}/firmware/install",
                json={"reboot_after": True},
            )
            resp.raise_for_status()

            # Wait for device to reboot and come back
            logger.info(f"{device.intended_hostname}: Waiting for firmware install and reboot...")
            time.sleep(60)

            if not self._wait_for_device(host, session):
                device.state = DeviceState.FAILED
                return False

            device.state = DeviceState.FIRMWARE_UPGRADED
            logger.info(f"{device.intended_hostname}: Firmware updated successfully")
            return True

        except FileNotFoundError:
            logger.error(
                f"{device.intended_hostname}: Firmware file not found: {firmware_file}"
            )
            device.state = DeviceState.FAILED
            return False
        except Exception as e:
            logger.error(f"{device.intended_hostname}: Firmware update failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _configure_network(
        self,
        session: requests.Session,
        host: str,
        device: DiscoveredDevice,
        network_config: dict,
    ) -> bool:
        """Configure Meinberg network settings."""
        logger.info(f"{device.intended_hostname}: Configuring network")
        device.state = DeviceState.CONFIGURING

        try:
            payload: dict = {}

            if "hostname" in network_config:
                payload["hostname"] = network_config["hostname"]

            if "ipv4" in network_config:
                ipv4 = network_config["ipv4"]
                payload["ipv4"] = {
                    "mode": ipv4.get("mode", "static"),
                    "address": ipv4.get("address"),
                    "netmask": ipv4.get("netmask"),
                    "gateway": ipv4.get("gateway"),
                }

            if "dns_servers" in network_config:
                payload["dns"] = {"servers": network_config["dns_servers"]}

            if "vlan_id" in network_config:
                payload["vlan"] = {
                    "enabled": True,
                    "id": network_config["vlan_id"],
                }

            resp = session.put(
                f"https://{host}{MEINBERG_API_BASE}/config/network",
                json=payload,
            )
            resp.raise_for_status()

            logger.info(f"{device.intended_hostname}: Network configured")
            return True

        except Exception as e:
            logger.error(f"{device.intended_hostname}: Network configuration failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _configure_ntp_references(
        self,
        session: requests.Session,
        host: str,
        device: DiscoveredDevice,
        ref_config: dict,
    ) -> bool:
        """Configure NTP reference sources (GPS, PTP, external NTP, etc.)."""
        logger.info(f"{device.intended_hostname}: Configuring NTP reference sources")

        try:
            payload: dict = {}

            # GPS receiver configuration
            gps_config = ref_config.get("gps", {})
            if gps_config:
                payload["gps"] = {
                    "enabled": gps_config.get("enabled", True),
                    "antenna_cable_delay_ns": gps_config.get("cable_delay_ns", 0),
                    "survey_mode": gps_config.get("survey_mode", "auto"),
                    "elevation_mask": gps_config.get("elevation_mask", 10),
                }

                # Fixed position (for timing-only mode)
                if "position" in gps_config:
                    pos = gps_config["position"]
                    payload["gps"]["fixed_position"] = {
                        "latitude": pos.get("latitude"),
                        "longitude": pos.get("longitude"),
                        "altitude": pos.get("altitude", 0),
                    }

            # PTP (IEEE 1588) configuration
            ptp_config = ref_config.get("ptp", {})
            if ptp_config:
                payload["ptp"] = {
                    "enabled": ptp_config.get("enabled", False),
                    "domain": ptp_config.get("domain", 0),
                    "profile": ptp_config.get("profile", "default"),
                    "transport": ptp_config.get("transport", "ipv4"),
                }

            # External NTP reference servers
            ext_ntp = ref_config.get("external_ntp", [])
            if ext_ntp:
                payload["external_ntp"] = [
                    {
                        "address": srv.get("address"),
                        "prefer": srv.get("prefer", False),
                        "minpoll": srv.get("minpoll", 4),
                        "maxpoll": srv.get("maxpoll", 6),
                    }
                    for srv in ext_ntp
                ]

            resp = session.put(
                f"https://{host}{MEINBERG_API_BASE}/config/ntp/references",
                json=payload,
            )
            resp.raise_for_status()

            logger.info(f"{device.intended_hostname}: NTP references configured")
            return True

        except Exception as e:
            logger.error(
                f"{device.intended_hostname}: NTP reference configuration failed: {e}"
            )
            device.state = DeviceState.FAILED
            return False

    def _configure_ntp_service(
        self,
        session: requests.Session,
        host: str,
        device: DiscoveredDevice,
        ntp_config: dict,
    ) -> bool:
        """Configure NTP service settings (clients, access control, broadcast)."""
        logger.info(f"{device.intended_hostname}: Configuring NTP service")

        try:
            payload: dict = {
                "enabled": ntp_config.get("enabled", True),
            }

            # Stratum level
            if "local_stratum" in ntp_config:
                payload["local_stratum"] = ntp_config["local_stratum"]

            # Access restrictions
            access_control = ntp_config.get("access_control", [])
            if access_control:
                payload["access_control"] = [
                    {
                        "network": acl.get("network"),
                        "mask": acl.get("mask"),
                        "flags": acl.get("flags", ["nomodify", "notrap"]),
                    }
                    for acl in access_control
                ]

            # Broadcast/multicast
            if "broadcast" in ntp_config:
                payload["broadcast"] = ntp_config["broadcast"]

            # Symmetric keys
            if "authentication" in ntp_config:
                auth = ntp_config["authentication"]
                payload["authentication"] = {
                    "enabled": auth.get("enabled", False),
                    "keys": auth.get("keys", []),
                }

            resp = session.put(
                f"https://{host}{MEINBERG_API_BASE}/config/ntp/service",
                json=payload,
            )
            resp.raise_for_status()

            logger.info(f"{device.intended_hostname}: NTP service configured")
            return True

        except Exception as e:
            logger.error(
                f"{device.intended_hostname}: NTP service configuration failed: {e}"
            )
            device.state = DeviceState.FAILED
            return False

    def _configure_system(
        self,
        session: requests.Session,
        host: str,
        device: DiscoveredDevice,
        system_config: dict,
    ) -> bool:
        """Configure system settings (timezone, syslog, SNMP)."""
        logger.info(f"{device.intended_hostname}: Configuring system settings")

        try:
            payload: dict = {}

            if "timezone" in system_config:
                payload["timezone"] = system_config["timezone"]

            if "syslog" in system_config:
                syslog = system_config["syslog"]
                payload["syslog"] = {
                    "servers": [
                        {"address": s.get("address"), "port": s.get("port", 514)}
                        for s in syslog.get("servers", [])
                    ],
                    "facility": syslog.get("facility", "local0"),
                }

            if "snmp" in system_config:
                snmp = system_config["snmp"]
                payload["snmp"] = {
                    "enabled": snmp.get("enabled", True),
                    "community": snmp.get("community", "public"),
                    "trap_receivers": snmp.get("trap_receivers", []),
                    "system_contact": snmp.get("system_contact", ""),
                    "system_location": snmp.get("system_location", ""),
                }

            if "notification_emails" in system_config:
                payload["email"] = {
                    "recipients": system_config["notification_emails"],
                    "smtp_server": system_config.get("smtp_server", ""),
                }

            resp = session.put(
                f"https://{host}{MEINBERG_API_BASE}/config/system",
                json=payload,
            )
            resp.raise_for_status()

            logger.info(f"{device.intended_hostname}: System settings configured")
            return True

        except Exception as e:
            logger.error(
                f"{device.intended_hostname}: System configuration failed: {e}"
            )
            device.state = DeviceState.FAILED
            return False

    def _configure_user(
        self,
        session: requests.Session,
        host: str,
        device: DiscoveredDevice,
        user_config: dict,
    ) -> None:
        """Create or update a user account on the Meinberg device."""
        username = user_config["username"]
        payload = {
            "username": username,
            "password": user_config["password"],
            "role": user_config.get("role", "admin"),
        }

        try:
            resp = session.put(
                f"https://{host}{MEINBERG_API_BASE}/config/users/{username}",
                json=payload,
            )
            resp.raise_for_status()
            logger.info(f"{device.intended_hostname}: User {username} configured")
        except requests.exceptions.HTTPError:
            # User may not exist yet — create it
            try:
                resp = session.post(
                    f"https://{host}{MEINBERG_API_BASE}/config/users",
                    json=payload,
                )
                resp.raise_for_status()
                logger.info(f"{device.intended_hostname}: User {username} created")
            except Exception as e:
                logger.error(
                    f"{device.intended_hostname}: Failed to configure user {username}: {e}"
                )

    def _wait_for_device(
        self,
        host: str,
        session: requests.Session,
        timeout: int = 300,
    ) -> bool:
        """Wait for the Meinberg device to become responsive after a reboot."""
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

        logger.error(f"Meinberg at {host} did not respond within {timeout}s")
        return False
