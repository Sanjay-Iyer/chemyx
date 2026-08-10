import math
from pathlib import Path

import pytest
import yaml

from chemyx_lab.config import PumpConfig
from chemyx_lab.workflows.si6_automated_nmr import (
    EXIT_CODES,
    AnalysisInconclusiveError,
    OperatorAbortError,
    PumpSafetyState,
    RunOutcome,
    RunPaths,
    Stage,
    StopStatus,
    TerminalStatus,
    build_run_summary,
    execute_with_emergency_stop,
    growth_percent,
    load_si6_config,
    main,
    maximum_duration_outcome,
    plateau_reached,
    run_cycle,
    run_process_fid_postprocessing,
    run_safe_metered_move,
    run_stage_sequence,
    stage_within_duration,
    validate_syringe_capacity,
)


CONFIG = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "experiments"
    / "02_si6_automated_nmr.yaml"
)


class FakePump:
    def __init__(self, *, stop_error=None, stop_response="stop"):
        self.calls = []
        self.stop_error = stop_error
        self.stop_response = stop_response

    def set_volume(self, volume):
        self.calls.append(("set_volume", volume))
        return f"volume={volume}"

    def start(self, delay=None):
        self.calls.append(("start", delay))
        return f"start={delay}"

    def stop(self):
        self.calls.append(("stop", None))
        if self.stop_error is not None:
            raise self.stop_error
        return self.stop_response


def pump_config() -> PumpConfig:
    return PumpConfig(
        port="FAKE",
        baud_rate=115200,
        channel=1,
        units=0,
        diameter=28.6,
        rate=5.0,
        volume=5.0,
        timeout=2.0,
        response_delay=0.0,
    )


@pytest.mark.parametrize(
    "termination",
    [
        KeyboardInterrupt("ctrl-c"),
        SystemExit(9),
        RuntimeError("normal exception"),
        OperatorAbortError("operator abort"),
        AnalysisInconclusiveError("analysis failed"),
    ],
)
def test_emergency_stop_is_attempted_for_all_termination_paths(termination):
    pump = FakePump()
    state = PumpSafetyState()

    def action():
        raise termination

    with pytest.raises(type(termination)):
        execute_with_emergency_stop(pump, state, action)

    assert ("stop", None) in pump.calls
    assert state.last_stop_status is StopStatus.SUCCEEDED


def test_stop_failure_does_not_hide_original_exception():
    original = ValueError("original failure")
    pump = FakePump(stop_error=RuntimeError("stop failed"))
    state = PumpSafetyState()

    with pytest.raises(ValueError, match="original failure") as caught:
        execute_with_emergency_stop(
            pump,
            state,
            lambda: (_ for _ in ()).throw(original),
        )

    assert caught.value is original
    assert state.last_stop_status is StopStatus.FAILED
    assert "stop failed" in state.last_stop_error


def test_interrupted_motion_is_uncertain_and_stop_is_attempted():
    pump = FakePump()
    state = PumpSafetyState()

    def interrupt(_label, _seconds):
        raise KeyboardInterrupt("during motion")

    with pytest.raises(KeyboardInterrupt, match="during motion"):
        run_safe_metered_move(
            pump,
            pump_config(),
            "withdraw",
            8.0,
            state,
            sleep_fn=interrupt,
        )

    assert state.uncertain
    assert state.uncertain_operation == "withdraw 8 mL"
    assert state.stop_attempts >= 1
    assert ("stop", None) in pump.calls


def test_uncertain_withdrawal_never_triggers_return_infusion(tmp_path):
    raw = load_si6_config(CONFIG)
    pump = FakePump()
    state = PumpSafetyState()
    paths = RunPaths(
        tmp_path,
        tmp_path / "raw",
        tmp_path / "plots",
        tmp_path / "time.csv",
        tmp_path / "spectra.csv",
        tmp_path / "operations.csv",
        tmp_path / "manifest.json",
    )

    def interrupt(_label, _seconds):
        raise KeyboardInterrupt("during first withdrawal")

    with pytest.raises(KeyboardInterrupt):
        run_cycle(
            pump,
            pump_config(),
            None,
            raw,
            paths,
            "test",
            state,
            sleep_fn=interrupt,
            operator_fn=lambda _prompt: None,
        )

    commanded_volumes = [value for name, value in pump.calls if name == "set_volume"]
    assert commanded_volumes == [-8.0]
    assert not any(value > 0 for value in commanded_volumes)


