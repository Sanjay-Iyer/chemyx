"""Independent Stage 2 ONLY runner: initial NMR, then every 30 minutes to 6 hours.

Phase 1 and reagent addition have already been handled by the operator. This
runner has no dosing path or dose reservation. It reuses the proven sampling
and instrument primitives without changing the two-stage experiment controller.
Launching --live starts immediately, using the instruments' existing HOMEs.
Saved measurements are processed by an optional background review process;
analysis never controls the instrument schedule.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from arduino.python.config import load_arduino_config
from arduino.python.needle_state import NeedleState, NeedleStateStore, TrackedNeedle
from arduino.python.protocol import bool_field
from arduino.python.run_lock import PortProcessLock
from arduino.scripts._common import controller_session
from .. import config
from ..runtime_journal import OperationJournal, discover_git_commit
from ..runtime_state import write_json_atomic
from . import chemyx_manual as manual, chemyx_position as position, pump_channels
from . import si6_fixed_time as fixed
from .si6_fixed_time_nmr import acquire_bounded

DEFAULT_CONFIG = config.REPO_ROOT / "configs/experiments/si6_stage2only_6h.yaml"


def load_settings(path=DEFAULT_CONFIG, machine_config=None, arduino_config=None):
    path = config.resolve_repo_path(path)
    raw = config.read_mapping_config(path, "stage2only experiment")
    if set(raw) != {"machine_config", "arduino_config", "manual_config", "stage", "workflow",
                    "pump", "nmr", "retrieval_wait_seconds", "output"}:
        raise ValueError("stage2only requires its own single-stage config; two-stage/dose settings are not accepted")
    stage = raw["stage"]
    if set(stage) != {"name", "hours", "interval_minutes", "include_endpoint"} or stage["name"] != "stage_2" or stage["include_endpoint"] is not True:
        raise ValueError("stage2only requires stage_2 and include_endpoint: true")
    stage["hours"] = fixed.positive(stage["hours"], "stage2only hours")
    stage["interval_minutes"] = fixed.positive(stage["interval_minutes"], "stage2only interval")
    duration = fixed.positive(stage["hours"] * 3600, "stage2only duration seconds")
    interval = fixed.positive(stage["interval_minutes"] * 60, "stage2only interval seconds")
    slots = duration / interval
    if not math.isfinite(slots) or slots < 1 or not math.isclose(slots, round(slots), rel_tol=0, abs_tol=1e-9):
        raise ValueError("stage2only duration must contain a whole number of sampling intervals")
    workflow = raw["workflow"]
    if set(workflow) != {"initial_needle_position", "pump_extra_seconds", "cycle"} or workflow["initial_needle_position"] != "UP":
        raise ValueError("stage2only accepts the UP-start sampling cycle only; no dose or Stage 1 actions")
    workflow["pump_extra_seconds"] = fixed.positive(workflow["pump_extra_seconds"], "pump extra seconds", zero=True)
    expected = ["withdraw", "needle", "withdraw", "pause", "nmr", "needle", "infuse", "withdraw", "infuse"]
    if [e["action"] for e in workflow["cycle"]] != expected or [workflow["cycle"][i].get("position") for i in (1, 5)] != ["HOME", "UP"]:
        raise ValueError("stage2only must retain the existing sampling / NMR / return / wash sequence")
    pump_channels.validate_stage_actions(raw, workflow["cycle"], "stage2only sampling")
    definitions = pump_channels.channel_definitions(raw)
    if set(definitions) != {1} or pump_channels.default_channel(raw) != 1:
        raise ValueError("stage2only configures sampling Channel 1 only; Channel 2 transfer is forbidden")
    recipe = definitions[1]
    if config.resolve_units(recipe["units"]) != 0 or float(recipe["initial_retained_volume_ml"]) != 0:
        raise ValueError("stage2only uses mL/min and Channel 1 manually restored to empty HOME")
    retained = 0.0
    for event in workflow["cycle"]:
        if event["action"] in {"withdraw", "infuse"}:
            if event.get("channel") != 1:
                raise ValueError("stage2only may transfer liquid on Channel 1 only")
            event["volume_ml"] = fixed.positive(event["volume_ml"], "sample volume")
            event["rate_ml_min"] = pump_channels.validate_rate(event["rate_ml_min"], 0, "sample rate")
            retained += event["volume_ml"] * (1 if event["action"] == "withdraw" else -1)
            pump_channels.check_volume(1, retained, recipe)
        elif event["action"] == "pause":
            event["seconds"] = fixed.positive(event["seconds"], "settle", zero=True)
    if abs(retained) > 1e-9:
        raise ValueError("stage2only sampling must return Channel 1 to its starting loading")
    machine = config.resolve_repo_path(machine_config or raw["machine_config"])
    arduino = load_arduino_config(config.resolve_repo_path(arduino_config or raw["arduino_config"]))
    if [arduino["needle"][k] for k in ("home_position", "down_position", "up_position")] != [0, 0, 1]:
        raise ValueError("stage2only requires existing Arduino HOME=DOWN=0, UP=1")
    if not arduino["needle"]["steps_per_unit"] or arduino["needle"]["up_step_sign"] not in (-1, 1):
        raise ValueError("Verified Arduino motion increment/direction required")
    pump_cfg, nmr = fixed.base.build_instrument_settings(raw, machine, acquisition_only=True)
    if nmr.route != "iflow" or nmr.result_type != "fid":
        raise ValueError("stage2only uses iFlow raw-FID acquisition")
    for key in ("timeout", "poll_seconds", "max_wait_seconds"):
        fixed.positive(getattr(nmr, key), f"NMR {key}")
    raw["retrieval_wait_seconds"] = fixed.positive(raw["retrieval_wait_seconds"], "retrieval wait", zero=True)
    fixed.positive(nmr.max_wait_seconds + raw["retrieval_wait_seconds"] + nmr.timeout * 10 + 10, "bounded NMR timeout")
    settings = manual.load_manual_config(config.resolve_repo_path(raw["manual_config"]))
    settings = replace(settings, machine_config=machine, port=pump_cfg.port, baud_rate=pump_cfg.baud_rate,
                       timeout=pump_cfg.timeout, response_delay=pump_cfg.response_delay)
    cs = settings.channels[1]
    if (cs.diameter_mm != float(recipe["syringe_diameter_mm"]) or cs.capacity_ml != float(recipe["syringe_capacity_ml"])
            or cs.syringe_volume_at_home_ml != 0):
        raise ValueError(f"Channel 1 manual HOME geometry/loading in {settings.path} must match {path}; HOME is never redefined")
    if set(raw["output"]) != {"run_root_dir"} or not isinstance(raw["output"]["run_root_dir"], str) or not raw["output"]["run_root_dir"].strip():
        raise ValueError("stage2only output.run_root_dir must be a nonempty path")
    return raw, arduino, settings, nmr


class Stage2OnlyRig(fixed.Rig):
    """Reuse sampling while making reagent addition unavailable in this runner."""
    def move(self, event):
        if event.get("channel") != 1:
            raise RuntimeError("stage2only forbids Channel 2 transfers")
        return super().move(event)

    def dose(self):
        raise RuntimeError("stage2only never delivers a reagent dose")


def run_stage2only(stage, rig, acquisition, observe):
    """One clock; include the final 6-hour sample and finish its cleanup.

    A slow cycle skips expired intermediate slots without catch-up bursts.
    The endpoint is always sampled once, as soon as earlier cleanup permits.
    """
    duration, interval = stage["hours"] * 3600.0, stage["interval_minutes"] * 60.0
    last_index = round(duration / interval)
    start = rig.clock.now()
    fixed.optional_event(rig.recorder, "stage_started", stage="stage_2", duration_seconds=duration,
                         monotonic_start=start, monotonic_deadline=start + duration, include_endpoint=True)
    index, skipped = 0, 0
    while index <= last_index:
        due = start + index * interval
        now = rig.clock.now()
        if now < due:
            rig.clock.sleep(due - now)
            continue  # Recheck the same monotonic due time if sleep returned early.
        elapsed = rig.clock.now() - start
        result = rig.sample(lambda: acquisition("stage_2", index + 1))
        try:
            observe(dict(result, stage="stage_2", observation=index + 1, scheduled_seconds=index * interval,
                         started_elapsed_seconds=elapsed, completed_elapsed_seconds=rig.clock.now() - start,
                         endpoint=index == last_index, physical_cleanup="COMPLETE"))
        except Exception as exc:
            fixed.progress(f"Warning: optional observation export failed: {exc}")
        if index == last_index:
            break
        next_index = min(last_index, max(index + 1, math.ceil((rig.clock.now() - start - 1e-9) / interval)))
        if next_index > index + 1:
            missed = next_index - index - 1
            skipped += missed
            fixed.optional_event(rig.recorder, "sampling_slots_skipped", stage="stage_2", count=missed,
                                 first_scheduled_seconds=(index + 1) * interval,
                                 last_scheduled_seconds=(next_index - 1) * interval)
            fixed.progress(f"Warning: {missed} sampling slot(s) missed while a previous cycle finished; no catch-up burst")
        index = next_index
    elapsed = rig.clock.now() - start
    fixed.optional_event(rig.recorder, "stage_completed", stage="stage_2", elapsed_seconds=elapsed, include_endpoint=True)
    return {"stage": "stage_2", "duration_seconds": duration, "elapsed_seconds": elapsed,
            "include_endpoint": True, "skipped_slots": skipped}


def new_identity():
    return "SI6_STAGE2ONLY_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:12]


def start_review_worker(output):
    """Fire-and-forget review process: no analysis imports, IPC waits or joins.

    The worker only reads saved observations/raw files. It receives no pump or
    needle handles. Its status is for the operator, never a scheduling input.
    """
    folder = Path(output) / "analysis_live"
    folder.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "chemyx_lab.workflows.si6_stage2only_analysis",
               "--run-dir", str(output), "--parent-pid", str(os.getpid())]
    options = {"creationflags": (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP |
                                 subprocess.IDLE_PRIORITY_CLASS)} if os.name == "nt" else {"start_new_session": True}
    env = dict(os.environ, MPLBACKEND="Agg", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
    with (folder / "worker.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=config.REPO_ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, close_fds=True, **options)
    return {"status": "STARTED", "pid": process.pid, "index": str(folder / "index.html"),
            "status_file": str(folder / "status.json"), "controls_run": False}


def run(raw, arduino, settings, nmr, *, mock, output, clock=None, acquisition=None, experiment_id=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    clock = clock or (fixed.SimulatedClock() if mock else fixed.Clock())
    experiment_id = experiment_id or new_identity()
    report = {"status": "INCOMPLETE", "workflow": "stage2only", "mode": "MOCK" if mock else "LIVE",
              "results": str(output), "experiment_id": experiment_id, "stages": [], "observations": [],
              "stage_1_performed": False, "dose_performed": False, "analysis_controls_run": False,
              "interactive_prompts": False, "physical_cleanup": False,
              "needle_home_policy": "return_to_saved_arduino_home",
              "pump_home_policy": "manual_return_to_existing_channel1_home_only"}
    recorder = fixed.FixedTimeRecorder(OperationJournal(output / "operation_journal.jsonl", experiment_id,
        monotonic_fn=clock.now, software_version=discover_git_commit(config.REPO_ROOT)), output / "run_state.json")
    fixed.progress("SI6 STAGE2ONLY" + (" [MOCK: no hardware]" if mock else ""))
    fixed.progress(f"Stage 2: {raw['stage']['hours']:g} hours; every {raw['stage']['interval_minutes']:g} minutes; initial and endpoint samples included")
    fixed.progress(f"Arduino: {arduino['arduino']['port']}; Chemyx: {settings.port}; NMR: {nmr.host}:{nmr.port}; {nmr.scans} scans")
    fixed.progress("Stage 1 skipped; no Channel 2 dose or loading reset. Prepare Channel 1 at its existing empty HOME before launch.")
    fixed.progress(f"Results: {output}")

    def optional_write(path, payload):
        try:
            write_json_atomic(path, payload)
        except Exception as exc:
            fixed.progress(f"Warning: optional output {path} failed: {exc}")

    def observe(row):
        report["observations"].append(row)
        try:
            with (output / "observations.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, default=str) + "\n")
        except Exception as exc:
            fixed.progress(f"Warning: optional observation export failed: {exc}")
        fixed.progress(f"STAGE2ONLY | sample {row['observation']} | scheduled {row['scheduled_seconds']/3600:.2f} h | cleanup OK | {row['status']}")
        if row.get("error"):
            fixed.progress(f"Warning: {row['error']}; continuing on the original schedule")
        optional_write(output / "report.json", report)

    optional_write(output / "configuration.json", raw)
    # Start before opening instrument transports. Never wait for, poll or join
    # this process, including on experiment completion or interruption.
    if mock:
        report["live_analysis"] = {"status": "MOCK_NOT_STARTED", "controls_run": False}
    else:
        try:
            report["live_analysis"] = start_review_worker(output)
            fixed.progress(f"Live analysis review: {report['live_analysis']['index']}")
        except Exception as exc:
            report["live_analysis"] = {"status": "UNAVAILABLE", "error": f"{type(exc).__name__}: {exc}", "controls_run": False}
            fixed.progress(f"Warning: live analysis could not start: {exc}; timed sampling will continue")
    optional_write(output / "report.json", report)
    if acquisition is None:
        def acquisition(stage, observation):
            path = output / "nmr_raw" / f"{stage}_{observation:04d}.dx"
            if mock:
                clock.sleep(30)
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx", path)
                return {"status": "NMR_SAVED", "path": str(path), "simulated": True}
            return acquire_bounded(nmr, path, raw["retrieval_wait_seconds"])
    try:
        with ExitStack() as stack:
            root = output / "mock_state/chemyx_state" if mock else position.state_root(False)
            ledger = stack.enter_context(position.PositionLedger(root, owner=f"stage2only {experiment_id}"))
            if not mock:
                try:
                    stack.enter_context(PortProcessLock(f"fixed-nmr:{nmr.host}:{nmr.port}"))
                except Exception as exc:
                    lock_error = f"NMR ownership unavailable; no STARTs will be sent: {exc}"
                    fixed.progress(f"Warning: {lock_error}; stage2only timed sampling will continue")
                    def acquisition(*_):
                        return {"status": "NMR_FAILED", "path": None, "requested": False, "error": lock_error}
            log = stack.enter_context(fixed.OptionalCommandLog(output / "instrument_commands.log"))
            with redirect_stdout(log):
                cfg = deepcopy(arduino)
                cfg["firmware"]["motion_enabled"] = True
                needle_path = output / "mock_state/needle_state.json" if mock else Path(cfg["needle"]["state_path"])
                controller = stack.enter_context(controller_session(cfg, "mock" if mock else "live", allow_motion=True, apply_runtime_config=True))
                controller.overall_timeout_s = float("inf")  # Individual command/motion deadlines remain active.
                pump = stack.enter_context(manual.pump_session(settings, mock))
                rig = Stage2OnlyRig(raw, cfg, settings, controller, pump, ledger, recorder, None, clock, mock, output)
                try:
                    rig.idle()
                    status = controller.status()
                    if bool_field(status, "moving") or status.get("fault") != "NONE":
                        raise RuntimeError(f"Arduino is moving or faulted at startup: {status}")
                    if mock:
                        # Fixtures only, in this mock run's isolated directory.
                        ledger.set_home(settings.channels[1].identity(settings.pump_identity(True)),
                                        receipt_id="simulation_fixture", established_by="mock manual setup")
                        NeedleStateStore(needle_path).save(NeedleState(logical_position=0, position_valid=True,
                            reason="operator_confirmed_home", last_updated=datetime.now(timezone.utc).isoformat(),
                            steps_per_unit=cfg["needle"]["steps_per_unit"], up_step_sign=cfg["needle"]["up_step_sign"]))
                    rig.needle = TrackedNeedle(controller, cfg, state_path=needle_path)
                    fixed.verify_needle(rig.needle, cfg=cfg)
                    ledger.record_manual_return_home(settings.channels[1].identity(settings.pump_identity(mock)), run_id=experiment_id)
                    recorder.record("stage2only_started", stage_1_performed=False, dose_performed=False, reference_redefined=False)
                    start_position = rig.needle.state.logical_position
                    recorder.record("startup_needle_home_requested", from_position=start_position, target=0,
                                    reference_basis="saved_standalone_arduino_home", reference_redefined=False, physical_state_effect=True)
                    rig.needle_to(0)
                    recorder.record("startup_needle_home_completed", from_position=start_position, target=0,
                                    movement_required=start_position != 0, reference_redefined=False, physical_state_effect=True)
                    rig.needle_to(1)
                except BaseException:
                    rig.stop()
                    raise
            try:
                original_sample = rig.sample
                def sample_logged(fn):
                    with redirect_stdout(log):
                        return original_sample(fn)
                rig.sample = sample_logged
                report["stages"].append(run_stage2only(raw["stage"], rig, acquisition, observe))
                with redirect_stdout(log):
                    rig.idle()
                    fixed.verify_needle(rig.needle, 1, cfg)
                report["physical_cleanup"] = True
                fixed.optional_event(recorder, "stage2only_complete", physical_cleanup=True, dose_performed=False)
                report["status"] = "COMPLETE"
            except BaseException:
                rig.stop()
                raise
    except BaseException as exc:
        if report["status"] == "COMPLETE":
            report["teardown_warning"] = f"{type(exc).__name__}: {exc}"
            fixed.progress(f"Warning: Stage 2 completed; connection/lock close failed: {report['teardown_warning']}")
        else:
            report.update(status="INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "FAILED",
                          error=f"{type(exc).__name__}: {exc}")
            fixed.progress(f"STAGE2ONLY {report['status']}: {report['error']}")
    finally:
        optional_write(output / "report.json", report)
    fixed.progress(f"STAGE2ONLY {report['status']} | observations: {len(report['observations'])} | final cleanup: {report['physical_cleanup']}")
    fixed.progress(f"Results: {output}")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--machine-config", type=Path)
    parser.add_argument("--arduino-config", type=Path)
    parser.add_argument("--output-dir", type=Path, help="New directory only; old results are preserved")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--live", action="store_true", help="Start Stage 2 immediately, with no confirmation prompts")
    modes.add_argument("--mock", action="store_true", help="Accelerated simulation; no hardware")
    modes.add_argument("--validate-config", action="store_true", help="Validate only; no hardware")
    args = parser.parse_args(argv)
    try:
        raw, arduino, settings, nmr = load_settings(args.config, args.machine_config, args.arduino_config)
        if args.validate_config:
            count = round(raw["stage"]["hours"] * 60 / raw["stage"]["interval_minutes"]) + 1
            fixed.progress(f"stage2only configuration valid; {count} scheduled measurements including initial/endpoint; {nmr.scans} scans; no hardware contacted")
            return 0
        identity = new_identity()
        output = args.output_dir or config.resolve_repo_path(raw["output"]["run_root_dir"]) / ("mock" if args.mock else "live") / identity
        report = run(raw, arduino, settings, nmr, mock=args.mock, output=output, experiment_id=identity)
        return 0 if report["status"] == "COMPLETE" else 1
    except Exception as exc:
        fixed.progress(f"stage2only startup failed: {type(exc).__name__}: {exc}")
        return 1
