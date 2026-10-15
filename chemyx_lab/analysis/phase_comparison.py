"""Post-session phase research using shared production FFTs and unchanged areas.

No instrument imports, control callbacks or dose interfaces. Method failures are
rows with unavailable measurements, never quantitative zeros.
"""
from __future__ import annotations
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time

import numpy as np
from .nmr import SpectrumData
from .phase_methods import apply_phase, settings
from .phase_quality import prepare_context, score_spectrum
from .phase_audit import write_json, write_rows, save_figure
from .plot_titles import resolve_dataset_display_name


def evaluate_shared(source, ppm, fft, metadata, args, analysis, method, output, prior=(), *, role="comparison", reporting=None):
    """Identical production baseline, candidate records, tracking and area math."""
    from .nmr_validation import pipeline, acquisition_time
    from .peak_tracking import candidate_records, configure_candidates
    from .stage_measurement import tracked_peak_area_observation
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refuse to overwrite method evidence: {output}")
    started = time.perf_counter()
    phased, phase = apply_phase(ppm, fft, metadata, method)
    phase["role"] = role
    phase_s = time.perf_counter() - started
    args = SimpleNamespace(**vars(args))
    args.phase_method = method
    args.direct_phase = not phase["inverse_phase"]
    if "peak_finding" in analysis:
        configure_candidates(args, analysis["peak_finding"])
    else:
        raise ValueError("Phase research requires the current peak_finding recipe")
    spec = SpectrumData(Path(source), metadata, ppm, np.abs(phased), processed_points=len(fft),
                        real=phased.real, imaginary=phased.imag, phase0_deg=phase["p0_deg"],
                        phase1_deg=phase["p1_deg"], observe_frequency_mhz=float(metadata.get("$SF", 60.)),
                        phase_method=method, phase_audit=phase)
    downstream_start = time.perf_counter()
    quantitative, _, picked = pipeline().process_spectrum_for_peaks(spec, args)
    candidates = candidate_records(picked)
    try:
        stamp, field = acquisition_time(metadata)
    except ValueError:
        stamp, field = None, None
    if stamp is not None and not isinstance(stamp, str):
        stamp = stamp.isoformat()
    raw_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    output.mkdir(parents=True)
    audit_dir = output / "phase_audit/shared"
    audit_dir.mkdir(parents=True)
    shared = {"unphased": fft} if role == "primary" else {}
    np.savez_compressed(audit_dir / "spectral_evidence.npz", **shared, ppm=ppm, phased=phased,
                        quantitative_real=quantitative, region_ppm=picked.ppm_axis,
                        regional_quantitative=picked.quantitative_corrected)
    write_json(audit_dir / "processing_metadata.json", {"raw_sha256": raw_hash,
               "peak_candidates": candidates, "phase": phase, "parameters": vars(args),
               "source_path": str(source), "jcamp_metadata": metadata})
    row = {"target_ppm": args.simple_target_ppm, "acquired_at": stamp, "timestamp_source": field}
    if method != "unphased":
        tracked_peak_area_observation(Path(source), output, row, analysis, list(prior))
        from .si6_final_qc import diagnostic_row
        row = diagnostic_row(row, reporting or {}, Path.cwd())
    else:
        row.update(peak_area=None, signal_classification="reference_only", peak_ppm=None)
    downstream_s = time.perf_counter() - downstream_start
    context = prepare_context(ppm, fft)
    row.update(method=method, role=role, acquisition_id=Path(source).stem, success=True,
               raw_sha256=raw_hash, fft_sha256=context.metadata["fft_sha256"], phase_s=phase_s,
               downstream_s=downstream_s, total_s=time.perf_counter() - started,
               phase=phase, quality=score_spectrum(phased, context),
               dataset_display_name=resolve_dataset_display_name(input_paths=source, metadata=metadata))
    write_json(output / "result.json", row)
    row["disk_bytes"] = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
    return row, phased


