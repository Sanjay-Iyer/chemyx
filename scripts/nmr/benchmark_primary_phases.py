"""Read-only archived FIDs -> shared FFT -> selectable phase -> production areas.

Outputs are new evidence only. No physical instruments or network clients.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import time
from types import SimpleNamespace
import tracemalloc
import _bootstrap  # noqa
from chemyx_lab import config
from chemyx_lab.analysis.nmr import _build_complex_spectrum
from chemyx_lab.analysis.nmr_validation import production_args, acquisition_time
from chemyx_lab.analysis.phase_comparison import evaluate_shared, summarize
from chemyx_lab.analysis.phase_methods import METHODS, deep_request
from chemyx_lab.analysis.phase_audit import write_json, write_rows
from chemyx_lab.analysis.solvent_presence import solvent_presence, record_sample_presence
from chemyx_lab.analysis.stage_completion import area_completion_evidence
from chemyx_lab.workflows.si6_automated_nmr import load_si6_config, build_stages


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--input", type=Path, action="append")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    recipe = load_si6_config(config.REPO_ROOT / "configs/experiments/si6_real_two_stage.yaml")
    roots = args.input or [config.REPO_ROOT / "results/raw/nmr/06-08-26", config.REPO_ROOT / "results/raw/nmr/06-09-26"]
    sources = [p for root in roots for p in root.rglob("*.dx")]
    if not sources:
        raise ValueError("No archived DX FIDs found; supply an existing --input dataset directory")
    cohort = []
    for source in sources:
        processing = production_args(source)
        fid, ppm, fft, _, _ = _build_complex_spectrum(source, line_broadening_hz=processing.line_broadening_hz,
                zero_fill_points=processing.zero_fill_points, truncation_window=processing.truncation_window)
        stamp, field = acquisition_time(fid.metadata)
        if stamp is None:
            raise ValueError(f"No authoritative acquisition timestamp: {source}")
        cohort.append((stamp, source, fid.metadata, ppm, fft, processing, field))
    cohort.sort(key=lambda item: item[0])
    rows, priors = [], defaultdict(list)
    initialization = {}
    if "deep_phaser" in args.methods:
        start = time.perf_counter()
        try:
            initialization["deep_phaser"] = deep_request({"preflight_only": True})
        except Exception as exc:
            initialization["deep_phaser"] = {"error": str(exc)}
        initialization["deep_phaser"]["measured_model_load_s"] = time.perf_counter() - start
    write_json(args.output / "initialization.json", initialization)
    tracemalloc.start()
    started = time.perf_counter()
    for index, (stamp, source, metadata, ppm, fft, processing, field) in enumerate(cohort):
        present = solvent_presence(source, recipe["solvent_monitor"])
        for method in args.methods:
            start = time.perf_counter()
            try:
                key = (source.parent.name, method)
                row, _ = evaluate_shared(source, ppm, fft, metadata, processing, recipe["analysis"], method,
                    args.output / f"{index:02d}_{source.parent.name}" / method, priors[key], reporting=recipe.get("qc_reporting"))
                record_sample_presence(row, priors[key], recipe["solvent_monitor"], present)
                priors[key].append(row)
                row["retrospective_endpoints"] = {s.name: area_completion_evidence(priors[key], s.completion)
                                                   for s in build_stages(recipe["workflow"])}
                write_json(args.output / f"{index:02d}_{source.parent.name}" / method / "result.json", row)
            except Exception as exc:
                row = {"method": method, "success": False, "peak_area": None, "error": str(exc),
                       "total_s": time.perf_counter() - start}
            row.update(source=str(source), dataset_display_name=source.parent.name,
                       acquired_at=stamp.isoformat(), timestamp_source=field)
            rows.append(row)
            write_json(args.output / "progress.json", rows)
            print(f'{index+1}/{len(cohort)} {method}: {row["success"]} {row["total_s"]:.3f}s', flush=True)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    summary = []
    for method in args.methods:
        group = [r for r in rows if r["method"] == method]
        good = [r for r in group if r["success"]]
        summary.append({"method": method, "successes": len(good), "failures": len(group)-len(good),
                        "median_s": statistics.median(r["total_s"] for r in good) if good else None,
                        "maximum_s": max((r["total_s"] for r in good), default=None),
                        "median_phase_s": statistics.median(r["phase_s"] for r in good) if good else None,
                        "median_downstream_s": statistics.median(r["downstream_s"] for r in good) if good else None})
    datasets = list(dict.fromkeys(row["dataset_display_name"] for row in rows))
    summarize(rows, args.output / "scientific_comparison", " + ".join(datasets))
    report = {"methods": summary, "spectra": len(cohort), "measured_total_s": time.perf_counter()-started,
              "python_tracemalloc_peak_bytes": peak, "memory_limit": "Python allocations only; excludes Node/WASM/native NumPy buffers",
              "disk_bytes": sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file()),
              "initialization": initialization, "hardware_contacted": False,
              "timing_limit": "HOME analysis timings; not a measurement of pump, RPC acquisition, transfer or cleanup time"}
    write_json(args.output / "benchmark.json", report)
    write_rows(args.output / "benchmark.csv", summary)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
