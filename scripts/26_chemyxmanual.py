"""26: simple manual Chemyx control — withdraw, infuse, stop, status.

Same pump code as 20-25 (timed transfer + STOP, any STOP reply accepted). Nothing
is saved or read back: no position record, HOME, locks, journals or dose records,
so an earlier stopped run, Ctrl+C or manual STOP never blocks a later command.
The pump does not report plunger position, so you choose the volume from what
you see. Each move is checked only against the syringe size, after one
confirmation (--yes skips it). Ctrl+C during a move sends STOP and prints about
how much moved. Writes no files.

  python scripts\\26_chemyxmanual.py status --live
  python scripts\\26_chemyxmanual.py infuse --channel 1 --volume 3.9 --live
  python scripts\\26_chemyxmanual.py withdraw --channel 2 --volume 0.5 --rate 1 --live
  python scripts\\26_chemyxmanual.py stop --live
"""
from __future__ import annotations

import argparse
import logging
import math
from pathlib import Path
import sys

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.workflows.simple_rig import RealClock, SimulatedClock, SyringePump, load_settings, ok

DEFAULT_CONFIG = Path("configs/experiments/26_chemyxmanual.yaml")
CHEMYX_KEYS = ("port", "baud_rate", "timeout_seconds", "response_delay_seconds", "syringe_diameter_mm",
               "syringe_capacity_ml", "extra_seconds")


class ManualError(RuntimeError):
    """A setting or argument is wrong; nothing was sent to the pump."""


def load_config(path=DEFAULT_CONFIG):
    cfg = load_settings(path)
    missing = [f"chemyx.{k}" for k in CHEMYX_KEYS if k not in (cfg.get("chemyx") or {})]
    missing += [f"channel_2.{k}" for k in ("syringe_diameter_mm", "syringe_capacity_ml") if k not in (cfg.get("channel_2") or {})]
    if missing:
        raise ManualError("Missing setting(s): " + ", ".join(missing))
    for section in ("chemyx", "channel_2"):
        if not config.DIAMETER_MIN <= float(cfg[section]["syringe_diameter_mm"]) <= config.DIAMETER_MAX:
            raise ManualError(f"{section}.syringe_diameter_mm is outside the pump's range")
    return cfg


def channel_settings(cfg, channel):
    """The SyringePump settings for one channel; both channels share one serial connection."""
    if channel == 1:
        return {"chemyx": dict(cfg["chemyx"], channel=1)}
    d = cfg["channel_2"]
    return {"chemyx": dict(cfg["chemyx"], channel=2, syringe_diameter_mm=d["syringe_diameter_mm"],
                           syringe_capacity_ml=d["syringe_capacity_ml"])}


def default_rate(cfg, channel):
    manual = cfg.get("manual") or {}
    fallback = 5.0 if channel == 1 else cfg["channel_2"].get("rate_ml_min", 1.0)
    return float(manual.get(f"channel_{channel}_rate_ml_min", fallback))


def check_move(cfg, channel, volume, rate):
    capacity = float(channel_settings(cfg, channel)["chemyx"]["syringe_capacity_ml"])
    if not (math.isfinite(volume) and 0 < volume <= capacity):
        raise ManualError(f"--volume must be more than 0 and at most the {capacity:g} mL Channel {channel} syringe")
    low, high = config.rate_limits(0)
    if not (math.isfinite(rate) and low <= rate <= high):
        raise ManualError(f"--rate must be between {low:g} and {high:g} mL/min")


