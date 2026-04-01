"""Dashboard API views for firmware management.

Provides endpoints for:
- Listing firmware catalog entries
- Running firmware compliance checks
- Starting firmware upgrade tests
- Viewing test results
"""

from __future__ import annotations

import json
import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import (
    Deployment,
    FirmwareComplianceSnapshot,
    FirmwareImage,
    FirmwareTestRun,
)

logger = logging.getLogger(__name__)


def _parse_json_body(request):
    """Parse JSON from request body."""
    try:
        return json.loads(request.body), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)


# ── Firmware Catalog API ──────────────────────────────────────────────────


@require_GET
def api_firmware_catalog(request):
    """GET /api/firmware/catalog/ — list all firmware images."""
    platform_filter = request.GET.get("platform", "")

    qs = FirmwareImage.objects.all()
    if platform_filter:
        qs = qs.filter(platform=platform_filter)

    images = []
    for img in qs:
        images.append({
            "id": img.pk,
            "platform": img.platform,
            "version": img.version,
            "filename": img.filename,
            "md5": img.md5,
            "min_version": img.min_version,
            "release_notes": img.release_notes,
            "recommended": img.recommended,
            "created_at": img.created_at.isoformat(),
        })

    # Group by platform for convenience
    platforms: dict[str, list] = {}
    for img in images:
        platforms.setdefault(img["platform"], []).append(img)

    return JsonResponse({
        "images": images,
        "platforms": platforms,
        "total": len(images),
    })


