"""Firmware management — catalog, upgrade testing, and compliance checking."""

from bare_metal_automation.firmware.catalog import FirmwareCatalog, FirmwareEntry
from bare_metal_automation.firmware.compliance import ComplianceChecker, ComplianceReport
from bare_metal_automation.firmware.tester import (
    FirmwareTestResult,
    FirmwareTestRunner,
    UpgradeTestOutcome,
    UpgradeTestPhase,
)

__all__ = [
    "FirmwareCatalog",
    "FirmwareEntry",
    "FirmwareTestRunner",
    "FirmwareTestResult",
    "UpgradeTestOutcome",
    "UpgradeTestPhase",
    "ComplianceChecker",
    "ComplianceReport",
]