def test_uncertain_stage_does_not_reach_next_reagent_checkpoint():
    stages = [
        Stage("one", "initial reagents", 60, 24),
        Stage("two", "add diphenyl silane", 15, 1.5),
    ]
    prompts = []

    outcome = run_stage_sequence(
        stages,
        prompts.append,
        lambda _stage: RunOutcome(TerminalStatus.SAFETY_STOP, "uncertain"),
    )

    assert outcome.status is TerminalStatus.SAFETY_STOP
    assert prompts == ["initial reagents"]


def test_inconclusive_analysis_does_not_reach_next_reagent_checkpoint():
    stages = [
        Stage("one", "initial reagents", 60, 24),
        Stage("two", "add diphenyl silane", 15, 1.5),
    ]
    prompts = []

    outcome = run_stage_sequence(
        stages,
        prompts.append,
        lambda _stage: RunOutcome(
            TerminalStatus.ANALYSIS_INCONCLUSIVE,
            "spectrum could not be analyzed",
        ),
    )

    assert outcome.status is TerminalStatus.ANALYSIS_INCONCLUSIVE
    assert prompts == ["initial reagents"]


@pytest.mark.parametrize(
    ("capacity", "margin", "passes"),
    [
        (12.999, 0.0, False),
        (13.0, 0.0, True),
        (14.0, 0.0, True),
        (14.0, 2.0, False),
        (15.0, 2.0, True),
    ],
)
def test_cumulative_thirteen_ml_capacity_boundary(capacity, margin, passes):
    raw = load_si6_config(CONFIG)
    raw["pump"]["syringe_capacity_ml"] = capacity
    raw["pump"]["syringe_safety_margin_ml"] = margin

    if passes:
        requirement = validate_syringe_capacity(raw)
        assert requirement.maximum_retained_volume_ml == pytest.approx(13.0)
    else:
        with pytest.raises(ValueError, match="Unsafe syringe capacity"):
            validate_syringe_capacity(raw)


def test_capacity_validation_fails_closed_when_capacity_missing():
    raw = load_si6_config(CONFIG)
    del raw["pump"]["syringe_capacity_ml"]

    with pytest.raises(ValueError, match="is required"):
        validate_syringe_capacity(raw)


@pytest.mark.parametrize("capacity", [math.nan, math.inf, -math.inf])
def test_capacity_validation_rejects_nonfinite_capacity(capacity):
    raw = load_si6_config(CONFIG)
    raw["pump"]["syringe_capacity_ml"] = capacity

    with pytest.raises(ValueError, match="must be finite"):
        validate_syringe_capacity(raw)


def test_invalid_capacity_fails_before_hardware_initialization(tmp_path, monkeypatch):
    raw = load_si6_config(CONFIG)
    raw["pump"]["syringe_capacity_ml"] = 12.0
    config_path = tmp_path / "unsafe.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    class ForbiddenPump:
        def __init__(self, *args, **kwargs):
            raise AssertionError("hardware adapter must not be initialized")

    monkeypatch.setattr(
        "chemyx_lab.workflows.si6_automated_nmr.Pump", ForbiddenPump
    )

    assert main(["--workflow-config", str(config_path)]) == EXIT_CODES[
        TerminalStatus.VALIDATION_FAILURE
    ]


def test_capacity_includes_initial_retained_volume_and_margin():
    raw = load_si6_config(CONFIG)
    raw["pump"].update(
        syringe_capacity_ml=16.0,
        initial_retained_volume_ml=2.0,
        syringe_safety_margin_ml=1.0,
    )

    requirement = validate_syringe_capacity(raw)

    assert requirement.maximum_retained_volume_ml == pytest.approx(15.0)


def test_process_fid_runs_for_one_acquisition_with_unique_output(tmp_path):
    raw_dir = tmp_path / "raw_nmr"
    raw_dir.mkdir()
    dx_path = raw_dir / "sample_8scan_gain12.dx"
    dx_path.write_text("test", encoding="utf-8")
    paths = RunPaths(
        tmp_path,
        raw_dir,
        tmp_path / "plots",
        tmp_path / "time.csv",
        tmp_path / "spectra.csv",
        tmp_path / "operations.csv",
        tmp_path / "manifest.json",
    )
    captured = {}

    class Completed:
        returncode = 0

    def fake_runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    output = run_process_fid_postprocessing(
        dx_path,
        paths,
        "081626_phsi4",
        runner=fake_runner,
    )

    command = captured["command"]
    assert command[1] == "-B"
    assert command[3] == str(dx_path.resolve())
    assert command[command.index("--region-min") + 1] == "5"
    assert command[command.index("--region-max") + 1] == "6.5"
    assert command[command.index("--dataset-display-name") + 1] == "081626_phsi4"
    assert captured["kwargs"]["check"] is False
    assert output == (
        tmp_path
        / "processed_nmr"
        / "081626_phsi4_sample_8scan_ga_full_spectrum"
    )


