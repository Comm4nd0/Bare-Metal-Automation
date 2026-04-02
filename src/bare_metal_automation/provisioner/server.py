"""HPE server provisioner — BIOS, RAID, SPP, OS install, and iLO config via Redfish."""

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
    ILO_DEFAULT_PASSWORD,
    ILO_DEFAULT_USER,
    LONG_OP_TIMEOUT,
    POLL_INTERVAL,
    REDFISH_BASE,
    REDFISH_MANAGERS,
    REDFISH_SYSTEMS,
    REDFISH_UPDATE,
)

logger = logging.getLogger(__name__)


class RedfishClient:
    """Low-level Redfish API client for HPE iLO 5."""

    def __init__(self, host: str, username: str, password: str, verify_ssl: bool = False) -> None:
        self.base_url = f"https://{host}"
        self.auth = HTTPBasicAuth(username, password)
        self.verify = verify_ssl
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.verify = self.verify
        self.session.headers.update({
            "Content-Type": "application/json",
            "OData-Version": "4.0",
        })

    def get(self, path: str) -> dict:
        resp = self.session.get(f"{self.base_url}{path}")
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, data: dict | None = None) -> requests.Response:
        resp = self.session.post(f"{self.base_url}{path}", json=data or {})
        resp.raise_for_status()
        return resp

    def patch(self, path: str, data: dict) -> requests.Response:
        resp = self.session.patch(f"{self.base_url}{path}", json=data)
        resp.raise_for_status()
        return resp

    def put(self, path: str, data: dict) -> requests.Response:
        resp = self.session.put(f"{self.base_url}{path}", json=data)
        resp.raise_for_status()
        return resp

    def delete(self, path: str) -> requests.Response:
        resp = self.session.delete(f"{self.base_url}{path}")
        resp.raise_for_status()
        return resp


