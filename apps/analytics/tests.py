from pathlib import Path

from django.test import SimpleTestCase

from calibration.parser import parse_calibration_text

from .engine import (
    AnalysisConfig,
    analyze_fuel_telemetry,
    codes_to_litres,
    diagnose_sensors,
)
from .services import calculate_fuel_balance, fetch_click_log_chunks, _dedupe_click_log_rows


REAL_CALIBRATION = """\
0;0;0
40;2;10
80;6;17
120;10;116
160;47;218
200;150;318
240;251;418
280;351;514
320;448;610
360;544;704
400;639;798
440;733;889
480;1039;1180
520;1345;1471
560;1651;1762
600;1957;2053
640;2263;2344
680;2569;2635
720;2875;2926
760;3181;3217
800;3487;3508
840;3793;3799
870;4095;4095
"""


class MockClickLogClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def fetch_click_log(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        return self.responses[index]


class FuelAnalyticsTests(SimpleTestCase):
    def setUp(self):
        self.grid = parse_calibration_text(
            "0;0;0\n"
            "100;500;500\n"
            "200;1000;1000\n"
            "300;1500;1500\n"
            "400;2000;2000\n"
            "500;2500;2500\n"
        )

    def test_interpolates_and_sums_two_sensors(self):
        litres = codes_to_litres([1000, 1000], self.grid)

        self.assertAlmostEqual(litres, 200.0, places=2)

    def test_detects_refuel_drain_and_diagnostics(self):
        points = []
        start = 1_783_600_000
        code = 1500

        for index in range(36):
            speed = 0
            if index < 8:
                code -= 5
                speed = 10
            elif 8 <= index < 14:
                code += 80
                speed = 0
            elif index == 20:
                code -= 450
                speed = 8
            elif 24 <= index < 34:
                speed = 15
                code = 1300
            else:
                code -= 4

            points.append(
                {
                    "EVENT_DATE": start + index * 300,
                    "SPEED": speed,
                    "LLS_CODE": [code, code],
                }
            )

        result = analyze_fuel_telemetry(
            points,
            self.grid,
            AnalysisConfig(
                min_refuel_litres=20,
                min_drain_litres=20,
                freeze_min_duration_seconds=20 * 60,
                smoothing_window=3,
            ),
        )

        self.assertGreaterEqual(len(result.refuels), 1)
        self.assertGreaterEqual(len(result.drains), 1)
        self.assertTrue(
            any("Long Freeze" in diagnostic.reason for diagnostic in result.diagnostics)
        )

    def test_real_calibration_detects_refuel_and_drain_over_long_period(self):
        grid = parse_calibration_text(REAL_CALIBRATION)
        start = 1_770_723_885
        points: list[dict] = []
        code_1 = 639
        code_2 = 798

        for month_index in range(5):
            for day in range(30):
                for hour in range(8):
                    index = month_index * 30 * 8 + day * 8 + hour
                    timestamp = start + index * 3600
                    speed = 0 if hour < 2 else 12

                    if month_index == 1 and 10 <= day <= 12 and hour < 4:
                        speed = 0
                        code_1 += 18
                        code_2 += 18
                    elif month_index == 2 and day == 15 and hour == 3:
                        speed = 14
                        code_1 -= 220
                        code_2 -= 220
                    elif hour >= 2:
                        code_1 = max(40, code_1 - 1)
                        code_2 = max(10, code_2 - 1)

                    points.append(
                        {
                            "EVENT_DATE": timestamp,
                            "SPEED": speed,
                            "LLS_CODE": [code_1, code_2],
                        }
                    )

        result = analyze_fuel_telemetry(
            points,
            grid,
            AnalysisConfig(
                min_refuel_litres=20,
                min_drain_litres=20,
                smoothing_window=5,
            ),
        )

        self.assertGreater(len(result.points), 0)
        self.assertGreaterEqual(len(result.refuels), 1)
        self.assertGreaterEqual(len(result.drains), 1)

    def test_empty_raw_rows_produce_no_events(self):
        result = analyze_fuel_telemetry([], self.grid)

        self.assertEqual(len(result.points), 0)
        self.assertEqual(len(result.refuels), 0)
        self.assertEqual(len(result.drains), 0)

    def test_skips_rows_without_timestamp(self):
        result = analyze_fuel_telemetry(
            [{"SPEED": 0, "LLS_CODE": [1000, 1000]}],
            self.grid,
        )

        self.assertEqual(len(result.points), 0)

    def test_skips_rows_without_active_sensor_reading(self):
        result = analyze_fuel_telemetry(
            [{"EVENT_DATE": 1_700_000_000, "SPEED": 0, "LLS_CODE": [0, 0]}],
            self.grid,
        )

        self.assertEqual(len(result.points), 0)

    def test_intermittent_signal_not_reported_as_jitter(self):
        points = []
        start = 1_700_000_000
        for index in range(12):
            code_2 = 2500 if index % 2 == 0 else 0
            points.append(
                {
                    "EVENT_DATE": start + index * 60,
                    "SPEED": 0,
                    "LLS_CODE": [2500, code_2],
                }
            )

        converted = analyze_fuel_telemetry(points, self.grid, AnalysisConfig())
        diagnostics = diagnose_sensors(
            converted.points,
            self.grid,
            AnalysisConfig(),
        )
        reasons = {item.reason for item in diagnostics}
        sensor_2_reasons = {
            item.reason for item in diagnostics if item.sensor_index == 1
        }

        self.assertIn("Пропадание сигнала ДУТ", sensor_2_reasons)
        self.assertNotIn("Хаотичный дребезг на стоянке", sensor_2_reasons)

    def test_dedupe_click_log_rows(self):
        rows = _dedupe_click_log_rows(
            [
                {"EVENT_DATE": 100, "SPEED": 0, "LLS_CODE": [1, 2]},
                {"EVENT_DATE": 100, "SPEED": 0, "LLS_CODE": [1, 2]},
                {"EVENT_DATE": 200, "SPEED": 1, "LLS_CODE": [3, 4]},
            ]
        )

        self.assertEqual(len(rows), 2)

    def test_accepts_time_field_alias(self):
        result = analyze_fuel_telemetry(
            [{"TIME": 1_700_000_000, "SPEED": 0, "LLS_CODE": [1000, 1000]}],
            self.grid,
        )

        self.assertEqual(len(result.points), 1)
        self.assertEqual(result.points[0].timestamp, 1_700_000_000)

    def test_calculate_fuel_balance(self):
        points = []
        start = 1_700_000_000
        code = 2000

        for index in range(20):
            speed = 10 if index < 10 else 0
            if 10 <= index < 14:
                code += 60
            elif index == 16:
                code -= 300
            else:
                code -= 3

            points.append(
                {
                    "EVENT_DATE": start + index * 600,
                    "SPEED": speed,
                    "LLS_CODE": [code, code],
                }
            )

        result = analyze_fuel_telemetry(
            points,
            self.grid,
            AnalysisConfig(min_refuel_litres=20, min_drain_litres=20, smoothing_window=3),
        )
        balance = calculate_fuel_balance(result)

        self.assertGreater(balance.start_litres, 0)
        self.assertGreater(balance.end_litres, 0)
        self.assertGreater(balance.refueled_litres, 0)
        self.assertGreater(balance.drained_litres, 0)
        self.assertGreaterEqual(balance.estimated_consumption_litres, 0)


    def test_progress_wrappers_pass_vehicle_name_as_keyword(self):
        from .services import (
            _wrap_analyze_progress_callback,
            _wrap_fetch_progress_callback,
        )

        fetch_calls: list[dict] = []
        analyze_calls: list[dict] = []

        def fetch_callback(*args, **kwargs):
            fetch_calls.append({"args": args, "kwargs": kwargs})

        def analyze_callback(*args, **kwargs):
            analyze_calls.append({"args": args, "kwargs": kwargs})

        wrapped_fetch = _wrap_fetch_progress_callback("ТС-1", fetch_callback)
        wrapped_analyze = _wrap_analyze_progress_callback("ТС-1", analyze_callback)

        assert wrapped_fetch is not None
        assert wrapped_analyze is not None

        wrapped_fetch(1, 2, (100, 200), 5)
        wrapped_analyze("convert", 1, 2, 5)

        self.assertEqual(fetch_calls[0]["kwargs"], {"vehicle_name": "ТС-1"})
        self.assertEqual(analyze_calls[0]["args"], ("convert", 1, 2, 5))
        self.assertEqual(analyze_calls[0]["kwargs"], {"vehicle_name": "ТС-1"})


class FetchClickLogChunksTests(SimpleTestCase):
    def test_merges_chunks_in_order_and_sorts_by_event_date(self):
        client = MockClickLogClient(
            [
                {"columns": [{"EVENT_DATE": 200, "LLS_CODE": [1, 2]}]},
                {"columns": [{"EVENT_DATE": 100, "LLS_CODE": [3, 4]}]},
            ]
        )
        chunks = [(100, 150), (150, 200)]
        progress_calls: list[tuple] = []

        rows = fetch_click_log_chunks(
            client=client,
            terminal_id=123,
            chunks=chunks,
            progress_callback=lambda *args: progress_calls.append(args),
            max_workers=2,
        )

        self.assertEqual([row["EVENT_DATE"] for row in rows], [100, 200])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(progress_calls), 2)
        self.assertEqual(progress_calls[0][3], 1)

    def test_empty_api_chunks_return_no_rows(self):
        client = MockClickLogClient(
            [{"columns": []} for _ in range(3)]
        )
        chunks = [(1, 2), (2, 3), (3, 4)]

        rows = fetch_click_log_chunks(
            client=client,
            terminal_id=123,
            chunks=chunks,
            max_workers=3,
        )

        self.assertEqual(rows, [])

    def test_loads_real_calibration_file_from_project_root(self):
        calibration_path = Path(__file__).resolve().parents[3] / "тарировка.txt"
        if not calibration_path.exists():
            self.skipTest("тарировка.txt is not available in the workspace root.")

        grid = parse_calibration_text(calibration_path.read_text(encoding="utf-8"))
        litres = codes_to_litres([639, 798], grid)

        self.assertEqual(grid.sensor_count, 2)
        self.assertEqual(len(grid.rows), 23)
        self.assertAlmostEqual(litres, 400.0, places=1)
