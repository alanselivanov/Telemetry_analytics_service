import os
from unittest.mock import patch

from django.test import SimpleTestCase

from calibration.parser import parse_calibration_text

from .concurrency import (
    analyze_fuel_telemetry_parallel,
    partition_rows_by_chunks,
    resolve_cpu_workers,
    resolve_io_workers,
    should_parallelize,
)
from .engine import AnalysisConfig, analyze_fuel_telemetry


class ConcurrencyHelpersTests(SimpleTestCase):
    def test_resolve_io_workers_auto_mode(self):
        workers = resolve_io_workers(None, task_count=10)
        self.assertGreaterEqual(workers, 1)
        self.assertLessEqual(workers, 10)

    def test_resolve_cpu_workers_respects_explicit_value(self):
        self.assertEqual(resolve_cpu_workers(3, task_count=10), 3)

    def test_should_parallelize_requires_enough_points(self):
        with patch.dict(os.environ, {"ANALYTICS_MIN_POINTS_FOR_PARALLEL": "1000"}):
            self.assertFalse(should_parallelize(500, 5))
            self.assertTrue(should_parallelize(1500, 5))

    def test_partition_rows_by_chunks(self):
        chunks = [(100, 200), (200, 300)]
        rows = [
            {"EVENT_DATE": 150, "LLS_CODE": [1, 2]},
            {"EVENT_DATE": 250, "LLS_CODE": [3, 4]},
            {"EVENT_DATE": 999, "LLS_CODE": [5, 6]},
        ]

        buckets = partition_rows_by_chunks(rows, chunks)

        self.assertEqual(len(buckets[0]), 1)
        self.assertEqual(len(buckets[1]), 1)
        self.assertEqual(buckets[0][0]["EVENT_DATE"], 150)


class ParallelAnalyzeTests(SimpleTestCase):
    def setUp(self):
        self.grid = parse_calibration_text(
            "0;0;0\n"
            "500;50;50\n"
            "1000;100;100\n"
            "1500;150;150\n"
            "2000;200;200\n"
            "2500;250;250\n"
            "3000;300;300\n"
        )

    def _build_rows(self, count: int) -> list[dict]:
        rows = []
        start = 1_700_000_000
        code = 2000
        for index in range(count):
            rows.append(
                {
                    "EVENT_DATE": start + index * 300,
                    "SPEED": 0 if index % 4 else 10,
                    "LLS_CODE": [code, code],
                }
            )
            code = max(500, code - 2)
        return rows

    def test_parallel_matches_sequential_for_small_dataset(self):
        rows = self._build_rows(40)
        chunks = [(rows[0]["EVENT_DATE"], rows[19]["EVENT_DATE"]), (rows[20]["EVENT_DATE"], rows[-1]["EVENT_DATE"])]
        config = AnalysisConfig(min_refuel_litres=50, smoothing_window=3)

        sequential = analyze_fuel_telemetry(rows, self.grid, config)
        parallel = analyze_fuel_telemetry_parallel(
            rows,
            self.grid,
            chunks=chunks,
            config=config,
            cpu_workers=2,
        )

        self.assertEqual(len(sequential.points), len(parallel.points))
        self.assertEqual(len(sequential.refuels), len(parallel.refuels))
        self.assertEqual(len(sequential.drains), len(parallel.drains))

    def test_parallel_runs_for_large_dataset(self):
        rows = self._build_rows(1200)
        chunks = [
            (rows[0]["EVENT_DATE"], rows[400]["EVENT_DATE"]),
            (rows[400]["EVENT_DATE"], rows[800]["EVENT_DATE"]),
            (rows[800]["EVENT_DATE"], rows[-1]["EVENT_DATE"]),
        ]
        progress: list[tuple] = []

        with patch.dict(os.environ, {"ANALYTICS_MIN_POINTS_FOR_PARALLEL": "500"}):
            result = analyze_fuel_telemetry_parallel(
                rows,
                self.grid,
                chunks=chunks,
                cpu_workers=2,
                progress_callback=lambda *args: progress.append(args),
            )

        self.assertEqual(len(result.points), 1200)
        self.assertTrue(any(item[0] == "convert" for item in progress))
        self.assertTrue(any(item[0] == "done" for item in progress))
