"""10's fixed-time physical experiment with the separate 04/05 legacy NMR method."""
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
from uuid import uuid4

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.workflows.stage2_legacy_nmr import LegacyAcquisition


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = load_script("original_stage2_for_legacy", "10_stage2only.py")
DEFAULT_CONFIG = config.REPO_ROOT / "configs/experiments/12_stage2only.yaml"


def fresh_output(cfg, prefix):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8]
    return config.resolve_repo_path(cfg["output"]["root_dir"]) / (prefix + stamp)


def load_config(path=DEFAULT_CONFIG):
    return demo.load_config(path)


def run_experiment(cfg, *, mock, output_dir=None, clock=None, client=None, **kwargs):
    clock = clock or (demo.SimulatedClock() if mock else demo.Clock())
    acquisition = LegacyAcquisition(cfg["nmr"], mock=mock, clock=clock, fixture=demo.FIXTURE, client=client)
    output_dir = Path(output_dir or fresh_output(cfg, "stage2only_legacy_demo_")).resolve()
    cfg = dict(cfg, analysis=dict(cfg["analysis"]))
    if not str(cfg["analysis"].get("dataset_display_name") or "").strip():
        cfg["analysis"]["dataset_display_name"] = output_dir.name
    print("STAGE 2 EXPERIMENT — LEGACY NMR METHOD (12)", flush=True)
    return demo.run_experiment(cfg, mock=mock,
        output_dir=output_dir, clock=clock,
        acquisition=acquisition, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, help="Optional fresh output directory")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_experiment(load_config(args.config), mock=args.mock, output_dir=args.output_dir)
        return 0 if result["status"] == "COMPLETE" else 1
    except Exception as exc:
        print(f"Legacy Stage 2 startup failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
