"""No-network/no-hardware validation inside an extracted offline source release."""
import argparse
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
import _bootstrap  # noqa
from chemyx_lab import config


def prohibited(*args, **kwargs):
    raise AssertionError("Network/hardware contact prohibited by offline release validation")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--deep", action="store_true")
    args = p.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    socket.create_connection = prohibited
    socket.socket.connect = prohibited
    import serial
    serial.Serial = prohibited
    # Also prohibit network calls in the local Node inference child.
    guard = args.output.resolve() / "deny_network.cjs"
    guard.write_text("const deny=()=>{throw Error('Network prohibited during offline validation')};\n"
                     "for(const n of ['http','https']){const m=require(n);m.request=deny;m.get=deny;}\n"
                     "require('net').connect=deny;require('net').createConnection=deny;require('tls').connect=deny;global.fetch=deny;\n")
    os.environ["NODE_OPTIONS"] = (os.environ.get("NODE_OPTIONS", "") + f' --require="{guard.as_posix()}"').strip()
    from chemyx_lab.analysis.phase_methods import METHODS, preflight
    from chemyx_lab.analysis.nmr import build_phased_spectrum
    from chemyx_lab.analysis.nmr_validation import production_args
    from chemyx_lab.release_inventory import source_files
    from chemyx_lab.workflows import three_instrument_si6 as si6
    import numpy as np
    prepared = si6.prepare(config.REPO_ROOT / "configs/experiments/si6_real_two_stage.yaml",
                           config.REPO_ROOT / "configs/machines/si6_real_COM4.yaml",
                           config.REPO_ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=True)
    fixture = config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"
    results = []
    from chemyx_lab.analysis.phase_comparison import evaluate_shared
    from chemyx_lab.analysis.nmr import _build_complex_spectrum
    defaults = production_args(fixture)
    fid, ppm, fft, _, _ = _build_complex_spectrum(fixture, line_broadening_hz=defaults.line_broadening_hz,
             zero_fill_points=defaults.zero_fill_points, truncation_window=defaults.truncation_window)
    for method in METHODS:
        if method == "deep_phaser" and not args.deep:
            results.append({"method": method, "status": "not_requested"})
            continue
        start = time.perf_counter()
        preflight({"processing": {"phase_method": method}})
        row, z = evaluate_shared(fixture, ppm, fft, fid.metadata, defaults, prepared[0]["analysis"], method,
                                 args.output / method, role="primary" if method == "acme" else "comparison")
        if not np.isfinite(z).all() or row.get("phase_method") != method:
            raise AssertionError("Selected method did not supply quantitative evidence")
        results.append({"method": method, "status": "PASS", "measured_s": time.perf_counter()-start,
                        "target_area": row["peak_area"]})
    # Exercise the public post-session path and both stage directories. These
    # are duplicate read-only fixture references, explicitly not a reaction.
    staged = args.output / "OFFLINE-COMPARISON-VALIDATION"
    (staged / "metadata").mkdir(parents=True)
    recipe = prepared[0]
    recipe["nmr"]["processing"]["phase_comparison"] = {"enabled": True,
        "methods": ["stored", "acme", "combined_objective", "ernst_p0"], "include_unphased_reference": True}
    (staged / "metadata/config_snapshot.json").write_text(json.dumps(recipe))
    for step in ("step_1", "step_2"):
        shutil.copytree(args.output / "acme", staged / step / "nmr_processed/fixture")
    from chemyx_lab.analysis.phase_comparison import compare_run
    comparison = compare_run(staged)
    comparison_rows = json.loads((Path(comparison["output"]) / "all_results.json").read_text())
    if comparison["failures"] or comparison["results"] != 10 or not all(r["success"] for r in comparison_rows):
        raise AssertionError(f"Post-session comparison failed: {comparison}")
    for step in ("step_1", "step_2"):
        if not (staged / step / "analysis/phase_method_summary.csv").is_file():
            raise AssertionError("Stage comparison outputs were not kept separate")
    report = {"status": "PASS", "network_prohibited": True, "hardware_prohibited": True,
              "source_files": len(source_files(config.REPO_ROOT)), "methods": results,
              "post_session_comparison": comparison,
              "git_required": False}
    (args.output / "validation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
