from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from analytics.engine import FuelAnalysisResult, FuelEvent as EngineFuelEvent, SensorDiagnostic, TelemetryPoint
from calibration.models import Vehicle
from calibration.parser import parse_calibration_text
from reports.models import FuelEvent
from reports.services import save_fuel_analysis_result


class VehicleReportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.vehicle = Vehicle.objects.create(
            terminal_id=336048351,
            name="№16 МТЗ (Трактор)",
        )
        self.grid = parse_calibration_text("0;0;0\n100;500;500\n200;1000;1000\n")
        calibration_table = self._create_calibration_table()
        self.analysis_run = save_fuel_analysis_result(
            vehicle=self.vehicle,
            calibration_table=calibration_table,
            date_from=1_700_000_000,
            date_to=1_700_100_000,
            result=FuelAnalysisResult(
                points=(
                    TelemetryPoint(1_700_000_000, 0, (1000, 1000), 200.0, 200.0),
                    TelemetryPoint(1_700_050_000, 0, (1100, 1100), 220.0, 220.0),
                    TelemetryPoint(1_700_090_000, 0, (900, 900), 180.0, 180.0),
                ),
                refuels=(
                    EngineFuelEvent(
                        "REFUEL",
                        1_700_020_000,
                        1_700_030_000,
                        50.0,
                        200.0,
                        250.0,
                        0.8,
                    ),
                ),
                drains=(
                    EngineFuelEvent(
                        "DRAIN",
                        1_700_070_000,
                        1_700_080_000,
                        30.0,
                        220.0,
                        190.0,
                        0.8,
                    ),
                ),
                diagnostics=(
                    SensorDiagnostic(
                        sensor_index=0,
                        status="warning",
                        reason="Пропадание сигнала ДУТ",
                        started_at=1_700_040_000,
                        ended_at=1_700_045_000,
                    ),
                ),
            ),
            source="test",
        )

    def _create_calibration_table(self):
        from calibration.parser import save_calibration_grid

        return save_calibration_grid(
            vehicle=self.vehicle,
            name="test",
            grid=self.grid,
            source_filename="test.csv",
            activate=True,
        )

    def test_vehicle_report_by_terminal_id_and_unix_period(self):
        url = reverse("vehicle-report", kwargs={"vehicle_id": self.vehicle.terminal_id})
        response = self.client.get(
            url,
            {"from": "1699999000", "to": "1700100000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["vehicle"]["terminal_id"], self.vehicle.terminal_id)
        self.assertEqual(response.data["summary"]["refuels_count"], 1)
        self.assertEqual(response.data["summary"]["drains_count"], 1)
        self.assertEqual(response.data["summary"]["telemetry_points_count"], 3)
        self.assertEqual(len(response.data["telemetry_points"]), 0)
        self.assertEqual(response.data["balance"]["refueled_litres"], 50.0)

    def test_vehicle_report_with_period_query(self):
        url = reverse("vehicle-report", kwargs={"vehicle_id": self.vehicle.id})
        response = self.client.get(url, {"period": "past hour"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("from", response.data["period"])
        self.assertIn("to", response.data["period"])

    def test_vehicle_report_include_telemetry(self):
        url = reverse("vehicle-report", kwargs={"vehicle_id": self.vehicle.id})
        response = self.client.get(
            url,
            {
                "from": "1699999000",
                "to": "1700100000",
                "include": "telemetry",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["telemetry_points"]), 3)
        self.assertTrue(response.data["summary"]["telemetry_included"])

    def test_vehicle_report_requires_period(self):
        url = reverse("vehicle-report", kwargs={"vehicle_id": self.vehicle.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.data)

    def test_fuel_events_list_filtered_by_terminal_and_period(self):
        url = reverse("fuel-event-list")
        response = self.client.get(
            url,
            {
                "terminal_id": self.vehicle.terminal_id,
                "from": "1699999000",
                "to": "1700100000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        event_types = {item["event_type"] for item in response.data["results"]}
        self.assertEqual(event_types, {FuelEvent.EventType.REFUEL, FuelEvent.EventType.DRAIN})

    def test_vehicle_list_endpoint(self):
        response = self.client.get(reverse("vehicle-list"))

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data["count"], 1)
