"""20: three instruments, 48 h Stage 1 -> one Channel 2 dose -> 6 h Stage 2 (simple path).

Arduino needle + Chemyx + NMR on 21's no-lockout rig, with 21's sampling recipe
unchanged: withdraw 8 mL (needle UP), needle DOWN to HOME, withdraw 5 mL, settle,
NMR scan, needle UP, return 13 mL, wash 5 mL out/back. Between the stages, as in
14: needle to HOME, Channel 2 infuses one dose, needle back UP. --live starts
immediately (no prompt), like 21 and 22. On any error or Ctrl+C the rig STOPs,
empties Channel 1 when its contents are known, returns the needle to HOME and
closes the ports (21's cleanup); the dose is never repeated automatically.

Live review plots as in 23: one separate, instrument-free process per stage,
started before the ports open, never controlling the run. Each stage folder
holds nmr_raw/*.dx and analysis/; the run folder holds log.txt.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab.workflows.simple_rig import (HardwareError, RealClock, SimpleRig, SimulatedClock, SyringePump,
    close_logger, make_logger, new_run_dir, ok)
from chemyx_lab.workflows.simple_review import begin_review


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage2only = load_script("stage2only_21_for_20", "21_stage2only.py")       # Rig settings + sampling recipe.
stages = load_script("stage1stage2_23_for_20", "23_stage1stage2.py")       # Stage loop, dose settings, review.
DEFAULT_CONFIG = Path("configs/experiments/20_stage1stage2.yaml")
PREFIX = "20_stage1stage2"
STAGES = stages.STAGES


def load_config(path=DEFAULT_CONFIG):
    cfg = stage2only.load_config(path)  # 21's checks: ports, travel range, syringe, recipe, NMR.
    if cfg["chemyx"]["channel"] != 1:
        raise ValueError("20 samples with chemyx.channel: 1")
    c = cfg["chemyx"]
    if float(c["pre_sample_volume_ml"]) + float(c["sample_volume_ml"]) > float(c["syringe_capacity_ml"]):
        raise ValueError("pre_sample_volume_ml + sample_volume_ml exceed the Channel 1 syringe")
    stages.check_dose_and_stages(cfg)
    return cfg


def sample(rig, cfg, path, number):
    return stage2only.sample(rig, cfg, path)  # 21's recipe, exactly as in the Stage 2 only run.


class StageRig(SimpleRig):
    """21's Arduino + CH1 + NMR rig, plus Channel 2 on the same Chemyx connection for the boundary dose only."""

    def __init__(self, cfg, *, mock, clock, log):
        super().__init__(cfg, mock=mock, clock=clock, log=log)
        self.dose_settings = cfg["channel_2"]
        self.ch2 = SyringePump(stages.channel_2_settings(cfg), mock=mock, clock=clock, log=log)
        self.ch2.contents_ml = float(self.dose_settings["initial_volume_ml"])
        self.dose_pending = False

    def __enter__(self):
        super().__enter__()
        self.ch2.pump = self.pump.pump  # One serial port; every CH2 command carries channel 2.
        self.ch2.stop()  # Nothing should be running; any reply is fine.
        return self

    def dose(self):
        d = self.dose_settings
        self.needle_to(0)  # HOME, as in 14.
        self.dose_pending = True
        self.ch2.transfer("infuse", d["volume_ml"], d["rate_ml_min"])
        self.dose_pending = False
        ok(self.log, f"Chemyx CH2: dose {d['volume_ml']:g} mL complete")
        self.needle_to(self.up_position)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and self.pump.pump.is_connected:
            self.ch2.stop()  # Before 21's cleanup closes the shared port.
            if self.dose_pending:
                self.log.error("Channel 2 dose interrupted; CH2 STOP sent. The amount added is uncertain and the "
                               "dose is never repeated automatically: inspect before another run.")
        return super().__exit__(exc_type, exc, tb)