def acquisition_comparison(source, primary_evidence, analysis, methods, output, *, dataset=None, priors=None, solvent_settings=None, reporting=None):
    """One FFT reused across all methods; only successful methods get folders."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Comparison already exists: {output}")
    output.mkdir(parents=True)
    primary_evidence = Path(primary_evidence)
    audit = json.loads(primary_evidence.with_name(primary_evidence.name.replace("spectral_evidence.npz", "processing_metadata.json")).read_text())
    with np.load(primary_evidence, allow_pickle=False) as arrays:
        ppm, fft = arrays["ppm"].copy(), arrays["unphased"].copy()
    if hashlib.sha256(Path(source).read_bytes()).hexdigest() != audit["raw_sha256"]:
        raise ValueError("Primary evidence raw hash does not match acquisition")
    rows, spectra = [], {}
    presence = None
    if solvent_settings is not None:
        from .solvent_presence import solvent_presence
        presence = solvent_presence(Path(source), solvent_settings)
    for method in methods:
        try:
            row, spectra[method] = evaluate_shared(source, ppm, fft, audit["jcamp_metadata"],
                    SimpleNamespace(**audit["parameters"]), analysis, method, output / method,
                    (priors or {}).get(method, []))
            if reporting is not None:
                from .si6_final_qc import diagnostic_row
                row = diagnostic_row(row, reporting, Path.cwd())
            if dataset:
                row["dataset_display_name"] = dataset
            if presence is not None and method != "unphased":
                from .solvent_presence import record_sample_presence
                record_sample_presence(row, (priors or {}).get(method, []), solvent_settings, presence)
            write_json(output / method / "result.json", row)
            if priors is not None and method != "unphased":
                priors.setdefault(method, []).append(row)
        except Exception as exc:
            failed = output / method
            if failed.exists():
                (output / "_failed_evidence").mkdir(exist_ok=True)
                failed.rename(output / "_failed_evidence" / method)
            spectra.pop(method, None)
            row = {"method": method, "success": False, "peak_area": None,
                   "error": f"{type(exc).__name__}: {exc}", "raw_sha256": audit["raw_sha256"]}
            from .nmr_validation import acquisition_time
            try:
                stamp, field = acquisition_time(audit["jcamp_metadata"])
                row.update(acquired_at=stamp.isoformat(), timestamp_source=field)
            except ValueError:
                pass
        rows.append(row)
    write_json(output / "comparison.json", rows)
    write_rows(output / "comparison.csv", [{k: v for k, v in r.items() if not isinstance(v, (dict, list))} for r in rows])
    dataset = dataset or resolve_dataset_display_name(input_paths=source, metadata=audit["jcamp_metadata"])
    plot_acquisition(ppm, spectra, output, dataset)
    return rows


def plot_acquisition(ppm, spectra, output, dataset):
    """Show retained complex spectra with a separately scaled target region."""
    import matplotlib.pyplot as plt
    output = Path(output)
    manifest = []
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), layout="constrained")
    for method, z in spectra.items():
        axes[0].plot(ppm, z.real, label=method, lw=.7)
        axes[1].plot(ppm, z.imag, label=method, lw=.7)
        target = (ppm >= 5.6) & (ppm <= 6.)
        axes[2].plot(ppm[target], z.real[target], label=method, lw=.7)
    for ax, title in zip(axes, ("Real spectrum", "Imaginary spectrum", "Target region")):
        ax.set_title(title)
        ax.set(xlabel="Chemical shift (ppm)", ylabel="Intensity (a.u.)")
        ax.invert_xaxis()
        if spectra:
            ax.legend(fontsize=7)
    axes[2].set_xlim(6., 5.6)
    save_figure(fig, output / "comparison.png", dataset, "Phase Comparison — shared complex FFT", manifest)
    write_rows(output / "plot_manifest.csv", manifest)


def summarize(rows, output, dataset):
    """One shared CSV supplies all time plots; metadata timestamps only."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    scalar = [{k: v for k, v in r.items() if not isinstance(v, (dict, list))} for r in rows]
    write_rows(output / "comparison_summary.csv", scalar)
    write_rows(output / "algorithm_runtime.csv", [{k: r.get(k) for k in ("method", "phase_s", "downstream_s", "total_s", "disk_bytes", "success")} for r in rows])
    write_json(output / "all_results.json", rows)
    import matplotlib.pyplot as plt
    manifest = []
    valid = [r for r in rows if r.get("success") and r.get("peak_area") is not None]
    timed = [r for r in rows if r.get("acquired_at") and r.get("timestamp_source") and "filename" not in r["timestamp_source"].lower() and "mtime" not in r["timestamp_source"].lower()]
    methods = sorted({r["method"] for r in valid})
    # Differences use descriptive within-acquisition medians, never ground truth.
    for name, description in (("target_area_vs_time", "Target Area vs Metadata Acquisition Time"),
                               ("area_difference_vs_time", "Target Area Difference from Cross-method Median")):
        if not any(r.get("success") and r.get("peak_area") is not None for r in timed):
            write_json(output / f"{name}_unavailable.json", {"reason": "No authoritative acquisition timestamps; no time plot fabricated"})
            continue
        fig, ax = plt.subplots(figsize=(12, 5), layout="constrained")
        for method in methods:
            group = sorted([r for r in timed if r["method"] == method], key=lambda r: r["acquired_at"])
            values = [r.get("peak_area") if r.get("success") and r.get("peak_area") is not None else float("nan") for r in group]
            if name.startswith("area_difference"):
                values = [r["peak_area"] - np.median([q["peak_area"] for q in valid if q["raw_sha256"] == r["raw_sha256"]])
                          if r.get("success") and r.get("peak_area") is not None else float("nan") for r in group]
            ax.plot([datetime.fromisoformat(r["acquired_at"]) for r in group], values, marker="o", label=method)
        ax.set(xlabel="JCAMP acquisition time (source fields in comparison_summary.csv)", ylabel="Area (a.u. ppm)")
        if methods:
            ax.legend()
        save_figure(fig, output / f"{name}.png", dataset, description, manifest)
    for name, key, description in (("phase_quality_comparison", "quality", "Negative Absorption Fraction"),
                                    ("algorithm_runtime", "total_s", "Total Method Processing Time")):
        fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
        values = [[(r.get("quality", {}).get("negative_area_fraction") if key == "quality" else r.get(key)) for r in valid if r["method"] == m] for m in methods]
        if methods:
            ax.boxplot(values, tick_labels=methods)
        ax.set_ylabel("Fraction" if key == "quality" else "Seconds")
        save_figure(fig, output / f"{name}.png", dataset, description, manifest)
    write_rows(output / "plot_manifest.csv", manifest)


