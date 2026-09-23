"""Focused tests for the Phase 4 three-way phase review GUI logic.

Every test here is offline: no spectrometer, pump, or needle is contacted, and
the production processing entry point is mocked wherever a pipeline run is
needed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PySide6 import QtWidgets


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
NMR_SCRIPTS = REPO_ROOT / "scripts/nmr"
sys.path.insert(0, str(NMR_SCRIPTS))

import phase3  # noqa: E402
import phase4  # noqa: E402


pytestmark = pytest.mark.skipif(
    not phase4.DEFAULT_DX.is_file(),
    reason="the audited demo .dx is not present in this checkout",
)


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture(scope="module")
def model():
    return phase4.ReviewSpectrumModel(phase4.DEFAULT_DX)


def _write_summary(
    run_dir: Path,
    *,
    source: Path,
    p0: float = 5.0,
    p1: float = -10.0,
    spectrum_csv: str = "",
    applied_shift_ppm: float = 0.0,
) -> Path:
    """Write a summary shaped like the one process_fid.py produces."""

    run_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "created_at": "2026-09-22T10:00:00",
        "script": "scripts/nmr/process_fid.py",
        "parameters": {
            "phase_method": "stored",
            "baseline_method": "asymmetric_least_squares",
            "zero_fill_points": phase4.ZERO_FILL_POINTS,
            "dataset_display_name": "Demo Dataset",
        },
        "records": [
            {
                "file": source.name,
                "source_path": str(source),
                "phase0_deg": p0,
                "phase1_deg": p1,
                "phase_method": "stored",
                "phase_direction": "inverse",
                "line_broadening_hz": phase4.LINE_BROADENING_HZ,
                "processed_points": phase4.ZERO_FILL_POINTS,
                "applied_shift_ppm": applied_shift_ppm,
                "spectrum_csv": spectrum_csv,
            }
        ],
    }
    path = run_dir / f"{run_dir.name}_summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Source loading and the separation of the three states
# ---------------------------------------------------------------------------


def test_loading_source_dx_exposes_original_and_stored_states(model):
    assert model.path == phase4.DEFAULT_DX.resolve()
    assert model.ppm.size == phase4.ZERO_FILL_POINTS
    assert model.original_spectrum.shape == model.ppm.shape
    # The stored phase is what the pipeline uses for --phase-method stored.
    assert model.stored_p0 == pytest.approx(5.0)
    assert model.stored_p1 == pytest.approx(-10.0)
    assert model.stored_pivot_ppm == pytest.approx(float(model.ppm[0]))


def test_rejects_a_non_dx_path(tmp_path):
    not_dx = tmp_path / "notes.txt"
    not_dx.write_text("not a spectrum", encoding="utf-8")
    with pytest.raises(ValueError):
        phase4.ReviewSpectrumModel(not_dx)


def test_original_automated_and_manual_stay_three_distinct_states(tmp_path, model):
    """Original is unphased; Automated and Manual are independent of it."""

    summary = _write_summary(
        tmp_path / "run_a", source=phase4.DEFAULT_DX, p0=5.0, p1=-10.0
    )
    automated = phase4.automated_result_from_summary(
        summary, source_filename=phase4.DEFAULT_DX.name
    )
    auto_ppm, auto_real, provenance = phase4.automated_spectrum(automated, model)
    manual = model.phased(40.0, 25.0, 5.8)

    original = model.original_spectrum
    assert provenance == phase4.SPECTRUM_RECONSTRUCTED
    assert not np.allclose(original, auto_real)
    assert not np.allclose(original, manual)
    assert not np.allclose(auto_real, manual)
    # The original is genuinely unphased: phasing by zero reproduces it.
    assert np.allclose(original, model.phased_index_zero(0.0, 0.0), atol=1e-9)
    assert np.allclose(auto_ppm, model.ppm)


def test_manual_phase_is_applied_once_from_the_cached_fft(model):
    """Two identical manual settings give identical traces (no drift)."""

    first = model.phased(12.0, -8.0, 5.5)
    second = model.phased(12.0, -8.0, 5.5)
    assert np.array_equal(first, second)
    # And the original is untouched by manual phasing.
    assert np.allclose(
        model.original_spectrum, np.real(model.fft_spectrum), atol=0.0
    )


# ---------------------------------------------------------------------------
# Automated metadata and the review round trip
# ---------------------------------------------------------------------------


def test_automated_result_serializes_the_required_metadata(tmp_path):
    summary = _write_summary(
        tmp_path / "run_b", source=phase4.DEFAULT_DX, p0=5.0, p1=-10.0
    )
    automated = phase4.automated_result_from_summary(
        summary, source_filename=phase4.DEFAULT_DX.name
    )
    payload = automated.to_payload()

    assert payload["entry_point"] == "scripts/nmr/process_fid.py:main"
    assert payload["source_filename"] == phase4.DEFAULT_DX.name
    assert Path(payload["source_dx"]) == phase4.DEFAULT_DX.resolve()
    assert Path(payload["output_directory"]) == summary.parent
    assert Path(payload["summary_path"]) == summary
    assert payload["phase"]["p0_deg"] == pytest.approx(5.0)
    assert payload["phase"]["p1_deg"] == pytest.approx(-10.0)
    assert payload["phase"]["phase_method"] == "stored"
    assert payload["processed_at"] == "2026-09-22T10:00:00"
    assert payload["processing_settings"]["baseline_method"] == (
        "asymmetric_least_squares"
    )
    assert phase4.AutomatedPhaseResult.from_payload(payload) == automated


def test_phase_review_json_round_trip(tmp_path, model):
    summary = _write_summary(tmp_path / "run_c", source=phase4.DEFAULT_DX)
    automated = phase4.automated_result_from_summary(
        summary, source_filename=phase4.DEFAULT_DX.name
    )
    candidates = phase4.PhaseCandidateCollection()
    candidates.add(11.0, -3.0, 5.7)
    candidates.add(-2.0, 7.5, 4.9)
    manual = phase4.PhaseCandidate("Manual", 11.0, -3.0, 5.7)

    review_path = tmp_path / "review.json"
    phase4.save_phase_review(
        review_path,
        model=model,
        automated=automated,
        manual=manual,
        candidates=candidates,
        when=datetime(2026, 9, 22, 11, 30, 0),
    )
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "chemyx-pump.nmr-phase-review.v1"
    assert payload["review_only"] is True

    restored = phase4.load_phase_review(review_path)
    assert restored.source_dx == phase4.DEFAULT_DX.resolve()
    assert restored.automated == automated
    assert restored.manual == manual
    assert restored.candidates == candidates.candidates
    assert restored.created_at.startswith("2026-09-22T11:30:00")


def test_load_phase_review_rejects_a_foreign_schema(tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"schema": "something.else.v1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="not a supported phase review file"):
        phase4.load_phase_review(path)


def test_missing_source_dx_is_reported_clearly(tmp_path, model):
    summary = _write_summary(tmp_path / "run_d", source=phase4.DEFAULT_DX)
    automated = phase4.automated_result_from_summary(
        summary, source_filename=phase4.DEFAULT_DX.name
    )
    review_path = tmp_path / "moved_review.json"
    phase4.save_phase_review(
        review_path, model=model, automated=automated, manual=None
    )

    # Rewrite the review so it points at a .dx that is nowhere to be found.
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    payload["source_dx"] = str(tmp_path / "gone" / "missing_spectrum.dx")
    payload["source_filename"] = "missing_spectrum.dx"
    payload["automated"]["source_dx"] = payload["source_dx"]
    review_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(phase4.SourceDxNotFound) as excinfo:
        phase4.load_phase_review(review_path, search_roots=[tmp_path])
    message = str(excinfo.value)
    assert "missing_spectrum.dx" in message
    assert "Searched" in message


def test_stale_recorded_source_path_falls_back_to_the_run_directory(tmp_path):
    """Production summaries from another machine carry paths that moved."""

    run_dir = tmp_path / "20260810_171441_si6"
    raw = run_dir / "raw_nmr"
    raw.mkdir(parents=True)
    relocated = raw / phase4.DEFAULT_DX.name
    relocated.write_bytes(phase4.DEFAULT_DX.read_bytes())

    stale = Path(r"C:\Code\chemyx_pump\chemyx\results\runs\gone") / relocated.name
    found = phase4.resolve_source_dx(
        stale, relocated.name, search_roots=[run_dir]
    )
    assert found == relocated.resolve()


# ---------------------------------------------------------------------------
# Copying the automated phase into the manual controls
# ---------------------------------------------------------------------------


def test_copy_automated_to_manual_reproduces_the_automated_trace(tmp_path, model):
    summary = _write_summary(
        tmp_path / "run_e", source=phase4.DEFAULT_DX, p0=5.0, p1=-10.0
    )
    automated = phase4.automated_result_from_summary(
        summary, source_filename=phase4.DEFAULT_DX.name
    )
    candidate = phase4.automated_as_candidate(automated, model)

    assert candidate.name == "Automated"
    assert candidate.pivot_ppm == pytest.approx(model.stored_pivot_ppm)
    assert candidate.p0_deg == pytest.approx(automated.p0_deg)
    assert candidate.p1_deg == pytest.approx(automated.p1_deg)

    _, automated_real, _ = phase4.automated_spectrum(automated, model)
    manual = model.phased(candidate.p0_deg, candidate.p1_deg, candidate.pivot_ppm)
    assert np.allclose(manual, automated_real, atol=1e-8)


def test_pivot_conversion_is_invertible_at_any_pivot(model):
    pivot = 5.8
    index_zero = model.effective_p0(33.0, -21.0, pivot)
    assert model.p0_at_pivot(index_zero, -21.0, pivot) == pytest.approx(33.0)


# ---------------------------------------------------------------------------
# Phase set compatibility with Phase 3
# ---------------------------------------------------------------------------


def test_phase3_phase_sets_load_unchanged_in_phase4(tmp_path):
    written_by_phase3 = phase3.PhaseCandidateCollection()
    written_by_phase3.add(12.3, -45.6, 5.812345)
    written_by_phase3.add(-7.8, 90.1, 4.701234)
    path = tmp_path / "phase3-set.json"
    written_by_phase3.save(path, phase4.DEFAULT_DX, "Demo Dataset")

    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == (
        "chemyx-pump.nmr-phase-set.v1"
    )

    read_by_phase4 = phase4.PhaseCandidateCollection()
    read_by_phase4.load(path, phase4.DEFAULT_DX)
    assert [
        (c.name, c.p0_deg, c.p1_deg, c.pivot_ppm)
        for c in read_by_phase4.candidates
    ] == [
        (c.name, c.p0_deg, c.p1_deg, c.pivot_ppm)
        for c in written_by_phase3.candidates
    ]
    assert read_by_phase4.selected_index == written_by_phase3.selected_index


def test_phase4_phase_sets_load_back_into_phase3(tmp_path):
    written_by_phase4 = phase4.PhaseCandidateCollection()
    written_by_phase4.add(1.5, -2.5, 5.0)
    path = tmp_path / "phase4-set.json"
    written_by_phase4.save(path, phase4.DEFAULT_DX, "Demo Dataset")

    read_by_phase3 = phase3.PhaseCandidateCollection()
    read_by_phase3.load(path, phase4.DEFAULT_DX)
    assert read_by_phase3.candidates[0].p0_deg == pytest.approx(1.5)


def test_named_candidates_do_not_collide(tmp_path):
    collection = phase4.PhaseCandidateCollection()
    first = collection.add(1.0, 2.0, 3.0, name="Automated")
    second = collection.add(4.0, 5.0, 6.0, name="Automated")
    assert first.name == "Automated"
    assert second.name == "Automated (2)"


# ---------------------------------------------------------------------------
# Browsing completed automated runs
# ---------------------------------------------------------------------------


def _build_fake_run(root: Path, run: str = "20260810_171441_si6") -> Path:
    run_dir = root / "project_demo" / run
    raw = run_dir / "raw_nmr"
    raw.mkdir(parents=True)
    dx = raw / phase4.DEFAULT_DX.name
    dx.write_bytes(phase4.DEFAULT_DX.read_bytes())
    analysis = run_dir / "processed_nmr" / "demo_full_spectrum"
    _write_summary(analysis, source=dx)
    return run_dir


def test_discover_runs_and_acquisitions_in_a_completed_run(tmp_path):
    run_dir = _build_fake_run(tmp_path)

    runs = phase4.discover_runs(tmp_path)
    assert runs == [run_dir]

    acquisitions = phase4.discover_acquisitions(run_dir)
    assert len(acquisitions) == 1
    acquisition = acquisitions[0]
    assert acquisition.run_name == run_dir.name
    assert acquisition.source_dx.name == phase4.DEFAULT_DX.name
    assert acquisition.has_automated_result
    assert acquisition.analysis_directory.name == "demo_full_spectrum"
    assert "[processed]" in acquisition.label()


def test_discover_runs_reports_nothing_for_a_missing_root(tmp_path):
    assert phase4.discover_runs(tmp_path / "nowhere") == []


def test_acquisition_without_a_processed_result_is_marked(tmp_path):
    run_dir = tmp_path / "project_demo" / "20260101_000000_si6"
    raw = run_dir / "raw_nmr"
    raw.mkdir(parents=True)
    (raw / phase4.DEFAULT_DX.name).write_bytes(phase4.DEFAULT_DX.read_bytes())

    acquisition = phase4.discover_acquisitions(run_dir)[0]
    assert not acquisition.has_automated_result
    assert acquisition.summary_path is None


def test_acquisition_details_read_structured_jcamp_metadata(tmp_path):
    run_dir = _build_fake_run(tmp_path)
    acquisition = phase4.discover_acquisitions(run_dir)[0]
    details = phase4.acquisition_details(acquisition)

    # Read from the JCAMP header, not parsed out of the filename.
    assert details["scans"] == "8"
    assert details["gain"] == "12"
    assert details["acquired_at"].startswith("2026-08-10 17:27:21")
    assert details["acquired_at_source"] == "LONG DATE header"
    assert details["automated_result"] == "present"


# ---------------------------------------------------------------------------
# Running the real pipeline (mocked) without touching the source
# ---------------------------------------------------------------------------


def test_automated_processing_loads_the_pipeline_output_and_leaves_source_alone(
    tmp_path, model
):
    source_copy = tmp_path / "source" / phase4.DEFAULT_DX.name
    source_copy.parent.mkdir(parents=True)
    source_copy.write_bytes(phase4.DEFAULT_DX.read_bytes())
    before = source_copy.read_bytes()
    before_mtime = source_copy.stat().st_mtime_ns

    calls: list[list[str]] = []

    def fake_pipeline(arguments: list[str]) -> int:
        calls.append(arguments)
        output_root = Path(arguments[arguments.index("--output-dir") + 1])
        run_name = arguments[arguments.index("--run-name") + 1]
        run_dir = output_root / run_name
        assert not run_dir.exists()
        run_dir.mkdir(parents=True)
        csv_path = run_dir / f"{run_name}_spectrum.csv"
        rows = ["original_ppm,referenced_ppm,real,imaginary,magnitude"]
        for ppm, real in zip(model.ppm, model.stored_spectrum):
            rows.append(f"{ppm},{ppm},{real},0.0,{abs(real)}")
        csv_path.write_text("\n".join(rows), encoding="utf-8")
        _write_summary(
            run_dir, source=source_copy, spectrum_csv=str(csv_path)
        )
        return 0

    result = phase4.run_automated_processing(
        source=source_copy,
        output_root=tmp_path / "out",
        dataset_display_name="Demo Dataset",
        pipeline_runner=fake_pipeline,
        when=datetime(2026, 9, 22, 12, 0, 0, 500000),
    )

    arguments = calls[0]
    assert arguments[0] == str(source_copy.resolve())
    assert "--export-csv" in arguments
    # Phase 4 observes what the pipeline decides; it does not impose a phase.
    assert "--phase-method" not in arguments
    assert "--phase0" not in arguments
    assert result.entry_point == "scripts/nmr/process_fid.py:main"
    assert result.produced_by_phase4
    assert result.output_directory.parent == (tmp_path / "out").resolve()

    # The displayed automated trace comes from the pipeline's own CSV.
    ppm, real, provenance = phase4.automated_spectrum(result, model)
    assert provenance == phase4.SPECTRUM_FROM_CSV
    assert np.allclose(real, model.stored_spectrum)
    assert np.allclose(ppm, model.ppm)

    assert source_copy.read_bytes() == before
    assert source_copy.stat().st_mtime_ns == before_mtime


def test_csv_trace_matches_the_reconstruction_within_csv_precision(
    tmp_path, model
):
    """The two automated-trace paths agree, allowing for CSV rounding.

    ``process_fid.py`` writes its spectrum CSV at float32 text precision, so a
    run's exported trace and the reconstruction from its recorded phase agree
    relatively, not bit for bit.  A real export of the audited demo file shows
    a worst-case difference of ~0.005 in a peak of ~72870.
    """

    run_dir = tmp_path / "precision_run"
    run_dir.mkdir(parents=True)
    csv_path = run_dir / "spectrum.csv"
    rows = ["original_ppm,referenced_ppm,real,imaginary,magnitude"]
    for ppm, real in zip(model.ppm, model.stored_spectrum):
        # Mimic the precision of the pipeline's own export.
        rows.append(
            f"{np.float32(ppm)},{np.float32(ppm)},{np.float32(real)},0.0,"
            f"{np.float32(abs(real))}"
        )
    csv_path.write_text("\n".join(rows), encoding="utf-8")
    _write_summary(
        run_dir, source=phase4.DEFAULT_DX, spectrum_csv=str(csv_path)
    )

    result = phase4.automated_result_from_summary(
        run_dir / f"{run_dir.name}_summary.json",
        source_filename=phase4.DEFAULT_DX.name,
    )
    from_csv_ppm, from_csv, provenance = phase4.automated_spectrum(result, model)
    assert provenance == phase4.SPECTRUM_FROM_CSV

    reconstructed = model.phased_index_zero(result.p0_deg, result.p1_deg)
    scale = float(np.max(np.abs(reconstructed)))
    assert np.max(np.abs(from_csv - reconstructed)) < 1e-6 * scale
    assert np.max(np.abs(from_csv_ppm - model.ppm)) < 1e-4

    # Copying that automated phase into Manual still reproduces the trace.
    candidate = phase4.automated_as_candidate(result, model)
    manual = model.phased(
        candidate.p0_deg, candidate.p1_deg, candidate.pivot_ppm
    )
    assert np.max(np.abs(manual - from_csv)) < 1e-6 * scale


def test_automated_processing_surfaces_a_pipeline_failure(tmp_path):
    with pytest.raises(RuntimeError, match="exit code 1"):
        phase4.run_automated_processing(
            source=phase4.DEFAULT_DX,
            output_root=tmp_path,
            dataset_display_name="Demo Dataset",
            pipeline_runner=lambda _arguments: 1,
        )


def test_reference_shift_is_applied_to_the_reconstructed_axis(tmp_path, model):
    summary = _write_summary(
        tmp_path / "run_shift",
        source=phase4.DEFAULT_DX,
        applied_shift_ppm=0.25,
    )
    automated = phase4.automated_result_from_summary(
        summary, source_filename=phase4.DEFAULT_DX.name
    )
    ppm, _, provenance = phase4.automated_spectrum(automated, model)
    assert provenance == phase4.SPECTRUM_RECONSTRUCTED
    assert np.allclose(ppm, model.ppm + 0.25)


# ---------------------------------------------------------------------------
# Window wiring
# ---------------------------------------------------------------------------


def test_window_shows_three_states_and_copies_automated_into_manual(
    qapp, tmp_path
):
    window = phase4.Phase4Window(phase4.DEFAULT_DX, runs_root=tmp_path)
    try:
        # Before any automated run, only Original and Manual carry data.
        assert window.automated is None
        assert window.original_curve.getData()[0] is not None
        assert "not run yet" in window.automated_phase_label.text()

        summary = _write_summary(tmp_path / "run_w", source=phase4.DEFAULT_DX)
        automated = phase4.automated_result_from_summary(
            summary, source_filename=phase4.DEFAULT_DX.name
        )
        window._set_automated(automated)

        assert window.copy_button.isEnabled()
        assert "P0 = 5.0°" in window.automated_phase_label.text()

        window.p0_slider.setValue(900)
        assert "P0 = 90.0°" in window.manual_phase_label.text()

        window._copy_automated_to_manual()
        p0_deg, p1_deg, pivot_ppm = window._current_values()
        assert p0_deg == pytest.approx(automated.p0_deg, abs=0.05)
        assert p1_deg == pytest.approx(automated.p1_deg, abs=0.05)
        assert pivot_ppm == pytest.approx(window.model.stored_pivot_ppm)

        # Original is still the unphased trace after all of that.
        original_y = window.original_curve.getData()[1]
        assert np.allclose(original_y, window.model.original_spectrum)
    finally:
        window.close()


def test_overlay_checkbox_adds_a_comparison_plot_without_hiding_the_stack(
    qapp, tmp_path
):
    window = phase4.Phase4Window(phase4.DEFAULT_DX, runs_root=tmp_path)
    try:
        assert not window.overlay_widget.isVisible()
        window.overlay_checkbox.setChecked(True)
        assert window.overlay_widget.isVisibleTo(window)
        assert window.stacked_widget.isVisibleTo(window)
    finally:
        window.close()
