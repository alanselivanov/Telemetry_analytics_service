from __future__ import annotations

from django.db import models

from calibration.models import CalibrationTable, Vehicle


class AnalysisRun(models.Model):

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name="analysis_runs",
    )
    calibration_table = models.ForeignKey(
        CalibrationTable,
        on_delete=models.PROTECT,
        related_name="analysis_runs",
    )
    date_from = models.PositiveBigIntegerField(db_index=True)
    date_to = models.PositiveBigIntegerField(db_index=True)
    source = models.CharField(max_length=32, default="cli")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["vehicle", "date_from", "date_to"]),
        ]

    def __str__(self) -> str:
        return f"Analysis #{self.id} for {self.vehicle}"


class TelemetryLogPoint(models.Model):

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="telemetry_points",
    )
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name="telemetry_points",
    )
    event_date = models.PositiveBigIntegerField(db_index=True)
    speed = models.FloatField(default=0)
    lls_codes = models.JSONField(default=list)
    litres = models.FloatField(null=True, blank=True)
    smoothed_litres = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["event_date"]
        indexes = [
            models.Index(fields=["vehicle", "event_date"]),
        ]


class FuelEvent(models.Model):

    class EventType(models.TextChoices):
        REFUEL = "REFUEL", "Refuel"
        DRAIN = "DRAIN", "Drain"

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="fuel_events",
    )
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name="fuel_events",
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    started_at = models.PositiveBigIntegerField(db_index=True)
    ended_at = models.PositiveBigIntegerField(db_index=True)
    volume_litres = models.FloatField()
    start_level_litres = models.FloatField()
    end_level_litres = models.FloatField()
    confidence = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["started_at"]
        indexes = [
            models.Index(fields=["vehicle", "event_type", "started_at"]),
        ]


class SensorFault(models.Model):

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="sensor_faults",
    )
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name="sensor_faults",
    )
    sensor_index = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=128)
    reason = models.CharField(max_length=255)
    started_at = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    ended_at = models.PositiveBigIntegerField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["started_at", "sensor_index"]
        indexes = [
            models.Index(fields=["vehicle", "sensor_index", "started_at"]),
        ]