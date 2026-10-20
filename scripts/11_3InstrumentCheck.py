"""Short attended three-instrument hardware check (Arduino + Chemyx CH1 + NMR).

A one-iteration companion to ``10_stage2only.py``. It reuses 10's configuration
loader, logger, Arduino/Chemyx rig (``DemoRig``/``setup_instruments``) and NMR
acquisition (``acquire_nmr``); nothing about 10 is modified. --live starts
without prompts. Analysis, if requested, runs afterwards in a separate process
that holds no instrument handles and can never change a hardware verdict.

Sequence: needle UP -> DOWN -> CH1 withdraw -> CH1 infuse -> needle UP ->
NMR (1 scan, one START) -> raw .dx saved -> optional analysis.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _bootstrap  # noqa: E402,F401  (puts the repository root on sys.path, as in 10)
import yaml  # noqa: E402

from chemyx_lab import config  # noqa: E402
from chemyx_lab.workflows.stage2_demo_instruments import setup_instruments  # noqa: E402
from chemyx_lab.workflows.stage2_demo_nmr import acquire_nmr  # noqa: E402


def _load_stage2only():
    spec = importlib.util.spec_from_file_location("stage2only_entry_for_check", SCRIPTS / "10_stage2only.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_stage2only()
DEFAULT_CHECK_CONFIG = config.REPO_ROOT / "configs/experiments/11_3InstrumentCheck.yaml"
RAW_NAME = "instrument_check_0001.dx"
DEFAULT_SETTINGS = dict(chemyx_channel=1, withdraw_volume_ml=1.0, infuse_volume_ml=1.0,
                        pump_rate_ml_min=5.0, nmr_scans=1, analyze_spectrum=True)
LIMITATIONS = [
    "Does not prove the full 13 mL sampling volume reaches/fills the NMR flow cell.",
    "Does not test the eight-scan setting, the optional Channel 2 dose or six-hour endurance.",
    "Chemyx PASS means START/STOP were acknowledged by the pump; verify plunger travel by eye.",
]


# ---------------------------------------------------------------- configuration
def load_check_config(path=DEFAULT_CHECK_CONFIG):
    """Read 10's hardware settings, then apply test settings in memory only."""
    path = Path(path)
    own = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(own, dict):
        raise ValueError("Check configuration must be a mapping")
    settings = dict(DEFAULT_SETTINGS, **(own.get("three_instrument_check") or {}))
    cfg = deepcopy(demo.load_config(own.get("base_config") or demo.DEFAULT_CONFIG))
    p, n = cfg["chemyx"], cfg["nmr"]
    if type(settings["chemyx_channel"]) is not int or settings["chemyx_channel"] != 1:
        raise ValueError("The check operates Chemyx Channel 1 only")
    for key in ("withdraw_volume_ml", "infuse_volume_ml", "pump_rate_ml_min"):
        demo.positive(settings[key], key)
    if type(settings["nmr_scans"]) is not int or settings["nmr_scans"] < 1 or type(settings["analyze_spectrum"]) is not bool:
        raise ValueError("nmr_scans must be a positive integer and analyze_spectrum a boolean")
    lo, hi = config.rate_limits(0)
    if not lo <= settings["pump_rate_ml_min"] <= hi:
        raise ValueError("pump_rate_ml_min exceeds the Chemyx mL/min limits")
    if settings["withdraw_volume_ml"] + p["safety_margin_ml"] > p["syringe_capacity_ml"] or settings["infuse_volume_ml"] > settings["withdraw_volume_ml"]:
        raise ValueError("Infusion cannot exceed the withdrawal, and the withdrawal must fit the syringe")
    n["scans"] = settings["nmr_scans"]
    cfg["channel_2"]["enabled"] = False
    cfg["analysis"]["enabled"] = settings["analyze_spectrum"]
    cfg["three_instrument_check"] = settings
    return cfg


