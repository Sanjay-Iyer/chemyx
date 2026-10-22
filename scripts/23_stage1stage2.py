"""23 (was 19): two instruments, 48 h Stage 1 -> one Channel 2 dose -> 6 h Stage 2.

Chemyx + NMR only: the needle stays in the flask and no Arduino is used. Every
sample is 24's cycle (CH1 withdraw -> NMR -> CH1 infuse), using 24's rig and
cycle code unchanged. Between the stages Channel 2 infuses one dose over the
same serial connection. One startup confirmation. 23 refuses to start if the
Channel 1 loading cannot cover every sample.

Live review plots come from one separate, instrument-free process per stage
(14's review worker); it is started before the serial port opens and never
controls, delays or ends the run. Each stage folder holds nmr_raw/*.dx and
analysis/ (plots + peak_areas.csv); the run folder holds log.txt.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import itertools
import math
import os
from pathlib import Path
import subprocess
import sys

import _bootstrap  # noqa: F401
import yaml

from chemyx_lab import config
from chemyx_lab.workflows.simple_rig import (PumpCommandError, RealClock, SimulatedClock, SyringePump, clock_text,
    close_logger, load_settings, make_logger, new_run_dir, ok)


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage2only = load_script("stage2only_24_for_23", "24_stage2only.py")
DEFAULT_CONFIG = Path("configs/experiments/23_stage1stage2.yaml")
PREFIX = "23_stage1stage2"
STAGES = ("stage_1", "stage_2")
REQUIRED = {
    "chemyx": ("channel", "withdraw_volume_ml", "infuse_volume_ml", "withdraw_rate_ml_min", "infuse_rate_ml_min",
               "initial_volume_ml", "syringe_diameter_mm", "syringe_capacity_ml", "port", "baud_rate",
               "timeout_seconds", "response_delay_seconds", "extra_seconds"),
    "channel_2": ("channel", "volume_ml", "rate_ml_min", "initial_volume_ml", "syringe_diameter_mm",
                  "syringe_capacity_ml"),
    "stages": STAGES,
    "nmr": ("host", "port", "scheme", "http_timeout_seconds", "acquisition_timeout_seconds", "poll_seconds",
            "scans", "gain", "auto_gain", "center_ppm", "width_ppm"),
    "review": ("enabled", "processing_config", "target_peak"),
    "output": ("root_dir",),
}


# ------------------------------------------------------------------ settings
def _number(value, name, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value < 0 or value == 0 and not zero:
        raise ValueError(f"{name} must be a finite {'nonnegative' if zero else 'positive'} number")
    return float(value)


def observation_times(stage):
    """Script 21's slots: 0, interval, ... below the duration, plus the endpoint when asked."""
    duration, interval = stage["duration_minutes"] * 60.0, stage["interval_minutes"] * 60.0
    slots = [i * interval for i in range(math.ceil(duration / interval))]
    return slots + ([duration] if stage["include_final_observation"] else [])


def syringe_plan(cfg):
    """Walk every planned sample; fail before any port opens if a syringe limit would be crossed."""
    c, d = cfg["chemyx"], cfg["channel_2"]
    withdraw, infuse, capacity = float(c["withdraw_volume_ml"]), float(c["infuse_volume_ml"]), float(c["syringe_capacity_ml"])
    cycles = sum(len(observation_times(cfg["stages"][name])) for name in STAGES)
    contents = peak = float(c["initial_volume_ml"])
    for number in range(1, cycles + 1):
        peak = max(peak, contents + withdraw)
        if contents + withdraw > capacity + 1e-9:
            raise ValueError(f"Channel 1 would hold {contents + withdraw:.2f} mL at sample {number} of {cycles} "
                             f"(capacity {capacity:g} mL): lower chemyx.initial_volume_ml or withdraw_volume_ml")
        contents += withdraw - infuse
        if contents < -1e-9:
            need = cycles * (infuse - withdraw)
            raise ValueError(f"Channel 1 runs out at sample {number} of {cycles}: {cycles} samples x "
                             f"{infuse - withdraw:+.3f} mL need at least {need:.2f} mL loaded, but "
                             f"chemyx.initial_volume_ml is {float(c['initial_volume_ml']):g} mL")
    return dict(cycles=cycles, ch1_left=max(0.0, contents), ch1_peak=peak, ch1_net=cycles * (infuse - withdraw),
                ch2_left=float(d["initial_volume_ml"]) - float(d["volume_ml"]))


