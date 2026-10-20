"""Two monotonic stage windows, manual starting setup, one dose per new run.

Every launch uses pump loading manually restored to its established HOME and
returns the needle to the HOME established by the standalone Arduino script.
Neither official HOME is redefined. Launch starts automatically without prompts;
old dose records do not veto a new experiment.
"""
from __future__ import annotations

import argparse
import builtins
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

from arduino.python.config import load_arduino_config
from arduino.python.needle_state import NeedleState, NeedleStateStore, TrackedNeedle
from arduino.python.protocol import bool_field
from arduino.python.run_lock import PortProcessLock
from arduino.scripts._common import controller_session
from .. import config
from ..runtime_journal import JournalError, OperationJournal, RunRecorder, SnapshotWriteError, discover_git_commit
from ..runtime_state import replay_journal, write_json_atomic
from . import chemyx_manual as manual, chemyx_position as position, pump_channels
from . import si6_automated_nmr as base
from .dose_guard import DoseGuard
from .si6_fixed_time_nmr import acquire_bounded

DEFAULT_CONFIG = config.REPO_ROOT / "configs/experiments/si6_fixed_48h_6h.yaml"


def progress(*args, **kwargs):
    """A closed/unwritable terminal is an optional-output failure."""
    try:
        builtins.print(*args, **kwargs)
    except (OSError, ValueError):
        pass


class Clock:
    now = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class SimulatedClock:
    """No wall-clock wait; all scheduling and physical waits advance this clock."""
    def __init__(self):
        self.elapsed = 0.0

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        if seconds < 0 or not math.isfinite(seconds):
            raise ValueError("Invalid simulated wait")
        self.elapsed += seconds


def optional_event(recorder, event, **fields):
    """Stage metadata is useful, but only physical command evidence is a gate."""
    try:
        recorder.record(event, **fields)
    except Exception as exc:
        progress(f"Warning: optional {event} event failed: {type(exc).__name__}: {exc}")


class FixedTimeRecorder(RunRecorder):
    """Require the operation journal; tolerate its rebuildable run snapshot."""
    def record(self, event_type, **fields):
        sequence = self.journal.next_sequence
        try:
            return super().record(event_type, **fields)
        except JournalError as exc:
            cause = exc
            while cause.__cause__ is not None:
                cause = cause.__cause__
                if not isinstance(cause, Exception):
                    raise cause  # A user interruption remains an interruption.
            if not isinstance(exc, SnapshotWriteError):
                raise
            replay = replay_journal(self.journal.path)
            if (not replay.valid or not replay.records or
                    replay.records[-1].get("sequence") != sequence or
                    replay.records[-1].get("run_id") != self.run_id or
                    replay.records[-1].get("event_type") != event_type):
                raise  # Missing/corrupt physical evidence is never optional.
            progress(f"Warning: run_state.json projection unavailable; verified journal sequence {sequence} retained: {exc}")
            return replay.records[-1]


class OptionalCommandLog:
    """Driver print failures must not interrupt physical cleanup or scheduling."""
    def __init__(self, path):
        self.path, self.stream, self.console = path, None, sys.stdout

    def warning(self, exc):
        try:
            progress(f"Warning: optional instrument log {self.path} unavailable: {exc}", file=self.console)
        except Exception:
            pass

    def __enter__(self):
        try:
            self.stream = self.path.open("w", encoding="utf-8")
        except Exception as exc:
            self.warning(exc)
        return self

    def write(self, text):
        if self.stream is not None:
            try:
                return self.stream.write(text)
            except Exception as exc:
                self.warning(exc)
                failed, self.stream = self.stream, None
                try:
                    failed.close()
                except Exception:
                    pass
        return len(text)

    def flush(self):
        if self.stream is not None:
            try:
                self.stream.flush()
            except Exception as exc:
                self.warning(exc)

    def __exit__(self, *_):
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception as exc:
                self.warning(exc)
        return False


def positive(value, label, *, zero=False):
    return pump_channels.positive(value, label, zero=zero)


