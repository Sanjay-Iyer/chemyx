"""Additive phase/peak evidence; contains no hardware or decision overrides."""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .nmr import _build_complex_spectrum
from .plot_titles import format_dataset_plot_title, resolve_dataset_display_name


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provenance():
    root = Path(__file__).resolve().parents[2]
    def git(*args):
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else "unavailable"
    versions = {}
    for name in ("numpy", "scipy", "nmrglue", "matplotlib", "PySide6"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    return {"created_at_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
            "versions": versions,
            "source_code_sha256": {str(p.relative_to(root)): sha256(p) for p in
                (root / "chemyx_lab/analysis/nmr.py", root / "scripts/nmr/process_fid.py",
                 root / "chemyx_lab/analysis/target_peak_report.py",
                 root / "chemyx_lab/analysis/statistics_report.py",
                 root / "chemyx_lab/analysis/plot_titles.py",
                 root / "chemyx_lab/workflows/si6_automated_nmr.py",
                 root / "chemyx_lab/analysis/nmr_validation.py",
                 root / "scripts/nmr/validation_phase_gui.py", Path(__file__)) if p.is_file()}}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding="utf-8")


def write_rows(path, rows, columns=None):
    rows = list(rows)
    columns = columns or list(dict.fromkeys(k for row in rows for k in row))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def save_figure(fig, stem, dataset, description, manifest, *, formats=("png", "svg", "pdf")):
    """One visible figure title and identity shared by every format."""
    import matplotlib.pyplot as plt
    dataset = " ".join(str(dataset).split())
    title = format_dataset_plot_title(dataset, description)
    fig.suptitle(title, fontsize=12)
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    for extension in formats:
        path = stem.with_suffix("." + extension)
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
        manifest.append({"path": str(path.resolve()), "dataset_display_name": dataset,
                         "visible_title": title})
    plt.close(fig)
    return title


def phase_parameters(spectrum, unphased):
    inverse = spectrum.phase_method in {"stored", "manual"}
    # Caller supplies the actual direction; automatic nmrglue methods are direct.
    return {"method": spectrum.phase_method, "p0_deg": spectrum.phase0_deg,
            "p1_deg": spectrum.phase1_deg, "pivot_fraction": 0.0,
            "pivot_ppm": float(spectrum.ppm_axis[0]), "phase_denominator": len(unphased),
            "stored_source_fields": ["$PHC0", "$PHC1"],
            "inverse_phase": inverse,
            "equation": "S[k] * exp(sign * i*pi/180 * (P0 + P1*k/N))",
            "optimization_objective": None if spectrum.phase_method in {"stored", "manual", "none"} else
                "nmrglue " + spectrum.phase_method,
            "phase_bounds": None,
            "fft_convention": "numpy.fft.fftshift(numpy.fft.fft(complex_FID))",
            "axis_convention": "ascending internal ppm; display high ppm at left"}


