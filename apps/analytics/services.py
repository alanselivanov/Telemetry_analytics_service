
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from calibration.models import CalibrationTable, Vehicle
from calibration.parser import grid_from_model, parse_calibration_text, save_calibration_grid
from omnicomm.client import OmnicommClient, extract_click_log_rows
from reports.models import AnalysisRun
from reports.services import save_fuel_analysis_result

from .concurrency import (
    analyze_fuel_telemetry_parallel,
    resolve_cpu_workers,
    resolve_io_workers,
)
from .engine import AnalysisConfig, FuelAnalysisResult, analyze_fuel_telemetry
from .mock_data import (
    MOCK_CALIBRATION,
    MOCK_CALIBRATION_NAME,
    MOCK_TERMINAL_ID,
    MOCK_VEHICLE_NAME,
    build_mock_points,
)

ProgressCallback = Callable[..., None]
FetchProgressCallback = Callable[..., None]
AnalyzeProgressCallback = Callable[..., None]

MIN_MEANINGFUL_LEVEL_LITRES = 0.5


@dataclass(frozen=True)
class FuelBalance:

    start_litres: float
    end_litres: float
    delta_litres: float
    refueled_litres: float
    drained_litres: float
    estimated_consumption_litres: float
    meaningful_points_count: int = 0
    total_points_count: int = 0
    unreliable: bool = False


@dataclass(frozen=True)
class FuelAnalysisExecution:

    analysis_run: AnalysisRun
    result: FuelAnalysisResult
    balance: FuelBalance
    raw_rows_count: int
    chunks_count: int
    io_workers: int = 1
    cpu_workers: int = 1


@dataclass(frozen=True)
class VehicleAnalysisTarget:

    vehicle: Vehicle
    calibration_table: CalibrationTable


def run_real_fuel_analysis(
    *,
    client: OmnicommClient,
    vehicle: Vehicle,
    calibration_table: CalibrationTable,
    chunks: list[tuple[int, int]],
    source: str = "run_telemetry",
    config: AnalysisConfig | None = None,
    fetch_progress_callback: FetchProgressCallback | None = None,
    analyze_progress_callback: AnalyzeProgressCallback | None = None,
    io_workers: int | None = None,
    cpu_workers: int | None = None,
) -> FuelAnalysisExecution:
    execution = _execute_vehicle_analysis(
        client=client,
        target=VehicleAnalysisTarget(
            vehicle=vehicle,
            calibration_table=calibration_table,
        ),
        chunks=chunks,
        source=source,
        config=config,
        fetch_progress_callback=fetch_progress_callback,
        analyze_progress_callback=analyze_progress_callback,
        io_workers=io_workers,
        cpu_workers=cpu_workers,
    )
    return execution


def run_multi_vehicle_fuel_analysis(
    *,
    client: OmnicommClient,
    targets: list[VehicleAnalysisTarget],
    chunks: list[tuple[int, int]],
    source: str = "run_telemetry",
    config: AnalysisConfig | None = None,
    vehicle_progress_callback: Callable[[int, int, str], None] | None = None,
    fetch_progress_callback: FetchProgressCallback | None = None,
    analyze_progress_callback: AnalyzeProgressCallback | None = None,
    io_workers: int | None = None,
    cpu_workers: int | None = None,
) -> list[FuelAnalysisExecution]:
    if not targets:
        return []

    if len(targets) == 1:
        return [
            run_real_fuel_analysis(
                client=client,
                vehicle=targets[0].vehicle,
                calibration_table=targets[0].calibration_table,
                chunks=chunks,
                source=source,
                config=config,
                fetch_progress_callback=fetch_progress_callback,
                analyze_progress_callback=analyze_progress_callback,
                io_workers=io_workers,
                cpu_workers=cpu_workers,
            )
        ]

    workers = resolve_io_workers(io_workers, len(targets))
    executions: list[FuelAnalysisExecution | None] = [None] * len(targets)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(
                _execute_vehicle_analysis,
                client=client,
                target=target,
                chunks=chunks,
                source=source,
                config=config,
                fetch_progress_callback=_wrap_fetch_progress_callback(
                    target.vehicle.name,
                    fetch_progress_callback,
                ),
                analyze_progress_callback=_wrap_analyze_progress_callback(
                    target.vehicle.name,
                    analyze_progress_callback,
                ),
                io_workers=io_workers,
                cpu_workers=cpu_workers,
            ): index
            for index, target in enumerate(targets)
        }

        completed = 0
        for future in as_completed(future_map):
            index = future_map[future]
            execution = future.result()
            executions[index] = execution
            completed += 1
            if vehicle_progress_callback:
                vehicle_progress_callback(
                    completed,
                    len(targets),
                    execution.analysis_run.vehicle.name,
                )

    return [execution for execution in executions if execution is not None]


