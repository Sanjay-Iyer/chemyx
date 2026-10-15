"""Consolidate preserved benchmarks and a final ACME convergence rerun.

No phase result is synthesized: all areas/parameters are retained measurements.
The optional ACME rerun replaces only that method, keyed by acquisition source.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import _bootstrap  # noqa
from chemyx_lab import config
from chemyx_lab.analysis.phase_audit import write_json, write_rows
from chemyx_lab.analysis.phase_comparison import summarize, plot_acquisition
from chemyx_lab.analysis.stage_completion import area_completion_evidence
from chemyx_lab.analysis.si6_final_qc import diagnostic_row
from chemyx_lab.workflows.si6_automated_nmr import load_si6_config, build_stages


def replay_retained(rows, roots, output):
    """Verify retained arrays against one freshly decoded common FFT per FID.

    This replays recorded phases, rather than estimating new phases or replacing
    any benchmark measurement. Historical inputs and benchmark outputs are read-only.
    """
    import hashlib
    import numpy as np
    import nmrglue as ng
    from chemyx_lab.analysis.nmr import _build_complex_spectrum
    audits = {}
    for root in roots:
        for path in root.glob("*/*/phase_audit/shared/processing_metadata.json"):
            audit = json.loads(path.read_text())
            audits[(audit["source_path"], audit["phase"]["method"])] = (path, audit)
    acquisitions = defaultdict(list)
    for row in rows:
        if row["success"]:
            acquisitions[row["source"]].append(row)
    verified = []
    for i, (source, group) in enumerate(acquisitions.items()):
        first_path, first = audits[(source, group[0]["method"])]
        pars = first["parameters"]
        _, ppm, fft, _, _ = _build_complex_spectrum(Path(source),
            line_broadening_hz=pars["line_broadening_hz"], zero_fill_points=pars["zero_fill_points"],
            truncation_window=pars["truncation_window"])
        folder = output / f"{i:02d}_{Path(source).parent.name}"
        folder.mkdir(parents=True)
        np.savez_compressed(folder / "shared_fft.npz", ppm=ppm, unphased=fft)
        spectra = {"unphased_reference": fft}
        for row in group:
            path, audit = audits[(source, row["method"])]
            phase = audit["phase"]
            with np.load(path.with_name("spectral_evidence.npz"), allow_pickle=False) as z:
                np.testing.assert_array_equal(z["ppm"], ppm)
                retained = z["phased"].copy()
            expected = ng.proc_base.ps(fft, p0=phase["p0_deg"], p1=phase["p1_deg"], inv=phase["inverse_phase"])
            np.testing.assert_allclose(retained, expected, rtol=1e-12, atol=1e-9)
            assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == audit["raw_sha256"] == row["raw_sha256"]
            spectra[row["method"]] = retained
            verified.append({"source": source, "method": row["method"], "retained_arrays": str(path.with_name("spectral_evidence.npz")),
                "maximum_absolute_replay_error": float(np.max(np.abs(retained-expected))), "rtol": 1e-12, "atol": 1e-9})
        plot_acquisition(ppm, spectra, folder, group[0]["dataset_display_name"])
    write_rows(output / "phase_replay_verification.csv", verified)
    return {"verified_spectra": len(acquisitions), "verified_method_arrays": len(verified),
            "maximum_absolute_replay_error": max(r["maximum_absolute_replay_error"] for r in verified)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--benchmark", type=Path, required=True)
    p.add_argument("--acme-rerun", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    rows = json.loads((args.benchmark / "progress.json").read_text())
    if args.acme_rerun:
        rerun = json.loads((args.acme_rerun / "progress.json").read_text())
        by_source = {r["source"]: r for r in rerun if r["method"] == "acme"}
        if len(by_source) != len({r["source"] for r in rows}):
            raise ValueError("ACME rerun does not cover the same entire cohort")
        rows = [by_source[r["source"]] if r["method"] == "acme" else r for r in rows]
    recipe = load_si6_config(config.REPO_ROOT / "configs/experiments/si6_real_two_stage.yaml")
    groups, methods, acquisitions = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        if row["success"]:
            row.update(diagnostic_row(row, recipe["qc_reporting"], config.REPO_ROOT))
        groups[(row["dataset_display_name"], row["method"])].append(row)
        methods[row["method"]].append(row)
        acquisitions[row["source"]].append(row)
    timing = []
    for method, group in methods.items():
        good = [r for r in group if r["success"]]
        timing.append({"method": method, "successes": len(good), "failures": len(group)-len(good),
                       "median_s": statistics.median(r["total_s"] for r in good) if good else None,
                       "maximum_s": max((r["total_s"] for r in good), default=None),
                       "median_phase_s": statistics.median(r["phase_s"] for r in good) if good else None,
                       "median_downstream_s": statistics.median(r["downstream_s"] for r in good) if good else None,
                       "median_negative_fraction": statistics.median(r["quality"]["negative_area_fraction"] for r in good) if good else None,
                       "median_even_imaginary_fraction": statistics.median(r["quality"]["even_imaginary_fraction"] for r in good) if good else None})
    endpoints = []
    for (dataset, method), group in groups.items():
        good = []
        for row in sorted(group, key=lambda r: r["acquired_at"]):
            if not row["success"]:
                break  # A selected primary failure ends the real workflow.
            good.append(row)
        for stage in build_stages(recipe["workflow"]):
            found = next((i for i in range(1, len(good)+1) if area_completion_evidence(good[:i], stage.completion)["complete"]), None)
            endpoints.append({"dataset": dataset, "method": method, "retrospective_rule": stage.name,
                              "first_satisfied_observation": found,
                              "timestamp": good[found-1]["acquired_at"] if found else None,
                              "scope": "retrospective rule sensitivity only; not reconstructed physical stages or dose authorization"})
    spreads = []
    for source, group in acquisitions.items():
        good = [r for r in group if r["success"] and r.get("peak_area") is not None]
        median = statistics.median(r["peak_area"] for r in good) if good else None
        for row in good:
            row["difference_from_descriptive_median"] = row["peak_area"]-median
            row["relative_difference_from_descriptive_median"] = (row["peak_area"]-median)/abs(median) if median else None
        positions = [r["peak_ppm"] for r in good if r.get("peak_ppm") is not None]
        spreads.append({"source": source, "successful_methods": len(good),
                        "all_detection_agree": len({r["signal_classification"] == "detected" for r in good}) == 1 if len(good) == len(methods) else None,
                        "area_median": median, "area_minimum": min((r["peak_area"] for r in good), default=None),
                        "area_maximum": max((r["peak_area"] for r in good), default=None),
                        "peak_position_spread_ppm": max(positions)-min(positions) if positions else None})
    datasets = list(dict.fromkeys(r["dataset_display_name"] for r in rows))
    replay = replay_retained(rows, [args.benchmark] + ([args.acme_rerun] if args.acme_rerun else []), args.output / "spectral_overlays")
    summarize(rows, args.output / "scientific_comparison", " + ".join(datasets))
    write_rows(args.output / "benchmark.csv", timing)
    write_rows(args.output / "endpoint_sensitivity.csv", endpoints)
    write_rows(args.output / "acquisition_spread.csv", spreads)
    report = {"spectra": len(acquisitions), "methods": timing, "detection_agreement_count": sum(r["all_detection_agree"] is True for r in spreads),
              "phase_replay": replay,
              "all_results": str(args.output / "scientific_comparison/all_results.json"),
              "preserved_original_benchmark": str(args.benchmark), "acme_rerun": str(args.acme_rerun),
              "runtime_note": "Measured HOME wall time under concurrent regression/benchmark load; timing is not instrument commissioning",
              "endpoint_note": "The two current stage rules are applied retrospectively to each acquisition cohort; no physical stage identity or dose decision is inferred"}
    write_json(args.output / "report.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
