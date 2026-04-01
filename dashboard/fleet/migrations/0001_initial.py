"""Initial migration for the fleet app."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("deploy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TemplateRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("current_version", models.CharField(max_length=50)),
                ("previous_versions", models.JSONField(blank=True, default=list)),
                ("changelog", models.TextField(blank=True)),
                ("released_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="SiteRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("site_name", models.CharField(max_length=200)),
                ("site_slug", models.SlugField(max_length=100, unique=True)),
                ("location", models.CharField(blank=True, max_length=200)),
                ("contact", models.EmailField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("last_deployment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="fleet_site", to="deploy.deployment")),
            ],
            options={"ordering": ["site_name"]},
        ),
        migrations.CreateModel(
            name="FleetScan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scanned_at", models.DateTimeField(auto_now_add=True)),
                ("site_count", models.IntegerField(default=0)),
                ("compliant_count", models.IntegerField(default=0)),
                ("outdated_count", models.IntegerField(default=0)),
                ("unknown_count", models.IntegerField(default=0)),
                ("scan_report", models.JSONField(blank=True, default=dict)),
            ],
            options={"ordering": ["-scanned_at"]},
        ),
        migrations.CreateModel(
            name="SiteComplianceRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("template_name", models.CharField(max_length=200)),
                ("deployed_version", models.CharField(blank=True, max_length=50)),
                ("current_version", models.CharField(blank=True, max_length=50)),
                ("status", models.CharField(choices=[("compliant", "Compliant"), ("outdated", "Outdated"), ("unknown", "Unknown"), ("never_deployed", "Never Deployed")], db_index=True, default="unknown", max_length=20)),
                ("deployed_at", models.DateTimeField(blank=True, null=True)),
                ("drift_details", models.JSONField(blank=True, default=dict)),
                ("scan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="site_results", to="fleet.fleetscan")),
                ("site", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="compliance_records", to="fleet.siterecord")),
            ],
            options={"ordering": ["site__site_name"]},
        ),
        migrations.AddConstraint(
            model_name="templaterecord",
            constraint=models.UniqueConstraint(fields=["name", "current_version"], name="unique_template_version"),
        ),
        migrations.AddConstraint(
            model_name="sitecompliancerecord",
            constraint=models.UniqueConstraint(fields=["scan", "site"], name="unique_scan_site"),
        ),
    ]
