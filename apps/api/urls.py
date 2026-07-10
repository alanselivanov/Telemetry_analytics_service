
from __future__ import annotations

from django.urls import path

from .views import (
    AnalysisRunListView,
    FuelEventListView,
    SensorFaultListView,
    TelemetryLogPointListView,
    VehicleListView,
    VehicleReportView,
)

urlpatterns = [
    path("vehicles/", VehicleListView.as_view(), name="vehicle-list"),
    path(
        "reports/vehicle/<int:vehicle_id>/",
        VehicleReportView.as_view(),
        name="vehicle-report",
    ),
    path("analysis-runs/", AnalysisRunListView.as_view(), name="analysis-run-list"),
    path("fuel-events/", FuelEventListView.as_view(), name="fuel-event-list"),
    path("sensor-faults/", SensorFaultListView.as_view(), name="sensor-fault-list"),
    path("telemetry-points/", TelemetryLogPointListView.as_view(), name="telemetry-point-list"),
]