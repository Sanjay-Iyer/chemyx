"""Full mock control with actual selected phase-derived area observations."""
import json
import numpy as np
import pytest
from chemyx_lab.testing.primary_phase_validation import run_primary_workflow, complex_spectrum
from chemyx_lab.analysis.phase_methods import apply_phase
from chemyx_lab.analysis.solvent_presence import evaluate_solvent_spectrum, DEFAULTS


@pytest.mark.parametrize("method", ["stored", "acme", "combined_objective", "ernst_p0", "deep_phaser"])
def test_full_selected_primary_workflow(tmp_path, monkeypatch, method):
    from chemyx_lab.workflows import three_instrument_si6 as si6
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("unexpected prompt"))
    if method == "deep_phaser":
        # Adapter/model inference is independently benchmarked on all archived
        # FIDs. This control test mocks only inference, not spectrum processing,
        # areas, endpoints, mock instruments or the dose ledger.
        from chemyx_lab.analysis import phase_methods
        monkeypatch.setattr(phase_methods, "deep_request", lambda *a, **k: {
            "success": True, "status": "TEST model inference fixture",
            "p0_deg": 23., "p1_deg": -47., "inverse_phase": True})
    # Reports are independently tested; keep this test focused on control/math.
    monkeypatch.setattr(si6, "final_reports", lambda *a: None)
    result = run_primary_workflow(tmp_path / method, method)
    assert result["outcome"].status.value == "completed", result["outcome"]
    assert result["rows"] and all(r["phase_method"] == method for r in result["rows"])
    assert all(r["phase"]["success"] for r in result["rows"])
    events = [json.loads(line) for line in result["journal"].read_text().splitlines()]
    assert sum(e["event_type"] == "dose_confirmed" for e in events) == 1
    assert result["run_dir"].parent.name == "experiments"
    assert (result["run_dir"] / "step_1/analysis/stage_completion.json").exists()
    assert (result["run_dir"] / "step_2/analysis/stage_completion.json").exists()
    assert {r["stage"] for r in result["rows"]} == {"stage_1", "stage_2"}


def test_acme_missing_solvent_continues_to_timed_deadline(tmp_path, monkeypatch):
    from chemyx_lab.workflows import three_instrument_si6 as si6
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("unexpected prompt"))
    monkeypatch.setattr(si6, "final_reports", lambda *a: None)
    result = run_primary_workflow(tmp_path / "missing", "acme", missing_solvent=True)
    assert result["outcome"].status.value == "completed"
    info = json.loads((result["run_dir"] / "metadata/experiment_info.json").read_text())
    assert info["timed_fallback_occurred"] and not info["chemical_completion_verified"]
    assert set(info["stage_end_reasons"].values()) == {"timed_fallback_completed"}
    recipe = json.loads((result["run_dir"] / "metadata/config_snapshot.json").read_text())
    assert recipe["workflow"]["initial_stage"]["max_hours"] == 48
    assert recipe["workflow"]["first_addition_stage"]["max_hours"] == 4
    from datetime import datetime
    for stage, duration in (("stage_1", 48), ("stage_2", 4)):
        events = [e for e in result["virtual_stage_clock"] if e["stage"] == stage]
        start = next(e for e in events if e["event_type"] == "monitoring_stage_started")
        end = next(e for e in events if e["event_type"] == "stage_transition_decision")
        elapsed = (datetime.fromisoformat(end["virtual_time"])-datetime.fromisoformat(start["virtual_time"])).total_seconds()
        assert elapsed >= duration*3600 - 1.e-6
    assert not result["rows"]  # no invented target measurement for blank samples
    assert info["effective_nmr_processing"]["phase_method"] == "acme"


def test_failed_primary_no_dose(tmp_path, monkeypatch):
    from chemyx_lab.workflows import three_instrument_si6 as si6
    monkeypatch.setattr(si6, "final_reports", lambda *a: None)
    result = run_primary_workflow(tmp_path / "failed", fail_primary=True)
    assert result["outcome"].exit_code != 0
    events = [json.loads(line) for line in result["journal"].read_text().splitlines()]
    assert not any(e["event_type"] == "dose_confirmed" for e in events)
    assert not result["rows"]


@pytest.mark.parametrize("case", ["weak", "absent", "shift", "overlap", "noise", "missing_solvent"])
def test_difficult_spectra_finite_and_solvent_phase_independent(case):
    x, z = complex_spectrum(.5 if case == "weak" else 0 if case == "absent" else 100.,
            drift=.08 if case == "shift" else 0., overlap=case == "overlap",
            noise=100 if case == "noise" else 1., solvent=case != "missing_solvent")
    phased, audit = apply_phase(x, z, {}, "acme")
    assert audit["success"] and np.isfinite(phased).all()
    a = evaluate_solvent_spectrum(x, np.abs(z), DEFAULTS)
    b = evaluate_solvent_spectrum(x, np.abs(phased), DEFAULTS)
    assert a["present"] == b["present"]
    assert a["present"] == (case != "missing_solvent")
