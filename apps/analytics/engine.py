"""Fuel analytics engine: interpolation, smoothing, events, and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Sequence

from calibration.parser import CalibrationGrid, SensorCurve

MIN_SENSOR_CODE = 0
MAX_SENSOR_CODE = 4095


@dataclass(frozen=True)
class TelemetryPoint:
    """Raw Omnicomm log point with optional processed fuel values."""

    timestamp: int
    speed: float
    lls_codes: tuple[int | None, ...]
    litres: float | None = None
    smoothed_litres: float | None = None


@dataclass(frozen=True)
class FuelEvent:
    """Detected refuel or drain event."""

    event_type: str
    started_at: int
    ended_at: int
    volume_litres: float
    start_level_litres: float
    end_level_litres: float
    confidence: float


@dataclass(frozen=True)
class SensorDiagnostic:
    """Detected fuel sensor health issue."""

    sensor_index: int
    status: str
    reason: str
    started_at: int | None = None
    ended_at: int | None = None
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FuelAnalysisResult:
    """Full result of one fuel analysis run."""

    points: tuple[TelemetryPoint, ...]
    refuels: tuple[FuelEvent, ...]
    drains: tuple[FuelEvent, ...]
    diagnostics: tuple[SensorDiagnostic, ...]


@dataclass(frozen=True)
class AnalysisConfig:
    """Tunable thresholds for offline fuel analytics."""

    smoothing_window: int = 5
    min_refuel_litres: float = 20.0
    min_drain_litres: float = 15.0
    max_hourly_consumption_litres: float = 60.0
    stable_speed_threshold: float = 0.1
    freeze_min_duration_seconds: int = 30 * 60
    freeze_code_tolerance: int = 1
    jitter_window_points: int = 8
    jitter_min_valid_points: int = 6
    jitter_min_span_codes: int = 700
    intermittent_min_zero_points: int = 2
    intermittent_min_valid_points: int = 4
    diagnostic_merge_gap_seconds: int = 30 * 60


def analyze_fuel_telemetry(
    raw_points: Sequence[dict],
    calibration_grid: CalibrationGrid,
    config: AnalysisConfig | None = None,
) -> FuelAnalysisResult:
    """Run full DУТ analysis for raw Omnicomm click/log rows."""
    settings = config or AnalysisConfig()
    points = convert_points_to_litres(raw_points, calibration_grid)
    smoothed = apply_median_filter(points, window=settings.smoothing_window)
    refuels = detect_refuels(smoothed, settings)
    drains = detect_drains(smoothed, settings)
    diagnostic_points = raw_points_for_sensor_diagnostics(raw_points)
    diagnostics = diagnose_sensors(
        smoothed,
        calibration_grid,
        settings,
        diagnostic_points=diagnostic_points,
    )

    return FuelAnalysisResult(
        points=tuple(smoothed),
        refuels=tuple(refuels),
        drains=tuple(drains),
        diagnostics=tuple(diagnostics),
    )


def convert_points_to_litres(
    raw_points: Sequence[dict],
    calibration_grid: CalibrationGrid,
) -> list[TelemetryPoint]:
    """Convert Omnicomm rows with ``LLS_CODE`` arrays into total fuel litres."""
    points: list[TelemetryPoint] = []

    for row in raw_points:
        raw_codes = row.get("LLS_CODE") or []
        codes = tuple(_normalize_code(value) for value in raw_codes)
        litres = codes_to_litres(codes, calibration_grid)
        if litres is None:
            continue
        if litres == 0.0 and _is_zero_only_reading(codes, calibration_grid.sensor_count):
            continue

        timestamp = row.get("EVENT_DATE") or row.get("TIME")
        if timestamp is None:
            continue

        points.append(
            TelemetryPoint(
                timestamp=int(timestamp),
                speed=float(row.get("SPEED") or 0),
                lls_codes=codes,
                litres=litres,
            )
        )

    points.sort(key=lambda point: point.timestamp)
    return points


def raw_points_for_sensor_diagnostics(
    raw_points: Sequence[dict],
) -> list[TelemetryPoint]:
    """Build code-only telemetry points for sensor health diagnostics."""
    points: list[TelemetryPoint] = []

    for row in raw_points:
        timestamp = row.get("EVENT_DATE") or row.get("TIME")
        if timestamp is None:
            continue

        raw_codes = row.get("LLS_CODE") or []
        points.append(
            TelemetryPoint(
                timestamp=int(timestamp),
                speed=float(row.get("SPEED") or 0),
                lls_codes=tuple(_normalize_code(value) for value in raw_codes),
            )
        )

    points.sort(key=lambda point: point.timestamp)
    return points


def codes_to_litres(
    codes: Sequence[int | None],
    calibration_grid: CalibrationGrid,
) -> float | None:
    """Interpolate each tank and sum litres when every tank is measurable."""
    total = 0.0

    for curve in calibration_grid.sensor_curves:
        code = codes[curve.sensor_index] if curve.sensor_index < len(codes) else None
        contribution = _sensor_litres_contribution(code, curve)
        if contribution is None:
            return None
        total += contribution

    return total


def _sensor_litres_contribution(code: int | None, curve: SensorCurve) -> float | None:
    if code is None:
        return None
    if code == 0:
        return 0.0
    if code < MIN_SENSOR_CODE or code > MAX_SENSOR_CODE:
        return None
    return interpolate_code_to_litres(code, curve)


def interpolate_code_to_litres(code: int, curve: SensorCurve) -> float | None:
    """Piecewise-linear interpolation of one raw sensor code into litres."""
    if code < MIN_SENSOR_CODE or code > MAX_SENSOR_CODE:
        return None

    points = curve.points
    if not points:
        return None

    min_code = points[0][0]
    max_code = points[-1][0]
    if code < min_code or code > max_code:
        return None

    if code == min_code:
        return points[0][1]
    if code == max_code:
        return points[-1][1]

    for (left_code, left_litres), (right_code, right_litres) in zip(points, points[1:]):
        if left_code <= code <= right_code:
            if right_code == left_code:
                return right_litres
            ratio = (code - left_code) / (right_code - left_code)
            return left_litres + ratio * (right_litres - left_litres)

    return None


def apply_median_filter(
    points: Sequence[TelemetryPoint],
    window: int = 5,
) -> list[TelemetryPoint]:
    """Smooth fuel bounce using a median filter."""
    if window <= 1 or not points:
        return [
            TelemetryPoint(
                timestamp=point.timestamp,
                speed=point.speed,
                lls_codes=point.lls_codes,
                litres=point.litres,
                smoothed_litres=point.litres,
            )
            for point in points
        ]

    radius = max(1, window // 2)
    result: list[TelemetryPoint] = []

    for index, point in enumerate(points):
        left = max(0, index - radius)
        right = min(len(points), index + radius + 1)
        values = [
            candidate.litres
            for candidate in points[left:right]
            if candidate.litres is not None
        ]
        smoothed = float(median(values)) if values else point.litres
        result.append(
            TelemetryPoint(
                timestamp=point.timestamp,
                speed=point.speed,
                lls_codes=point.lls_codes,
                litres=point.litres,
                smoothed_litres=smoothed,
            )
        )

    return result


def detect_refuels(
    points: Sequence[TelemetryPoint],
    config: AnalysisConfig,
) -> list[FuelEvent]:
    """Detect stable fuel growth while the vehicle is stopped."""
    events: list[FuelEvent] = []
    candidate_start: TelemetryPoint | None = None
    previous: TelemetryPoint | None = None

    for point in points:
        if point.smoothed_litres is None:
            continue

        if previous is None:
            previous = point
            continue

        delta = point.smoothed_litres - (previous.smoothed_litres or 0)
        stopped = point.speed <= config.stable_speed_threshold

        if stopped and delta > 0:
            candidate_start = candidate_start or previous
        elif candidate_start is not None:
            volume = (previous.smoothed_litres or 0) - (
                candidate_start.smoothed_litres or 0
            )
            if volume >= config.min_refuel_litres:
                events.append(
                    _build_event("REFUEL", candidate_start, previous, volume)
                )
            candidate_start = None

        previous = point

    if candidate_start is not None and previous is not None:
        volume = (previous.smoothed_litres or 0) - (candidate_start.smoothed_litres or 0)
        if volume >= config.min_refuel_litres:
            events.append(_build_event("REFUEL", candidate_start, previous, volume))

    return events


def detect_drains(
    points: Sequence[TelemetryPoint],
    config: AnalysisConfig,
) -> list[FuelEvent]:
    """Detect sharp drops exceeding plausible engine consumption."""
    events: list[FuelEvent] = []

    for previous, point in zip(points, points[1:]):
        if previous.smoothed_litres is None or point.smoothed_litres is None:
            continue

        delta = point.smoothed_litres - previous.smoothed_litres
        if delta >= 0:
            continue

        elapsed_seconds = max(1, point.timestamp - previous.timestamp)
        allowed_drop = config.max_hourly_consumption_litres * elapsed_seconds / 3600
        actual_drop = abs(delta)

        if actual_drop >= config.min_drain_litres and actual_drop > allowed_drop:
            net_drop = (previous.smoothed_litres or 0) - (point.smoothed_litres or 0)
            if net_drop >= config.min_drain_litres:
                events.append(
                    _build_event("DRAIN", previous, point, net_drop)
                )

    return _merge_adjacent_events(events, "DRAIN")


def diagnose_sensors(
    points: Sequence[TelemetryPoint],
    calibration_grid: CalibrationGrid,
    config: AnalysisConfig,
    *,
    diagnostic_points: Sequence[TelemetryPoint] | None = None,
) -> list[SensorDiagnostic]:
    """Detect frozen, invalid, and chaotic fuel sensor behaviour."""
    code_points = list(diagnostic_points or points)
    diagnostics: list[SensorDiagnostic] = []
    diagnostics.extend(_diagnose_invalid_codes(code_points, calibration_grid))
    diagnostics.extend(_diagnose_out_of_grid_codes(code_points, calibration_grid))
    diagnostics.extend(_diagnose_long_freeze(points, calibration_grid.sensor_count, config))
    diagnostics.extend(_diagnose_intermittent_signal(points, calibration_grid.sensor_count, config))
    diagnostics.extend(_diagnose_stationary_jitter(points, calibration_grid.sensor_count, config))
    return _merge_sensor_diagnostics(diagnostics, config.diagnostic_merge_gap_seconds)


def _diagnose_invalid_codes(
    points: Sequence[TelemetryPoint],
    calibration_grid: CalibrationGrid,
) -> list[SensorDiagnostic]:
    diagnostics: list[SensorDiagnostic] = []
    sensor_count = calibration_grid.sensor_count

    for point in points:
        for sensor_index in range(sensor_count):
            code = (
                point.lls_codes[sensor_index]
                if sensor_index < len(point.lls_codes)
                else None
            )
            if code is None or code < MIN_SENSOR_CODE or code > MAX_SENSOR_CODE:
                diagnostics.append(
                    SensorDiagnostic(
                        sensor_index=sensor_index,
                        status="Датчик не в порядке / Требуется диагностика",
                        reason="Некорректный код ДУТ",
                        started_at=point.timestamp,
                        ended_at=point.timestamp,
                        details={"code": code},
                    )
                )

    return diagnostics


def _diagnose_out_of_grid_codes(
    points: Sequence[TelemetryPoint],
    calibration_grid: CalibrationGrid,
) -> list[SensorDiagnostic]:
    """Detect sensor codes that fall outside the calibration grid range."""
    diagnostics: list[SensorDiagnostic] = []

    for point in points:
        for curve in calibration_grid.sensor_curves:
            code = (
                point.lls_codes[curve.sensor_index]
                if curve.sensor_index < len(point.lls_codes)
                else None
            )
            if code is None or code <= 0:
                continue
            if code < MIN_SENSOR_CODE or code > MAX_SENSOR_CODE:
                continue

            curve_points = curve.points
            if not curve_points:
                diagnostics.append(
                    SensorDiagnostic(
                        sensor_index=curve.sensor_index,
                        status="Датчик не в порядке / Требуется диагностика",
                        reason="Код ДУТ вне тарировочной сетки",
                        started_at=point.timestamp,
                        ended_at=point.timestamp,
                        details={"code": code},
                    )
                )
                continue

            min_code = curve_points[0][0]
            max_code = curve_points[-1][0]
            if code < min_code or code > max_code:
                diagnostics.append(
                    SensorDiagnostic(
                        sensor_index=curve.sensor_index,
                        status="Датчик не в порядке / Требуется диагностика",
                        reason="Код ДУТ вне тарировочной сетки",
                        started_at=point.timestamp,
                        ended_at=point.timestamp,
                        details={
                            "code": code,
                            "grid_min_code": min_code,
                            "grid_max_code": max_code,
                        },
                    )
                )

    return diagnostics


def _diagnose_long_freeze(
    points: Sequence[TelemetryPoint],
    sensor_count: int,
    config: AnalysisConfig,
) -> list[SensorDiagnostic]:
    diagnostics: list[SensorDiagnostic] = []
    if not points:
        return diagnostics

    for sensor_index in range(sensor_count):
        freeze_start: TelemetryPoint | None = None
        freeze_code: int | None = None
        last_moving: TelemetryPoint | None = None

        for point in points:
            if point.speed <= config.stable_speed_threshold:
                freeze_start = None
                freeze_code = None
                last_moving = None
                continue

            code = point.lls_codes[sensor_index] if sensor_index < len(point.lls_codes) else None
            if code is None:
                continue

            if freeze_code is None or abs(code - freeze_code) > config.freeze_code_tolerance:
                freeze_start = point
                freeze_code = code
            last_moving = point

            if freeze_start and last_moving:
                duration = last_moving.timestamp - freeze_start.timestamp
                if duration >= config.freeze_min_duration_seconds:
                    diagnostics.append(
                        SensorDiagnostic(
                            sensor_index=sensor_index,
                            status="Датчик не в порядке / Требуется диагностика",
                            reason="Long Freeze: код ДУТ не меняется в движении",
                            started_at=freeze_start.timestamp,
                            ended_at=last_moving.timestamp,
                            details={"code": freeze_code, "duration_seconds": duration},
                        )
                    )
                    freeze_start = None
                    freeze_code = None

    return diagnostics


def _diagnose_intermittent_signal(
    points: Sequence[TelemetryPoint],
    sensor_count: int,
    config: AnalysisConfig,
) -> list[SensorDiagnostic]:
    """Detect alternating valid readings and zeros while the vehicle is stopped."""
    diagnostics: list[SensorDiagnostic] = []
    if not points:
        return diagnostics

    for sensor_index in range(sensor_count):
        window: list[TelemetryPoint] = []

        for point in points:
            if point.speed > config.stable_speed_threshold:
                window = []
                continue

            window.append(point)
            if len(window) < config.jitter_window_points:
                continue
            if len(window) > config.jitter_window_points:
                window.pop(0)

            values = _sensor_codes_from_window(window, sensor_index)
            valid_values = [value for value in values if _is_valid_sensor_code(value)]
            zero_count = sum(1 for value in values if value == 0)

            if (
                len(valid_values) >= config.intermittent_min_valid_points
                and zero_count >= config.intermittent_min_zero_points
                and max(valid_values) - min(valid_values) < config.jitter_min_span_codes
            ):
                diagnostics.append(
                    SensorDiagnostic(
                        sensor_index=sensor_index,
                        status="Датчик не в порядке / Требуется диагностика",
                        reason="Пропадание сигнала ДУТ",
                        started_at=window[0].timestamp,
                        ended_at=window[-1].timestamp,
                        details={
                            "zero_points": zero_count,
                            "valid_points": len(valid_values),
                        },
                    )
                )
                window = []

    return diagnostics


def _diagnose_stationary_jitter(
    points: Sequence[TelemetryPoint],
    sensor_count: int,
    config: AnalysisConfig,
) -> list[SensorDiagnostic]:
    diagnostics: list[SensorDiagnostic] = []
    if not points:
        return diagnostics

    for sensor_index in range(sensor_count):
        window: list[TelemetryPoint] = []

        for point in points:
            if point.speed > config.stable_speed_threshold:
                window = []
                continue

            window.append(point)
            if len(window) < config.jitter_window_points:
                continue
            if len(window) > config.jitter_window_points:
                window.pop(0)

            values = _sensor_codes_from_window(window, sensor_index)
            valid_values = [value for value in values if _is_valid_sensor_code(value)]
            if len(valid_values) < config.jitter_min_valid_points:
                continue

            span = max(valid_values) - min(valid_values)
            if span >= config.jitter_min_span_codes:
                diagnostics.append(
                    SensorDiagnostic(
                        sensor_index=sensor_index,
                        status="Датчик не в порядке / Требуется диагностика",
                        reason="Хаотичный дребезг на стоянке",
                        started_at=window[0].timestamp,
                        ended_at=window[-1].timestamp,
                        details={"code_span": span},
                    )
                )
                window = []

    return diagnostics


def _build_event(
    event_type: str,
    start: TelemetryPoint,
    end: TelemetryPoint,
    volume: float,
) -> FuelEvent:
    return FuelEvent(
        event_type=event_type,
        started_at=start.timestamp,
        ended_at=end.timestamp,
        volume_litres=round(volume, 3),
        start_level_litres=round(start.smoothed_litres or 0, 3),
        end_level_litres=round(end.smoothed_litres or 0, 3),
        confidence=0.8,
    )


def _merge_adjacent_events(events: Sequence[FuelEvent], event_type: str) -> list[FuelEvent]:
    if not events:
        return []

    merged: list[FuelEvent] = [events[0]]
    for event in events[1:]:
        previous = merged[-1]
        if event.started_at - previous.ended_at <= 10 * 60:
            net_volume = max(
                0.0,
                previous.start_level_litres - event.end_level_litres,
            )
            merged[-1] = FuelEvent(
                event_type=event_type,
                started_at=previous.started_at,
                ended_at=event.ended_at,
                volume_litres=round(net_volume, 3),
                start_level_litres=previous.start_level_litres,
                end_level_litres=event.end_level_litres,
                confidence=min(previous.confidence, event.confidence),
            )
        else:
            merged.append(event)

    return merged


def _merge_sensor_diagnostics(
    diagnostics: Sequence[SensorDiagnostic],
    merge_gap_seconds: int,
) -> list[SensorDiagnostic]:
    """Merge adjacent diagnostics for the same sensor and reason."""
    if not diagnostics:
        return []

    ordered = sorted(
        diagnostics,
        key=lambda item: (item.sensor_index, item.reason, item.started_at or 0),
    )
    merged: list[SensorDiagnostic] = [ordered[0]]

    for diagnostic in ordered[1:]:
        previous = merged[-1]
        if (
            previous.sensor_index == diagnostic.sensor_index
            and previous.reason == diagnostic.reason
            and previous.ended_at is not None
            and diagnostic.started_at is not None
            and diagnostic.started_at - previous.ended_at <= merge_gap_seconds
        ):
            merged[-1] = SensorDiagnostic(
                sensor_index=previous.sensor_index,
                status=previous.status,
                reason=previous.reason,
                started_at=previous.started_at,
                ended_at=max(previous.ended_at or 0, diagnostic.ended_at or 0),
                details=previous.details,
            )
        else:
            merged.append(diagnostic)

    return merged


def _sensor_codes_from_window(
    window: Sequence[TelemetryPoint],
    sensor_index: int,
) -> list[int | None]:
    return [
        point.lls_codes[sensor_index]
        if sensor_index < len(point.lls_codes)
        else None
        for point in window
    ]


def _is_zero_only_reading(
    codes: Sequence[int | None],
    sensor_count: int,
) -> bool:
    """Treat all-zero LLS payloads as missing signal, not an empty tank."""
    for sensor_index in range(sensor_count):
        code = codes[sensor_index] if sensor_index < len(codes) else None
        if code not in (None, 0):
            return False
    return True


def _is_valid_sensor_code(code: int | None) -> bool:
    return code is not None and MIN_SENSOR_CODE < code <= MAX_SENSOR_CODE


def _normalize_code(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
