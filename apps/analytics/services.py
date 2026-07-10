"""Application-level orchestration for fuel analytics workflows."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from calibration.models import CalibrationTable, Vehicle
from calibration.parser import grid_from_model, parse_calibration_text, save_calibration_grid
from omnicomm.client import OmnicommClient, extract_click_log_rows
from reports.models import AnalysisRun
from reports.services import save_fuel_analysis_result

from .engine import AnalysisConfig, FuelAnalysisResult, analyze_fuel_telemetry
from .mock_data import (
    MOCK_CALIBRATION,
    MOCK_CALIBRATION_NAME,
    MOCK_TERMINAL_ID,
    MOCK_VEHICLE_NAME,
    build_mock_points,
)

ProgressCallback = Callable[..., None]


@dataclass(frozen=True)
class FuelBalance:
    """Fuel balance summary for an analysis result."""

    start_litres: float
    end_litres: float
    delta_litres: float
    refueled_litres: float
    drained_litres: float
    estimated_consumption_litres: float


@dataclass(frozen=True)
class FuelAnalysisExecution:
    """Saved execution returned to CLI/API callers."""

    analysis_run: AnalysisRun
    result: FuelAnalysisResult
    balance: FuelBalance
    raw_rows_count: int
    chunks_count: int


def run_real_fuel_analysis(
    *,
    client: OmnicommClient,
    vehicle: Vehicle,
    calibration_table: CalibrationTable,
    chunks: list[tuple[int, int]],
    source: str = "run_telemetry",
    config: AnalysisConfig | None = None,
    progress_callback: ProgressCallback | None = None,
    max_workers: int = 4,
) -> FuelAnalysisExecution:
    """
    Download click/log chunks in parallel, run analytics, and persist results.

    The Omnicomm client owns authorization; this service only orchestrates the
    existing client/calibration/analytics/report layers.
    """
    raw_rows = fetch_click_log_chunks(
        client=client,
        terminal_id=vehicle.terminal_id,
        chunks=chunks,
        progress_callback=progress_callback,
        max_workers=max_workers,
    )
    calibration_grid = grid_from_model(calibration_table)
    result = analyze_fuel_telemetry(raw_rows, calibration_grid, config or AnalysisConfig())
    balance = calculate_fuel_balance(result)
    analysis_run = save_fuel_analysis_result(
        vehicle=vehicle,
        calibration_table=calibration_table,
        date_from=chunks[0][0],
        date_to=chunks[-1][1],
        result=result,
        source=source,
        metadata={
            "chunks": len(chunks),
            "raw_rows": len(raw_rows),
            "mode": "omnicomm_click_log",
        },
    )

    return FuelAnalysisExecution(
        analysis_run=analysis_run,
        result=result,
        balance=balance,
        raw_rows_count=len(raw_rows),
        chunks_count=len(chunks),
    )


def fetch_click_log_chunks(
    *,
    client: OmnicommClient,
    terminal_id: int,
    chunks: list[tuple[int, int]],
    progress_callback: ProgressCallback | None = None,
    max_workers: int = 4,
) -> list[dict]:
    """Fetch Omnicomm click/log data for all chunks using a thread pool."""
    if not chunks:
        return []

    workers = max(1, min(max_workers, len(chunks)))
    rows_by_chunk: dict[int, list[dict]] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                client.fetch_click_log,
                terminal_id=terminal_id,
                date_from=chunk_start,
                date_to=chunk_end,
            ): (index, (chunk_start, chunk_end))
            for index, (chunk_start, chunk_end) in enumerate(chunks, start=1)
        }

        for future in as_completed(futures):
            index, chunk = futures[future]
            response = future.result()
            chunk_rows = extract_click_log_rows(response)
            rows_by_chunk[index] = chunk_rows
            if progress_callback:
                progress_callback(index, len(chunks), chunk, len(chunk_rows))

    raw_rows: list[dict] = []
    for index in sorted(rows_by_chunk):
        raw_rows.extend(rows_by_chunk[index])

    raw_rows.sort(key=lambda row: row.get("EVENT_DATE") or row.get("TIME") or 0)
    return _dedupe_click_log_rows(raw_rows)


def _dedupe_click_log_rows(rows: list[dict]) -> list[dict]:
    """Drop exact duplicate rows returned by Omnicomm click/log."""
    seen: set[tuple] = set()
    unique_rows: list[dict] = []

    for row in rows:
        key = (
            row.get("EVENT_DATE") or row.get("TIME"),
            tuple(row.get("LLS_CODE") or []),
            row.get("SPEED"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)

    return unique_rows


def run_mock_fuel_analysis(*, source: str = "run_telemetry_mock") -> FuelAnalysisExecution:
    """Run analytics on built-in mock anomalies and save the result."""
    vehicle, _ = Vehicle.objects.update_or_create(
        terminal_id=MOCK_TERMINAL_ID,
        defaults={"name": MOCK_VEHICLE_NAME},
    )
    calibration_table = _get_or_create_mock_calibration(vehicle)
    raw_rows = build_mock_points()
    calibration_grid = grid_from_model(calibration_table)
    result = analyze_fuel_telemetry(
        raw_rows,
        calibration_grid,
        AnalysisConfig(
            min_refuel_litres=25,
            min_drain_litres=20,
            freeze_min_duration_seconds=20 * 60,
            jitter_window_points=5,
            jitter_min_span_codes=600,
        ),
    )
    balance = calculate_fuel_balance(result)
    analysis_run = save_fuel_analysis_result(
        vehicle=vehicle,
        calibration_table=calibration_table,
        date_from=raw_rows[0]["EVENT_DATE"],
        date_to=raw_rows[-1]["EVENT_DATE"],
        result=result,
        source=source,
        metadata={"raw_rows": len(raw_rows), "mode": "mock_anomalies"},
    )

    return FuelAnalysisExecution(
        analysis_run=analysis_run,
        result=result,
        balance=balance,
        raw_rows_count=len(raw_rows),
        chunks_count=1,
    )


def calculate_fuel_balance(result: FuelAnalysisResult) -> FuelBalance:
    """Calculate basic balance and estimated consumption from processed points."""
    valid_points = [point for point in result.points if point.smoothed_litres is not None]
    if not valid_points:
        return FuelBalance(0, 0, 0, 0, 0, 0)

    start = float(valid_points[0].smoothed_litres or 0)
    end = float(valid_points[-1].smoothed_litres or 0)
    refueled = sum(event.volume_litres for event in result.refuels)
    drained = sum(event.volume_litres for event in result.drains)
    delta = end - start
    consumption = max(0.0, start + refueled - drained - end)

    return FuelBalance(
        start_litres=round(start, 3),
        end_litres=round(end, 3),
        delta_litres=round(delta, 3),
        refueled_litres=round(refueled, 3),
        drained_litres=round(drained, 3),
        estimated_consumption_litres=round(consumption, 3),
    )


def _get_or_create_mock_calibration(vehicle: Vehicle) -> CalibrationTable:
    table = (
        CalibrationTable.objects.filter(
            vehicle=vehicle,
            source_filename="mock_anomalies.csv",
            is_active=True,
        )
        .prefetch_related("points")
        .first()
    )
    if table:
        return table

    grid = parse_calibration_text(MOCK_CALIBRATION)
    return save_calibration_grid(
        vehicle=vehicle,
        name=MOCK_CALIBRATION_NAME,
        grid=grid,
        source_filename="mock_anomalies.csv",
        activate=True,
    )
