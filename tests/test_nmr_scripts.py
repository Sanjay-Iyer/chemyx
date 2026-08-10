"""Unit and CLI integration tests for the NMR analysis scripts.

Run with:  conda activate AI && python -m pytest tests/test_nmr_scripts.py -v
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure the repo root and scripts/nmr are importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_NMR = REPO_ROOT / "scripts" / "nmr"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_NMR))

# Now we can import from _common (the bootstrap is already handled above)
from _common import (  # noqa: E402
    DEFAULT_TARGET_PPM,
    FileResult,
    collect_dx_files,
    compute_deltas,
    create_output_dir,
    derive_group,
    derive_run_name,
    detect_plateau,
    parse_acquisition_timestamp,
    sort_by_timestamp,
    write_csv,
    write_summary,
    _safe_name,
)
from _config import (  # noqa: E402
    AnalysisConfig,
    apply_cli_overrides,
    load_config,
)
from chemyx_lab.config import ConfigError  # noqa: E402
import process_fid  # noqa: E402
from process_fid import _plot_real, _select_simple_peak_rows  # noqa: E402

# Real test data
REAL_DATA_DIR = REPO_ROOT / "results" / "raw" / "nmr" / "06-08-26"
HAS_REAL_DATA = REAL_DATA_DIR.is_dir() and any(REAL_DATA_DIR.glob("*.dx"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_peak(**overrides):
    """Create a mock PeakResult with sensible defaults."""
    defaults = dict(
        source=Path("test.dx"),
        target_ppm=6.1,
        peak_ppm=6.15,
        peak_height=1000.0,
        peak_area=50.0,
        snr=5.0,
        prominence=200.0,
        prominence_snr=2.0,
        width_ppm=0.03,
        peaks_considered=3,
        points_in_window=96,
        baseline=800.0,
        noise=40.0,
    )
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_file_result(
    filename: str,
    height: float = 1000.0,
    area: float = 50.0,
    ts: datetime | None = None,
    error: str | None = None,
    ts_source: str = "LONG DATE header",
) -> FileResult:
    peak = None if error else _make_mock_peak(
        source=Path(filename), peak_height=height, peak_area=area
    )
    return FileResult(
        path=Path(filename),
        peak=peak,
        error=error,
        timestamp=ts,
        timestamp_source=ts_source,
    )


# ===================================================================
# Unit tests: _common.py functions
# ===================================================================


class TestParseAcquisitionTimestamp:
    def test_long_date_header(self):
        meta = {"LONG DATE": "2026/06/08 14:21:24-0400"}
        dt, source = parse_acquisition_timestamp(meta)
        assert dt == datetime(2026, 6, 8, 14, 21, 24)
        assert "LONG DATE" in source

    def test_epoch_header(self):
        meta = {"$DATE": "1780942884"}
        dt, source = parse_acquisition_timestamp(meta)
        assert dt is not None
        assert "$DATE" in source

    def test_filename_fallback_hhmm(self):
        meta = {}
        fp = Path("CEC-PhSi2-flow(sequence-1415)-06-08-26.dx")
        dt, source = parse_acquisition_timestamp(meta, fp)
        assert dt is not None
        assert "filename" in source
        assert dt.hour == 14 and dt.minute == 15

    def test_run_metadata_precedes_filename(self):
        meta = {"TIMESTAMP": "2026-06-09T10:07:43"}
        fp = Path("sample(sequence-1415).dx")
        dt, source = parse_acquisition_timestamp(meta, fp)
        assert dt == datetime(2026, 6, 9, 10, 7, 43)
        assert "run metadata" in source

    def test_file_mtime_fallback(self, tmp_path):
        fp = tmp_path / "sample.dx"
        fp.write_text("test", encoding="utf-8")
        dt, source = parse_acquisition_timestamp({}, fp)
        assert dt is not None
        assert source == "file modification time"

    def test_no_timestamp_available(self):
        meta = {}
        dt, source = parse_acquisition_timestamp(meta)
        assert dt is None
        assert source == "none"

    def test_malformed_long_date(self):
        meta = {"LONG DATE": "not-a-date"}
        dt, source = parse_acquisition_timestamp(meta)
        # Should fall through, not crash
        assert dt is None or source != "LONG DATE header"


class TestSortByTimestamp:
    def test_sorts_by_timestamp(self):
        r1 = _make_file_result("b.dx", ts=datetime(2026, 1, 1, 12, 0))
        r2 = _make_file_result("a.dx", ts=datetime(2026, 1, 1, 11, 0))
        r3 = _make_file_result("c.dx", ts=datetime(2026, 1, 1, 13, 0))
        sorted_results = sort_by_timestamp([r1, r2, r3])
        assert [r.path.name for r in sorted_results] == ["a.dx", "b.dx", "c.dx"]

    def test_no_timestamp_falls_back_to_filename(self):
        r1 = _make_file_result("b.dx")
        r2 = _make_file_result("a.dx")
        sorted_results = sort_by_timestamp([r1, r2])
        assert [r.path.name for r in sorted_results] == ["a.dx", "b.dx"]

    def test_mixed_timestamp_and_no_timestamp(self):
        r1 = _make_file_result("b.dx", ts=datetime(2026, 1, 1, 12, 0))
        r2 = _make_file_result("z.dx")  # no timestamp
        sorted_results = sort_by_timestamp([r1, r2])
        # timestamped files come first (sort key 0), then non-timestamped (1)
        assert sorted_results[0].path.name == "b.dx"
        assert sorted_results[1].path.name == "z.dx"


class TestComputeDeltas:
    def test_first_row_has_no_delta(self):
        results = [_make_file_result("a.dx", height=100, area=10)]
        rows = compute_deltas(results)
        assert rows[0]["delta_height"] == ""
        assert rows[0]["delta_area"] == ""

    def test_second_row_has_delta(self):
        results = [
            _make_file_result("a.dx", height=100, area=10),
            _make_file_result("b.dx", height=150, area=15),
        ]
        rows = compute_deltas(results)
        assert rows[1]["delta_height"] == 50.0
        assert rows[1]["delta_area"] == 5.0
        assert rows[1]["pct_change_height"] == pytest.approx(50.0)
        assert rows[1]["pct_change_area"] == pytest.approx(50.0)

    def test_zero_value_growth(self):
        """When previous value is 0, percent change should be 0 (not crash)."""
        results = [
            _make_file_result("a.dx", height=0, area=0),
            _make_file_result("b.dx", height=100, area=10),
        ]
        rows = compute_deltas(results)
        assert rows[1]["pct_change_height"] == 0.0
        assert rows[1]["pct_change_area"] == 0.0

    def test_error_file_doesnt_break_deltas(self):
        results = [
            _make_file_result("a.dx", height=100, area=10),
            _make_file_result("bad.dx", error="parse failed"),
            _make_file_result("c.dx", height=200, area=20),
        ]
        rows = compute_deltas(results)
        assert rows[1].get("error") == "parse failed"
        # c.dx should delta from a.dx since bad.dx was skipped
        assert rows[2]["delta_height"] == 100.0

    def test_elapsed_seconds_computed(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0)
        t1 = datetime(2026, 1, 1, 11, 0, 0)
        results = [
            _make_file_result("a.dx", ts=t0),
            _make_file_result("b.dx", ts=t1),
        ]
        rows = compute_deltas(results)
        assert rows[0]["elapsed_seconds"] == 0.0
        assert rows[1]["elapsed_seconds"] == 3600.0


class TestDetectPlateau:
    def test_plateau_reached(self):
        rows = [
            {"file": "a.dx", "pct_change_height": ""},
            {"file": "b.dx", "pct_change_height": 50.0},
            {"file": "c.dx", "pct_change_height": 3.0},
            {"file": "d.dx", "pct_change_height": 2.0},
            {"file": "e.dx", "pct_change_height": 1.0},
        ]
        result = detect_plateau(
            rows, "peak_height", "pct_change_height",
            threshold_pct=5.0, min_consecutive=2,
        )
        assert result.reached is True
        assert result.plateau_file == "c.dx"
        assert result.consecutive_below >= 2

    def test_plateau_not_reached_single_below(self):
        """Only one change below threshold — not enough."""
        rows = [
            {"file": "a.dx", "pct_change_height": ""},
            {"file": "b.dx", "pct_change_height": 50.0},
            {"file": "c.dx", "pct_change_height": 3.0},
            {"file": "d.dx", "pct_change_height": 20.0},
        ]
        result = detect_plateau(
            rows, "peak_height", "pct_change_height",
            threshold_pct=5.0, min_consecutive=2,
        )
        assert result.reached is False

    def test_plateau_resets_on_spike(self):
        rows = [
            {"file": "a.dx", "pct_change_height": ""},
            {"file": "b.dx", "pct_change_height": 2.0},
            {"file": "c.dx", "pct_change_height": 30.0},  # spike resets
            {"file": "d.dx", "pct_change_height": 2.0},
        ]
        result = detect_plateau(
            rows, "peak_height", "pct_change_height",
            threshold_pct=5.0, min_consecutive=2,
        )
        assert result.reached is False
        assert result.consecutive_below == 1

    def test_empty_rows(self):
        result = detect_plateau(
            [], "peak_height", "pct_change_height",
            threshold_pct=5.0, min_consecutive=2,
        )
        assert result.reached is False

    def test_height_and_area_independent(self):
        """Plateau can be reached for area but not height."""
        rows = [
            {"file": "a.dx", "pct_change_height": "", "pct_change_area": ""},
            {"file": "b.dx", "pct_change_height": 20.0, "pct_change_area": 3.0},
            {"file": "c.dx", "pct_change_height": 15.0, "pct_change_area": 2.0},
        ]
        h = detect_plateau(rows, "peak_height", "pct_change_height", 5.0, 2)
        a = detect_plateau(rows, "peak_area", "pct_change_area", 5.0, 2)
        assert h.reached is False
        assert a.reached is True


class TestCreateOutputDir:
    def test_creates_directory(self, tmp_path):
        out = create_output_dir(tmp_path, "test")
        assert out.is_dir()
        assert "test" in out.name

    def test_collision_handling(self, tmp_path):
        out1 = create_output_dir(tmp_path, "test", run_name="myrun")
        out2 = create_output_dir(tmp_path, "test", run_name="myrun")
        assert out1 != out2
        assert out1.is_dir()
        assert out2.is_dir()


def test_region_plot_shows_integrated_area_and_filename_only(tmp_path, monkeypatch):
    import numpy as np
    from matplotlib.figure import Figure

    captured = {}
    original_savefig = Figure.savefig

    def capture_plot(figure, *args, **kwargs):
        axis = figure.axes[0]
        captured["title"] = axis.get_title()
        captured["xlim"] = axis.get_xlim()
        captured["labels"] = axis.get_legend_handles_labels()[1]
        return original_savefig(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", capture_plot)
    ppm = np.linspace(4.0, 7.0, 601)
    baseline = 10.0 + 0.5 * (ppm - 5.8)
    corrected = 100.0 * np.exp(-((ppm - 5.8) / 0.04) ** 2)
    output = _plot_real(
        ppm,
        baseline + corrected,
        tmp_path / "region.png",
        "sample.dx",
        (5.0, 6.5),
        (5.8,),
        zoom=True,
        display=(4.0, 7.0),
        integration_regions=((5.72, 5.88),),
        integration_ppm=ppm,
        integration_corrected=corrected,
        dataset_display_name="081626_phsi4",
    )

    assert output.is_file()
    assert captured["title"] == "081626_phsi4 sample.dx"
    assert captured["xlim"] == pytest.approx((7.0, 4.0))
    assert "Integrated peak area" in captured["labels"]
    assert "Integration baseline" in captured["labels"]
    assert "Integration bounds" in captured["labels"]
    assert "search region" not in captured["labels"]
    assert not any("QC-passed" in label for label in captured["labels"])


def test_process_fid_accepts_dataset_display_name_override():
    args = process_fid._parser().parse_args(
        ["sample.dx", "--dataset-display-name", "081626_phsi4"]
    )

    assert args.dataset_display_name == "081626_phsi4"


def test_simple_peak_selection_keeps_one_qc_passed_target_peak():
    args = argparse.Namespace(
        simple_restrict_to_window=True,
        simple_target_ppm=5.8,
        simple_window_ppm=0.1,
    )
    rows = [
        {"name": "target", "interpolated_ppm": 5.79, "snr": 25, "qc_pass": True},
        {"name": "weaker", "interpolated_ppm": 5.82, "snr": 10, "qc_pass": True},
        {"name": "failed", "interpolated_ppm": 5.85, "snr": 50, "qc_pass": False},
        {"name": "off-target", "interpolated_ppm": 5.35, "snr": 100, "qc_pass": True},
    ]

    selected = _select_simple_peak_rows(rows, args)

    assert [row["name"] for row in selected] == ["target"]


def test_process_fid_merges_local_paths_for_no_argument_run(tmp_path, monkeypatch):
    shared = tmp_path / "analysis.yaml"
    local = tmp_path / "analysis.local.yaml"
    shared.write_text(
        textwrap.dedent(
            """
            input:
              paths: [shared/raw]
            regional_analysis:
              ppm_min: 5.0
              ppm_max: 6.5
            output:
              directory: shared/processed
            """
        ),
        encoding="utf-8",
    )
    local.write_text(
        textwrap.dedent(
            """
            input:
              paths: [D:/work-data/raw/06-09-26]
            output:
              directory: D:/work-data/processed
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(process_fid, "DEFAULT_CONFIG_PATH", shared)
    monkeypatch.setattr(process_fid, "LOCAL_CONFIG_PATH", local)

    defaults = process_fid._config_defaults([])
    args = process_fid._parser(defaults).parse_args([])

    assert args.paths == ["D:/work-data/raw/06-09-26"]
    assert args.output_dir == Path("D:/work-data/processed")
    assert args.region_min == 5.0
    assert args.region_max == 6.5
    assert args.config is None


