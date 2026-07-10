"""Parse flexible time range strings and split long periods into API chunks."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

CHUNK_MAX_DAYS = 7
SECONDS_PER_DAY = 86_400

_RELATIVE_RANGE = re.compile(
    r"^(?:past|last)?\s*(\d+)\s*"
    r"(hour|hours|hr|h|day|days|d|week|weeks|wk|w|month|months|mo|m)\s*$",
    re.IGNORECASE,
)
_RELATIVE_SINGLE = re.compile(
    r"^(?:past|last)\s+(hour|day|week|month)s?\s*$",
    re.IGNORECASE,
)
_EXPLICIT_RANGE = re.compile(
    r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{2})\.(\d{2})\.(\d{4})\s*$"
)

_UNIT_TO_SECONDS: dict[str, int] = {
    "hour": 3_600,
    "hours": 3_600,
    "hr": 3_600,
    "h": 3_600,
    "day": SECONDS_PER_DAY,
    "days": SECONDS_PER_DAY,
    "d": SECONDS_PER_DAY,
    "week": 7 * SECONDS_PER_DAY,
    "weeks": 7 * SECONDS_PER_DAY,
    "wk": 7 * SECONDS_PER_DAY,
    "w": 7 * SECONDS_PER_DAY,
    "month": 30 * SECONDS_PER_DAY,
    "months": 30 * SECONDS_PER_DAY,
    "mo": 30 * SECONDS_PER_DAY,
    "m": 30 * SECONDS_PER_DAY,
}


class TimeRangeParseError(ValueError):
    """Raised when a time range string cannot be interpreted."""


def parse_time_range(text: str, now: datetime | None = None) -> tuple[int, int]:
    normalized = " ".join(text.strip().split())
    if not normalized:
        raise TimeRangeParseError("Time range cannot be empty.")

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    explicit = _EXPLICIT_RANGE.match(normalized)
    if explicit:
        return _parse_explicit_range(explicit, reference.tzinfo)

    relative = _RELATIVE_RANGE.match(normalized)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        return _relative_range(reference, amount, unit)

    single = _RELATIVE_SINGLE.match(normalized)
    if single:
        unit = single.group(1).lower()
        return _relative_range(reference, 1, unit)

    raise TimeRangeParseError(
        "Unrecognized time range format. Examples: '1 hour', '2 weeks', "
        "'3 months', '01.07.2026 - 05.07.2026'."
    )


def chunk_time_range(
    date_from: int,
    date_to: int,
    max_days: int = CHUNK_MAX_DAYS,
) -> list[tuple[int, int]]:
    """
    Split ``[date_from, date_to]`` into contiguous chunks of at most ``max_days`` days.

    Each chunk starts exactly where the previous one ended, so no time is skipped.
    """
    if date_from >= date_to:
        raise ValueError("dateFrom must be earlier than dateTo.")

    tz = timezone.utc
    range_start = datetime.fromtimestamp(date_from, tz=tz)
    range_end = datetime.fromtimestamp(date_to, tz=tz)
    max_delta = timedelta(days=max_days)

    if range_end - range_start <= max_delta:
        return [(date_from, date_to)]

    chunks: list[tuple[int, int]] = []
    chunk_start = range_start

    while chunk_start < range_end:
        chunk_end = min(chunk_start + max_delta, range_end)
        chunk_start_ts = int(chunk_start.timestamp())
        chunk_end_ts = int(chunk_end.timestamp())

        if chunk_end_ts <= chunk_start_ts:
            raise ValueError("Chunk stepping failed; time range could not be advanced.")

        chunks.append((chunk_start_ts, chunk_end_ts))
        chunk_start = chunk_end

    _validate_chunks_cover_range(chunks, date_from, date_to)
    return chunks


def format_timestamp(ts: int, tz: timezone | None = None) -> str:
    zone = tz or timezone.utc
    return datetime.fromtimestamp(ts, tz=zone).strftime("%d.%m.%Y %H:%M:%S UTC")


def _validate_chunks_cover_range(
    chunks: list[tuple[int, int]],
    date_from: int,
    date_to: int,
) -> None:
    if not chunks:
        raise ValueError("Chunk list cannot be empty.")

    if chunks[0][0] != date_from:
        raise ValueError("First chunk does not start at dateFrom.")

    if chunks[-1][1] != date_to:
        raise ValueError("Last chunk does not end at dateTo.")

    for index in range(len(chunks) - 1):
        current_end = chunks[index][1]
        next_start = chunks[index + 1][0]
        if next_start != current_end:
            raise ValueError(
                "Gap detected between chunks "
                f"{index + 1} and {index + 2}: "
                f"previous chunk ends at {format_timestamp(current_end)}, "
                f"next chunk starts at {format_timestamp(next_start)}."
            )


def _relative_range(reference: datetime, amount: int, unit: str) -> tuple[int, int]:
    if amount <= 0:
        raise TimeRangeParseError("Relative time amount must be greater than zero.")

    seconds = _UNIT_TO_SECONDS.get(unit)
    if seconds is None:
        raise TimeRangeParseError(f"Unsupported time unit: {unit}")

    date_to = int(reference.timestamp())
    date_from = date_to - amount * seconds
    return date_from, date_to


def _parse_explicit_range(
    match: re.Match[str],
    tz: timezone,
) -> tuple[int, int]:
    start = datetime(
        int(match.group(3)),
        int(match.group(2)),
        int(match.group(1)),
        tzinfo=tz,
    )
    end = datetime(
        int(match.group(6)),
        int(match.group(5)),
        int(match.group(4)),
        23,
        59,
        59,
        tzinfo=tz,
    )

    date_from = int(start.timestamp())
    date_to = int(end.timestamp())

    if date_from >= date_to:
        raise TimeRangeParseError("Start date must be earlier than end date.")

    return date_from, date_to
