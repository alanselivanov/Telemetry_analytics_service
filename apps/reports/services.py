"""Persistence helpers for analytics results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db import models, transaction
from django.shortcuts import get_object_or_404

from analytics.engine import FuelAnalysisResult
from calibration.models import CalibrationTable, Vehicle

from .models import AnalysisRun, FuelEvent, SensorFault, TelemetryLogPoint

if TYPE_CHECKING:
    from analytics.services import FuelBalance


@dataclass(frozen=True)
class VehicleHistoricalReport:
    """Aggregated persisted analytics for one vehicle and period."""

    vehicle: Vehicle
    date_from: int
    date_to: int
    analysis_runs: tuple[AnalysisRun, ...]
    refuels: tuple[FuelEvent, ...]
    drains: tuple[FuelEvent, ...]
    sensor_faults: tuple[SensorFault, ...]
    telemetry_points: tuple[TelemetryLogPoint, ...]
    balance: "FuelBalance"
    summary: dict[str, Any]


def resolve_vehicle(vehicle_id: int) -> Vehicle:
    """Resolve a vehicle by database id or Omnicomm terminal_id."""
    vehicle = Vehicle.objects.filter(pk=vehicle_id).first()
    if vehicle is not None:
        return vehicle
    return get_object_or_404(Vehicle, terminal_id=vehicle_id)


def get_vehicle_historical_report(
    vehicle_id: int,
    *,
    date_from: int,
    date_to: int,
    include_telemetry: bool = False,
    telemetry_limit: int = 5000,
) -> VehicleHistoricalReport:
    """Load persisted analytics for a vehicle that overlap the requested period."""
    vehicle = resolve_vehicle(vehicle_id)

    analysis_runs = tuple(
        AnalysisRun.objects.filter(
            vehicle=vehicle,
            date_from__lt=date_to,
            date_to__gt=date_from,
        )
        .select_related("calibration_table")
        .order_by("-created_at")
    )

    refuels = tuple(
        FuelEvent.objects.filter(
            vehicle=vehicle,
            event_type=FuelEvent.EventType.REFUEL,
            started_at__lt=date_to,
            ended_at__gt=date_from,
        ).order_by("started_at")
    )
    drains = tuple(
        FuelEvent.objects.filter(
            vehicle=vehicle,
            event_type=FuelEvent.EventType.DRAIN,
            started_at__lt=date_to,
            ended_at__gt=date_from,
        ).order_by("started_at")
    )
    sensor_faults = tuple(
        SensorFault.objects.filter(
            vehicle=vehicle,
            started_at__isnull=False,
            started_at__lt=date_to,
        )
        .filter(models.Q(ended_at__gt=date_from) | models.Q(ended_at__isnull=True))
        .order_by("started_at", "sensor_index")
    )

    points_queryset = TelemetryLogPoint.objects.filter(
        vehicle=vehicle,
        event_date__gte=date_from,
        event_date__lte=date_to,
    ).order_by("event_date")

    points_count = points_queryset.count()
    if include_telemetry:
        telemetry_points = tuple(points_queryset[:telemetry_limit])
    else:
        telemetry_points = ()

    balance = _calculate_balance_from_queryset(points_queryset, refuels, drains)

    summary = {
        "analysis_runs_count": len(analysis_runs),
        "telemetry_points_count": points_count,
        "refuels_count": len(refuels),
        "drains_count": len(drains),
        "sensor_faults_count": len(sensor_faults),
        "telemetry_included": include_telemetry,
        "telemetry_limit": telemetry_limit if include_telemetry else 0,
    }

    return VehicleHistoricalReport(
        vehicle=vehicle,
        date_from=date_from,
        date_to=date_to,
        analysis_runs=analysis_runs,
        refuels=refuels,
        drains=drains,
        sensor_faults=sensor_faults,
        telemetry_points=telemetry_points,
        balance=balance,
        summary=summary,
    )


def _calculate_balance_from_queryset(
    points_queryset,
    refuels: tuple[FuelEvent, ...],
    drains: tuple[FuelEvent, ...],
) -> "FuelBalance":
    from analytics.services import MIN_MEANINGFUL_LEVEL_LITRES, FuelBalance

    refueled = sum(event.volume_litres for event in refuels)
    drained = sum(event.volume_litres for event in drains)

    first = (
        points_queryset.filter(smoothed_litres__gt=MIN_MEANINGFUL_LEVEL_LITRES)
        .values_list("smoothed_litres", flat=True)
        .first()
    )
    last = (
        points_queryset.filter(smoothed_litres__gt=MIN_MEANINGFUL_LEVEL_LITRES)
        .order_by("-event_date")
        .values_list("smoothed_litres", flat=True)
        .first()
    )
    total_points = points_queryset.filter(smoothed_litres__isnull=False).count()
    meaningful_points = points_queryset.filter(
        smoothed_litres__gt=MIN_MEANINGFUL_LEVEL_LITRES
    ).count()

    if first is None or last is None:
        first = (
            points_queryset.filter(smoothed_litres__isnull=False)
            .values_list("smoothed_litres", flat=True)
            .first()
        )
        last = (
            points_queryset.filter(smoothed_litres__isnull=False)
            .order_by("-event_date")
            .values_list("smoothed_litres", flat=True)
            .first()
        )
        if first is None or last is None:
            return FuelBalance(0, 0, 0, round(refueled, 3), round(drained, 3), 0)
        start = float(first)
        end = float(last)
        return FuelBalance(
            start_litres=round(start, 3),
            end_litres=round(end, 3),
            delta_litres=round(end - start, 3),
            refueled_litres=round(refueled, 3),
            drained_litres=round(drained, 3),
            estimated_consumption_litres=round(max(0.0, start + refueled - drained - end), 3),
            meaningful_points_count=meaningful_points,
            total_points_count=total_points,
            unreliable=True,
        )

    start = float(first)
    end = float(last)
    delta = end - start
    consumption = max(0.0, start + refueled - drained - end)

    return FuelBalance(
        start_litres=round(start, 3),
        end_litres=round(end, 3),
        delta_litres=round(delta, 3),
        refueled_litres=round(refueled, 3),
        drained_litres=round(drained, 3),
        estimated_consumption_litres=round(consumption, 3),
        meaningful_points_count=meaningful_points,
        total_points_count=total_points,
        unreliable=False,
    )


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