def load_config(path=DEFAULT_CONFIG):
    cfg = load_settings(path)
    for section, keys in REQUIRED.items():
        for key in keys:
            if not isinstance(cfg.get(section), dict) or key not in cfg[section]:
                raise ValueError(f"Missing setting: {section}.{key}")
    c, n = cfg["chemyx"], cfg["nmr"]
    if c["channel"] != 1:
        raise ValueError("23 samples with chemyx.channel: 1")
    for key in ("withdraw_volume_ml", "infuse_volume_ml", "withdraw_rate_ml_min", "infuse_rate_ml_min",
                "syringe_capacity_ml", "timeout_seconds"):
        _number(c[key], f"chemyx.{key}")
    for key in ("initial_volume_ml", "extra_seconds", "response_delay_seconds"):
        _number(c[key], f"chemyx.{key}", zero=True)
    low, high = config.rate_limits(0)
    for name in ("withdraw_rate_ml_min", "infuse_rate_ml_min"):
        if not low <= float(c[name]) <= high:
            raise ValueError(f"chemyx.{name} is outside the pump's range")
    if not config.DIAMETER_MIN <= float(c["syringe_diameter_mm"]) <= config.DIAMETER_MAX:
        raise ValueError("chemyx.syringe_diameter_mm is outside the pump's range")
    if type(n["scans"]) is not int or n["scans"] < 1:
        raise ValueError("nmr.scans must be a positive whole number")
    for key in ("http_timeout_seconds", "acquisition_timeout_seconds", "poll_seconds"):
        _number(n[key], f"nmr.{key}")
    check_dose_and_stages(cfg)
    syringe_plan(cfg)
    return cfg


def check_dose_and_stages(cfg):
    """Channel 2, both stages and live review; shared with 20 (three instruments)."""
    for section in ("channel_2", "stages", "review"):
        for key in REQUIRED[section]:
            if not isinstance(cfg.get(section), dict) or key not in cfg[section]:
                raise ValueError(f"Missing setting: {section}.{key}")
    d = cfg["channel_2"]
    if d["channel"] != 2:
        raise ValueError("The boundary dose uses channel_2.channel: 2")
    for key in ("volume_ml", "rate_ml_min", "syringe_capacity_ml"):
        _number(d[key], f"channel_2.{key}")
    _number(d["initial_volume_ml"], "channel_2.initial_volume_ml", zero=True)
    low, high = config.rate_limits(0)
    if not low <= float(d["rate_ml_min"]) <= high:
        raise ValueError("channel_2.rate_ml_min is outside the pump's range")
    if not config.DIAMETER_MIN <= float(d["syringe_diameter_mm"]) <= config.DIAMETER_MAX:
        raise ValueError("channel_2.syringe_diameter_mm is outside the pump's range")
    if not float(d["volume_ml"]) <= float(d["initial_volume_ml"]) <= float(d["syringe_capacity_ml"]):
        raise ValueError("channel_2.initial_volume_ml must cover volume_ml and fit the syringe")
    for name in STAGES:
        stage = cfg["stages"][name]
        if not isinstance(stage, dict) or not {"duration_minutes", "interval_minutes", "include_final_observation"} <= set(stage):
            raise ValueError(f"stages.{name} needs duration_minutes, interval_minutes and include_final_observation")
        duration = _number(stage["duration_minutes"], f"stages.{name}.duration_minutes")
        if duration / _number(stage["interval_minutes"], f"stages.{name}.interval_minutes") > 10000 \
                or type(stage["include_final_observation"]) is not bool:
            raise ValueError(f"Invalid or excessive schedule for stages.{name}")
    if type(cfg["review"]["enabled"]) is not bool:
        raise ValueError("review.enabled must be true or false")


