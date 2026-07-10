# Generated for telemetry analytics service phase 3.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Vehicle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("terminal_id", models.PositiveBigIntegerField(db_index=True, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("external_uuid", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name", "terminal_id"]},
        ),
        migrations.CreateModel(
            name="CalibrationTable",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("sensor_count", models.PositiveSmallIntegerField()),
                ("source_filename", models.CharField(blank=True, max_length=255)),
                ("raw_rows", models.JSONField(default=list)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="calibration_tables", to="calibration.vehicle")),
            ],
            options={"ordering": ["-is_active", "-created_at"]},
        ),
        migrations.CreateModel(
            name="CalibrationPoint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("litres", models.DecimalField(decimal_places=3, max_digits=10)),
                ("sensor_codes", models.JSONField()),
                ("row_number", models.PositiveIntegerField()),
                ("table", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="points", to="calibration.calibrationtable")),
            ],
            options={
                "ordering": ["litres", "row_number"],
                "unique_together": {("table", "row_number")},
            },
        ),
        migrations.AddIndex(
            model_name="calibrationtable",
            index=models.Index(fields=["vehicle", "is_active"], name="calibration_vehicle_a83492_idx"),
        ),
    ]