class HPEServerProvisioner:
    """Provisions HPE ProLiant servers via iLO 5 Redfish API.

    Provisioning sequence:
    1. iLO firmware update (if specified)
    2. BIOS configuration
    3. RAID/storage configuration
    4. HPE SPP (Service Pack for ProLiant) installation
    5. OS installation via virtual media
    6. iLO production configuration (networking, users, alerts)
    """

    def __init__(
        self,
        inventory: DeploymentInventory,
        firmware_dir: str = "configs/firmware",
        iso_dir: str = "configs/iso",
        http_server: str | None = None,
    ) -> None:
        self.inventory = inventory
        self.firmware_dir = firmware_dir
        self.iso_dir = iso_dir
        self.http_server = http_server or inventory.laptop_ip

    def provision_server(self, device: DiscoveredDevice) -> bool:
        """Run the full provisioning sequence for an HPE server."""
        spec = self.inventory.get_device_spec(device.serial) or {}

        ilo_ip = device.ip
        ilo_user = spec.get("ilo_username", ILO_DEFAULT_USER)
        ilo_pass = spec.get("ilo_password", ILO_DEFAULT_PASSWORD)

        client = RedfishClient(ilo_ip, ilo_user, ilo_pass)

        try:
            # Verify connectivity
            system_info = client.get(REDFISH_SYSTEMS)
            logger.info(
                f"{device.intended_hostname}: Connected to iLO — "
                f"{system_info.get('Model', 'Unknown')} "
                f"(Serial: {system_info.get('SerialNumber', 'N/A')})"
            )

            # Step 1: iLO firmware update
            ilo_firmware = spec.get("ilo_firmware")
            if ilo_firmware:
                if not self._update_ilo_firmware(client, device, ilo_firmware):
                    return False

            # Step 2: BIOS configuration
            bios_settings = spec.get("bios_settings", {})
            if bios_settings:
                if not self._configure_bios(client, device, bios_settings):
                    return False

            # Step 3: RAID configuration
            raid_config = spec.get("raid_config")
            if raid_config:
                if not self._configure_raid(client, device, raid_config):
                    return False

            # Step 4: HPE SPP installation
            spp_iso = spec.get("spp_iso")
            if spp_iso:
                if not self._install_spp(client, device, spp_iso):
                    return False

            # Step 5: OS installation
            os_iso = spec.get("os_iso")
            if os_iso:
                if not self._install_os(client, device, os_iso, spec):
                    return False

            # Step 6: iLO production configuration
            ilo_config = spec.get("ilo_config", {})
            if ilo_config:
                if not self._configure_ilo(client, device, ilo_config):
                    return False

            device.state = DeviceState.PROVISIONED
            logger.info(f"{device.intended_hostname}: Server provisioning complete")
            return True

        except requests.exceptions.ConnectionError:
            logger.error(f"{device.intended_hostname}: Cannot reach iLO at {ilo_ip}")
            device.state = DeviceState.FAILED
            return False
        except Exception as e:
            logger.error(f"{device.intended_hostname}: Provisioning failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _update_ilo_firmware(
        self, client: RedfishClient, device: DiscoveredDevice, firmware_file: str
    ) -> bool:
        """Update iLO firmware via Redfish UpdateService."""
        logger.info(f"{device.intended_hostname}: Updating iLO firmware ({firmware_file})")

        try:
            # Check current iLO firmware version
            manager = client.get(REDFISH_MANAGERS)
            current_fw = manager.get("FirmwareVersion", "unknown")
            logger.info(f"{device.intended_hostname}: Current iLO firmware: {current_fw}")

            # Upload firmware via HTTP URI
            firmware_uri = f"http://{self.http_server}/{self.firmware_dir}/{firmware_file}"
            update_payload = {
                "ImageURI": firmware_uri,
                "Targets": [f"{REDFISH_MANAGERS}"],
            }

            resp = client.post(
                f"{REDFISH_UPDATE}/Actions/UpdateService.SimpleUpdate",
                data=update_payload,
            )

            # Wait for iLO to apply the update (it will reset itself)
            task_uri = resp.headers.get("Location")
            if task_uri:
                self._wait_for_task(client, device, task_uri, "iLO firmware update")
            else:
                logger.info(
                    f"{device.intended_hostname}: Waiting for iLO firmware flash..."
                )
                time.sleep(120)

            # Wait for iLO to come back
            self._wait_for_ilo(client, device.ip)

            logger.info(f"{device.intended_hostname}: iLO firmware updated")
            return True

        except Exception as e:
            logger.error(f"{device.intended_hostname}: iLO firmware update failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _configure_bios(
        self, client: RedfishClient, device: DiscoveredDevice, bios_settings: dict
    ) -> bool:
        """Configure BIOS settings via Redfish."""
        logger.info(f"{device.intended_hostname}: Configuring BIOS settings")
        device.state = DeviceState.BIOS_CONFIGURING

        try:
            # Get current BIOS settings
            bios_current = client.get(f"{REDFISH_SYSTEMS}/Bios")
            current_attrs = bios_current.get("Attributes", {})

            # Determine which settings need changing
            changes_needed = {}
            for key, value in bios_settings.items():
                if current_attrs.get(key) != value:
                    changes_needed[key] = value

            if not changes_needed:
                logger.info(f"{device.intended_hostname}: BIOS already configured")
                device.state = DeviceState.BIOS_CONFIGURED
                return True

            logger.info(
                f"{device.intended_hostname}: Applying {len(changes_needed)} BIOS changes"
            )

            # Apply BIOS settings (pending reboot)
            client.patch(
                f"{REDFISH_SYSTEMS}/Bios/Settings",
                data={"Attributes": changes_needed},
            )

            # Reboot to apply BIOS changes
            self._reboot_server(client, device)

            # Wait for server to come back
            self._wait_for_server_post(client, device)

            device.state = DeviceState.BIOS_CONFIGURED
            logger.info(f"{device.intended_hostname}: BIOS configuration applied")
            return True

        except Exception as e:
            logger.error(f"{device.intended_hostname}: BIOS configuration failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _configure_raid(
        self, client: RedfishClient, device: DiscoveredDevice, raid_config: dict
    ) -> bool:
        """Configure RAID via Redfish Smart Storage."""
        logger.info(f"{device.intended_hostname}: Configuring RAID")
        device.state = DeviceState.RAID_CONFIGURING

        try:
            storage_path = f"{REDFISH_SYSTEMS}/SmartStorage/ArrayControllers"
            controllers = client.get(storage_path)

            members = controllers.get("Members", [])
            if not members:
                logger.error(f"{device.intended_hostname}: No storage controllers found")
                device.state = DeviceState.FAILED
                return False

            controller_uri = members[0].get("@odata.id")
            controller = client.get(controller_uri)
            logger.info(
                f"{device.intended_hostname}: Controller: {controller.get('Model', 'Unknown')}"
            )

            # Delete existing logical drives if requested
            if raid_config.get("clear_existing", False):
                self._clear_logical_drives(client, device, controller_uri)

            # Create logical drives
            for ld_config in raid_config.get("logical_drives", []):
                self._create_logical_drive(client, device, controller_uri, ld_config)

            # Reboot to apply storage changes
            self._reboot_server(client, device)
            self._wait_for_server_post(client, device)

            device.state = DeviceState.RAID_CONFIGURED
            logger.info(f"{device.intended_hostname}: RAID configuration applied")
            return True

        except Exception as e:
            logger.error(f"{device.intended_hostname}: RAID configuration failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _clear_logical_drives(
        self, client: RedfishClient, device: DiscoveredDevice, controller_uri: str
    ) -> None:
        """Delete all existing logical drives."""
        ld_collection = client.get(f"{controller_uri}/LogicalDrives")
        for member in ld_collection.get("Members", []):
            ld_uri = member.get("@odata.id")
            logger.info(f"{device.intended_hostname}: Deleting logical drive {ld_uri}")
            client.delete(ld_uri)

    def _create_logical_drive(
        self,
        client: RedfishClient,
        device: DiscoveredDevice,
        controller_uri: str,
        ld_config: dict,
    ) -> None:
        """Create a logical drive on the storage controller."""
        raid_level = ld_config.get("raid_level", "Raid1")
        drives = ld_config.get("drives", [])
        spare_drives = ld_config.get("spare_drives", [])
        name = ld_config.get("name", "LogicalDrive")

        payload = {
            "LogicalDriveName": name,
            "Raid": raid_level,
            "DataDrives": drives,
        }
        if spare_drives:
            payload["SpareDrives"] = spare_drives

        # Strip and accelerator settings
        if "strip_size_kb" in ld_config:
            payload["StripSizeBytes"] = ld_config["strip_size_kb"] * 1024
        if "accelerator" in ld_config:
            payload["Accelerator"] = ld_config["accelerator"]

        logger.info(
            f"{device.intended_hostname}: Creating {raid_level} array '{name}' "
            f"with {len(drives)} drives"
        )

        client.post(f"{controller_uri}/LogicalDrives", data=payload)

    def _install_spp(
        self, client: RedfishClient, device: DiscoveredDevice, spp_iso: str
    ) -> bool:
        """Install HPE Service Pack for ProLiant via virtual media."""
        logger.info(f"{device.intended_hostname}: Installing HPE SPP ({spp_iso})")
        device.state = DeviceState.DRIVER_PACK_INSTALLING

        try:
            iso_uri = f"http://{self.http_server}/{self.iso_dir}/{spp_iso}"

            # Mount SPP ISO via virtual media
            self._mount_virtual_media(client, device, iso_uri)

            # Set one-time boot to CD
            self._set_one_time_boot(client, "Cd")

            # Reboot to SPP
            self._reboot_server(client, device)

            # Wait for SPP to complete (this can take 30-60 minutes)
            logger.info(
                f"{device.intended_hostname}: Waiting for SPP installation "
                f"(this may take up to 60 minutes)..."
            )
            time.sleep(300)  # Initial wait for SPP to start
            self._wait_for_server_post(client, device, timeout=LONG_OP_TIMEOUT)

            # Unmount ISO
            self._unmount_virtual_media(client, device)

            device.state = DeviceState.DRIVER_PACK_INSTALLED
            logger.info(f"{device.intended_hostname}: HPE SPP installation complete")
            return True

        except Exception as e:
            logger.error(f"{device.intended_hostname}: SPP installation failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _install_os(
        self,
        client: RedfishClient,
        device: DiscoveredDevice,
        os_iso: str,
        spec: dict,
    ) -> bool:
        """Install OS via virtual media boot."""
        logger.info(f"{device.intended_hostname}: Installing OS ({os_iso})")
        device.state = DeviceState.OS_INSTALLING

        try:
            iso_uri = f"http://{self.http_server}/{self.iso_dir}/{os_iso}"

            # Mount OS ISO via virtual media
            self._mount_virtual_media(client, device, iso_uri)

            # Mount kickstart/preseed if specified
            kickstart_iso = spec.get("kickstart_iso")
            if kickstart_iso:
                ks_uri = f"http://{self.http_server}/{self.iso_dir}/{kickstart_iso}"
                self._mount_virtual_media(client, device, ks_uri, media_index=1)

            # Set one-time boot to CD
            self._set_one_time_boot(client, "Cd")

            # Reboot to OS installer
            self._reboot_server(client, device)

            # Wait for OS installation (can take 15-45 minutes)
            logger.info(
                f"{device.intended_hostname}: Waiting for OS installation "
                f"(this may take up to 45 minutes)..."
            )
            time.sleep(300)  # Initial wait for installer
            self._wait_for_os_install(client, device, timeout=LONG_OP_TIMEOUT)

            # Unmount ISOs
            self._unmount_virtual_media(client, device)

            device.state = DeviceState.OS_INSTALLED
            logger.info(f"{device.intended_hostname}: OS installation complete")
            return True

        except Exception as e:
            logger.error(f"{device.intended_hostname}: OS installation failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _configure_ilo(
        self,
        client: RedfishClient,
        device: DiscoveredDevice,
        ilo_config: dict,
    ) -> bool:
        """Apply production iLO configuration (networking, users, alerts)."""
        logger.info(f"{device.intended_hostname}: Configuring iLO for production")
        device.state = DeviceState.BMC_CONFIGURING

        try:
            # Configure iLO network settings
            network_config = ilo_config.get("network", {})
            if network_config:
                self._configure_ilo_network(client, device, network_config)

            # Configure iLO user accounts
            users = ilo_config.get("users", [])
            for user_config in users:
                self._configure_ilo_user(client, device, user_config)

            # Remove default administrator if a replacement is configured
            if ilo_config.get("remove_default_admin", False) and users:
                self._remove_default_admin(client, device)

            # Configure SNMP alerts
            snmp_config = ilo_config.get("snmp", {})
            if snmp_config:
                self._configure_ilo_snmp(client, device, snmp_config)

            # Configure NTP
            ntp_config = ilo_config.get("ntp", {})
            if ntp_config:
                self._configure_ilo_ntp(client, device, ntp_config)

            # Set iLO hostname
            ilo_hostname = ilo_config.get("hostname")
            if ilo_hostname:
                client.patch(
                    f"{REDFISH_MANAGERS}/EthernetInterfaces/1",
                    data={"HostName": ilo_hostname},
                )

            device.state = DeviceState.BMC_CONFIGURED
            logger.info(f"{device.intended_hostname}: iLO configuration complete")
            return True

        except Exception as e:
            logger.error(f"{device.intended_hostname}: iLO configuration failed: {e}")
            device.state = DeviceState.FAILED
            return False

    def _configure_ilo_network(
        self, client: RedfishClient, device: DiscoveredDevice, network_config: dict
    ) -> None:
        """Configure iLO dedicated management network interface."""
        nic_payload: dict = {}

        if "ipv4" in network_config:
            ipv4 = network_config["ipv4"]
            nic_payload["IPv4Addresses"] = [{
                "Address": ipv4.get("address"),
                "SubnetMask": ipv4.get("subnet_mask"),
                "Gateway": ipv4.get("gateway"),
            }]

        if "dns_servers" in network_config:
            nic_payload["NameServers"] = network_config["dns_servers"]

        if "vlan_id" in network_config:
            nic_payload["VLAN"] = {
                "VLANEnable": True,
                "VLANId": network_config["vlan_id"],
            }

        if nic_payload:
            client.patch(
                f"{REDFISH_MANAGERS}/EthernetInterfaces/1",
                data=nic_payload,
            )
            logger.info(f"{device.intended_hostname}: iLO network configured")

    def _configure_ilo_user(
        self, client: RedfishClient, device: DiscoveredDevice, user_config: dict
    ) -> None:
        """Create or update an iLO user account."""
        username = user_config["username"]
        payload = {
            "UserName": username,
            "Password": user_config["password"],
            "Oem": {
                "Hpe": {
                    "LoginName": username,
                    "Privileges": {
                        "LoginPriv": user_config.get("login", True),
                        "RemoteConsolePriv": user_config.get("remote_console", True),
                        "VirtualMediaPriv": user_config.get("virtual_media", True),
                        "VirtualPowerAndResetPriv": user_config.get("power_reset", True),
                        "iLOConfigPriv": user_config.get("ilo_config", False),
                        "UserConfigPriv": user_config.get("user_config", False),
                    },
                }
            },
        }

        # Try to find existing user
        accounts = client.get(f"{REDFISH_BASE}/AccountService/Accounts")
        for member in accounts.get("Members", []):
            account = client.get(member["@odata.id"])
            if account.get("UserName") == username:
                client.patch(member["@odata.id"], data=payload)
                logger.info(f"{device.intended_hostname}: Updated iLO user {username}")
                return

        # Create new user
        client.post(f"{REDFISH_BASE}/AccountService/Accounts", data=payload)
        logger.info(f"{device.intended_hostname}: Created iLO user {username}")

    def _remove_default_admin(
        self, client: RedfishClient, device: DiscoveredDevice
    ) -> None:
        """Remove the default Administrator account."""
        accounts = client.get(f"{REDFISH_BASE}/AccountService/Accounts")
        for member in accounts.get("Members", []):
            account = client.get(member["@odata.id"])
            if account.get("UserName") == ILO_DEFAULT_USER:
                client.delete(member["@odata.id"])
                logger.info(
                    f"{device.intended_hostname}: Removed default admin account"
                )
                return

    def _configure_ilo_snmp(
        self, client: RedfishClient, device: DiscoveredDevice, snmp_config: dict
    ) -> None:
        """Configure SNMP alerting on iLO."""
        payload = {}
        if "community" in snmp_config:
            payload["ReadCommunity"] = snmp_config["community"]
        if "trap_destinations" in snmp_config:
            payload["AlertDestinations"] = snmp_config["trap_destinations"]
        if "system_contact" in snmp_config:
            payload["SystemContact"] = snmp_config["system_contact"]
        if "system_location" in snmp_config:
            payload["SystemLocation"] = snmp_config["system_location"]

        if payload:
            client.patch(f"{REDFISH_MANAGERS}/SnmpService", data=payload)
            logger.info(f"{device.intended_hostname}: iLO SNMP configured")

    def _configure_ilo_ntp(
        self, client: RedfishClient, device: DiscoveredDevice, ntp_config: dict
    ) -> None:
        """Configure NTP on iLO."""
        payload = {
            "Oem": {
                "Hpe": {
                    "IPv4Addresses": ntp_config.get("servers", []),
                    "PropagateTimeToHost": ntp_config.get("propagate_to_host", True),
                }
            }
        }
        client.patch(f"{REDFISH_MANAGERS}/DateTime", data=payload)
        logger.info(f"{device.intended_hostname}: iLO NTP configured")

    # --- Helper methods ---

    def _mount_virtual_media(
        self,
        client: RedfishClient,
        device: DiscoveredDevice,
        iso_uri: str,
        media_index: int = 0,
    ) -> None:
        """Mount an ISO image via iLO virtual media."""
        vm_path = f"{REDFISH_MANAGERS}/VirtualMedia/{media_index + 2}"
        client.patch(vm_path, data={
            "Image": iso_uri,
            "Oem": {"Hpe": {"BootOnNextServerReset": True}},
        })
        logger.info(f"{device.intended_hostname}: Mounted virtual media: {iso_uri}")

    def _unmount_virtual_media(
        self, client: RedfishClient, device: DiscoveredDevice
    ) -> None:
        """Unmount all virtual media."""
        vm_collection = client.get(f"{REDFISH_MANAGERS}/VirtualMedia")
        for member in vm_collection.get("Members", []):
            vm = client.get(member["@odata.id"])
            if vm.get("Inserted"):
                client.patch(member["@odata.id"], data={"Image": ""})
        logger.info(f"{device.intended_hostname}: Virtual media unmounted")

    def _set_one_time_boot(self, client: RedfishClient, boot_target: str) -> None:
        """Set one-time boot override."""
        client.patch(REDFISH_SYSTEMS, data={
            "Boot": {
                "BootSourceOverrideTarget": boot_target,
                "BootSourceOverrideEnabled": "Once",
            }
        })

    def _reboot_server(
        self, client: RedfishClient, device: DiscoveredDevice
    ) -> None:
        """Graceful reboot via Redfish."""
        try:
            # Try graceful shutdown first
            client.post(
                f"{REDFISH_SYSTEMS}/Actions/ComputerSystem.Reset",
                data={"ResetType": "GracefulRestart"},
            )
        except requests.exceptions.HTTPError:
            # Fall back to force restart
            client.post(
                f"{REDFISH_SYSTEMS}/Actions/ComputerSystem.Reset",
                data={"ResetType": "ForceRestart"},
            )
        logger.info(f"{device.intended_hostname}: Server rebooting")

    def _wait_for_server_post(
        self,
        client: RedfishClient,
        device: DiscoveredDevice,
        timeout: int = 600,
    ) -> bool:
        """Wait for the server to complete POST after reboot."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                system = client.get(REDFISH_SYSTEMS)
                power_state = system.get("PowerState", "")
                post_state = system.get("Oem", {}).get("Hpe", {}).get("PostState", "")

                if power_state == "On" and post_state in (
                        "FinishedPost", "InPostDiscoveryComplete"
                    ):
                    logger.info(f"{device.intended_hostname}: Server POST complete")
                    return True
            except Exception:
                pass  # iLO may be temporarily unreachable during reboot
            time.sleep(POLL_INTERVAL)

        logger.warning(
            f"{device.intended_hostname}: Server POST did not complete within {timeout}s"
        )
        return False

    def _wait_for_os_install(
        self,
        client: RedfishClient,
        device: DiscoveredDevice,
        timeout: int = LONG_OP_TIMEOUT,
    ) -> bool:
        """Wait for OS installation to complete by monitoring server state."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                system = client.get(REDFISH_SYSTEMS)
                power_state = system.get("PowerState", "")
                post_state = system.get("Oem", {}).get("Hpe", {}).get("PostState", "")

                # After OS install, server typically reboots and finishes POST
                if power_state == "On" and post_state in (
                        "FinishedPost", "InPostDiscoveryComplete"
                    ):
                    # Check if virtual media is still connected (installer still running)
                    vm_collection = client.get(f"{REDFISH_MANAGERS}/VirtualMedia")
                    still_booting_from_iso = False
                    for member in vm_collection.get("Members", []):
                        vm = client.get(member["@odata.id"])
                        if vm.get("Inserted") and vm.get("BootOnNextReset"):
                            still_booting_from_iso = True
                            break

                    if not still_booting_from_iso:
                        logger.info(
                            f"{device.intended_hostname}: OS installation appears complete"
                        )
                        return True

            except Exception:
                logger.debug(
                    "%s: OS install poll error, retrying", device.intended_hostname,
                )
            time.sleep(POLL_INTERVAL)

        logger.warning(
            f"{device.intended_hostname}: OS install did not complete within {timeout}s"
        )
        return False

    def _wait_for_ilo(self, client: RedfishClient, ip: str, timeout: int = 300) -> bool:
        """Wait for iLO to become responsive after a firmware update."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                client.get(REDFISH_BASE)
                return True
            except Exception:
                logger.debug("iLO at %s not yet responsive, retrying", ip)
                time.sleep(15)

        logger.warning(f"iLO at {ip} did not respond within {timeout}s")
        return False

    def _wait_for_task(
        self,
        client: RedfishClient,
        device: DiscoveredDevice,
        task_uri: str,
        description: str,
        timeout: int = LONG_OP_TIMEOUT,
    ) -> bool:
        """Poll a Redfish task until completion."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                task = client.get(task_uri)
                state = task.get("TaskState", "")

                if state == "Completed":
                    logger.info(f"{device.intended_hostname}: {description} completed")
                    return True
                elif state in ("Exception", "Killed"):
                    logger.error(
                        f"{device.intended_hostname}: {description} failed: "
                        f"{task.get('Messages', [])}"
                    )
                    return False

            except Exception:
                logger.debug(
                    "%s: %s poll error, retrying", device.intended_hostname, description,
                )
            time.sleep(POLL_INTERVAL)

        logger.warning(
            f"{device.intended_hostname}: {description} timed out after {timeout}s"
        )
        return False
