"""Initial migration for the deploy app."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Deployment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("site_name", models.CharField(max_length=200)),
                ("site_slug", models.SlugField(max_length=100)),
                ("template_name", models.CharField(max_length=200)),
                ("template_version", models.CharField(max_length=50)),
                ("bundle_path", models.CharField(blank=True, max_length=500)),
                ("manifest_hash", models.CharField(blank=True, help_text="SHA-256 of manifest.yaml", max_length=64)),
                ("ingested_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(choices=[("ingested", "Ingested"), ("running", "Running"), ("completed", "Completed"), ("failed", "Failed"), ("aborted", "Aborted")], db_index=True, default="ingested", max_length=20)),
                ("operator", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="deployments", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-ingested_at"]},
        ),
        migrations.CreateModel(
            name="DeploymentPhase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phase_number", models.IntegerField()),
                ("phase_name", models.CharField(max_length=100)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("completed", "Completed"), ("warning", "Warning"), ("failed", "Failed"), ("skipped", "Skipped")], db_index=True, default="pending", max_length=20)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("duration_seconds", models.FloatField(blank=True, null=True)),
                ("warning_count", models.IntegerField(default=0)),
                ("error_message", models.TextField(blank=True)),
                ("deployment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="phases", to="deploy.deployment")),
            ],
            options={"ordering": ["phase_number"]},
        ),
        migrations.CreateModel(
            name="DeploymentDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("serial_number", models.CharField(max_length=100)),
                ("hostname", models.CharField(max_length=200)),
                ("role", models.CharField(max_length=50)),
                ("platform", models.CharField(max_length=50)),
                ("config_path", models.CharField(blank=True, max_length=500)),
                ("firmware_path", models.CharField(blank=True, max_length=500)),
                ("os_media_path", models.CharField(blank=True, max_length=500)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("discovered", "Discovered"), ("cabling_validated", "Cabling Validated"), ("firmware_staged", "Firmware Staged"), ("configuring", "Configuring"), ("configured", "Configured"), ("provisioning", "Provisioning"), ("provisioned", "Provisioned"), ("verified", "Verified"), ("failed", "Failed"), ("missing", "Missing")], db_index=True, default="pending", max_length=30)),
                ("discovered_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("discovered_at", models.DateTimeField(blank=True, null=True)),
                ("configured_at", models.DateTimeField(blank=True, null=True)),
                ("provisioned_at", models.DateTimeField(blank=True, null=True)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True)),
                ("current_phase", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="active_devices", to="deploy.deploymentphase")),
                ("deployment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="devices", to="deploy.deployment")),
            ],
            options={"ordering": ["hostname"]},
        ),
        migrations.CreateModel(
            name="DeviceLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("timestamp", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("level", models.CharField(choices=[("DEBUG", "Debug"), ("INFO", "Info"), ("WARN", "Warning"), ("ERROR", "Error")], default="INFO", max_length=10)),
                ("message", models.TextField()),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="logs", to="deploy.deploymentdevice")),
                ("phase", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="device_logs", to="deploy.deploymentphase")),
            ],
            options={"ordering": ["timestamp"]},
        ),
        migrations.CreateModel(
            name="FactoryReset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(choices=[("running", "Running"), ("completed", "Completed"), ("failed", "Failed"), ("aborted", "Aborted")], db_index=True, default="running", max_length=20)),
                ("sanitisation_method", models.CharField(default="write-erase", max_length=100)),
                ("report", models.JSONField(blank=True, default=dict)),
                ("deployment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="factory_resets", to="deploy.deployment")),
                ("operator", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="factory_resets", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.CreateModel(
            name="ResetPhase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phase_number", models.IntegerField()),
                ("phase_name", models.CharField(max_length=100)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("completed", "Completed"), ("warning", "Warning"), ("failed", "Failed"), ("skipped", "Skipped")], default="pending", max_length=20)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("devices_reset", models.IntegerField(default=0)),
                ("devices_total", models.IntegerField(default=0)),
                ("log_output", models.TextField(blank=True)),
                ("reset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="phases", to="deploy.factoryreset")),
            ],
            options={"ordering": ["phase_number"]},
        ),
        migrations.CreateModel(
            name="DeviceResetCertificate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("serial_number", models.CharField(max_length=100)),
                ("sanitisation_method", models.CharField(max_length=100)),
                ("verified", models.BooleanField(default=False)),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                ("device", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reset_certificates", to="deploy.deploymentdevice")),
                ("operator", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="issued_certificates", to=settings.AUTH_USER_MODEL)),
                ("reset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="certificates", to="deploy.factoryreset")),
            ],
            options={"ordering": ["-timestamp"]},
        ),
        migrations.AddConstraint(
            model_name="deploymentphase",
            constraint=models.UniqueConstraint(fields=["deployment", "phase_number"], name="unique_deployment_phase"),
        ),
        migrations.AddConstraint(
            model_name="deploymentdevice",
            constraint=models.UniqueConstraint(fields=["deployment", "serial_number"], name="unique_deployment_device"),
        ),
        migrations.AddConstraint(
            model_name="resetphase",
            constraint=models.UniqueConstraint(fields=["reset", "phase_number"], name="unique_reset_phase"),
        ),
    ]
