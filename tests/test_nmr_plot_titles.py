"""Dataset-aware title contracts for every focused NMR figure path."""

from __future__ import annotations

import numpy as np
import pytest

from chemyx_lab.analysis.completion import CompletionResult
from chemyx_lab.analysis.plot_titles import (
    format_dataset_plot_title,
    resolve_dataset_display_name,
)
from chemyx_lab.analysis.target_peak_config import (
    FigureConfig,
    StageConfig,
    TargetPeakConfig,
)
from chemyx_lab.analysis.target_peak_plots import (
    _absolute_timing_offset_figure,
    _area_figure,
    _change_figure,
    _completion_figure,
    _dashboard_figure,
    _heatmap_figure,
    _main_figure,
    _normalized_figure,
    _percent_figure,
    _rate_figure,
    _spectra_figure,
    _stage_figure,
    _timing_dumbbell_figure,
    _timing_clock_test_figure,
    _timing_clock_v2_figure,
    _timing_elapsed_absolute_offset_figure,
    _timing_elapsed_figure,
    _timing_elapsed_with_offsets_figure,
    _timing_offset_figure,
    render_target_peak_figures,
)
from chemyx_lab.analysis.target_peak_report import TargetPeakAnalysis
from chemyx_lab.analysis.timing_comparison import build_timing_comparison_rows


def _analysis(dataset_display_name: str = "06-09-26") -> TargetPeakAnalysis:
    times = np.asarray([0.0, 0.5, 1.1, 1.8])
    areas = np.asarray([10.0, 14.0, 16.0, 16.2])
    measurements = []
    rates = []
    decision_trace = []
    for index, (time, area) in enumerate(zip(times, areas)):
        measurements.append(
            {
                "elapsed_time_hours": time,
                "area": area,
                "area_ci_low": area - 0.4,
                "area_ci_high": area + 0.4,
                "peak_height": 40.0 + index,
                "snr": 20.0 + index,
                "peak_center_ppm": 5.79 + index * 0.001,
                "fwhm_hz": 1.2 + index * 0.05,
                "prominence_snr": 15.0 + index,
                "peak_quality_pass": True,
            }
        )
        rates.append(
            {
                "elapsed_time_hours": time,
                "delta_area": np.nan if index == 0 else areas[index] - areas[index - 1],
                "area_rate_per_hour": np.nan if index == 0 else 2.0,
                "rolling_slope_per_hour": np.nan if index < 2 else 0.5,
                "rolling_slope_ci_low": np.nan if index < 2 else 0.2,
                "rolling_slope_ci_high": np.nan if index < 2 else 0.8,
                "percent_change_per_interval": np.nan if index == 0 else 2.0,
            }
        )
        decision_trace.append(
            {
                "status": "insufficient_data" if index < 2 else "stable",
            }
        )
    normalized = [
        {
            "normalization_mode": mode,
            "elapsed_time_hours": time,
            "normalized_area": area / areas.max(),
        }
        for mode in ("fraction_of_max", "relative_to_first", "zero_to_one")
        for time, area in zip(times, areas)
    ]
    timing_comparison = build_timing_comparison_rows(
        [
            {
                "file": f"sample(sequence-{sequence}).dx",
                "timestamp": timestamp,
                "timestamp_source": "LONG DATE header",
            }
            for sequence, timestamp in (
                ("0900", "2026-06-09T09:13:16"),
                ("0930", "2026-06-09T09:42:30"),
                ("1000", "2026-06-09T10:10:10"),
                ("1030", "2026-06-09T10:41:20"),
            )
        ]
    )
    completion = CompletionResult(
        status="stable",
        trend_direction="growth",
        complete=False,
        completion_index=None,
        completion_elapsed_hours=None,
        completion_timestamp=None,
        reason="synthetic test",
        evidence_level="low",
        metrics={},
        thresholds={},
        quality_warnings=(),
    )
    config = TargetPeakConfig(
        enabled=True,
        dataset_display_name=dataset_display_name,
        peak_label="5.7 ppm",
        expected_center_ppm=5.79,
        search_window_ppm=(5.70, 5.90),
        integration_window_ppm=(5.72, 5.86),
        plot_window_ppm=(5.68, 5.92),
        figures=FigureConfig(formats=("png",), dpi=72),
        stages=(StageConfig("growth", 0.0, 1.8, "growth"),),
    )
    ppm = np.linspace(5.68, 5.92, 80)
    grid = np.asarray(
        [
            (1.0 + 0.1 * index)
            * np.exp(-0.5 * ((ppm - (5.79 + 0.001 * index)) / 0.012) ** 2)
            for index in range(len(times))
        ]
    )
    return TargetPeakAnalysis(
        config=config,
        measurements=measurements,
        rates=rates,
        normalized=normalized,
        decision_trace=decision_trace,
        completion=completion,
        spectra_long=[],
        spectrum_grid_ppm=ppm,
        spectrum_grid_intensity=grid,
        timing_comparison=timing_comparison,
    )


