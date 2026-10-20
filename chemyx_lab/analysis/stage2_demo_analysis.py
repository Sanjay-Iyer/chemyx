"""Small, instrument-free live review child for ``10_stage2only``.

Each invocation catches up with saved raw files. Cached ACME/ALS arrays avoid
rephasing earlier acquisitions; the cumulative CSV is also the plot's time
table. Missing peaks remain NaN. Nothing here controls experiment timing.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, fields
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np

from .. import config
from .nmr import build_phased_spectrum
from .nmr_validation import acquisition_time, pipeline
from .plot_titles import format_dataset_plot_title, resolve_dataset_display_name
from .scipy_peak_benchmark_plots import draw_integration
from .scipy_peak_comparison import DetectionSettings, ascending, choose_target, detection_context, evaluate

PRIMARY_METHOD = "scipy_width95_prominence8"
RAW_NAME = re.compile(r"stage_2_(\d+)(\.rpc)?\.dx", re.IGNORECASE)


def authoritative_time(metadata):
    try:
        return acquisition_time(metadata)
    except ValueError:
        # The existing production hierarchy may support additional metadata
        # fields. Explicitly exclude its filename / mtime fallbacks.
        from _common import parse_acquisition_timestamp
        stamp, source = parse_acquisition_timestamp(metadata, filepath=None)
        if stamp is None:
            raise ValueError("No authoritative JCAMP timestamp; filename and mtime are not acquisition times")
        return stamp, source


def atomic_text(path, text):
    path = Path(path)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_csv(path, rows, columns=None):
    import io
    stream = io.StringIO(newline="")
    columns = columns or list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows({key: json.dumps(value) if isinstance(value, (list, dict)) else value
                     for key, value in row.items()} for row in rows)
    atomic_text(path, stream.getvalue())


def detection_settings(analysis):
    target = dict(analysis.get("target_peak") or {})
    rel_height = float(target.pop("rel_height", .95))
    if rel_height != .95 or float(target.get("prominence_snr", 8)) != 8:
        raise ValueError("Live review uses the approved prominence 8 sigma / width 0.95 algorithm")
    allowed = {field.name for field in fields(DetectionSettings)}
    if target.keys() - allowed:
        raise ValueError("Unknown target_peak settings: " + ", ".join(sorted(target.keys() - allowed)))
    settings = DetectionSettings(**{"name": "prominence_8", "prominence_snr": 8., **target})
    for key, value in asdict(settings).items():
        if key != "name" and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value) or value < 0 or key != "smoothing_ppm" and value == 0):
            raise ValueError(f"Invalid target_peak.{key}")
    if not 5.3 <= settings.search_low < settings.expected_ppm < settings.search_high <= 6.3:
        raise ValueError("Target search and expected center must lie within 5.3–6.3 ppm")
    if settings.minimum_width_ppm >= settings.maximum_width_ppm:
        raise ValueError("Target minimum width must be less than maximum width")
    return settings


def processing_setup(analysis):
    if str(analysis.get("phase_method", "acme")).lower() != "acme":
        raise ValueError("Live review preserves production ACME phase correction")
    processing_path = config.resolve_repo_path(analysis.get("processing_config", "configs/nmr/analysis.yaml"))
    production = pipeline()
    raw_config, _, _ = production._resolved_config_mapping(["--config", str(processing_path)])
    args = production._parser(production._config_defaults(raw_config)).parse_args(["--phase-method", "acme"])
    if args.baseline_method != "asymmetric_least_squares" or args.reference_method != "metadata" or args.normalization != "none":
        raise ValueError("Live review preserves ALS, metadata referencing and unnormalized intensities")
    fingerprint = hashlib.sha256(processing_path.read_bytes() + b"\nACME/ALS/live-demo-v1").hexdigest()
    return production, args, fingerprint, raw_config


def process_raw(path, cache_dir, production, args, processing_hash):
    """Read only raw data; a damaged/stale cache is discarded, never required."""
    raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    cache_path = cache_dir / (path.stem + ".npz")
    try:
        with np.load(cache_path, allow_pickle=False) as cached:
            if str(cached["raw_sha256"]) == raw_hash and str(cached["processing_sha256"]) == processing_hash:
                return {"path": path, "ppm": cached["ppm"].copy(), "intensity": cached["intensity"].copy(),
                        "noise": float(cached["noise"]), "timestamp": datetime.fromisoformat(str(cached["acquisition_time"])),
                        "timestamp_source": str(cached["timestamp_source"]), "timestamp_value": str(cached["timestamp_value"]),
                        "raw_sha256": raw_hash, "phase_audit": json.loads(str(cached["phase_audit"])), "cached": True}
    except Exception:
        pass
    print(f"Analysis: Processing {path.name} with ACME / ALS", flush=True)
    spectrum = build_phased_spectrum(path, phase_method="acme", line_broadening_hz=args.line_broadening_hz,
                                    zero_fill_points=args.zero_fill_points, truncation_window=args.truncation_window)
    timestamp, source = authoritative_time(spectrum.metadata)
    real, _, picked = production.process_spectrum_for_peaks(spectrum, args)
    x, y = ascending(spectrum.ppm_axis, real)
    source_value = (spectrum.metadata.get("LONG DATE", "") if source == "LONG DATE header"
                    else spectrum.metadata.get("$DATE", "") if source == "$DATE epoch header"
                    else spectrum.metadata.get(source.split(" run metadata")[0].split(" header")[0], ""))
    audit = spectrum.phase_audit or {}
    temporary = cache_path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, ppm=x, intensity=y, noise=float(picked.noise), raw_sha256=raw_hash,
                            processing_sha256=processing_hash, acquisition_time=timestamp.isoformat(),
                            timestamp_source=source, timestamp_value=str(source_value), phase_audit=json.dumps(audit))
    temporary.replace(cache_path)
    return {"path": path, "ppm": x, "intensity": y, "noise": float(picked.noise), "timestamp": timestamp,
            "timestamp_source": source, "timestamp_value": source_value, "raw_sha256": raw_hash,
            "phase_audit": audit, "cached": False}


def measure(spectra, settings):
    rows, previous = [], None
    start = min((s["timestamp"].timestamp() for s in spectra), default=0)
    for spectrum in spectra:
        base = {"observation": f"observation_{int(RAW_NAME.fullmatch(spectrum['path'].name).group(1)):04d}",
                "source_file": str(spectrum["path"]), "raw_sha256": spectrum["raw_sha256"],
                "acquisition_time": spectrum["timestamp"].isoformat(), "timestamp_source": spectrum["timestamp_source"],
                "timestamp_value": spectrum["timestamp_value"], "elapsed_hours": (spectrum["timestamp"].timestamp() - start) / 3600,
                "phase_method": "acme", "primary_area_method": PRIMARY_METHOD, "noise": spectrum["noise"]}
        try:
            context = detection_context(spectrum["ppm"], spectrum["intensity"], spectrum["noise"], settings)
            peak, status, flags = choose_target(context, settings, previous)
            result = evaluate(context, peak, status, flags, "width_95", settings, fixed_area=float("nan"))
            if peak is not None and status == "success":
                previous = peak["center"]
            if any("half-span" in flag for flag in result["qc_flags"]):
                result.update(area_dynamic=float("nan"), area_signed=float("nan"), detection_status="unreliable_boundary")
            result.update(base, area=result["area_dynamic"], signed_area=result["area_signed"], analysis_status="COMPLETE")
            result["analysis_quality_pass"] = result["detection_status"] == "success" and result["integration_valid"]
        except Exception as exc:
            result = dict(base, method="width_95", parameters=json.dumps(asdict(settings)), area=float("nan"),
                          area_dynamic=float("nan"), area_signed=float("nan"), peak_center_ppm=float("nan"),
                          boundary_low_ppm=float("nan"), boundary_high_ppm=float("nan"), integration_valid=False,
                          analysis_quality_pass=False, detection_status="failed", analysis_status="FAILED",
                          qc_flags=[f"{type(exc).__name__}: {exc}"])
            print(f"Analysis WARNING: {spectrum['path'].name}: target processing failed: {exc}", flush=True)
        spectrum["measurement"] = result
        rows.append(result)
    return rows


def save_png(fig, path, dataset, description, fingerprint):
    title = format_dataset_plot_title(dataset, description)
    fig.suptitle(title, fontsize=11, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, .94])
    temporary = path.with_name("." + path.stem + ".tmp.png")
    try:
        fig.savefig(temporary, dpi=110, metadata={"Title": title, "Dataset": dataset})
        temporary.replace(path)
    finally:
        plt.close(fig)
    return {"figure": str(path), "dataset": dataset, "visible_title": title, "format": "png", "fingerprint": fingerprint}


def render(spectra, rows, output, dataset, analysis, processing_hash):
    """All time plots use rows written verbatim to the shared peak_areas CSV."""
    manifest, failures = [], []
    try:
        with (output / "plot_manifest.csv").open(newline="", encoding="utf-8") as handle:
            existing = {row["figure"]: row for row in csv.DictReader(handle)}
    except Exception:
        existing = {}
    if analysis.get("save_spectrum_plots", True):
        for spectrum in spectra:
            row = spectrum["measurement"]
            fingerprint = hashlib.sha256((json.dumps(row, sort_keys=True) + dataset + processing_hash).encode()).hexdigest()
            for kind in ("spectrum", "target"):
                path = output / (row["observation"] + ("_target" if kind == "target" else "") + ".png")
                previous = existing.get(str(path))
                if previous and previous.get("fingerprint") == fingerprint and path.is_file():
                    manifest.append(previous)
                    continue
                try:
                    fig, ax = plt.subplots(figsize=(10, 5.7))
                    if kind == "target":
                        draw_integration(ax, spectrum, row)
                    else:
                        ax.plot(spectrum["ppm"], spectrum["intensity"], linewidth=.7)
                        ax.set(xlim=(max(spectrum["ppm"]), min(spectrum["ppm"])), xlabel="Chemical shift (ppm)", ylabel="Intensity (a.u.)")
                        ax.grid(alpha=.2)
                    description = row["observation"] + (" Dynamic Target Integration" if kind == "target" else " ACME / ALS Spectrum")
                    manifest.append(save_png(fig, path, dataset, description, fingerprint))
                    print(f"Analysis: Plot saved: {path}", flush=True)
                except Exception as exc:
                    plt.close("all")
                    row["analysis_status"] = "PLOT_FAILED"
                    failures.append({"source_file": row["source_file"], "error": f"Plot failed: {exc}"})
    if analysis.get("save_area_vs_time", True):
        try:
            fig, ax = plt.subplots(figsize=(9, 5.5))
            valid_time = [row for row in rows if math.isfinite(row.get("elapsed_hours", float("nan")))]
            times = [row["elapsed_hours"] for row in valid_time]
            ax.plot(times, [row["area"] for row in valid_time], "o-", label="Dynamic area: 8 sigma / width 0.95")
            flagged = [row for row in valid_time if math.isfinite(row["area"]) and not row["analysis_quality_pass"]]
            if flagged:
                ax.scatter([row["elapsed_hours"] for row in flagged], [row["area"] for row in flagged], marker="x", color="orange", zorder=4, label="Review QC flags")
            missing = [row["elapsed_hours"] for row in valid_time if not math.isfinite(row["area"])]
            if missing:
                ax.plot(missing, [.03] * len(missing), "|", color="gray", transform=ax.get_xaxis_transform(), label="Missing dynamic result (no area value)")
            if times:
                ax.set_xlim(0, max(times) if max(times) else 1 / 60)
            ax.set(xlabel="Hours since first acquisition (JCAMP metadata)", ylabel="Positive integrated area (a.u. ppm)")
            ax.grid(alpha=.2)
            ax.legend(fontsize=8)
            manifest.append(save_png(fig, output / "peak_area_vs_time.png", dataset, "Peak Area vs Time", "cumulative"))
        except Exception as exc:
            plt.close("all")
            failures.append({"error": f"Area trend plot failed: {exc}"})
            for row in rows:
                if row["analysis_status"] == "COMPLETE":
                    row["analysis_status"] = "PLOT_FAILED"
    for failure in failures:
        print(f"Analysis WARNING: {failure['error']}; raw data preserved", flush=True)
    return manifest, failures


def analyze(run_dir, config_path, *, raw_dir=None):
    run_dir = Path(run_dir).expanduser().resolve()
    raw_dir = Path(raw_dir).expanduser().resolve() if raw_dir else run_dir / "nmr_raw"
    output = run_dir / "analysis"
    output.mkdir(parents=True, exist_ok=True)
    cache = output / ".cache"
    cache.mkdir(exist_ok=True)
    cfg = config.read_mapping_config(Path(config_path), "Stage 2 demo configuration")
    analysis = cfg.get("analysis") or {}
    settings = detection_settings(analysis)
    production, args, processing_hash, production_config = processing_setup(analysis)
    names = (analysis.get("dataset_display_name"),
             (production_config.get("statistics") or {}).get("dataset_display_name"),
             (production_config.get("target_peak") or {}).get("dataset_display_name"))
    configured_name = next((name for name in names if name is not None and str(name).strip()), None)
    dataset = resolve_dataset_display_name(configured_name=configured_name
        or f"{cfg.get('experiment', {}).get('name', 'stage2only_demo')} {run_dir.name}", output_path=run_dir)
    dataset = format_dataset_plot_title(dataset, "")
    spectra, failures = [], []
    groups = raw_groups(raw_dir, read_summary(run_dir), run_dir)
    for observation, paths in groups.items():
        variant_errors = []
        for path in paths:
            try:
                spectra.append(process_raw(path, cache, production, args, processing_hash))
                break
            except Exception as exc:
                variant_errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        else:
            failure = {"observation": f"observation_{observation:04d}", "source_file": str(paths[0]),
                       "analysis_status": "FAILED", "area": float("nan"), "elapsed_hours": float("nan"),
                       "error": "; ".join(variant_errors)}
            failures.append(failure)
            print(f"Analysis WARNING: {failure['error']}; raw data preserved", flush=True)
    spectra.sort(key=lambda item: (item["timestamp"].timestamp(), str(item["path"])))
    rows = measure(spectra, settings)
    manifest, plot_failures = render(spectra, rows, output, dataset, analysis, processing_hash)
    write_csv(output / "peak_areas.csv", rows + failures)
    write_csv(output / "plot_manifest.csv", manifest)
    failures.extend(plot_failures)
    succeeded = sum(row["analysis_status"] == "COMPLETE" for row in rows)
    summary = {"status": "COMPLETE" if not failures and succeeded == len(groups) else "PARTIAL",
               "raw_files": len(groups), "successful_analyses": succeeded, "failed_analyses": len(groups) - succeeded,
               "missing_target_peaks": sum(not math.isfinite(row["area"]) for row in rows),
               "cached_spectra": sum(s["cached"] for s in spectra), "dataset": dataset,
               "primary_area_method": PRIMARY_METHOD, "analysis_controls_run": False, "errors": failures,
               "output_dir": str(output)}
    atomic_text(output / "analysis_summary.json", json.dumps(summary, indent=2) + "\n")
    for row in rows:
        print(f"Analysis: {Path(row['source_file']).name}: target {row['detection_status']}, "
              f"center={row['peak_center_ppm']:.5g} ppm, area={row['area']:.6g}", flush=True)
    print(f"Analysis: {succeeded}/{len(groups)} spectra processed; output {output}", flush=True)
    return summary


def parent_alive(pid):
    """Read process liveness without sending a signal on Windows."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87  # invalid PID; access-denied means unknown/alive
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def read_summary(run_dir):
    try:
        result = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def raw_groups(raw_dir, summary, run_dir):
    """One acquisition per numeric observation, even if HTTP saved two variants."""
    groups = {}
    if not raw_dir.is_dir():
        return groups
    for path in raw_dir.iterdir():
        match = RAW_NAME.fullmatch(path.name)
        if match and path.is_file():
            groups.setdefault(int(match.group(1)), []).append(path)
    preferred = {}
    observations = summary.get("observations")
    for row in observations if isinstance(observations, list) else []:
        if not isinstance(row, dict) or row.get("status") != "NMR_SAVED" or not isinstance(row.get("path"), str):
            continue
        path = Path(row["path"])
        candidates = [path.resolve()] if path.is_absolute() else [(run_dir / path).resolve(), path.resolve()]
        for candidate in candidates:
            match = RAW_NAME.fullmatch(candidate.name)
            if match and candidate in groups.get(int(match.group(1)), []):
                preferred[int(match.group(1))] = candidate
    return {number: sorted(paths, key=lambda path: (path != preferred.get(number),
                    not bool(RAW_NAME.fullmatch(path.name).group(2)), path.name))
            for number, paths in sorted(groups.items())}