def run(cfg, *, mock, output_dir=None, clock=None, rig_factory=StageRig, review_launcher=None):
    """The physical run. `review_launcher(stage_dir, raw_dir, cfg, log)` starts live plots when review is enabled."""
    clock = clock or (SimulatedClock() if mock else RealClock())
    run_dir = new_run_dir(cfg, PREFIX, output_dir)
    log = make_logger(run_dir, clock, PREFIX)
    tally = {name: dict(slots=len(stages.observation_times(cfg["stages"][name])), saved=0, nmr_failed=0, missed=0)
             for name in STAGES}
    tally["dose"] = "NOT ATTEMPTED"
    status = "FAILED"
    c, d, s1, s2 = cfg["chemyx"], cfg["channel_2"], cfg["stages"]["stage_1"], cfg["stages"]["stage_2"]
    t1, t2 = stages.observation_times(s1), stages.observation_times(s2)
    try:
        log.info("=" * 60)
        log.info("20 THREE-INSTRUMENT STAGE 1 + STAGE 2 RUN" + (" [MOCK: no hardware]" if mock else " [LIVE]"))
        log.info(f"Stage 1: {stages.hours(s1['duration_minutes'])}, every {stages.hours(s1['interval_minutes'])}: "
                 f"{len(t1)} samples (0 to {stages.hours(t1[-1] / 60)}); the window then runs to "
                 f"{stages.hours(s1['duration_minutes'])}")
        log.info(f"Then CH2 dose: needle to HOME, {d['volume_ml']:g} mL at {d['rate_ml_min']:g} mL/min, needle UP")
        log.info(f"Stage 2 (starts after the dose): {stages.hours(s2['duration_minutes'])}, every "
                 f"{stages.hours(s2['interval_minutes'])}: {len(t2)} samples (0 to {stages.hours(t2[-1] / 60)})")
        log.info(f"Each sample (21's recipe): withdraw {c['pre_sample_volume_ml']:g} mL (needle UP), needle to HOME, "
                 f"withdraw {c['sample_volume_ml']:g} mL, settle {c['settle_seconds']:g} s, {cfg['nmr']['scans']}-scan NMR, "
                 f"needle UP, return {c['pre_sample_volume_ml'] + c['sample_volume_ml']:g} mL, wash {c['wash_volume_ml']:g} mL")
        log.info("Live review plots: " + ("stage_1/analysis and stage_2/analysis (separate process; never affects the run)"
                                          if cfg["review"]["enabled"] and review_launcher else "OFF"))
        log.info(f"Results: {run_dir}")
        log.info(f"Assuming: HOME set with needle_control.py, Channel 1 empty, Channel 2 loaded with "
                 f"{d['initial_volume_ml']:g} mL, NMR idle")
        log.info("=" * 60)
        stage_dirs = {name: run_dir / name for name in STAGES}
        for name, folder in stage_dirs.items():
            (folder / "nmr_raw").mkdir(parents=True)
            begin_review(folder, folder / "nmr_raw", cfg, log, review_launcher)  # Before the serial ports open.
        with rig_factory(cfg, mock=mock, clock=clock, log=log) as rig:
            rig.needle_to(0)  # The saved HOME (moves only if the needle was left elsewhere).
            rig.needle_to(cfg["arduino"]["up_position"])
            stages.run_stage(rig, cfg, "stage_1", stage_dirs["stage_1"], clock, log, tally, sample=sample)
            log.info("=" * 60)
            log.info(f"CHANNEL 2 DOSE: {d['volume_ml']:g} mL at {d['rate_ml_min']:g} mL/min")
            tally["dose"] = "STARTED"
            rig.dose()
            tally["dose"] = "COMPLETE"
            stages.run_stage(rig, cfg, "stage_2", stage_dirs["stage_2"], clock, log, tally, sample=sample)
            status = "COMPLETE"
    except KeyboardInterrupt:
        status = "INTERRUPTED"
    except HardwareError:
        status = "STOPPED"  # The rig already logged the cause and stopped everything.
    except Exception as exc:
        log.error(f"FAILED: {type(exc).__name__}: {exc}")
    finally:
        if tally["dose"] == "STARTED":
            tally["dose"] = "UNCERTAIN"
        log.info("=" * 60)
        r1, r2 = tally["stage_1"], tally["stage_2"]
        line = (f"{status} | stage_1: {r1['saved']}/{r1['slots']} saved, NMR failed {r1['nmr_failed']}, missed "
                f"{r1['missed']} | CH2 dose: {tally['dose']} | stage_2: {r2['saved']}/{r2['slots']} saved, "
                f"NMR failed {r2['nmr_failed']}, missed {r2['missed']}")
        if status == "COMPLETE" and r1["saved"] == r1["slots"] and r2["saved"] == r2["slots"]:
            ok(log, line)
        elif status == "COMPLETE":
            log.warning(line)
        else:
            log.error(line)
        log.info(f"Results: {run_dir}")
        if review_launcher is not None and cfg["review"]["enabled"]:
            log.info("Review plots: stage_1\\analysis and stage_2\\analysis (the last update finishes shortly after this run)")
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
        print(f"20 configuration error: {type(exc).__name__}: {exc}", flush=True)
        return 2
    try:
        status, _ = run(cfg, mock=args.mock, output_dir=args.output_dir, review_launcher=stages.start_review)
    except Exception as exc:  # Only before the log exists, e.g. the output folder already exists.
        print(f"20 could not start: {type(exc).__name__}: {exc}", flush=True)
        return 1
    return {"COMPLETE": 0, "INTERRUPTED": 130}.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
