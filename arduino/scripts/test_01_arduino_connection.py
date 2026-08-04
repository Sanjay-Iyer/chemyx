"""Stage 1: Arduino USB, serial, PING, and built-in LED hello world."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
from _common import (
    add_standard_arguments,
    configure_logging,
    confirm_live,
    controller_session,
    execution_mode,
    load_cli_config,
    run_recorded,
    serialized_events,
)

from arduino.python.config import require_live, test1_missing
from arduino.python.workflows import HardDeadline, run_test_01

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "arduino.example.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Arduino-only connection and LED test")
    add_standard_arguments(parser, motion_capable=False, default_config=DEFAULT_CONFIG)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    cfg = load_cli_config(args)
    if args.list_ports:
        return 0
    mode = execution_mode(args)
    if mode == "validate_only":
        print("Configuration syntax valid. No port was opened.")
        return 0
    if mode == "dry_run":
        print("Test 1 dry run: READY -> PING/PONG -> STATUS -> LED ON/OFF -> BLINK.")
        print("No port was opened and no result record was created.")
        return 0
    failure_context = {"motion_attempted": False}

    def runner(_run_dir):
        if mode == "live":
            require_live("ARDUINO TEST 1", test1_missing(cfg))
            confirm_live("RUN ARDUINO TEST 1")
        deadline = HardDeadline(min(59.0, cfg["arduino"]["overall_timeout_s"]))
        with controller_session(cfg, mode, allow_motion=False) as controller:
            failure_context["firmware_version"] = controller.identity.get("version")
            deadline.check("Test 1 connection")
            state = run_test_01(controller, deadline)
            return state, controller.identity.get("version"), {"arduino_only_wiring_confirmed": mode == "live"}, serialized_events(controller)

    return run_recorded(
        test_name="test_01_arduino_connection",
        mode=mode,
        cfg=cfg,
        runner=runner,
        failure_context=failure_context,
    )


if __name__ == "__main__":
    raise SystemExit(main())