def test_formatter_normalizes_whitespace_and_avoids_duplicate_prefixes():
    assert (
        format_dataset_plot_title("  06-09-26  ", " Focused   Spectral Evolution ")
        == "06-09-26 Focused Spectral Evolution"
    )
    assert (
        format_dataset_plot_title("06-09-26", "06-09-26 Area vs Time")
        == "06-09-26 Area vs Time"
    )
    assert format_dataset_plot_title("06-09-26", "") == "06-09-26"


def test_missing_dataset_uses_documented_visible_fallback():
    assert format_dataset_plot_title("", "Area vs Time") == "Unspecified dataset Area vs Time"
    assert resolve_dataset_display_name() == "Unspecified dataset"


def test_dataset_resolution_prefers_config_then_metadata_then_paths(tmp_path):
    dated = tmp_path / "06-09-26" / "plots" / "figure.png"
    assert (
        resolve_dataset_display_name(
            "CONFIGURED",
            metadata={"dataset_display_name": "METADATA"},
            output_path=dated,
        )
        == "CONFIGURED"
    )
    assert (
        resolve_dataset_display_name(
            metadata={"dataset_display_name": "METADATA"},
            output_path=dated,
        )
        == "METADATA"
    )
    assert resolve_dataset_display_name(output_path=dated) == "06-09-26"
    assert (
        resolve_dataset_display_name(
            output_path=tmp_path / "TEST-RUN-001" / "plots" / "figure.png"
        )
        == "TEST-RUN-001"
    )


def test_all_focused_single_panel_titles_begin_with_configured_dataset():
    pytest.importorskip("matplotlib")
    from matplotlib import pyplot as plt

    analysis = _analysis()
    figures = [
        _area_figure(analysis, (6, 4)),
        _normalized_figure(analysis, (6, 4)),
        _rate_figure(analysis, (6, 4)),
        _percent_figure(analysis, (6, 4)),
        _spectra_figure(
            analysis,
            (6, 4),
            stacked=True,
            descriptive_title="Focused Spectral Evolution",
        ),
        _spectra_figure(
            analysis,
            (6, 4),
            stacked=False,
            descriptive_title="Focused Spectral Overlay",
        ),
        _spectra_figure(
            analysis,
            (6, 4),
            stacked=True,
            descriptive_title="Spectral Waterfall",
        ),
        _heatmap_figure(analysis, (6, 4)),
        _stage_figure(analysis, (6, 4)),
        _timing_elapsed_figure(analysis, (6, 4)),
        _timing_elapsed_with_offsets_figure(analysis, (6, 4)),
        _timing_offset_figure(analysis, (6, 4)),
        _timing_dumbbell_figure(analysis, (6, 4)),
        _absolute_timing_offset_figure(analysis, (6, 4)),
        _timing_elapsed_absolute_offset_figure(analysis, (6, 4)),
        _timing_clock_test_figure(analysis, (6, 4)),
        _timing_clock_v2_figure(analysis, (6, 4)),
    ]
    try:
        titles = [figure.axes[0].get_title() for figure in figures]
        assert all(title.startswith("06-09-26 ") for title in titles)
        assert "06-09-26 Focused Spectral Evolution" in titles
        assert "06-09-26 Area vs Time" in titles
        assert "06-09-26 Expected vs Actual Elapsed Time" in titles
        assert "06-09-26 Timing Offset by Acquisition" in titles
        assert "06-09-26 Filename Time vs Metadata Time" in titles
        assert "06-09-26 Absolute Timing Offset by Acquisition" in titles
        assert (
            "06-09-26 Expected vs Actual Elapsed Time with Absolute Offset"
            in titles
        )
        assert "06-09-26 Filename vs Metadata Timing Test" in titles
        assert "06-09-26 Filename vs Metadata Clock Time V2" in titles
    finally:
        for figure in figures:
            plt.close(figure)


