"""11's short physical check with exactly the same legacy NMR function as 12."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.workflows.stage2_legacy_nmr import LegacyAcquisition


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy_demo = load_script("legacy_stage2_for_check", "12_stage2only.py")
check = load_script("original_check_for_legacy", "11_3InstrumentCheck.py")
DEFAULT_CONFIG = config.REPO_ROOT / "configs/experiments/13_3InstrumentCheck.yaml"


def load_check_config(path=DEFAULT_CONFIG):
    cfg = check.load_check_config(path)
    if cfg["nmr"]["scans"] != 1:
        raise ValueError("13 is the one-scan instrument check; nmr_scans must be 1")
    return cfg


def run_check(cfg, *, mock, output_dir=None, clock=None, client=None, analyzer=None, **kwargs):
    clock = clock or (check.demo.SimulatedClock() if mock else check.demo.Clock())
    acquisition = LegacyAcquisition(cfg["nmr"], mock=mock, clock=clock, fixture=check.demo.FIXTURE, client=client)
    def acquire_for_check(nmr_cfg, raw, *, clock, emit):
        result = acquisition(nmr_cfg, raw, clock=clock, emit=emit)
        # 11's MISSING verdict assumes its stricter method already proved
        # completion. A legacy missing-data return may still be premature.
        if result["status"] == "NMR_MISSING" and result.get("unresolved_start"):
            return dict(result, status="NMR_FAILED", legacy_status="NMR_MISSING")
        if result["status"] == "NMR_SAVED" and Path(result["path"]) != raw:
            # 11 validates its canonical file as well as analyzing it. Keep that
            # contract without replacing/discarding the instrument's own export.
            # These are only fresh-run output files; historical data is untouched.
            actual = Path(result["path"])
            backup = raw.with_suffix(".server_export.dx")
            try:
                if backup.exists():
                    raise FileExistsError(f"Existing server export preserved: {backup}")
                raw.rename(backup)
                actual.rename(raw)
            except OSError as exc:
                if backup.exists() and not raw.exists():
                    backup.rename(raw)
                return dict(result, status="NMR_FAILED", reason=f"Raw output publication failed: {exc}")
            result.update(path=str(raw), server_export_path=str(backup))
            emit(f"NMR: Server export preserved at {backup}; fresh raw FID at {raw}")
        return result
    # 11's analyzer otherwise assumes the primary filename; legacy raw saving
    # may preserve a server export and return a separate .rpc.dx file.
    def analyze(run, snapshot, raw, emit):
        actual = Path(acquisition.last_result["path"])
        return (analyzer or check.run_analysis)(run, snapshot, actual, emit)
    print("THREE-INSTRUMENT CHECK — LEGACY NMR METHOD (13)", flush=True)
    return check.run_check(cfg, mock=mock,
        output_dir=output_dir or legacy_demo.fresh_output(cfg, "3instrument_legacy_check_"), clock=clock,
        acquisition=acquire_for_check, analyzer=analyze, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, help="Optional fresh output directory")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_check(load_check_config(args.config), mock=args.mock, output_dir=args.output_dir)
        return 0 if result["status"] == "COMPLETE" else 1
    except Exception as exc:
        print(f"Legacy three-instrument check startup failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
