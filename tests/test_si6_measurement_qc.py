"""Independent corrected-trace/needle-reference gates without hardware."""

from copy import deepcopy
import hashlib
import json

import numpy as np
import pytest
import yaml

from arduino.python.errors import PositionUncertainError
from arduino.python.needle_state import TrackedNeedle
from chemyx_lab import config
from chemyx_lab.analysis.stage_measurement import fixed_window_measurement
from chemyx_lab.workflows import three_instrument_si6 as si6
from chemyx_lab.workflows.si6_profile import validate_profile


def evidence(tmp_path, *, amplitude=10, negative=False):
    raw = tmp_path / "raw.dx"
    raw.write_bytes(b"synthetic array QC source; not an acquired spectrum")
    folder = tmp_path / "processed/phase_audit/sample"
    folder.mkdir(parents=True)
    x = np.linspace(5.3, 6.3, 2001)
    y = amplitude * np.exp(-(((x - 5.8) / 0.015) ** 2)) + 0.001 * np.sin(
        np.arange(len(x))
    )
    if negative:
        y -= amplitude * np.exp(-(((x - 5.83) / 0.015) ** 2))
    np.savez(folder / "spectral_evidence.npz", region_ppm=x, regional_quantitative=y)
    metadata = dict(
        raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        parameters={"normalization": "none"},
        phase={"method": "stored", "p0_deg": 0, "p1_deg": 0},
    )
    (folder / "processing_metadata.json").write_text(json.dumps(metadata))
    analysis = dict(
        integration_window_ppm=0.1,
        plot_window_ppm=0.5,
        measurement_qc=dict(
            noise_multiplier=3,
            max_noise_fraction=0.01,
            max_area_uncertainty_fraction=0.005,
            undetected_max_fraction=0.025,
        ),
    )
    return raw, folder.parent.parent, analysis


def test_bounded_absence_uses_corrected_trace_not_zero_filled_peak(tmp_path):
    source, processed, analysis = evidence(tmp_path, amplitude=0.01)
    row = dict(
        target_ppm=5.8,
        peak_clear=False,
        peak_area=0,
        qc_failure_reasons="no QC-passing peak",
        rejected_candidate_count=0,
    )
    reference = dict(
        peak_clear=True, peak_area=1, completion_area=1, reference_height=10
    )
    result = fixed_window_measurement(
        source, processed, row, analysis, [], reference=reference
    )
    assert result["peak_area"] == 0
    assert result["completion_area"] > 0 and result["area_uncertainty"] > 0
    assert (
        result["measurement_valid"]
        and result["signal_classification"] == "bounded_low_signal"
    )


@pytest.mark.parametrize(
    "issue", ["no_initial", "cancelled", "not_low", "nan_reference", "zero_reference"]
)
def test_invalid_absence_cannot_complete(tmp_path, issue):
    source, processed, analysis = evidence(
        tmp_path,
        amplitude=10 if issue in {"cancelled", "not_low"} else 0.01,
        negative=issue == "cancelled",
    )
    row = dict(
        target_ppm=5.8,
        peak_clear=False,
        peak_area=0,
        qc_failure_reasons="no QC-passing peak",
    )
    reference = dict(
        peak_clear=True, peak_area=1, completion_area=1, reference_height=10
    )
    if issue == "nan_reference":
        reference["reference_height"] = float("nan")
    if issue == "zero_reference":
        reference["reference_height"] = 0
    with pytest.raises(ValueError):
        fixed_window_measurement(
            source,
            processed,
            row,
            analysis,
            [],
            reference=None if issue == "no_initial" else reference,
        )


def test_live_profile_requires_explicit_needle_home(tmp_path):
    cfg = yaml.safe_load(
        (config.REPO_ROOT / "arduino/configs/arduino.local.yaml").read_text()
    )

    class Controller:
        def status(self):
            return {"moving": "false", "fault": "NONE"}

    state = tmp_path / "needle.json"
    with pytest.raises(PositionUncertainError):
        TrackedNeedle(Controller(), cfg, state_path=state, allow_home_assumption=False)
    demo = TrackedNeedle(Controller(), cfg, state_path=state)
    with pytest.raises(PositionUncertainError, match="Demo-assumed"):
        TrackedNeedle(Controller(), cfg, state_path=state, allow_home_assumption=False)
    demo.confirm_home(operator_confirmed=True)
    explicit = TrackedNeedle(
        Controller(), cfg, state_path=state, allow_home_assumption=False
    )
    assert explicit.position_certain


def test_fixed_gain_enforced_in_profile_and_effective_environment(
    tmp_path, monkeypatch
):
    raw = yaml.safe_load(
        (
            config.REPO_ROOT
            / "config_templates/experiments/si6_two_stage_fast_sim.yaml"
        ).read_text()
    )
    changed = deepcopy(raw)
    changed["nmr"]["auto_gain"] = True
    with pytest.raises(ValueError, match="fixed gain"):
        validate_profile(changed, si6.base.build_stages(raw["workflow"]))
    monkeypatch.setenv("NMR_AUTO_GAIN", "true")
    # Use the actual supported environment key from config's override mapping.
    monkeypatch.setenv("NMR_DEFAULT_AUTO_GAIN", "true")
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="auto_gain"):
        si6.prepare(
            path,
            tmp_path / "no_machine.yaml",
            config.REPO_ROOT / "arduino/configs/arduino.example.yaml",
            mock=True,
        )