def console_log(verbose):
    log = logging.Logger("26_chemyxmanual", logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    # The shared pump code reports its tracked contents; 26 tracks none, so that number means nothing here.
    handler.addFilter(lambda record: "syringe holds about" not in record.getMessage())
    log.addHandler(handler)
    return log


def open_channels(cfg, channels, *, mock, clock, log):
    """Connect once; every channel's pump object uses that one serial connection."""
    pumps = {ch: SyringePump(channel_settings(cfg, ch), mock=mock, clock=clock, log=log) for ch in (1, 2)}
    pumps[1].pump.connect()
    log.info(f"Chemyx: connected on {pumps[1].port}")
    pumps[2].pump = pumps[1].pump
    return {ch: pumps[ch] for ch in channels}, pumps[1].pump


def run(command, cfg, *, mock, channels, volume=None, rate=None, yes=False, clock=None, input_fn=input, verbose=False):
    """Returns the exit code: 0 done, 1 failed, 3 declined, 130 Ctrl+C."""
    clock = clock or (SimulatedClock() if mock else RealClock())
    log = console_log(verbose)
    mode = "MOCK (no hardware)" if mock else "LIVE"
    log.info(f"26 CHEMYX MANUAL | {command.upper()} | {mode} | nothing is saved or read back")
    if command in ("withdraw", "infuse"):
        [ch] = channels
        s = channel_settings(cfg, ch)["chemyx"]
        seconds = volume / rate * 60 + float(cfg["chemyx"]["extra_seconds"])
        log.info(f"Channel {ch} ({s['syringe_diameter_mm']:g} mm / {s['syringe_capacity_ml']:g} mL syringe): "
                 f"{command.upper()} {volume:g} mL at {rate:g} mL/min (about {seconds:.0f} s, then STOP)")
        log.info("The pump does not report plunger position: check the syringe and lines by eye.")
        if not yes and input_fn("Start this move? [y/N]: ").strip().lower() not in {"y", "yes"}:
            log.info("Cancelled. Nothing was sent to the pump.")
            return 3
    try:
        pumps, driver = open_channels(cfg, channels, mock=mock, clock=clock, log=log)
    except KeyboardInterrupt:
        log.warning("Interrupted before the pump was contacted")
        return 130
    except Exception as exc:
        log.error(f"Chemyx connection failed: {type(exc).__name__}: {exc}. Is another program (a 20-25 run, "
                  "the Chemyx GUI) using the port?")
        return 1
    try:
        if command == "status":
            reply = str(driver.help() or "").strip()
            if not reply:
                log.error("Chemyx did not answer HELP: check cable, port and power")
                return 1
            ok(log, "Chemyx answered (HELP). No motion and no STOP were sent.")
            for ch in (1, 2):
                s = channel_settings(cfg, ch)["chemyx"]
                log.info(f"Channel {ch}: {s['syringe_diameter_mm']:g} mm / {s['syringe_capacity_ml']:g} mL syringe; "
                         f"default rate {default_rate(cfg, ch):g} mL/min")
            log.info("No position is shown: 26 keeps none. Check the plungers by eye.")
            return 0
        if command == "stop":
            sent = [pump.stop() for pump in pumps.values()]
            if all(sent):
                ok(log, "STOP sent to channel(s) " + ", ".join(map(str, pumps)) + ". Nothing was saved.")
                return 0
            log.error("STOP could not be sent to every channel: use the Chemyx front-panel STOP")
            return 1
        [(ch, pump)] = pumps.items()
        pump.stop()  # Nothing should be running; any reply is fine (also clears a paused pump).
        pump.contents_ml = volume if command == "infuse" else 0.0  # No saved position: check this move only.
        started = clock.now()
        try:
            pump.transfer(command, volume, rate)
        except KeyboardInterrupt:
            stopped = clock.now()
            pump.stop()
            moved = min(volume, max(0.0, rate * (stopped - started) / 60))
            log.warning(f"Ctrl+C: STOP sent to Channel {ch}. About {moved:.2f} of {volume:g} mL was "
                        f"{'infused' if command == 'infuse' else 'withdrawn'} (estimate from pump time). Nothing was saved.")
            return 130
        ok(log, f"Channel {ch}: {command} {volume:g} mL complete. Nothing was saved.")
        return 0
    except KeyboardInterrupt:
        for pump in pumps.values():
            pump.stop()
        log.warning("Ctrl+C: STOP sent. Nothing was saved.")
        return 130
    except Exception as exc:
        for pump in pumps.values():
            pump.stop()
        log.error(f"FAILED: {type(exc).__name__}: {exc}. STOP sent; nothing was saved.")
        return 1
    finally:
        driver.disconnect()
        log.info("Chemyx port closed")


def main(argv=None, *, input_fn=input):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("status", "stop", "withdraw", "infuse"))
    parser.add_argument("--channel", choices=("1", "2", "both"), help="Required for withdraw/infuse; stop defaults to both")
    parser.add_argument("--volume", type=float, metavar="ML", help="Volume for withdraw/infuse")
    parser.add_argument("--rate", type=float, metavar="ML_MIN", help="Speed for this command (default from the YAML)")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation for withdraw/infuse")
    parser.add_argument("--verbose", action="store_true", help="Also print every pump reply")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--live", action="store_true", help="Real pump")
    modes.add_argument("--mock", action="store_true", help="Fake pump, simulated time")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.command in ("withdraw", "infuse"):
            if args.channel not in ("1", "2") or args.volume is None:
                raise ManualError(f"{args.command} needs --channel 1 or 2 and --volume ML")
            channels = [int(args.channel)]
            rate = args.rate if args.rate is not None else default_rate(cfg, channels[0])
            check_move(cfg, channels[0], args.volume, rate)
        else:
            channels = [1, 2] if args.channel in (None, "both") else [int(args.channel)]
            rate = None
    except Exception as exc:
        print(f"26 settings error: {exc}. Nothing was sent to the pump.", flush=True)
        return 2
    return run(args.command, cfg, mock=args.mock, channels=channels, volume=args.volume, rate=rate,
               yes=args.yes, input_fn=input_fn, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
