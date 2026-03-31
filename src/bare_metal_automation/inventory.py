"""Inventory loader — parse and validate the deployment inventory YAML."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

from bare_metal_automation.models import DeploymentInventory


class DeviceSpec(BaseModel):
    """Schema for a single device in the inventory."""

    role: str
    hostname: str
    template: str
    platform: str
    # Network device firmware fields
    firmware_image: str | None = None
    firmware_version: str | None = None
    firmware_md5: str | None = None
    # HPE server fields
    ilo_firmware: str | None = None
    os_iso: str | None = None
    kickstart_iso: str | None = None
    spp_iso: str | None = None
    bios_settings: dict | None = None
    raid_config: dict | None = None
    ilo_config: dict | None = None
    # Meinberg NTP fields
    ntp_references: dict | None = None
    ntp_config: dict | None = None
    system_config: dict | None = None
    network_config: dict | None = None


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
