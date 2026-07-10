"""DRF serializers for aggregated fuel analytics."""

from __future__ import annotations

from rest_framework import serializers

from reports.models import AnalysisRun, FuelEvent, SensorFault, TelemetryLogPoint


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