# ----------------------------------------------------------------------- mock
class MockNmrClient:
    """Records the HTTP-client calls that the real acquire_nmr makes.

    Mimics the documented iFlow behaviour acquire_nmr requires: a START receipt,
    a matching OriginalReceipt/NumberOfScansRun, Service/Acquire completion and
    a raw FID only once the requested scans have run.
    """

    RECEIPT = {"ExperimentNumber": 1, "TimeStamp": "2026-01-01T00:00:00"}

    def __init__(self, raw_text, *, never_complete=False):
        self.calls, self.raw_text, self.never_complete = [], raw_text, never_complete
        self.started, self.polls, self.scans = False, 0, 1

    def _done(self):
        return self.started and self.polls > 2 and not self.never_complete

    def iflow_experiment_status(self):
        self.calls.append("GET /interfaces/iFlow/ExperimentStatus")
        if not self.started:
            return {}
        self.polls += 1
        status = {"OriginalReceipt": dict(self.RECEIPT), "NumberOfScansRun": self.scans if self._done() else 0}
        if self._done():
            status["JDX_FileContents_TD"] = self.raw_text
        return status

    def acquire_status(self):
        self.calls.append("GET /interfaces/Service/Acquire")
        return {"Status": {"InProgress": not self._done(), "LastAcquireSuccessful": self._done()}}

    def iflow_1d_settings(self):
        self.calls.append("GET /interfaces/iFlow/Settings/1D")
        return {}

    def iflow_experiment_settings(self):
        self.calls.append("GET /interfaces/iFlow/ExperimentSettings")
        return {}

    def set_iflow_1d_settings(self, settings):
        self.calls.append("PUT /interfaces/iFlow/Settings/1D")

    def run_iflow_experiment(self, settings):
        self.calls.append(f"PUT /interfaces/iFlow/RunExperiment (NumberOfScans={settings.get('NumberOfScans')})")
        self.started, self.scans = True, settings["NumberOfScans"]
        return dict(self.RECEIPT)


# ------------------------------------------------------------------- analysis
def run_analysis(run, snapshot, raw_path, emit):
    """Process the saved file in a separate process; never touches instruments."""
    work = run / "analysis/_work"
    (work / "nmr_raw").mkdir(parents=True, exist_ok=True)
    # The existing review tool recognises stage_2_NNNN.dx; work on a copy.
    shutil.copyfile(raw_path, work / "nmr_raw/stage_2_0001.dx")
    command = [sys.executable, "-m", "chemyx_lab.analysis.stage2_demo_analysis", "--run-dir", str(work),
               "--config", str(snapshot), "--raw-dir", str(work / "nmr_raw")]
    try:
        proc = subprocess.run(command, cwd=config.REPO_ROOT, capture_output=True, text=True, timeout=300,
                              env=dict(__import__("os").environ, MPLBACKEND="Agg"))
    except Exception as exc:
        return dict(status="FAIL", detail=f"{type(exc).__name__}: {exc}")
    for line in (proc.stdout or "").splitlines():
        if line.strip():
            emit(f"ANALYSIS: {line.strip()}")
    plot = work / "analysis/observation_0001.png"
    if proc.returncode != 0 or not plot.is_file():
        return dict(status="FAIL", detail=(proc.stderr or proc.stdout or f"exit {proc.returncode}")[-600:])
    target = run / "analysis/instrument_check_0001.png"
    shutil.copyfile(plot, target)
    for name in ("peak_areas.csv", "analysis_summary.json"):
        if (work / "analysis" / name).is_file():
            shutil.copyfile(work / "analysis" / name, run / "analysis" / name)
    return dict(status="PASS", detail=str(target))


# ----------------------------------------------------------------- the check
def rollup(steps, keys):
    values = [steps[key] for key in keys]
    return "FAIL" if "FAIL" in values else "PASS" if all(v == "PASS" for v in values) else "NOT_RUN"