def hours(minutes):
    return f"{minutes / 60:g} h" if minutes >= 60 else f"{minutes:g} min"


def summary_lines(cfg):
    c, d, n, s1, s2 = cfg["chemyx"], cfg["channel_2"], cfg["nmr"], cfg["stages"]["stage_1"], cfg["stages"]["stage_2"]
    plan, t1, t2 = syringe_plan(cfg), observation_times(s1), observation_times(s2)
    net = float(c["infuse_volume_ml"]) - float(c["withdraw_volume_ml"])
    return [
        "23 TWO-INSTRUMENT STAGE 1 + STAGE 2 RUN",
        "Arduino: NOT USED | Needle: stationary in flask",
        f"Every sample: CH1 withdraw {c['withdraw_volume_ml']:g} mL -> NMR {n['scans']} scans -> CH1 infuse "
        f"{c['infuse_volume_ml']:g} mL (flow {c['withdraw_rate_ml_min']:g} / {c['infuse_rate_ml_min']:g} mL/min; "
        f"net {net:+.3f} mL per sample)",
        f"Stage 1: {hours(s1['duration_minutes'])}, every {hours(s1['interval_minutes'])}: {len(t1)} samples "
        f"(0 to {hours(t1[-1] / 60)}); the window then runs to {hours(s1['duration_minutes'])}",
        f"Then CH2 dose: {d['volume_ml']:g} mL at {d['rate_ml_min']:g} mL/min, once",
        f"Stage 2 (starts after the dose): {hours(s2['duration_minutes'])}, every {hours(s2['interval_minutes'])}: "
        f"{len(t2)} samples (0 to {hours(t2[-1] / 60)})",
        f"CH1 loaded (operator): {c['initial_volume_ml']:g} mL; {plan['cycles']} samples deliver {plan['ch1_net']:+.3f} mL; "
        f"about {plan['ch1_left']:.2f} mL left at the end (peak {plan['ch1_peak']:.2f} / {c['syringe_capacity_ml']:g} mL)",
        f"CH2 loaded (operator): {d['initial_volume_ml']:g} mL; {plan['ch2_left']:.2f} mL left after the dose",
        "Live review plots: " + ("stage_1/analysis and stage_2/analysis (separate process; never affects the run)"
                                 if cfg["review"]["enabled"] else "OFF (review.enabled: false)"),
        "Confirm: needle and CH2 line secured in flask, NMR idle, sample-return path open, both syringes loaded",
        "as above, and flask capacity for the planned net delivery and dose. Operator checks physical feasibility.",
    ]


def results_dir(cfg, output_dir, prefix=PREFIX):
    if output_dir is not None:
        return new_run_dir(cfg, prefix, output_dir)
    root = config.resolve_repo_path(cfg["output"]["root_dir"])
    stem = f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}"
    for suffix in itertools.count():
        try:
            return new_run_dir(cfg, prefix, root / (stem if suffix == 0 else f"{stem}_{suffix}"))
        except FileExistsError:
            continue


# ----------------------------------------------------------------------- rig
def channel_2_settings(cfg):
    d = cfg["channel_2"]
    return {"chemyx": dict(cfg["chemyx"], channel=int(d["channel"]), syringe_diameter_mm=d["syringe_diameter_mm"],
                           syringe_capacity_ml=d["syringe_capacity_ml"])}


class StageRig(stage2only.TwoInstrumentRig):
    """24's CH1 + NMR rig, plus Channel 2 on the same Chemyx connection for the boundary dose only."""

    def __init__(self, cfg, *, mock, clock, log):
        super().__init__(cfg, mock=mock, clock=clock, log=log)
        self.ch2 = SyringePump(channel_2_settings(cfg), mock=mock, clock=clock, log=log)
        self.ch2.contents_ml = float(cfg["channel_2"]["initial_volume_ml"])
        self.ch2_ready = self.dose_pending = False
        self.completed = []

    def __enter__(self):
        super().__enter__()
        self.ch2.pump = self.pump.pump  # One serial port; every CH2 command carries channel 2.
        self.ch2_ready = self.ch2.stop()  # Nothing should be running; any reply is fine.
        return self

    def transfer(self, action):
        super().transfer(action)
        self.completed.append(action)

    def dose(self):
        d = self.cfg["channel_2"]
        self.dose_pending = True
        self.ch2.transfer("infuse", d["volume_ml"], d["rate_ml_min"])
        self.dose_pending = False
        ok(self.log, f"CHEMYX CH2: Dose {d['volume_ml']:g} mL - COMPLETE")

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and self.pump.pump.is_connected:
            self.ch2.stop()  # Before 24's cleanup closes the shared port.
            if self.dose_pending:
                self.log.error("Channel 2 dose interrupted; CH2 STOP sent. The amount added is uncertain and the "
                               "dose is never repeated automatically: inspect before another run.")
        return super().__exit__(exc_type, exc, tb)


