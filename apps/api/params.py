"""Query parameter parsing for REST API time filters."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from cli.time_range import TimeRangeParseError, parse_time_range

_DATE_ONLY = re.compile(r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*$")


class ApiPeriodParseError(ValueError):
    """Raised when API period query parameters are invalid."""


def parse_api_period(
    *,
    date_from: str | None,
    date_to: str | None,
    period: str | None,
) -> tuple[int, int]:
    """
    Resolve ``from``/``to`` or ``period`` query params into unix timestamps.

    Supported forms:
    - ``from=1778503454&to=1783687454``
    - ``from=11.05.2026&to=10.07.2026``
    - ``period=2 months``
    """
    if period:
        try:
            return parse_time_range(period)
        except TimeRangeParseError as exc:
            raise ApiPeriodParseError(str(exc)) from exc

    if not date_from or not date_to:
        raise ApiPeriodParseError(
            "Provide either 'period' or both 'from' and 'to' query parameters."
        )

    try:
        start = _parse_api_timestamp(date_from)
        end = _parse_api_timestamp(date_to, end_of_day=True)
    except TimeRangeParseError as exc:
        raise ApiPeriodParseError(str(exc)) from exc

    if start >= end:
        raise ApiPeriodParseError("'from' must be earlier than 'to'.")

    return start, end


def _parse_api_timestamp(value: str, *, end_of_day: bool = False) -> int:
    normalized = value.strip()
    if not normalized:
        raise TimeRangeParseError("Timestamp value cannot be empty.")

    if normalized.isdigit():
        return int(normalized)

    match = _DATE_ONLY.match(normalized)
    if not match:
        raise TimeRangeParseError(
            f"Unsupported date format: {value!r}. Use unix timestamp or DD.MM.YYYY."
        )

    day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if end_of_day:
        dt = datetime(year, month, day, 23, 59, 59, tzinfo=timezone.utc)
    else:
        dt = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp())
