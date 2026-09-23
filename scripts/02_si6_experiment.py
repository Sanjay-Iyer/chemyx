"""Level 2: configured, journaled three-instrument Si6 experiment."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.workflows import three_instrument_si6 as si6


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--live", action="store_true")
    parser.add_argument("--workflow-config", type=Path, default=config.REPO_ROOT / "configs/experiments/02_si6_automated_nmr.yaml")
    parser.add_argument("--machine-config", type=Path, default=config.REPO_ROOT / "configs/machines/00_machine.local.yaml")
    parser.add_argument("--arduino-config", type=Path, default=config.REPO_ROOT / "arduino/configs/arduino.example.yaml")
    parser.add_argument("--mock-cycles-per-stage", type=int, default=4)
    parser.add_argument("--acknowledge-review", metavar="RUN_ID", help="after reconciling a previous live run that requires review, name it to allow this live run")
    args = parser.parse_args(argv)
    mode = "LIVE" if args.live else "MOCK" if args.mock else "VALIDATE ONLY"
    print(f"Si6 EXPERIMENT | {mode}")
    try:
        raw, arduino, pump, nmr = si6.prepare(args.workflow_config, args.machine_config, args.arduino_config, mock=not args.live, acknowledged_review=args.acknowledge_review)
        stages = si6.base.build_stages(raw["workflow"])
        for stage in stages:
            print(f"{stage.name}: every {stage.interval_minutes:g} min for up to {stage.max_hours:g} h ({stage.max_measurements} measurements), plateau stop={stage.plateau_stopping_enabled}")
        window = float(raw["analysis"]["detection_window_ppm"])
        print(f"Tracked resonance: {nmr.target_ppm:g} +/- {window:g} ppm; stable intervals required: {raw['analysis']['plateau_consecutive_intervals']}; repeat rounds: {raw['workflow']['repeat_addition_rounds']}")
        print(f"Cycle values: {si6.cycle_values(raw)}")
        if not (args.mock or args.live):
            print("Configuration valid. No hardware opened; choose --mock or --live.")
            return 0
        if args.mock_cycles_per_stage < 1:
            raise ValueError("--mock-cycles-per-stage must be positive")
        if args.live:
            if not sys.stdin.isatty() or input("Type RUN SI6 THREE INSTRUMENTS to contact hardware: ").strip() != "RUN SI6 THREE INSTRUMENTS":
                raise RuntimeError("Live run not confirmed")
        identity = si6.RunIdentity("si6", args.mock)
        with si6.open_services(raw, arduino, pump, nmr, identity=identity, fast_mock_processing=args.mock, acknowledged_review=args.acknowledge_review) as services:
            outcome = si6.run_experiment(services, mock_cycles_per_stage=args.mock_cycles_per_stage)
            print(f"Results: {services.paths.run_dir}")
        print(f"Si6 EXPERIMENT: {outcome.status.value}: {outcome.message}")
        return outcome.exit_code
    except BaseException as exc:
        print(f"Si6 EXPERIMENT: FAIL ({type(exc).__name__}: {exc})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
