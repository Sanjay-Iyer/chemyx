"""Reproducible real-data validation, isolated from all experiment runners."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime
import hashlib
import importlib.metadata
import json
import logging
import math
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from uuid import uuid4

import numpy as np

from .nmr import build_phased_spectrum, read_jcamp_fid
from .nmr_validation import acquisition_time, pipeline
from .plot_titles import resolve_dataset_display_name
from .scipy_peak_comparison import (METHODS, METHOD_LABELS, parameter_sweep, detection_context,
    choose_target, evaluate, ascending, integration_trace, integrate_interval)
from .scipy_peak_benchmark_plots import FigureWriter, individual, observation_comparison, dataset_figures
from .target_peak_report import build_target_peak_analysis

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUTS = [ROOT / "results/raw/nmr" / day for day in ("06-08-26", "06-09-26")]
CONFIG = ROOT / "configs/nmr/analysis.yaml"
SOURCES = ["scripts/nmr/process_fid.py", "chemyx_lab/analysis/nmr.py", "chemyx_lab/analysis/phase_methods.py",
           "chemyx_lab/analysis/phase_optimizers.py", "configs/nmr/analysis.yaml",
           "scripts/06_si6_stage2only_run.py", "scripts/07_analyze_stage2only.py",
           "scripts/08_si6_stage2only_scipy_run.py", "scripts/09_analyze_stage2only_scipy.py",
           "chemyx_lab/workflows/si6_stage2only.py", "chemyx_lab/workflows/si6_stage2only_analysis.py",
           "chemyx_lab/analysis/stage2only_reanalysis.py"]
DOCS = {"find_peaks": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.find_peaks.html",
        "peak_widths": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.peak_widths.html",
        "peak_prominences": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.peak_prominences.html"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def write_csv(path, rows, columns=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def fresh_output(requested):
    requested = Path(requested).resolve()
    if not requested.exists():
        requested.mkdir(parents=True)
        return requested
    child = requested / ("run_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8])
    child.mkdir(parents=True, exist_ok=False)
    return child


def snapshot(paths):
    result = {}
    for path in paths:
        path = Path(path)
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else []
        for file in files:
            st = file.stat()
            result[str(file.resolve())] = {"sha256": sha(file), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns}
    return result


def inventory_dataset(directory, dataset):
    """One acquisition per primary/RPC pair or identical timestamp + FID payload."""
    directory = Path(directory).resolve()
    groups = {}
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() == ".dx"):
        key = re.sub(r"\.rpc$", "", path.stem, flags=re.I).casefold()
        groups.setdefault(key, []).append(path)
    inventory, selected, identities = [], [], {}
    for _, paths in sorted(groups.items()):
        chosen = None
        for path in sorted(paths, key=lambda p: (p.name.lower().endswith(".rpc.dx"), str(p))):
            row = {"dataset": dataset, "source_file": str(path), "raw_sha256": sha(path), "status": "", "used_file": "", "note": ""}
            try:
                fid = read_jcamp_fid(path)
                try:
                    stamp, source = acquisition_time(fid.metadata)
                except ValueError:
                    from _common import parse_acquisition_timestamp
                    stamp, source = parse_acquisition_timestamp(fid.metadata, filepath=None)
                    if stamp is None:
                        raise ValueError("No authoritative acquisition metadata; filename/mtime forbidden")
                payload = np.asarray(fid.complex_points, np.complex128)
                fid_hash = hashlib.sha256(payload.tobytes()).hexdigest()
                row.update(acquisition_time=stamp.isoformat(), timestamp_source=source,
                           timestamp_raw=fid.metadata.get("LONG DATE", fid.metadata.get("$DATE", "")), fid_sha256=fid_hash)
                identity = (stamp.isoformat(), fid_hash)
                if chosen is not None or identity in identities:
                    used = chosen or identities[identity]
                    row.update(status="duplicate_skipped", used_file=str(used), note="Same primary/RPC observation or same timestamp and decoded FID")
                else:
                    chosen = path
                    identities[identity] = path
                    row.update(status="selected", used_file=str(path))
                    selected.append({"path": path, "timestamp": stamp, "inventory_row": row, "metadata": fid.metadata})
            except Exception as exc:
                row.update(status="failed_inventory", note=f"{type(exc).__name__}: {exc}")
            inventory.append(row)
    return inventory, sorted(selected, key=lambda r: (r["timestamp"].timestamp(), str(r["path"])))


def historical_presence(dataset, filename):
    """Provisional presence annotations, NOT true areas/bounds or independent validation.

    Existing analysis.yaml comments describe operator-confirmed historical
    presence. Preserve this provenance and use separately from signal metrics.
    """
    token = re.search(r"sequence-(\d{4})", filename)
    token = token[1] if token else ""
    if dataset == "06-08-26" and token in {"1115", "1215", "1315", "1415", "1515", "1615", "1700"}:
        return "present" if token in {"1115", "1215"} else "absent"
    if dataset == "06-09-26" and token in {"0900", "1000", "1015", "1030", "1045", "1100", "1115", "1130"}:
        return "absent" if token == "0900" else "present"
    return "unlabelled"


def process_one(item, dataset, production, args, destination, index):
    path = item["path"]
    spectrum = build_phased_spectrum(path, line_broadening_hz=args.line_broadening_hz,
        zero_fill_points=args.zero_fill_points, phase0_deg=args.phase0, phase1_deg=args.phase1,
        inverse_phase=not args.direct_phase, phase_method="acme", truncation_window=args.truncation_window)
    y, _, picked = production.process_spectrum_for_peaks(spectrum, args)
    x, y = ascending(spectrum.ppm_axis, y)
    observation = f"obs_{index + 1:02d}_{item['timestamp'].strftime('%H%M%S')}"
    noise = float(picked.noise)
    fixed, signed = integrate_interval(x, y, 5.70, 5.90, interpolate=False)
    s = {"dataset": dataset, "observation": observation, "source_file": str(path),
         "timestamp": item["timestamp"], "acquisition_time": item["timestamp"].isoformat(),
         "timestamp_source": item["inventory_row"]["timestamp_source"], "ppm": x, "intensity": y,
         "regional_ppm": picked.ppm_axis, "regional_intensity": picked.quantitative_corrected,
         "noise": noise, "fixed_area": fixed, "fixed_signed_area": signed, "picked": picked,
         "historical_presence": historical_presence(dataset, path.name), "phase_audit": spectrum.phase_audit,
         "observe_frequency_mhz": spectrum.observe_frequency_mhz}
    dest = destination / dataset / "processed_spectra"
    dest.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(dest / f"{observation}.npz", ppm=x, intensity=y,
                        regional_ppm=picked.ppm_axis, regional_intensity=picked.quantitative_corrected)
    write_json(dest / f"{observation}_provenance.json", {k: s[k] for k in ("source_file", "acquisition_time", "timestamp_source", "noise", "historical_presence", "phase_audit")})
    mask = (x >= 5.3) & (x <= 6.3)
    write_csv(dest / f"{observation}.csv", [{"ppm": float(a), "processed_real_intensity": float(b)} for a, b in zip(x[mask], y[mask])])
    return s


def fixed_parity(spectra, target_config):
    objects = [SimpleNamespace(file=Path(s["source_file"]).name, source_path=s["source_file"], spectrum_index=i,
        timestamp=s["timestamp"], timestamp_source=s["timestamp_source"], full_ppm=s["ppm"], full_intensity=s["intensity"], peaks=[]) for i, s in enumerate(spectra)]
    report = build_target_peak_analysis(objects, target_config)
    rows = []
    for s, measurement in zip(spectra, report.measurements):
        positive_error = s["fixed_area"] - measurement["area"]
        signed_error = s["fixed_signed_area"] - measurement["signed_area"]
        if abs(positive_error) > 1e-10 or abs(signed_error) > 1e-10:
            raise AssertionError("Production target report fixed-area mismatch")
        rows.append({"dataset": s["dataset"], "observation": s["observation"], "source_file": s["source_file"],
            "benchmark_area": s["fixed_area"], "production_target_report_area": measurement["area"],
            "positive_error": positive_error, "signed_error": signed_error, "phase_method": "acme", "passed": True})
    return rows


def variable_comparator(s, args, production):
    rows = []
    for p in s["picked"].peaks:
        width_hz = p.width_ppm * s["observe_frequency_mhz"]
        ok, reasons, _ = production._peak_qc(p, width_hz, args)
        rows.append({"peak": p, "qc_pass": ok, "qc_reasons": reasons, "interpolated_ppm": p.interpolated_ppm,
                     "snr": p.snr, "height": p.peak_height})
    selected = production._select_simple_peak_rows(rows, args)
    p = selected[0]["peak"] if selected else None
    nan = float("nan")
    flags = ["Additional comparator: production regional polynomial baseline, no local endpoint line",
             "Actual peaks_simple CSV zero-fills failed detections; benchmark preserves missing status"]
    row = {"dataset": s["dataset"], "observation": s["observation"], "source_file": s["source_file"],
           "acquisition_time": s["acquisition_time"], "timestamp_source": s["timestamp_source"], "elapsed_hours": s["elapsed_hours"],
           "method": "production_variable", "configuration": "production", "parameters": "{}",
           "peak_center_ppm": p.interpolated_ppm if p else nan, "peak_width_ppm": p.width_ppm if p else nan,
           "boundary_low_ppm": p.interpolated_ppm - max(p.width_ppm, args.min_peak_width_ppm) if p else nan,
           "boundary_high_ppm": p.interpolated_ppm + max(p.width_ppm, args.min_peak_width_ppm) if p else nan,
           "prominence": p.prominence if p else nan, "snr": p.snr if p else nan,
           "area_dynamic": p.positive_area if p else nan, "area_signed": p.signed_area if p else nan,
           "area_fixed": s["fixed_area"], "area_difference": p.positive_area - s["fixed_area"] if p else nan,
           "relative_difference_percent": (100 * (p.positive_area - s["fixed_area"]) / s["fixed_area"]) if p and s["fixed_area"] > 1 else nan,
           "detection_status": "success" if p else "missing", "integration_valid": p is not None,
           "qc_flags": flags, "historical_presence": s["historical_presence"], "endpoint_interpolation": False}
    return row


def export_trace_and_audit(output, s, row):
    if not row["integration_valid"]:
        return {"dataset": s["dataset"], "observation": s["observation"], "configuration": row["configuration"],
                "method": row["method"], "status": "missing diagnostic", "passed": True}
    x, y = s["ppm"], s["intensity"]
    low, high = row["boundary_low_ppm"], row["boundary_high_ppm"]
    if row["method"] == "production_variable":
        x, y = s["regional_ppm"], s["regional_intensity"]
        mask = (x >= low) & (x <= high)
        xx, yy, baseline = x[mask], y[mask], np.zeros(np.count_nonzero(mask))
    else:
        xx, yy, baseline = integration_trace(x, y, low, high, interpolate=row["method"] != "fixed")
    positive = np.maximum(yy - baseline, 0)
    order = np.argsort(xx)
    area = float(np.trapezoid(positive[order], xx[order]))
    signed = float(np.trapezoid((yy - baseline)[order], xx[order]))
    error = area - row["area_dynamic"]
    if abs(error) > 1e-9 or abs(signed - row["area_signed"]) > 1e-9:
        raise AssertionError(f"Plotted/CSV integration mismatch: {s['observation']} {row['method']}")
    path = output / s["dataset"] / "individual_spectra" / s["observation"] / row["configuration"] / f"{row['method']}_integration.csv"
    write_csv(path, [{"ppm": float(a), "processed_signal": float(b), "local_baseline": float(c), "positive_integrand": float(d)}
                     for a, b, c, d in zip(xx, yy, baseline, positive)])
    row["integration_coordinates_csv"] = str(path)
    return {"dataset": s["dataset"], "observation": s["observation"], "configuration": row["configuration"], "method": row["method"],
            "status": "integrated", "reported_area": row["area_dynamic"], "plotted_shaded_area": area,
            "absolute_error": abs(error), "signed_error": signed - row["area_signed"], "points": len(xx), "passed": True}


def finite_values(values):
    return np.asarray([v for v in values if isinstance(v, (float, int)) and math.isfinite(v)], float)


def median(values):
    values = finite_values(values)
    return float(np.median(values)) if values.size else float("nan")


def summarize(rows, *, cross=False):
    groups = {}
    for r in rows:
        if r["method"] == "production_variable" or (r["method"] == "fixed" and r["configuration"] != "reference"):
            continue
        key = (" + ".join(sorted(set(v["dataset"] for v in rows))) if cross else r["dataset"], r["configuration"], r["method"])
        groups.setdefault(key, []).append(r)
    result = []
    for (dataset, profile, method), rr in groups.items():
        detected = [r for r in rr if math.isfinite(r.get("peak_center_ppm", float("nan")))]
        present = [r for r in rr if r["historical_presence"] == "present"]
        absent = [r for r in rr if r["historical_presence"] == "absent"]
        missed_present = sum(not math.isfinite(r["peak_center_ppm"]) for r in present)
        detected_absent = sum(math.isfinite(r["peak_center_ppm"]) for r in absent)
        overlap = sum(bool(r.get("nearby_peak_count")) or r.get("envelope_maxima", 0) > 1 for r in detected)
        guards = sum("guard" in "; ".join(r["qc_flags"]) for r in detected)
        outside = sum("half-span" in "; ".join(r["qc_flags"]) for r in detected)
        edge = median([r.get("edge_height_fraction", float("nan")) for r in detected])
        # Relative-to-center boundary changes remove genuine chemical-shift motion.
        center_jumps, bound_jumps, area_jumps = [], [], []
        for day in sorted(set(r["dataset"] for r in rr)):
            series = [r for r in rr if r["dataset"] == day and r["historical_presence"] == "present" and math.isfinite(r["peak_center_ppm"])]
            for a, b in zip(series, series[1:]):
                center_jumps.append(abs(b["peak_center_ppm"] - a["peak_center_ppm"]))
                bound_jumps.append(max(abs((b[k] - b["peak_center_ppm"]) - (a[k] - a["peak_center_ppm"]))
                                       for k in ("boundary_low_ppm", "boundary_high_ppm")))
                if a["area_dynamic"] > 1:
                    area_jumps.append(abs(b["area_dynamic"] - a["area_dynamic"]) / a["area_dynamic"])
        label_count = len(present) + len(absent)
        reliability = 1 - (missed_present + detected_absent) / label_count if label_count else len(detected) / max(1, len(rr))
        denominator = max(1, len(detected))
        edge_quality = max(0., 1 - max(0., edge if math.isfinite(edge) else 1.) / .5)
        bound_median = median(bound_jumps)
        stability = max(0., 1 - (bound_median if math.isfinite(bound_median) else .1) / .1)
        score = 100 * (.45 * reliability + .20 * (1 - overlap / denominator) + .20 * edge_quality
                       + .10 * (1 - min(1., (guards + outside) / denominator)) + .05 * stability)
        result.append({"dataset": dataset, "configuration": profile, "method": method,
            "observations": len(rr), "successful_detections": sum(r["detection_status"] == "success" for r in rr),
            "missing_detections": len(rr) - len(detected), "ambiguous_detections": sum(r["detection_status"] == "ambiguous" for r in rr),
            "unreliable_boundaries": guards, "potential_overlap_cases": overlap, "excessive_boundary_cases": outside,
            "provisional_present_count": len(present), "missed_provisional_present": missed_present,
            "detected_provisional_absent": detected_absent, "provisional_absent_count": len(absent),
            "median_edge_height_fraction": edge, "median_center_change_ppm": median(center_jumps),
            "median_center_relative_boundary_change_ppm": bound_median,
            "median_fractional_area_change": median(area_jumps), "median_area": median([r["area_dynamic"] for r in detected]),
            "median_difference_from_fixed_percent": median([r.get("relative_difference_percent", float("nan")) for r in detected]),
            "ranking_score": score, "ranking_status": "heuristic, provisional presence labels; no area ground truth"})
    return sorted(result, key=lambda r: (-r["ranking_score"], r["method"], r["configuration"]))


def synthetic_checks():
    """Secondary known-area controls, clearly distinct from real-data evidence."""
    from dataclasses import replace
    from .scipy_peak_comparison import DetectionSettings
    rng = np.random.default_rng(12345)
    x = np.linspace(5.3, 6.3, 10001)
    output = []
    for case, center, width, neighbor in [("isolated", 5.79, .015, False), ("shifted", 5.95, .015, False),
        ("broadened", 5.79, .030, False), ("neighbor", 5.79, .015, True), ("low_snr", 5.79, .015, False)]:
        height = 4. if case == "low_snr" else 200.
        y = height / (1 + ((x - center) / width) ** 2) + rng.normal(0, 1, x.size)
        if neighbor:
            y += 150 / (1 + ((x - 5.89) / .02) ** 2)
        settings = replace(DetectionSettings(), maximum_shift_ppm=.2, search_low=5.5, search_high=6.1)
        context = detection_context(x, y, 1., settings)
        peak, status, flags = choose_target(context, settings)
        fixed = integrate_interval(x, y, 5.7, 5.9, interpolate=False)[0]
        for method in METHODS:
            row = evaluate(context, peak, status, flags, method, settings, fixed_area=fixed)
            lo, hi = row["boundary_low_ppm"], row["boundary_high_ppm"]
            expected = height * width * (np.arctan((hi - center) / width) - np.arctan((lo - center) / width)) if np.isfinite(lo + hi) else float("nan")
            row.update(case=case, data_kind="synthetic Lorentzian + fixed-seed noise", analytic_full_target_area=math.pi * height * width,
                analytic_target_area_inside_bounds=expected,
                warning="Full target area excludes neighbor and baseline; endpoint subtraction deliberately removes some tail area")
            output.append(row)
    return output


def write_findings(output, counts, ranking, parity, audit, rows):
    lines = ["# Real-data SciPy NMR boundary benchmark", "", "## Scope and reproducibility", "",
        "Offline validation only. Each selected raw FID was Fourier processed and ACME phased once using production code, then passed through the unchanged production ALS baseline. All boundary methods reuse that same real array. No instrument interfaces were used; scripts 06–09 and shared production code were not edited.", "",
        "See `analysis_configuration.json`, `processing.log`, `dataset_inventory.csv`, `plot_manifest.csv`, `integration_audit.csv`, and `preservation_verification.json`. Every individual PNG has its actual integration coordinates in the adjacent CSV. Failed detections have diagnostic PNGs and missing area, never replacement zero or another peak.", "",
        "## Datasets", "", "| Dataset | Discovered exports | Processed acquisitions | Duplicate skipped | Inventory failed | Processing failed |", "|---|---:|---:|---:|---:|---:|"]
    for c in counts:
        lines.append(f"| {c['dataset']} | {c['discovered']} | {c['processed']} | {c['duplicate_skipped']} | {c['inventory_failed']} | {c['processing_failed']} |")
    lines += ["", "## Methods and area conventions", "",
        "17 one-factor-at-a-time detection profiles × 8 boundary methods, plus the existing variable-width production comparator. Profiles test all three requested search regions; prominence 3/5/8/12 sigma; height 3/5/8 sigma; distances .01/.02/.04 ppm; width limits .006–.22, .012–.18, .020–.12 ppm; maximum movement .06/.12/.20 ppm; expected center 5.79/5.83 ppm; no/.006/.012 ppm identification smoothing. Unchanged settings for each profile are in the configuration JSON.", "",
        "Noise is the unchanged production regional clipped-polynomial residual MAD estimate. A first-difference estimate on zero-filled data was much smaller in a preliminary check, so it is not used to make detection artificially permissive. Smoothing identifies peaks/boundaries only. Final areas use unsmoothed ALS data.", "",
        "Dynamic methods: peak_widths at rel_height .50/.75/.90/.95, prominence bases, guarded local minima, and adaptive return within 2 noise sigma for .008 ppm. Candidate selection uses proximity to expected/prior center under a hard displacement bound, not height. Adjacent maxima within .025 ppm are flagged as a possible multiplet/overlap envelope; minima/adaptive methods consider its outer maxima. Distinct-neighbor valleys and .18 ppm half-span guards prevent silently crossing other maxima. A guard reached before baseline return is an unreliable boundary, not a clean integration.", "",
        "SciPy rel_height is measured downward from the peak by a fraction of prominence, not a fraction of absolute intensity. Prominence-base widths can span distant valleys. These are signal geometry, not validated chemical identities. Sources: " + ", ".join(f"[{k}]({v})" for k, v in DOCS.items()) + ".", "",
        "Fixed reproduces production exactly: sampled points inside 5.70–5.90 ppm, endpoint-foot line, signed and positive trapezoids. Dynamic feet are interpolated in ppm and their intensity comes from the original ALS trace. An additional interpolated-fixed comparison quantifies this small endpoint-convention effect separately. Existing `peaks_simple` uses a regional polynomial correction and center ± measured width; it is labelled separately because this additional baseline differs from the focused report.", "",
        f"Production target-report parity: {len(parity)} spectra; maximum absolute positive-area error = {max([abs(r['positive_error']) for r in parity], default=0):.3g}. All {len(audit)} plot/measurement audits passed (including diagnostics).", "",
        "## Ranking and its limits", "",
        "Ranking is an explicit heuristic: 45% agreement with provisional historical presence labels, 20% absence of overlap flags, 20% low endpoint residual height, 10% no guard/excessive-span flags, 5% stability of boundaries relative to center. Agreement with fixed area and area smoothness receive no direct score. Presence labels are existing operator annotations in configs/nmr/analysis.yaml comments (June 8 first two signals present; June 9 all except 0900 present), not independent truth for boundaries, areas, multiplets, or these ACME results. Unlabelled datasets use coverage only. This ranking is exploratory and is not a calibrated accuracy score.", "",
        "Area and center changes can be genuine chemistry; smoothness alone is not validation. Fixed-window noise area remains a measurement even when no peak is found. Dynamic missing measurements are not equivalent to zero signal. No method has independently known true area in these experiments.", "",
        "| Rank | Configuration | Boundary method | Score | Missing present | Detected absent | Overlap flags | Median Δ vs fixed (%) |", "|---:|---|---|---:|---:|---:|---:|---:|"]
    for index, r in enumerate(ranking[:20], 1):
        lines.append(f"| {index} | {r['configuration']} | {r['method']} | {r['ranking_score']:.2f} | {r['missed_provisional_present']} | {r['detected_provisional_absent']} | {r['potential_overlap_cases']} | {r['median_difference_from_fixed_percent']:.1f} |")
    lines += ["", "## Per-dataset signal findings", ""]
    for c in counts:
        day = c["dataset"]
        rr = [r for r in rows if r["dataset"] == day and r["configuration"] == "reference" and r["method"] == "width_95"]
        centers = finite_values([r["peak_center_ppm"] for r in rr])
        lines.append(f"- **{day}:** reference profile detected {len(centers)}/{len(rr)} candidates. " +
            (f"Centers {centers.min():.5f}–{centers.max():.5f} ppm (range {np.ptp(centers):.5f})." if centers.size else "No qualified target candidates."))
        if centers.size:
            outside = [r["observation"] for r in rr if math.isfinite(r["boundary_low_ppm"]) and (r["boundary_low_ppm"] < 5.7 or r["boundary_high_ppm"] > 5.9)]
            lines.append("  Width-at-.95 extends beyond the fixed window in: " + (", ".join(outside) if outside else "none") + ". This indicates possible tail truncation, not proof of target-only missing area.")
    best = next((r for r in ranking if r["method"] in {"width_75", "width_90", "width_95", "local_minimum", "adaptive_noise", "prominence_bases"}), None)
    lines += ["", "## Unreviewed scoring output", "",
        (f"Highest-ranked dynamic candidate by the heuristic: **{best['method']} / {best['configuration']}**. This requires visual scientific review; the score does not establish the best chemical integration. Review individual PNGs and QC flags before adopting any setting." if best else "No qualified dynamic recommendation."),
        "Half-prominence intervals are a deliberately narrow comparator and should not be interpreted as full resonance area. Wide intervals capture more tails but risk neighboring signals or baseline. Missing/ambiguous outcomes must remain explicit. Do not automatically replace the production fixed method with this first benchmark. The scientifically defensible choice may be a flagged dynamic method plus retained fixed reference rather than a single unconditional algorithm.", "",
        "Known-area synthetic Lorentzian controls are secondary only (`synthetic_secondary_checks.csv`); they do not establish the real signal's shape or chemical identity.", "",
        "## Most useful plots", "",
        "For each dataset, open `parameter_comparisons/*_methods.png`, `time_series/area_vs_time.png`, `time_series/dynamic_vs_fixed.png`, `time_series/center_vs_time.png`, `time_series/boundaries_vs_time.png`, `time_series/peak_width_vs_time.png`, and `time_series/spectrum_overlays.png`. Individual profile/method PNGs and matching integration CSVs are under `individual_spectra/<observation>/<profile>/`. Cross-dataset ranking is `cross_dataset_comparison/parameter_ranking.csv`.", ""]
    (output / "cross_dataset_comparison/findings.md").write_text("\n".join(lines), encoding="utf-8")


def run(inputs, requested_output, config_path=CONFIG, *, dpi=120):
    production = pipeline()
    inputs = [Path(p).resolve() for p in inputs]
    if any(not p.is_dir() for p in inputs):
        raise ValueError("Every input must be an existing raw-data directory")
    output = fresh_output(requested_output)
    log = logging.getLogger("scipy_peak_benchmark")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    log.addHandler(logging.FileHandler(output / "processing.log", encoding="utf-8"))
    log.addHandler(logging.StreamHandler(sys.stdout))
    log.info("Offline benchmark output: %s", output)
    raw_config, _, _ = production._resolved_config_mapping(["--config", str(config_path)])
    args = production._parser(production._config_defaults(raw_config)).parse_args(["--config", str(config_path), "--phase-method", "acme"])
    if args.baseline_method != "asymmetric_least_squares" or args.reference_method != "metadata":
        raise ValueError("This benchmark preserves the requested production ALS and metadata-reference configuration")
    protected = [*inputs, *[ROOT / p for p in SOURCES], *[ROOT / "results/processed/nmr" / p.name for p in inputs]]
    before = snapshot(protected)
    write_json(output / "preservation_before.json", before)
    profiles = parameter_sweep()
    write_json(output / "analysis_configuration.json", {"input_directories": inputs, "production_config": str(config_path),
        "production_config_sha256": sha(config_path), "production_processing": vars(args), "phase_override": "acme",
        "detection_profiles": [asdict(p) for p in profiles], "boundary_methods": METHODS,
        "boundary_specifications": {"fixed": {"low_ppm": 5.7, "high_ppm": 5.9, "interpolate": False},
            **{f"width_{n}": {"rel_height": n / 100, "interpolate": True} for n in (50, 75, 90, 95)},
            "prominence_bases": {"scipy_base_indices": True},
            "local_minimum": {"threshold_sigma": 2, "minimum_valley_prominence_sigma": .5, "neighbor_guards": True},
            "adaptive_noise": {"threshold_sigma": 2, "sustained_baseline_ppm": .008, "neighbor_guards": True}},
        "software": {p: importlib.metadata.version(p) for p in ("numpy", "scipy", "matplotlib", "nmrglue")},
        "documentation": DOCS, "random_seed": 12345, "image_dpi": dpi,
        "ranking_weights": {"provisional_presence": .45, "no_overlap": .20, "low_edge_signal": .20, "no_boundary_guard": .10, "boundary_stability": .05},
        "limitations": "No true real-data boundary/area labels; ranking is exploratory; no production changes"})
    writer = FigureWriter(output, dpi=dpi)
    inventory, all_rows, parity_rows, audits, counts, metadata_rows, sensitivity = [], [], [], [], [], [], []
    datasets = []
    for directory in inputs:
        dataset = resolve_dataset_display_name(input_paths=[directory])
        if dataset in datasets:
            raise ValueError("Input datasets must have distinct display names")
        datasets.append(dataset)
        inv, selected = inventory_dataset(directory, dataset)
        inventory.extend(inv)
        spectra = []
        for index, item in enumerate(selected):
            try:
                log.info("Processing %s %s", dataset, item["path"].name)
                s = process_one(item, dataset, production, args, output, index)
                spectra.append(s)
                item["inventory_row"]["status"] = "processed"
                metadata_rows.append({"dataset": dataset, "observation": s["observation"], "source_file": s["source_file"],
                    "acquisition_time": s["acquisition_time"], "timestamp_source": s["timestamp_source"],
                    "source_value": item["inventory_row"]["timestamp_raw"], "historical_presence": s["historical_presence"]})
            except Exception as exc:
                item["inventory_row"].update(status="failed_processing", note=f"{type(exc).__name__}: {exc}")
                log.exception("Processing failed; continuing to next raw spectrum")
        counts.append({"dataset": dataset, "discovered": len(inv), "processed": len(spectra),
            "duplicate_skipped": sum(r["status"] == "duplicate_skipped" for r in inv),
            "inventory_failed": sum(r["status"] == "failed_inventory" for r in inv),
            "processing_failed": sum(r["status"] == "failed_processing" for r in inv)})
        if not spectra:
            continue
        start = min(s["timestamp"] for s in spectra)
        for s in spectra:
            s["elapsed_hours"] = (s["timestamp"] - start).total_seconds() / 3600
            interpolated_fixed = integrate_interval(s["ppm"], s["intensity"], 5.7, 5.9)[0]
            sensitivity.append({"dataset": dataset, "observation": s["observation"], "production_fixed_area": s["fixed_area"],
                "interpolated_same_fixed_bounds_area": interpolated_fixed, "difference": interpolated_fixed - s["fixed_area"]})
        parity_rows.extend(fixed_parity(spectra, production._target_peak_config(["--config", str(config_path)])))
        day_rows = []
        # Explicit time-ordered state per profile; missing/ambiguous points never
        # update the anchor. No expected drift can jump to an unrelated tallest peak.
        for profile in profiles:
            previous_center = None
            for s in spectra:
                log.info("Plotting %s %s profile=%s", dataset, s["observation"], profile.name)
                context = detection_context(s["ppm"], s["intensity"], s["noise"], profile)
                peak, status, flags = choose_target(context, profile, previous_center)
                if peak is not None and status == "success":
                    previous_center = peak["center"]
                for method in METHODS:
                    try:
                        row = evaluate(context, peak, status, flags, method, profile, fixed_area=s["fixed_area"])
                    except Exception as exc:
                        log.exception("Boundary failure; keeping diagnostic and continuing")
                        row = evaluate(context, None, "failed", [f"{type(exc).__name__}: {exc}"], "width_50", profile, fixed_area=s["fixed_area"])
                        row["method"] = method
                    row.update({k: s[k] for k in ("dataset", "observation", "source_file", "acquisition_time", "timestamp_source", "elapsed_hours", "historical_presence")})
                    audits.append(export_trace_and_audit(output, s, row))
                    row["plot"] = individual(writer, dataset, s, row)
                    day_rows.append(row)
        for s in spectra:
            row = variable_comparator(s, args, production)
            audits.append(export_trace_and_audit(output, s, row))
            row["plot"] = individual(writer, dataset, s, row)
            day_rows.append(row)
            observation_comparison(writer, dataset, s, [r for r in day_rows if r["observation"] == s["observation"]])
        dataset_figures(writer, dataset, spectra, day_rows)
        write_csv(output / dataset / "tables/complete_comparison.csv", day_rows)
        write_csv(output / dataset / "tables/parameter_summary.csv", summarize(day_rows))
        all_rows.extend(day_rows)
        log.info("Completed %s: %s", dataset, counts[-1])
    write_csv(output / "dataset_inventory.csv", inventory)
    write_csv(output / "dataset_summary.csv", counts)
    write_csv(output / "acquisition_metadata.csv", metadata_rows)
    write_csv(output / "fixed_production_parity.csv", parity_rows)
    write_csv(output / "fixed_endpoint_interpolation_sensitivity.csv", sensitivity)
    write_csv(output / "integration_audit.csv", audits)
    write_csv(output / "synthetic_secondary_checks.csv", synthetic_checks())
    ranking = summarize(all_rows, cross=True)
    for i, r in enumerate(ranking, 1):
        r["rank"] = i
    write_csv(output / "cross_dataset_comparison/area_comparison.csv", all_rows)
    write_csv(output / "cross_dataset_comparison/parameter_ranking.csv", ranking)
    # Ranking is a heuristic presentation, not an estimated probability of accuracy.
    from matplotlib import pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 8))
    best = ranking[:20][::-1]
    ax.barh([r["configuration"] + " / " + r["method"] for r in best], [r["ranking_score"] for r in best])
    ax.set(xlim=(0, 100), xlabel="Exploratory score; no true-area calibration")
    fig.tight_layout(rect=[0, 0, 1, .94])
    writer.save(fig, "cross_dataset_comparison/comparison_plots/parameter_ranking.png", " + ".join(datasets), "Candidate Parameter Ranking")
    write_csv(output / "plot_manifest.csv", writer.manifest)
    after = snapshot(protected)
    preservation = {"unchanged": before == after, "protected_file_count": len(before),
        "changed": [k for k in set(before) | set(after) if before.get(k) != after.get(k)],
        "scope": "All input files, named production code/config files, and existing per-day processed analysis folders"}
    write_json(output / "preservation_verification.json", preservation)
    if not preservation["unchanged"]:
        raise AssertionError("Protected files changed during the benchmark")
    write_findings(output, counts, ranking, parity_rows, audits, all_rows)
    expected_individuals = sum(c["processed"] for c in counts) * (len(profiles) * len(METHODS) + 1)
    verification = {"expected_individual_plots": expected_individuals, "individual_plots": sum("individual_spectra" in p["file"] for p in writer.manifest),
        "total_plots": len(writer.manifest), "comparison_rows": len(all_rows), "plot_area_audits": len(audits),
        "production_parity_spectra": len(parity_rows), "preservation": preservation}
    from PIL import Image
    png_errors = []
    for record in writer.manifest:
        try:
            with Image.open(record["file"]) as im:
                if im.info.get("Title") != record["visible_title"] or record["dataset"] not in record["visible_title"]:
                    raise ValueError("Dataset/title mismatch")
                im.verify()
        except Exception as exc:
            png_errors.append({"file": record["file"], "error": str(exc)})
    verification["png_errors"] = png_errors
    verification["all_expected_plots_present"] = verification["individual_plots"] == expected_individuals
    verification["passed"] = not png_errors and verification["all_expected_plots_present"] and bool(all_rows)
    write_json(output / "verification.json", verification)
    (output / "README.md").write_text("# SciPy real-data NMR boundary benchmark\n\n"
        "Start with [the findings](cross_dataset_comparison/findings.md) and the [clickable review index](review_index.html).\n\n"
        "Open each day's `parameter_comparisons/*_methods.png` for six-panel boundary comparisons. "
        "Every individual method PNG has a neighboring CSV with its exact integration samples, baseline and positive integrand. "
        "Both days have separate time-series plots and complete numerical tables.\n\n"
        "All plots visibly identify dataset/acquisition. JCAMP metadata is authoritative. "
        "Original data, production scripts and previous processed folders were preserved.\n\n"
        "`verification.json` records expected plot counts and image/area checks; "
        "`fixed_production_parity.csv` verifies the existing focused-area measurement. "
        "`synthetic_secondary_checks.csv` is explicitly secondary synthetic evidence.\n", encoding="utf-8")
    from .scipy_peak_benchmark_review import finalize_review
    verification = finalize_review(output)
    log.info("FINAL %s", json.dumps({"output": str(output), "datasets": counts, "verification": verification}, default=str))
    return output, verification


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="*", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results/101926_peaks")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--dpi", type=int, default=120)
    args = parser.parse_args(argv)
    if args.dpi < 80:
        parser.error("--dpi must be at least 80 for readable review plots")
    output, verification = run(args.directories or DEFAULT_INPUTS, args.output_dir, args.config, dpi=args.dpi)
    print(f"Completed offline real-data benchmark: {output}", flush=True)
    return 0 if verification["passed"] else 1
