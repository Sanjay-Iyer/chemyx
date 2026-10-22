"""Optional one-time Channel 2 dose for the simple Stage 2 only runs (21, 24).

With ``channel_2.enabled: true`` the dose runs once, ``delay_after_time_zero_seconds``
after the time-0 sample has finished; the sampling schedule is unchanged. Channel 2
shares Channel 1's Chemyx connection (every command carries channel 2). Nothing is
saved: a dose stopped by Ctrl+C or an error is reported UNCERTAIN and never repeated.
"""
from __future__ import annotations

import math

from .. import config
from .simple_rig import SyringePump, clock_text, ok

KEYS = ("channel", "volume_ml", "rate_ml_min", "delay_after_time_zero_seconds", "initial_volume_ml",
        "syringe_diameter_mm", "syringe_capacity_ml")


def dose_enabled(cfg):
    d = cfg.get("channel_2")
    if d is None:
        return False
    if not isinstance(d, dict) or type(d.get("enabled", False)) is not bool:
        raise ValueError("channel_2.enabled must be true or false")
    return d.get("enabled", False)


def check_channel_2(cfg):
    """Fail before any port opens if an enabled dose is set up wrong. Returns whether it is enabled."""
    if not dose_enabled(cfg):
        return False
    d = cfg["channel_2"]
    missing = [f"channel_2.{key}" for key in KEYS if key not in d]
    if missing:
        raise ValueError("Missing setting(s): " + ", ".join(missing))
    if d["channel"] != 2:
        raise ValueError("channel_2.channel must be 2")
    for key in ("volume_ml", "rate_ml_min", "syringe_capacity_ml", "initial_volume_ml", "delay_after_time_zero_seconds"):
        value = d[key]
        zero = key in ("initial_volume_ml", "delay_after_time_zero_seconds")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or value < 0 or value == 0 and not zero:
            raise ValueError(f"channel_2.{key} must be a finite {'nonnegative' if zero else 'positive'} number")
    low, high = config.rate_limits(0)
    if not low <= float(d["rate_ml_min"]) <= high:
        raise ValueError("channel_2.rate_ml_min is outside the pump's range")
    if not config.DIAMETER_MIN <= float(d["syringe_diameter_mm"]) <= config.DIAMETER_MAX:
        raise ValueError("channel_2.syringe_diameter_mm is outside the pump's range")
    if not float(d["volume_ml"]) <= float(d["initial_volume_ml"]) <= float(d["syringe_capacity_ml"]):
        raise ValueError("channel_2.initial_volume_ml must cover volume_ml and fit the syringe")
    return True


def channel_2_settings(cfg):
    d = cfg["channel_2"]
    return {"chemyx": dict(cfg["chemyx"], channel=2, syringe_diameter_mm=d["syringe_diameter_mm"],
                           syringe_capacity_ml=d["syringe_capacity_ml"])}


def dose_seconds(cfg):
    d = cfg["channel_2"]
    return float(d["volume_ml"]) / float(d["rate_ml_min"]) * 60 + float(cfg["chemyx"]["extra_seconds"])


def plan_lines(cfg):
    d = cfg["channel_2"]
    return [f"Channel 2 dose: {d['volume_ml']:g} mL at {d['rate_ml_min']:g} mL/min (about {dose_seconds(cfg):.0f} s), once, "
            f"{d['delay_after_time_zero_seconds']:g} s after the time-0 sample finishes; CH2 loaded: "
            f"{d['initial_volume_ml']:g} mL",
            "The schedule is unchanged: a sample whose time passes during the dose is skipped (no catch-up)"]


class Channel2:
    """Channel 2 on Channel 1's Chemyx connection, for the one dose only."""

    def __init__(self, cfg, *, mock, clock, log):
        self.settings, self.log = cfg["channel_2"], log
        self.pump = SyringePump(channel_2_settings(cfg), mock=mock, clock=clock, log=log)
        self.pump.contents_ml = float(self.settings["initial_volume_ml"])
        self.attached = self.pending = False

    def attach(self, driver):
        self.pump.pump, self.attached = driver, True
        self.pump.stop()  # Nothing should be running; any reply is fine.

    def dose(self):
        d = self.settings
        self.pending = True
        self.pump.transfer("infuse", d["volume_ml"], d["rate_ml_min"])
        self.pending = False
        ok(self.log, f"Chemyx CH2: dose {d['volume_ml']:g} mL complete")

    def stop(self):
        """Error/Ctrl+C cleanup, before Channel 1's cleanup closes the shared port."""
        if self.attached and self.pump.pump.is_connected:
            self.pump.stop()
            if self.pending:
                self.log.error("Channel 2 dose interrupted; CH2 STOP sent. The amount added is uncertain and the "
                               "dose is never repeated automatically: inspect before another run.")


def dose_after_time_zero(rig, cfg, clock, log, tally, start, next_due):
    """After the time-0 sample: wait the configured delay, then `rig.dose()` once."""
    delay = float(cfg["channel_2"]["delay_after_time_zero_seconds"])
    at = clock.now() - start + delay
    log.info("=" * 60)
    log.info(f"CHANNEL 2 DOSE at {clock_text(at)} ({delay:g} s after the time-0 sample); it takes about "
             f"{dose_seconds(cfg):.0f} s" + (f"; next sample at {clock_text(next_due)}" if next_due is not None else ""))
    while clock.now() - start < at:
        clock.sleep(at - (clock.now() - start))
    tally["dose"] = "STARTED"
    rig.dose()
    tally["dose"] = "COMPLETE"
    log.info("=" * 60)
