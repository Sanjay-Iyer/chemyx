"""Analyze one .dx file or a directory of .dx files near 6.1 ppm."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from chemyx_lab import config
from chemyx_lab.nmr import NmrProcessingError, analyze_dx_peak, iter_dx_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Find a small NMR peak near target ppm.")
    parser.add_argument("path", help="DX file or directory containing DX files")
    parser.add_argument("--target", type=float, default=config.NMR_TARGET_PPM)
    parser.add_argument("--window", type=float, default=config.NMR_PEAK_WINDOW_PPM)
    args = parser.parse_args()

    files = list(iter_dx_files(Path(args.path)))
    if not files:
        print(f"No .dx files found under {args.path}")
        return 1

    print("file, target_ppm, peak_ppm, snr, peak_height, baseline, noise")
    exit_code = 0
    for dx_file in files:
        try:
            result = analyze_dx_peak(dx_file, target_ppm=args.target, window_ppm=args.window)
        except NmrProcessingError as exc:
            print(f"{dx_file}, ERROR, {exc}")
            exit_code = 1
            continue
        print(
            f"{result.source.name}, {result.target_ppm:.4f}, "
            f"{result.peak_ppm:.4f}, {result.snr:.2f}, "
            f"{result.peak_height:.6g}, {result.baseline:.6g}, {result.noise:.6g}"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