def compare_run(run_dir):
    """Called only AFTER open_services exits. Rerun via standalone CLI if needed."""
    run_dir = Path(run_dir)
    recipe = json.loads((run_dir / "metadata/config_snapshot.json").read_text())
    cfg = settings(recipe["nmr"])["phase_comparison"]
    if not cfg["enabled"]:
        return {"status": "disabled"}
    methods = cfg["methods"] + (["unphased"] if cfg["include_unphased_reference"] else [])
    all_rows, failures = [], []
    for step in ("step_1", "step_2"):
        step_rows, priors = [], {}
        for evidence in sorted((run_dir / step / "nmr_processed").glob("*/phase_audit/*/*spectral_evidence.npz")):
            try:
                audit = json.loads(evidence.with_name(evidence.name.replace("spectral_evidence.npz", "processing_metadata.json")).read_text())
                source = Path(audit["source_path"])
                # A copied experiment can retain the original absolute source
                # path in its audit. Prefer its local, hash-verified raw file.
                local_source = run_dir / step / "nmr_raw" / source.name
                if local_source.is_file():
                    source = local_source
                rows = acquisition_comparison(source, evidence, recipe["analysis"], methods,
                    evidence.parents[2] / "phase_comparison", dataset=run_dir.name, priors=priors,
                    solvent_settings=recipe.get("solvent_monitor"), reporting=recipe.get("qc_reporting"))
                for row in rows:
                    row["step"] = step
                    if not row.get("success"):
                        failures.append({"evidence": str(evidence), "method": row["method"], "error": row.get("error")})
                step_rows.extend(rows)
            except Exception as exc:
                failures.append({"evidence": str(evidence), "error": str(exc)})
        if step_rows:
            write_rows(run_dir / step / "analysis/phase_method_summary.csv", [{k: v for k, v in r.items() if not isinstance(v, (dict, list))} for r in step_rows])
        all_rows.extend(step_rows)
    target = run_dir / "final_qc/phase_method_comparison"
    summarize(all_rows, target, run_dir.name)
    write_json(target / "comparison_failures.json", failures)
    return {"results": len(all_rows), "failures": failures, "output": str(target)}
