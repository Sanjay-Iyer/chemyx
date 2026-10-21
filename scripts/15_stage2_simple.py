"""15 — simple Stage 2 run (replaces 12): no state files, no lockouts.

Prepare first: HOME set with needle_control.py set-home (shared position file),
Channel 1 syringe empty, NMR idle. --live starts immediately, moves the needle
to the saved HOME if it is elsewhere, raises it to UP, then runs
every slot with 12's recipe: withdraw 8 mL (needle UP), needle DOWN to HOME,
withdraw 5 mL, settle, NMR scan, needle UP, return 13 mL, wash 5 mL out/back.

An NMR failure skips that slot's spectrum and the schedule continues. A needle
or pump error, or Ctrl+C, STOPs the pump and Arduino, returns the needle to
HOME and exits. Output: one folder with nmr_raw/*.dx and log.txt.

Once the physical run has ended (needle at HOME, ports closed), the command
line runs the existing 09 SciPy analysis on the saved spectra in its own
process: tables and PNG plots go to analysis_stage2only_scipy/ in the same
folder. The raw .dx files are only read. Analysis problems are logged; they
never change the run's status or exit code. After Ctrl+C there is no analysis.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import _bootstrap  # noqa: F401
from chemyx_lab.workflows.simple_rig import (HardwareError, RealClock, SimpleRig, SimulatedClock,
    check_settings, clock_text, close_logger, load_settings, make_logger, new_run_dir, ok)

DEFAULT_CONFIG = Path("configs/experiments/15_stage2_simple.yaml")
ANALYSIS_SCRIPT = Path(__file__).resolve().with_name("09_analyze_stage2only_scipy.py")
ANALYSIS_TIMEOUT_SECONDS = 3600  # 09's own processor limit; 15 waits up to 10 minutes longer.
EXTRA_SETTINGS = {
    "experiment": ("duration_minutes", "interval_minutes", "include_final_observation"),
    "chemyx": ("pre_sample_volume_ml", "sample_volume_ml", "sample_rate_ml_min", "return_rate_ml_min",
               "wash_volume_ml", "wash_rate_ml_min", "settle_seconds"),
}


def load_config(path=DEFAULT_CONFIG):
    return check_settings(load_settings(path), EXTRA_SETTINGS)


def observation_times(cfg):
    exp = cfg["experiment"]
    duration, interval = exp["duration_minutes"] * 60.0, exp["interval_minutes"] * 60.0
    slots = [i * interval for i in range(math.ceil(duration / interval))]
    return slots + ([duration] if exp["include_final_observation"] else [])


def sample(rig, cfg, path):
    """One slot. NMR problems come back in the result; hardware errors raise."""
    c, up = cfg["chemyx"], cfg["arduino"]["up_position"]
    rig.pump_transfer("withdraw", c["pre_sample_volume_ml"], c["sample_rate_ml_min"])
    rig.needle_to(0)
    rig.pump_transfer("withdraw", c["sample_volume_ml"], c["sample_rate_ml_min"])
    rig.log.info(f"Settling {c['settle_seconds']:g} s")
    rig.clock.sleep(c["settle_seconds"])
    result = rig.run_nmr(cfg["nmr"]["scans"], path)
    rig.needle_to(up)
    rig.pump_transfer("infuse", c["pre_sample_volume_ml"] + c["sample_volume_ml"], c["return_rate_ml_min"])
    rig.pump_transfer("withdraw", c["wash_volume_ml"], c["wash_rate_ml_min"])
    rig.pump_transfer("infuse", c["wash_volume_ml"], c["wash_rate_ml_min"])
    return result


def analyze_after_run(raw_dir, log, *, timeout=ANALYSIS_TIMEOUT_SECONDS):
    """Post-run only: script 09 in a separate process on the saved spectra. Never raises.

    Returns 09's exit code (0 complete, 1 partial/failed, 2 no usable spectra), or None
    when 09 could not be run to the end.
    """
    later = f'python scripts\\09_analyze_stage2only_scipy.py "{raw_dir}"'
    log.info("=" * 60)
    log.info("POST-RUN ANALYSIS: script 09 (SciPy 8-sigma / width 0.95) in a separate process")
    log.info("The physical run is finished; this takes a few minutes. Ctrl+C skips it.")
    try:
        proc = subprocess.run([sys.executable, str(ANALYSIS_SCRIPT), str(raw_dir), "--timeout-seconds", f"{timeout:g}"],
                              cwd=ANALYSIS_SCRIPT.parents[1], env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout + 600)
    except KeyboardInterrupt:
        log.warning(f"Analysis skipped (Ctrl+C); raw spectra kept. Run it later: {later}")
        return None
    except Exception as exc:
        log.error(f"ANALYSIS FAILED: {type(exc).__name__}: {exc}")
        log.error(f"Raw spectra kept unchanged. Run it later: {later}")
        return None
    output = None
    for line in (proc.stdout + proc.stderr).splitlines():
        if line.strip():
            log.info(f"09: {line}")
        if line.startswith("Output: "):
            output = Path(line[len("Output: "):].strip())
    status = "UNKNOWN"
    try:
        status = json.loads((output / "analysis_summary.json").read_text(encoding="utf-8"))["status"]
    except Exception:
        pass
    plots = output / "stage_2" / "plots" if output else None
    if proc.returncode == 0 and plots:
        ok(log, f"Analysis COMPLETE. Plots: {plots}")
        ok(log, f"Results table: {output / 'stage_2' / 'target_peak_measurements.csv'}")
    elif status == "PARTIAL" and plots and plots.is_dir():
        log.warning(f"Analysis PARTIAL (see {output / 'ANALYSIS_SUMMARY.txt'}). Plots made: {plots}")
    else:
        log.error(f"ANALYSIS FAILED ({status}, 09 exit {proc.returncode}); see {output or 'the 09 lines above'}")
        log.error(f"Raw spectra kept unchanged. Run it later: {later}")
    return proc.returncode


def run(cfg, *, mock, output_dir=None, clock=None, rig_factory=SimpleRig, analyze=None):
    """The physical run. `analyze(raw_dir, log)`, when given, runs only after the rig has closed."""
    clock = clock or (SimulatedClock() if mock else RealClock())
    run_dir = new_run_dir(cfg, "15_stage2_simple", output_dir)
    raw_dir = run_dir / "nmr_raw"
    log = make_logger(run_dir, clock, "15_stage2_simple")
    slots = observation_times(cfg)
    tally = dict(slots=len(slots), saved=0, nmr_failed=0, missed=0)
    status = "FAILED"
    try:
        log.info("=" * 60)
        log.info("15 STAGE 2 SIMPLE RUN" + (" [MOCK: no hardware]" if mock else " [LIVE]"))
        log.info(f"{len(slots)} slots every {cfg['experiment']['interval_minutes']:g} min over "
                 f"{cfg['experiment']['duration_minutes']:g} min; {cfg['nmr']['scans']} scans each")
        log.info(f"Results: {run_dir}")
        log.info("Assuming: HOME set with needle_control.py, Channel 1 syringe empty, NMR idle")
        log.info("=" * 60)
        with rig_factory(cfg, mock=mock, clock=clock, log=log) as rig:
            rig.needle_to(0)  # The saved HOME (moves only if the needle was left elsewhere).
            rig.needle_to(cfg["arduino"]["up_position"])
            start = clock.now()
            for number, due in enumerate(slots, 1):
                if number > 1 and clock.now() - start > due + 1e-6:
                    tally["missed"] += 1
                    log.warning(f"Slot {number}/{len(slots)} MISSED (scheduled {clock_text(due)}); continuing")
                    continue
                if clock.now() - start < due:
                    log.info(f"Next slot {number}/{len(slots)} at {clock_text(due)}; "
                             f"waiting {due - (clock.now() - start):.0f} s")
                    while clock.now() - start < due:
                        clock.sleep(due - (clock.now() - start))
                log.info(f"--- Slot {number}/{len(slots)} (scheduled {clock_text(due)}) ---")
                result = sample(rig, cfg, raw_dir / f"stage_2_{number:04d}.dx")
                if result["status"] == "NMR_SAVED":
                    tally["saved"] += 1
                    ok(log, f"Slot {number}/{len(slots)} done; spectrum saved")
                else:
                    tally["nmr_failed"] += 1
                    log.warning(f"Slot {number}/{len(slots)} done; NMR failed, spectrum skipped "
                                f"({str(result.get('error') or result['status'])[:300]})")
            status = "COMPLETE"
    except KeyboardInterrupt:
        status = "INTERRUPTED"
    except HardwareError:
        status = "STOPPED"  # The rig already logged the cause and stopped everything.
    except Exception as exc:
        log.error(f"FAILED: {type(exc).__name__}: {exc}")
    finally:
        log.info("=" * 60)
        line = (f"{status} | slots {tally['slots']} | spectra saved {tally['saved']} | "
                f"NMR failed {tally['nmr_failed']} | missed {tally['missed']}")
        if status == "COMPLETE" and tally["saved"] == tally["slots"]:
            ok(log, line)
        elif status == "COMPLETE":
            log.warning(line)
        else:
            log.error(line)
        log.info(f"Spectra: {raw_dir}")
        if tally["saved"] and analyze is not None and status != "INTERRUPTED":
            try:
                analyze(raw_dir, log)  # The rig's with-block has already returned HOME and closed the ports.
            except BaseException as exc:  # Never let analysis replace the physical result.
                log.error(f"ANALYSIS FAILED: {type(exc).__name__}: {exc}; raw spectra kept")
        elif tally["saved"]:
            log.info(f'Analysis later: python scripts\\09_analyze_stage2only_scipy.py "{raw_dir}"')
        close_logger(log)
    return status, tally


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
        print(f"15 configuration error: {type(exc).__name__}: {exc}", flush=True)
        return 2
    try:
        status, _ = run(cfg, mock=args.mock, output_dir=args.output_dir, analyze=analyze_after_run)
    except Exception as exc:  # Only before the log exists, e.g. the output folder already exists.
        print(f"15 could not start: {type(exc).__name__}: {exc}", flush=True)
        return 1
    return {"COMPLETE": 0, "INTERRUPTED": 130}.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
