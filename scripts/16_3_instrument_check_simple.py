"""16 — quick three-instrument check (replaces 13): no state files, no lockouts.

Prepare first: HOME set with needle_control.py set-home (shared position file),
Channel 1 syringe empty, NMR idle. --live starts immediately:

  1. connect the Arduino and Chemyx, check the NMR answers
  2. needle to the saved HOME if elsewhere, UP one increment, DOWN back to HOME
  3. Channel 1: withdraw 1 mL, infuse 1 mL
  4. one NMR scan (04/05 legacy completion), saved as nmr_raw/check_0001.dx
  5. needle at HOME, ports closed; PASS/FAIL printed for each part
"""
from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab.workflows.simple_rig import (HardwareError, RealClock, SimpleRig, SimulatedClock,
    check_settings, close_logger, load_settings, make_logger, new_run_dir, ok)

DEFAULT_CONFIG = Path("configs/experiments/16_3_instrument_check_simple.yaml")
EXTRA_SETTINGS = {"check": ("withdraw_volume_ml", "infuse_volume_ml", "pump_rate_ml_min", "nmr_scans")}
STEPS = ("Connect Arduino + Chemyx", "NMR reachable", "Needle UP/DOWN", "Chemyx CH1 withdraw/infuse",
         "NMR scan", "Needle HOME + ports closed")


def load_config(path=DEFAULT_CONFIG):
    return check_settings(load_settings(path), EXTRA_SETTINGS)


def run(cfg, *, mock, output_dir=None, clock=None, rig_factory=SimpleRig):
    clock = clock or (SimulatedClock() if mock else RealClock())
    run_dir = new_run_dir(cfg, "16_check_simple", output_dir)
    log = make_logger(run_dir, clock, "16_check_simple")
    t = cfg["check"]
    results = {step: "NOT RUN" for step in STEPS}
    current, rig = STEPS[0], None
    try:
        log.info("=" * 60)
        log.info("16 THREE-INSTRUMENT CHECK (simple)" + (" [MOCK: no hardware]" if mock else " [LIVE]"))
        log.info(f"Results: {run_dir}")
        log.info("Assuming: HOME set with needle_control.py, Channel 1 syringe empty, NMR idle")
        log.info("=" * 60)
        with rig_factory(cfg, mock=mock, clock=clock, log=log) as rig:
            results[current] = "PASS"
            results["NMR reachable"] = "PASS" if rig.nmr_reachable else "WARN"
            current = "Needle UP/DOWN"
            rig.needle_to(0)  # The saved HOME (moves only if the needle was left elsewhere).
            rig.needle_move("up")
            rig.needle_move("down")
            results[current] = "PASS"
            current = "Chemyx CH1 withdraw/infuse"
            rig.pump_transfer("withdraw", t["withdraw_volume_ml"], t["pump_rate_ml_min"])
            rig.pump_transfer("infuse", t["infuse_volume_ml"], t["pump_rate_ml_min"])
            results[current] = "PASS"
            current = "NMR scan"
            scan = rig.run_nmr(t["nmr_scans"], run_dir / "nmr_raw" / "check_0001.dx")
            results[current] = "PASS" if scan["status"] == "NMR_SAVED" else "FAIL"
            current = None
    except KeyboardInterrupt:
        if current:
            results[current] = "INTERRUPTED"
    except Exception as exc:
        if current:
            results[current] = "FAIL"
        if not isinstance(exc, HardwareError):  # Hardware errors were already logged by the rig.
            log.error(f"FAILED: {type(exc).__name__}: {exc}")
    finally:
        if rig is not None:
            results["Needle HOME + ports closed"] = "PASS" if rig.returned_home else "FAIL"
        passed = all(results[step] == "PASS" for step in STEPS if step != "NMR reachable")
        log.info("=" * 60)
        if passed:
            ok(log, "THREE-INSTRUMENT CHECK PASSED")
        else:
            log.error("THREE-INSTRUMENT CHECK FAILED")
        for step in STEPS:
            line = f"  {step:<28} {results[step]}"
            if results[step] == "PASS":
                ok(log, line)
            elif results[step] in ("WARN", "NOT RUN"):
                log.warning(line)
            else:
                log.error(line)
        log.info("=" * 60)
        close_logger(log)
    return passed, results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, help="Optional new run folder")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true", help="Fake instruments, simulated clock")
    modes.add_argument("--live", action="store_true", help="Real instruments; starts immediately")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"16 configuration error: {type(exc).__name__}: {exc}", flush=True)
        return 2
    try:
        passed, results = run(cfg, mock=args.mock, output_dir=args.output_dir)
    except Exception as exc:  # Only before the log exists, e.g. the output folder already exists.
        print(f"16 could not start: {type(exc).__name__}: {exc}", flush=True)
        return 1
    return 0 if passed else (130 if "INTERRUPTED" in results.values() else 1)


if __name__ == "__main__":
    raise SystemExit(main())