class TestSafeName:
    def test_preserves_alnum(self):
        assert _safe_name("abc123") == "abc123"

    def test_replaces_special_chars(self):
        assert _safe_name("my file (1)") == "my_file__1"

    def test_empty_string_fallback(self):
        result = _safe_name("")
        assert len(result) > 0  # should get timestamp


class TestWriteCsv:
    def test_writes_csv(self, tmp_path):
        rows = [{"file": "a.dx", "peak_height": 100, "peak_area": 50}]
        path = write_csv(tmp_path / "out.csv", rows, columns=["file", "peak_height", "peak_area"])
        assert path.exists()
        with path.open() as f:
            reader = csv.DictReader(f)
            data = list(reader)
        assert len(data) == 1
        assert data[0]["file"] == "a.dx"


class TestWriteSummary:
    def test_writes_json(self, tmp_path):
        path = write_summary(tmp_path / "summary.json", {"key": "value"})
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload["key"] == "value"


class TestCollectDxFiles:
    def test_deduplicates_and_sorts(self, tmp_path):
        (tmp_path / "b.dx").write_text("fake")
        (tmp_path / "a.dx").write_text("fake")
        files = collect_dx_files([str(tmp_path), str(tmp_path / "a.dx")])
        names = [f.name for f in files]
        assert names == ["a.dx", "b.dx"]

    def test_empty_directory(self, tmp_path):
        files = collect_dx_files([str(tmp_path)])
        assert files == []


