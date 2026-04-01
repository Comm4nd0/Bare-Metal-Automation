"""Inventory loader — parse and validate the deployment inventory YAML."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator, model_validator  # noqa: I001

from bare_metal_automation.models import DeploymentInventory

# Legacy field names that should be migrated into vendor_config
_HPE_FIELDS = frozenset({
    "ilo_firmware", "os_iso", "kickstart_iso", "spp_iso",
    "bios_settings", "raid_config", "ilo_config",
})
_MEINBERG_FIELDS = frozenset({
    "ntp_references", "ntp_config", "system_config", "network_config",
})
_LEGACY_VENDOR_FIELDS = _HPE_FIELDS | _MEINBERG_FIELDS


class DeviceSpec(BaseModel):
    """Schema for a single device in the inventory."""

    role: str
    hostname: str
    template: str
    platform: str
    category: str | None = None
    # Generic firmware fields (all device types)
    firmware_image: str | None = None
    firmware_version: str | None = None
    firmware_md5: str | None = None
    # Vendor-specific config — drivers receive this dict as-is
    vendor_config: dict | None = None

    # Legacy fields kept for backward compatibility (migrated to vendor_config)
    ilo_firmware: str | None = None
    os_iso: str | None = None
    kickstart_iso: str | None = None
    spp_iso: str | None = None
    bios_settings: dict | None = None
    raid_config: dict | None = None
    ilo_config: dict | None = None
    ntp_references: dict | None = None
    ntp_config: dict | None = None
    system_config: dict | None = None
    network_config: dict | None = None

    @model_validator(mode="after")
    def _migrate_legacy_fields(self) -> DeviceSpec:
        """Move legacy vendor-specific top-level fields into vendor_config."""
        migrated: dict = {}
        for field_name in _LEGACY_VENDOR_FIELDS:
            value = getattr(self, field_name, None)
            if value is not None:
                migrated[field_name] = value
        if migrated:
            if self.vendor_config is None:
                self.vendor_config = migrated
            else:
                # Existing vendor_config takes precedence
                for k, v in migrated.items():
                    self.vendor_config.setdefault(k, v)
        return self


class InventorySchema(BaseModel):
    """Schema for the full inventory file."""

    deployment: dict
    devices: dict[str, dict]

    @field_validator("deployment")
    @classmethod
    def validate_deployment(cls, v: dict) -> dict:
        required = {"name", "bootstrap_subnet", "laptop_ip", "management_vlan"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing deployment fields: {missing}")
        return v


def load_inventory(path: Path) -> DeploymentInventory:
    """Load an inventory YAML file and return a DeploymentInventory."""
    if not path.exists():
        raise FileNotFoundError(f"Inventory file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    # Validate schema
    schema = InventorySchema(**raw)

    return DeploymentInventory(
        name=schema.deployment["name"],
        bootstrap_subnet=schema.deployment["bootstrap_subnet"],
        laptop_ip=schema.deployment["laptop_ip"],
        management_vlan=schema.deployment["management_vlan"],
        devices=schema.devices,
    )
