"""Numerical 08/09 processor: ACME/ALS -> 8-sigma find_peaks -> width 0.95.

No instrument interfaces. Primary tables/plots/rates/kinetics use dynamic area.
The original 5.70–5.90 area and legacy statistics are labelled comparisons.
Missing/invalid dynamic results never fall back to fixed area or zero.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, fields, replace
from datetime import datetime
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
import traceback

import numpy as np

from .. import config
from .kinetics import fit_all_models
from .nmr import _trapezoid, build_phased_spectrum, track_peak_families
from .nmr_validation import acquisition_time, pipeline
from .plot_titles import format_dataset_plot_title, resolve_dataset_display_name
from .scipy_peak_comparison import (DetectionSettings, ascending, choose_target, detection_context,
    evaluate, integrate_interval, integration_trace)
from .target_peak_report import _normalizations
from .time_series import series_rates, statistical_plateau, white_noise_area_standard_error

DEFAULT_CONFIG = config.REPO_ROOT / "configs/nmr/analysis.yaml"
DEFAULT_PROFILE = config.REPO_ROOT / "configs/nmr/stage2only_scipy.yaml"
PRIMARY_METHOD = "scipy_width95_prominence8"


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def write_csv(path, rows, columns=None):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    columns = columns or list(dict.fromkeys(k for r in rows for k in r)) or ["status"]
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def load_profile(path=DEFAULT_PROFILE):
    raw = config.read_mapping_config(config.resolve_repo_path(path), "Stage 2 SciPy profile")
    if set(raw) != {"schema", "primary_method", "rel_height", "detection"} or raw["schema"] != "chemyx.stage2-scipy.v1":
        raise ValueError("Invalid Stage 2 SciPy profile schema")
    if raw["primary_method"] != "width_95" or float(raw["rel_height"]) != .95:
        raise ValueError("08/09 require peak_widths(rel_height=0.95)")
    if not isinstance(raw["detection"], dict) or set(raw["detection"]) - {f.name for f in fields(DetectionSettings)}:
        raise ValueError("Unknown SciPy detection settings")
    settings = DetectionSettings(**raw["detection"])
    if float(settings.prominence_snr) != 8.:
        raise ValueError("08/09 require minimum find_peaks prominence of 8 sigma")
    for key, value in asdict(settings).items():
        if key == "name":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Detection profile requires a name")
            continue
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError(f"{key} must be finite numeric data")
        if key != "smoothing_ppm" and value <= 0 or key == "smoothing_ppm" and value < 0:
            raise ValueError(f"{key} must be positive (smoothing may be zero)")
    if not 5.3 <= settings.search_low < settings.expected_ppm < settings.search_high <= 6.3:
        raise ValueError("Target search/expected center must fit the supported 5.3–6.3 ppm context")
    if settings.minimum_width_ppm >= settings.maximum_width_ppm:
        raise ValueError("Minimum width must be less than maximum width")
    return settings, raw


def authoritative_time(metadata):
    """Existing metadata hierarchy, preserving LONG DATE timezone when supplied."""
    try:
        return acquisition_time(metadata)
    except ValueError:
        from _common import parse_acquisition_timestamp
        stamp, source = parse_acquisition_timestamp(metadata, filepath=None)
        if stamp is None:
            raise ValueError("No authoritative JCAMP acquisition timestamp; no filename/mtime fallback")
        return stamp, source


def process_inputs(paths, args, production):
    spectra, failures = [], []
    for path in paths:
        try:
            spectrum = build_phased_spectrum(path, phase_method="acme", line_broadening_hz=args.line_broadening_hz,
                zero_fill_points=args.zero_fill_points, truncation_window=args.truncation_window)
            timestamp, source = authoritative_time(spectrum.metadata)
            real, _, picked = production.process_spectrum_for_peaks(spectrum, args)
            x, y = ascending(spectrum.ppm_axis, real)
            fixed, fixed_signed = integrate_interval(x, y, 5.7, 5.9, interpolate=False)
            spectra.append({"path": path, "timestamp": timestamp, "timestamp_source": source,
                "source_value": spectrum.metadata.get("LONG DATE", spectrum.metadata.get("$DATE", "")),
                "acquisition_time": timestamp.isoformat(), "ppm": x, "intensity": y,
                "noise": float(picked.noise), "picked": picked, "spectrum": spectrum,
                "fixed_area": fixed, "fixed_signed_area": fixed_signed,
                "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            print(f"ACME/ALS processed {path.name}", flush=True)
        except Exception as exc:
            failures.append({"source_file": str(path), "status": "PROCESSING_FAILED", "error": f"{type(exc).__name__}: {exc}"})
            print(f"Processing failed for {path.name}; continuing: {exc}", file=sys.stderr, flush=True)
            traceback.print_exc()
    return sorted(spectra, key=lambda s: (s["timestamp"].timestamp(), str(s["path"]))), failures


def dynamic_measurements(spectra, settings, out):
    """The exact validated width_95/prominence_8 algorithm, in acquisition order."""
    rows, previous = [], None
    start = min((s["timestamp"].timestamp() for s in spectra), default=0)
    for i, s in enumerate(spectra):
        observation = f"obs_{i + 1:04d}"
        s.update(observation=observation, elapsed_hours=(s["timestamp"].timestamp() - start) / 3600)
        base = {"file": s["path"].name, "source_file": str(s["path"]), "raw_sha256": s["raw_sha256"],
            "observation": observation, "acquisition_time": s["acquisition_time"], "timestamp_source": s["timestamp_source"],
            "elapsed_hours": s["elapsed_hours"], "phase_method": "acme", "primary_area_method": PRIMARY_METHOD}
        try:
            context = detection_context(s["ppm"], s["intensity"], s["noise"], settings)
            peak, status, flags = choose_target(context, settings, previous)
            row = evaluate(context, peak, status, flags, "width_95", settings, fixed_area=s["fixed_area"])
            if peak is not None and status == "success":
                previous = peak["center"]
            if any("half-span" in flag for flag in row["qc_flags"]):
                row["candidate_area"] = row["area_dynamic"]
                row["candidate_signed_area"] = row["area_signed"]
                row["detection_status"] = "unreliable_boundary"
                row["area_dynamic"] = row["area_signed"] = float("nan")
                row["area_difference"] = row["relative_difference_percent"] = float("nan")
            row.update(base)
            row["area"] = row["area_dynamic"]  # Primary public field is always dynamic.
            row["signed_area"] = row["area_signed"]
            row["analysis_quality_pass"] = row["detection_status"] == "success" and row["integration_valid"]
            row["noise"] = s["noise"]
            row["integration_left_ppm"], row["integration_right_ppm"] = row["boundary_low_ppm"], row["boundary_high_ppm"]
            if row["integration_valid"]:
                xx, yy, line = integration_trace(s["ppm"], s["intensity"], row["boundary_low_ppm"], row["boundary_high_ppm"])
                area = float(_trapezoid(np, np.maximum(yy - line, 0), xx))
                reference_area = row.get("candidate_area", row["area"])
                if not math.isclose(area, reference_area, rel_tol=1e-10, abs_tol=1e-10):
                    raise AssertionError("Primary area differs from plotted integration coordinates")
                coord = out / "integration_coordinates" / f"{observation}.csv"
                write_csv(coord, [{"ppm": float(a), "processed_signal": float(b), "local_baseline": float(c),
                                  "positive_integrand": float(max(b - c, 0))} for a, b, c in zip(xx, yy, line)])
                row["integration_coordinates_csv"] = str(coord)
                se = white_noise_area_standard_error(s["ppm"], row["boundary_low_ppm"], row["boundary_high_ppm"], s["noise"])
                row.update(area_standard_error=se, area_ci_low=row["area"] - 1.96 * se, area_ci_high=row["area"] + 1.96 * se,
                    uncertainty_method="white-noise lower bound; excludes phase, baseline and boundary uncertainty")
        except Exception as exc:
            print(f"Dynamic detection failed for {s['path'].name}; continuing: {exc}", file=sys.stderr, flush=True)
            row = dict(base, method="width_95", configuration=settings.name, parameters=json.dumps(asdict(settings)),
                peak_center_ppm=float("nan"), peak_width_ppm=float("nan"), boundary_low_ppm=float("nan"),
                boundary_high_ppm=float("nan"), area=float("nan"), area_dynamic=float("nan"), area_signed=float("nan"),
                signed_area=float("nan"), area_fixed=s["fixed_area"], integration_valid=False, analysis_quality_pass=False,
                detection_status="failed", qc_flags=[f"{type(exc).__name__}: {exc}"], noise=s["noise"])
        rows.append(row)
        s["measurement"] = row
        np.savez_compressed(out / "processed_spectra" / f"{observation}.npz", ppm=s["ppm"], real=s["intensity"])
        write_json(out / "processed_spectra" / f"{observation}_phase.json", s["spectrum"].phase_audit)
    return rows


def dynamic_statistics(rows, raw_config, out):
    times, areas = [r["elapsed_hours"] for r in rows], [r["area"] for r in rows]
    rates = []
    for i, rate in enumerate(series_rates(times, areas, rolling_window=4)):
        rates.append(dict(asdict(rate), observation=rows[i]["observation"], elapsed_hours=times[i], area=areas[i],
            analysis_quality_pass=rows[i]["analysis_quality_pass"] and (i == 0 or rows[i - 1]["analysis_quality_pass"]),
            primary_area_method=PRIMARY_METHOD))
    write_csv(out / "statistics/target_peak_rates.csv", rates)
    normalized = []
    for mode, values in _normalizations(areas, ("fraction_of_max", "relative_to_first", "zero_to_one")).items():
        normalized.extend({"observation": r["observation"], "elapsed_hours": r["elapsed_hours"], "mode": mode,
            "normalized_area": float(value), "analysis_quality_pass": r["analysis_quality_pass"], "primary_area_method": PRIMARY_METHOD}
            for r, value in zip(rows, values))
    write_csv(out / "statistics/target_peak_normalized.csv", normalized)
    quality_areas = [r["area"] if r["analysis_quality_pass"] else float("nan") for r in rows]
    statistical = raw_config.get("statistics", {})
    plateau = statistical.get("plateau", {})
    result = statistical_plateau(times, quality_areas, minimum_points=int(plateau.get("minimum_points", 4)),
        equivalence_percent_per_hour=float(plateau.get("equivalence_percent_per_hour", 1.)),
        persistence_points=int(plateau.get("persistence_points", 3)), allow_declining=bool(plateau.get("allow_declining_plateau", False)))
    write_json(out / "statistics/dynamic_plateau_review.json", dict(asdict(result), primary_area_method=PRIMARY_METHOD,
        analysis_controls_run=False, quality_filtered=True, purpose="Retrospective review only; no experiment decisions"))
    fits, best = [], None
    kinetics = statistical.get("kinetics", {})
    if kinetics.get("enabled", True):
        fitted, best = fit_all_models(times, quality_areas,
            models=kinetics.get("models", ["zero_order", "first_order_decay", "first_order_formation", "first_order_formation_lag"]),
            analysis_target="primary_dynamic_width95")
        fits = [dict(asdict(f), is_best_by_aicc=f.kinetic_model == best, primary_area_method=PRIMARY_METHOD,
                     quality_filtered=True) for f in fitted]
    write_csv(out / "statistics/dynamic_kinetic_fits.csv", fits)
    return rates, normalized, {"best_kinetic_model": best, "plateau_review": asdict(result)}


def secondary_statistics(production, spectra, raw_config, args, dataset, out):
    """Preserve the general full-dataset statistics as explicitly secondary output."""
    settings = replace(production.load_statistics_config(raw_config.get("statistics")), dataset_display_name=dataset)
    if not settings.enabled:
        return {"enabled": False}
    raw = []
    for s in spectra:
        spec = s["spectrum"]
        raw.append({"file": s["path"].name, "source_path": str(s["path"]), "timestamp": s["timestamp"],
            "timestamp_source": s["timestamp_source"], "observe_frequency_mhz": spec.observe_frequency_mhz,
            "reference_shift_ppm": 0., "reference_qc_pass": True, "phase0_deg": spec.phase0_deg,
            "phase1_deg": spec.phase1_deg, "picked": s["picked"], "full_ppm": s["ppm"], "full_intensity": s["intensity"]})
    assignments, _ = track_peak_families([s["picked"].peaks for s in spectra])
    return production._run_statistics(raw, assignments, settings, out / "secondary_statistics", target_peak_config=None,
                                     processing_provenance={"phase_method": "acme", "role": "secondary comparison"})


def process(paths, output, *, config_path=DEFAULT_CONFIG, profile_path=DEFAULT_PROFILE, dataset_display_name=None):
    """Process explicit files into a new directory; no fallback data or overwrite."""
    production = pipeline()
    settings, profile = load_profile(profile_path)
    paths = [Path(p).expanduser().resolve() for p in paths]
    if not paths or any(not p.is_file() or p.suffix.lower() != ".dx" for p in paths):
        raise ValueError("Explicit existing raw .dx files are required; no configured input fallback")
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate explicit inputs are not accepted; select acquisitions once")
    out = Path(output).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "processed_spectra").mkdir()
    raw_config, _, _ = production._resolved_config_mapping(["--config", str(config_path)])
    args = production._parser(production._config_defaults(raw_config)).parse_args(["--phase-method", "acme"])
    if args.baseline_method != "asymmetric_least_squares" or args.reference_method != "metadata" or args.normalization != "none":
        raise ValueError("08/09 preserve ACME, ALS, metadata referencing and unnormalized quantitative intensities")
    dataset = resolve_dataset_display_name(configured_name=dataset_display_name or raw_config.get("statistics", {}).get("dataset_display_name")
        or raw_config.get("target_peak", {}).get("dataset_display_name"), input_paths=paths, output_path=out)
    dataset = format_dataset_plot_title(dataset, "")
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    write_json(out / "analysis_configuration.json", {"primary_area_method": PRIMARY_METHOD, "primary_field": "target_peak_measurements.csv: area",
        "phase_method": "acme", "baseline_method": "asymmetric_least_squares", "profile": profile, "processing": vars(args),
        "dataset_display_name": dataset, "profile_path": str(profile_path), "profile_sha256": hashlib.sha256(Path(profile_path).read_bytes()).hexdigest(),
        "config_path": str(config_path), "config_sha256": hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
        "fixed_area_role": "5.70–5.90 ppm comparison only; never replaces missing dynamic area", "analysis_controls_run": False,
        "packages": {p: importlib.metadata.version(p) for p in ("numpy", "scipy", "nmrglue", "matplotlib")}})
    spectra, failures = process_inputs(paths, args, production)
    rows = dynamic_measurements(spectra, settings, out)
    write_csv(out / "target_peak_measurements.csv", rows)
    write_csv(out / "statistics/target_peak_measurements.csv", rows)
    write_csv(out / "processing_errors.csv", failures)
    write_csv(out / "fixed_window_comparison.csv", [{"observation": s["observation"], "source_file": str(s["path"]),
        "acquisition_time": s["acquisition_time"], "elapsed_hours": s["elapsed_hours"], "dynamic_area": s["measurement"]["area"],
        "fixed_area": s["fixed_area"], "fixed_signed_area": s["fixed_signed_area"], "role": "comparison only"} for s in spectra])
    write_csv(out / "acquisition_metadata.csv", [{"observation": s["observation"], "source_file": str(s["path"]),
        "acquisition_time": s["acquisition_time"], "timestamp_source": s["timestamp_source"], "source_value": s["source_value"]} for s in spectra])
    errors, rates, normalized, stat_info, secondary = [], [], [], {}, {}
    if spectra:
        try:
            rates, normalized, stat_info = dynamic_statistics(rows, raw_config, out)
        except Exception as exc:
            errors.append(f"Dynamic statistics: {type(exc).__name__}: {exc}")
            traceback.print_exc()
        try:
            from .stage2_scipy_plots import render
            render(spectra, rows, rates, normalized, dataset, raw_config, out)
        except Exception as exc:
            errors.append(f"Plots: {type(exc).__name__}: {exc}")
            traceback.print_exc()
        try:
            secondary = secondary_statistics(production, spectra, raw_config, args, dataset, out)
        except Exception as exc:
            errors.append(f"Secondary comparison statistics: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in before.items())
    if not unchanged:
        raise AssertionError("Raw input hash changed")
    detection_failures = sum(r["detection_status"] == "failed" for r in rows)
    summary = {"status": "NO_DATA" if not spectra else "PARTIAL" if failures or errors or detection_failures else "COMPLETE",
        "primary_area_method": PRIMARY_METHOD, "minimum_prominence_sigma": 8., "rel_height": .95,
        "dataset_display_name": dataset, "files_found": len(paths), "files_processed": len(spectra), "files_failed": len(failures),
        "dynamic_detections": sum(math.isfinite(r["area"]) for r in rows),
        "missing_detections": sum(not math.isfinite(r["area"]) for r in rows),
        "dynamic_processing_failures": detection_failures,
        "quality_passing_measurements": sum(r["analysis_quality_pass"] for r in rows), "errors": errors,
        "primary_statistics": stat_info, "secondary_comparison_statistics": secondary,
        "raw_inputs_unchanged": unchanged, "raw_sha256": before, "analysis_controls_run": False, "instruments_contacted": False}
    write_json(out / "processing_summary.json", summary)
    (out / "README.md").write_text("# Stage 2 SciPy primary analysis\n\n"
        "Primary area: `target_peak_measurements.csv` → `area` (8σ find_peaks; peak_widths rel_height=0.95).\n\n"
        "Open `plots/area_vs_time.png` and `plots/observations/*region_dynamic*.png`. Missing remains missing; "
        "ambiguous candidate areas have explicit quality flags. A `success` status is not proof of chemical identity.\n\n"
        "`fixed_window_comparison.csv` and `secondary_statistics/` are comparisons, not the primary metric. "
        "Statistics labelled quality-filtered exclude ambiguous/missing/invalid observations. "
        "Timing comes only from acquisition metadata; analysis never controls an instrument run.\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("status", "primary_area_method", "files_processed", "files_failed", "dynamic_detections", "missing_detections", "errors")}), flush=True)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Explicit raw FID files, supplied by 08/09")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--phase-method", choices=["acme"], default="acme")
    parser.add_argument("--dataset-display-name")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run-name", default="processed")
    args = parser.parse_args(argv)
    try:
        if Path(args.run_name).name != args.run_name or args.run_name in {".", ".."}:
            raise ValueError("run-name must be a simple new directory name")
        summary = process(args.paths, args.output_dir / args.run_name, config_path=args.config,
                          profile_path=args.profile, dataset_display_name=args.dataset_display_name)
        return 0 if summary["files_processed"] else 1
    except Exception as exc:
        print(f"SciPy Stage 2 analysis failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
