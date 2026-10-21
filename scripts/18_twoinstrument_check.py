"""18: quick check of the 17/19 two-instrument rig before a long run.

Chemyx + NMR only (no Arduino; the needle stays in the flask). Connects the
Chemyx and sends STOP to Channel 1 and Channel 2 (CH2 never moves), checks the
NMR answers, then one 17 cycle with the check values: CH1 withdraw 1 mL ->
1-scan NMR -> CH1 infuse 1 mL. One startup confirmation. Writes log.txt and
check_0001.dx in a new folder. Exit code 0 only when every part passes.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab.workflows.simple_rig import PumpCommandError, RealClock, SimulatedClock, close_logger, make_logger, ok


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nineteen = load_script("stage1_stage2_19_for_18", "19_twoinstrument_stage1_stage2.py")
DEFAULT_CONFIG = Path("configs/experiments/18_twoinstrument_check.yaml")
PREFIX = "18_twoinstrument_check"


def load_config(path=DEFAULT_CONFIG):
    return nineteen.load_config(path)  # 19's checks, with 18's check volumes and scan count.


def part_names(cfg):
    c = cfg["chemyx"]
    return {"connect": "Chemyx connect + STOP (CH1 and CH2)", "nmr_check": "NMR reachable",
            "withdraw": f"CH1 withdraw {c['withdraw_volume_ml']:g} mL",
            "nmr": f"NMR {cfg['nmr']['scans']}-scan spectrum saved",
            "infuse": f"CH1 infuse {c['infuse_volume_ml']:g} mL (sample returned)",
            "close": "Chemyx STOP + port closed"}


def report(log, outcome, text):
    if outcome == "PASS":
        ok(log, text)
    elif outcome == "FAIL":
        log.error(text)
    else:
        log.warning(text)


def run(cfg, *, mock, output_dir=None, clock=None, rig_factory=nineteen.StageRig, confirm=False, input_fn=input):
    clock = clock or (SimulatedClock() if mock else RealClock())
    run_dir = nineteen.results_dir(cfg, output_dir, PREFIX)
    log = make_logger(run_dir, clock, PREFIX)
    names = part_names(cfg)
    results = dict.fromkeys(names, "NOT RUN")
    status, rig = "FAILED", None
    c = cfg["chemyx"]
    try:
        log.info("18 TWO-INSTRUMENT CHECK" + (" [MOCK: no hardware]" if mock else " [LIVE]"))
        log.info("Arduino: NOT USED | Needle: stationary in flask | CH2: STOP only, never moved")
        log.info(f"CH1: withdraw {c['withdraw_volume_ml']:g} mL -> NMR {cfg['nmr']['scans']} scan(s) -> infuse "
                 f"{c['infuse_volume_ml']:g} mL at {c['withdraw_rate_ml_min']:g} / {c['infuse_rate_ml_min']:g} mL/min")
        log.info(f"CH1 loaded (operator): {c['initial_volume_ml']:g} mL | Results: {run_dir}")
        log.info("Confirm: needle secured in flask, NMR idle, sample-return path open, CH1 loaded as above.")
        if confirm and input_fn("Confirm rig is ready [y/N]: ").strip().lower() not in {"y", "yes"}:
            status = "DECLINED"
            return status, results
        with rig_factory(cfg, mock=mock, clock=clock, log=log) as rig:
            results["connect"] = "PASS" if rig.ch2_ready else "FAIL"
            results["nmr_check"] = "PASS" if rig.nmr.check() else "FAIL"
            try:
                result = nineteen.seventeen.sample(rig, cfg, run_dir / "check_0001.dx", 1)
                results["nmr"] = "PASS" if result["status"] == "NMR_SAVED" else "FAIL"
            finally:
                results["withdraw"] = "PASS" if "withdraw" in rig.completed else "FAIL"
                results["infuse"] = "PASS" if "infuse" in rig.completed else "FAIL"
                if results["nmr"] == "NOT RUN" and "withdraw" in rig.completed:
                    results["nmr"] = "FAIL"
        status = "COMPLETE"
    except KeyboardInterrupt:
        status = "INTERRUPTED"
        log.error("Interrupted by operator (Ctrl+C)")
    except (PumpCommandError, nineteen.seventeen.NmrRunError) as exc:
        status = "STOPPED"
        log.error(str(exc))
    except Exception as exc:
        log.error(f"FAILED: {type(exc).__name__}: {exc}")
    finally:
        if status != "DECLINED":
            if rig is not None:
                results["close"] = "PASS" if not rig.pump.pump.is_connected else "FAIL"
            elif results["connect"] == "NOT RUN":
                results["connect"] = "FAIL"
            log.info("=" * 60)
            for key, name in names.items():
                report(log, results[key], f"{results[key]:7s} {name}")
            passed = all(value == "PASS" for value in results.values())
            report(log, "PASS" if passed else "FAIL",
                   "CHECK PASSED: the rig is ready for 19" if passed else f"CHECK FAILED ({status})")
            log.info(f"Results preserved: {run_dir}")
        close_logger(log)
    return status, results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, help="Optional new run folder; existing folders are never overwritten")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true", help="Fake pump/NMR and simulated time")
    modes.add_argument("--live", action="store_true", help="Real instruments after one rig-ready confirmation")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"18 configuration error: {type(exc).__name__}: {exc}", flush=True)
        return 2
    try:
        status, results = run(cfg, mock=args.mock, output_dir=args.output_dir, confirm=True)
    except Exception as exc:
        print(f"18 could not start: {type(exc).__name__}: {exc}", flush=True)
        return 1
    if status == "DECLINED":
        return 3
    return 0 if status == "COMPLETE" and all(value == "PASS" for value in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
