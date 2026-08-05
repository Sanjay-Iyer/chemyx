"""Stage 2: small forward/backward movement of a mechanically unloaded motor."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import _bootstrap  # noqa: F401
from _common import (
    add_standard_arguments,
    bounded_console_input,
    configure_logging,
    confirm_live,
    controller_session,
    execution_mode,
    load_cli_config,
    run_recorded,
    serialized_events,
)

from arduino.python.config import require_live, test2_missing
from arduino.python.results import unresolved_live_motion_failure
from arduino.python.workflows import HardDeadline, run_test_02

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "arduino.example.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unloaded stepper motor hello world")
    add_standard_arguments(parser, motion_capable=True, default_config=DEFAULT_CONFIG)
    return parser


def _mock_cfg(cfg: dict) -> dict:
    value = deepcopy(cfg)
    value["firmware"]["motion_enabled"] = True
    value["signal_interface"]["signal_inverted"] = False
    value["driver"]["enable_active_low"] = True
    value["motion"].update({"test_02_steps": 200, "test_02_speed_steps_s": 300})
    return value


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    cfg = load_cli_config(args)
    if args.list_ports:
        return 0
    mode = execution_mode(args)
    if mode == "validate_only":
        missing = test2_missing(cfg)
        print("Configuration syntax valid. No port was opened.")
        print(f"Live Test 2 missing requirements: {len(missing)}")
        return 0
    if mode == "dry_run" or args.preflight_only:
        missing = test2_missing(cfg)
        print("Test 2 preflight only. No port was opened and no motor command was sent.")
        for item in missing:
            print(f"MISSING: {item}")
        return 0 if mode != "live" or not missing else 1
    run_cfg = _mock_cfg(cfg) if mode == "mock" else cfg
    failure_context = {"motion_attempted": False}

    def runner(_run_dir):
        if mode == "live":
            missing = test2_missing(run_cfg)
            if unresolved_live_motion_failure(run_cfg["results"]["run_root_dir"], run_cfg):
                missing.append("Documented operator inspection after the latest failed live motion")
            require_live("LIVE MOTOR TEST", missing)
            confirm_live("RUN ARDUINO TEST 2")
        deadline = HardDeadline(min(120.0, run_cfg["safety"]["hard_runtime_limit_s"]))
        with controller_session(
            run_cfg,
            mode,
            allow_motion=True,
            motion_dispatch_callback=lambda: failure_context.update(motion_attempted=True),
            apply_runtime_config=True,
        ) as controller:
            failure_context["firmware_version"] = controller.identity.get("version")
            state = run_test_02(
                controller,
                run_cfg,
                deadline,
                sleep_fn=(lambda _seconds: None) if mode == "mock" else __import__("time").sleep,
            )
            version = controller.identity.get("version")
            events = serialized_events(controller)
        if mode == "mock":
            observations = {
                key: "SYNTHETIC MOCK OBSERVATION"
                for key in ("direction", "noise", "vibration", "temperature", "approximate_return_position")
            }
            observations["operator_acceptance"] = "MOCK ONLY"
        else:
            observations = {
                key: bounded_console_input(f"Record {key.replace('_', ' ')}: ", deadline)
                for key in ("direction", "noise", "vibration", "temperature", "approximate_return_position")
            }
            if any(not value for value in observations.values()):
                raise RuntimeError("Every Test 2 observation must be recorded")
            acceptance = bounded_console_input(
                "Type ACCEPT TEST 2 OBSERVATIONS after reviewing the unloaded motor: ", deadline
            )
            if acceptance != "ACCEPT TEST 2 OBSERVATIONS":
                raise RuntimeError("Test 2 observations were not accepted")
            observations["operator_acceptance"] = acceptance
        confirmations = {
            "motor_mechanically_disconnected": run_cfg["motor"].get("mechanically_disconnected_for_test_02", False),
            "shaft_safe_to_rotate": run_cfg["safety"].get("operator_shaft_safe_confirmed", False),
            "post_run_observations": observations,
        }
        return state, version, confirmations, events

    return run_recorded(
        test_name="test_02_unloaded_motor",
        mode=mode,
        cfg=run_cfg,
        runner=runner,
        failure_context=failure_context,
    )


if __name__ == "__main__":
    raise SystemExit(main())
