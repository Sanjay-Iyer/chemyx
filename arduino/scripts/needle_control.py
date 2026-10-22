"""Manual D3 STEP / D4 DIR needle control: status, set-home, home, up, down, stop.

The position is two plain-text integers in runs/arduino, shared with scripts
20/21/22 (see arduino/python/simple_needle.py): the logical counter (SET-HOME and
HOME -> 0, UP +1, DOWN -1 per completed command) and the motor steps from HOME.
No saved fault, validity or recovery state is checked, so an earlier failure
never blocks a later command.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from arduino.python.config import REPO_ROOT, load_arduino_config
from arduino.python.protocol import bool_field
from arduino.python.simple_needle import NeedleError, SimpleNeedle


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "arduino.local.yaml"


def make_needle(cfg, *, mock, log):
    n, m = cfg["needle"], cfg["motion"]
    folder = Path(cfg["results"]["run_root_dir"])
    folder = folder if folder.is_absolute() else REPO_ROOT / folder
    return SimpleNeedle(port=cfg["arduino"]["port"], baud_rate=cfg["arduino"]["baud_rate"],
                        steps_per_unit=n["steps_per_unit"], up_step_sign=n["up_step_sign"],
                        speed_steps_s=m["maximum_speed_steps_s"],
                        acceleration_steps_s2=m["maximum_acceleration_steps_s2"],
                        min_position=n["min_position"], max_position=n["max_position"],
                        position_file=folder / "simple_needle_position.txt", log=log, mock=mock,
                        expected_version=cfg["arduino"].get("expected_version"))


def confirm(prompt, mock):
    if mock:
        return True
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "home", "set-home", "confirm-home", "up", "down", "return-home", "stop", "manual-up", "manual-down"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--steps", type=int,
                        help="UP/DOWN only: move this many motor steps instead of one calibrated increment "
                             "(needle.steps_per_unit). The position counter still changes by one; HOME "
                             "uses the saved motor-step count.")
    parser.add_argument("--speed", type=int, default=100, help=argparse.SUPPRESS)
    parser.add_argument("--acceleration", type=int, default=500, help=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--mock", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_arduino_config(args.config)
    action = {"home": "return-home", "set-home": "confirm-home"}.get(args.action, args.action)
    action = {"manual-up": "up", "manual-down": "down"}.get(action, action)
    if args.speed != 100 or args.acceleration != 500:
        parser.error("UP/DOWN use the configured motion speed and acceleration; adjust the configuration after physical verification")
    if args.steps is not None and (action not in ("up", "down") or args.steps < 1):
        parser.error("--steps applies only to up/down and must be a positive number of motor steps")
    if args.mock:
        cfg["needle"].update({"steps_per_unit": cfg["needle"].get("steps_per_unit") or 20,
                              "up_step_sign": cfg["needle"].get("up_step_sign") or 1})
        cfg["motion"].update({"maximum_speed_steps_s": cfg["motion"].get("maximum_speed_steps_s") or 100,
                              "maximum_acceleration_steps_s2": cfg["motion"].get("maximum_acceleration_steps_s2") or 300})
    log = logging.Logger("needle_control", logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)
    needle = None
    try:
        needle = make_needle(cfg, mock=args.mock, log=log)
        needle.connect(prepare_motion=action in ("up", "down", "return-home"))
        if action == "status":
            firmware = needle.controller.status()
            print(f"Saved needle {needle.describe()}; firmware moving={firmware['moving']}; "
                  f"fault={firmware['fault']}; enabled={firmware['enabled']}")
        elif action == "stop":
            needle.stop()
            print("STOP sent. The saved position was not changed.")
        elif action == "confirm-home":
            if bool_field(needle.controller.status(), "moving"):
                raise NeedleError("The Arduino reports motion; run stop first")
            if not confirm("Needle stopped at the correct sampling position? Record it as HOME = 0 [y/N]: ", args.mock):
                print("HOME not changed.")
                return 1
            needle.set_home()
        elif action == "return-home":
            if not needle.has_home:
                raise NeedleError("No HOME recorded: place the needle at the sampling position and run set-home")
            if not confirm(f"Move the needle to HOME from {needle.describe()}? [y/N]: ", args.mock):
                print("No movement.")
                return 1
            needle.home()
            print("Needle at HOME (0). If an earlier move was interrupted, confirm the position by eye.")
        else:
            # UP/DOWN move at once, without a confirmation prompt.
            print("Relative manual move; no physical position sensor. Inspect clear travel in the requested direction.")
            needle.move(action, args.steps)
    except KeyboardInterrupt:
        if needle is not None:
            needle.stop()
        print("Interrupted: STOP sent. Check the needle by eye; run set-home before relying on the saved position.")
        return 130
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if needle is not None:
            needle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