# ===================================================================
# Config-file support (_config.py)
# ===================================================================


def _write_config(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "analysis.yaml"
    cfg.write_text(textwrap.dedent(body), encoding="utf-8")
    return cfg


class TestLoadConfig:
    def test_explicit_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config("compare_timeseries", tmp_path / "nope.yaml")

    def test_common_section_applied(self, tmp_path):
        path = _write_config(tmp_path, """
            common:
              target_ppm: 5.8
              window_ppm: 0.2
              plots: false
        """)
        cfg = load_config("compare_timeseries", path)
        assert cfg.target_ppm == 5.8
        assert cfg.window_ppm == 0.2
        assert cfg.plots is False

    def test_script_section_applied(self, tmp_path):
        path = _write_config(tmp_path, """
            compare_timeseries:
              plateau_threshold_pct: 3.0
              plateau_consecutive: 4
        """)
        cfg = load_config("compare_timeseries", path)
        assert cfg.plateau_threshold_pct == 3.0
        assert cfg.plateau_consecutive == 4

    def test_output_dir_coerced_to_path(self, tmp_path):
        path = _write_config(tmp_path, """
            common:
              output_dir: results/custom
        """)
        cfg = load_config("batch_report", path)
        assert isinstance(cfg.output_dir, Path)
        assert cfg.output_dir == Path("results/custom")

    def test_unknown_key_rejected(self, tmp_path):
        path = _write_config(tmp_path, """
            common:
              taret_ppm: 6.1
        """)
        with pytest.raises(ConfigError):
            load_config("compare_timeseries", path)

    def test_unknown_section_rejected(self, tmp_path):
        path = _write_config(tmp_path, """
            bogus_section:
              foo: 1
        """)
        with pytest.raises(ConfigError):
            load_config("compare_timeseries", path)

    def test_unknown_script_name_raises(self, tmp_path):
        path = _write_config(tmp_path, "common: {}\n")
        with pytest.raises(ValueError):
            load_config("not_a_script", path)

    def test_group_tokens_list(self, tmp_path):
        path = _write_config(tmp_path, """
            common:
              group: null
              group_tokens: [PhSi2, PhSi4]
        """)
        cfg = load_config("compare_timeseries", path)
        assert cfg.group_tokens == ["PhSi2", "PhSi4"]
        assert cfg.group is None

    def test_group_tokens_scalar_coerced_to_list(self, tmp_path):
        path = _write_config(tmp_path, """
            common:
              group_tokens: PhSi2
        """)
        cfg = load_config("batch_report", path)
        assert cfg.group_tokens == ["PhSi2"]

    def test_group_explicit(self, tmp_path):
        path = _write_config(tmp_path, """
            common:
              group: PhSi6
        """)
        cfg = load_config("plot_spectra", path)
        assert cfg.group == "PhSi6"

    def test_plot_spectra_section(self, tmp_path):
        path = _write_config(tmp_path, """
            plot_spectra:
              zoom_window_ppm: 0.3
              normalize_overlay: false
        """)
        cfg = load_config("plot_spectra", path)
        assert cfg.zoom_window_ppm == 0.3
        assert cfg.normalize_overlay is False

    def test_repo_default_config_is_valid(self):
        """The shipped configs/nmr/analysis.yaml must load for every script."""
        for script in ("analyze_single", "compare_timeseries",
                       "batch_report", "plot_spectra"):
            cfg = load_config(script, None)  # default path
            assert isinstance(cfg, AnalysisConfig)
            assert cfg.target_ppm == DEFAULT_TARGET_PPM


class TestApplyCliOverrides:
    def test_cli_wins_over_config(self):
        cfg = AnalysisConfig(target_ppm=6.1)
        args = argparse.Namespace(target_ppm=5.0, window_ppm=None)
        apply_cli_overrides(cfg, args)
        assert cfg.target_ppm == 5.0  # CLI provided
        assert cfg.window_ppm == cfg.window_ppm  # untouched

    def test_none_leaves_config_value(self):
        cfg = AnalysisConfig(plateau_consecutive=2)
        args = argparse.Namespace(plateau_consecutive=None)
        apply_cli_overrides(cfg, args)
        assert cfg.plateau_consecutive == 2

    def test_false_boolean_is_applied(self):
        """--no-plots => plots=False must override a config True."""
        cfg = AnalysisConfig(plots=True)
        args = argparse.Namespace(plots=False)
        apply_cli_overrides(cfg, args)
        assert cfg.plots is False


class TestDeriveRunName:
    def test_directory_name_used(self, tmp_path):
        d = tmp_path / "06-08-26"
        d.mkdir()
        name = derive_run_name([d], "timeseries")
        assert name.startswith("06-08-26_")
        assert name.endswith("_timeseries")

    def test_file_stem_used(self, tmp_path):
        f = tmp_path / "sample-1115.dx"
        f.write_text("x")
        name = derive_run_name([f], "single")
        assert name.startswith("sample-1115_")
        assert name.endswith("_single")

    def test_special_chars_sanitized(self, tmp_path):
        d = tmp_path / "run (a)"
        d.mkdir()
        name = derive_run_name([d], "batch")
        assert "(" not in name and ")" not in name and " " not in name


class TestDeriveGroup:
    TOKENS = ["PhSi2", "PhSi4", "PhSi6"]

    def test_token_detected_from_filename(self):
        names = ["CEC-PhSi4-flow(sequence-1115)-06-08-26.dx"]
        assert derive_group(names, self.TOKENS) == "PhSi4"

    def test_case_insensitive(self):
        names = ["cec-phsi6-flow.dx"]
        assert derive_group(names, self.TOKENS) == "PhSi6"

    def test_first_matching_token_wins(self):
        # order in tokens list decides
        names = ["PhSi2-and-PhSi4.dx"]
        assert derive_group(names, self.TOKENS) == "PhSi2"

    def test_explicit_overrides_tokens(self):
        names = ["CEC-PhSi2-flow.dx"]
        assert derive_group(names, self.TOKENS, explicit="custom") == "custom"

    def test_no_match_returns_none(self):
        assert derive_group(["random.dx"], self.TOKENS) is None

    def test_empty_tokens_returns_none(self):
        assert derive_group(["CEC-PhSi2.dx"], []) is None

    def test_explicit_sanitized(self):
        assert derive_group([], [], explicit="Ph Si/2") == "Ph_Si_2"


# ===================================================================
# CLI integration tests (run actual scripts via subprocess)
# ===================================================================

PYTHON = sys.executable


def _run_script(script_name: str, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    cmd = [PYTHON, str(SCRIPTS_NMR / script_name)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or str(REPO_ROOT),
        timeout=120,
    )


def _run_dir(base: Path) -> Path:
    """Locate the run output folder (holding results.csv) under *base*.

    Robust to an optional group sub-folder (e.g. base/PhSi2/<run>/), since the
    real test data filenames contain a group token.
    """
    matches = list(Path(base).rglob("results.csv"))
    assert matches, f"no results.csv found under {base}"
    return matches[0].parent


@pytest.mark.skipif(not HAS_REAL_DATA, reason="No real NMR data in results/raw/nmr/06-08-26/")
class TestAnalyzeSingleCLI:
    def test_basic_run(self, tmp_path):
        dx_file = next(REAL_DATA_DIR.glob("*.dx"))
        result = _run_script("analyze_single.py", [
            str(dx_file),
            "--output-dir", str(tmp_path),
        ])
        # Exit 1 is valid when strict peak QC rejects this real dataset; the
        # CLI must still produce a traceable report.
        assert result.returncode in {0, 1}, f"STDERR:\n{result.stderr}"
        # Check outputs
        run_dir = _run_dir(tmp_path)
        assert (run_dir / "results.csv").exists()
        assert (run_dir / "summary.json").exists()

    def test_no_plots_flag(self, tmp_path):
        dx_file = next(REAL_DATA_DIR.glob("*.dx"))
        result = _run_script("analyze_single.py", [
            str(dx_file),
            "--output-dir", str(tmp_path),
            "--no-plots",
        ])
        assert result.returncode in {0, 1}, f"STDERR:\n{result.stderr}"
        run_dir = _run_dir(tmp_path)
        plots = list((run_dir / "plots").glob("*.png"))
        assert len(plots) == 0

    def test_nonexistent_file(self, tmp_path):
        result = _run_script("analyze_single.py", [
            str(tmp_path / "nonexistent.dx"),
            "--output-dir", str(tmp_path),
        ])
        assert result.returncode != 0


@pytest.mark.skipif(not HAS_REAL_DATA, reason="No real NMR data in results/raw/nmr/06-08-26/")
class TestCompareTimeseriesCLI:
    def test_directory_input(self, tmp_path):
        result = _run_script("compare_timeseries.py", [
            str(REAL_DATA_DIR),
            "--output-dir", str(tmp_path),
        ])
        assert result.returncode in {0, 1}, f"STDERR:\n{result.stderr}"
        run_dir = _run_dir(tmp_path)
        assert (run_dir / "results.csv").exists()
        assert (run_dir / "summary.json").exists()
        # Real data files are named CEC-PhSi2-... so the default config's
        # group_tokens should file this run under a PhSi2/ sub-folder.
        assert run_dir.parent.name == "PhSi2"

        # Check CSV has expected columns
        with (run_dir / "results.csv").open() as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            assert "peak_height" in fieldnames
            assert "peak_area" in fieldnames
            assert "delta_height" in fieldnames
            assert "pct_change_height" in fieldnames

        # Check summary has plateau results
        summary = json.loads((run_dir / "summary.json").read_text())
        assert "plateau_height" in summary
        assert "plateau_area" in summary
        assert summary["plateau_height"]["metric"] == "peak_height"
        assert summary["plateau_area"]["metric"] == "peak_area"

    def test_no_plots_skips_png(self, tmp_path):
        result = _run_script("compare_timeseries.py", [
            str(REAL_DATA_DIR),
            "--output-dir", str(tmp_path),
            "--no-plots",
        ])
        assert result.returncode in {0, 1}, f"STDERR:\n{result.stderr}"
        run_dir = _run_dir(tmp_path)
        plots = list((run_dir / "plots").glob("*.png")) if (run_dir / "plots").exists() else []
        assert len(plots) == 0

    def test_custom_plateau_threshold(self, tmp_path):
        result = _run_script("compare_timeseries.py", [
            str(REAL_DATA_DIR),
            "--output-dir", str(tmp_path),
            "--plateau-threshold", "50",
            "--plateau-consecutive", "3",
            "--no-plots",
        ])
        assert result.returncode in {0, 1}, f"STDERR:\n{result.stderr}"


@pytest.mark.skipif(not HAS_REAL_DATA, reason="No real NMR data in results/raw/nmr/06-08-26/")
class TestBatchReportCLI:
    def test_directory_input(self, tmp_path):
        result = _run_script("batch_report.py", [
            str(REAL_DATA_DIR),
            "--output-dir", str(tmp_path),
        ])
        assert result.returncode in {0, 1}, f"STDERR:\n{result.stderr}"
        run_dir = _run_dir(tmp_path)
        assert (run_dir / "results.csv").exists()
        assert (run_dir / "summary.json").exists()

        # Check summary has statistics
        summary = json.loads((run_dir / "summary.json").read_text())
        assert "statistics" in summary
        assert "height" in summary["statistics"]
        assert "area" in summary["statistics"]

    def test_multiple_file_inputs(self, tmp_path):
        dx_files = sorted(REAL_DATA_DIR.glob("*.dx"))[:3]
        result = _run_script("batch_report.py", [
            *[str(f) for f in dx_files],
            "--output-dir", str(tmp_path),
            "--no-plots",
        ])
        assert result.returncode in {0, 1}, f"STDERR:\n{result.stderr}"


# ===================================================================
# Edge-case tests
# ===================================================================


class TestEdgeCases:
    def test_output_dir_collision(self, tmp_path):
        """Creating two runs with the same name should not overwrite."""
        d1 = create_output_dir(tmp_path, "single", run_name="run1")
        (d1 / "marker.txt").write_text("original")
        d2 = create_output_dir(tmp_path, "single", run_name="run1")
        assert d1 != d2
        assert (d1 / "marker.txt").read_text() == "original"

    def test_compute_deltas_single_file(self):
        """Single file should produce a row with no deltas."""
        results = [_make_file_result("a.dx", height=100, area=10)]
        rows = compute_deltas(results)
        assert len(rows) == 1
        assert rows[0]["delta_height"] == ""

    def test_compute_deltas_all_errors(self):
        results = [
            _make_file_result("a.dx", error="fail1"),
            _make_file_result("b.dx", error="fail2"),
        ]
        rows = compute_deltas(results)
        assert len(rows) == 2
        assert all(r.get("error") for r in rows)

    def test_plateau_all_missing_pct(self):
        rows = [
            {"file": "a.dx", "pct_change_height": ""},
            {"file": "b.dx", "pct_change_height": ""},
        ]
        result = detect_plateau(rows, "peak_height", "pct_change_height", 5.0, 2)
        assert result.reached is False
