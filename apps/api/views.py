from __future__ import annotations

from rest_framework.generics import ListAPIView

from reports.models import AnalysisRun, FuelEvent, SensorFault, TelemetryLogPoint

from .serializers import (
    AnalysisRunSerializer,
    FuelEventSerializer,
    SensorFaultSerializer,
    TelemetryLogPointSerializer,
)


class TerminalFilterMixin:
    """Filter querysets by optional ``terminal_id`` query parameter."""

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        terminal_id = self.request.query_params.get("terminal_id")
        if terminal_id:
            queryset = queryset.filter(vehicle__terminal_id=terminal_id)
        return queryset


class AnalysisRunListView(TerminalFilterMixin, ListAPIView):
    queryset = AnalysisRun.objects.select_related("vehicle", "calibration_table")
    serializer_class = AnalysisRunSerializer


class FuelEventListView(TerminalFilterMixin, ListAPIView):
    queryset = FuelEvent.objects.select_related("vehicle", "analysis_run")
    serializer_class = FuelEventSerializer


class SensorFaultListView(TerminalFilterMixin, ListAPIView):
    queryset = SensorFault.objects.select_related("vehicle", "analysis_run")
    serializer_class = SensorFaultSerializer


class TelemetryLogPointListView(TerminalFilterMixin, ListAPIView):
    queryset = TelemetryLogPoint.objects.select_related("vehicle", "analysis_run")
    serializer_class = TelemetryLogPointSerializer
