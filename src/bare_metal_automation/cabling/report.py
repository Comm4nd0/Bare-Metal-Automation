"""Cabling validation report generator.

Produces both machine-readable (JSON) and human-readable summaries of
the cabling validation results for a deployment.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from bare_metal_automation.models import CablingResult

logger = logging.getLogger(__name__)

# Status categories
BLOCKING_STATUSES = frozenset({"wrong_device", "missing"})
WARNING_STATUSES = frozenset({"wrong_port", "unexpected"})
OK_STATUSES = frozenset({"correct", "adaptable"})

STATUS_SYMBOL = {
    "correct":      "✓",
    "adaptable":    "↔",
    "wrong_port":   "⚠",
    "wrong_device": "✗",
    "missing":      "✗",
    "unexpected":   "⚠",
}

STATUS_LABEL = {
    "correct":      "Correct",
    "adaptable":    "Adaptable",
    "wrong_port":   "Wrong port",
    "wrong_device": "Wrong device",
    "missing":      "Missing",
    "unexpected":   "Unexpected",
}


@dataclass
class DeviceCablingReport:
    serial: str
    hostname: str
    results: list[dict]   # serialisable CablingResult dicts
    correct: int = 0
    adaptable: int = 0
    warnings: int = 0
    errors: int = 0


@dataclass
class ValidationReport:
    """Structured report for a complete cabling validation run."""

    deployment_name: str
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    devices: list[DeviceCablingReport] = field(default_factory=list)

    # Aggregate counts
    total_correct: int = 0
    total_adaptable: int = 0
    total_warnings: int = 0
    total_errors: int = 0

    @property
    def blocking(self) -> bool:
        """True when there are issues that must be resolved before deploying."""
        return self.total_errors > 0

    @property
    def ready_to_deploy(self) -> bool:
        return not self.blocking

    def to_json(self, indent: int = 2) -> str:
        """Return a JSON string representation of this report."""
        data = {
            "deployment": self.deployment_name,
            "generated_at": self.generated_at,
            "summary": {
                "correct": self.total_correct,
                "adaptable": self.total_adaptable,
                "warnings": self.total_warnings,
                "errors": self.total_errors,
                "blocking": self.blocking,
                "ready_to_deploy": self.ready_to_deploy,
            },
            "devices": [asdict(d) for d in self.devices],
        }
        return json.dumps(data, indent=indent)

    def to_human_readable(self) -> str:
        """Return a plain-text summary suitable for console or log output."""
        lines: list[str] = [
            f"Cabling Validation Report — {self.deployment_name}",
            f"Generated: {self.generated_at}",
            "=" * 60,
        ]

        for device in self.devices:
            lines.append(f"\n{device.hostname} ({device.serial})")
            for r in device.results:
                symbol = STATUS_SYMBOL.get(r["status"], "?")
                label = STATUS_LABEL.get(r["status"], r["status"])
                lines.append(
                    f"  {symbol} {r['local_port']:30s}  {label:15s}  "
                    f"{r.get('message', '')}"
                )

        lines += [
            "",
            "─" * 60,
            f"  Correct:      {self.total_correct}",
            f"  Adaptable:    {self.total_adaptable}",
            f"  Warnings:     {self.total_warnings}",
            f"  Blocking:     {self.total_errors}",
        ]

        if self.blocking:
            lines.append(
                f"\n  *** {self.total_errors} BLOCKING ISSUE(S) — "
                f"deployment cannot proceed ***"
            )
        else:
            lines.append("\n  All checks passed — ready to deploy.")

        return "\n".join(lines)


def generate_report(
    deployment_name: str,
    results: dict[str, list[CablingResult]],
    serial_to_hostname: dict[str, str],
) -> ValidationReport:
    """Build a :class:`ValidationReport` from raw validation results.

    Args:
        deployment_name:   Human-readable deployment label.
        results:           ``{serial: [CablingResult, ...]}`` as returned by
                           the cabling validator.
        serial_to_hostname: Maps serial → intended hostname for display.

    Returns:
        Populated :class:`ValidationReport`.
    """
    report = ValidationReport(deployment_name=deployment_name)

    for serial, cabling_results in results.items():
        hostname = serial_to_hostname.get(serial, serial)

        device_report = DeviceCablingReport(
            serial=serial,
            hostname=hostname,
            results=[],
        )

        for r in cabling_results:
            device_report.results.append({
                "local_port": r.local_port,
                "status": r.status,
                "actual_remote": r.actual_remote or "",
                "actual_remote_port": r.actual_remote_port or "",
                "intended_remote": r.intended_remote or "",
                "intended_remote_port": r.intended_remote_port or "",
                "message": r.message or "",
            })

            if r.status in BLOCKING_STATUSES:
                device_report.errors += 1
                report.total_errors += 1
            elif r.status in WARNING_STATUSES:
                device_report.warnings += 1
                report.total_warnings += 1
            elif r.status == "adaptable":
                device_report.adaptable += 1
                report.total_adaptable += 1
            elif r.status == "correct":
                device_report.correct += 1
                report.total_correct += 1

        report.devices.append(device_report)

    return report
