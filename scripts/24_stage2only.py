"""24 (was 17): two instruments, Stage 2 only. Stationary needle, CH1 WITHDRAW -> NMR -> INFUSE.

One startup confirmation. No Arduino, Stage 1, Channel 2, processing or saved
control state. Uses Script 21's SyringePump and standalone 04/05 NmrScanner.
Only log.txt and raw observation_001.dx, ... are written in each new run folder.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import math
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.workflows.simple_rig import (
    NmrScanner, PumpCommandError, RealClock, SimulatedClock, SyringePump,
    clock_text, close_logger, load_settings, make_logger, new_run_dir, ok,
)
from chemyx_lab.workflows.si6_fixed_time_nmr import running_state

DEFAULT_CONFIG = Path("configs/experiments/24_stage2only.yaml")


class NmrRunError(RuntimeError):
    """NMR idle could not be confirmed after a failed acquisition."""


def load_config(path=DEFAULT_CONFIG):
    cfg = load_settings(path)
    required = {
        "chemyx": ("channel", "withdraw_volume_ml", "infuse_volume_ml", "withdraw_rate_ml_min",
                   "infuse_rate_ml_min", "initial_volume_ml", "syringe_diameter_mm", "syringe_capacity_ml",
                   "port", "baud_rate", "timeout_seconds", "response_delay_seconds", "extra_seconds"),
        "experiment": ("observation_count", "interval_minutes"),
        "nmr": ("host", "port", "scheme", "http_timeout_seconds", "acquisition_timeout_seconds",
                "poll_seconds", "scans", "gain", "auto_gain", "center_ppm", "width_ppm"),
        "output": ("root_dir",),
    }
    for section, keys in required.items():
        for key in keys:
            if not isinstance(cfg.get(section), dict) or key not in cfg[section]:
                raise ValueError(f"Missing setting: {section}.{key}")
    c, n, exp = cfg["chemyx"], cfg["nmr"], cfg["experiment"]
    if type(c["channel"]) is not int or c["channel"] != 1:
        raise ValueError("Script 24 requires chemyx.channel: 1")
    for section, key in ((exp, "observation_count"), (n, "scans")):
        if type(section[key]) is not int or section[key] < 1:
            raise ValueError(f"{key} must be a positive whole number")
    for section, keys in (
        (exp, ("interval_minutes",)),
        (c, ("withdraw_volume_ml", "infuse_volume_ml", "withdraw_rate_ml_min", "infuse_rate_ml_min",
             "syringe_capacity_ml", "timeout_seconds")),
        (n, ("http_timeout_seconds", "acquisition_timeout_seconds", "poll_seconds")),
    ):
        for key in keys:
            if not math.isfinite(float(section[key])) or float(section[key]) <= 0:
                raise ValueError(f"{key} must be finite and positive")
    for key in ("initial_volume_ml", "extra_seconds", "response_delay_seconds"):
        if not math.isfinite(float(c[key])) or float(c[key]) < 0:
            raise ValueError(f"chemyx.{key} must be finite and nonnegative")
    if not config.DIAMETER_MIN <= float(c["syringe_diameter_mm"]) <= config.DIAMETER_MAX:
        raise ValueError("chemyx.syringe_diameter_mm is outside the pump's range")
    if float(c["initial_volume_ml"]) > float(c["syringe_capacity_ml"]):
        raise ValueError("chemyx.initial_volume_ml exceeds the syringe capacity")
    low, high = config.rate_limits(0)
    for key in ("withdraw_rate_ml_min", "infuse_rate_ml_min"):
        if not low <= float(c[key]) <= high:
            raise ValueError(f"chemyx.{key} is outside the pump's range")
    return cfg


def observation_times(cfg):
    exp = cfg["experiment"]
    return [i * float(exp["interval_minutes"]) * 60 for i in range(exp["observation_count"])]


def summary_lines(cfg):
    c, n, exp = cfg["chemyx"], cfg["nmr"], cfg["experiment"]
    net = float(c["infuse_volume_ml"]) - float(c["withdraw_volume_ml"])
    return [
        "24 TWO-INSTRUMENT STAGE 2 ONLY RUN",
        "Arduino: NOT USED | Needle: Stationary in flask",
        f"Chemyx: Channel 1 | Withdraw: {c['withdraw_volume_ml']:g} mL | Infuse: {c['infuse_volume_ml']:g} mL",
        f"Flow: withdraw {c['withdraw_rate_ml_min']:g}, infuse {c['infuse_rate_ml_min']:g} mL/min",
        f"Initial syringe liquid (operator prepared): {c['initial_volume_ml']:g} mL / {c['syringe_capacity_ml']:g} mL",
        f"Net delivery per cycle (infuse - withdraw): {net:+.3f} mL",
        f"Total planned net delivery: {net * exp['observation_count']:+.3f} mL "
        "(positive = additional liquid delivered; no automatic replenishment)",
        f"NMR: {n['scans']} scans | Observations: {exp['observation_count']} | Interval: {exp['interval_minutes']:g} minutes",
        f"Scheduled observations: 0 through {observation_times(cfg)[-1] / 60:g} minutes; late slots skipped",
        "Confirm: needle secured in flask, NMR idle, sample-return path open, sufficient syringe liquid/headroom,",
        "and adequate flask capacity for the planned net delivery. Operator checks physical feasibility.",
    ]


class TwoInstrumentRig:
    """Only pump/NMR; temporary sample and uncertainty flags live in this process."""

    def __init__(self, cfg, *, mock, clock, log):
        self.cfg, self.log = cfg, log
        self.pump = SyringePump(cfg, mock=mock, clock=clock, log=log)
        self.pump.contents_ml = float(cfg["chemyx"]["initial_volume_ml"])
        self.nmr = NmrScanner(cfg, mock=mock, clock=clock, log=log)
        self.sample_pending = self.pump_uncertain = self.nmr_pending = False
        self.nmr_stop_attempted = False

    def __enter__(self):
        try:
            self.pump.connect()
        except BaseException as exc:
            if self.pump.pump.is_connected:
                self.pump.stop()
            self.pump.close()
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise PumpCommandError(f"Chemyx connection failed: {type(exc).__name__}: {exc}") from exc
        return self

    def transfer(self, action):
        c = self.cfg["chemyx"]
        try:
            self.pump.transfer(action, c[f"{action}_volume_ml"], c[f"{action}_rate_ml_min"])
        except BaseException:
            # Do not estimate an interrupted displacement or automatically retry it.
            self.pump_uncertain = True
            raise
        ok(self.log, f"CHEMYX CH1: {action.capitalize()} {c[f'{action}_volume_ml']:g} mL - COMPLETE")

    def return_sample(self):
        if self.sample_pending and not self.pump_uncertain:
            self.transfer("infuse")
            self.sample_pending = False

    def stop_nmr_and_confirm_idle(self):
        """Error path only: existing iFlow cancel and legacy wait; no new START."""
        self.nmr_stop_attempted = True
        client = self.nmr.client()
        try:
            client.cancel_iflow_experiment()
            self.log.warning("NMR: STOP/cancel sent")
        except Exception as exc:
            self.log.warning(f"NMR: STOP/cancel failed: {type(exc).__name__}: {exc}")
        try:
            final = client.wait_for_idle(status_getter=client.iflow_experiment_status,
                                        poll_seconds=self.cfg["nmr"]["poll_seconds"],
                                        max_wait_seconds=self.cfg["nmr"]["acquisition_timeout_seconds"])
            # The existing positive-idle parser is used only after an error.
            # An empty/unknown reply must never authorize another START.
            if running_state(final) is not False:
                raise NmrRunError("NMR status does not confirm idle")
        except Exception as exc:
            self.log.error(f"NMR: idle NOT confirmed: {type(exc).__name__}: {exc}")
            return False
        self.nmr_pending = False
        ok(self.log, "NMR: idle confirmed after failure")
        return True

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                self.log.error(f"STOPPING: {exc_type.__name__}: {exc}")
                self.pump.stop()  # Always before closing the port.
                if self.nmr_pending and not self.nmr_stop_attempted:
                    self.stop_nmr_and_confirm_idle()
                if self.sample_pending and not self.pump_uncertain:
                    try:
                        self.return_sample()
                    except BaseException as return_exc:
                        self.log.error(f"Chemyx sample return failed: {type(return_exc).__name__}: {return_exc}")
                        self.pump.stop()
                if self.sample_pending or self.pump_uncertain:
                    self.log.error("Liquid may remain in the syringe; physical inspection is required before another run.")
        finally:
            self.pump.close()
            self.log.info("Chemyx connection closed; NMR uses per-request HTTP connections")
        return False


def sample(rig, cfg, path, number):
    rig.transfer("withdraw")
    rig.sample_pending = True
    rig.nmr_pending, rig.nmr_stop_attempted = True, False
    try:
        result = rig.nmr.scan(path, cfg["nmr"]["scans"])
    except Exception as exc:
        result = dict(status="NMR_FAILED", error=f"{type(exc).__name__}: {exc}")
    idle = True
    if result["status"] == "NMR_SAVED":
        rig.nmr_pending = False
        ok(rig.log, f"NMR: Observation {number}, {cfg['nmr']['scans']} scans - COMPLETE")
        rig.log.info(f"Saved {Path(result['path']).name}")
    else:
        rig.log.error(f"NMR observation {number} FAILED: {result.get('error') or result['status']}")
        idle = rig.stop_nmr_and_confirm_idle()
    # Even if NMR idle cannot be confirmed, return a completed withdrawal when
    # pump displacement remains known and the operator-confirmed path is open.
    rig.return_sample()
    if not idle:
        raise NmrRunError("NMR idle remains uncertain; sample returned, run stopped; check the NMR touchscreen")
    return result


def results_dir(cfg, output_dir):
    if output_dir is not None:
        return new_run_dir(cfg, "24_stage2only", output_dir)
    root = config.resolve_repo_path(cfg["output"]["root_dir"])
    stem = f"24_stage2only_{datetime.now():%Y%m%d_%H%M%S}"
    suffix = 0
    while True:
        target = root / (stem if suffix == 0 else f"{stem}_{suffix}")
        try:
            return new_run_dir(cfg, "24_stage2only", target)
        except FileExistsError:
            suffix += 1


def run(cfg, *, mock, output_dir=None, clock=None, rig_factory=TwoInstrumentRig, confirm=False, input_fn=input):
    clock = clock or (SimulatedClock() if mock else RealClock())
    run_dir = results_dir(cfg, output_dir)
    log = make_logger(run_dir, clock, "24_stage2only")
    slots = observation_times(cfg)
    tally = dict(slots=len(slots), saved=0, nmr_failed=0, missed=0)
    status = "FAILED"
    try:
        for line in summary_lines(cfg):
            log.info(line)
        log.info("Mode: " + ("MOCK (no hardware)" if mock else "LIVE"))
        log.info(f"Results: {run_dir}")
        if confirm and input_fn("Confirm rig is ready [y/N]: ").strip().lower() not in {"y", "yes"}:
            status = "DECLINED"
            return status, tally
        with rig_factory(cfg, mock=mock, clock=clock, log=log) as rig:
            start = clock.now()
            for number, due in enumerate(slots, 1):
                # Same fixed-start schedule and missed-slot behavior as Script 21.
                if number > 1 and clock.now() - start > due + 1e-6:
                    tally["missed"] += 1
                    log.warning(f"Slot {number}/{len(slots)} MISSED (scheduled {clock_text(due)}); continuing")
                    continue
                if clock.now() - start < due:
                    log.info(f"Next observation {number} at {clock_text(due)}")
                    while clock.now() - start < due:
                        clock.sleep(due - (clock.now() - start))
                log.info(f"--- Observation {number}/{len(slots)} (scheduled {clock_text(due)}) ---")
                result = sample(rig, cfg, run_dir / f"observation_{number:03d}.dx", number)
                if result["status"] == "NMR_SAVED":
                    tally["saved"] += 1
                else:
                    tally["nmr_failed"] += 1
            status = "COMPLETE"
    except KeyboardInterrupt:
        status = "INTERRUPTED"
        log.error("Interrupted by operator (Ctrl+C)")
    except (PumpCommandError, NmrRunError) as exc:
        status = "STOPPED"
        log.error(str(exc))
    except Exception as exc:
        log.error(f"FAILED: {type(exc).__name__}: {exc}")
    finally:
        log.info(f"{status} | slots {tally['slots']} | spectra saved {tally['saved']} | "
                 f"NMR failed {tally['nmr_failed']} | missed {tally['missed']}")
        log.info(f"Results preserved: {run_dir}")
        close_logger(log)
    return status, tally


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, help="Optional new run folder; existing folders are never overwritten")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true", help="Fake pump/NMR and simulated time")
    modes.add_argument("--live", action="store_true", help="Real instruments after one rig-ready confirmation")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"24 configuration error: {type(exc).__name__}: {exc}", flush=True)
        return 2
    try:
        status, _ = run(cfg, mock=args.mock, output_dir=args.output_dir, confirm=True)
    except Exception as exc:
        print(f"24 could not start: {type(exc).__name__}: {exc}", flush=True)
        return 1
    return {"COMPLETE": 0, "DECLINED": 3, "INTERRUPTED": 130}.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