def load_settings(path=DEFAULT_CONFIG, machine_config=None, arduino_config=None):
    path = config.resolve_repo_path(path)
    raw = config.read_mapping_config(path, "fixed-time experiment")
    if set(raw) != {"machine_config", "arduino_config", "manual_config", "stages", "workflow", "pump", "nmr", "retrieval_wait_seconds", "output"}:
        raise ValueError("Fixed-time config requires only machine_config, arduino_config, manual_config, stages, workflow, pump, nmr, retrieval_wait_seconds, output")
    if len(raw["stages"]) != 2:
        raise ValueError("Exactly two timed stages are required")
    for index, stage in enumerate(raw["stages"], 1):
        if set(stage) != {"name", "hours", "interval_minutes"} or stage["name"] != f"stage_{index}":
            raise ValueError("Stages must be stage_1 and stage_2 with hours and interval_minutes")
        stage["hours"] = positive(stage["hours"], "stage hours")
        stage["interval_minutes"] = positive(stage["interval_minutes"], "stage interval")
        positive(stage["hours"] * 3600, "stage duration seconds")
        positive(stage["interval_minutes"] * 60, "stage interval seconds")
    workflow = raw["workflow"]
    if set(workflow) != {"initial_needle_position", "pump_extra_seconds", "cycle", "dose"} or workflow["initial_needle_position"] != "UP":
        raise ValueError("Use the existing UP-start sampling sequence and one boundary dose")
    workflow["pump_extra_seconds"] = positive(workflow["pump_extra_seconds"], "pump extra seconds", zero=True)
    # Reuse the production physical recipe; no operator actions or analysis.
    expected = ["withdraw", "needle", "withdraw", "pause", "nmr", "needle", "infuse", "withdraw", "infuse"]
    if [e["action"] for e in workflow["cycle"]] != expected:
        raise ValueError("Sampling order must remain withdraw / HOME / withdraw / settle / NMR / UP / return / wash withdraw / wash infuse")
    if [workflow["cycle"][i].get("position") for i in (1, 5)] != ["HOME", "UP"]:
        raise ValueError("Sampling needle positions must remain HOME then UP")
    if set(workflow["dose"]) != {"channel", "volume_ml", "rate_ml_min"} or workflow["dose"]["channel"] != 2:
        raise ValueError("The single transition dose must address Channel 2")
    machine = config.resolve_repo_path(machine_config or raw["machine_config"])
    arduino = load_arduino_config(config.resolve_repo_path(arduino_config or raw["arduino_config"]))
    if [arduino["needle"][k] for k in ("home_position", "down_position", "up_position")] != [0, 0, 1]:
        raise ValueError("Automated Arduino HOME=DOWN=0, UP=1 are required")
    if not arduino["needle"]["steps_per_unit"] or arduino["needle"]["up_step_sign"] not in (-1, 1):
        raise ValueError("Existing verified Arduino motion increment/direction required")
    definitions = pump_channels.channel_definitions(raw)
    if set(definitions) != {1, 2}:
        raise ValueError("Fixed-time sampling and dosing require Channel 1 and Channel 2")
    for ch, recipe in definitions.items():
        for key in ("syringe_diameter_mm", "syringe_capacity_ml", "initial_retained_volume_ml",
                    "syringe_safety_margin_ml", "rate_ml_min", "default_volume_ml"):
            recipe[key] = float(recipe[key])
    raw["pump"]["channels"] = {str(ch): recipe for ch, recipe in definitions.items()}
    pump_cfg, nmr = base.build_instrument_settings(raw, machine, acquisition_only=True)
    if nmr.route != "iflow" or nmr.result_type != "fid":
        raise ValueError("This preset uses the existing iFlow raw-FID acquisition protocol")
    for k in ("timeout", "poll_seconds", "max_wait_seconds"):
        positive(getattr(nmr, k), f"NMR {k}")
    raw["retrieval_wait_seconds"] = positive(raw["retrieval_wait_seconds"], "retrieval wait", zero=True)
    positive(nmr.max_wait_seconds + raw["retrieval_wait_seconds"] + nmr.timeout * 10 + 10,
             "bounded NMR worker timeout")
    settings = manual.load_manual_config(config.resolve_repo_path(raw["manual_config"]))
    settings = replace(settings, machine_config=machine, port=pump_cfg.port, baud_rate=pump_cfg.baud_rate,
                       timeout=pump_cfg.timeout, response_delay=pump_cfg.response_delay)
    for ch in (1, 2):
        cs, recipe = settings.channels[ch], definitions[ch]
        if (cs.diameter_mm != recipe["syringe_diameter_mm"] or cs.capacity_ml != recipe["syringe_capacity_ml"]
                or cs.syringe_volume_at_home_ml != recipe["initial_retained_volume_ml"]):
            raise ValueError(
                f"Channel {ch}: manual HOME geometry/loading must match the experiment; manual rates/volumes remain independent. "
                f"{settings.path} gives {cs.diameter_mm} mm / {cs.capacity_ml} mL / {cs.syringe_volume_at_home_ml} mL at HOME "
                f"(geometry from {cs.geometry_source}); {path} needs {recipe['syringe_diameter_mm']:g} mm / "
                f"{recipe['syringe_capacity_ml']:g} mL / {recipe['initial_retained_volume_ml']:g} mL loaded. Verify the installed "
                "syringe, correct the YAML that is wrong, then re-establish HOME with chemyx_control.py set-home if the HOME convention changed.")
    retained = {ch: d["initial_retained_volume_ml"] for ch, d in definitions.items()}
    for event in workflow["cycle"]:
        if event["action"] in {"withdraw", "infuse"}:
            pump_channels.validate_operation(raw, event, "sampling")
            if event["channel"] != 1:
                raise ValueError("Only Channel 1 samples")
            event["volume_ml"] = positive(event["volume_ml"], "sample volume")
            event["rate_ml_min"] = pump_channels.validate_rate(event["rate_ml_min"], 0, "sample rate")
            retained[1] += event["volume_ml"] * (1 if event["action"] == "withdraw" else -1)
            pump_channels.check_volume(1, retained[1], definitions[1])
        elif event["action"] == "pause":
            event["seconds"] = positive(event["seconds"], "settle", zero=True)
    if abs(retained[1] - definitions[1]["initial_retained_volume_ml"]) > 1e-9:
        raise ValueError("Sampling cleanup must restore Channel 1 loading")
    dose = workflow["dose"]
    dose["volume_ml"] = positive(dose["volume_ml"], "dose volume")
    dose["rate_ml_min"] = pump_channels.validate_rate(dose["rate_ml_min"], 0, "Channel 2 dose")
    pump_channels.check_volume(2, retained[2] - dose["volume_ml"], definitions[2])
    return raw, arduino, settings, nmr


