"""Real toluene full-system soak; count/time limits, no chemistry endpoints."""
from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.workflows import three_instrument_si6 as si6
from chemyx_lab.workflows.si6_soak import run_soak


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Contact real instruments; otherwise load configuration only")
    parser.add_argument("--workflow-config", type=Path, default=config.REPO_ROOT / "configs/experiments/si6_real_toluene_soak_test.yaml")
    parser.add_argument("--machine-config", type=Path, default=config.REPO_ROOT / "configs/machines/si6_real_COM4.yaml")
    parser.add_argument("--arduino-config", type=Path, default=config.REPO_ROOT / "arduino/configs/arduino_real_COM3.yaml")
    parser.add_argument("--acknowledge-review", metavar="RUN_ID", help="After physical reconciliation, acknowledge a previous failed run")
    args = parser.parse_args(argv)
    try:
        prepared = si6.prepare(args.workflow_config, args.machine_config, args.arduino_config,
                               mock=not args.live, allow_soak=True, acknowledged_review=args.acknowledge_review)
        raw, arduino, pump, nmr = prepared
        settings = raw["soak_test"]
        print(f"TOLUENE SOAK | Arduino {arduino['arduino']['port']} | Chemyx {pump.port} | NMR {nmr.host}:{nmr.port}")
        print(f"{settings['iterations']} iterations, {settings['interval_minutes']:g} min start-to-start, maximum {settings['max_hours']:g} h, {nmr.scans} scans; no chemistry endpoints.")
        print(f"Channel 2: {settings['channel2']}")
        if not args.live:
            print("Configuration valid. No hardware opened; choose --live to run.")
            return 0
        if input("Confirm toluene-only rig, loaded syringes and confirmed HOME; start live soak? [y/N]: ").strip().lower() not in ("y", "yes"):
            print("Soak aborted before opening instruments.")
            return 3
        with si6.open_services(*prepared, identity=si6.RunIdentity("soak", False), acknowledged_review=args.acknowledge_review) as services:
            outcome = run_soak(services)
            print(f"Results: {services.paths.run_dir}")
        print(f"TOLUENE SOAK: {outcome.status.value}: {outcome.message}")
        return outcome.exit_code
    except KeyboardInterrupt:
        print("TOLUENE SOAK: interrupted; stop handling requested. Inspect the rig before another run.")
        return 3
    except Exception as exc:
        print(f"TOLUENE SOAK: FAIL ({type(exc).__name__}: {exc})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
