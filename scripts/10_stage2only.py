"""Attended, clock-based Stage 2 demo; independent of the 06/08 controllers.

Prepare the instruments manually. --live starts immediately without prompts.
Only an optional numerical review child is asynchronous; acquisition is direct.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

import _bootstrap  # noqa: F401
import yaml

from chemyx_lab import config
from chemyx_lab.workflows.stage2_demo_instruments import setup_instruments, validate_physical_config
from chemyx_lab.workflows.stage2_demo_nmr import acquire_nmr

DEFAULT_CONFIG = config.REPO_ROOT / "configs/experiments/10_stage2only.yaml"
FIXTURE = config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"


class Clock:
    now = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class SimulatedClock:
    def __init__(self):
        self.elapsed = 0.0

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += max(0., seconds)


def positive(value, name, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (value == 0 and not zero):
        raise ValueError(f"{name} must be a finite {'nonnegative' if zero else 'positive'} number")
    return float(value)


def load_config(path=DEFAULT_CONFIG):
    cfg = config.read_mapping_config(config.resolve_repo_path(path), "10 Stage 2 demo")
    for section in ("experiment", "arduino", "chemyx", "channel_2", "nmr", "analysis", "output"):
        if not isinstance(cfg.get(section), dict):
            raise ValueError(f"Missing configuration section: {section}")
    exp, nmr = cfg["experiment"], cfg["nmr"]
    duration = positive(exp["duration_minutes"], "duration_minutes") * 60
    interval = positive(exp["interval_minutes"], "interval_minutes") * 60
    if not math.isfinite(duration + interval) or duration / interval > 10000:
        raise ValueError("Invalid or excessive observation schedule")
    if type(exp["include_final_observation"]) is not bool or not str(exp["name"]).strip():
        raise ValueError("An experiment name and boolean include_final_observation are required")
    if not str(nmr["host"]).strip() or type(nmr["port"]) is not int or not 1 <= nmr["port"] <= 65535:
        raise ValueError("NMR host and valid integer port are required")
    if nmr.get("scheme", "http") not in {"http", "https"}:
        raise ValueError("NMR scheme must be http or https")
    for key in ("http_timeout_seconds", "acquisition_timeout_seconds", "poll_seconds", "width_ppm"):
        positive(nmr[key], f"nmr.{key}")
    if type(nmr["scans"]) is not int or nmr["scans"] < 1 or type(nmr["auto_gain"]) is not bool:
        raise ValueError("NMR scans must be a positive integer and auto_gain a boolean")
    if not isinstance(nmr["center_ppm"], (int, float)) or not math.isfinite(nmr["center_ppm"]):
        raise ValueError("NMR center_ppm must be finite")
    if nmr.get("gain") is not None:
        positive(nmr["gain"], "nmr.gain", zero=True)
    raw = Path(nmr.get("raw_directory", "nmr_raw"))
    if raw.is_absolute() or not raw.parts or ".." in raw.parts or raw == Path("."):
        raise ValueError("nmr.raw_directory must be a subdirectory of the fresh run")
    validate_physical_config(cfg)
    return cfg


def observation_times(cfg):
    exp = cfg["experiment"]
    duration, interval = exp["duration_minutes"] * 60., exp["interval_minutes"] * 60.
    slots = [i * interval for i in range(math.ceil(duration / interval))]
    return slots + ([duration] if exp["include_final_observation"] else [])


def elapsed_text(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def make_logger(run, cfg, clock, origin):
    logger = logging.Logger(f"stage2_demo_{run.name}", logging.INFO)
    handlers = []
    if cfg["output"].get("terminal_logging", True):
        handlers.append(logging.StreamHandler(sys.stdout))
    if cfg["output"].get("save_log_file", True):
        try:
            handlers.append(logging.FileHandler(run / "experiment.log", encoding="utf-8"))
        except OSError as exc:
            print(f"Log file unavailable; continuing: {exc}", flush=True)
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger, lambda message: logger.info(f"[{elapsed_text(clock.now() - origin[0])}] {message}")


def save_summary(run, summary, emit):
    try:
        temporary = run / ".summary.json.tmp"
        temporary.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
        temporary.replace(run / "summary.json")
    except OSError as exc:
        emit(f"Summary output warning: {exc}; this file is not startup permission")


def start_analysis(run, config_path, raw_dir, emit):
    """One optional review child, started before serial handles; never waited on."""
    log_path = run / "analysis_live.log"
    command = [sys.executable, "-m", "chemyx_lab.analysis.stage2_demo_analysis", "--run-dir", str(run),
               "--config", str(config_path), "--raw-dir", str(raw_dir), "--watch", "--parent-pid", str(os.getpid())]
    options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.IDLE_PRIORITY_CLASS} if os.name == "nt" else {}
    env = dict(os.environ, MPLBACKEND="Agg", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    with log_path.open("a", encoding="utf-8") as output:
        subprocess.Popen(command, cwd=config.REPO_ROOT, stdin=subprocess.DEVNULL, stdout=output,
                         stderr=subprocess.STDOUT, close_fds=True, env=env, **options)
    stop = threading.Event()
    def relay():
        try:
            with log_path.open(encoding="utf-8", errors="replace") as source:
                while not stop.is_set():
                    line = source.readline()
                    if line:
                        emit(line.rstrip())
                    else:
                        stop.wait(.1)
        except OSError:
            pass
    threading.Thread(target=relay, daemon=True, name="analysis_progress_only").start()
    return stop


def run_experiment(cfg, *, mock, output_dir=None, clock=None, rig_factory=setup_instruments,
                   acquisition=None, analysis_launcher=start_analysis):
    """One sequential loop; no previous-run records or analysis decisions."""
    clock = clock or (SimulatedClock() if mock else Clock())
    identity = "stage2only_demo_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8]
    run = Path(output_dir or config.resolve_repo_path(cfg["output"]["root_dir"]) / identity).resolve()
    run.mkdir(parents=True, exist_ok=False)
    raw_dir = run / cfg["nmr"].get("raw_directory", "nmr_raw")
    origin = [clock.now()]
    logger, emit = make_logger(run, cfg, clock, origin)
    slots = observation_times(cfg)
    summary = dict(status="RUNNING", mode="MOCK" if mock else "LIVE", results=str(run),
        duration_seconds=cfg["experiment"]["duration_minutes"] * 60., interval_seconds=cfg["experiment"]["interval_minutes"] * 60.,
        scheduled_observations=len(slots), attempted_observations=0, skipped_observations=0,
        nmr_saved=0, nmr_failed=0, nmr_missing=0, analyses_successful=0, analyses_failed=0,
        analyses_pending=0, analysis_complete=not cfg["analysis"].get("enabled", True),
        channel_2_status="DISABLED" if not cfg["channel_2"]["enabled"] else "NOT_ATTEMPTED", observations=[])
    stop_relay = None
    rig = None
    nmr_session = {}  # Only this invocation's unresolved START; never persisted or loaded.
    try:
        snapshot = run / "config_used.yaml"
        snapshot_ready = True
        try:
            # Give this new dataset its own explicit title without importing
            # numerical processing or borrowing an old dataset's display name.
            saved_cfg = dict(cfg, analysis=dict(cfg["analysis"]))
            if not str(saved_cfg["analysis"].get("dataset_display_name") or "").strip():
                saved_cfg["analysis"]["dataset_display_name"] = " ".join(
                    f"{cfg['experiment']['name']} {run.name}".split())
            snapshot.write_text(yaml.safe_dump(saved_cfg, sort_keys=False), encoding="utf-8")
        except OSError as exc:
            snapshot_ready = False
            emit(f"Config snapshot warning: {exc}; review unavailable, timed acquisition continues")
        save_summary(run, summary, emit)
        emit("STAGE 2 DEMO EXPERIMENT" + (" [MOCK: no hardware]" if mock else ""))
        emit(f"Duration: {cfg['experiment']['duration_minutes']:g} minutes; interval: {cfg['experiment']['interval_minutes']:g} minutes; {len(slots)} observations")
        emit(f"Channel 2 addition: {'ENABLED' if cfg['channel_2']['enabled'] else 'DISABLED'}")
        emit(f"Results: {run}")
        if cfg["analysis"].get("enabled", True) and snapshot_ready:
            try:
                stop_relay = analysis_launcher(run, snapshot, raw_dir, emit)
            except Exception as exc:
                emit(f"Analysis WARNING: cannot start optional review: {exc}; acquisition continues")

        with rig_factory(cfg, run, mock=mock, clock=clock, emit=emit) as rig:
            rig.prepare()
            origin[0] = clock.now()
            emit("EXPERIMENT STARTED")
            for index, due in enumerate(slots):
                elapsed = clock.now() - origin[0]
                if index and due < elapsed - 1e-8:
                    summary["skipped_observations"] += 1
                    emit(f"MISSED observation {index + 1}: scheduled {elapsed_text(due)}; no catch-up acquisition")
                    continue
                remaining = due - elapsed
                if remaining > 0:
                    emit(f"Next observation: {elapsed_text(due)}; waiting {remaining:.1f} seconds")
                    while clock.now() - origin[0] < due:
                        clock.sleep(due - (clock.now() - origin[0]))
                summary["attempted_observations"] += 1
                emit(f"OBSERVATION {index + 1} / {len(slots)}")

                def measure():
                    path = raw_dir / f"stage_2_{index + 1:04d}.dx"
                    try:
                        if acquisition is not None:
                            result = acquisition(cfg["nmr"], path, clock=clock, emit=emit)
                        elif mock:
                            emit("NMR: Simulated acquisition (no network)")
                            clock.sleep(80)
                            path.parent.mkdir(parents=True, exist_ok=True)
                            with path.open("xb") as output:
                                output.write(FIXTURE.read_bytes())
                            result = dict(status="NMR_SAVED", path=str(path), simulated=True)
                            emit(f"NMR: Raw data saved: {path}")
                        else:
                            result = acquire_nmr(cfg["nmr"], path, state=nmr_session,
                                                 now=clock.now, sleep=clock.sleep, emit=emit)
                        if not isinstance(result, dict) or result.get("status") not in {"NMR_SAVED", "NMR_FAILED", "NMR_MISSING"}:
                            raise ValueError("Invalid NMR acquisition outcome")
                    except Exception as exc:
                        result = dict(status="NMR_FAILED", path=None, reason=f"{type(exc).__name__}: {exc}")
                    recovered = result.get("recovered_previous")
                    if recovered:
                        for prior in summary["observations"]:
                            original = raw_dir / f"stage_2_{prior['observation']:04d}.dx"
                            if str(original) == recovered["original_path"] and prior["status"] != "NMR_SAVED":
                                summary[{"NMR_FAILED": "nmr_failed", "NMR_MISSING": "nmr_missing"}[prior["status"]]] -= 1
                                prior.update(status="NMR_SAVED", path=recovered["path"],
                                             unresolved_start=False,
                                             reason="Original acquisition completed during later monitoring")
                                summary["nmr_saved"] += 1
                                break
                    summary[{"NMR_SAVED": "nmr_saved", "NMR_FAILED": "nmr_failed", "NMR_MISSING": "nmr_missing"}[result["status"]]] += 1
                    row = dict(result, stage="stage_2", observation=index + 1, scheduled_seconds=due,
                               acquired_elapsed_seconds=clock.now() - origin[0], physical_cleanup="PENDING")
                    summary["observations"].append(row)
                    if result["status"] != "NMR_SAVED":
                        emit(f"NMR FAILED: {str(result.get('reason', 'No raw spectrum saved'))[:1200]}; continuing scheduled experiment")
                    return result

                rig.sample(measure)
                summary["observations"][-1]["physical_cleanup"] = "COMPLETE"
                emit(f"Observation {index + 1} COMPLETE; cleanup complete")
                if index == 0 and cfg["channel_2"]["enabled"]:
                    delay = cfg["channel_2"]["delay_after_time_zero_seconds"]
                    emit(f"Chemyx CH2: Waiting {delay:g} seconds after Time 0 physical cleanup")
                    clock.sleep(delay)
                    summary["channel_2_status"] = "ATTEMPTED_UNCERTAIN"
                    rig.dose()
                    summary["channel_2_status"] = "COMPLETED"
                    emit("Chemyx CH2: One reagent addition complete")
                save_summary(run, summary, emit)
            while clock.now() - origin[0] < summary["duration_seconds"]:
                clock.sleep(summary["duration_seconds"] - (clock.now() - origin[0]))
            summary["status"] = "COMPLETE" if summary["nmr_saved"] else "COMPLETE_NO_SPECTRA"
    except BaseException as exc:
        summary["status"] = "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "FAILED"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        emit(f"PHYSICAL INSTRUMENT / EXECUTION ERROR: {summary['error']}; no further movement")
        if rig is not None:
            rig.stop()
    finally:
        summary["elapsed_seconds"] = clock.now() - origin[0]
        try:
            review = json.loads((run / "analysis/analysis_summary.json").read_text(encoding="utf-8"))
            successful, failed = review["successful_analyses"], review["failed_analyses"]
            if type(successful) is not int or type(failed) is not int or min(successful, failed) < 0:
                raise ValueError("Invalid review counts")
            summary["analyses_successful"], summary["analyses_failed"] = successful, failed
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Counts are reporting only, never an instrument decision.
        if cfg["analysis"].get("enabled", True):
            summary["analyses_pending"] = max(0, summary["nmr_saved"] - summary["analyses_successful"] - summary["analyses_failed"])
        save_summary(run, summary, emit)
        emit(f"{summary['status']} | elapsed {elapsed_text(summary['elapsed_seconds'])} | scheduled {len(slots)} | attempted {summary['attempted_observations']} | skipped {summary['skipped_observations']}")
        emit(f"NMR saved {summary['nmr_saved']}; failed {summary['nmr_failed']}; missing {summary['nmr_missing']}; analyses successful {summary['analyses_successful']}, failed {summary['analyses_failed']}, pending {summary['analyses_pending']}")
        emit(f"Channel 2: {summary['channel_2_status']}; analysis can finish separately; see {run / 'analysis_live.log'}")
        emit(f'Full analysis later: python scripts\\09_analyze_stage2only_scipy.py "{raw_dir}"')
        if stop_relay is not None:
            stop_relay.set()
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, help="Optional new run directory; existing directories are preserved")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run_experiment(load_config(args.config), mock=args.mock, output_dir=args.output_dir)
        return 0 if summary["status"] == "COMPLETE" else 1
    except Exception as exc:
        print(f"Stage 2 demo startup failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