def verify_needle(needle, target=None, cfg=None):
    status = needle.status()
    if (bool_field(status, "moving") or status.get("fault") != "NONE" or not bool_field(status, "position_valid")
            or target is not None and status.get("logical_position") != str(target)
            or needle.store.load() != needle.state):
        raise RuntimeError(f"Arduino motion/reference verification failed: {status}. "
                           "Use the standalone Arduino script to restore its saved reference if needed; "
                           "the experiment never redefines needle HOME.")
    return status


def record_prepared_pump_start(rig):
    """Record manually restored loading at existing HOME; never set a new HOME."""
    rig.recorder.record("manual_pump_return_home_intent",
        channel_loading_ml={str(ch): rig.settings.channels[ch].syringe_volume_at_home_ml for ch in (1, 2)},
        reference_basis="operator_prepared_existing_home_before_launch", reference_redefined=False,
        physical_position_measured=False)
    for ch in (1, 2):
        rig.ledger.record_manual_return_home(
            rig.settings.channels[ch].identity(rig.settings.pump_identity(rig.mock)), run_id=rig.recorder.run_id)
    rig.recorder.record("manual_pump_return_home_recorded", reference_redefined=False,
        physical_position_measured=False, physical_motion_commanded=False,
        previous_run_reconciliation_required=False)


