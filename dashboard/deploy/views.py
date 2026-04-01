"""Django views for the deploy app — phase tracker and device grid."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import Deployment, DeploymentPhase, DeploymentDevice


def index(request: HttpRequest) -> HttpResponse:
    """Landing page — list all deployments, most recent first."""
    deployments = Deployment.objects.select_related("operator").order_by("-ingested_at")[:20]
    return render(request, "deploy/index.html", {"deployments": deployments})


def deployment_detail(request: HttpRequest, deployment_id: int) -> HttpResponse:
    """
    Main phase tracker view for a single deployment.

    Shows the 11-phase pipeline (traffic lights) + the device grid.
    WebSocket JS connects to ws/deployments/<id>/ for live updates.
    """
    deployment = get_object_or_404(
        Deployment.objects.select_related("operator"),
        pk=deployment_id,
    )
    phases = deployment.phases.order_by("phase_number")
    devices = deployment.devices.select_related("current_phase").order_by("hostname")

    return render(
        request,
        "deploy/deployment_detail.html",
        {
            "deployment": deployment,
            "phases": phases,
            "devices": devices,
        },
    )


def phase_detail(request: HttpRequest, deployment_id: int, phase_number: int) -> HttpResponse:
    """
    Drill-down view for a single phase.

    Shows per-device status within the phase, post-config check results,
    error messages, and device logs.
    """
    deployment = get_object_or_404(Deployment, pk=deployment_id)
    phase = get_object_or_404(DeploymentPhase, deployment=deployment, phase_number=phase_number)

    # Devices that have (or had) this phase as their current_phase,
    # or all devices if the phase hasn't started yet.
    if phase.status in ("running", "completed", "warning", "failed"):
        phase_devices = DeploymentDevice.objects.filter(
            deployment=deployment,
            current_phase=phase,
        ).prefetch_related("logs")
    else:
        phase_devices = deployment.devices.all()

    return render(
        request,
        "deploy/phase_detail.html",
        {
            "deployment": deployment,
            "phase": phase,
            "phase_devices": phase_devices,
        },
    )
