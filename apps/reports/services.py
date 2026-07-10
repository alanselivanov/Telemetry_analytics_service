
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
    from analytics.services import MIN_MEANINGFUL_LEVEL_LITRES, build_fuel_balance

    refueled = sum(event.volume_litres for event in refuels)
    drained = sum(event.volume_litres for event in drains)
    total_points = points_queryset.filter(smoothed_litres__isnull=False).count()
    meaningful_points = points_queryset.filter(
        smoothed_litres__gt=MIN_MEANINGFUL_LEVEL_LITRES
    ).count()

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

    if first is not None and last is not None:
        return build_fuel_balance(
            start_litres=float(first),
            end_litres=float(last),
            refueled_litres=refueled,
            drained_litres=drained,
            meaningful_points_count=meaningful_points,
            total_points_count=total_points,
            unreliable=False,
        )

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
        return build_fuel_balance(
            start_litres=0.0,
            end_litres=0.0,
            refueled_litres=refueled,
            drained_litres=drained,
            meaningful_points_count=meaningful_points,
            total_points_count=total_points,
        )

    return build_fuel_balance(
        start_litres=float(first),
        end_litres=float(last),
        refueled_litres=refueled,
        drained_litres=drained,
        meaningful_points_count=meaningful_points,
        total_points_count=total_points,
        unreliable=True,
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