class Rig:
    def __init__(self, raw, arduino, settings, needle, pump, ledger, recorder, guard, clock, mock, output):
        self.raw, self.arduino, self.settings = raw, arduino, settings
        self.needle, self.pump, self.ledger = needle, pump, ledger
        self.recorder, self.guard, self.clock = recorder, guard, clock
        self.mock, self.output = mock, output

    def idle(self):
        for ch in (1, 2):
            manual.confirm_idle(self.pump, ch, self.recorder, "fixed_time")

    def needle_to(self, target):
        self.idle()
        self.recorder.record("needle_requested", target=target, physical_state_effect=True)
        self.needle.move_to(target)
        verify_needle(self.needle, target, self.arduino)
        self.recorder.record("needle_completed", target=target, physical_state_effect=True)

    def move(self, event):
        self.idle()
        verify_needle(self.needle, cfg=self.arduino)
        ch = event["channel"]
        cs = replace(self.settings.channels[ch], rate_ml_min=event["rate_ml_min"])
        plan = position.plan_transfer(self.ledger.state(ch), cs.identity(self.settings.pump_identity(self.mock)),
                                      event["action"], event["volume_ml"])
        # Same metered driver and shared position tracker as manual/production.
        cfg = self.settings.pump_config(cs, plan.volume_ml)
        self.pump.select_channel(ch)
        try:
            base.configure_pump(self.pump, cfg)
            tracker = self.ledger.tracker(channel=ch, pump=self.settings.pump_identity(self.mock),
                diameter_mm=cs.diameter_mm, capacity_ml=cs.capacity_ml, source="SI6 fixed-time", run_id=self.recorder.run_id)
            before = cs.syringe_volume_at_home_ml + position.to_ml(self.ledger.state(ch)["current_displacement_nl"])
            state = base.PumpSafetyState(channel=ch, retained_volume_ml=before)
            base.run_safe_metered_move(self.pump, cfg, plan.direction, plan.volume_ml, state,
                extra_seconds=self.raw["workflow"]["pump_extra_seconds"],
                sleep_fn=lambda _label, seconds: self.clock.sleep(seconds), recorder=self.recorder,
                workflow_phase="fixed_time", position_tracker=tracker)
        finally:
            self.pump.select_channel(1)
        after = self.ledger.state(ch)
        if not after["position_known"] or after["current_displacement_nl"] != plan.displacement_after_nl:
            raise RuntimeError(f"Channel {ch}: movement ledger completion is uncertain; {after}")

    def sample(self, acquisition):
        verify_needle(self.needle, 1, self.arduino)
        initial = self.ledger.state(1)["current_displacement_nl"]
        result = None
        for event in self.raw["workflow"]["cycle"]:
            action = event["action"]
            if action in {"withdraw", "infuse"}:
                self.move(event)
            elif action == "needle":
                self.needle_to(0 if event["position"] == "HOME" else 1)
            elif action == "pause":
                self.clock.sleep(event["seconds"])
            else:
                # NMR has no authority over pump/needle completion evidence.
                try:
                    result = acquisition()
                    if not isinstance(result, dict) or result.get("status") not in {"NMR_SAVED", "NMR_MISSING", "NMR_FAILED"}:
                        raise ValueError(f"Invalid acquisition result: {result!r}")
                except Exception as exc:
                    result = {"status": "NMR_FAILED", "path": None, "error": f"{type(exc).__name__}: {exc}"}
                # Preserve the data failure even if subsequent physical cleanup
                # must stop. This diagnostic event never authorizes motion.
                optional_event(self.recorder, "nmr_observation", **result)
                # A stationary NMR acquisition controls no fluidic actuator.
                # Even if its START reply is lost, verified fluidic cleanup is
                # safe; future NMR STARTs still require positive NMR idle.
                self.idle()
                verify_needle(self.needle, 0, self.arduino)
        self.idle()
        verify_needle(self.needle, 1, self.arduino)
        if self.ledger.state(1)["current_displacement_nl"] != initial:
            raise RuntimeError("Sampling cleanup did not restore the Channel 1 displacement")
        return result

    def dose(self):
        self.needle_to(0)
        event = dict(self.raw["workflow"]["dose"], action="infuse")
        # Existing exclusive dose guard: no automatic replay after this point.
        self.guard.mark("DISPATCH_INTENT", channel=2, volume_ml=event["volume_ml"], rate_ml_min=event["rate_ml_min"])
        intent = self.recorder.record("dose_dispatch_intent", dose_id=self.guard.data["dose_id"], channel=2,
                                      experiment_id=self.guard.data["experiment_id"], configuration_sha256=self.guard.data["configuration_sha256"],
                                      volume_ml=event["volume_ml"], rate_ml_min=event["rate_ml_min"])
        self.move(event)
        replay = replay_journal(self.recorder.journal.path)
        completed = [r for r in replay.records if r["sequence"] > intent["sequence"] and r.get("channel") == 2
                     and r.get("operation_type") == "infuse" and r.get("lifecycle_state") == "completed"]
        if not replay.valid or len(completed) != 1:
            raise RuntimeError("Channel 2 dose has no single matched durable completion; automatic repeat refused")
        evidence = self.recorder.record("dose_confirmed", channel=2, dose_id=self.guard.data["dose_id"],
            experiment_id=self.guard.data["experiment_id"], configuration_sha256=self.guard.data["configuration_sha256"],
            pump_operation_id=completed[0]["operation_id"], pump_completion_sequence=completed[0]["sequence"],
            volume_ml=event["volume_ml"], rate_ml_min=event["rate_ml_min"])
        self.guard.mark("CONFIRMED", completion_journal_sequence=evidence["sequence"],
            retained_volume_ml=self.settings.channels[2].syringe_volume_at_home_ml - event["volume_ml"])
        self.needle_to(1)

    def stop(self):
        # Independent best effort: neither logging nor one device blocks STOP
        # attempts on the other devices. No automatic reverse/cleanup on abort.
        failures = []
        for ch in (1, 2):
            try:
                base.attempt_emergency_stop(self.pump, base.PumpSafetyState(channel=ch))
            except BaseException as exc:
                failures.append(f"Channel {ch} emergency STOP attempt failed: {type(exc).__name__}: {exc}")
        try:
            self.needle.stop_best_effort()
        except BaseException as exc:
            failures.append(f"Arduino emergency STOP/state persistence failed: {type(exc).__name__}: {exc}")
        for message in failures:
            try:
                progress(message)
            except Exception:
                pass


