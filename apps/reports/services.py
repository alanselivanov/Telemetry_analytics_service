"""Persistence helpers for analytics results."""

from __future__ import annotations

from django.db import transaction

from analytics.engine import FuelAnalysisResult
from calibration.models import CalibrationTable, Vehicle

from .models import AnalysisRun, FuelEvent, SensorFault, TelemetryLogPoint


@transaction.atomic
def save_fuel_analysis_result(
    *,
    vehicle: Vehicle,
    calibration_table: CalibrationTable,
    date_from: int,
    date_to: int,
    result: FuelAnalysisResult,
    source: str = "cli",
    metadata: dict | None = None,
) -> AnalysisRun:
    """Persist telemetry points, fuel events, and sensor diagnostics."""
    analysis_run = AnalysisRun.objects.create(
        vehicle=vehicle,
        calibration_table=calibration_table,
        date_from=date_from,
        date_to=date_to,
        source=source,
        metadata=metadata or {},
    )

    TelemetryLogPoint.objects.bulk_create(
        [
            TelemetryLogPoint(
                analysis_run=analysis_run,
                vehicle=vehicle,
                event_date=point.timestamp,
                speed=point.speed,
                lls_codes=list(point.lls_codes),
                litres=point.litres,
                smoothed_litres=point.smoothed_litres,
            )
            for point in result.points
        ]
    )

    FuelEvent.objects.bulk_create(
        [
            FuelEvent(
                analysis_run=analysis_run,
                vehicle=vehicle,
                event_type=event.event_type,
                started_at=event.started_at,
                ended_at=event.ended_at,
                volume_litres=event.volume_litres,
                start_level_litres=event.start_level_litres,
                end_level_litres=event.end_level_litres,
                confidence=event.confidence,
            )
            for event in (*result.refuels, *result.drains)
        ]
    )

    SensorFault.objects.bulk_create(
        [
            SensorFault(
                analysis_run=analysis_run,
                vehicle=vehicle,
                sensor_index=diagnostic.sensor_index,
                status=diagnostic.status,
                reason=diagnostic.reason,
                started_at=diagnostic.started_at,
                ended_at=diagnostic.ended_at,
                details=diagnostic.details,
            )
            for diagnostic in result.diagnostics
        ]
    )

    return analysis_run
