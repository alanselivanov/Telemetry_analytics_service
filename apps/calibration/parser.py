
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
    pass


@dataclass(frozen=True)
class CalibrationRow:

    sensor_code: int
    tank_litres: tuple[float, ...]
    row_number: int


@dataclass(frozen=True)
class SensorCurve:

    sensor_index: int
    points: tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class CalibrationGrid:

    rows: tuple[CalibrationRow, ...]
    sensor_curves: tuple[SensorCurve, ...]

    @property
    def sensor_count(self) -> int:
        return len(self.sensor_curves)


def parse_calibration_text(text: str) -> CalibrationGrid:
    rows: list[CalibrationRow] = []
    expected_tank_count: int | None = None

    for row_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split(";")]
        if len(parts) < 2:
            raise CalibrationParseError(
                f"Line {row_number}: expected sensor code and at least one tank litres value."
            )

        sensor_code = _parse_sensor_code(parts[0], row_number)
        tank_litres = tuple(_parse_tank_litres(value, row_number) for value in parts[1:])

        if len(tank_litres) > MAX_SENSOR_COUNT:
            raise CalibrationParseError(
                f"Line {row_number}: expected at most {MAX_SENSOR_COUNT} tanks."
            )

        if expected_tank_count is None:
            expected_tank_count = len(tank_litres)
        elif len(tank_litres) != expected_tank_count:
            raise CalibrationParseError(
                f"Line {row_number}: expected {expected_tank_count} tank values, "
                f"got {len(tank_litres)}."
            )

        rows.append(
            CalibrationRow(
                sensor_code=sensor_code,
                tank_litres=tank_litres,
                row_number=row_number,
            )
        )

    if len(rows) < 2:
        raise CalibrationParseError("Calibration table must contain at least two rows.")

    rows.sort(key=lambda row: row.sensor_code)
    return build_calibration_grid(rows)


def parse_calibration_file(path: str | Path, encoding: str = "utf-8-sig") -> CalibrationGrid:
    file_path = Path(path)
    return parse_calibration_text(file_path.read_text(encoding=encoding))


def build_calibration_grid(rows: Iterable[CalibrationRow]) -> CalibrationGrid:
    sorted_rows = tuple(sorted(rows, key=lambda row: row.sensor_code))
    if not sorted_rows:
        raise CalibrationParseError("Calibration rows cannot be empty.")

    tank_count = len(sorted_rows[0].tank_litres)
    per_sensor_points: list[list[tuple[int, float]]] = [[] for _ in range(tank_count)]

    for row in sorted_rows:
        for sensor_index, litres in enumerate(row.tank_litres):
            per_sensor_points[sensor_index].append((row.sensor_code, litres))

    curves = tuple(
        SensorCurve(
            sensor_index=index,
            points=_dedupe_curve_points(points),
        )
        for index, points in enumerate(per_sensor_points)
    )
    return CalibrationGrid(rows=sorted_rows, sensor_curves=curves)


def grid_from_model(table: CalibrationTable) -> CalibrationGrid:
    if table.raw_rows:
        rows = tuple(_calibration_row_from_raw(raw_row) for raw_row in table.raw_rows)
        return build_calibration_grid(rows)

    rows = (
        CalibrationRow(
            sensor_code=int(point.litres),
            tank_litres=tuple(float(value) for value in point.sensor_codes),
            row_number=point.row_number,
        )
        for point in table.points.all()
    )
    return build_calibration_grid(rows)


def _calibration_row_from_raw(raw_row: dict) -> CalibrationRow:
    if "sensor_code" in raw_row:
        return CalibrationRow(
            sensor_code=int(raw_row["sensor_code"]),
            tank_litres=tuple(float(value) for value in raw_row["tank_litres"]),
            row_number=int(raw_row["row_number"]),
        )

    return CalibrationRow(
        sensor_code=int(raw_row["litres"]),
        tank_litres=tuple(float(value) for value in raw_row["sensor_codes"]),
        row_number=int(raw_row["row_number"]),
    )


@transaction.atomic
def save_calibration_grid(
    *,
    vehicle: Vehicle,
    name: str,
    grid: CalibrationGrid,
    source_filename: str = "",
    activate: bool = True,
) -> CalibrationTable:
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
                "sensor_code": row.sensor_code,
                "tank_litres": list(row.tank_litres),
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
                litres=row.sensor_code,
                sensor_codes=list(row.tank_litres),
                row_number=row.row_number,
            )
            for row in grid.rows
        ]
    )
    return table


def _dedupe_curve_points(points: list[tuple[int, float]]) -> tuple[tuple[int, float], ...]:
    by_code: dict[int, float] = {}
    for code, litres in sorted(points, key=lambda item: item[0]):
        by_code[code] = litres
    return tuple((code, by_code[code]) for code in sorted(by_code))


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


def _parse_tank_litres(value: str, row_number: int) -> float:
    try:
        return float(Decimal(value.replace(",", ".")))
    except InvalidOperation as exc:
        raise CalibrationParseError(
            f"Line {row_number}: invalid tank litres value {value!r}."
        ) from exc