def run_stage(stage, rig, acquisition, observe):
    duration, interval = stage["hours"] * 3600.0, stage["interval_minutes"] * 60.0
    start = rig.clock.now()
    deadline = start + duration
    optional_event(rig.recorder, "stage_started", stage=stage["name"], duration_seconds=duration,
                        monotonic_start=start, monotonic_deadline=deadline)
    index = 0
    while True:
        now = rig.clock.now()
        if now >= deadline:
            break
        due = start + index * interval
        if now < due:
            rig.clock.sleep(min(due, deadline) - now)
            continue
        # This admission check covers a boundary reached during scheduling I/O.
        if rig.clock.now() >= deadline:
            break
        elapsed = rig.clock.now() - start
        result = rig.sample(lambda: acquisition(stage["name"], index + 1))
        try:
            observe(dict(result, stage=stage["name"], observation=index + 1,
                         scheduled_seconds=index * interval, started_elapsed_seconds=elapsed,
                         completed_elapsed_seconds=rig.clock.now() - start, physical_cleanup="COMPLETE"))
        except Exception as exc:
            progress(f"Warning: optional observation export failed: {type(exc).__name__}: {exc}")
        # No catch-up bursts. Missed slots do not reset either stage clock.
        next_index = max(index + 1, math.ceil((rig.clock.now() - start) / interval))
        index = next_index
    elapsed = rig.clock.now() - start
    optional_event(rig.recorder, "stage_completed", stage=stage["name"], elapsed_seconds=elapsed,
                        monotonic_deadline=deadline)
    return {"stage": stage["name"], "elapsed_seconds": elapsed, "duration_seconds": duration}


def new_identity():
    return "SI6_FIXED_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:12]


