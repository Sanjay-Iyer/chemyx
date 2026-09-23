"""Supervised D3 STEP / D4 DIR needle operation with durable logical state."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
from _common import confirm_live, controller_session

from arduino.python.config import load_arduino_config
from arduino.python.errors import PositionUncertainError
from arduino.python.needle_state import NeedleStateStore, TrackedNeedle


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "arduino.example.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "confirm-home", "up", "down", "return-home", "stop"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--mock", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_arduino_config(args.config)
    mode_name = "live" if args.live else "mock"
    action = args.action
    if args.live and action not in ("status", "stop"):
        if cfg["signal_interface"].get("wiring_reviewed") is not True:
            raise SystemExit("Review the validated D3/D4 wiring and set signal_interface.wiring_reviewed=true")
        if cfg["safety"].get("emergency_disconnect_documented") is not True:
            raise SystemExit("Document the physical 24 V driver-power disconnect before motion")
        if cfg["firmware"].get("motion_enabled") is not True:
            raise SystemExit("Set firmware.motion_enabled=true after supervised review")
        confirm_live("CONFIRM NEEDLE AT HOME ZERO" if action == "confirm-home" else f"MOVE NEEDLE {action.upper()}")
    if args.mock:
        cfg["firmware"]["motion_enabled"] = True
        cfg["needle"].update({"steps_per_unit": cfg["needle"].get("steps_per_unit") or 20,
                              "up_step_sign": cfg["needle"].get("up_step_sign") or 1})
        cfg["motion"].update({"maximum_speed_steps_s": cfg["motion"].get("maximum_speed_steps_s") or 100,
                              "maximum_acceleration_steps_s2": cfg["motion"].get("maximum_acceleration_steps_s2") or 300})
    with controller_session(cfg, mode_name, allow_motion=action in ("up", "down", "return-home"), apply_runtime_config=True) as controller:
        state_path = Path(cfg["results"]["run_root_dir"]) / "mock_needle_state.json" if args.mock else Path(cfg["needle"]["state_path"])
        if action == "status":
            saved = NeedleStateStore(state_path).load()
            firmware = controller.status()
            print(f"Last known needle position: {saved.logical_position if saved.logical_position is not None else 'unknown'}; "
                  f"valid={str(saved.position_valid).lower()}; source=software_estimate; "
                  f"firmware moving={firmware['moving']}; fault={firmware['fault']}")
            return 0
        try:
            needle = TrackedNeedle(controller, cfg, state_path=state_path)
        except PositionUncertainError:
            if action != "confirm-home":
                raise
            backup = NeedleStateStore(state_path).quarantine_corrupt()
            print(f"Corrupt state retained at {backup}; physically inspected HOME will establish a new reference")
            needle = TrackedNeedle(controller, cfg, state_path=state_path)
        if action == "confirm-home":
            needle.confirm_home(operator_confirmed=True)
        elif action == "up":
            needle.needle_up()
        elif action == "down":
            needle.needle_down()
        elif action == "return-home":
            needle.return_to_home()
        elif action == "stop":
            needle.stop()
        status = needle.status()
        print(f"Needle logical position: {status['logical_position']}; valid={status['position_valid']}; "
              f"source={status['position_source']}; firmware moving={status['moving']}; fault={status['fault']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
