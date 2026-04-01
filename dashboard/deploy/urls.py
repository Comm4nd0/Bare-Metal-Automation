"""URL routing for the deploy app."""

from django.urls import path

from . import views

app_name = "deploy"

urlpatterns = [
    path("", views.index, name="index"),
    path("deployments/<int:deployment_id>/", views.deployment_detail, name="deployment_detail"),
    path(
        "deployments/<int:deployment_id>/phases/<int:phase_number>/",
        views.phase_detail,
        name="phase_detail",
    ),
]