def test_multi_panel_figures_have_dataset_aware_suptitles():
    pytest.importorskip("matplotlib")
    from matplotlib import pyplot as plt

    analysis = _analysis()
    figures = [
        _change_figure(analysis, (7, 5)),
        _dashboard_figure(analysis, (8, 8)),
        _completion_figure(analysis, (7, 5)),
        _main_figure(analysis, (7, 5)),
    ]
    try:
        assert [figure._suptitle.get_text() for figure in figures] == [
            "06-09-26 Area Change Between Acquisitions",
            "06-09-26 Target Peak Quality Control",
            "06-09-26 Completion Detection",
            "06-09-26 Target Peak Analysis",
        ]
    finally:
        for figure in figures:
            plt.close(figure)


def test_area_legend_is_concise_and_identifies_peak_marker():
    pytest.importorskip("matplotlib")
    from matplotlib import pyplot as plt

    figure = _area_figure(_analysis(), (6, 4))
    try:
        labels = figure.axes[0].get_legend_handles_labels()[1]
        assert labels == ["5.7 ppm peak"]
        assert "Approx. 95% uncertainty interval" not in labels
    finally:
        plt.close(figure)


def test_timing_plot_values_match_shared_comparison_rows():
    pytest.importorskip("matplotlib")
    from matplotlib import pyplot as plt

    analysis = _analysis()
    elapsed_figure = _timing_elapsed_figure(analysis, (6, 4))
    offset_figure = _timing_offset_figure(analysis, (6, 4))
    absolute_offset_figure = _absolute_timing_offset_figure(analysis, (6, 4))
    absolute_elapsed_figure = _timing_elapsed_absolute_offset_figure(
        analysis, (6, 4)
    )
    try:
        nominal = [
            60.0 * row["nominal_elapsed_hours"]
            for row in analysis.timing_comparison
        ]
        actual = [
            60.0 * row["actual_elapsed_hours"]
            for row in analysis.timing_comparison
        ]
        offset = [
            row["elapsed_timing_offset_minutes"]
            for row in analysis.timing_comparison
        ]
        absolute_offset = [
            row["clock_time_offset_minutes"]
            for row in analysis.timing_comparison
        ]
        assert elapsed_figure.axes[0].lines[0].get_ydata() == pytest.approx(nominal)
        assert elapsed_figure.axes[0].lines[1].get_ydata() == pytest.approx(actual)
        assert offset_figure.axes[0].lines[-1].get_ydata() == pytest.approx(offset)
        plotted_absolute_offset = absolute_offset_figure.axes[0].collections[1]
        np.testing.assert_allclose(
            np.asarray(plotted_absolute_offset.get_offsets()[:, 1], dtype=float),
            absolute_offset,
        )
        assert plotted_absolute_offset.get_offsets()[0, 1] != 0.0
        assert absolute_elapsed_figure.axes[0].lines[0].get_ydata() == pytest.approx(
            nominal
        )
        assert absolute_elapsed_figure.axes[0].lines[1].get_ydata() == pytest.approx(
            actual
        )
        assert len(absolute_elapsed_figure.axes[0].collections) >= 1
        absolute_labels = [
            annotation.get_text() for annotation in absolute_elapsed_figure.axes[0].texts
        ]
        assert absolute_labels[0] == "+13.3 min"
        assert absolute_labels == [f"{value:+.1f} min" for value in absolute_offset]
    finally:
        plt.close(elapsed_figure)
        plt.close(offset_figure)
        plt.close(absolute_offset_figure)
        plt.close(absolute_elapsed_figure)


