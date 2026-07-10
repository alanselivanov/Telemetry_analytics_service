"""Parsing and in-memory representation of fuel sensor calibration tables."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from django.db import transaction

from .models import CalibrationPoint, CalibrationTable, Vehicle

MIN_SENSOR_CODE = 0
MAX_SENSOR_CODE = 4095
MAX_SENSOR_COUNT = 16


class CalibrationParseError(ValueError):
    """Raised when a calibration CSV/TXT file has invalid structure."""


@dataclass(frozen=True)
class CalibrationRow:
    """One parsed calibration row."""

    litres: Decimal
    sensor_codes: tuple[int, ...]
    row_number: int


@dataclass(frozen=True)
class SensorCurve:
    """Piecewise-linear curve for a single LLS sensor."""

    sensor_index: int
    points: tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class CalibrationGrid:
    """Prepared interpolation grid built from calibration rows."""

    rows: tuple[CalibrationRow, ...]
    sensor_curves: tuple[SensorCurve, ...]

    @property
    def sensor_count(self) -> int:
        return len(self.sensor_curves)


def parse_calibration_text(text: str) -> CalibrationGrid:
    """Parse semicolon-separated calibration content into an interpolation grid."""
    rows: list[CalibrationRow] = []
    expected_sensor_count: int | None = None

    for row_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split(";")]
        if len(parts) < 2:
            raise CalibrationParseError(
                f"Line {row_number}: expected litres and at least one sensor code."
            )

        try:
            litres = Decimal(parts[0].replace(",", "."))
        except InvalidOperation as exc:
            raise CalibrationParseError(
                f"Line {row_number}: invalid litres value {parts[0]!r}."
            ) from exc

        sensor_codes = tuple(_parse_sensor_code(value, row_number) for value in parts[1:])

        if len(sensor_codes) > MAX_SENSOR_COUNT:
            raise CalibrationParseError(
                f"Line {row_number}: expected at most {MAX_SENSOR_COUNT} sensors."
            )

        if expected_sensor_count is None:
            expected_sensor_count = len(sensor_codes)
        elif len(sensor_codes) != expected_sensor_count:
            raise CalibrationParseError(
                f"Line {row_number}: expected {expected_sensor_count} sensor codes, "
                f"got {len(sensor_codes)}."
            )

        rows.append(
            CalibrationRow(
                litres=litres,
                sensor_codes=sensor_codes,
                row_number=row_number,
            )
        )

    if len(rows) < 2:
        raise CalibrationParseError("Calibration table must contain at least two rows.")

    rows.sort(key=lambda row: row.litres)
    return build_calibration_grid(rows)


def parse_calibration_file(path: str | Path, encoding: str = "utf-8-sig") -> CalibrationGrid:
    """Parse a CSV/TXT calibration file from disk."""
    file_path = Path(path)
    return parse_calibration_text(file_path.read_text(encoding=encoding))


def build_calibration_grid(rows: Iterable[CalibrationRow]) -> CalibrationGrid:
    """
    Build per-sensor interpolation curves.

    The file stores total litres in column 1. For multi-sensor vehicles we split
    each row's total litres evenly across active sensors, then sum per-sensor
    interpolated values during analytics.
    """
    sorted_rows = tuple(sorted(rows, key=lambda row: row.litres))
    if not sorted_rows:
        raise CalibrationParseError("Calibration rows cannot be empty.")

    sensor_count = len(sorted_rows[0].sensor_codes)
    per_sensor_points: list[list[tuple[int, float]]] = [[] for _ in range(sensor_count)]

    for row in sorted_rows:
        active_count = max(1, sum(1 for code in row.sensor_codes if code > 0))
        litres_share = float(row.litres) / active_count

        for sensor_index, code in enumerate(row.sensor_codes):
            if code <= 0 and float(row.litres) > 0:
                continue
            per_sensor_points[sensor_index].append((code, litres_share))

    curves = tuple(
        SensorCurve(
            sensor_index=index,
            points=tuple(sorted(set(points), key=lambda point: point[0])),
        )
        for index, points in enumerate(per_sensor_points)
    )
    return CalibrationGrid(rows=sorted_rows, sensor_curves=curves)


def grid_from_model(table: CalibrationTable) -> CalibrationGrid:
    """Build an interpolation grid from a persisted calibration table."""
    rows = (
        CalibrationRow(
            litres=point.litres,
            sensor_codes=tuple(int(code) for code in point.sensor_codes),
            row_number=point.row_number,
        )
        for point in table.points.all()
    )
    return build_calibration_grid(rows)


@transaction.atomic
def save_calibration_grid(
    *,
    vehicle: Vehicle,
    name: str,
    grid: CalibrationGrid,
    source_filename: str = "",
    activate: bool = True,
) -> CalibrationTable:
    """Persist a parsed grid and its points for one vehicle."""
    if activate:
        CalibrationTable.objects.filter(vehicle=vehicle, is_active=True).update(
            is_active=False
        )

    table = CalibrationTable.objects.create(
        vehicle=vehicle,
        name=name,
        sensor_count=grid.sensor_count,
        source_filename=source_filename,
        raw_rows=[
            {
                "litres": str(row.litres),
                "sensor_codes": list(row.sensor_codes),
                "row_number": row.row_number,
            }
            for row in grid.rows
        ],
        is_active=activate,
    )

    CalibrationPoint.objects.bulk_create(
        [
            CalibrationPoint(
                table=table,
                litres=row.litres,
                sensor_codes=list(row.sensor_codes),
                row_number=row.row_number,
            )
            for row in grid.rows
        ]
    )
    return table


def _parse_sensor_code(value: str, row_number: int) -> int:
    try:
        code = int(value)
    except ValueError as exc:
        raise CalibrationParseError(
            f"Line {row_number}: invalid sensor code {value!r}."
        ) from exc

    if code < MIN_SENSOR_CODE or code > MAX_SENSOR_CODE:
        raise CalibrationParseError(
            f"Line {row_number}: sensor code {code} is outside 0-4095."
        )

    return code