def watch(run_dir, config_path, parent_pid, *, raw_dir=None, interval_seconds=1.):
    """One optional child follows this run; final review never gates the rig."""
    run_dir = Path(run_dir).expanduser().resolve()
    raw_dir = Path(raw_dir).expanduser().resolve() if raw_dir else run_dir / "nmr_raw"
    cfg = config.read_mapping_config(Path(config_path), "Stage 2 demo configuration")
    every_n = (cfg.get("analysis") or {}).get("every_n_acquisitions", 1)
    if isinstance(every_n, bool) or not isinstance(every_n, int) or every_n < 1:
        raise ValueError("analysis.every_n_acquisitions must be a positive integer")
    previous_fingerprint, last_batch_count, result = None, 0, None
    while True:
        current = read_summary(run_dir)
        finished = str(current.get("status", "")).upper() in {"COMPLETE", "COMPLETE_NO_SPECTRA", "FAILED", "INTERRUPTED"}
        gone = not parent_alive(parent_pid)
        groups = raw_groups(raw_dir, current, run_dir)
        paths = sorted(path for variants in groups.values() for path in variants)
        fingerprint = {number: tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(variants))
                       for number, variants in groups.items()}
        changed = fingerprint != previous_fingerprint
        admitted_changed = previous_fingerprint is not None and any(
            fingerprint.get(number) != prior for number, prior in previous_fingerprint.items())
        if (changed and (len(groups) // every_n > last_batch_count // every_n or admitted_changed)) or finished or gone:
            # A final pass includes spectra saved since the previous batch,
            # including a partial frequency group at the experiment endpoint.
            if changed or result is None or finished or gone:
                try:
                    result = analyze(run_dir, config_path, raw_dir=raw_dir)
                except Exception as exc:
                    print(f"Analysis WARNING: batch failed: {type(exc).__name__}: {exc}; raw data preserved", flush=True)
                    result = {"status": "FAILED", "raw_files": len(groups), "successful_analyses": 0,
                              "failed_analyses": len(groups), "analysis_controls_run": False,
                              "errors": [{"error": f"{type(exc).__name__}: {exc}"}]}
                    output = run_dir / "analysis"
                    output.mkdir(parents=True, exist_ok=True)
                    atomic_text(output / "analysis_summary.json", json.dumps(result, indent=2) + "\n")
                previous_fingerprint, last_batch_count = fingerprint, len(groups)
        if finished or gone:
            # The controller writes terminal status once, then makes no more
            # summary changes. While RUNNING, this child never edits its file.
            current = read_summary(run_dir)
            if current:
                current.update(analyses_successful=result["successful_analyses"],
                               analyses_failed=result["failed_analyses"], analyses_pending=0, analysis_complete=True)
                atomic_text(run_dir / "summary.json", json.dumps(current, indent=2) + "\n")
            print("Analysis: Final review finished; instrument run did not wait for analysis", flush=True)
            return 0 if result["successful_analyses"] or not paths else 1
        time.sleep(interval_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--watch", action="store_true", help="Follow new raw files until the current run finishes")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args(argv)
    if args.watch and not args.parent_pid:
        parser.error("--watch requires --parent-pid")
    try:
        if args.watch:
            return watch(args.run_dir, args.config, args.parent_pid, raw_dir=args.raw_dir)
        result = analyze(args.run_dir, args.config, raw_dir=args.raw_dir)
        return 0 if result["successful_analyses"] else 1
    except Exception as exc:
        print(f"Analysis WARNING: {type(exc).__name__}: {exc}; raw data preserved", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