def test_slide_paper_and_manifest_share_dataset_identity(tmp_path):
    pytest.importorskip("matplotlib")
    analysis = _analysis()
    _, manifest = render_target_peak_figures(analysis, tmp_path)
    assert {row["dataset"] for row in manifest} == {"06-09-26"}
    assert all(row["visible_title"].startswith("06-09-26 ") for row in manifest)
    assert {row["dataset"] for row in manifest if row["group"] == "slides"} == {
        "06-09-26"
    }
    assert {row["dataset"] for row in manifest if row["group"] == "paper"} == {
        "06-09-26"
    }
    archived_slide_stems = {
        "06_expected_vs_actual_time",
        "09_elapsed_time_with_offsets",
        "11_expected_vs_actual_elapsed_time_absolute_offset",
        "12_filename_vs_metadata_clock_time_v1",
    }
    archived_rows = [row for row in manifest if row["group"] == "slides/archive"]
    assert {
        next(stem for stem in archived_slide_stems if stem in row["figure"])
        for row in archived_rows
    } == archived_slide_stems
    active_slide_figures = [
        row["figure"] for row in manifest if row["group"] == "slides"
    ]
    assert not any(
        stem in figure
        for stem in archived_slide_stems
        for figure in active_slide_figures
    )
    assert any(
        "13_filename_vs_metadata_clock_time.png" in figure
        for figure in active_slide_figures
    )
    assert not any(
        "13_filename_vs_metadata_clock_time_v2" in row["figure"]
        for row in manifest
    )
    timing_rows = [
        row for row in manifest if "target_peak_timing_comparison.csv" in row["data_files"]
    ]
    assert len(timing_rows) == 17
    assert {row["visible_title"] for row in timing_rows} == {
        "06-09-26 Expected vs Actual Elapsed Time",
        "06-09-26 Timing Offset by Acquisition",
        "06-09-26 Filename Time vs Metadata Time",
        "06-09-26 Absolute Timing Offset by Acquisition",
        "06-09-26 Expected vs Actual Elapsed Time with Absolute Offset",
        "06-09-26 Filename vs Metadata Timing Test",
        "06-09-26 Filename vs Metadata Clock Time V2",
    }


def test_timing_clock_test_uses_each_series_own_timestamps():
    pytest.importorskip("matplotlib")
    from datetime import datetime
    import matplotlib.dates as mdates
    from matplotlib import pyplot as plt

    analysis = _analysis()
    figure = _timing_clock_test_figure(analysis, (6, 4))
    try:
        rows = analysis.timing_comparison
        expected_nominal_x = mdates.date2num(
            [datetime.fromisoformat(row["nominal_timestamp"]) for row in rows]
        )
        expected_metadata_x = mdates.date2num(
            [datetime.fromisoformat(row["metadata_timestamp"]) for row in rows]
        )
        expected_nominal_y = [
            60.0 * row["nominal_elapsed_hours"] for row in rows
        ]
        expected_metadata_y = [
            60.0 * row["actual_elapsed_hours"] for row in rows
        ]
        nominal_line, metadata_line = figure.axes[0].lines
        np.testing.assert_allclose(
            nominal_line.get_xdata(orig=False), expected_nominal_x
        )
        np.testing.assert_allclose(
            metadata_line.get_xdata(orig=False), expected_metadata_x
        )
        np.testing.assert_allclose(nominal_line.get_ydata(), expected_nominal_y)
        np.testing.assert_allclose(metadata_line.get_ydata(), expected_metadata_y)
        assert not np.array_equal(expected_nominal_x, expected_metadata_x)
    finally:
        plt.close(figure)


def test_timing_clock_v2_preserves_v1_data_and_adds_offsets():
    pytest.importorskip("matplotlib")
    from matplotlib import pyplot as plt

    analysis = _analysis()
    v1 = _timing_clock_test_figure(analysis, (6, 4))
    v2 = _timing_clock_v2_figure(analysis, (6, 4))
    try:
        for v1_line, v2_line in zip(v1.axes[0].lines, v2.axes[0].lines, strict=True):
            np.testing.assert_allclose(
                v1_line.get_xdata(orig=False), v2_line.get_xdata(orig=False)
            )
            np.testing.assert_allclose(v1_line.get_ydata(), v2_line.get_ydata())
        assert len(v1.axes[0].patches) == 0
        assert len(v2.axes[0].patches) == 1
        expected_labels = [
            f"{row['clock_time_offset_minutes']:+.1f} min"
            for row in analysis.timing_comparison
        ]
        assert [text.get_text() for text in v2.axes[0].texts] == expected_labels
        assert expected_labels[0] == "+13.3 min"
    finally:
        plt.close(v1)
        plt.close(v2)


def test_synthetic_dataset_changes_titles_without_code_changes():
    pytest.importorskip("matplotlib")
    from matplotlib import pyplot as plt

    analysis = _analysis("TEST-RUN-001")
    figure = _spectra_figure(
        analysis,
        (6, 4),
        stacked=True,
        descriptive_title="Focused Spectral Evolution",
    )
    try:
        assert figure.axes[0].get_title() == "TEST-RUN-001 Focused Spectral Evolution"
    finally:
        plt.close(figure)
