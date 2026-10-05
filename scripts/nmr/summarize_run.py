"""Summarize an existing run offline without reprocessing FIDs or contacting instruments."""
from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab.analysis.final_nmr_summary import summarize_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_folder", type=Path)
    args = parser.parse_args(argv)
    try:
        output = summarize_run(args.run_folder)
    except (OSError, ValueError) as exc:
        print(f"NMR summary failed: {exc}")
        return 1
    print(f"Final NMR summary: {output}")
    print("Missing data and timing limitations are recorded in manifest.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
