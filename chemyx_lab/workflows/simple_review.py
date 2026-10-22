"""Launch optional live NMR plots for 20-25 without importing numerical code.

The child watches raw files and owns only review outputs. The instrument
process never waits for it or reads its results, and no lock/state file is used.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import yaml

from ..config import REPO_ROOT


def begin_review(run_dir, raw_dir, cfg, log, launcher):
    """Optional run() hook; even a broken launcher cannot stop an experiment."""
    if launcher is None:
        return
    try:
        if (cfg.get("review") or {}).get("enabled", True):
            launcher(run_dir, raw_dir, cfg, log)
    except Exception as exc:
        log.warning(f"Review plots ({Path(run_dir).name}) NOT started: {type(exc).__name__}: {exc}; "
                    "the instrument run continues")


def start_review(run_dir, raw_dir, cfg, log):
    """Start one file-watching child before the instrument ports open. Never wait."""
    run_dir, raw_dir = Path(run_dir), Path(raw_dir)
    try:
        review = cfg.get("review") or {}
        if not review.get("enabled", True):
            return
        dataset = (f"{run_dir.parent.name} {run_dir.name}"
                   if run_dir.name in {"stage_1", "stage_2"} else run_dir.name)
        target = {"prominence_snr": 8.0, "rel_height": 0.95,
                  "search_low": 5.6, "search_high": 6.0, "expected_ppm": 5.79,
                  **(review.get("target_peak") or {})}
        settings = {"experiment": {"name": dataset}, "analysis": {
            "dataset_display_name": dataset, "phase_method": "acme",
            "processing_config": review.get("processing_config", "configs/nmr/analysis.yaml"),
            "target_peak": target, "save_spectrum_plots": True,
            "save_area_vs_time": True, "every_n_acquisitions": 1}}
        path = run_dir / "review_config.yaml"
        path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
        command = [sys.executable, "-m", "chemyx_lab.analysis.stage2_demo_analysis", "--run-dir", str(run_dir),
                   "--config", str(path), "--raw-dir", str(raw_dir), "--watch", "--parent-pid", str(os.getpid())]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.IDLE_PRIORITY_CLASS} if os.name == "nt" else {}
        env = dict(os.environ, MPLBACKEND="Agg", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
        with (run_dir / "review.log").open("a", encoding="utf-8") as output:
            subprocess.Popen(command, cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=output,
                             stderr=subprocess.STDOUT, close_fds=True, env=env, **options)
        log.info(f"Review plots ({run_dir.name}): {run_dir / 'analysis'}, updated after each saved spectrum")
        log.info(f"Open observation_####.png (full spectrum), observation_####_target.png (peak), "
                 f"and peak_areas.csv there; processing messages: {run_dir / 'review.log'}")
    except Exception as exc:
        log.warning(f"Review plots ({run_dir.name}) NOT started: {type(exc).__name__}: {exc}; the run continues")
