"""Supervised D3 STEP / D4 DIR needle operation with durable logical state."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401
from _common import controller_session

from arduino.python.config import load_arduino_config
from arduino.python.errors import MotionInterlockError, PositionUncertainError
from arduino.python.manual_jog import tracked_relative_jog
from arduino.python.needle_state import NeedleStateStore
from arduino.python.home_recovery import STOP_UNRESOLVED, restore_home
from arduino.python.protocol import bool_field


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "arduino.local.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "home", "set-home", "confirm-home", "up", "down", "return-home", "stop", "manual-up", "manual-down"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--steps", type=int,
                        help="UP/DOWN only: move this many motor steps instead of one calibrated increment "
                             "(needle.steps_per_unit). A size that is not a whole number of increments "
                             "resets the movement count and clears a saved HOME until set-home is run again.")
    parser.add_argument("--speed", type=int, default=100, help=argparse.SUPPRESS)
    parser.add_argument("--acceleration", type=int, default=500, help=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--mock", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_arduino_config(args.config)
    mode_name = "live" if args.live else "mock"
    action = {"home": "return-home", "set-home": "confirm-home"}.get(args.action, args.action)
    action = {"manual-up": "up", "manual-down": "down"}.get(action, action)
    if args.speed != 100 or args.acceleration != 500:
        parser.error("UP/DOWN use the configured motion speed and acceleration; adjust the configuration after physical verification")
    if args.steps is not None and (action not in ("up", "down") or args.steps < 1):
        parser.error("--steps applies only to up/down and must be a positive number of motor steps")
    home_action = action in ("return-home", "confirm-home")
    if args.live and action not in ("status", "stop"):
        cfg["firmware"]["motion_enabled"] = True
        if not home_action:
            # UP/DOWN move at once, without a confirmation prompt.
            print("Relative manual move; no physical position sensor. Inspect clear travel in the requested direction; the saved count is not proof of clearance.")
    if args.mock:
        cfg["firmware"]["motion_enabled"] = True
        cfg["needle"].update({"steps_per_unit": cfg["needle"].get("steps_per_unit") or 20,
                              "up_step_sign": cfg["needle"].get("up_step_sign") or 1})
        cfg["motion"].update({"maximum_speed_steps_s": cfg["motion"].get("maximum_speed_steps_s") or 100,
                              "maximum_acceleration_steps_s2": cfg["motion"].get("maximum_acceleration_steps_s2") or 300})
    with controller_session(cfg, mode_name, allow_motion=action in ("up", "down"), apply_runtime_config=action not in ("status", "stop") and not home_action) as controller:
        state_path = Path(cfg["results"]["run_root_dir"]) / "mock_needle_state.json" if args.mock else Path(cfg["needle"]["state_path"])
        if home_action:
            def confirm_home(prompt):
                if args.mock:
                    return True
                try:
                    return input(prompt).strip().lower() in ("y", "yes")
                except EOFError:
                    return False
            restore_home(controller, cfg, state_path, confirm=confirm_home,
                         force_reference=action == "confirm-home", mode=mode_name)
            return 0
        if action == "stop":
            # STOP must not depend on a readable/valid software position record.
            try:
                moving = controller.status().get("moving") != "false"
            except Exception:
                moving = True
            stop_verified = False
            try:
                reply = controller.stop()
                if not bool_field(reply.fields, "stopped") or bool_field(controller.status(), "moving"):
                    raise MotionInterlockError("STOP did not verify a stationary controller")
                stop_verified = True
            finally:
                # A failed STOP acknowledgment must not leave a formerly valid
                # position available for a later automatic HOME move.
                try:
                    saved = NeedleStateStore(state_path).load()
                except PositionUncertainError:
                    print("Corrupt position record retained. Re-establish HOME after inspection.")
                else:
                    if moving or not stop_verified or saved.reason in STOP_UNRESOLVED:
                        NeedleStateStore(state_path).save(replace(saved, position_valid=False, reason="stop_verified_position_unknown" if stop_verified else "manual_stop_failed", last_updated=datetime.now(timezone.utc).isoformat()))
            print("STOP sent. No HOME reference was inferred.")
            return 0
        if action == "status":
            saved = NeedleStateStore(state_path).load()
            firmware = controller.status()
            valid = saved.position_valid and not NeedleStateStore(state_path).home_pending_path.exists()
            print(f"Last known needle position: {saved.logical_position if saved.logical_position is not None else 'unknown'}; "
                  f"valid={str(valid).lower()}; source=software_estimate; "
                  f"firmware moving={firmware['moving']}; fault={firmware['fault']}")
            return 0
        saved = tracked_relative_jog(controller, cfg, action, state_path=state_path, steps=args.steps)
        print(f"Needle moved {action.upper()} {args.steps or cfg['needle']['steps_per_unit']} steps.")
        if saved.logical_position is None:
            print(f"Needle movement count: reset ({args.steps} is not a whole number of "
                  f"{cfg['needle']['steps_per_unit']}-step increments); calibrated HOME=False. "
                  "Run set-home at the sampling position before an experiment.")
        else:
            print(f"Needle movement count: {saved.logical_position:+d}; calibrated HOME={saved.position_valid}; source=software_estimate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
