"""Simple Arduino needle + Chemyx pump + NMR helpers for scripts 15 and 16.

Deliberately minimal. Nothing here reads or writes needle_state.json, HOME
journals, pump ledgers, dose records or port lock files, so nothing left over
from an earlier run can block this one. The needle uses the same SimpleNeedle
code and the same two one-integer files as arduino/scripts/needle_control.py
(runs/arduino/simple_needle_position.txt and simple_needle_steps.txt). NMR uses
the standalone 04/05 legacy_acquire unchanged. Files written: those two
integers, NMR .dx files and the run's plain-text log.txt.

Kept as safety: the firmware's own 120-second move timeout, the configured
-3..+5 travel range, and STOP to the pump and Arduino on any error or Ctrl+C.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import logging
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from .. import config
from ..instruments.chemyx import Pump
from ..instruments.nmr import NmrRpcConfig
from .nmr_standalone import TracingClient, legacy_acquire, raw_text
from .stage2_legacy_nmr import MockLegacyClient

FIXTURE = config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"
SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")

REQUIRED = {
    "arduino": ("port", "baud_rate", "steps_per_unit", "up_step_sign", "speed_steps_s",
                "acceleration_steps_s2", "min_position", "max_position", "up_position", "position_file"),
    "chemyx": ("port", "baud_rate", "channel", "timeout_seconds", "response_delay_seconds",
               "syringe_diameter_mm", "syringe_capacity_ml", "extra_seconds", "return_rate_ml_min"),
    "nmr": ("host", "port", "scheme", "http_timeout_seconds", "acquisition_timeout_seconds",
            "poll_seconds", "scans", "gain", "auto_gain", "center_ppm", "width_ppm"),
    "output": ("root_dir",),
}


class PumpCommandError(RuntimeError):
    """A pump command failed now; the run stops. Nothing is saved for later."""


def __getattr__(name):
    # Keep the 15/16 public imports, without loading Arduino for pump/NMR-only users.
    if name in {"NeedleError", "SimpleNeedle", "HardwareError"}:
        from arduino.python.simple_needle import NeedleError, SimpleNeedle
        return {"NeedleError": NeedleError, "SimpleNeedle": SimpleNeedle,
                "HardwareError": (NeedleError, PumpCommandError)}[name]
    raise AttributeError(name)


# ---------------------------------------------------------------- clocks/settings
class RealClock:
    now = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class SimulatedClock:
    """Mock runs: waits advance a counter instead of sleeping."""
    def __init__(self):
        self.elapsed = 0.0

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += max(0.0, seconds)


def clock_text(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _merge(base, override):
    merged = deepcopy(base)
    for key, value in override.items():
        merged[key] = _merge(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


def load_settings(path):
    """Read one YAML; an optional `base_config:` file supplies values it does not set."""
    cfg = config.read_mapping_config(config.resolve_repo_path(path), "simple run config")
    base = cfg.pop("base_config", None)
    return _merge(load_settings(base), cfg) if base else cfg


def check_settings(cfg, extra=None):
    """Fail before any port opens if a setting is missing or clearly wrong."""
    required = dict(REQUIRED, **(extra or {}))
    missing = [f"{section}.{key}" for section, keys in required.items() for key in keys
               if not isinstance(cfg.get(section), dict) or key not in cfg[section]]
    if missing:
        raise ValueError("Missing setting(s): " + ", ".join(missing))
    a, p = cfg["arduino"], cfg["chemyx"]
    if a["up_step_sign"] not in (-1, 1) or not a["min_position"] <= 0 < a["up_position"] <= a["max_position"]:
        raise ValueError("arduino: need up_step_sign of 1 or -1 and min_position <= 0 < up_position <= max_position")
    if str(a["port"]).strip().upper() == str(p["port"]).strip().upper():
        raise ValueError("The Arduino and Chemyx need different COM ports")
    if not config.DIAMETER_MIN <= float(p["syringe_diameter_mm"]) <= config.DIAMETER_MAX:
        raise ValueError("chemyx.syringe_diameter_mm is outside the pump's range")
    if type(cfg["nmr"]["scans"]) is not int or cfg["nmr"]["scans"] < 1:
        raise ValueError("nmr.scans must be a positive whole number")
    return cfg


def new_run_dir(cfg, prefix, output_dir=None):
    run = Path(output_dir) if output_dir else (
        config.resolve_repo_path(cfg["output"]["root_dir"]) / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}")
    run = run.resolve()
    run.mkdir(parents=True, exist_ok=False)
    return run


# ------------------------------------------------------------------------ logging
def _ansi_console(stream):
    if os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING lets PowerShell show the colours.
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                    and kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


class _Elapsed(logging.Filter):
    def __init__(self, clock):
        super().__init__()
        self.clock, self.start = clock, clock.now()

    def filter(self, record):
        record.elapsed = clock_text(self.clock.now() - self.start)
        return True


class _ColourFormatter(logging.Formatter):
    COLOURS = {SUCCESS: "32", logging.WARNING: "33", logging.ERROR: "31", logging.CRITICAL: "1;31"}

    def __init__(self, fmt, colour):
        super().__init__(fmt)
        self.colour = colour

    def format(self, record):
        text = super().format(record)
        code = self.COLOURS.get(record.levelno) if self.colour else None
        return f"\x1b[{code}m{text}\x1b[0m" if code else text


def make_logger(run_dir, clock, name):
    """Console (coloured, INFO and above) plus run_dir/log.txt (plain, also raw replies)."""
    logger = logging.Logger(f"{name}.{Path(run_dir).name}", logging.DEBUG)
    elapsed = _Elapsed(clock)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_ColourFormatter("[%(elapsed)s] %(message)s", _ansi_console(sys.stdout)))
    text_file = logging.FileHandler(Path(run_dir) / "log.txt", encoding="utf-8")
    text_file.setFormatter(logging.Formatter("%(asctime)s [%(elapsed)s] %(levelname)-7s %(message)s",
                                             "%Y-%m-%d %H:%M:%S"))
    for handler in (console, text_file):
        handler.addFilter(elapsed)
        logger.addHandler(handler)
    return logger


def ok(log, message):
    log.log(SUCCESS, message)


def close_logger(log):
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)


# ------------------------------------------------------------------------ needle
def make_needle(cfg, *, mock, log):
    """The same SimpleNeedle (and position files) as needle_control.py."""
    from arduino.python.simple_needle import SimpleNeedle

    a = cfg["arduino"]
    return SimpleNeedle(port=a["port"], baud_rate=a["baud_rate"], steps_per_unit=int(a["steps_per_unit"]),
                        up_step_sign=int(a["up_step_sign"]), speed_steps_s=a["speed_steps_s"],
                        acceleration_steps_s2=a["acceleration_steps_s2"], min_position=a["min_position"],
                        max_position=a["max_position"], position_file=config.resolve_repo_path(a["position_file"]),
                        log=log, mock=mock)


# -------------------------------------------------------------------------- pump
class SyringePump:
    """One Chemyx channel. Replies are logged; only an 'error'/'invalid' reply fails a step."""

    def __init__(self, cfg, *, mock, clock, log):
        p = cfg["chemyx"]
        self.log, self.clock = log, clock
        self.channel = int(p["channel"])
        self.diameter = float(p["syringe_diameter_mm"])
        self.capacity = float(p["syringe_capacity_ml"])
        self.extra_seconds = float(p["extra_seconds"])
        self.contents_ml = 0.0  # The operator starts with the syringe empty.
        self.active = None      # (action, volume, rate, started_at) while a timed transfer is running.
        self.port = "mock Chemyx" if mock else p["port"]
        self.pump = Pump(port=p["port"], baud_rate=p["baud_rate"], channel=self.channel, units=0,
                         timeout=p["timeout_seconds"], response_delay=0 if mock else p["response_delay_seconds"],
                         mock=mock, verify=False)

    def connect(self):
        self.pump.connect()
        self.log.info(f"Chemyx: connected on {self.port}; channel {self.channel}, {self.diameter:g} mm syringe")
        self.stop()  # Nothing should be running; any reply is fine.

    def _send(self, label, call, *, strict=True):
        try:
            reply = call()
        except Exception as exc:
            raise PumpCommandError(f"Chemyx CH{self.channel} {label} failed: {type(exc).__name__}: {exc}") from exc
        text = str(reply or "").strip()
        self.log.debug(f"Chemyx CH{self.channel} {label} -> {text!r}")
        if any(word in text.lower() for word in ("error", "invalid")):
            if strict:
                raise PumpCommandError(f"Chemyx CH{self.channel} {label} rejected: {text!r}")
            self.log.warning(f"Chemyx CH{self.channel} {label} reply: {text!r} (continuing)")
        return text

    def transfer(self, action, volume_ml, rate_ml_min):
        """pump_transfer: withdraw or infuse `volume_ml` at `rate_ml_min`, wait, then STOP."""
        if action not in ("withdraw", "infuse"):
            raise ValueError("action must be 'withdraw' or 'infuse'")
        volume, rate = float(volume_ml), float(rate_ml_min)
        low, high = config.rate_limits(0)
        if not (math.isfinite(volume) and volume > 0) or not low <= rate <= high:
            raise PumpCommandError(f"Invalid transfer: {volume_ml} mL at {rate_ml_min} mL/min")
        after = self.contents_ml + (volume if action == "withdraw" else -volume)
        if after < -1e-6 or after > self.capacity + 1e-6:
            raise PumpCommandError(f"{action} {volume:g} mL would take the syringe to {after:g} mL "
                                   f"(allowed 0 to {self.capacity:g} mL)")
        ch, pump = self.channel, self.pump
        self.log.info(f"Chemyx CH{ch}: {action} {volume:g} mL at {rate:g} mL/min")
        self._send("units", lambda: pump.set_units(0, channel=ch))
        self._send("diameter", lambda: pump.set_diameter(self.diameter, channel=ch))
        self._send("rate", lambda: pump.set_rate(rate, channel=ch))
        self._send("volume", lambda: pump.set_volume(-volume if action == "withdraw" else volume, channel=ch))
        self.active = (action, volume, rate, self.clock.now())
        self._send("start", lambda: pump.start(channel=ch, delay=0))
        self.clock.sleep(volume / rate * 60 + self.extra_seconds)
        self._send("stop", lambda: pump.stop(channel=ch), strict=False)  # Wording is never checked.
        self.active, self.contents_ml = None, max(0.0, after)
        self.log.info(f"Chemyx CH{ch}: {action} complete; syringe holds about {round(self.contents_ml, 2):g} mL")

    def settle_interrupted(self, stopped_at):
        """After an emergency STOP mid-transfer: estimate the contents from how long the pump ran.

        Biased low by one second of flow, so emptying never pushes past fully-in.
        """
        if self.active is None:
            return
        action, volume, rate, started_at = self.active
        moved, margin = min(volume, max(0.0, rate * (stopped_at - started_at) / 60)), rate / 60
        if action == "withdraw":
            self.contents_ml += max(0.0, moved - margin)
        else:
            self.contents_ml = max(0.0, self.contents_ml - min(volume, moved + margin))
        self.active = None
        self.log.warning(f"Chemyx CH{self.channel}: {action} of {volume:g} mL stopped after about "
                         f"{stopped_at - started_at:.0f} s; syringe holds about {self.contents_ml:.2f} mL (estimated)")

    def stop(self):
        try:
            reply = self.pump.stop(channel=self.channel)
            self.log.info(f"Chemyx CH{self.channel}: STOP sent (reply {str(reply or '').strip()!r})")
            return True
        except BaseException as exc:
            self.log.error(f"Chemyx CH{self.channel}: STOP could not be sent ({type(exc).__name__}: {exc}); "
                           "use the front-panel STOP")
            return False

    def close(self):
        try:
            self.pump.disconnect()
        except Exception as exc:
            self.log.warning(f"Chemyx: port close warning: {exc}")


# --------------------------------------------------------------------------- NMR
class _MockTracingClient(TracingClient):
    """--mock only: the fixture-based fake NMR behind the real 04/05 TracingClient API."""

    def __init__(self, fake):
        super().__init__(NmrRpcConfig(host="mock.invalid"))
        self.fake = fake

    def _request(self, method, path, payload=None, params=None):
        self.exchanges.append({"n": len(self.exchanges) + 1, "t_s": 0.0, "method": method, "path": path})
        return self.fake._request(method, path, payload, params)


class NmrScanner:
    """Standalone 04/05 sequence per scan: read status (baseline), read both templates, legacy_acquire.

    A new TracingClient per scan, so nothing is carried from one scan to the next.
    """

    def __init__(self, cfg, *, mock, clock, log):
        n = cfg["nmr"]
        self.settings, self.mock, self.log = n, mock, log
        self.fake = MockLegacyClient(FIXTURE.read_text(encoding="utf-8"), clock) if mock else None
        self.rpc = NmrRpcConfig(host=n["host"], port=n["port"], scheme=n["scheme"], timeout=n["http_timeout_seconds"],
                                poll_seconds=n["poll_seconds"], max_wait_seconds=n["acquisition_timeout_seconds"])

    def client(self):
        return _MockTracingClient(self.fake) if self.mock else TracingClient(self.rpc, echo=self.log.debug)

    def check(self):
        """Informational only: an unreachable NMR does not stop the run."""
        if self.mock:
            self.log.info("NMR: mock client (no network)")
            return True
        try:
            self.client().ping()
        except Exception as exc:
            self.log.warning(f"NMR: not reachable ({type(exc).__name__}: {exc}); scans will still be attempted")
            return False
        ok(self.log, f"NMR: reachable at {self.settings['host']}:{self.settings['port']}")
        return True

    def _out(self, line):
        text = line.strip()
        if "NMR STATUS: WAITING" in text:
            self.log.debug(f"NMR: {text}")  # One line per 2-second poll: log.txt only.
        elif any(word in text for word in ("TIMEOUT", "NOT RETURNED", "SAME AS BEFORE", "Last status")):
            self.log.warning(f"NMR: {text}")
        else:
            self.log.info(f"NMR: {text}")

    def scan(self, path, scans):
        """run_nmr: returns 04/05 legacy_acquire's result dict; never raises for NMR problems."""
        n = self.settings
        settings = SimpleNamespace(receiver_gain=n["gain"], auto_gain=n["auto_gain"], scans=int(scans),
                                   spectral_center=n["center_ppm"], sweep_width=n["width_ppm"],
                                   poll_seconds=n["poll_seconds"], max_wait_seconds=n["acquisition_timeout_seconds"])
        client = self.client()
        client.quiet_status = True
        self.log.info(f"NMR: {scans} scan(s), standalone 04/05 method")
        try:
            baseline = client.iflow_experiment_status()
            templates = (client.iflow_1d_settings(), client.iflow_experiment_settings())
            if not all(isinstance(value, dict) for value in templates):
                raise ValueError("A settings template is not a JSON object")
        except Exception as exc:
            self.log.warning(f"NMR FAILED before START (nothing sent): {type(exc).__name__}: {exc}")
            return dict(status="NMR_FAILED", path=None, error=f"{type(exc).__name__}: {exc}")
        result = legacy_acquire(settings, path, client, templates, baseline_fid=raw_text(baseline), out=self._out)
        if result["status"] != "NMR_SAVED":
            self.log.warning(f"NMR FAILED: {result['status']}: {result.get('error') or 'no raw FID saved'}")
        return result


