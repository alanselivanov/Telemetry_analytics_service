"""Concurrency helpers for parallel API fetch and CPU-bound fuel analytics."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Callable, Sequence

from calibration.parser import CalibrationGrid

from .engine import (
    AnalysisConfig,
    FuelAnalysisResult,
    TelemetryPoint,
    analyze_fuel_telemetry,
    apply_median_filter,
    convert_points_to_litres,
    detect_drains,
    detect_refuels,
    diagnose_sensors,
)

ProgressCallback = Callable[..., None]


def resolve_io_workers(requested: int | None = None, task_count: int = 1) -> int:
    """Resolve the number of threads for network-bound Omnicomm requests."""
    if requested is not None and requested > 0:
        return max(1, min(requested, task_count))
    configured = _env_int("ANALYTICS_IO_MAX_WORKERS", 0)
    if configured > 0:
        return max(1, min(configured, task_count))
    return max(1, min(task_count, (os.cpu_count() or 4) * 2))


def resolve_cpu_workers(requested: int | None = None, task_count: int = 1) -> int:
    """Resolve worker threads for CPU-bound telemetry math."""
    if requested is not None and requested > 0:
        return max(1, min(requested, task_count))
    configured = _env_int("ANALYTICS_CPU_MAX_WORKERS", 0)
    if configured > 0:
        return max(1, min(configured, task_count))
    return max(1, min(task_count, os.cpu_count() or 4))


def should_parallelize(point_count: int, segment_count: int) -> bool:
    """Decide whether parallel CPU processing is worth the overhead."""
    min_points = _env_int("ANALYTICS_MIN_POINTS_FOR_PARALLEL", 1000)
    return segment_count > 1 and point_count >= min_points


def partition_rows_by_chunks(
    rows: Sequence[dict],
    chunks: Sequence[tuple[int, int]],
) -> list[list[dict]]:
    """Assign raw API rows to the API chunk they belong to."""
    if not chunks:
        return [list(rows)]

    buckets: list[list[dict]] = [[] for _ in chunks]
    for row in rows:
        timestamp = row.get("EVENT_DATE") or row.get("TIME")
        if timestamp is None:
            continue

        ts = int(timestamp)
        for index, (chunk_start, chunk_end) in enumerate(chunks):
            is_last = index == len(chunks) - 1
            if is_last:
                if chunk_start <= ts <= chunk_end:
                    buckets[index].append(row)
                    break
            elif chunk_start <= ts < chunk_end:
                buckets[index].append(row)
                break

    return buckets


def analyze_fuel_telemetry_parallel(
    raw_points: Sequence[dict],
    calibration_grid: CalibrationGrid,
    *,
    chunks: Sequence[tuple[int, int]] | None = None,
    config: AnalysisConfig | None = None,
    cpu_workers: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> FuelAnalysisResult:
    """
    Analyze telemetry using a hybrid pipeline:

    1. Convert each time chunk to litres in parallel (thread pool).
    2. Merge the timeline and detect events sequentially to preserve accuracy.
    """
    settings = config or AnalysisConfig()
    rows = list(raw_points)
    chunk_ranges = list(chunks or [])
    segments = (
        partition_rows_by_chunks(rows, chunk_ranges)
        if chunk_ranges
        else [rows]
    )
    non_empty_segments = [segment for segment in segments if segment]
    if not non_empty_segments:
        return analyze_fuel_telemetry([], calibration_grid, settings)

    if not should_parallelize(len(rows), len(non_empty_segments)):
        return analyze_fuel_telemetry(rows, calibration_grid, settings)

    workers = resolve_cpu_workers(cpu_workers, len(non_empty_segments))
    converted_segments = _convert_segments_parallel(
        non_empty_segments,
        calibration_grid,
        workers=workers,
        progress_callback=progress_callback,
    )

    points = sorted(
        (point for segment in converted_segments for point in segment),
        key=lambda point: point.timestamp,
    )
    if progress_callback:
        progress_callback("merge", len(points))

    smoothed = apply_median_filter(points, window=settings.smoothing_window)
    if progress_callback:
        progress_callback("events", len(smoothed))

    refuels = detect_refuels(smoothed, settings)
    drains = detect_drains(smoothed, settings)
    diagnostics = diagnose_sensors(smoothed, calibration_grid, settings)

    if progress_callback:
        progress_callback("done", len(smoothed), len(refuels), len(drains), len(diagnostics))

    return FuelAnalysisResult(
        points=tuple(smoothed),
        refuels=tuple(refuels),
        drains=tuple(drains),
        diagnostics=tuple(diagnostics),
    )


def run_in_thread_pool(
    tasks: Sequence[tuple[Callable, tuple, dict]],
    *,
    max_workers: int,
    progress_callback: ProgressCallback | None = None,
) -> list:
    """Execute homogeneous callables in a shared thread pool."""
    if not tasks:
        return []

    workers = resolve_io_workers(max_workers, len(tasks))
    results: list = [None] * len(tasks)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(func, *args, **kwargs): index
            for index, (func, args, kwargs) in enumerate(tasks)
        }
        completed = 0
        for future in as_completed(future_map):
            index = future_map[future]
            results[index] = future.result()
            completed += 1
            if progress_callback:
                progress_callback(completed, len(tasks))

    return results


def _convert_segments_parallel(
    segments: Sequence[Sequence[dict]],
    calibration_grid: CalibrationGrid,
    *,
    workers: int,
    progress_callback: ProgressCallback | None = None,
) -> list[list[TelemetryPoint]]:
    if len(segments) == 1:
        return [convert_points_to_litres(segments[0], calibration_grid)]

    config_payload = asdict(AnalysisConfig())
    jobs = [
        (list(segment), calibration_grid, config_payload, index, len(segments))
        for index, segment in enumerate(segments, start=1)
    ]

    converted: list[list[TelemetryPoint] | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_convert_segment_worker, job): job[3] - 1
            for job in jobs
        }
        for future in as_completed(futures):
            index, points = future.result()
            converted[index] = points
            if progress_callback:
                progress_callback("convert", index + 1, len(jobs), len(points))

    return [segment for segment in converted if segment is not None]


def _convert_segment_worker(
    job: tuple[list[dict], CalibrationGrid, dict, int, int],
) -> tuple[int, list[TelemetryPoint]]:
    segment, calibration_grid, _config_payload, index, _total = job
    return index - 1, convert_points_to_litres(segment, calibration_grid)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default
