"""Level 1: fail-fast diagnostic of the shared three-instrument interfaces."""
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
    selection = parser.add_mutually_exclusive_group()
    for name in ("needle", "pump", "nmr", "process", "all"):
        selection.add_argument(f"--{name}-only" if name != "all" else "--all", dest="selection", action="store_const", const=name)
    parser.add_argument("--workflow-config", type=Path, default=config.REPO_ROOT / "configs/experiments/02_si6_automated_nmr.yaml")
    parser.add_argument("--machine-config", type=Path, default=config.REPO_ROOT / "configs/machines/00_machine.local.yaml")
    parser.add_argument("--arduino-config", type=Path, default=config.REPO_ROOT / "arduino/configs/arduino.example.yaml")
    parser.add_argument("--input-dx", type=Path, help="existing JCAMP file for --process-only")
    parser.add_argument("--acknowledge-review", metavar="RUN_ID", help="after reconciling a previous live run that requires review, name it to allow this live run")
    args = parser.parse_args(argv)
    mode = "LIVE" if args.live else "MOCK" if args.mock else "VALIDATE ONLY"
    selected = args.selection or "all"
    print(f"THREE-INSTRUMENT SYSTEM TEST | {mode} | {selected}")
    try:
        raw, arduino, pump, nmr = si6.prepare(args.workflow_config, args.machine_config, args.arduino_config, mock=not args.live or selected == "process", require_needle_live=selected not in ("nmr", "process"), acknowledged_review=args.acknowledge_review)
        low, high = nmr.target_ppm - float(raw["analysis"]["detection_window_ppm"]), nmr.target_ppm + float(raw["analysis"]["detection_window_ppm"])
        print(f"Arduino: {arduino['arduino']['expected_device']} {arduino['firmware']['version']}; Chemyx: configured; NMR: {nmr.route}; processing: process_fid; tracked window {low:.2f}-{high:.2f} ppm")
        if not (args.mock or args.live):
            print("Configuration valid. No hardware opened; choose --mock or --live.")
            return 0
        if args.live and selected != "process":
            if not sys.stdin.isatty() or input("Type RUN THREE INSTRUMENT TEST to contact hardware: ").strip() != "RUN THREE INSTRUMENT TEST":
                raise RuntimeError("Live run not confirmed")
        if selected == "process":
            source = args.input_dx or (si6.MOCK_NMR_FIXTURE if args.mock else None)
            if source is None or not source.is_file():
                raise ValueError("--process-only requires --input-dx for a live-data file")
            paths, processed, row = si6.run_processing_only(raw, nmr, source, mock=args.mock)
            print(f"[PASS] NMR processing: {processed}\n[PASS] NMR analysis: tracked peak {row['peak_ppm']:.3f} ppm, area {row['peak_area']:g}")
            print(f"Results: {paths.run_dir}")
            print("THREE-INSTRUMENT SYSTEM TEST: PASS (processing only; no instrument contacted)")
            return 0
        if selected == "nmr":
            paths = si6.run_nmr_only(raw, pump, nmr, mock=args.mock)
            print(f"Results: {paths.run_dir}\nTHREE-INSTRUMENT SYSTEM TEST: PASS (NMR only; no serial ports opened)")
            return 0
        identity = si6.RunIdentity("diagnostic", args.mock, selected)
        with si6.open_services(raw, arduino, pump, nmr, identity=identity, acknowledged_review=args.acknowledge_review) as services:
            si6.preflight(services, check_nmr=selected in ("all", "nmr"))
            si6.run_diagnostic(services, selected)
            print(f"Results: {services.paths.run_dir}")
        print("THREE-INSTRUMENT SYSTEM TEST: PASS")
        return 0
    except BaseException as exc:
        print(f"THREE-INSTRUMENT SYSTEM TEST: FAIL ({type(exc).__name__}: {exc})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
