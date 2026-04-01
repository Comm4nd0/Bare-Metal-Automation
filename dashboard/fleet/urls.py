"""URL routing for the fleet app."""

from django.urls import path

from . import views

app_name = "fleet"

urlpatterns = [
    path("", views.fleet_index, name="index"),
    path("sites/<slug:site_slug>/", views.site_detail, name="site_detail"),
    path("scans/<int:scan_id>/", views.scan_detail, name="scan_detail"),
]
