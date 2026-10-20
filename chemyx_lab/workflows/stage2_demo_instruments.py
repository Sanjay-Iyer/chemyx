"""Small physical adapter for the attended 10 demo; no pump history or homing."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import math
import io
from pathlib import Path
import re

from arduino.python.config import DEFAULTS
from arduino.python.needle_state import NeedleState, NeedleStateStore, TrackedNeedle
from arduino.python.protocol import bool_field
from arduino.scripts._common import controller_session
from .. import config
from ..instruments.chemyx import Pump


class PhysicalInstrumentError(RuntimeError):
    """Physical state is uncertain; no further movements are permitted."""


def _number(value, label, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise ValueError(f"{label} must be finite and {'nonnegative' if zero else 'positive'}")
    return float(value)


def validate_physical_config(cfg):
    a, p, d = cfg["arduino"], cfg["chemyx"], cfg["channel_2"]
    if type(p["sampling_channel"]) is not int or p["sampling_channel"] != 1 or type(d["channel"]) is not int or d["channel"] != 2:
        raise ValueError("Sampling must use Channel 1 and optional reagent must use Channel 2")
    if type(d["enabled"]) is not bool:
        raise ValueError("channel_2.enabled must be true or false")
    for section, values in (("arduino", a), ("chemyx", p)):
        if not isinstance(values["port"], str) or not values["port"].strip():
            raise ValueError(f"{section}.port is required")
        _number(values["baud_rate"], f"{section}.baud_rate")
    if a["port"].strip().casefold().removeprefix("\\\\.\\") == p["port"].strip().casefold().removeprefix("\\\\.\\"):
        raise ValueError("Arduino and Chemyx must use different serial ports")
    for key in ("timeout_seconds", "sample_rate_ml_min", "return_rate_ml_min", "wash_rate_ml_min", "pre_sample_volume_ml", "sample_volume_ml", "wash_volume_ml"):
        _number(p[key], f"chemyx.{key}")
    for key in ("response_delay_seconds", "settle_seconds", "extra_seconds", "initial_volume_ml"):
        _number(p[key], f"chemyx.{key}", zero=True)
    for section, values in (("chemyx", p), ("channel_2", d)):
        diameter = _number(values["syringe_diameter_mm"], f"{section}.syringe_diameter_mm")
        capacity = _number(values["syringe_capacity_ml"], f"{section}.syringe_capacity_ml")
        margin = _number(values["safety_margin_ml"], f"{section}.safety_margin_ml", zero=True)
        initial = _number(values["initial_volume_ml"], f"{section}.initial_volume_ml", zero=True)
        if not config.DIAMETER_MIN <= diameter <= config.DIAMETER_MAX or initial + margin > capacity:
            raise ValueError(f"{section}: invalid syringe geometry/loading")
    if p["initial_volume_ml"] != 0 or p["pre_sample_volume_ml"] + p["sample_volume_ml"] + p["safety_margin_ml"] > p["syringe_capacity_ml"] or p["wash_volume_ml"] + p["safety_margin_ml"] > p["syringe_capacity_ml"]:
        raise ValueError("Channel 1 must start empty and sampling/wash must fit the syringe")
    lo, hi = config.rate_limits(0)
    for rate in (p["sample_rate_ml_min"], p["return_rate_ml_min"], p["wash_rate_ml_min"], d["rate_ml_min"]):
        if not lo <= _number(rate, "pump rate") <= hi:
            raise ValueError("Pump rate exceeds the Chemyx mL/min limits")
    volume = _number(d["volume_ml"], "channel_2.volume_ml")
    _number(d["delay_after_time_zero_seconds"], "channel_2.delay_after_time_zero_seconds", zero=True)
    if d["enabled"] and volume > d["initial_volume_ml"]:
        raise ValueError("Channel 2 dose exceeds its manually prepared reagent loading")
    for key in ("steps_per_unit", "speed_steps_s", "acceleration_steps_s2", "min_position", "max_position", "home_position", "up_position", "up_step_sign"):
        if type(a[key]) is not int:
            raise ValueError(f"arduino.{key} must be an integer")
    if a["home_position"] != 0 or a["up_position"] != 1 or a["up_step_sign"] not in (-1, 1) or not a["min_position"] <= 0 < 1 <= a["max_position"] or a["steps_per_unit"] <= 0 or not 1 <= a["speed_steps_s"] <= 100 or not 1 <= a["acceleration_steps_s2"] <= 50000:
        raise ValueError("Arduino needs the existing HOME=0/UP=1 reference and calibrated bounded motion")
    if not isinstance(a["state_path"], str) or not a["state_path"].strip():
        raise ValueError("arduino.state_path must identify the standalone script's existing reference")


class DemoRig:
    def __init__(self, cfg, controller, needle, pump, clock, emit):
        self.cfg, self.controller, self.needle, self.pump, self.clock, self.emit = cfg, controller, needle, pump, clock, emit
        self.retained = {1: cfg["chemyx"]["initial_volume_ml"], 2: cfg["channel_2"]["initial_volume_ml"]}
        self.uncertain = False

    def _safe(self):
        if self.uncertain:
            raise PhysicalInstrumentError("Previous physical operation is uncertain; manually inspect the rig before restarting")

    @staticmethod
    def _ack(reply, action):
        text = str(reply or "")
        return not any(word in text.lower() for word in ("error", "invalid", "failed")) and re.search(rf"(?im)^\s*pump\s+{action}\s*$", text) is not None

    def _idle(self):
        for ch in ([1, 2] if self.cfg["channel_2"]["enabled"] else [1]):
            if not self._ack(self.pump.stop(channel=ch), "stop"):
                raise PhysicalInstrumentError(f"Chemyx CH{ch}: STOP was not acknowledged")

    def prepare(self):
        self.emit("Arduino: Loading the existing official HOME reference; no new HOME is set")
        self.move_needle(0)
        self.move_needle(1)

    def move_needle(self, target):
        self._safe()
        self.emit(f"Arduino: Moving needle {'DOWN / HOME' if target == 0 else 'UP' if target == 1 else target}")
        try:
            self._idle()
            self.needle.move_to(target)
            status = self.needle.status()
            if bool_field(status, "moving") or status.get("fault") != "NONE" or not bool_field(status, "position_valid") or self.needle.state.logical_position != target:
                raise PhysicalInstrumentError(f"Arduino movement not confirmed: {status}")
        except BaseException:
            self.uncertain = True
            raise
        self.emit("Arduino: Needle movement complete")

    def transfer(self, channel, volume_ml, rate_ml_min, label):
        self._safe()
        if channel not in ([1, 2] if self.cfg["channel_2"]["enabled"] else [1]) or volume_ml == 0 or not math.isfinite(volume_ml):
            raise PhysicalInstrumentError("Unconfigured channel or invalid relative pump transfer")
        values = self.cfg["chemyx"] if channel == 1 else self.cfg["channel_2"]
        retained = self.retained[channel] - volume_ml
        lo, hi = config.rate_limits(0)
        if retained < -1e-8 or retained + values["safety_margin_ml"] > values["syringe_capacity_ml"] + 1e-8 or not math.isfinite(rate_ml_min) or not lo <= rate_ml_min <= hi:
            raise PhysicalInstrumentError(f"Chemyx CH{channel}: configured volume/rate limit exceeded")
        self.emit(f"Chemyx CH{channel}: {label}; {'infuse' if volume_ml > 0 else 'withdraw'} {abs(volume_ml):g} mL at {rate_ml_min:g} mL/min")
        try:
            status = self.needle.status()
            if bool_field(status, "moving") or status.get("fault") != "NONE" or not bool_field(status, "position_valid"):
                raise PhysicalInstrumentError("Needle must be stationary with a known reference before pumping")
            self.pump.select_channel(channel)
            self.pump.set_units(0, channel=channel)
            self.pump.set_diameter(values["syringe_diameter_mm"], channel=channel)
            self.pump.set_rate(rate_ml_min, channel=channel)
            self.pump.set_volume(volume_ml, channel=channel)
            if not self._ack(self.pump.start(channel=channel, delay=0), "start"):
                raise PhysicalInstrumentError(f"Chemyx CH{channel}: START outcome is uncertain")
            self.clock.sleep(abs(volume_ml) / rate_ml_min * 60 + self.cfg["chemyx"]["extra_seconds"])
            if not self._ack(self.pump.stop(channel=channel), "stop"):
                raise PhysicalInstrumentError(f"Chemyx CH{channel}: STOP outcome is uncertain")
            self.retained[channel] = retained
        except BaseException:
            self.uncertain = True
            try:
                self.pump.stop(channel=channel)
            except BaseException:
                pass
            raise
        self.emit(f"Chemyx CH{channel}: {label} complete")

    def sample(self, acquire_callback):
        p = self.cfg["chemyx"]
        if self.needle.state.logical_position != 1:
            raise PhysicalInstrumentError("Sampling must begin at the existing needle UP position")
        self.transfer(1, -p["pre_sample_volume_ml"], p["sample_rate_ml_min"], "Pre-sample withdrawal")
        self.move_needle(0)
        self.transfer(1, -p["sample_volume_ml"], p["sample_rate_ml_min"], "Sample withdrawal")
        self.emit(f"Sampling: Settling for {p['settle_seconds']:g} seconds")
        self.clock.sleep(p["settle_seconds"])
        try:
            result = acquire_callback()
        except Exception as exc:
            result = {"status": "NMR_FAILED", "error": f"{type(exc).__name__}: {exc}"}
            self.emit(f"NMR FAILED: {exc}; completing physical cleanup")
        self.move_needle(1)
        self.transfer(1, p["pre_sample_volume_ml"] + p["sample_volume_ml"], p["return_rate_ml_min"], "Sample return")
        self.transfer(1, -p["wash_volume_ml"], p["wash_rate_ml_min"], "Wash withdrawal")
        self.transfer(1, p["wash_volume_ml"], p["wash_rate_ml_min"], "Wash return")
        return result

    def dose(self):
        d = self.cfg["channel_2"]
        if not d["enabled"]:
            raise PhysicalInstrumentError("Channel 2 addition is disabled")
        self.move_needle(0)
        self.transfer(2, d["volume_ml"], d["rate_ml_min"], "Reagent addition")
        self.move_needle(1)

    def stop(self):
        self.uncertain = True
        for ch in ([1, 2] if self.cfg["channel_2"]["enabled"] else [1]):
            try:
                self.pump.stop(channel=ch)
            except BaseException:
                pass
        try:
            self.needle.stop_best_effort()
        except BaseException:
            pass


@contextmanager
def setup_instruments(cfg, run_dir, *, mock, clock, emit):
    """Open only the existing drivers; mock state is confined to this fresh run."""
    validate_physical_config(cfg)
    a, p = cfg["arduino"], cfg["chemyx"]
    needle_cfg = deepcopy(DEFAULTS)
    needle_cfg["arduino"].update(port=a["port"], baud_rate=a["baud_rate"])
    needle_cfg["firmware"]["motion_enabled"] = True
    needle_cfg["motion"].update(maximum_speed_steps_s=a["speed_steps_s"], maximum_acceleration_steps_s2=a["acceleration_steps_s2"])
    needle_cfg["needle"].update({key: a[key] for key in ("min_position", "max_position", "home_position", "up_position", "steps_per_unit", "up_step_sign")})
    needle_cfg["needle"]["down_position"] = 0
    state_path = Path(run_dir) / "mock_state/needle_state.json" if mock else config.resolve_repo_path(a["state_path"])
    needle_cfg["needle"]["state_path"] = str(state_path)
    if mock:
        NeedleStateStore(state_path).save(NeedleState(logical_position=0, position_valid=True, reason="operator_confirmed_home", last_updated=datetime.now(timezone.utc).isoformat(), steps_per_unit=a["steps_per_unit"], up_step_sign=a["up_step_sign"]))
    with ExitStack() as stack:
        controller = stack.enter_context(controller_session(needle_cfg, "mock" if mock else "live", allow_motion=True, apply_runtime_config=True))
        controller.overall_timeout_s = float("inf")
        pump = stack.enter_context(Pump(port=p["port"], baud_rate=p["baud_rate"], channel=1, units=0, timeout=p["timeout_seconds"], response_delay=0 if mock else p["response_delay_seconds"], mock=mock))
        with redirect_stdout(io.StringIO()) as reference_message:
            needle = TrackedNeedle(controller, needle_cfg)
        for line in reference_message.getvalue().splitlines():
            emit(f"Arduino: {line}")
        rig = DemoRig(cfg, controller, needle, pump, clock, emit)
        try:
            yield rig
        except BaseException:
            rig.stop()
            raise
