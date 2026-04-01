"""URL configuration for Bare Metal Automation dashboard."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("deploy.api.urls")),
    path("fleet/", include("fleet.urls")),
    path("", include("deploy.urls")),
]