def _wrap_fetch_progress_callback(
    vehicle_name: str,
    callback: FetchProgressCallback | None,
) -> FetchProgressCallback | None:
    if callback is None:
        return None

    def wrapped(
        index: int,
        total: int,
        chunk: tuple[int, int],
        row_count: int = 0,
    ) -> None:
        callback(index, total, chunk, row_count, vehicle_name=vehicle_name)

    return wrapped


def _wrap_analyze_progress_callback(
    vehicle_name: str,
    callback: AnalyzeProgressCallback | None,
) -> AnalyzeProgressCallback | None:
    if callback is None:
        return None

    def wrapped(stage: str, *values: int) -> None:
        callback(stage, *values, vehicle_name=vehicle_name)

    return wrapped


def _execute_vehicle_analysis(
    *,
    client: OmnicommClient,
    target: VehicleAnalysisTarget,
    chunks: list[tuple[int, int]],
    source: str,
    config: AnalysisConfig | None,
    fetch_progress_callback: FetchProgressCallback | None,
    analyze_progress_callback: AnalyzeProgressCallback | None,
    io_workers: int | None,
    cpu_workers: int | None,
) -> FuelAnalysisExecution:
    vehicle = target.vehicle
    calibration_table = target.calibration_table
    resolved_io_workers = resolve_io_workers(io_workers, len(chunks))
    resolved_cpu_workers = resolve_cpu_workers(cpu_workers, len(chunks))

    raw_rows = fetch_click_log_chunks(
        client=client,
        terminal_id=vehicle.terminal_id,
        chunks=chunks,
        progress_callback=fetch_progress_callback,
        max_workers=resolved_io_workers,
    )
    calibration_grid = grid_from_model(calibration_table)

    def _analysis_progress(*args) -> None:
        if analyze_progress_callback:
            analyze_progress_callback(*args)

    result = analyze_fuel_telemetry_parallel(
        raw_rows,
        calibration_grid,
        chunks=chunks,
        config=config or AnalysisConfig(),
        cpu_workers=resolved_cpu_workers,
        progress_callback=_analysis_progress,
    )
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
            "io_workers": resolved_io_workers,
            "cpu_workers": resolved_cpu_workers,
        },
    )

    return FuelAnalysisExecution(
        analysis_run=analysis_run,
        result=result,
        balance=balance,
        raw_rows_count=len(raw_rows),
        chunks_count=len(chunks),
        io_workers=resolved_io_workers,
        cpu_workers=resolved_cpu_workers,
    )


def fetch_click_log_chunks(
    *,
    client: OmnicommClient,
    terminal_id: int,
    chunks: list[tuple[int, int]],
    progress_callback: FetchProgressCallback | None = None,
    max_workers: int | None = None,
) -> list[dict]:
    if not chunks:
        return []

    workers = resolve_io_workers(max_workers, len(chunks))
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
    valid_points = [point for point in result.points if point.smoothed_litres is not None]
    refueled = sum(event.volume_litres for event in result.refuels)
    drained = sum(event.volume_litres for event in result.drains)

    if not valid_points:
        return build_fuel_balance(
            start_litres=0.0,
            end_litres=0.0,
            refueled_litres=refueled,
            drained_litres=drained,
        )

    start, end, meaningful_count, unreliable = _resolve_boundary_levels(valid_points)
    return build_fuel_balance(
        start_litres=start,
        end_litres=end,
        refueled_litres=refueled,
        drained_litres=drained,
        meaningful_points_count=meaningful_count,
        total_points_count=len(valid_points),
        unreliable=unreliable,
    )


def build_fuel_balance(
    *,
    start_litres: float,
    end_litres: float,
    refueled_litres: float,
    drained_litres: float,
    meaningful_points_count: int = 0,
    total_points_count: int = 0,
    unreliable: bool = False,
) -> FuelBalance:
    delta = end_litres - start_litres
    consumption = max(0.0, start_litres + refueled_litres - drained_litres - end_litres)
    return FuelBalance(
        start_litres=round(start_litres, 3),
        end_litres=round(end_litres, 3),
        delta_litres=round(delta, 3),
        refueled_litres=round(refueled_litres, 3),
        drained_litres=round(drained_litres, 3),
        estimated_consumption_litres=round(consumption, 3),
        meaningful_points_count=meaningful_points_count,
        total_points_count=total_points_count,
        unreliable=unreliable,
    )


def _resolve_boundary_levels(
    valid_points: list,
) -> tuple[float, float, int, bool]:
    meaningful = [
        point
        for point in valid_points
        if (point.smoothed_litres or 0) > MIN_MEANINGFUL_LEVEL_LITRES
    ]

    if meaningful:
        return (
            float(meaningful[0].smoothed_litres or 0),
            float(meaningful[-1].smoothed_litres or 0),
            len(meaningful),
            False,
        )

    start = float(valid_points[0].smoothed_litres or 0)
    end = float(valid_points[-1].smoothed_litres or 0)
    return start, end, 0, True


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