"""Optional 08 review worker: independent SciPy processing, no instrument handles."""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import subprocess
import sys

from .. import config
from . import si6_stage2only_analysis as review

PROCESSOR = config.REPO_ROOT / "scripts/nmr/process_stage2only_scipy.py"
ANALYSIS_CONFIG = config.REPO_ROOT / "configs/nmr/analysis.yaml"
PROFILE = config.REPO_ROOT / "configs/nmr/stage2only_scipy.yaml"
PROCESSING_TIMEOUT_SECONDS = 600


def process_snapshot(run, snapshot, sources, dataset, *, runner=None, timeout=PROCESSING_TIMEOUT_SECONDS):
    """Run one cumulative numerical job with a hard, analysis-only timeout."""
    if not sources:
        raise ValueError("No explicit raw inputs; never use a configured fallback dataset")
    command = [sys.executable, str(PROCESSOR), *[s["raw_file"] for s in sources],
        "--config", str(ANALYSIS_CONFIG), "--profile", str(PROFILE), "--phase-method", "acme",
        "--dataset-display-name", dataset, "--output-dir", str(snapshot), "--run-name", "processed"]
    with (snapshot / "acquisition_metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["raw_file", "acquired_at", "timestamp_source", "source_value"])
        writer.writeheader()
        writer.writerows(sources)
    env = dict(os.environ, MPLBACKEND="Agg", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.IDLE_PRIORITY_CLASS} if os.name == "nt" else {}
    with (snapshot / "processor.log").open("w", encoding="utf-8") as log:
        completed = (runner or subprocess.run)(command, cwd=config.REPO_ROOT, env=env, timeout=timeout,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, **options)
    if completed.returncode:
        raise RuntimeError(f"SciPy NMR processor exit {completed.returncode}; see {snapshot / 'processor.log'}")
    return snapshot / "processed"


class ReviewWorker(review.ReviewWorker):
    """Reuse saved-file watching/dashboard logic with a different numerical child."""
    def __init__(self, run, *, processor=None, timestamp_reader=None):
        super().__init__(run, processor=processor or process_snapshot, timestamp_reader=timestamp_reader)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        review.watch(args.run_dir, parent_pid=args.parent_pid, once=args.once, worker=ReviewWorker(args.run_dir))
        return 0
    except Exception as exc:
        print(f"Optional SciPy analysis worker failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
