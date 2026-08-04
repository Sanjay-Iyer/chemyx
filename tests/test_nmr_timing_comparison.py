"""Nominal filename time versus authoritative metadata acquisition time."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
import sys

import pytest

from chemyx_lab.analysis.nmr import read_jcamp_fid
from chemyx_lab.analysis.timing_comparison import (
    TIMING_COMPARISON_COLUMNS,
    build_timing_comparison_rows,
    is_metadata_timestamp_source,
    parse_filename_nominal_time,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
NMR_SCRIPTS = REPO_ROOT / "scripts" / "nmr"
if str(NMR_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(NMR_SCRIPTS))

from _common import parse_acquisition_timestamp  # noqa: E402


def _measurements():
    return [
        {
            "file": "sample(sequence-0900)-06-09-26.dx",
            "timestamp": "2026-06-09T09:13:16",
            "timestamp_source": "LONG DATE header",
        },
        {
            "file": "sample(sequence-1000)-06-09-26.dx",
            "timestamp": "2026-06-09T10:07:43",
            "timestamp_source": "LONG DATE header",
        },
        {
            "file": "sample(sequence-1015)-06-09-26.dx",
            "timestamp": "2026-06-09T10:26:05",
            "timestamp_source": "LONG DATE header",
        },
    ]


def test_filename_nominal_hhmm_parser_is_strict_and_preserves_token():
    parsed, token = parse_filename_nominal_time(
        "CEC-PhSi2-flow(sequence-1015)-06-09-26.dx"
    )
    assert token == "1015"
    assert (parsed.hour, parsed.minute) == (10, 15)
    assert parse_filename_nominal_time("sample(sequence-2560).dx")[0] is None
    assert parse_filename_nominal_time("sample.dx") == (None, "")


def test_timing_offsets_use_independently_anchored_elapsed_times():
    rows = build_timing_comparison_rows(_measurements())
    assert all(row["comparison_qc_pass"] for row in rows)
    assert rows[0]["nominal_elapsed_hours"] == 0.0
    assert rows[0]["actual_elapsed_hours"] == 0.0
    assert rows[0]["elapsed_timing_offset_minutes"] == 0.0
    assert rows[0]["clock_time_offset_minutes"] == pytest.approx(13 + 16 / 60)
    assert rows[1]["nominal_elapsed_hours"] == 1.0
    assert rows[1]["actual_elapsed_hours"] == pytest.approx(54.45 / 60)
    assert rows[1]["elapsed_timing_offset_minutes"] == pytest.approx(-5.55)
    assert rows[1]["clock_time_offset_minutes"] == pytest.approx(7 + 43 / 60)
    assert rows[2]["elapsed_timing_offset_minutes"] == pytest.approx(-2.1833333333)
    for row in rows:
        metadata_time = datetime.fromisoformat(row["metadata_timestamp"])
        filename_time = datetime.fromisoformat(row["nominal_timestamp"])
        expected_clock_offset = (
            metadata_time - filename_time
        ).total_seconds() / 60.0
        assert row["clock_time_offset_minutes"] == pytest.approx(
            expected_clock_offset
        )
    assert rows[0]["clock_time_offset_minutes"] != 0.0


def test_non_metadata_fallback_fails_closed_for_actual_time():
    rows = build_timing_comparison_rows(
        [
            {
                "file": "sample(sequence-0900).dx",
                "timestamp": "2000-01-01T09:00:00",
                "timestamp_source": "filename sequence-0900 (HHMM)",
            }
        ]
    )
    assert not rows[0]["comparison_qc_pass"]
    assert rows[0]["metadata_timestamp"] == ""
    assert "not metadata" in rows[0]["comparison_qc_reason"]
    assert is_metadata_timestamp_source("LONG DATE header")
    assert not is_metadata_timestamp_source("file modification time")


def test_csv_export_columns_and_values_match_comparison_rows(tmp_path):
    rows = build_timing_comparison_rows(_measurements())
    path = tmp_path / "target_peak_timing_comparison.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TIMING_COMPARISON_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with path.open(newline="", encoding="utf-8") as handle:
        exported = list(csv.DictReader(handle))
    assert list(exported[0]) == TIMING_COMPARISON_COLUMNS
    assert float(exported[1]["elapsed_timing_offset_minutes"]) == pytest.approx(-5.55)
    assert float(exported[0]["clock_time_offset_minutes"]) == pytest.approx(
        13 + 16 / 60
    )
    assert exported[1]["metadata_time_source"] == "LONG DATE header"


def test_real_06_09_26_headers_use_long_date_and_filename_hhmm():
    raw_dir = REPO_ROOT / "results" / "raw" / "nmr" / "06-09-26"
    files = sorted(raw_dir.glob("*.dx"))
    assert len(files) == 8
    measurements = []
    for path in files:
        fid = read_jcamp_fid(path)
        timestamp, source = parse_acquisition_timestamp(fid.metadata, path)
        measurements.append(
            {
                "file": path.name,
                "timestamp": timestamp,
                "timestamp_source": source,
            }
        )
    rows = build_timing_comparison_rows(measurements)
    assert [row["filename_sequence"] for row in rows] == [
        "0900", "1000", "1015", "1030", "1045", "1100", "1115", "1130"
    ]
    assert {row["metadata_time_source"] for row in rows} == {"LONG DATE header"}
    assert all(row["comparison_qc_pass"] for row in rows)
    assert rows[-1]["nominal_elapsed_hours"] == 2.5
    assert rows[-1]["actual_elapsed_hours"] == pytest.approx(2.3938888889)
    assert rows[-1]["elapsed_timing_offset_minutes"] == pytest.approx(-6.3666666667)