@csrf_exempt
@require_POST
def api_firmware_catalog_sync(request):
    """POST /api/firmware/catalog/sync/ — sync DB from catalog YAML file.

    Reads the firmware catalog YAML and upserts FirmwareImage records.
    """
    from bare_metal_automation.firmware.catalog import FirmwareCatalog
    from bare_metal_automation.settings import FIRMWARE_CATALOG

    try:
        catalog = FirmwareCatalog.from_yaml(FIRMWARE_CATALOG)
    except FileNotFoundError:
        return JsonResponse(
            {"error": f"Catalog file not found: {FIRMWARE_CATALOG}"},
            status=404,
        )

    created = 0
    updated = 0

    for platform, entries in catalog.entries.items():
        for entry in entries:
            _, was_created = FirmwareImage.objects.update_or_create(
                platform=platform,
                version=entry.version,
                defaults={
                    "filename": entry.filename,
                    "md5": entry.md5,
                    "min_version": entry.min_version,
                    "release_notes": entry.release_notes,
                    "recommended": entry.recommended,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

    return JsonResponse({
        "status": "synced",
        "created": created,
        "updated": updated,
        "total": created + updated,
    })


# ── Firmware Test Results API ─────────────────────────────────────────────


@require_GET
def api_firmware_tests(request):
    """GET /api/firmware/tests/ — list firmware test runs."""
    limit = min(int(request.GET.get("limit", 50)), 200)
    deployment_id = request.GET.get("deployment_id")

    qs = FirmwareTestRun.objects.all()
    if deployment_id:
        qs = qs.filter(deployment_id=deployment_id)

    tests = []
    for t in qs[:limit]:
        tests.append({
            "id": t.pk,
            "device_hostname": t.device_hostname,
            "device_ip": t.device_ip,
            "platform": t.platform,
            "previous_version": t.previous_version,
            "target_version": t.target_version,
            "final_version": t.final_version,
            "outcome": t.outcome,
            "phase_reached": t.phase_reached,
            "config_reapply_success": t.config_reapply_success,
            "pre_validation_passed": t.pre_validation_passed,
            "post_validation_passed": t.post_validation_passed,
            "duration_seconds": t.duration_seconds,
            "error_message": t.error_message,
            "findings": t.findings,
            "started_at": t.started_at.isoformat(),
        })

    return JsonResponse({"tests": tests, "total": len(tests)})


@require_GET
def api_firmware_test_detail(request, pk):
    """GET /api/firmware/tests/<id>/ — detail for a single test run."""
    test = get_object_or_404(FirmwareTestRun, pk=pk)
    return JsonResponse({
        "id": test.pk,
        "device_hostname": test.device_hostname,
        "device_ip": test.device_ip,
        "platform": test.platform,
        "previous_version": test.previous_version,
        "target_version": test.target_version,
        "final_version": test.final_version,
        "outcome": test.outcome,
        "phase_reached": test.phase_reached,
        "config_reapply_success": test.config_reapply_success,
        "pre_validation_passed": test.pre_validation_passed,
        "post_validation_passed": test.post_validation_passed,
        "duration_seconds": test.duration_seconds,
        "error_message": test.error_message,
        "findings": test.findings,
        "started_at": test.started_at.isoformat(),
    })


@csrf_exempt
@require_POST
def api_record_firmware_test(request):
    """POST /api/firmware/tests/ — record a firmware test result.

    Body: {
        "deployment_id": 1,
        "device_hostname": "sw-core-01",
        "device_ip": "10.255.0.10",
        "platform": "cisco_iosxe",
        "previous_version": "17.06.01",
        "target_version": "17.09.04a",
        "final_version": "17.09.04a",
        "outcome": "passed",
        "phase_reached": "complete",
        "config_reapply_success": true,
        "pre_validation_passed": true,
        "post_validation_passed": true,
        "duration_seconds": 342.5,
        "findings": ["...", "..."]
    }
    """
    data, err = _parse_json_body(request)
    if err:
        return err

    deployment = None
    if data.get("deployment_id"):
        deployment = Deployment.objects.filter(pk=data["deployment_id"]).first()

    test = FirmwareTestRun.objects.create(
        deployment=deployment,
        device_hostname=data.get("device_hostname", ""),
        device_ip=data.get("device_ip", "0.0.0.0"),
        platform=data.get("platform", ""),
        previous_version=data.get("previous_version", ""),
        target_version=data.get("target_version", ""),
        final_version=data.get("final_version", ""),
        outcome=data.get("outcome", "skipped"),
        phase_reached=data.get("phase_reached", "snapshot"),
        config_reapply_success=data.get("config_reapply_success", False),
        pre_validation_passed=data.get("pre_validation_passed"),
        post_validation_passed=data.get("post_validation_passed"),
        duration_seconds=data.get("duration_seconds", 0.0),
        error_message=data.get("error_message", ""),
        findings=data.get("findings", []),
    )

    return JsonResponse({"id": test.pk, "outcome": test.outcome}, status=201)


# ── Compliance API ────────────────────────────────────────────────────────


@require_GET
def api_firmware_compliance(request):
    """GET /api/firmware/compliance/ — latest compliance snapshot."""
    snapshot = FirmwareComplianceSnapshot.objects.first()
    if snapshot is None:
        return JsonResponse({"status": "no_data"})

    return JsonResponse({
        "total_devices": snapshot.total_devices,
        "compliant": snapshot.compliant_count,
        "upgrade_available": snapshot.upgrade_available_count,
        "blocked": snapshot.blocked_count,
        "unreachable": snapshot.unreachable_count,
        "compliance_percentage": snapshot.compliance_percentage,
        "details": snapshot.details,
        "created_at": snapshot.created_at.isoformat(),
    })


@csrf_exempt
@require_POST
def api_record_compliance(request):
    """POST /api/firmware/compliance/ — record a compliance snapshot.

    Body: {
        "deployment_id": 1,
        "total_devices": 10,
        "compliant": 7,
        "upgrade_available": 2,
        "blocked": 1,
        "unreachable": 0,
        "compliance_percentage": 70.0,
        "details": [...]
    }
    """
    data, err = _parse_json_body(request)
    if err:
        return err

    deployment = None
    if data.get("deployment_id"):
        deployment = Deployment.objects.filter(pk=data["deployment_id"]).first()

    snapshot = FirmwareComplianceSnapshot.objects.create(
        deployment=deployment,
        total_devices=data.get("total_devices", 0),
        compliant_count=data.get("compliant", 0),
        upgrade_available_count=data.get("upgrade_available", 0),
        blocked_count=data.get("blocked", 0),
        unreachable_count=data.get("unreachable", 0),
        compliance_percentage=data.get("compliance_percentage", 0.0),
        details=data.get("details", []),
    )

    return JsonResponse({"id": snapshot.pk}, status=201)
