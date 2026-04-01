"""DRF viewsets for the deploy app API."""

from rest_framework import filters, mixins, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from deploy.models import (
    Deployment,
    DeploymentDevice,
    DeploymentPhase,
    DeviceLog,
    FactoryReset,
)

from .serializers import (
    DeploymentDetailSerializer,
    DeploymentDeviceSerializer,
    DeploymentListSerializer,
    DeploymentPhaseSerializer,
    DeviceLogSerializer,
    FactoryResetSerializer,
)


class DeploymentViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve deployments."""

    queryset = Deployment.objects.select_related("operator").order_by("-ingested_at")
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["ingested_at", "status", "site_name"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return DeploymentDetailSerializer
        return DeploymentListSerializer

    @action(detail=True, methods=["get"])
    def phases(self, request: Request, pk=None) -> Response:
        """Return all phases for a deployment."""
        deployment = self.get_object()
        phases = deployment.phases.all()
        serializer = DeploymentPhaseSerializer(phases, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def devices(self, request: Request, pk=None) -> Response:
        """Return all devices for a deployment."""
        deployment = self.get_object()
        devices = deployment.devices.select_related("current_phase").all()
        serializer = DeploymentDeviceSerializer(devices, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def factory_resets(self, request: Request, pk=None) -> Response:
        """Return all factory resets for a deployment."""
        deployment = self.get_object()
        resets = deployment.factory_resets.prefetch_related("phases", "certificates").all()
        serializer = FactoryResetSerializer(resets, many=True)
        return Response(serializer.data)


class DeploymentPhaseViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve deployment phases."""

    queryset = DeploymentPhase.objects.select_related("deployment").order_by("deployment", "phase_number")
    serializer_class = DeploymentPhaseSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["phase_number", "status"]


class DeploymentDeviceViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve deployment devices."""

    queryset = DeploymentDevice.objects.select_related("deployment", "current_phase").order_by("hostname")
    serializer_class = DeploymentDeviceSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["hostname", "status", "role"]

    @action(detail=True, methods=["get"])
    def logs(self, request: Request, pk=None) -> Response:
        """Return logs for a device, optionally filtered by phase."""
        device = self.get_object()
        qs = device.logs.order_by("timestamp")
        phase_id = request.query_params.get("phase")
        if phase_id:
            qs = qs.filter(phase_id=phase_id)
        serializer = DeviceLogSerializer(qs, many=True)
        return Response(serializer.data)


class FactoryResetViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve factory resets."""

    queryset = FactoryReset.objects.select_related("deployment", "operator").prefetch_related(
        "phases", "certificates"
    ).order_by("-started_at")
    serializer_class = FactoryResetSerializer