def run_check(cfg, *, mock, output_dir=None, clock=None, rig_factory=setup_instruments, nmr_client=None,
              acquisition=None, analyzer=run_analysis):
    clock = clock or (demo.SimulatedClock() if mock else demo.Clock())
    t = cfg["three_instrument_check"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run = Path(output_dir or config.resolve_repo_path(cfg["output"]["root_dir"]) / f"3instrument_check_{stamp}").resolve()
    run.mkdir(parents=True, exist_ok=False)
    raw = run / cfg["nmr"].get("raw_directory", "nmr_raw") / RAW_NAME
    origin = [clock.now()]
    logger, emit = demo.make_logger(run, cfg, clock, origin)
    keys = ["arduino_up", "arduino_down", "chemyx_withdraw", "chemyx_infuse", "arduino_return_up",
            "nmr_acquisition", "raw_saving", "analysis"]
    steps = {key: "NOT_RUN" for key in keys}
    summary = dict(status="RUNNING", mode="MOCK" if mock else "LIVE", results=str(run), settings=t,
                   steps=steps, details={}, failed_step=None, error=None, limitations=LIMITATIONS)
    rig = None

    def save():
        try:
            temporary = run / ".summary.json.tmp"
            temporary.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
            temporary.replace(run / "summary.json")
        except OSError as exc:
            emit(f"Summary output warning: {exc}")

    def fail(key, label, exc):
        steps[key] = "FAIL"
        summary["failed_step"], summary["error"] = key, f"{type(exc).__name__}: {exc}"
        emit(f"{label}: FAILED - {summary['error']}")
        if rig is not None:
            rig.stop()  # Uncertain needle/syringe state: no further motion.

    try:
        saved = deepcopy(cfg)
        saved["analysis"].setdefault("dataset_display_name", None)
        if not str(saved["analysis"].get("dataset_display_name") or "").strip():
            saved["analysis"]["dataset_display_name"] = run.name
        snapshot = run / "config_used.yaml"
        snapshot.write_text(yaml.safe_dump(saved, sort_keys=False), encoding="utf-8")
        save()
        bar = "=" * 50
        emit(bar)
        emit("THREE-INSTRUMENT HARDWARE CHECK" + (" [MOCK: no hardware]" if mock else ""))
        emit("Arduino + Chemyx + NMR")
        emit(bar)
        emit(f"Results: {run}")
        emit("Connecting to instruments")
        try:
            context = rig_factory(cfg, run, mock=mock, clock=clock, emit=emit)
            rig = context.__enter__()
        except BaseException as exc:
            summary["failed_step"], summary["error"] = "connect", f"{type(exc).__name__}: {exc}"
            emit(f"CONNECT (Arduino serial / saved needle reference / Chemyx serial): FAILED - {summary['error']}")
            raise
        try:
            rate, wv, iv = t["pump_rate_ml_min"], t["withdraw_volume_ml"], t["infuse_volume_ml"]
            sequence = [
                ("arduino_up", "ARDUINO: needle UP", lambda: rig.move_needle(1)),
                ("arduino_down", "ARDUINO: needle DOWN", lambda: rig.move_needle(0)),
                ("chemyx_withdraw", f"CHEMYX CH1: withdraw {wv:g} mL", lambda: rig.transfer(1, -wv, rate, "Check withdrawal")),
                ("chemyx_infuse", f"CHEMYX CH1: infuse {iv:g} mL", lambda: rig.transfer(1, iv, rate, "Check infusion")),
                ("arduino_return_up", "ARDUINO: returning needle UP", lambda: rig.move_needle(1)),
            ]
            physical_ok = True
            for key, label, action in sequence:
                emit(label)
                try:
                    action()
                except Exception as exc:
                    fail(key, label, exc)
                    physical_ok = False
                    break
                steps[key] = "PASS"
                emit(f"{label}: complete (confirmed by instrument feedback)")
                save()
            if physical_ok:
                emit(f"NMR: Configuring {cfg['nmr']['scans']} scan(s)")
                try:
                    if acquisition is not None:
                        result = acquisition(cfg["nmr"], raw, clock=clock, emit=emit)
                    else:
                        client = nmr_client or (MockNmrClient(demo.FIXTURE.read_text(encoding="utf-8")) if mock else None)
                        result = acquire_nmr(cfg["nmr"], raw, state={}, client=client, now=clock.now, sleep=clock.sleep, emit=emit)
                        if isinstance(client, MockNmrClient):
                            summary["details"]["nmr_http_calls"] = list(client.calls)
                    status = result.get("status")
                    summary["details"]["nmr"] = result
                    if status == "NMR_SAVED" and raw.is_file() and raw.stat().st_size and raw.read_text(encoding="utf-8", errors="replace").lstrip().startswith("##"):
                        steps["nmr_acquisition"] = steps["raw_saving"] = "PASS"
                        emit(f"NMR: Raw .dx file saved: {raw}")
                    elif status == "NMR_MISSING":  # Completion confirmed, raw FID never retrieved.
                        steps["nmr_acquisition"], steps["raw_saving"] = "PASS", "FAIL"
                        summary["failed_step"], summary["error"] = "raw_saving", str(result.get("reason"))
                        emit(f"NMR: Raw FID retrieval/saving FAILED - {result.get('reason')}")
                    else:
                        steps["nmr_acquisition"] = "FAIL"
                        summary["failed_step"], summary["error"] = "nmr_acquisition", str(result.get("reason", status))
                        emit(f"NMR: Acquisition FAILED - {str(result.get('reason', status))[:1200]}")
                except Exception as exc:
                    fail("nmr_acquisition", "NMR", exc)
            else:
                emit("NMR: skipped because the physical needle/syringe state is uncertain")
            if mock and rig is not None:
                summary["details"]["mock_commands"] = dict(
                    arduino=[str(x).strip() for x in getattr(getattr(rig.controller, "transport", None), "tx_log", [])],
                    chemyx=[str(x).strip() for x in getattr(getattr(rig.pump, "ser", None), "tx_log", [])])
        except BaseException as exc:
            summary["status"] = "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "FAILED"
            summary["error"] = f"{type(exc).__name__}: {exc}"
            emit(f"EXECUTION ERROR: {summary['error']}; no further movement")
            rig.stop()
            raise
        finally:
            context.__exit__(*sys.exc_info())  # Close serial handles before analysis.
        rig = None
        if t["analyze_spectrum"] and steps["raw_saving"] == "PASS":
            emit("ANALYSIS: Processing spectrum (separate process)")
            outcome = analyzer(run, snapshot, raw, emit)
            steps["analysis"], summary["details"]["analysis"] = outcome["status"], outcome
            emit("ANALYSIS: Spectrum plot saved" if outcome["status"] == "PASS" else f"ANALYSIS: FAILED - {outcome['detail']}")
    except BaseException as exc:
        if summary["status"] == "RUNNING":
            summary["status"] = "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "FAILED"
            summary["error"] = summary["error"] or f"{type(exc).__name__}: {exc}"
            if summary["failed_step"] is None:
                summary["failed_step"] = "execution"
    finally:
        hardware = [steps[k] for k in keys[:7]]
        ok = all(v == "PASS" for v in hardware) and (steps["analysis"] == "PASS" or not t["analyze_spectrum"])
        if summary["status"] == "RUNNING":
            summary["status"] = "COMPLETE" if ok else "FAILED"
        summary["rollup"] = {"Arduino": rollup(steps, keys[:2] + ["arduino_return_up"]),
                             "Chemyx Channel 1": rollup(steps, ["chemyx_withdraw", "chemyx_infuse"]),
                             "NMR Acquisition": steps["nmr_acquisition"], "Raw FID Saving": steps["raw_saving"],
                             "NMR Analysis": steps["analysis"] if t["analyze_spectrum"] else "DISABLED"}
        save()
        emit("=" * 50)
        emit("THREE-INSTRUMENT CHECK " + ("COMPLETE" if summary["status"] == "COMPLETE" else f"{summary['status']} (failed step: {summary['failed_step']})"))
        for name, value in summary["rollup"].items():
            emit(f"{name}: {value}")
        emit("=" * 50)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CHECK_CONFIG)
    parser.add_argument("--output-dir", type=Path, help="Optional new run directory; existing directories are preserved")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run_check(load_check_config(args.config), mock=args.mock, output_dir=args.output_dir)
        return 0 if summary["status"] == "COMPLETE" else 1
    except Exception as exc:
        print(f"Three-instrument check startup failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