# --------------------------------------------------------------------------- rig
class SimpleRig:
    """Arduino + Chemyx + NMR for one run, used as a context manager.

    Startup uses the saved needle position (live: set-home must have been run).
    On any error or Ctrl+C: STOP the pump and the Arduino. After Ctrl+C or a pump
    error, also raise the needle and infuse the estimated syringe contents back
    (as the recipe returns a sample). Then return the needle to HOME when its
    position is known and close both ports. Ctrl+C again skips a cleanup step.
    """

    def __init__(self, cfg, *, mock, clock, log):
        self.log, self.clock = log, clock
        self.up_position = cfg["arduino"]["up_position"]
        self.empty_rate = float(cfg["chemyx"]["return_rate_ml_min"])
        self.needle = make_needle(cfg, mock=mock, log=log)
        self.pump = SyringePump(cfg, mock=mock, clock=clock, log=log)
        self.nmr = NmrScanner(cfg, mock=mock, clock=clock, log=log)
        self.mock = mock
        self.nmr_reachable = None
        self.returned_home = False

    def __enter__(self):
        from arduino.python.simple_needle import NeedleError

        try:
            try:
                self.needle.connect()
            except Exception as exc:
                raise NeedleError(f"Arduino connect failed: {type(exc).__name__}: {exc}") from exc
            if not self.needle.has_home and self.mock:
                self.log.info("Arduino: mock run with no saved HOME; the mock counter starts at 0")
                self.needle.set_home()
            if not self.needle.has_home:
                raise NeedleError("No saved needle HOME: place the needle at its sampling position and run "
                                  "python arduino\\scripts\\needle_control.py set-home --live "
                                  "--config arduino\\configs\\arduino_real_COM3.yaml")
            self.log.info(f"Arduino: saved needle {self.needle.describe()}")
            try:
                self.pump.connect()
            except Exception as exc:
                raise PumpCommandError(f"Chemyx connect failed: {type(exc).__name__}: {exc}") from exc
        except KeyboardInterrupt:
            self.log.error("CONNECT INTERRUPTED (Ctrl+C)")
            self.close()
            raise
        except Exception as exc:
            self.log.error(f"CONNECT FAILED: {exc}")
            self.close()
            raise
        self.nmr_reachable = self.nmr.check()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                cause = "Ctrl+C" if issubclass(exc_type, KeyboardInterrupt) else f"{exc_type.__name__}: {exc}"
                self.log.error(f"STOPPING: {cause}")
                self.emergency_stop()
                if issubclass(exc_type, (KeyboardInterrupt, PumpCommandError)):
                    self.empty_syringe()
                elif self.pump.contents_ml >= 0.01:
                    self.log.warning(f"Chemyx: syringe NOT emptied after this error; about "
                                     f"{self.pump.contents_ml:.2f} mL left; empty Channel 1 by hand")
            self.returned_home = self.return_home()
        finally:
            self.close()
        return False

    # Simple direct helpers used by 15 and 16.
    def needle_move(self, direction, steps=None):
        self.needle.move(direction, steps)

    def needle_to(self, position):
        self.needle.move_to_units(position)

    def pump_transfer(self, action, volume_ml, rate_ml_min):
        self.pump.transfer(action, volume_ml, rate_ml_min)

    def run_nmr(self, scans, path):
        return self.nmr.scan(path, scans)

    def emergency_stop(self):
        stopped_at = self.clock.now()
        self.pump.stop()
        self.needle.stop()
        self.pump.settle_interrupted(stopped_at)

    def empty_syringe(self):
        """Needle UP, then infuse the estimated contents back to empty. Never raises."""
        volume = math.floor(self.pump.contents_ml * 1000) / 1000  # Round down: never past fully-in.
        if volume < 0.01:
            return True
        if not self.needle.known:
            self.log.error(f"Chemyx: syringe NOT emptied (needle position uncertain); about {volume:.2f} mL "
                           "left; empty Channel 1 by hand")
            return False
        self.log.warning(f"Emptying Channel 1: needle UP, infuse about {volume:.2f} mL back (press Ctrl+C to skip)")
        try:
            self.needle.move_to_units(self.up_position)
            self.pump.transfer("infuse", volume, self.empty_rate)
            self.pump.contents_ml = 0.0  # Under 0.001 mL of rounding is left.
        except KeyboardInterrupt:
            stopped_at = self.clock.now()
            self.pump.stop()
            self.pump.settle_interrupted(stopped_at)
            self.log.warning(f"Emptying skipped (Ctrl+C); about {self.pump.contents_ml:.2f} mL may remain; "
                             "empty Channel 1 by hand")
            return False
        except Exception as exc:
            self.pump.stop()
            self.log.error(f"Emptying failed ({exc}); about {self.pump.contents_ml:.2f} mL may remain; "
                           "empty Channel 1 by hand")
            return False
        ok(self.log, "Channel 1 syringe emptied (estimated from pump time; up to about 0.1 mL may remain)")
        return True

    def return_home(self):
        n = self.needle
        if not n.known:
            self.log.error("Needle NOT moved: a move did not finish, so the saved position may be wrong. "
                           "Check the needle by eye, then run needle_control.py set-home.")
            return False
        if not n.has_home:
            return False
        if n.steps != 0:
            self.log.info(f"Returning needle to HOME from {n.describe()} (press Ctrl+C to skip)")
        try:
            n.home()
        except KeyboardInterrupt:
            n.stop()
            self.log.warning("Return to HOME skipped (Ctrl+C); check the needle by eye")
            return False
        except Exception as exc:
            n.stop()
            self.log.error(f"Return to HOME failed ({exc}); check the needle by eye")
            return False
        ok(self.log, f"Needle at HOME; saved position 0 ({n.position_path})")
        return True

    def close(self):
        self.needle.close()
        self.pump.close()
        self.log.info("Serial ports closed")