def retain_phase_audit(spectrum, args, analysis_ppm, quantitative_real, picked, output,
                       *, dataset=None, unphased=None, provenance_data=None):
    """Retain lossless arrays and a readable phase audit without changing processing."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if unphased is None:
        _, _, unphased, _, _ = _build_complex_spectrum(
            spectrum.source, line_broadening_hz=spectrum.line_broadening_hz,
            zero_fill_points=spectrum.processed_points, truncation_window=spectrum.truncation_window)
    unphased = np.asarray(unphased).copy()
    phased = np.asarray(spectrum.real) + 1j * np.asarray(spectrum.imaginary)
    # Baseline before optional normalization; also retain the final quantitative trace.
    baseline = np.asarray(spectrum.real) - np.asarray(quantitative_real)
    if args.normalization == "max":
        baseline_description = "phased_real minus final normalized quantitative trace; not physical baseline"
    else:
        baseline_description = "global baseline removed from phased real"
    np.savez_compressed(output / "spectral_evidence.npz", ppm=spectrum.ppm_axis,
                        analysis_ppm=analysis_ppm, unphased=unphased, phased=phased,
                        baseline=baseline, quantitative_real=quantitative_real,
                        region_ppm=picked.ppm_axis, regional_baseline=picked.baseline,
                        detection_smoothed=picked.smoothed,
                        regional_quantitative=picked.quantitative_corrected)
    params = phase_parameters(spectrum, unphased)
    params["inverse_phase"] = bool(not args.direct_phase) if spectrum.phase_method in {"stored", "manual"} else False
    params["sign"] = -1 if params["inverse_phase"] else 1
    dataset = dataset or resolve_dataset_display_name(
        input_paths=spectrum.source, metadata=spectrum.metadata)
    meta = {**(provenance_data or provenance()), "schema": "chemyx.nmr-phase-audit.v1",
            "source_path": str(Path(spectrum.source).resolve()),
            "raw_sha256": sha256(spectrum.source), "dataset_display_name": dataset,
            "phase": params, "parameters": vars(args), "jcamp_metadata": spectrum.metadata,
            "processing_function": "scripts/nmr/process_fid.py:process_spectrum_for_peaks",
            "baseline_array_description": baseline_description,
            "decision_scope": "regional processing only; controller decisions require run sequence and config"}
    write_json(output / "processing_metadata.json", meta)
    write_json(output / "phase_parameters.json", params)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), layout="constrained", sharex=True)
    x = np.asarray(spectrum.ppm_axis)
    panels = [(unphased.real, "A Unphased real"), (phased.real, "B Phased real"),
              (None, "C Before / after"), (phased.imag, "D Phased imaginary")]
    for ax, (y, title) in zip(axes.flat, panels):
        if y is None:
            ax.plot(x, unphased.real, lw=.65, label="Before")
            ax.plot(x, phased.real, lw=.65, label="After")
            ax.legend(fontsize=8)
        else:
            ax.plot(x, y, lw=.65)
        ax.set(title=title, xlabel="Chemical shift (ppm)", ylabel="Intensity (a.u.)",
               xlim=(float(x.max()), float(x.min())))
        ax.axvspan(args.region_min, args.region_max, alpha=.1, color="orange")
        ax.axhline(0, lw=.5, color="gray")
    manifest = []
    save_figure(fig, output / "phase_before_after", dataset,
                f"Phase Audit — {spectrum.phase_method}, P0={spectrum.phase0_deg:.3f}°, "
                f"P1={spectrum.phase1_deg:.3f}°, pivot k=0", manifest, formats=("png",))
    write_rows(output / "plot_manifest.csv", manifest)
    return meta


def retain_monitoring_audit(source, result, analysis, measurement_metadata, output):
    """Preserve the controller's distinct phase-insensitive measurement evidence."""
    from .nmr import estimate_local_baseline
    source=Path(source)
    fid,ppm,unphased,points,lb=_build_complex_spectrum(source,
        line_broadening_hz=float(analysis.get('line_broadening_hz',.3)),zero_fill_points=None)
    magnitude=np.abs(unphased)
    baseline,noise=estimate_local_baseline(ppm,magnitude,target_ppm=result.target_ppm,
        detection_window_ppm=float(analysis['detection_window_ppm']),
        baseline_window_ppm=result.baseline_window_ppm,polynomial_order=result.baseline_polynomial_order)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(output/'monitoring_evidence.npz',ppm=ppm,unphased=unphased,
                        magnitude=magnitude,baseline=baseline,corrected=magnitude-baseline)
    meta={**provenance(),'schema':'chemyx.nmr-monitoring-audit.v1','source_path':str(source.resolve()),
        'raw_sha256':sha256(source),'actual_acquisition_long_date':fid.metadata.get('LONG DATE'),
        'timestamp_source':'LONG DATE header' if fid.metadata.get('LONG DATE') else 'unavailable',
        'phase_method':'none; magnitude is phase invariant','processing_function':'nmr.analyze_dx_peak',
        'peak_result':asdict_peak_result(result),'analysis_parameters':analysis,
        'measurement_metadata':measurement_metadata,'fft_points':points,'line_broadening_hz':lb,
        'decision_function':'si6_automated_nmr.plateau_reached; sequence decision in time_series/journal'}
    write_json(output/'processing_metadata.json',meta)
    return meta


def asdict_peak_result(result):
    from dataclasses import asdict
    return asdict(result)
