"""Inventory validation service — validate inventory.yaml before deployment.

Parses the inventory file, runs schema and content checks, and returns
a structured report that the dashboard can display to the user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from bare_metal_automation.inventory import DeviceSpec, InventorySchema
from bare_metal_automation.models import DevicePlatform, DeviceRole
from bare_metal_automation.settings import FIRMWARE_DIR, TEMPLATE_DIR

logger = logging.getLogger(__name__)


@dataclass
class ValidationCheck:
    """Result of a single validation check."""

    name: str
    status: str  # "pass", "fail", "warning"
    message: str
    details: list[str] = field(default_factory=list)


@dataclass
class InventoryValidationReport:
    """Full validation report for an inventory file."""

    checks: list[ValidationCheck] = field(default_factory=list)
    inventory_data: dict | None = None
    devices: list[dict] | None = None
    passed: bool = False

    def to_dict(self) -> dict:
        """Serialise for JSON response."""
        return {
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "message": c.message,
                    "details": c.details,
                }
                for c in self.checks
            ],
            "inventory_data": self.inventory_data,
            "devices": self.devices,
            "passed": self.passed,
        }


def validate_inventory(path: Path) -> InventoryValidationReport:
    """Run all validation checks against an inventory file.

    Never raises — all errors are captured in the report.
    """
    report = InventoryValidationReport()

    # ── Check 1: File exists ─────────────────────────────────────────────
    if not path.exists():
        report.checks.append(ValidationCheck(
            name="File Exists",
            status="fail",
            message=f"Inventory file not found: {path}",
        ))
        return report

    report.checks.append(ValidationCheck(
        name="File Exists",
        status="pass",
        message=f"Found {path}",
    ))

    # ── Check 2: YAML parseable ──────────────────────────────────────────
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        report.checks.append(ValidationCheck(
            name="YAML Parseable",
            status="fail",
            message="Failed to parse YAML",
            details=[str(exc)],
        ))
        return report

    if not isinstance(raw, dict):
        report.checks.append(ValidationCheck(
            name="YAML Parseable",
            status="fail",
            message=f"Expected YAML mapping, got {type(raw).__name__}",
        ))
        return report

    report.checks.append(ValidationCheck(
        name="YAML Parseable",
        status="pass",
        message="Valid YAML document",
    ))

    # ── Check 3: Schema validation ───────────────────────────────────────
    try:
        schema = InventorySchema(**raw)
    except ValidationError as exc:
        details = []
        for err in exc.errors():
            loc = " → ".join(str(p) for p in err["loc"])
            details.append(f"{loc}: {err['msg']}")
        report.checks.append(ValidationCheck(
            name="Schema Validation",
            status="fail",
            message=f"{len(exc.errors())} schema error(s)",
            details=details,
        ))
        return report
    except Exception as exc:
        report.checks.append(ValidationCheck(
            name="Schema Validation",
            status="fail",
            message=f"Unexpected error: {exc}",
        ))
        return report

    report.checks.append(ValidationCheck(
        name="Schema Validation",
        status="pass",
        message="Inventory matches expected schema",
    ))

    # ── Check 4: Deployment fields ───────────────────────────────────────
    dep = schema.deployment
    missing_fields = []
    for field_name in ("name", "bootstrap_subnet", "laptop_ip", "management_vlan"):
        val = dep.get(field_name)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing_fields.append(field_name)

    if missing_fields:
        report.checks.append(ValidationCheck(
            name="Deployment Fields",
            status="fail",
            message=f"Missing or empty: {', '.join(missing_fields)}",
        ))
    else:
        report.checks.append(ValidationCheck(
            name="Deployment Fields",
            status="pass",
            message=(
                f"{dep['name']} — subnet {dep['bootstrap_subnet']}, "
                f"laptop {dep['laptop_ip']}, VLAN {dep['management_vlan']}"
            ),
        ))
        report.inventory_data = {
            "name": dep["name"],
            "bootstrap_subnet": dep["bootstrap_subnet"],
            "laptop_ip": dep["laptop_ip"],
            "management_vlan": dep["management_vlan"],
        }

    # ── Check 5: Devices present ─────────────────────────────────────────
    devices_raw = schema.devices
    if not devices_raw:
        report.checks.append(ValidationCheck(
            name="Devices Present",
            status="fail",
            message="No devices defined in inventory",
        ))
        return report

    report.checks.append(ValidationCheck(
        name="Devices Present",
        status="pass",
        message=f"{len(devices_raw)} device(s) defined",
    ))

    # ── Check 6: Per-device validation ───────────────────────────────────
    valid_roles = {r.value for r in DeviceRole}
    valid_platforms = {p.value for p in DevicePlatform}
    device_errors: list[str] = []
    device_warnings: list[str] = []
    device_list: list[dict] = []

    for serial, spec_raw in devices_raw.items():
        issues: list[str] = []

        # Validate via Pydantic DeviceSpec
        try:
            spec = DeviceSpec(**spec_raw)
        except ValidationError as exc:
            for err in exc.errors():
                loc = " → ".join(str(p) for p in err["loc"])
                issues.append(f"{loc}: {err['msg']}")
            device_errors.append(f"{serial}: {', '.join(issues)}")
            device_list.append({
                "serial": serial,
                "hostname": spec_raw.get("hostname", "?"),
                "role": spec_raw.get("role", "?"),
                "platform": spec_raw.get("platform", "?"),
                "template": spec_raw.get("template", ""),
                "firmware_image": spec_raw.get("firmware_image", ""),
                "status": "fail",
                "issues": issues,
            })
            continue

        # Check role validity
        if spec.role and spec.role not in valid_roles:
            issues.append(f"Unknown role: {spec.role}")

        # Check platform validity
        if spec.platform and spec.platform not in valid_platforms:
            # Not a built-in platform — warn but don't fail
            # (could be a custom driver)
            device_warnings.append(
                f"{serial} ({spec.hostname}): non-standard platform '{spec.platform}'"
            )

        if issues:
            device_errors.extend(f"{serial} ({spec.hostname}): {i}" for i in issues)

        device_list.append({
            "serial": serial,
            "hostname": spec.hostname,
            "role": spec.role,
            "platform": spec.platform,
            "template": spec.template,
            "firmware_image": spec.firmware_image or "",
            "firmware_version": spec.firmware_version or "",
            "status": "fail" if issues else "pass",
            "issues": issues,
        })

    if device_errors:
        report.checks.append(ValidationCheck(
            name="Device Validation",
            status="fail",
            message=f"{len(device_errors)} device error(s)",
            details=device_errors,
        ))
    elif device_warnings:
        report.checks.append(ValidationCheck(
            name="Device Validation",
            status="warning",
            message=f"All devices valid ({len(device_warnings)} warning(s))",
            details=device_warnings,
        ))
    else:
        report.checks.append(ValidationCheck(
            name="Device Validation",
            status="pass",
            message=f"All {len(device_list)} device(s) valid",
        ))

    report.devices = device_list

    # ── Check 7: Template files exist ────────────────────────────────────
    template_missing: list[str] = []
    for dev in device_list:
        tmpl = dev.get("template", "")
        if tmpl:
            tmpl_path = Path(TEMPLATE_DIR) / tmpl
            if not tmpl_path.exists():
                template_missing.append(f"{dev['serial']} ({dev['hostname']}): {tmpl}")

    if template_missing:
        report.checks.append(ValidationCheck(
            name="Template Files",
            status="warning",
            message=f"{len(template_missing)} template file(s) not found",
            details=template_missing,
        ))
    else:
        report.checks.append(ValidationCheck(
            name="Template Files",
            status="pass",
            message="All referenced template files found",
        ))

    # ── Check 8: Firmware files exist ────────────────────────────────────
    firmware_missing: list[str] = []
    for dev in device_list:
        fw = dev.get("firmware_image", "")
        if fw:
            fw_path = Path(FIRMWARE_DIR) / fw
            if not fw_path.exists():
                firmware_missing.append(f"{dev['serial']} ({dev['hostname']}): {fw}")

    if firmware_missing:
        report.checks.append(ValidationCheck(
            name="Firmware Files",
            status="warning",
            message=f"{len(firmware_missing)} firmware file(s) not found",
            details=firmware_missing,
        ))
    else:
        report.checks.append(ValidationCheck(
            name="Firmware Files",
            status="pass",
            message="All referenced firmware files found",
        ))

    # ── Overall result ───────────────────────────────────────────────────
    report.passed = all(c.status != "fail" for c in report.checks)

    return report
