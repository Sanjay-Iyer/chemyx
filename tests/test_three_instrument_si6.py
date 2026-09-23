"""Mock-only ordering and safety tests; no serial or NMR endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
import yaml

from arduino.python.errors import LiveExecutionBlocked, PositionUncertainError
from chemyx_lab import config as repo_config
from chemyx_lab.analysis.nmr import NmrProcessingError
from chemyx_lab.instruments.nmr import NmrRpcError
from chemyx_lab.recovery import RecoveryClassification, inspect_run
from chemyx_lab.runtime_journal import JournalError, OperationJournal
from chemyx_lab.workflows import si6_automated_nmr as base
from chemyx_lab.workflows import three_instrument_si6 as integrated

ARDUINO_EXAMPLE = repo_config.REPO_ROOT / "arduino/configs/arduino.example.yaml"
NMR_ANALYSIS_CONFIG = repo_config.REPO_ROOT / "configs/nmr/analysis.yaml"
NO_RESONANCE_FIXTURE = integrated.MOCK_NMR_FIXTURE.with_name("no_resonance_phsi2_20260609_0900.dx")
# Operator-confirmed positions of the tracked resonance (configs/nmr/analysis.yaml).
CONFIRMED_RESONANCE_PPM = (5.785, 5.847)
CLEANUP_ACTIONS = ["infuse 13 DOWN", "UP", "withdraw 5 UP", "infuse 5 UP"]


class FakeNeedle:
    def __init__(self):
        self.position = 100
    def status(self):
        return {"moving": "false", "fault": "NONE", "homed": "true", "position_known": "true", "limit_up": "false", "limit_down": "false", "commanded_position_steps": str(self.position)}
    def stop_best_effort(self):
        return True
    def ping(self):
        return "PONG"
    def enable(self):
        return None
    def home(self):
        self.position = 0


class FakeServices:
    def __init__(self, tmp_path, *, analysis_error=False, pump_error=False, plateau=True, needle_error=False, nmr_error=False):
        self.events = []
        self.needle = FakeNeedle()
        self.arduino_cfg = {"motion": {"safe_up_position_steps": 100, "test_down_position_steps": 500, "maximum_travel_steps": 1000, "maximum_speed_steps_s": 300}}
        self.mock = True
        self.raw = {"three_instrument": {"test_withdraw_ml": 0.5, "test_infuse_ml": 0.5}, "workflow": {"cycle": [
            {"action": "withdraw", "volume_ml": 8}, {"action": "operator"},
            {"action": "withdraw", "volume_ml": 5}, {"action": "pause", "seconds": 300},
            {"action": "nmr"}, {"action": "infuse", "volume_ml": 13},
            {"action": "operator"}, {"action": "withdraw", "volume_ml": 5},
            {"action": "infuse", "volume_ml": 5}]}}
        self.paths = SimpleNamespace(run_dir=tmp_path)
        self.state = base.PumpSafetyState()
        self.recorder = SimpleNamespace(record=lambda *a, **kw: None)
        self.pump = SimpleNamespace(stop=lambda: "pump stop", help=lambda: "Chemyx 4000X")
        self.measurement_step = None
        self.analysis_error = analysis_error
        self.pump_error = pump_error
        self.plateau = plateau
        self.needle_error = needle_error
        self.nmr_error = nmr_error
    def record(self, name, **fields):
        self.events.append((name, fields))
    def assert_pump_idle(self):
        self.events.append(("idle", {}))
    def move_needle(self, label):
        if self.needle_error and label == "DOWN":
            raise RuntimeError("lower position not verified")
        self.events.append((label, {}))
        self.needle.position = 100 if label == "UP" else 500
    def pump_move(self, direction, volume, needle_label, **kwargs):
        if self.pump_error and (direction == "withdraw" or volume == 13):
            raise RuntimeError("pump failed")
        self.events.append((f"{direction} {volume:g} {needle_label}", {}))
    def sleep(self, label, seconds):
        self.events.append(("settle", {"seconds": seconds}))
    def verify_cleanup_preconditions(self, down, expected_ml):
        self.events.append(("precheck", {"down": down, "expected_ml": expected_ml}))
        return self.needle.status()
    def nmr_measurement(self, **kwargs):
        self.measurement_step = "NMR acquisition"
        if self.nmr_error:
            raise RuntimeError("NMR acquisition failed")
        progress = kwargs.get("progress")
        if progress:
            progress("NMR acquisition")
            progress("NMR data retrieval")
        self.measurement_step = "NMR processing"
        if self.analysis_error:
            raise RuntimeError("processing failed")
        if progress:
            progress("NMR processing")
        self.events.append(("NMR", {}))
        if progress:
            progress("NMR analysis")
        path = self.paths.run_dir / "sample.dx"
        processed = self.paths.run_dir / "processed"
        return {"plateau": self.plateau}, path, processed


def physical_actions(events):
    return [name for name, _ in events if name in {"DOWN", "UP", "settle", "NMR"} or name.startswith(("withdraw", "infuse"))]


def test_exact_cycle_order_and_plateau_cleanup(tmp_path):
    s = FakeServices(tmp_path)
    result = integrated.sample_cycle(s, stage="initial", cycle=1, rows=[], started=datetime.now())
    assert result["plateau"] is True
    assert physical_actions(s.events) == ["withdraw 8 UP", "DOWN", "withdraw 5 DOWN", "settle", "NMR", *CLEANUP_ACTIONS]
    statuses = [item[1]["status"] for item in s.events if item[0] in ("cycle_status", "cycle_completed")]
    assert statuses == ["STARTED", "NMR_COMPLETE", "CLEANUP_COMPLETE", "COMPLETE"]
    assert [name for name, _ in s.events if name == "cycle_completed"] == ["cycle_completed"]
    assert s.events.index(next(item for item in s.events if item[0] == "cycle_completed")) > s.events.index(next(item for item in s.events if item[0] == "cycle_status" and item[1].get("status") == "CLEANUP_COMPLETE"))


def test_measurement_failure_with_known_state_still_runs_the_same_cleanup(tmp_path):
    s = FakeServices(tmp_path, analysis_error=True)
    with pytest.raises(integrated.MeasurementFailedAfterCleanup) as caught:
        integrated.sample_cycle(s, stage="initial", cycle=1, rows=[], started=datetime.now())
    assert caught.value.failed_step == "NMR processing"
    assert physical_actions(s.events) == ["withdraw 8 UP", "DOWN", "withdraw 5 DOWN", "settle", *CLEANUP_ACTIONS]
    names = [name for name, _ in s.events]
    assert names.index("precheck") < names.index("infuse 13 DOWN")
    statuses = [fields["status"] for name, fields in s.events if name == "cycle_status"]
    assert statuses == ["STARTED", "MEASUREMENT_FAILED", "CLEANUP_COMPLETE"]
    assert "cycle_completed" not in names
    assert [fields["result_classification"] for name, fields in s.events if name == "recovery_cleanup"] == ["started", "completed"]


def test_physical_failure_never_records_complete(tmp_path):
    s = FakeServices(tmp_path, pump_error=True)
    with pytest.raises(RuntimeError):
        integrated.sample_cycle(s, stage="initial", cycle=1, rows=[], started=datetime.now())
    statuses = [item[1]["status"] for item in s.events if item[0] == "cycle_status"]
    assert statuses[-1] == "FAILED"
    assert "COMPLETE" not in statuses
    assert "manual_inspection_required" in [name for name, _ in s.events]


def test_cycle_requires_configured_sop_order(tmp_path):
    s = FakeServices(tmp_path)
    s.raw["workflow"]["cycle"][5]["action"] = "operator"
    with pytest.raises(ValueError, match="requires"):
        integrated.cycle_values(s.raw)


def test_needle_verification_fails_closed():
    needle = FakeNeedle()
    needle.position = 200
    with pytest.raises(integrated.VerificationError, match="commanded position"):
        integrated.verify_needle(needle, 100)


def test_diagnostic_happy_path(tmp_path):
    s = FakeServices(tmp_path)
    passed = integrated.run_diagnostic(s, "all")
    assert "Needle DOWN" in passed
    assert "Chemyx infuse" in passed
    assert all(label in passed for label in ("NMR acquisition", "NMR data retrieval", "NMR processing", "NMR analysis"))


@pytest.mark.parametrize("failure,label", [
    ("needle_error", "Needle DOWN"),
    ("pump_error", "Chemyx withdraw"),
    ("nmr_error", "NMR acquisition"),
    ("analysis_error", "NMR processing"),
])
def test_diagnostic_names_failed_subsystem(tmp_path, capsys, failure, label):
    s = FakeServices(tmp_path, **{failure: True})
    with pytest.raises(RuntimeError):
        integrated.run_diagnostic(s, "all")
    assert f"[FAIL] {label}" in capsys.readouterr().out
    if failure == "needle_error":
        assert not any(name.startswith("withdraw") for name, _ in s.events)


def test_existing_stage_configuration_and_plateau_rule():
    raw = base.load_si6_config(base.DEFAULT_CONFIG)
    stages = base.build_stages(raw["workflow"])
    assert stages[0].interval_minutes == 60
    assert stages[1].interval_minutes == 15
    assert len(stages) == 4
    assert base.scheduled_measurement_offset_seconds(stages[0], 1) == 3600
    from dataclasses import replace
    assert base.scheduled_measurement_offset_seconds(replace(stages[0], interval_minutes=30), 1) == 1800
    assert raw["analysis"]["plateau_consecutive_intervals"] == 3


def test_integrated_peak_plot_has_visible_dataset_title(tmp_path, monkeypatch):
    import matplotlib.figure
    seen = []
    original = matplotlib.figure.Figure.suptitle
    def capture(self, title, *args, **kwargs):
        seen.append(title)
        return original(self, title, *args, **kwargs)
    monkeypatch.setattr(matplotlib.figure.Figure, "suptitle", capture)
    raw = base.load_si6_config(base.DEFAULT_CONFIG)
    paths = base.create_run_paths(tmp_path)
    dx = paths.raw_dir / "sample.dx"
    shutil.copyfile(repo_config.REPO_ROOT / "nmr_template/experiment_data_1782760626.959469.dx", dx)
    row, _ = base.analyze_timepoint(dx, paths, raw["analysis"], {
        "iteration": 1, "stage": "test", "elapsed_hours": 0,
        "target_ppm": 7.0, "dataset_display_name": "TEST-RUN-001",
    })
    assert "TEST-RUN-001 Peak Review" in seen
    assert row["plot_title"] == "TEST-RUN-001 Peak Review"
    assert (paths.run_dir / row["plot_file"]).is_file()


# The tests below drive the real Services, NeedleController, Pump, journal, and
# recovery code through the mock transports that 01/02 --mock use. The first
# NMR measurement in a session runs production process_fid; later mocks of the
# byte-identical fixture reuse its tracked-window tables.

def mock_rig(tmp_path, *, fast=True, identity=None, single_stage=False):
    raw, arduino, pump, nmr = integrated.prepare(base.DEFAULT_CONFIG, tmp_path / "no_machine.yaml", ARDUINO_EXAMPLE, mock=True)
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    if single_stage:
        raw["workflow"]["first_addition_stage"] = None
        raw["workflow"]["repeat_addition_rounds"] = 0
    return integrated.open_services(raw, arduino, pump, nmr, identity=identity or integrated.RunIdentity("si6", True), fast_mock_processing=fast)


def journal_events(run_dir):
    return [json.loads(line) for line in (run_dir / "operation_journal.jsonl").read_text(encoding="utf-8").splitlines()]


def only_run_dir(tmp_path, root="runs_mock"):
    (run_dir,) = (tmp_path / root).iterdir()
    return run_dir


def per_cycle_actions(events):
    """Pump moves with their needle state, needle moves, and NMR, per cycle."""
    actions, cycle = {}, None
    for event in events:
        kind = event["event_type"]
        if kind == "cycle_status":
            cycle = event["cycle_number"]
        elif kind == "pump_needle_context" and cycle is not None:
            actions.setdefault(cycle, []).append(f"{event['operation_type']} {event['requested_volume_ml']:g} {event['needle_state']}")
        elif kind == "needle_transition" and event["result_classification"] == "completed" and cycle is not None:
            actions.setdefault(cycle, []).append(event["target"])
        elif kind == "nmr_retrieved" and cycle is not None:
            actions.setdefault(cycle, []).append("NMR")
    return actions


def events_after(events, predicate):
    index = next(i for i, event in enumerate(events) if predicate(event))
    return events[index + 1:]


# Task 2: the tracked resonance window.

def test_production_window_is_the_confirmed_resonance_window():
    raw = base.load_si6_config(base.DEFAULT_CONFIG)
    target, half = float(raw["nmr"]["target_ppm"]), float(raw["analysis"]["detection_window_ppm"])
    low, high = target - half, target + half
    assert low <= CONFIRMED_RESONANCE_PPM[0] and CONFIRMED_RESONANCE_PPM[1] <= high
    analysis_cfg = yaml.safe_load(NMR_ANALYSIS_CONFIG.read_text(encoding="utf-8"))
    assert [round(low, 6), round(high, 6)] == analysis_cfg["target_peak"]["search_window_ppm"]


def process_in_process(monkeypatch, titles):
    """Run production process_fid in this process so its axes titles are visible."""
    import matplotlib.axes
    sys.path.insert(0, str(repo_config.REPO_ROOT / "scripts" / "nmr"))
    import process_fid
    original_set = matplotlib.axes.Axes.set
    def capture(self, *args, **kwargs):
        if "title" in kwargs:
            titles.append(kwargs["title"])
        return original_set(self, *args, **kwargs)
    monkeypatch.setattr(matplotlib.axes.Axes, "set", capture)
    original = base.run_process_fid_postprocessing
    def run(dx_path, paths, name, **kwargs):
        runner = lambda command, cwd=None, check=False: SimpleNamespace(returncode=process_fid.main(command[3:]))
        return original(dx_path, paths, name, runner=runner, **kwargs)
    monkeypatch.setattr(base, "run_process_fid_postprocessing", run)


def test_representative_spectrum_is_measured_by_the_production_metric(tmp_path, monkeypatch):
    titles = []
    process_in_process(monkeypatch, titles)
    raw, _, _, nmr = integrated.prepare(base.DEFAULT_CONFIG, tmp_path / "no_machine.yaml", ARDUINO_EXAMPLE, mock=True)
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    paths, _, row = integrated.run_processing_only(raw, nmr, integrated.MOCK_NMR_FIXTURE, mock=False)
    assert row["peak_clear"] is True
    assert 5.70 <= row["peak_ppm"] <= 5.90
    assert row["snr"] >= float(raw["analysis"]["min_peak_snr"])
    # AGENTS.md: the manifest records the dataset identity and the visible title.
    manifest = json.loads(paths.manifest_json.read_text(encoding="utf-8"))
    assert paths.run_dir.name.endswith("_processing_only")
    assert manifest["run_kind"] == "processing_only" and manifest["dataset_display_name"] == paths.run_dir.name
    (figure,) = manifest["figures"]
    assert figure["visible_title"] == f"{paths.run_dir.name} {paths.raw_dir.joinpath(integrated.MOCK_NMR_FIXTURE.name).name}"
    assert figure["visible_title"] in titles
    assert (paths.run_dir / figure["file"]).is_file()


def test_spectrum_without_the_resonance_is_not_a_measurement(tmp_path):
    raw, _, _, nmr = integrated.prepare(base.DEFAULT_CONFIG, tmp_path / "no_machine.yaml", ARDUINO_EXAMPLE, mock=True)
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    with pytest.raises(base.AnalysisInconclusiveError, match="checks"):
        integrated.run_processing_only(raw, nmr, NO_RESONANCE_FIXTURE, mock=False)


# Task 3: failures after the sample was withdrawn.

def fail_acquisition(s):
    def acquire(*_args, **_kwargs):
        raise NmrRpcError("iFlow RPC timed out")
    s.acquire = acquire


def empty_retrieval(s):
    acquire = s.acquire
    def broken(nmr_cfg, save_dir, *, label):
        path = acquire(nmr_cfg, save_dir, label=label)
        path.write_bytes(b"")
        return path
    s.acquire = broken


def fail_processing(s):
    def process(*_args, **_kwargs):
        raise base.AnalysisInconclusiveError("process_fid.py failed with exit code 1")
    s.process = process


def strip_long_date(s):
    acquire = s.acquire
    def broken(nmr_cfg, save_dir, *, label):
        path = acquire(nmr_cfg, save_dir, label=label)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text("".join(line for line in lines if "LONG DATE" not in line), encoding="utf-8")
        return path
    s.acquire = broken


def no_product_peak(s):
    def acquire(nmr_cfg, save_dir, *, label):
        path = save_dir / f"{label}.dx"
        shutil.copyfile(NO_RESONANCE_FIXTURE, path)
        return path
    s.acquire = acquire


def fail_reaction_metric(s):
    def analyze(*_args, **_kwargs):
        raise NmrProcessingError("tracked-window table is unreadable")
    s.analyze = analyze


def fail_plateau(s):
    def plateau(*_args, **_kwargs):
        raise ValueError("plateau calculation failed")
    s.plateau_patch = plateau


@pytest.mark.parametrize("inject,failed_step", [
    (fail_acquisition, "NMR acquisition"),
    (empty_retrieval, "NMR data retrieval"),
    (fail_processing, "NMR processing"),
    (strip_long_date, "LONG DATE metadata"),
    (no_product_peak, "NMR analysis"),
    (fail_reaction_metric, "NMR analysis"),
    (fail_plateau, "plateau evaluation"),
])
def test_measurement_failure_with_known_state_cleans_up_then_stops_for_review(tmp_path, monkeypatch, inject, failed_step):
    with mock_rig(tmp_path) as s:
        up, _, _ = integrated.positions(s.arduino_cfg, mock=True)
        inject(s)
        if hasattr(s, "plateau_patch"):
            monkeypatch.setattr(base, "plateau_reached", s.plateau_patch)
        outcome = integrated.run_experiment(s, mock_cycles_per_stage=4)
        assert integrated.verify_needle(s.needle, up)["commanded_position_steps"] == str(up)
        assert s.state.retained_volume_ml == 0.0 and not s.state.uncertain
    assert outcome.status is base.TerminalStatus.ANALYSIS_INCONCLUSIVE and outcome.exit_code == 7
    run_dir = only_run_dir(tmp_path)
    events = journal_events(run_dir)
    (failed,) = [e for e in events if e["event_type"] == "measurement_failed"]
    assert failed["failed_step"] == failed_step and failed["counted_toward_plateau"] is False
    # The unchanged SOP cleanup follows the failure: return 13 mL while DOWN,
    # then UP, then the 5 mL exchange.
    retrieved = failed_step not in ("NMR acquisition", "NMR data retrieval")
    assert per_cycle_actions(events)[1] == ["withdraw 8 UP", "DOWN", "withdraw 5 DOWN", *(["NMR"] if retrieved else []), *CLEANUP_ACTIONS]
    statuses = [e["status"] for e in events if e["event_type"] == "cycle_status"]
    assert statuses == ["STARTED", "MEASUREMENT_FAILED", "CLEANUP_COMPLETE"]
    assert [e["result_classification"] for e in events if e["event_type"] == "recovery_cleanup"] == ["started", "completed"]
    assert "cycle_completed" not in [e["event_type"] for e in events]
    assert not any(e["event_type"] == "analysis_result" and e.get("result_classification") == "valid" for e in events)
    assert not any(e["event_type"] == "operator_checkpoint" and "diphenyl" in e["checkpoint"] for e in events)
    assert not (run_dir / "time_series.csv").exists()
    terminal = events[-1]
    assert terminal["event_type"] == "terminal" and terminal["physical_state_certainty"] == "certain"
    assert terminal["operator_review_required"] is True
    result = inspect_run(run_dir)
    assert result.classification is RecoveryClassification.TERMINAL_NONCOMPLETION
    assert result.replay.state.operator_review_required and not result.replay.state.manual_inspection_required
    assert result.replay.state.estimated_retained_syringe_volume_ml == 0.0


def unconfirmed_stop_during(s, operation):
    stop = s.pump.stop
    fired = []
    def maybe_silent(*args, **kwargs):
        if s.state.current_operation == operation and not fired:
            fired.append(operation)
            return ""
        return stop(*args, **kwargs)
    s.pump.stop = maybe_silent


def test_needle_fault_marks_position_uncertain_and_blocks_pump(tmp_path):
    with pytest.raises(PositionUncertainError):
        with mock_rig(tmp_path) as s:
            _, down, _ = integrated.positions(s.arduino_cfg, mock=True)
            move = s.needle.move_absolute
            def faulting_move(target, speed):
                if target == down:
                    s.needle.transport.scenario = "firmware_fault"
                return move(target, speed)
            s.needle.move_absolute = faulting_move
            integrated.run_experiment(s, mock_cycles_per_stage=1)
    run_dir = only_run_dir(tmp_path)
    events = journal_events(run_dir)
    failed = next(e for e in events if e["event_type"] == "cycle_status" and e["status"] == "FAILED")
    assert failed["failed_step"] == "needle DOWN"
    assert not any(e["event_type"] in ("pump_needle_context", "recovery_cleanup") for e in events_after(events, lambda e: e is failed))
    result = inspect_run(run_dir)
    assert result.classification is RecoveryClassification.PHYSICAL_STATE_UNCERTAIN
    assert result.replay.state.estimated_retained_syringe_volume_ml == 8.0


def test_uncertain_pump_state_stops_without_automatic_cleanup(tmp_path):
    with pytest.raises(base.PumpStateUncertainError):
        with mock_rig(tmp_path) as s:
            unconfirmed_stop_during(s, "withdraw 5 mL")
            integrated.run_experiment(s, mock_cycles_per_stage=1)
    run_dir = only_run_dir(tmp_path)
    events = journal_events(run_dir)
    failed = next(e for e in events if e["event_type"] == "cycle_status" and e["status"] == "FAILED")
    assert failed["failed_step"] == "sample withdraw"
    after = events_after(events, lambda e: e is failed)
    assert not any(e["event_type"] in ("needle_transition", "pump_needle_context", "recovery_cleanup") for e in after)
    assert inspect_run(run_dir).classification is RecoveryClassification.PHYSICAL_STATE_UNCERTAIN


def test_measurement_failure_with_needle_fault_refuses_automatic_cleanup(tmp_path):
    with pytest.raises(integrated.PhysicalStateUncertain):
        with mock_rig(tmp_path) as s:
            def analyze(*_args, **_kwargs):
                s.needle.transport.fault = "LIMIT_DOWN_ACTIVE"
                raise NmrProcessingError("analysis failed while the needle faulted")
            s.analyze = analyze
            integrated.run_experiment(s, mock_cycles_per_stage=1)
    run_dir = only_run_dir(tmp_path)
    events = journal_events(run_dir)
    assert [e["result_classification"] for e in events if e["event_type"] == "recovery_cleanup"] == ["not_attempted"]
    failed = next(e for e in events if e["event_type"] == "measurement_failed")
    assert not any(e["event_type"] in ("needle_transition", "pump_needle_context") for e in events_after(events, lambda e: e is failed))
    result = inspect_run(run_dir)
    assert result.classification is RecoveryClassification.PHYSICAL_STATE_UNCERTAIN
    assert result.replay.state.estimated_retained_syringe_volume_ml == 13.0


def test_failed_cleanup_requires_manual_inspection(tmp_path):
    with pytest.raises(base.PumpStateUncertainError):
        with mock_rig(tmp_path) as s:
            fail_reaction_metric(s)
            unconfirmed_stop_during(s, "infuse 13 mL")
            integrated.run_experiment(s, mock_cycles_per_stage=1)
    run_dir = only_run_dir(tmp_path)
    events = journal_events(run_dir)
    assert [e["result_classification"] for e in events if e["event_type"] == "recovery_cleanup"] == ["started", "failed"]
    failed = next(e for e in events if e["event_type"] == "cycle_status" and e["status"] == "FAILED")
    assert failed["failed_step"] == "return infusion while DOWN"
    assert not any(e["event_type"] == "needle_transition" for e in events_after(events, lambda e: e is failed))
    assert "manual_inspection_required" in [e["event_type"] for e in events]
    assert inspect_run(run_dir).classification is RecoveryClassification.PHYSICAL_STATE_UNCERTAIN


def interrupt_settle(s):
    def sleep(label, _seconds):
        if label == "NMR settle":
            raise KeyboardInterrupt
    s.sleep = sleep


def interrupt_acquisition(s):
    def acquire(*_args, **_kwargs):
        raise KeyboardInterrupt
    s.acquire = acquire


def journal_failure_during_analysis(s):
    def analyze(*_args, **_kwargs):
        raise JournalError("journal disk full")
    s.analyze = analyze


@pytest.mark.parametrize("inject,error", [
    (interrupt_settle, KeyboardInterrupt),
    (interrupt_acquisition, KeyboardInterrupt),
    (journal_failure_during_analysis, JournalError),
])
def test_abort_or_journal_failure_never_triggers_automatic_motion(tmp_path, inject, error):
    with pytest.raises(error):
        with mock_rig(tmp_path) as s:
            inject(s)
            integrated.run_experiment(s, mock_cycles_per_stage=1)
    run_dir = only_run_dir(tmp_path)
    events = journal_events(run_dir)
    failed = next(e for e in events if e["event_type"] == "cycle_status" and e["status"] == "FAILED")
    after = events_after(events, lambda e: e is failed)
    assert not any(e["event_type"] == "recovery_cleanup" for e in events)
    assert not any(e["event_type"] in ("pump_needle_context", "needle_transition", "cycle_completed") for e in after)
    result = inspect_run(run_dir)
    assert result.classification is RecoveryClassification.MANUAL_INSPECTION_REQUIRED
    assert result.replay.state.estimated_retained_syringe_volume_ml == 13.0


@pytest.mark.parametrize("condition", ["motion_active", "uncertain", "stop_unconfirmed"])
def test_needle_does_not_move_without_confirmed_pump_stop(tmp_path, condition):
    with mock_rig(tmp_path) as s:
        integrated.home_and_raise(s)
        sent = len(s.needle.transport.tx_log)
        if condition == "stop_unconfirmed":
            s.state.last_stop_status = base.StopStatus.UNCONFIRMED
        else:
            setattr(s.state, condition, True)
        with pytest.raises(integrated.VerificationError):
            s.move_needle("DOWN")
        assert not any("MOVE_ABS" in line for line in s.needle.transport.tx_log[sent:])


def test_pump_does_not_move_unless_needle_at_expected_position(tmp_path):
    with mock_rig(tmp_path) as s:
        integrated.home_and_raise(s)
        volumes = []
        s.pump.set_volume = lambda *args, **kwargs: volumes.append(args)
        with pytest.raises(integrated.VerificationError, match="commanded position"):
            s.pump_move("withdraw", 5.0, "DOWN", stage="initial", cycle=1)
        assert volumes == []
        assert s.state.retained_volume_ml == 0.0


@pytest.mark.parametrize("failure,error,message", [
    ("empty_retrieval", integrated.VerificationError, "nonempty"),
    ("no_processing_output", integrated.VerificationError, "process_fid"),
    ("no_long_date", integrated.VerificationError, "LONG DATE"),
    ("unclear_peak", base.AnalysisInconclusiveError, "checks"),
])
def test_nmr_failures_fail_closed_and_add_no_row(tmp_path, failure, error, message):
    rows = []
    with mock_rig(tmp_path) as s:
        acquire, analyze = s.acquire, s.analyze
        def broken_acquire(nmr_cfg, save_dir, *, label):
            path = acquire(nmr_cfg, save_dir, label=label)
            if failure == "empty_retrieval":
                path.write_bytes(b"")
            elif failure == "no_long_date":
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                path.write_text("".join(line for line in lines if "LONG DATE" not in line), encoding="utf-8")
            return path
        s.acquire = broken_acquire
        if failure == "no_processing_output":
            s.process = lambda _dx, paths, _name: paths.run_dir / "missing"
        if failure == "unclear_peak":
            def unclear(*args):
                row, spectrum = analyze(*args)
                return dict(row, peak_clear=False, qc_failure_reasons="snr below threshold"), spectrum
            s.analyze = unclear
        with pytest.raises(error, match=message):
            s.nmr_measurement(stage="diagnostic", cycle=1, rows=rows, started=datetime.now())
    assert rows == []


def test_live_never_reuses_test_down_position():
    motion = {"safe_up_position_steps": 100, "test_down_position_steps": 500, "sample_down_position_steps": None,
              "maximum_travel_steps": 1000, "maximum_speed_steps_s": 300}
    assert integrated.positions({"motion": motion}, mock=True) == (100, 500, 300)
    with pytest.raises(ValueError, match="Commissioned"):
        integrated.positions({"motion": motion}, mock=False)
    motion["sample_down_position_steps"] = 1200
    with pytest.raises(ValueError, match="maximum travel"):
        integrated.positions({"motion": motion}, mock=False)
    motion["sample_down_position_steps"] = 700
    assert integrated.positions({"motion": motion}, mock=False) == (100, 700, 300)


def test_full_mock_stage_follows_sop_and_completes_only_after_cleanup(tmp_path):
    with mock_rig(tmp_path, single_stage=True) as s:
        outcome = integrated.run_experiment(s, mock_cycles_per_stage=4)
        assert s.state.retained_volume_ml == 0.0 and not s.state.uncertain
    assert outcome.status is base.TerminalStatus.COMPLETED
    run_dir = only_run_dir(tmp_path)
    events = journal_events(run_dir)
    actions = per_cycle_actions(events)
    assert sorted(actions) == [1, 2, 3, 4]
    assert all(value == ["withdraw 8 UP", "DOWN", "withdraw 5 DOWN", "NMR", *CLEANUP_ACTIONS] for value in actions.values())
    for cycle in actions:
        statuses = [e for e in events if e.get("cycle_number") == cycle and e["event_type"] in ("cycle_status", "cycle_completed")]
        assert [e.get("status") for e in statuses] == ["STARTED", "NMR_COMPLETE", "CLEANUP_COMPLETE", "COMPLETE"]
    assert [e["scheduled_measurement_number"] for e in events if e["event_type"] == "plateau_detection"] == [4]
    result = inspect_run(run_dir)
    assert result.classification is RecoveryClassification.TERMINAL_COMPLETED
    assert not result.replay.state.manual_inspection_required and not result.replay.state.operator_review_required


# Task 4: stage duration, plateau, and operator decisions.

@pytest.mark.parametrize("interval,hours,immediate,expected", [
    (15, 2, False, 7), (60, 26, False, 25), (30, 26, False, 51), (15, 2, True, 8), (60, 1, False, 0),
])
def test_stage_duration_governs_measurement_count(interval, hours, immediate, expected):
    assert base.duration_measurement_slots(interval, hours, immediate) == expected


def test_explicit_measurement_cap_may_not_cut_a_stage_short(tmp_path):
    raw = base.load_si6_config(base.DEFAULT_CONFIG)
    raw["workflow"]["first_addition_stage"]["max_measurements"] = 6
    path = tmp_path / "capped.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="before max_hours 2 h"):
        integrated.prepare(path, tmp_path / "no_machine.yaml", ARDUINO_EXAMPLE, mock=True)
    raw["workflow"]["first_addition_stage"]["max_measurements"] = 7
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    loaded, *_ = integrated.prepare(path, tmp_path / "no_machine.yaml", ARDUINO_EXAMPLE, mock=True)
    assert base.build_stages(loaded["workflow"])[1].max_measurements == 7


class StepClock:
    def __init__(self):
        self.seconds = 0.0
    def monotonic(self):
        return self.seconds
    def wall_now(self):
        return datetime(2026, 9, 22, 12, 0, 0) + timedelta(seconds=self.seconds)
    def sleep(self, _label, seconds):
        self.seconds += max(0.0, seconds)


def test_plateau_on_a_measurement_ending_after_the_ceiling_still_completes_the_stage():
    clock = StepClock()
    stage = base.Stage("s", "prepare", interval_minutes=1.0, max_hours=0.05, measure_immediately=False, plateau_stopping_enabled=True, max_measurements=2, max_measurements_explicit=False)
    def measurement(schedule):
        second = schedule.scheduled_measurement_number == 2
        # The second measurement starts at 120 s and finishes past the 180 s ceiling.
        clock.seconds += 150.0 if second else 10.0
        return base.MeasurementObservation(True, second, "a", "b", "c")
    result = base.run_monitoring_stage(stage, measurement, monotonic_fn=clock.monotonic, wall_now_fn=clock.wall_now, sleep_fn=clock.sleep)
    assert result.outcome.stage_outcome is base.StageOutcome.PLATEAU_REACHED
    assert result.outcome.status is base.TerminalStatus.COMPLETED


def test_stage_limit_without_plateau_requires_a_decision_and_never_advances_itself(tmp_path):
    with mock_rig(tmp_path) as s:
        outcome = integrated.run_experiment(s, mock_cycles_per_stage=1)
        first_prompt = base.build_stages(s.raw["workflow"])[0].operator_prompt
    assert outcome.status is base.TerminalStatus.OPERATOR_ABORTED
    run_dir = only_run_dir(tmp_path)
    events = journal_events(run_dir)
    assert {e["checkpoint"] for e in events if e["event_type"] == "operator_checkpoint"} == {first_prompt}
    (limit,) = [e for e in events if e["event_type"] == "stage_limit_reached"]
    assert limit["plateau_reached"] is False and limit["result_classification"] == "plateau_not_reached"
    assert [e["result_classification"] for e in events if e["event_type"] == "stage_limit_decision"] == ["requested", "abort"]
    assert [e["event_type"] for e in events].count("cycle_completed") == 1
    result = inspect_run(run_dir)
    assert result.classification is RecoveryClassification.TERMINAL_NONCOMPLETION
    assert not result.replay.state.manual_inspection_required


def scripted(*choices):
    queue = list(choices)
    return lambda _stage, _outcome: queue.pop(0)


def test_operator_can_continue_a_stage_until_plateau(tmp_path):
    with mock_rig(tmp_path) as s:
        outcome = integrated.run_experiment(s, mock_cycles_per_stage=2, stage_decision=scripted("continue", "abort"))
    assert outcome.status is base.TerminalStatus.OPERATOR_ABORTED
    events = journal_events(only_run_dir(tmp_path))
    plateau = [e for e in events if e["event_type"] == "plateau_detection"]
    # Plateau needs four measurements; it arrives after the continued stage.
    assert [e["workflow_phase"] for e in plateau] == ["initial_reaction"]
    assert [e["result_classification"] for e in events if e["event_type"] == "stage_limit_decision"] == ["requested", "continue", "requested", "abort"]
    checkpoints = [e["checkpoint"] for e in events if e["event_type"] == "operator_checkpoint" and e["result_classification"] == "requested"]
    assert len(checkpoints) == 2 and "diphenyl" in checkpoints[1]
    assert [e["event_type"] for e in events].count("cycle_completed") == 6


def test_operator_can_advance_without_plateau(tmp_path):
    with mock_rig(tmp_path) as s:
        outcome = integrated.run_experiment(s, mock_cycles_per_stage=1, stage_decision=scripted(*["advance"] * 4))
        prompts = [stage.operator_prompt for stage in base.build_stages(s.raw["workflow"])]
    assert outcome.status is base.TerminalStatus.COMPLETED
    assert "advanced without a plateau" in outcome.message
    events = journal_events(only_run_dir(tmp_path))
    assert [e["checkpoint"] for e in events if e["event_type"] == "operator_checkpoint" and e["result_classification"] == "requested"] == prompts
    assert events[-1]["stages_advanced_without_plateau"] == ["initial_reaction", "first_diphenyl_silane", "round_1_acetone", "round_1_diphenyl_silane"]


def test_interactive_stage_decision_is_explicit():
    stage = base.Stage("s", "p", 15, 2, max_measurements=7)
    outcome = base.maximum_duration_outcome(stage)
    answers = iter(["maybe", "advance"])
    assert integrated.operator_stage_decision(stage, outcome, input_fn=lambda _p: next(answers), interactive=True) == "advance"
    assert integrated.operator_stage_decision(stage, outcome, input_fn=lambda _p: "CONTINUE", interactive=True) == "continue"
    assert integrated.operator_stage_decision(stage, outcome, interactive=False) == "abort"
    def eof(_prompt):
        raise EOFError
    assert integrated.operator_stage_decision(stage, outcome, input_fn=eof, interactive=True) == "abort"


def test_declined_reagent_checkpoint_ends_the_run_at_rest(tmp_path, monkeypatch):
    def decline(*_args, **_kwargs):
        raise base.OperatorAbortError("Operator did not confirm the required action")
    monkeypatch.setattr(base, "run_stage_sequence", decline)
    with mock_rig(tmp_path) as s:
        outcome = integrated.run_experiment(s)
    assert outcome.status is base.TerminalStatus.OPERATOR_ABORTED and outcome.exit_code == 3
    run_dir = only_run_dir(tmp_path)
    assert journal_events(run_dir)[-1]["physical_state_certainty"] == "certain"
    assert inspect_run(run_dir).classification is RecoveryClassification.TERMINAL_NONCOMPLETION


# Task 6: run identity, output folders, and the previous-run review gate.

def test_run_identity_names_mode_and_kind(tmp_path):
    labels = {
        ("si6", False, None): "si6_live", ("si6", True, None): "si6_mock",
        ("diagnostic", False, "all"): "diagnostic_all_live", ("diagnostic", True, "needle"): "diagnostic_needle_mock",
        ("processing_only", False, None): "processing_only", ("processing_only", True, None): "processing_only_mock",
    }
    for (kind, mock, selection), label in labels.items():
        assert integrated.RunIdentity(kind, mock, selection).label == label
    raw = {"output": {"run_root_dir": str(tmp_path / "results" / "runs" / "si6")}}
    assert integrated.run_root(raw, integrated.RunIdentity("si6", True)).name == "si6_mock"
    assert integrated.run_root(raw, integrated.RunIdentity("si6", False)).name == "si6"


def test_mock_run_folder_manifest_and_titles_identify_the_run(tmp_path):
    with mock_rig(tmp_path, fast=False, identity=integrated.RunIdentity("diagnostic", True, "nmr")) as s:
        integrated.run_diagnostic(s, "nmr")
        run_dir = s.paths.run_dir
    assert run_dir.parent.name == "runs_mock" and run_dir.name.endswith("_diagnostic_nmr_mock")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["run_kind"], manifest["mode"], manifest["diagnostic_selection"]) == ("diagnostic", "mock", "nmr")
    assert manifest["dataset_display_name"] == manifest["run_id"] == run_dir.name
    (figure,) = manifest["figures"]
    assert figure["visible_title"].startswith(f"{run_dir.name} ") and (run_dir / figure["file"]).is_file()
    first = journal_events(run_dir)[0]
    assert (first["mode"], first["run_kind"]) == ("mock", "diagnostic")


def write_run(root, name, mode, *events):
    run_dir = root / name
    run_dir.mkdir(parents=True)
    journal = OperationJournal(run_dir / "operation_journal.jsonl", name, software_version="test")
    journal.append("phase_transition", new_state="initializing", mode=mode, result_classification=mode)
    for event_type, fields in events:
        journal.append(event_type, **fields)
    return run_dir


def test_unresolved_live_run_blocks_the_next_live_run_until_acknowledged(tmp_path):
    root = tmp_path / "si6"
    raw = {"output": {"run_root_dir": str(root)}}
    write_run(root, "20260922_090000_si6_live", "live", ("cycle_status", {"status": "CLEANUP_COMPLETE"}), ("terminal", {"terminal_status": "completed", "physical_state_certainty": "certain"}))
    assert integrated.check_previous_run_review(raw, None) is None
    write_run(root, "20260922_100000_si6_live", "live", ("cycle_status", {"status": "STARTED"}), ("terminal", {"terminal_status": "failed", "physical_state_certainty": "requires_inspection"}))
    # A newer mock run never gates or hides a live one.
    write_run(root, "20260922_110000_si6_mock", "mock", ("cycle_status", {"status": "STARTED"}))
    with pytest.raises(LiveExecutionBlocked, match="--acknowledge-review 20260922_100000_si6_live"):
        integrated.check_previous_run_review(raw, None)
    assert integrated.check_previous_run_review(raw, "20260922_100000_si6_live").run_dir.name == "20260922_100000_si6_live"


def test_operator_review_after_measurement_failure_blocks_the_next_live_run(tmp_path):
    root = tmp_path / "si6"
    raw = {"output": {"run_root_dir": str(root)}}
    write_run(root, "20260922_120000_si6_live", "live", ("cycle_status", {"status": "CLEANUP_COMPLETE"}), ("terminal", {"terminal_status": "analysis_inconclusive", "physical_state_certainty": "certain", "operator_review_required": True}))
    with pytest.raises(LiveExecutionBlocked, match="operator review required"):
        integrated.check_previous_run_review(raw, None)
