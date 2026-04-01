"""URL routing for the deploy REST API."""

from rest_framework.routers import DefaultRouter

from .views import (
    DeploymentDeviceViewSet,
    DeploymentPhaseViewSet,
    DeploymentViewSet,
    FactoryResetViewSet,
)

router = DefaultRouter()
router.register("deployments", DeploymentViewSet, basename="deployment")
router.register("phases", DeploymentPhaseViewSet, basename="phase")
router.register("devices", DeploymentDeviceViewSet, basename="device")
router.register("resets", FactoryResetViewSet, basename="reset")

urlpatterns = router.urls
