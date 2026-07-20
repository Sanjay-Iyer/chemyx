from pathlib import Path

from chemyx_lab.analysis.nmr import PeakResult
from chemyx_lab.analysis.outputs import (
    create_analysis_run,
    peak_result_to_row,
    write_results_csv,
)


def test_create_analysis_run_builds_clean_paths(tmp_path):
    run = create_analysis_run(tmp_path, run_name="test run")

    assert run.run_dir == tmp_path / "test_run"
    assert run.results_csv == run.run_dir / "results.csv"
    assert run.plots_dir == run.run_dir / "plots"
    assert run.manifest_json == run.run_dir / "manifest.json"
    assert run.plots_dir.is_dir()


def test_peak_result_writes_csv_row(tmp_path):
    result = PeakResult(
        source=Path("sample.dx"),
        target_ppm=6.1,
        peak_ppm=6.15,
        peak_height=100.0,
        baseline=10.0,
        noise=5.0,
        snr=18.0,
        prominence=50.0,
        prominence_snr=10.0,
        width_ppm=0.03,
        peaks_considered=2,
        points_in_window=40,
    )
    row = peak_result_to_row(result, plot_file="plots/sample.png")
    csv_path = tmp_path / "results.csv"

    write_results_csv(csv_path, [row])

    text = csv_path.read_text(encoding="utf-8")
    assert "sample.dx" in text
    assert "plots/sample.png" in text
    assert "prominence_snr" in text
