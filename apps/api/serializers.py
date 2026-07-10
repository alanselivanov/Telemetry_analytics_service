"""DRF serializers for aggregated fuel analytics."""

from __future__ import annotations

from rest_framework import serializers

from analytics.services import FuelBalance
from calibration.models import Vehicle
from reports.models import AnalysisRun, FuelEvent, SensorFault, TelemetryLogPoint
from reports.services import VehicleHistoricalReport


class VehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ["id", "terminal_id", "name", "external_uuid", "created_at", "updated_at"]


class AnalysisRunSerializer(serializers.ModelSerializer):
    terminal_id = serializers.IntegerField(source="vehicle.terminal_id", read_only=True)
    vehicle_name = serializers.CharField(source="vehicle.name", read_only=True)

    class Meta:
        model = AnalysisRun
        fields = [
            "id",
            "terminal_id",
            "vehicle_name",
            "date_from",
            "date_to",
            "source",
            "metadata",
            "created_at",
        ]


class FuelEventSerializer(serializers.ModelSerializer):
    terminal_id = serializers.IntegerField(source="vehicle.terminal_id", read_only=True)
    vehicle_name = serializers.CharField(source="vehicle.name", read_only=True)

    class Meta:
        model = FuelEvent
        fields = [
            "id",
            "analysis_run_id",
            "terminal_id",
            "vehicle_name",
            "event_type",
            "started_at",
            "ended_at",
            "volume_litres",
            "start_level_litres",
            "end_level_litres",
            "confidence",
            "created_at",
        ]


class SensorFaultSerializer(serializers.ModelSerializer):
    terminal_id = serializers.IntegerField(source="vehicle.terminal_id", read_only=True)
    vehicle_name = serializers.CharField(source="vehicle.name", read_only=True)

    class Meta:
        model = SensorFault
        fields = [
            "id",
            "analysis_run_id",
            "terminal_id",
            "vehicle_name",
            "sensor_index",
            "status",
            "reason",
            "started_at",
            "ended_at",
            "details",
            "created_at",
        ]


class TelemetryLogPointSerializer(serializers.ModelSerializer):
    terminal_id = serializers.IntegerField(source="vehicle.terminal_id", read_only=True)

    class Meta:
        model = TelemetryLogPoint
        fields = [
            "id",
            "analysis_run_id",
            "terminal_id",
            "event_date",
            "speed",
            "lls_codes",
            "litres",
            "smoothed_litres",
        ]


class FuelBalanceSerializer(serializers.Serializer):
    start_litres = serializers.FloatField()
    end_litres = serializers.FloatField()
    delta_litres = serializers.FloatField()
    refueled_litres = serializers.FloatField()
    drained_litres = serializers.FloatField()
    estimated_consumption_litres = serializers.FloatField()

    @classmethod
    def from_balance(cls, balance: FuelBalance) -> "FuelBalanceSerializer":
        return cls(balance)


class VehicleHistoricalReportSerializer:
    """Build JSON payload for the vehicle historical report endpoint."""

    @classmethod
    def from_report(cls, report: VehicleHistoricalReport) -> dict:
        return {
            "vehicle": VehicleSerializer(report.vehicle).data,
            "period": {"from": report.date_from, "to": report.date_to},
            "summary": report.summary,
            "balance": FuelBalanceSerializer(report.balance).data,
            "analysis_runs": AnalysisRunSerializer(report.analysis_runs, many=True).data,
            "refuels": FuelEventSerializer(report.refuels, many=True).data,
            "drains": FuelEventSerializer(report.drains, many=True).data,
            "sensor_faults": SensorFaultSerializer(report.sensor_faults, many=True).data,
            "telemetry_points": TelemetryLogPointSerializer(
                report.telemetry_points,
                many=True,
            ).data,
        }
