"""Validate both Si6 chemistry trends through production spectrum analysis; mocks only."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.testing.si6_synthetic_analysis import validate_all


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="New output directory; existing paths are never overwritten")
    args = parser.parse_args()
    output = args.output_dir or config.REPO_ROOT / "test_tmp_si6_synthetic_validation" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    try:
        validate_all(output)
    except Exception as exc:
        print(f"FAIL synthetic validation: {type(exc).__name__}: {exc}")
        print("SOFTWARE ONLY | HARDWARE NOT TESTED | THRESHOLDS NOT COMMISSIONED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