def test_repeated_cycle_with_nonzero_retained_balance_is_rejected():
    raw = load_si6_config(CONFIG)
    raw["workflow"]["cycle"][-1]["volume_ml"] = 4.0

    with pytest.raises(ValueError, match="return to its initial retained volume"):
        validate_syringe_capacity(raw, repetitions=3)


def analysis_config(**overrides):
    values = {
        "plateau_consecutive_intervals": 1,
        "plateau_max_growth_percent": 5.0,
        "plateau_max_decline_percent": 2.0,
        "detection_window_ppm": 0.12,
        "min_peak_snr": 5.0,
        "min_prominence_snr": 3.0,
        "min_peak_area": 1.0,
        "area_epsilon": 1e-9,
    }
    values.update(overrides)
    return values


def measurement(*, growth=None, area=100.0, snr=10.0, prominence=5.0, peak=6.1, error=""):
    return {
        "target_ppm": 6.1,
        "peak_ppm": peak,
        "peak_area": area,
        "snr": snr,
        "prominence_snr": prominence,
        "peak_clear": not error,
        "growth_percent": growth,
        "error": error,
    }


@pytest.mark.parametrize("change", [4.0, 0.0, -1.5])
def test_conservative_plateau_accepts_only_changes_inside_band(change):
    assert plateau_reached(
        [measurement(), measurement(growth=change)],
        analysis_config(),
    )


@pytest.mark.parametrize("change", [-20.0, 5.1, math.inf, math.nan])
def test_conservative_plateau_rejects_large_or_nonfinite_change(change):
    assert not plateau_reached(
        [measurement(), measurement(growth=change)],
        analysis_config(),
    )


@pytest.mark.parametrize(
    "bad_row",
    [
        measurement(area=-1.0, growth=0.0),
        measurement(area=1e-12, growth=0.0),
        measurement(snr=4.9, growth=0.0),
        measurement(prominence=2.9, growth=0.0),
        measurement(peak=6.3, growth=0.0),
        measurement(error="missing peak", growth=0.0),
        measurement(area=math.nan, growth=0.0),
    ],
)
def test_bad_signal_quality_cannot_satisfy_plateau(bad_row):
    assert not plateau_reached(
        [measurement(), bad_row],
        analysis_config(),
    )


def test_bad_spectrum_breaks_consecutive_plateau_window():
    analysis = analysis_config(plateau_consecutive_intervals=3)
    rows = [
        measurement(),
        measurement(growth=1.0),
        measurement(error="corrupt spectrum"),
        measurement(growth=1.0),
        measurement(growth=1.0),
    ]

    assert not plateau_reached(rows, analysis)
    rows.extend([measurement(growth=1.0), measurement(growth=1.0)])
    assert plateau_reached(rows, analysis)


def test_growth_rejects_near_zero_negative_and_nonfinite_areas():
    assert growth_percent(1e-12, 2.0, epsilon=1e-9) is None
    assert growth_percent(10.0, -1.0) is None
    assert growth_percent(10.0, math.inf) is None


def test_maximum_duration_is_not_completion_and_blocks_next_chemistry():
    stages = [
        Stage("one", "initial reagents", 60, 24),
        Stage("two", "next reagent", 15, 1.5),
    ]
    prompts = []
    outcome = run_stage_sequence(
        stages,
        prompts.append,
        lambda stage: RunOutcome(
            TerminalStatus.MAXIMUM_DURATION_REACHED,
            f"{stage.name} timed out",
        ),
    )

    assert outcome.status is TerminalStatus.MAXIMUM_DURATION_REACHED
    assert outcome.status is not TerminalStatus.COMPLETED
    assert prompts == ["initial reagents"]


def test_maximum_duration_boundary_uses_injected_clock_without_waiting():
    stage = Stage("initial", "prepare", 60, 1.0)

    assert stage_within_duration(stage, 100.0, lambda: 3699.999)
    assert not stage_within_duration(stage, 100.0, lambda: 3700.0)
    outcome = maximum_duration_outcome(stage)
    assert outcome.status is TerminalStatus.MAXIMUM_DURATION_REACHED
    assert "No next reagent-addition stage" in outcome.message


@pytest.mark.parametrize("status", list(TerminalStatus))
def test_every_terminal_status_has_exit_code_and_summary(status):
    outcome = RunOutcome(status, f"summary for {status.value}")
    summary = build_run_summary(outcome)

    assert outcome.exit_code == EXIT_CODES[status]
    assert summary["status"] == status.value
    assert summary["exit_code"] == EXIT_CODES[status]
    assert summary["message"] == outcome.message