# -------------------------------------------------------------- live review
def start_review(stage_dir, raw_dir, cfg, log):
    """One review child per stage (14's worker): reads raw files only; never waited on or read back."""
    r = cfg["review"]
    settings = {"experiment": {"name": stage_dir.parent.name}, "analysis": {
        "dataset_display_name": f"{stage_dir.parent.name} {stage_dir.name}", "phase_method": "acme",
        "processing_config": r["processing_config"], "target_peak": dict(r["target_peak"]),
        "save_spectrum_plots": True, "save_area_vs_time": True, "every_n_acquisitions": 1}}
    try:
        path = stage_dir / "review_config.yaml"
        path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
        command = [sys.executable, "-m", "chemyx_lab.analysis.stage2_demo_analysis", "--run-dir", str(stage_dir),
                   "--config", str(path), "--raw-dir", str(raw_dir), "--watch", "--parent-pid", str(os.getpid())]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.IDLE_PRIORITY_CLASS} if os.name == "nt" else {}
        env = dict(os.environ, MPLBACKEND="Agg", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
        with (stage_dir / "review.log").open("a", encoding="utf-8") as output:
            subprocess.Popen(command, cwd=config.REPO_ROOT, stdin=subprocess.DEVNULL, stdout=output,
                             stderr=subprocess.STDOUT, close_fds=True, env=env, **options)
        log.info(f"Review plots ({stage_dir.name}): {stage_dir / 'analysis'}, updated after each saved spectrum")
    except Exception as exc:
        log.warning(f"Review plots ({stage_dir.name}) NOT started: {type(exc).__name__}: {exc}; the run continues")


# ---------------------------------------------------------------------- run
def run_stage(rig, cfg, name, stage_dir, clock, log, tally, sample=None):
    """One stage window; `sample(rig, cfg, path, number)` defaults to 24's cycle (20 passes 21's)."""
    sample = sample or stage2only.sample
    stage, counts = cfg["stages"][name], tally[name]
    slots = observation_times(stage)
    start = clock.now()
    log.info("=" * 60)
    log.info(f"{name.upper()} STARTED: {len(slots)} samples over {hours(stage['duration_minutes'])}")
    for number, due in enumerate(slots, 1):
        # Same fixed-start schedule and missed-slot behaviour as 21 and 24.
        if number > 1 and clock.now() - start > due + 1e-6:
            counts["missed"] += 1
            log.warning(f"{name} sample {number}/{len(slots)} MISSED (scheduled {clock_text(due)}); continuing")
            continue
        if clock.now() - start < due:
            log.info(f"{name}: next sample {number}/{len(slots)} at {clock_text(due)}")
            while clock.now() - start < due:
                clock.sleep(due - (clock.now() - start))
        log.info(f"--- {name} sample {number}/{len(slots)} (scheduled {clock_text(due)}) ---")
        result = sample(rig, cfg, stage_dir / "nmr_raw" / f"{name}_{number:04d}.dx", number)
        counts["saved" if result["status"] == "NMR_SAVED" else "nmr_failed"] += 1
    end = stage["duration_minutes"] * 60.0
    if clock.now() - start < end:
        log.info(f"{name}: last sample done; the stage window ends at {clock_text(end)}")
        while clock.now() - start < end:
            clock.sleep(end - (clock.now() - start))
    ok(log, f"{name.upper()} COMPLETE | {counts['saved']}/{counts['slots']} spectra saved | "
            f"NMR failed {counts['nmr_failed']} | missed {counts['missed']}")


def run(cfg, *, mock, output_dir=None, clock=None, rig_factory=StageRig, confirm=False, input_fn=input,
        review_launcher=None):
    """The physical run. `review_launcher(stage_dir, raw_dir, cfg, log)` starts live plots when review is enabled."""
    clock = clock or (SimulatedClock() if mock else RealClock())
    run_dir = results_dir(cfg, output_dir)
    log = make_logger(run_dir, clock, PREFIX)
    tally = {name: dict(slots=len(observation_times(cfg["stages"][name])), saved=0, nmr_failed=0, missed=0)
             for name in STAGES}
    tally["dose"] = "NOT ATTEMPTED"
    status = "FAILED"
    try:
        for line in summary_lines(cfg):
            log.info(line)
        log.info("Mode: " + ("MOCK (no hardware)" if mock else "LIVE"))
        log.info(f"Results: {run_dir}")
        if confirm and input_fn("Confirm rig is ready [y/N]: ").strip().lower() not in {"y", "yes"}:
            status = "DECLINED"
            return status, tally
        stage_dirs = {name: run_dir / name for name in STAGES}
        for name, folder in stage_dirs.items():
            (folder / "nmr_raw").mkdir(parents=True)
            if review_launcher is not None and cfg["review"]["enabled"]:
                review_launcher(folder, folder / "nmr_raw", cfg, log)  # Before the serial port opens.
        with rig_factory(cfg, mock=mock, clock=clock, log=log) as rig:
            run_stage(rig, cfg, "stage_1", stage_dirs["stage_1"], clock, log, tally)
            log.info("=" * 60)
            log.info(f"CHANNEL 2 DOSE: {cfg['channel_2']['volume_ml']:g} mL at {cfg['channel_2']['rate_ml_min']:g} mL/min")
            tally["dose"] = "STARTED"
            rig.dose()
            tally["dose"] = "COMPLETE"
            run_stage(rig, cfg, "stage_2", stage_dirs["stage_2"], clock, log, tally)
            status = "COMPLETE"
    except KeyboardInterrupt:
        status = "INTERRUPTED"
        log.error("Interrupted by operator (Ctrl+C)")
    except (PumpCommandError, stage2only.NmrRunError) as exc:
        status = "STOPPED"
        log.error(str(exc))
    except Exception as exc:
        log.error(f"FAILED: {type(exc).__name__}: {exc}")
    finally:
        if tally["dose"] == "STARTED":
            tally["dose"] = "UNCERTAIN"
        log.info("=" * 60)
        s1, s2 = tally["stage_1"], tally["stage_2"]
        line = (f"{status} | stage_1: {s1['saved']}/{s1['slots']} saved, NMR failed {s1['nmr_failed']}, missed "
                f"{s1['missed']} | CH2 dose: {tally['dose']} | stage_2: {s2['saved']}/{s2['slots']} saved, "
                f"NMR failed {s2['nmr_failed']}, missed {s2['missed']}")
        if status == "COMPLETE" and s1["saved"] == s1["slots"] and s2["saved"] == s2["slots"]:
            ok(log, line)
        elif status == "COMPLETE":
            log.warning(line)
        else:
            log.error(line)
        log.info(f"Results preserved: {run_dir}")
        if status != "DECLINED" and review_launcher is not None and cfg["review"]["enabled"]:
            log.info("Review plots: stage_1\\analysis and stage_2\\analysis (the last update finishes shortly after this run)")
        close_logger(log)
    return status, tally


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
        print(f"23 configuration error: {type(exc).__name__}: {exc}", flush=True)
        return 2
    try:
        status, _ = run(cfg, mock=args.mock, output_dir=args.output_dir, confirm=True, review_launcher=start_review)
    except Exception as exc:
        print(f"23 could not start: {type(exc).__name__}: {exc}", flush=True)
        return 1
    return {"COMPLETE": 0, "DECLINED": 3, "INTERRUPTED": 130}.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
