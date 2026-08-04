"""Nominal filename time versus authoritative NMR metadata time.

The filename token ``sequence-HHMM`` is a nominal clock label.  The actual
acquisition timestamp must come from JCAMP metadata (normally ``LONG DATE``),
not from the filename or file modification time fallbacks.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
import re
from typing import Mapping, Sequence


_SEQUENCE_HHMM_RE = re.compile(r"sequence-(\d{4})(?!\d)", re.IGNORECASE)

TIMING_COMPARISON_COLUMNS = [
    "acquisition_index",
    "file",
    "filename_sequence",
    "nominal_timestamp",
    "nominal_time_source",
    "metadata_timestamp",
    "metadata_time_source",
    "nominal_elapsed_hours",
    "actual_elapsed_hours",
    "elapsed_timing_offset_minutes",
    "absolute_elapsed_timing_offset_minutes",
    "clock_time_offset_minutes",
    "comparison_qc_pass",
    "comparison_qc_reason",
]


def parse_filename_nominal_time(filename: str | Path) -> tuple[time | None, str]:
    """Parse a nominal ``sequence-HHMM`` time without treating it as metadata."""

    match = _SEQUENCE_HHMM_RE.search(Path(filename).name)
    if match is None:
        return None, ""
    token = match.group(1)
    try:
        return time(hour=int(token[:2]), minute=int(token[2:])), token
    except ValueError:
        return None, token


def is_metadata_timestamp_source(source: str) -> bool:
    """Return whether *source* identifies an authoritative metadata field."""

    normalized = str(source or "").casefold()
    return (
        "long date" in normalized
        or "$date" in normalized
        or "run metadata" in normalized
    )


def _parse_timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_timing_comparison_rows(
    measurements: Sequence[Mapping[str, object]],
) -> list[dict]:
    """Build the shared table behind all expected-versus-actual timing plots.

    ``elapsed_timing_offset_minutes`` is
    ``(actual_elapsed_hours - nominal_elapsed_hours) * 60``.  Elapsed time is
    anchored independently to the first actual and first nominal acquisition.
    ``clock_time_offset_minutes`` retains the absolute per-file difference
    between the metadata timestamp and the filename clock label.
    """

    parsed: list[dict] = []
    first_actual: datetime | None = None
    nominal_base_date = None
    previous_nominal_minutes: int | None = None
    nominal_day_offset = 0
    first_nominal: datetime | None = None

    for index, measurement in enumerate(measurements, 1):
        filename = str(measurement.get("file") or measurement.get("source_file") or "")
        nominal_clock, sequence = parse_filename_nominal_time(filename)
        actual = _parse_timestamp(measurement.get("timestamp"))
        actual_source = str(measurement.get("timestamp_source") or "none")
        metadata_valid = actual is not None and is_metadata_timestamp_source(actual_source)
        if metadata_valid and first_actual is None:
            first_actual = actual
        if nominal_base_date is None and actual is not None:
            nominal_base_date = actual.date()

        nominal: datetime | None = None
        if nominal_clock is not None and nominal_base_date is not None:
            minutes = nominal_clock.hour * 60 + nominal_clock.minute
            if (
                previous_nominal_minutes is not None
                and minutes < previous_nominal_minutes
                and previous_nominal_minutes - minutes > 12 * 60
            ):
                nominal_day_offset += 1
            previous_nominal_minutes = minutes
            nominal = datetime.combine(
                nominal_base_date + timedelta(days=nominal_day_offset),
                nominal_clock,
                tzinfo=actual.tzinfo if actual is not None else None,
            )
            if first_nominal is None:
                first_nominal = nominal

        reasons: list[str] = []
        if nominal_clock is None:
            reasons.append("filename lacks a valid sequence-HHMM token")
        if actual is None:
            reasons.append("actual timestamp is unavailable")
        elif not metadata_valid:
            reasons.append(f"timestamp source is not metadata: {actual_source}")

        nominal_elapsed = (
            (nominal - first_nominal).total_seconds() / 3600.0
            if nominal is not None and first_nominal is not None
            else None
        )
        actual_elapsed = (
            (actual - first_actual).total_seconds() / 3600.0
            if metadata_valid and first_actual is not None
            else None
        )
        elapsed_offset = (
            60.0 * (actual_elapsed - nominal_elapsed)
            if actual_elapsed is not None and nominal_elapsed is not None
            else None
        )
        clock_offset = (
            (actual - nominal).total_seconds() / 60.0
            if metadata_valid and nominal is not None
            else None
        )
        parsed.append(
            {
                "acquisition_index": index,
                "file": filename,
                "filename_sequence": sequence,
                "nominal_timestamp": nominal.isoformat() if nominal else "",
                "nominal_time_source": (
                    f"filename sequence-{sequence} (HHMM)" if nominal_clock else "none"
                ),
                "metadata_timestamp": actual.isoformat() if metadata_valid else "",
                "metadata_time_source": actual_source,
                "nominal_elapsed_hours": nominal_elapsed,
                "actual_elapsed_hours": actual_elapsed,
                "elapsed_timing_offset_minutes": elapsed_offset,
                "absolute_elapsed_timing_offset_minutes": (
                    abs(elapsed_offset) if elapsed_offset is not None else None
                ),
                "clock_time_offset_minutes": clock_offset,
                "comparison_qc_pass": not reasons,
                "comparison_qc_reason": "; ".join(reasons),
            }
        )
    return parsed
