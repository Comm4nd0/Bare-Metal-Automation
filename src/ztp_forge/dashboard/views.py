"""Views for ZTP-Forge dashboard."""

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import CablingResult, Deployment, DeploymentLog, Device


# ── HTML Views ──────────────────────────────────────────────────────────────

def dashboard(request):
    """Main dashboard showing the latest deployment status."""
    deployment = Deployment.objects.first()
    if deployment is None:
        return render(request, "dashboard/no_deployment.html")

    devices = deployment.devices.all()
    logs = deployment.logs.all()[:50]

    cabling_stats = {}
    if deployment.phase not in ("pre_flight", "discovery", "topology"):
        for status_val, label in CablingResult.status.field.choices:
            count = CablingResult.objects.filter(
                device__deployment=deployment, status=status_val
            ).count()
            if count > 0:
                cabling_stats[label] = count

    return render(request, "dashboard/index.html", {
        "deployment": deployment,
        "devices": devices,
        "logs": logs,
        "cabling_stats": cabling_stats,
    })


def deployment_detail(request, pk):
    """Detail view for a specific deployment."""
    deployment = get_object_or_404(Deployment, pk=pk)
    devices = deployment.devices.all()
    logs = deployment.logs.all()[:100]

    return render(request, "dashboard/deployment_detail.html", {
        "deployment": deployment,
        "devices": devices,
        "logs": logs,
    })


def device_detail(request, pk):
    """Detail view for a single device including cabling results."""
    device = get_object_or_404(Device, pk=pk)
    cabling_results = device.cabling_results.all()

    return render(request, "dashboard/device_detail.html", {
        "device": device,
        "cabling_results": cabling_results,
    })


def deployment_list(request):
    """List all deployments."""
    deployments = Deployment.objects.all()
    return render(request, "dashboard/deployment_list.html", {
        "deployments": deployments,
    })


# ── Read API ────────────────────────────────────────────────────────────────

@require_GET
def api_status(request):
    """GET current deployment status (for dashboard polling)."""
    deployment = Deployment.objects.first()
    if deployment is None:
        return JsonResponse({"status": "no_deployment"})

    devices = []
    for d in deployment.devices.all():
        devices.append({
            "id": d.pk,
            "hostname": str(d),
            "ip": d.ip,
            "role": d.role,
            "state": d.state,
            "state_display": d.get_state_display(),
            "serial": d.serial,
            "platform": d.platform,
            "bfs_depth": d.bfs_depth,
            "config_order": d.config_order,
        })

    return JsonResponse({
        "deployment": {
            "id": deployment.pk,
            "name": deployment.name,
            "phase": deployment.phase,
            "phase_display": deployment.phase_display,
            "progress": deployment.phase_progress,
            "started_at": deployment.started_at.isoformat(),
            "updated_at": deployment.updated_at.isoformat(),
        },
        "devices": devices,
    })


@require_GET
def api_device_status(request, pk):
    """GET status for a single device."""
    device = get_object_or_404(Device, pk=pk)
    cabling = [
        {
            "local_port": c.local_port,
            "status": c.status,
            "actual_remote": c.actual_remote,
            "actual_remote_port": c.actual_remote_port,
            "intended_remote": c.intended_remote,
            "intended_remote_port": c.intended_remote_port,
            "message": c.message,
        }
        for c in device.cabling_results.all()
    ]
    return JsonResponse({
        "id": device.pk,
        "hostname": str(device),
        "ip": device.ip,
        "role": device.role,
        "state": device.state,
        "serial": device.serial,
        "platform": device.platform,
        "bfs_depth": device.bfs_depth,
        "config_order": device.config_order,
        "management_ip": device.management_ip,
        "cabling_results": cabling,
    })


# ── Write API (used by the automation process) ─────────────────────────────

def _parse_json_body(request):
    """Parse JSON from request body."""
    try:
        return json.loads(request.body), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)


@csrf_exempt
@require_POST
def api_create_deployment(request):
    """
    POST /api/deployments/
    Create a new deployment run.

    Body: {
        "name": "DC-Rack-42",
        "bootstrap_subnet": "10.255.0.0/16",
        "laptop_ip": "10.255.255.1",
        "management_vlan": 100
    }
    """
    data, err = _parse_json_body(request)
    if err:
        return err

    name = data.get("name")
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)

    deployment = Deployment.objects.create(
        name=name,
        bootstrap_subnet=data.get("bootstrap_subnet", ""),
        laptop_ip=data.get("laptop_ip", ""),
        management_vlan=data.get("management_vlan", 0),
    )
    return JsonResponse({"id": deployment.pk, "name": deployment.name}, status=201)


@csrf_exempt
@require_http_methods(["PUT", "PATCH"])
def api_update_deployment(request, pk):
    """
    PUT/PATCH /api/deployments/<id>/
    Update deployment phase and other fields.

    Body: {
        "phase": "discovery"
    }
    """
    deployment = get_object_or_404(Deployment, pk=pk)
    data, err = _parse_json_body(request)
    if err:
        return err

    allowed_fields = {"phase", "name", "bootstrap_subnet", "laptop_ip", "management_vlan"}
    for field_name in allowed_fields:
        if field_name in data:
            setattr(deployment, field_name, data[field_name])
    deployment.save()

    return JsonResponse({
        "id": deployment.pk,
        "phase": deployment.phase,
        "phase_display": deployment.phase_display,
        "progress": deployment.phase_progress,
    })


