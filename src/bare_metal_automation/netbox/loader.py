"""NetBox loader — query NetBox and return a DeploymentInventory.

This is the main integration point. It replaces the static YAML loader
with a live NetBox query while producing the identical output structure.
All downstream BMA components work unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from bare_metal_automation.models import DeploymentInventory
from bare_metal_automation.netbox.client import (
    NetBoxClient,
    NetBoxMappingError,
)
from bare_metal_automation.netbox.mapper import (
    map_deployment_metadata,
    map_device_to_spec,
)

logger = logging.getLogger(__name__)


class NetBoxLoader:
    """Loads deployment data from NetBox as a DeploymentInventory.

    Produces the identical structure to ``inventory.py:load_inventory()``,
    ensuring all downstream components work without changes.
    """

    def __init__(self, client: NetBoxClient) -> None:
        self.client = client

    def list_available_nodes(
        self, pattern: str = r"^D\d+$",
    ) -> list[dict[str, Any]]:
        """List deployable nodes available in NetBox.

        Returns a list of dicts with tag info and device counts,
        suitable for populating a dashboard dropdown.
        """
        return self.client.list_node_tags(pattern)

    def load_node(
        self,
        tag: str,
        laptop_ip: str = "",
    ) -> DeploymentInventory:
        """Load a complete node from NetBox as a DeploymentInventory.

        This is the main method. It:
        1. Fetches all devices tagged with the node tag
        2. Fetches config contexts and IPs for each device
        3. Fetches IPAM prefixes and VLANs
        4. Maps everything to BMA's inventory format
        5. Returns a DeploymentInventory ready for the orchestrator

        Args:
            tag: Node tag (e.g. "D001" or slug "d001").
            laptop_ip: Override laptop IP (default: derived from subnet).

        Returns:
            DeploymentInventory matching the YAML loader output.

        Raises:
            NetBoxMappingError: If device data is incomplete.
        """
        tag_slug = tag.lower()

        # Step 1: Fetch devices
        devices = self.client.get_devices_by_tag(tag_slug)
        logger.info("Fetched %d devices for node %s", len(devices), tag)

        # Step 2: Fetch config contexts and IPs per device
        device_specs: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        for device in devices:
            try:
                config_ctx = self.client.get_config_context(device.id)
                ips = self.client.get_device_ips(device.id)

                serial, spec = map_device_to_spec(
                    device, config_ctx, ips,
                )
                device_specs[serial] = spec

                logger.info(
                    "Mapped device %s (%s) — role=%s, platform=%s",
                    device.name,
                    serial,
                    spec.get("role"),
                    spec.get("platform"),
                )
            except ValueError as e:
                errors.append(str(e))
            except Exception as e:
                errors.append(
                    f"Device '{device.name}': unexpected error — {e}",
                )

        if errors:
            raise NetBoxMappingError(
                f"Failed to map {len(errors)} device(s):\n"
                + "\n".join(f"  • {e}" for e in errors),
            )

        # Step 3: Fetch IPAM data
        prefixes = self.client.get_prefixes_by_tag(tag_slug)
        vlans = self.client.get_vlans_by_tag(tag_slug)

        # Step 4: Map deployment metadata
        metadata = map_deployment_metadata(
            tag=tag,
            prefixes=prefixes,
            vlans=vlans,
            laptop_ip=laptop_ip,
        )

        # Step 5: Construct DeploymentInventory
        inventory = DeploymentInventory(
            name=metadata["name"],
            bootstrap_subnet=metadata["bootstrap_subnet"],
            laptop_ip=metadata["laptop_ip"],
            management_vlan=metadata["management_vlan"],
            devices=device_specs,
        )

        logger.info(
            "Loaded node %s: %d devices, subnet=%s, vlan=%d",
            tag,
            len(device_specs),
            inventory.bootstrap_subnet,
            inventory.management_vlan,
        )
        return inventory

    @staticmethod
    def save_inventory_yaml(
        inventory: DeploymentInventory,
        path: Path | str,
    ) -> Path:
        """Write the inventory to a YAML file.

        Generates a file identical in format to the manual
        inventory.yaml for debugging and inspection.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "deployment": {
                "name": inventory.name,
                "bootstrap_subnet": inventory.bootstrap_subnet,
                "laptop_ip": inventory.laptop_ip,
                "management_vlan": inventory.management_vlan,
            },
            "devices": inventory.devices,
        }

        with path.open("w") as f:
            yaml.dump(
                data,
                f,
                default_flow_style=False,
                sort_keys=False,
                width=120,
            )

        logger.info("Saved inventory YAML to %s", path)
        return path
