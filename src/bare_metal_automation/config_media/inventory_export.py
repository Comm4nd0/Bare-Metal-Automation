"""Generate BMA inventory.yaml from NetBox device data.

Queries all devices tagged to a deployment node, maps them to the
BMA inventory format (serial → spec), and writes inventory.yaml to
the specified output directory.

Output format matches configs/inventory/inventory.example.yaml:

    deployment:
      name: "D001"
      bootstrap_subnet: "10.255.0.0/16"
      laptop_ip: "10.255.255.1"
      management_vlan: 100
    devices:
      <serial>:
        role: core-switch
        hostname: sw-core-01
        platform: cisco_ios
        template: switches/core.j2
        management_ip: 10.0.100.1
        management_subnet: 255.255.255.0
        firmware_image: c2960cx-universalk9-mz.152-7.E8.bin
        config_file: sw-core-01.cfg
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class InventoryExporter:
    """Export BMA inventory.yaml from NetBox-sourced device data.

    Args:
        output_dir: Directory where inventory.yaml will be written.
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

    def export(
        self,
        deployment_meta: dict[str, Any],
        device_specs: dict[str, dict[str, Any]],
        config_file_map: dict[str, str] | None = None,
        firmware_map: dict[str, str] | None = None,
        media_map: dict[str, dict[str, str]] | None = None,
    ) -> Path:
        """Write inventory.yaml from deployment metadata and per-device specs.

        Args:
            deployment_meta:  Dict with name, bootstrap_subnet, laptop_ip,
                              management_vlan (from mapper.map_deployment_metadata).
            device_specs:     Dict mapping serial → spec dict
                              (from mapper.map_device_to_spec for each device).
            config_file_map:  Optional dict mapping serial → rendered .cfg filename.
            firmware_map:     Optional dict mapping serial → firmware filename.
            media_map:        Optional dict mapping serial → {spp_iso, os_iso, ...}.

        Returns:
            Path to written inventory.yaml.
        """
        config_file_map = config_file_map or {}
        firmware_map = firmware_map or {}
        media_map = media_map or {}

        # Enrich device specs with bundle file references
        enriched: dict[str, dict[str, Any]] = {}
        for serial, spec in device_specs.items():
            entry = dict(spec)  # shallow copy

            if serial in config_file_map:
                entry["config_file"] = config_file_map[serial]

            if serial in firmware_map:
                # Only overwrite firmware_image if the spec doesn't already have one
                entry.setdefault("firmware_image", firmware_map[serial])

            if serial in media_map:
                for key, val in media_map[serial].items():
                    entry.setdefault(key, val)

            enriched[serial] = entry

        inventory = {
            "deployment": deployment_meta,
            "devices": enriched,
        }

        out_path = self.output_dir / "inventory.yaml"
        out_path.write_text(
            yaml.dump(inventory, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        logger.info(
            "Exported inventory.yaml with %d device(s) → %s",
            len(enriched),
            out_path,
        )
        return out_path

    @staticmethod
    def from_netbox(
        client: Any,
        mapper: Any,
        tag: str,
        output_dir: Path,
        laptop_ip: str = "",
    ) -> tuple["InventoryExporter", dict[str, Any], dict[str, dict[str, Any]]]:
        """Factory: fetch from NetBox, map, return (exporter, meta, specs).

        Convenience method that wires together NetBoxClient + mapper to
        produce ready-to-export data without the caller needing to know
        the pynetbox internals.

        Args:
            client:     NetBoxClient instance.
            mapper:     The bare_metal_automation.netbox.mapper module
                        (passed as module reference for testability).
            tag:        Node tag slug, e.g. "d001".
            output_dir: Where inventory.yaml will be written.
            laptop_ip:  Override laptop IP (otherwise derived from subnet).

        Returns:
            Tuple of (InventoryExporter, deployment_meta_dict, device_specs_dict).
        """
        # Fetch all devices for this tag
        devices = client.get_devices_by_tag(tag)

        # Fetch IPAM data for deployment-level metadata
        prefixes = client.get_prefixes_by_tag(tag)
        vlans = client.get_vlans_by_tag(tag)
        deployment_meta = mapper.map_deployment_metadata(
            tag, prefixes, vlans, laptop_ip=laptop_ip,
        )

        # Map each device to a spec
        device_specs: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        for device in devices:
            try:
                config_context = client.get_config_context(device.id)
                ip_addresses = client.get_device_ips(device.id)
                serial, spec = mapper.map_device_to_spec(
                    device, config_context, ip_addresses,
                )
                device_specs[serial] = spec
            except (ValueError, Exception) as e:
                msg = f"Device '{device.name}': {e}"
                logger.warning("Skipping device — %s", msg)
                errors.append(msg)

        if errors:
            logger.warning(
                "%d device(s) skipped due to mapping errors:\n%s",
                len(errors),
                "\n".join(f"  • {e}" for e in errors),
            )

        exporter = InventoryExporter(output_dir)
        return exporter, deployment_meta, device_specs