@csrf_exempt
@require_POST
def api_add_device(request, deployment_pk):
    """
    POST /api/deployments/<id>/devices/
    Register a discovered device.

    Body: {
        "ip": "10.255.0.10",
        "mac": "aa:bb:cc:dd:ee:ff",
        "serial": "FOC2145X0AB",
        "hostname": "Switch",
        "intended_hostname": "sw-core-01",
        "role": "core-switch",
        "platform": "cisco_ios",
        "state": "discovered",
        "bfs_depth": 1,
        "config_order": 1,
        "management_ip": "192.168.100.1"
    }
    """
    deployment = get_object_or_404(Deployment, pk=deployment_pk)
    data, err = _parse_json_body(request)
    if err:
        return err

    ip = data.get("ip")
    if not ip:
        return JsonResponse({"error": "ip is required"}, status=400)

    device, created = Device.objects.update_or_create(
        deployment=deployment,
        ip=ip,
        defaults={
            "mac": data.get("mac", ""),
            "serial": data.get("serial", ""),
            "hostname": data.get("hostname", ""),
            "intended_hostname": data.get("intended_hostname", ""),
            "role": data.get("role", ""),
            "platform": data.get("platform", ""),
            "state": data.get("state", "unknown"),
            "bfs_depth": data.get("bfs_depth"),
            "config_order": data.get("config_order"),
            "management_ip": data.get("management_ip", ""),
        },
    )

    return JsonResponse({
        "id": device.pk,
        "hostname": str(device),
        "created": created,
    }, status=201 if created else 200)


@csrf_exempt
@require_http_methods(["PUT", "PATCH"])
def api_update_device(request, pk):
    """
    PUT/PATCH /api/devices/<id>/
    Update a device's state and fields.

    Body: {
        "state": "configured"
    }
    """
    device = get_object_or_404(Device, pk=pk)
    data, err = _parse_json_body(request)
    if err:
        return err

    allowed_fields = {
        "state", "hostname", "intended_hostname", "role", "platform",
        "serial", "mac", "bfs_depth", "config_order", "management_ip",
    }
    for field_name in allowed_fields:
        if field_name in data:
            setattr(device, field_name, data[field_name])
    device.save()

    return JsonResponse({
        "id": device.pk,
        "hostname": str(device),
        "state": device.state,
    })


@csrf_exempt
@require_POST
def api_update_device_by_serial(request, deployment_pk, serial):
    """
    POST /api/deployments/<id>/devices/serial/<serial>/
    Update a device by serial number (handy for the automation process).

    Body: {
        "state": "configured"
    }
    """
    deployment = get_object_or_404(Deployment, pk=deployment_pk)
    device = get_object_or_404(Device, deployment=deployment, serial=serial)
    data, err = _parse_json_body(request)
    if err:
        return err

    allowed_fields = {
        "state", "hostname", "intended_hostname", "role", "platform",
        "bfs_depth", "config_order", "management_ip",
    }
    for field_name in allowed_fields:
        if field_name in data:
            setattr(device, field_name, data[field_name])
    device.save()

    return JsonResponse({
        "id": device.pk,
        "hostname": str(device),
        "state": device.state,
    })


@csrf_exempt
@require_POST
def api_add_cabling_results(request, device_pk):
    """
    POST /api/devices/<id>/cabling/
    Add cabling validation results for a device.

    Body: {
        "results": [
            {
                "local_port": "Gi1/0/1",
                "status": "correct",
                "actual_remote": "sw-access-01",
                "actual_remote_port": "Gi1/0/48",
                "intended_remote": "sw-access-01",
                "intended_remote_port": "Gi1/0/48",
                "message": "Connection verified"
            }
        ]
    }
    """
    device = get_object_or_404(Device, pk=device_pk)
    data, err = _parse_json_body(request)
    if err:
        return err

    results = data.get("results", [])
    if not results:
        return JsonResponse({"error": "results list is required"}, status=400)

    # Clear existing results for this device and replace
    device.cabling_results.all().delete()

    created = []
    for r in results:
        cr = CablingResult.objects.create(
            device=device,
            local_port=r.get("local_port", ""),
            status=r.get("status", "missing"),
            actual_remote=r.get("actual_remote", ""),
            actual_remote_port=r.get("actual_remote_port", ""),
            intended_remote=r.get("intended_remote", ""),
            intended_remote_port=r.get("intended_remote_port", ""),
            message=r.get("message", ""),
        )
        created.append(cr.pk)

    return JsonResponse({"created": len(created), "ids": created}, status=201)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_logs(request, deployment_pk):
    """
    GET  /api/deployments/<id>/logs/ — retrieve recent logs.
    POST /api/deployments/<id>/logs/ — add a log entry.

    POST Body: {
        "level": "INFO",
        "phase": "discovery",
        "message": "Found 5 devices on bootstrap network"
    }
    """
    deployment = get_object_or_404(Deployment, pk=deployment_pk)

    if request.method == "POST":
        data, err = _parse_json_body(request)
        if err:
            return err

        message = data.get("message")
        if not message:
            return JsonResponse({"error": "message is required"}, status=400)

        log = DeploymentLog.objects.create(
            deployment=deployment,
            level=data.get("level", "INFO"),
            phase=data.get("phase", ""),
            message=message,
        )
        return JsonResponse({"id": log.pk}, status=201)

    # GET
    limit = min(int(request.GET.get("limit", 50)), 200)
    logs = deployment.logs.all()[:limit]

    return JsonResponse({
        "logs": [
            {
                "id": log.pk,
                "level": log.level,
                "phase": log.phase,
                "message": log.message,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ]
    })
