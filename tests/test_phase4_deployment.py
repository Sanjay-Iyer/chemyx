"""Focused offline phase review, matching, numerical reruns and preservation."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
import sys
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import yaml
from PySide6 import QtWidgets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/nmr"))
import phase4
import process_fid
from chemyx_lab.analysis.final_nmr_summary import summarize_run

FIXTURES = ROOT / "chemyx_lab/testing/fixtures"


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def summary_for(folder, source, *, settings=None):
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"parameters": settings or {}, "records": [{
        "file": source.name, "source_path": str(source), "phase0_deg": 5,
        "phase1_deg": -10, "phase_direction": "inverse", "error": "",
    }]}
    path = folder / f"{folder.name}_summary.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_unprocessed_acquisition_never_borrows_another_record(tmp_path, qapp):
    raw = tmp_path / "raw_nmr"
    raw.mkdir()
    a, b = raw / "a.dx", raw / "b.dx"
    a.write_bytes((FIXTURES / "tracked_resonance_phsi4_20260810.dx").read_bytes())
    b.write_bytes((FIXTURES / "no_resonance_phsi2_20260609_0900.dx").read_bytes())
    summary = summary_for(tmp_path / "processed_nmr/a", a)
    acquisitions = phase4.discover_acquisitions(tmp_path)
    assert acquisitions[0].has_automated_result
    assert not acquisitions[1].has_automated_result
    assert "UNPROCESSED" in acquisitions[1].label()
    with pytest.raises(ValueError, match="No exact"):
        phase4.automated_result_from_summary(summary, source_filename=b.name)
    payload = json.loads(summary.read_text())
    payload["records"][0]["raw_sha256"] = "0" * 64
    summary.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="content does not match"):
        phase4.automated_result_from_summary(summary, source_filename=a.name)
    payload["records"][0].pop("raw_sha256")
    summary.write_text(json.dumps(payload), encoding="utf-8")
    result = phase4.automated_result_from_summary(summary, source_filename=a.name)
    window = phase4.Phase4Window(a, runs_root=tmp_path)
    try:
        window._load_source(a, result)
        window._copy_automated_to_manual()
        window.phase_candidates.add(30, 40, 5.8)
        window._load_source(b)
        assert window.automated is None
        assert window.automated_curve.getData()[0] is None or len(window.automated_curve.getData()[0]) == 0
        assert not window.copy_button.isEnabled()
        assert not window.manual_analysis_button.isEnabled()
        assert not window.phase_candidates.candidates
        assert "UNPROCESSED" in window.status.text()
        assert np.allclose(window.original_curve.getData()[1], window.model.original_spectrum)
    finally:
        window.close()


def test_recorded_fft_settings_and_direction_are_respected(tmp_path, qapp):
    source = FIXTURES / "tracked_resonance_phsi4_20260810.dx"
    settings = {"line_broadening_hz": 0.1, "zero_fill_points": 131072,
                "truncation_window": "half-cosine"}
    path = summary_for(tmp_path / "result", source, settings=settings)
    data = json.loads(path.read_text())
    data["records"][0].update(line_broadening_hz=0.1, processed_points=131072)
    path.write_text(json.dumps(data), encoding="utf-8")
    result = phase4.automated_result_from_summary(path, source_filename=source.name)
    window = phase4.Phase4Window(source, runs_root=tmp_path)
    try:
        window._set_automated(result)
        expected = phase4.ReviewSpectrumModel(source, processing_settings=settings)
        assert window.model.processed_points == 131072
        assert np.allclose(window.model.fft_spectrum, expected.fft_spectrum)
        assert window.original_plot.getViewBox().state["xInverted"]
        for direction in ("inverse", "direct", "automatic"):
            variant = replace(result, phase_direction=direction)
            candidate = phase4.automated_as_candidate(variant, expected, pivot_ppm=5.8)
            _, after, _ = phase4.automated_spectrum(variant, expected)
            assert np.allclose(expected.phased(candidate.p0_deg, candidate.p1_deg, candidate.pivot_ppm), after)
    finally:
        window.close()


def test_review_destination_cannot_overwrite_production(tmp_path):
    raw = tmp_path / "raw_nmr"
    raw.mkdir()
    source = raw / "a.dx"
    source.write_bytes((FIXTURES / "tracked_resonance_phsi4_20260810.dx").read_bytes())
    path = summary_for(tmp_path / "processed_nmr/a", source)
    result = phase4.automated_result_from_summary(path, source_filename=source.name)
    model = phase4.ReviewSpectrumModel(source)
    for destination in (source, path, path.parent, tmp_path / "final_nmr_summary/review.json"):
        with pytest.raises(ValueError, match="separate"):
            phase4.save_phase_review(destination, model=model, automated=result)
    journal = tmp_path / "operation_journal.jsonl"
    journal.write_text('{"event_type":"terminal"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="non-review"):
        phase4.save_phase_review(journal, model=model, automated=result)
    with pytest.raises(ValueError, match="separate"):
        phase4.run_manual_processing(model=model, automated=result,
            manual=phase4.automated_as_candidate(result, model), output_root=path.parent)


@pytest.fixture(scope="module")
def production_run(tmp_path_factory):
    """Two genuine fixtures through the unchanged production processor."""
    root = tmp_path_factory.mktemp("phase_real")
    raw = root / "raw_nmr"
    raw.mkdir()
    cfg, _, _ = process_fid._resolved_config_mapping([])
    # Extra statistics charts are outside this test; quantitative processing is unchanged.
    cfg["statistics"]["enabled"] = False
    cfg["target_peak"]["enabled"] = False
    config = root / "test_processing.yaml"
    config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    results = []
    for name, fixture in (("a", "tracked_resonance_phsi4_20260810.dx"),
                          ("b", "no_resonance_phsi2_20260609_0900.dx")):
        source = raw / f"{name}.dx"
        source.write_bytes((FIXTURES / fixture).read_bytes())
        assert process_fid.main([str(source), "--config", str(config),
            "--output-dir", str(root / "processed_nmr"), "--run-name", name,
            "--dataset-display-name", "PHASE-REVIEW-TEST", "--export-csv",
            "--simple-restrict-to-window", "--simple-window-ppm", "0.1"]) == 0
        results.append(phase4.automated_result_from_summary(
            root / f"processed_nmr/{name}/{name}_summary.json", source_filename=source.name))
    peak = read_csv(next(results[0].output_directory.glob("*peaks_simple.csv")))[0]
    row = dict(iteration=1, stage="fixture_review", file="a.dx", peak_clear=True,
               peak_ppm=peak["peak_ppm"], peak_area=peak["integrated_area"],
               snr=peak["snr"], prominence_snr=peak["prominence_snr"],
               growth_percent="", plateau=False)
    with (root / "time_series.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    summarize_run(root)
    return root, config, results


def test_real_manual_analysis_uses_phase_and_preserves_production(production_run, qapp):
    root, config, results = production_run
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    window = phase4.Phase4Window(results[0].source_dx, runs_root=root)
    try:
        a, b = results
        assert (a.p0_deg, a.p1_deg) == (5, -10)
        assert (b.p0_deg, b.p1_deg) == (59.5, -79)
        window._load_source(a.source_dx, a)
        window._copy_automated_to_manual()
        candidate = phase4.PhaseCandidate("Manual", *window._current_values())
        runner = lambda args: process_fid.main([*args, "--config", str(config)])
        original = phase4.run_manual_processing(model=window.model, automated=a,
            manual=candidate, output_root=root / "manual_reviews", pipeline_runner=runner)
        original_area = float(read_csv(next(original.glob("*peaks_simple.csv")))[0]["integrated_area"])
        production_area = float(read_csv(next(a.output_directory.glob("*peaks_simple.csv")))[0]["integrated_area"])
        assert original_area == pytest.approx(production_area, abs=0.01)
        window.p0_slider.setValue(250)  # deliberately different manual phase
        window.p1_slider.setValue(-70)
        window.pivot.setValue(5.8)
        changed = phase4.PhaseCandidate("Manual", *window._current_values())
        output = phase4.run_manual_processing(model=window.model, automated=a,
            manual=changed, output_root=root / "manual_reviews", pipeline_runner=runner)
        payload = json.loads((output / "manual_phase_review.json").read_text())
        manual_result = phase4.AutomatedPhaseResult.from_payload(payload["manual_analysis"])
        assert manual_result.phase_method == "manual"
        assert manual_result.p0_deg == pytest.approx(window.model.effective_p0(25, -7, 5.8))
        _, phased, _ = phase4.automated_spectrum(manual_result, window.model)
        scale = np.max(np.abs(phased))
        assert np.max(np.abs(phased - window.model.phased(25, -7, 5.8))) < 1e-6 * scale
        old_qc = read_csv(next(original.glob("*peak_qc_log.csv")))
        new_qc = read_csv(next(output.glob("*peak_qc_log.csv")))
        assert old_qc != new_qc  # numerical detection/integration/QC followed the phase
        changed_peaks = read_csv(next(output.glob("*peaks_simple.csv")))
        assert changed_peaks and float(changed_peaks[0]["integrated_area"]) != pytest.approx(original_area, abs=0.01)
        assert payload["automated"]["phase"]["p0_deg"] == 5
        assert payload["manual"]["pivot_ppm"] == 5.8
        assert payload["review_only"] and payload["analysis_role"] == "manual_review"
        restored = phase4.load_phase_review(output / "manual_phase_review.json")
        assert restored.manual == changed
        window._load_source(b.source_dx, b)
        window._copy_automated_to_manual()
        assert window._current_values()[:2] == (59.5, -79)
        assert not window.phase_candidates.candidates
        assert window.last_manual_output is None
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items())
        regenerated = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/nmr/summarize_run.py"), str(root)], capture_output=True, text=True)
        assert regenerated.returncode == 0, regenerated.stdout + regenerated.stderr
        # Reporting still follows the production tables, ignoring the two
        # separate manual review folders, including their altered peak areas.
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items())
    finally:
        window.close()


def test_manual_button_dispatches_current_controls_without_replacing_production(tmp_path, qapp, monkeypatch):
    source = FIXTURES / "tracked_resonance_phsi4_20260810.dx"
    summary = summary_for(tmp_path / "production", source)
    result = phase4.automated_result_from_summary(summary, source_filename=source.name)
    window = phase4.Phase4Window(source, runs_root=tmp_path)
    captured = []
    def recalculate(**kwargs):
        captured.append(kwargs)
        return tmp_path / "manual_reviews/phase4_manual_test"
    monkeypatch.setattr(phase4, "run_manual_processing", recalculate)
    try:
        window._set_automated(result)
        window.output_directory = tmp_path / "manual_reviews"
        window.p0_slider.setValue(250)
        window.p1_slider.setValue(-70)
        window.pivot.setValue(5.8)
        window.manual_analysis_button.click()
        assert len(captured) == 1
        assert captured[0]["manual"] == phase4.PhaseCandidate("Manual", 25, -7, 5.8)
        assert window.automated is result
        assert window.last_manual_output == tmp_path / "manual_reviews/phase4_manual_test"
        assert window.manual_analysis_button.isEnabled() and window.run_button.isEnabled()
    finally:
        window.close()
