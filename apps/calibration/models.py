from __future__ import annotations

from django.db import models


class Vehicle(models.Model):
    """Vehicle known to the offline analytics system."""

    terminal_id = models.PositiveBigIntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    external_uuid = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "terminal_id"]

    def __str__(self) -> str:
        return f"{self.name} ({self.terminal_id})"


class CalibrationTable(models.Model):
    """Fuel-level sensor calibration grid attached to a single vehicle."""

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name="calibration_tables",
    )
    name = models.CharField(max_length=255)
    sensor_count = models.PositiveSmallIntegerField()
    source_filename = models.CharField(max_length=255, blank=True)
    raw_rows = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "-created_at"]
        indexes = [
            models.Index(fields=["vehicle", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} - {self.vehicle}"


class CalibrationPoint(models.Model):
    """One row of a calibration grid: sensor code and litres per tank."""

    table = models.ForeignKey(
        CalibrationTable,
        on_delete=models.CASCADE,
        related_name="points",
    )
    litres = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text="Sensor code from column 1 of the calibration file.",
    )
    sensor_codes = models.JSONField(
        help_text="Litres per tank from columns 2..N of the calibration file.",
    )
    row_number = models.PositiveIntegerField()

    class Meta:
        ordering = ["litres", "row_number"]
        unique_together = [("table", "row_number")]

    def __str__(self) -> str:
        return f"{self.table_id}: {self.litres} L -> {self.sensor_codes}"