def run(raw, arduino, settings, nmr, *, mock, output, clock=None, acquisition=None, experiment_id=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    raw_dir = output / "nmr_raw"
    clock = clock or (SimulatedClock() if mock else Clock())
    report = {"status": "INCOMPLETE", "mode": "MOCK" if mock else "LIVE", "results": str(output),
              "stages": [], "observations": [], "dose_confirmed": False, "physical_cleanup": False,
              "analysis_performed": False}
    experiment_id = experiment_id or new_identity()
    report["experiment_id"] = experiment_id
    recorder = FixedTimeRecorder(OperationJournal(output / "operation_journal.jsonl", experiment_id,
        monotonic_fn=clock.now, software_version=discover_git_commit(config.REPO_ROOT)), output / "run_state.json")
    # Dose evidence belongs to this fresh output folder. No global prior-dose
    # scan or reconciliation can block a manually prepared new experiment.
    settings = replace(settings, dose_ledger_dir=output / ("mock_state" if mock else "state") / "si6_doses")
    guard = DoseGuard(settings.dose_ledger_dir, experiment_id, raw, experiment_id)
    report["dose_record"] = str(guard.path)
    report["restart_policy"] = "new_experiment_from_manual_setup"
    report["needle_home_policy"] = "return_to_saved_arduino_home"
    report["pump_home_policy"] = "manual_return_to_existing_chemyx_home"
    report["interactive_prompts"] = False
    progress("SI6 FIXED-TIME EXPERIMENT" + (" [MOCK: no hardware]" if mock else ""))
    progress(f"Arduino: {arduino['arduino']['port']}; Chemyx: {settings.port}; NMR: {nmr.host}:{nmr.port}; {nmr.scans} scans")
    for stage in raw["stages"]:
        progress(f"{stage['name']}: {stage['hours']:g} hours; interval {stage['interval_minutes']:g} minutes")
    dose = raw["workflow"]["dose"]
    progress(f"Channel 2: loaded {settings.channels[2].syringe_volume_at_home_ml:.2f} mL; infuse {dose['volume_ml']:.2f} mL once at {dose['rate_ml_min']:.2f} mL/min")
    progress(f"Results: {output}")

    def optional_write(path, payload):
        try:
            write_json_atomic(path, payload)
        except Exception as exc:
            progress(f"Warning: optional output {path} failed: {type(exc).__name__}: {exc}")

    optional_write(output / "configuration.json", raw)

    def observe(row):
        report["observations"].append(row)
        try:
            with (output / "observations.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, default=str) + "\n")
        except Exception as exc:
            progress(f"Warning: observation export failed: {exc}; observation retained in run report")
        progress(f"{row['stage'].upper()} | observation {row['observation']} | elapsed {row['started_elapsed_seconds']/3600:.2f} h | Arduino/Chemyx/cleanup: OK | {row['status']}")
        if row.get("error"):
            progress(f"Warning: {row['error']}\nExperiment continuing on the original schedule.")
        optional_write(output / "report.json", report)

    if acquisition is None:
        def acquisition(stage, observation):
            path = raw_dir / f"{stage}_{observation:04d}.dx"
            if mock:
                # Clearly marked fixture, never generated or used after a failure.
                clock.sleep(30)
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx", path)
                return {"status": "NMR_SAVED", "path": str(path), "simulated": True}
            return acquire_bounded(nmr, path, raw["retrieval_wait_seconds"])
    rig = None
    try:
        with ExitStack() as stack:
            root = output / "mock_state/chemyx_state" if mock else position.state_root(False)
            ledger = stack.enter_context(position.PositionLedger(root, owner=f"fixed-time {experiment_id}"))
            # Also serialize NMR-only users of this new workflow across pump ports.
            if not mock:
                try:
                    stack.enter_context(PortProcessLock(f"fixed-nmr:{nmr.host}:{nmr.port}"))
                except Exception as exc:
                    nmr_lock_error = f"NMR ownership unavailable; no STARTs will be sent by this run: {exc}"
                    progress(f"Warning: {nmr_lock_error}; timed fluidics will continue")
                    def acquisition(*_):
                        return {"status": "NMR_FAILED", "path": None, "requested": False, "error": nmr_lock_error}
            log = stack.enter_context(OptionalCommandLog(output / "instrument_commands.log"))
            with redirect_stdout(log):
                needle_path = output / "mock_state/needle_state.json" if mock else Path(arduino["needle"]["state_path"])
                cfg = deepcopy(arduino)
                cfg["firmware"]["motion_enabled"] = True
                controller = stack.enter_context(controller_session(cfg, "mock" if mock else "live", allow_motion=True, apply_runtime_config=True))
                # Long-lived session; serial commands and firmware motion retain
                # their individual deadlines/watchdog. Bench session age is not
                # a legitimate 54-hour experiment stop condition.
                controller.overall_timeout_s = float("inf")
                pump = stack.enter_context(manual.pump_session(settings, mock))
                # Until initialization completes, the raw controller provides
                # STOP on an error; no reference-based motion is dispatched.
                rig = Rig(raw, cfg, settings, controller, pump, ledger, recorder, guard, clock, mock, output)
                try:
                    rig.idle()
                    status = controller.status()
                    if bool_field(status, "moving") or status.get("fault") != "NONE":
                        raise RuntimeError(f"Arduino is moving or faulted at startup: {status}")
                    if mock:
                        # Simulation fixture only. Never replace a live HOME.
                        for ch in (1, 2):
                            ledger.set_home(settings.channels[ch].identity(settings.pump_identity(True)),
                                receipt_id="simulation_fixture", established_by="mock manual setup")
                        NeedleStateStore(needle_path).save(NeedleState(logical_position=0, position_valid=True,
                            reason="operator_confirmed_home", last_updated=datetime.now(timezone.utc).isoformat(),
                            steps_per_unit=cfg["needle"]["steps_per_unit"], up_step_sign=cfg["needle"]["up_step_sign"]))
                    needle = TrackedNeedle(controller, cfg, state_path=needle_path)
                    rig.needle = needle
                    verify_needle(needle, cfg=cfg)
                    record_prepared_pump_start(rig)
                    guard.reserve()
                    recorder.record("experiment_reserved", **guard.data, ledger_path=str(guard.path))
                    from_position = needle.state.logical_position
                    recorder.record("startup_needle_home_requested", from_position=from_position, target=0,
                        state_path=str(needle_path), reference_basis="saved_standalone_arduino_home",
                        reference_redefined=False, physical_state_effect=True)
                    rig.needle_to(0)
                    recorder.record("startup_needle_home_completed", from_position=from_position, target=0,
                        movement_required=from_position != 0, reference_redefined=False, physical_state_effect=True)
                    rig.needle_to(1)
                except BaseException:
                    rig.stop()
                    raise
            try:
                # Physical driver chatter goes to file; progress remains short.
                def sample_logged(fn):
                    with redirect_stdout(log):
                        return original_sample(fn)
                original_sample = rig.sample
                rig.sample = sample_logged
                report["stages"].append(run_stage(raw["stages"][0], rig, acquisition, observe))
                progress("STAGE 1 COMPLETE; Channel 2 dose starting")
                with redirect_stdout(log):
                    rig.dose()
                report["dose_confirmed"] = True
                progress(f"Channel 2: {dose['volume_ml']:.2f} mL at {dose['rate_ml_min']:.2f} mL/min CONFIRMED; STARTING STAGE 2")
                report["stages"].append(run_stage(raw["stages"][1], rig, acquisition, observe))
                with redirect_stdout(log):
                    rig.idle()
                    verify_needle(needle, 1, cfg)
                report["physical_cleanup"] = True
                optional_event(recorder, "fixed_time_complete", physical_cleanup=True, dose_confirmed=True)
                report["status"] = "COMPLETE"
            except BaseException:
                rig.stop()  # While both transports and their locks are still open.
                raise
    except BaseException as exc:
        if report["status"] == "COMPLETE":
            # Only closing transports/locks remained: every physical step, the
            # dose and final verification are already durable. Not a failed run.
            report["teardown_warning"] = f"{type(exc).__name__}: {exc}"
            progress(f"Warning: experiment complete, but closing an instrument connection or lock failed: {report['teardown_warning']}")
        else:
            report.update(status="INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "FAILED",
                          error=f"{type(exc).__name__}: {exc}")
            progress(f"EXPERIMENT {report['status']}: {report['error']}")
            progress("For another new fixed-time run, manually restore the stated starting loading, "
                     "at the existing pump HOME, and fluid paths, then launch using the saved Arduino HOME. "
                     "Old dose records require no reconciliation. An uncertain needle reference must "
                     "be restored with the standalone Arduino script; this run never sets a new HOME.")
    finally:
        report["dose_confirmed"] = guard.data["status"] == "CONFIRMED"
        if guard.path.exists():
            optional_write(output / "channel2_dose.json", dict(guard.data, canonical_ledger_path=str(guard.path)))
        optional_write(output / "report.json", report)
    saved = sum(r["status"] == "NMR_SAVED" for r in report["observations"])
    progress(f"EXPERIMENT {report['status']} | NMR saved: {saved}; missing/failed: {len(report['observations'])-saved}; dose confirmed: {report['dose_confirmed']}; physical cleanup complete: {report['physical_cleanup']}")
    progress(f"Results: {output}")
    return report


def nmr_check(raw, nmr, *, output=None, input_fn=input):
    """One live acquisition through this experiment's own NMR worker and settings.

    No pump, needle, position ledger or dose record is opened and no spectrum is
    processed. Run it on WORK before the long experiment: the 2-scan checkout
    uses the legacy acquisition path, not this worker.
    """
    output = Path(output or config.resolve_repo_path(raw["output"]["run_root_dir"]) / "nmr_check" / new_identity())
    progress(f"NMR CHECK {nmr.host}:{nmr.port}: one {nmr.scans}-scan acquisition through the fixed-time worker; "
             "no pump, needle or dose record is used")
    if input_fn("Confirm the NMR is idle and no other program is using it [y/N]: ").strip().lower() not in {"y", "yes"}:
        progress("NMR check declined; no NMR command was sent")
        return 1
    output.mkdir(parents=True, exist_ok=False)
    try:
        # The same ownership a live experiment holds: never overlaps a running one.
        with PortProcessLock(f"fixed-nmr:{nmr.host}:{nmr.port}"):
            result = acquire_bounded(nmr, output / "nmr_raw/nmr_check_0001.dx", raw["retrieval_wait_seconds"])
    except Exception as exc:
        result = {"status": "NMR_FAILED", "path": None, "requested": False,
                  "error": f"NMR ownership unavailable (is an experiment running?): {type(exc).__name__}: {exc}"}
    try:
        write_json_atomic(output / "nmr_check.json", result)
    except Exception as exc:
        progress(f"Warning: {output / 'nmr_check.json'} not written: {exc}")
    progress(f"NMR CHECK {result['status']}" + (f": {result['path']}" if result.get("path") else "")
             + (f" | {result['error']}" if result.get("error") else ""))
    progress(f"Status before START: {result.get('before')!r}")
    progress(f"Last status: {result.get('final')!r}")
    progress(f"Evidence: {output}")
    return 0 if result["status"] == "NMR_SAVED" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--machine-config", type=Path)
    parser.add_argument("--arduino-config", type=Path)
    parser.add_argument("--output-dir", type=Path, help="New directory only; existing results are never overwritten")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--live", action="store_true",
                       help="Start immediately without prompts; prepare the rig at its established HOME/loading first")
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--validate-config", action="store_true")
    modes.add_argument("--nmr-check", action="store_true",
                       help="Live: one acquisition through this experiment's NMR worker; no pump, needle or dose use")
    args = parser.parse_args(argv)
    try:
        raw, arduino, settings, nmr = load_settings(args.config, args.machine_config, args.arduino_config)
        if args.validate_config:
            progress(f"Fixed-time configuration valid; no hardware contacted; {nmr.scans} scans")
            return 0
        if args.nmr_check:
            return nmr_check(raw, nmr, output=args.output_dir)
        identity = new_identity()
        root = config.resolve_repo_path(raw["output"]["run_root_dir"])
        output = args.output_dir or root / ("mock" if args.mock else "live") / identity
        report = run(raw, arduino, settings, nmr, mock=args.mock, output=output, experiment_id=identity)
        return 0 if report["status"] == "COMPLETE" else 1
    except Exception as exc:
        progress(f"Fixed-time startup failed: {type(exc).__name__}: {exc}")
        return 1
