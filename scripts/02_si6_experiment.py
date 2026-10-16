"""Level 2: configured, journaled three-instrument Si6 experiment."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.analysis.final_nmr_summary import summarize_run
from chemyx_lab.workflows import three_instrument_si6 as si6


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--live", action="store_true")
    modes.add_argument("--validate-config", action="store_true", help="Configuration only; never open physical instrument connections")
    parser.add_argument("--workflow-config", type=Path, help="Run recipe; defaults to current real two-stage YAML, or fast simulation YAML with --mock")
    parser.add_argument("--machine-config", type=Path, default=config.REPO_ROOT / "configs/machines/00_machine.local.yaml")
    parser.add_argument("--arduino-config", type=Path, default=config.REPO_ROOT / "arduino/configs/arduino.local.yaml")
    parser.add_argument("--mock-cycles-per-stage", type=int, default=4)
    args = parser.parse_args(argv)
    if args.workflow_config is None:
        args.workflow_config = config.REPO_ROOT / ("config_templates/experiments/si6_two_stage_fast_sim.yaml" if args.mock else "configs/experiments/si6_real_two_stage.yaml")
    mode = "LIVE" if args.live else "MOCK" if args.mock else "VALIDATE ONLY"
    print(f"Si6 EXPERIMENT | {mode}")
    try:
        raw, arduino, pump, nmr = si6.prepare(args.workflow_config, args.machine_config, args.arduino_config, mock=not (args.live or args.validate_config))
        print(f"EXPERIMENT: {raw['workflow'].get('experiment_id', raw['workflow']['name'])}\n"
              f"Recipe: {args.workflow_config}\nMachine: {args.machine_config}\nArduino: {args.arduino_config}\n"
              f"Resolved ports: Arduino={arduino['arduino'].get('port')} | Chemyx={pump.port}\n"
              f"NMR: {nmr.host}:{nmr.port} | scans={nmr.scans} | gain={nmr.receiver_gain} | auto_gain={nmr.auto_gain}")
        stages = si6.base.build_stages(raw["workflow"])
        if args.live and (len(stages) != 2 or not all(stage.completion and stage.completion.get("method") == "area_only" for stage in stages)):
            raise ValueError("The live automatic entry point requires the current two-stage area_only recipe. Use the separate legacy runner only for an intentionally manual workflow.")
        for index, stage in enumerate(stages, 1):
            slots = si6.base.duration_measurement_slots(stage.interval_minutes, stage.max_hours, stage.measure_immediately)
            limit = (f"Minimum completion time: {stage.min_completion_hours:g} hours\n"
                     "Observation limit: duration governs; no count cap" if stage.min_completion_hours is not None
                     else f"Normal observation cap: {stage.max_measurements}")
            print(f"STAGE {index} ({stage.name})\nSampling interval: {stage.interval_minutes:g} minutes\nMaximum duration / timed fallback deadline: {stage.max_hours:g} hours\n"
                  f"{limit}\nMaximum nominal scheduled acquisitions within duration: {slots} (slow cycles can reduce this)\n"
                  f"Chemical endpoint stopping enabled: {stage.plateau_stopping_enabled}")
        window = si6.tracked_window(raw, nmr)[1]
        confirmations = [stage.completion.get("consecutive_iterations", stage.completion.get("consecutive_confirmations")) for stage in stages if stage.completion]
        stable_intervals = confirmations or raw["analysis"].get("plateau_consecutive_intervals", 3)
        print(f"Tracked resonance: {nmr.target_ppm:g} +/- {window:g} ppm search; stable intervals required: {stable_intervals}; repeat rounds: {raw['workflow']['repeat_addition_rounds']}")
        print(f"Cycle values: {si6.cycle_values(raw)}")
        if not (args.mock or args.live):
            print("Configuration valid. No hardware opened; choose --mock or --live.")
            return 0
        if args.mock_cycles_per_stage < 1:
            raise ValueError("--mock-cycles-per-stage must be positive")
        if args.live:
            if input("Confirm rig at rest, loaded syringes and readiness; start the automatic live Si6 experiment? [y/N]: ").strip().lower() not in ("y", "yes"):
                raise RuntimeError("Live run not confirmed")
        identity = si6.RunIdentity("si6", args.mock)
        with si6.open_services(raw, arduino, pump, nmr, identity=identity, fast_mock_processing=args.mock,
                               config_sources={"workflow": str(args.workflow_config), "machine": str(args.machine_config), "arduino": str(args.arduino_config)}) as services:
            outcome = si6.run_experiment(services, mock_cycles_per_stage=args.mock_cycles_per_stage)
            print(f"Results: {services.paths.run_dir}")
        # Reporting runs only after the instrument session has closed. A report
        # failure must not change the experiment's existing outcome or exit code.
        try:
            print(f"Final NMR summary: {summarize_run(services.paths.run_dir)}")
        except Exception as exc:
            print(f"Final NMR summary unavailable ({type(exc).__name__}: {exc}); rerun scripts/nmr/summarize_run.py on this folder.")
        try:
            from chemyx_lab.analysis.phase_comparison import compare_run
            print(f"Post-session phase comparison: {compare_run(services.paths.run_dir)}")
        except Exception as exc:
            # Optional research must never change the production outcome.
            from chemyx_lab.analysis.phase_audit import write_json
            try:
                write_json(services.paths.final_dir / "phase_comparison_failure.json", {"error": str(exc)})
            except Exception as log_exc:
                print(f"Could not save optional comparison failure: {log_exc}")
            print(f"Post-session comparison unavailable: {exc}")
        print(f"Si6 EXPERIMENT: {outcome.status.value}: {outcome.message}")
        return outcome.exit_code
    except BaseException as exc:
        print(f"Si6 EXPERIMENT: FAIL ({type(exc).__name__}: {exc})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
