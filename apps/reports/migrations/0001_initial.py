# Generated for telemetry analytics service phase 3.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("calibration", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalysisRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date_from", models.PositiveBigIntegerField(db_index=True)),
                ("date_to", models.PositiveBigIntegerField(db_index=True)),
                ("source", models.CharField(default="cli", max_length=32)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("calibration_table", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="analysis_runs", to="calibration.calibrationtable")),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="analysis_runs", to="calibration.vehicle")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="FuelEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("REFUEL", "Refuel"), ("DRAIN", "Drain")], max_length=16)),
                ("started_at", models.PositiveBigIntegerField(db_index=True)),
                ("ended_at", models.PositiveBigIntegerField(db_index=True)),
                ("volume_litres", models.FloatField()),
                ("start_level_litres", models.FloatField()),
                ("end_level_litres", models.FloatField()),
                ("confidence", models.FloatField(default=0.0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("analysis_run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fuel_events", to="reports.analysisrun")),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fuel_events", to="calibration.vehicle")),
            ],
            options={"ordering": ["started_at"]},
        ),
        migrations.CreateModel(
            name="SensorFault",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sensor_index", models.PositiveSmallIntegerField()),
                ("status", models.CharField(max_length=128)),
                ("reason", models.CharField(max_length=255)),
                ("started_at", models.PositiveBigIntegerField(blank=True, db_index=True, null=True)),
                ("ended_at", models.PositiveBigIntegerField(blank=True, null=True)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("analysis_run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sensor_faults", to="reports.analysisrun")),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sensor_faults", to="calibration.vehicle")),
            ],
            options={"ordering": ["started_at", "sensor_index"]},
        ),
        migrations.CreateModel(
            name="TelemetryLogPoint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_date", models.PositiveBigIntegerField(db_index=True)),
                ("speed", models.FloatField(default=0)),
                ("lls_codes", models.JSONField(default=list)),
                ("litres", models.FloatField(blank=True, null=True)),
                ("smoothed_litres", models.FloatField(blank=True, null=True)),
                ("analysis_run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="telemetry_points", to="reports.analysisrun")),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="telemetry_points", to="calibration.vehicle")),
            ],
            options={"ordering": ["event_date"]},
        ),
        migrations.AddIndex(
            model_name="analysisrun",
            index=models.Index(fields=["vehicle", "date_from", "date_to"], name="reports_ana_vehicle_b7b492_idx"),
        ),
        migrations.AddIndex(
            model_name="fuelevent",
            index=models.Index(fields=["vehicle", "event_type", "started_at"], name="reports_fue_vehicle_f1a77c_idx"),
        ),
        migrations.AddIndex(
            model_name="sensorfault",
            index=models.Index(fields=["vehicle", "sensor_index", "started_at"], name="reports_sen_vehicle_44e3cc_idx"),
        ),
        migrations.AddIndex(
            model_name="telemetrylogpoint",
            index=models.Index(fields=["vehicle", "event_date"], name="reports_tel_vehicle_78db3e_idx"),
        ),
    ]
