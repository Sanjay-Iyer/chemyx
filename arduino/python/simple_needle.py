"""Simple needle control shared by needle_control.py and scripts 20/21/22.

Two plain-text files, one signed integer each, side by side (runs/arduino):

  simple_needle_position.txt  logical counter: SET-HOME/HOME -> 0, +1 per completed
                              UP command, -1 per completed DOWN command (any --steps)
  simple_needle_steps.txt     motor steps from HOME (UP positive); used only to move
                              back to HOME, because --steps sizes can differ

Both are written only after the Arduino reports DONE. Nothing else is saved: no
validity flags, fault codes, journals or recovery state, so an earlier failure
never blocks a later command. After a move that did not finish, the files keep
their previous values; check the needle by eye and run set-home before moving it.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.controller import NeedleController
from arduino.python.protocol import bool_field
from arduino.python.transport import SerialTransport

FIRMWARE_MOVE_LIMIT_S = 120
CHECK_BY_EYE = "the needle may not be where the saved position says; check it by eye, then run set-home"


class NeedleError(RuntimeError):
    """A needle command failed now. Nothing about it is saved for later runs."""


def _read_int(path):
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None  # Missing or unreadable: treated as "no HOME recorded".


def _write_int(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(f"{int(value)}\n", encoding="utf-8")
    os.replace(temporary, path)


class SimpleNeedle:
    def __init__(self, *, port, baud_rate, steps_per_unit, up_step_sign, speed_steps_s,
                 acceleration_steps_s2, min_position, max_position, position_file, log,
                 mock=False, expected_version=None):
        if type(steps_per_unit) is not int or steps_per_unit < 1 or up_step_sign not in (-1, 1):
            raise NeedleError("Set the verified needle steps_per_unit and up_step_sign in the configuration")
        self.log, self.mock = log, mock
        self.unit, self.sign = steps_per_unit, up_step_sign
        self.speed, self.acceleration = int(speed_steps_s), int(acceleration_steps_s2)
        self.lowest, self.highest = int(min_position) * self.unit, int(max_position) * self.unit
        position_file = Path(position_file)
        if mock:  # Rehearsals never touch the real counter.
            position_file = position_file.with_name(position_file.stem + "_mock" + position_file.suffix)
        self.position_path = position_file
        self.steps_path = position_file.with_name(position_file.name.replace("position", "steps"))
        self.position, self.steps = _read_int(self.position_path), _read_int(self.steps_path)
        self.known = True  # This process only: False after a move that did not report DONE.
        self.port = "mock Arduino" if mock else port
        transport = (FakeArduinoTransport(runtime_configurable=True) if mock else
                     SerialTransport(port, int(baud_rate), read_timeout_s=0.1, write_timeout_s=1.0))
        self.controller = NeedleController(transport, expected_version=expected_version, allow_motion=True)

    @property
    def has_home(self):
        return self.position is not None and self.steps is not None

    def describe(self):
        if not self.has_home:
            return "no HOME set (run set-home at the sampling position)"
        where = "HOME" if self.steps == 0 else f"{self.steps:+d} motor steps from HOME"
        return f"position {self.position:+d} ({where})" if self.position else f"position 0 ({where})"

    def connect(self, *, prepare_motion=True):
        c = self.controller
        c.open()
        c.overall_timeout_s = math.inf  # The driver's per-session cap would end a six-hour run.
        self.log.info(f"Arduino: connected on {self.port} (firmware {c.identity.get('version')})")
        if not prepare_motion:
            return
        status = c.status()
        if bool_field(status, "moving"):
            self.log.warning("Arduino: was still moving; sending STOP")
            c.stop()
        if status.get("fault") != "NONE":
            self.log.warning(f"Arduino: clearing firmware fault {status.get('fault')} left from earlier")
            c.clear_fault()
        if bool_field(status, "enabled"):
            c.disable()  # Software motion arm only (ENA is not wired); lets the settings below apply.
        c.configure_runtime({"firmware": {"motion_enabled": True}, "signal_interface": {"signal_inverted": False},
                             "motion": {"maximum_speed_steps_s": self.speed,
                                        "maximum_acceleration_steps_s2": self.acceleration}})

    def _save(self, position, steps):
        _write_int(self.position_path, position)
        _write_int(self.steps_path, steps)
        self.position, self.steps = position, steps

    def set_home(self):
        """Record the current stopped position as HOME = 0. No movement."""
        if bool_field(self.controller.status(), "moving"):
            raise NeedleError("The Arduino reports motion; run stop first")
        self._save(0, 0)
        self.known = True
        self.log.info(f"Arduino: HOME set; position 0 saved in {self.position_path}")

    def move(self, direction, steps=None):
        """UP or DOWN by `steps` motor steps (default one calibrated increment)."""
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        steps = self.unit if steps is None else steps
        if type(steps) is not int or steps < 1:
            raise ValueError("steps must be a positive whole number of motor steps")
        self._jog(steps if direction == "up" else -steps, counter=1 if direction == "up" else -1)

    def home(self):
        """Move back to HOME by the saved motor-step count, then counter = 0."""
        if not self.has_home:
            raise NeedleError("No HOME recorded: place the needle at the sampling position and run set-home")
        if self.steps == 0:
            self._save(0, 0)
            self.log.info("Arduino: already at HOME (0)")
            return
        self._jog(-self.steps, home=True)

    def move_to_units(self, units):
        """20/21/22: go to an absolute position in increments (0 = HOME, 1 = UP)."""
        if not self.has_home:
            raise NeedleError("No HOME recorded: place the needle at the sampling position and run set-home")
        target = round(units * self.unit)
        if target == 0:
            self.home()
        elif target != self.steps:
            self._jog(target - self.steps, counter=1 if target > self.steps else -1)

    def _jog(self, delta, *, counter=0, home=False):
        if not self.known:
            raise NeedleError(f"An earlier move in this run did not finish: {CHECK_BY_EYE}")
        span = self.highest - self.lowest
        if abs(delta) > span:
            raise NeedleError(f"{abs(delta)} steps exceeds the configured needle travel of {span} steps")
        if self.has_home and not self.lowest <= self.steps + delta <= self.highest:
            raise NeedleError(f"Travel limit: {self.steps:+d} -> {self.steps + delta:+d} steps is outside "
                              f"[{self.lowest:+d}, {self.highest:+d}] (-3..+5 increments from HOME)")
        if abs(delta) / self.speed + 2 * self.speed / self.acceleration + 5 >= FIRMWARE_MOVE_LIMIT_S:
            raise NeedleError("Move cannot finish inside the firmware's 120-second timeout")
        c = self.controller
        try:
            status = c.status()
            if bool_field(status, "moving") or status.get("fault") != "NONE":
                raise NeedleError(f"Arduino not ready: moving={status.get('moving')} fault={status.get('fault')}")
            if not bool_field(status, "enabled"):
                c.enable()
            start, motor = int(status["commanded_position_steps"]), delta * self.sign
            self.known = False
            done = c.jog(motor, self.speed)
            if int(done.fields.get("position_steps", start)) != start + motor:
                raise NeedleError(f"Arduino reported {done.fields.get('position_steps')} steps, "
                                  f"expected {start + motor}: {CHECK_BY_EYE}")
        except NeedleError:
            raise
        except BaseException as exc:
            # The driver turns Ctrl+C during a move into PositionUncertainError; keep it a Ctrl+C.
            if isinstance(exc, KeyboardInterrupt) or isinstance(exc.__cause__, KeyboardInterrupt):
                self.log.warning(f"Arduino: move interrupted; {CHECK_BY_EYE}")
                raise KeyboardInterrupt from exc
            stage = "" if self.known else f"; {CHECK_BY_EYE}"
            raise NeedleError(f"Arduino move failed: {type(exc).__name__}: {exc}{stage}") from exc
        self.known = True
        direction = "UP" if delta > 0 else "DOWN"
        if not self.has_home:
            self.log.info(f"Arduino: needle {direction} {abs(delta)} steps (no HOME set: position not saved)")
            return
        if home:
            self._save(0, 0)
        else:
            self._save(self.position + counter, self.steps + delta)
        self.log.info(f"Arduino: needle {direction} {abs(delta)} steps -> {self.describe()}")

    def stop(self):
        """Best-effort STOP; resends without waiting if the reply stream is out of step. Files unchanged."""
        c = self.controller
        try:
            c.stop()
            self.log.info("Arduino: STOP sent")
            return True
        except BaseException as exc:
            self.log.warning(f"Arduino: STOP reply not read ({type(exc).__name__}: {exc}); resending")
        try:
            c._sequence += 1
            c.transport.write_line(f"{c._sequence} STOP", 1.0, None)
            return True
        except BaseException as exc:
            self.log.error(f"Arduino: STOP could not be sent ({exc}); switch off motor power if it is moving")
            return False

    def close(self):
        try:
            self.controller.close()
        except Exception as exc:
            self.log.warning(f"Arduino: port close warning: {exc}")
