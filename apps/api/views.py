from __future__ import annotations

from django.db import models
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from calibration.models import Vehicle
from reports.models import AnalysisRun, FuelEvent, SensorFault, TelemetryLogPoint
from reports.services import get_vehicle_historical_report

from .params import ApiPeriodParseError, parse_api_period
from .serializers import (
    AnalysisRunSerializer,
    FuelEventSerializer,
    SensorFaultSerializer,
    TelemetryLogPointSerializer,
    VehicleHistoricalReportSerializer,
    VehicleSerializer,
)


class PeriodFilterMixin:
    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        terminal_id = self.request.query_params.get("terminal_id")
        if terminal_id:
            queryset = queryset.filter(vehicle__terminal_id=terminal_id)

        if not any(
            self.request.query_params.get(key)
            for key in ("from", "to", "period")
        ):
            return queryset

        try:
            date_from, date_to = parse_api_period(
                date_from=self.request.query_params.get("from"),
                date_to=self.request.query_params.get("to"),
                period=self.request.query_params.get("period"),
            )
        except ApiPeriodParseError:
            return queryset

        return self._apply_period_filter(queryset, date_from, date_to)

    def _apply_period_filter(self, queryset, date_from: int, date_to: int):
        model = queryset.model
        if model is AnalysisRun:
            return queryset.filter(date_from__lt=date_to, date_to__gt=date_from)
        if model is TelemetryLogPoint:
            return queryset.filter(event_date__gte=date_from, event_date__lte=date_to)
        if model is FuelEvent:
            return queryset.filter(started_at__lt=date_to, ended_at__gt=date_from)
        if model is SensorFault:
            return queryset.filter(started_at__isnull=False, started_at__lt=date_to).filter(
                models.Q(ended_at__gt=date_from) | models.Q(ended_at__isnull=True)
            )
        return queryset


class VehicleListView(ListAPIView):
    queryset = Vehicle.objects.all()
    serializer_class = VehicleSerializer


class VehicleReportView(APIView):
    def get(self, request, vehicle_id: int):
        try:
            date_from, date_to = parse_api_period(
                date_from=request.query_params.get("from"),
                date_to=request.query_params.get("to"),
                period=request.query_params.get("period"),
            )
        except ApiPeriodParseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        include_telemetry = request.query_params.get("include", "").lower() in {
            "telemetry",
            "all",
            "points",
        }
        try:
            telemetry_limit = min(
                50_000,
                max(1, int(request.query_params.get("telemetry_limit", 5000))),
            )
        except ValueError:
            return Response(
                {"detail": "telemetry_limit must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        report = get_vehicle_historical_report(
            vehicle_id,
            date_from=date_from,
            date_to=date_to,
            include_telemetry=include_telemetry,
            telemetry_limit=telemetry_limit,
        )
        payload = VehicleHistoricalReportSerializer.from_report(report)
        return Response(payload)


class AnalysisRunListView(PeriodFilterMixin, ListAPIView):
    queryset = AnalysisRun.objects.select_related("vehicle", "calibration_table")
    serializer_class = AnalysisRunSerializer


class FuelEventListView(PeriodFilterMixin, ListAPIView):
    queryset = FuelEvent.objects.select_related("vehicle", "analysis_run")
    serializer_class = FuelEventSerializer


class SensorFaultListView(PeriodFilterMixin, ListAPIView):
    queryset = SensorFault.objects.select_related("vehicle", "analysis_run")
    serializer_class = SensorFaultSerializer


class TelemetryLogPointListView(PeriodFilterMixin, ListAPIView):
    queryset = TelemetryLogPoint.objects.select_related("vehicle", "analysis_run")
    serializer_class = TelemetryLogPointSerializer
