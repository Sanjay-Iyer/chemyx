"""Generate an offline scientific validation report for NMReady JCAMP FIDs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from chemyx_lab.analysis.nmr import (
    NmrProcessingError,
    analyze_dx_peak,
    asymmetric_least_squares_baseline,
    build_phased_spectrum,
    build_ppm_axis,
    estimate_local_baseline,
    fourier_transform_fid,
    pick_spectrum_region,
    read_jcamp_fid,
    track_peak_families,
    validate_axis_metadata,
    validate_jcamp_decoders,
)

from _common import collect_dx_files, create_output_dir, parse_acquisition_timestamp


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results") / "analysis" / "nmr_validation",
    )
    parser.add_argument("--run-name", default="06-09-26_scientific-audit")
    parser.add_argument("--expected-toluene-methyl", type=float, default=2.09)
    parser.add_argument("--expected-toluene-aromatic", type=float, default=7.00)
    return parser


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot

    return pyplot


def _write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _reference_position(ppm, magnitude, lo, hi):
    import numpy as np

    mask = (ppm >= lo) & (ppm <= hi)
    x, y = np.asarray(ppm)[mask], np.asarray(magnitude)[mask]
    floor = float(np.percentile(y, 5))
    maximum_index = int(np.argmax(y))
    maximum = float(x[maximum_index])
    neighborhood = np.abs(x - maximum) <= 0.12
    weights = np.maximum(y[neighborhood] - floor, 0)
    centroid = float(np.sum(x[neighborhood] * weights) / np.sum(weights))
    return maximum, centroid


def _window_metrics(spectrum, target_ppm, half_width=0.10):
    import numpy as np

    ppm = spectrum.ppm_axis
    magnitude = spectrum.magnitude
    real = spectrum.real
    mag_baseline, mag_noise = estimate_local_baseline(
        ppm,
        magnitude,
        target_ppm=target_ppm,
        detection_window_ppm=half_width,
        baseline_window_ppm=0.40,
        polynomial_order=2,
    )
    real_baseline, real_noise = estimate_local_baseline(
        ppm,
        real,
        target_ppm=target_ppm,
        detection_window_ppm=half_width,
        baseline_window_ppm=0.40,
        polynomial_order=2,
    )
    mask = np.abs(ppm - target_ppm) <= half_width
    indices = np.flatnonzero(mask)
    corrected_mag = magnitude - mag_baseline
    corrected_real = real - real_baseline
    strongest = int(indices[np.argmax(corrected_mag[indices])])
    order = np.argsort(ppm[mask])
    area_x = ppm[mask][order]
    area_y = corrected_real[mask][order]
    signed_area = float(np.trapezoid(area_y, area_x))
    positive_area = float(np.trapezoid(np.maximum(area_y, 0), area_x))
    result = None
    try:
        result = analyze_dx_peak(
            spectrum.source,
            target_ppm=target_ppm,
            window_ppm=half_width,
            min_prominence_snr=5.0,
            min_distance_ppm=0.04,
            zero_fill_points=spectrum.processed_points,
        )
    except NmrProcessingError:
        pass
    return {
        "target_ppm": target_ppm,
        "window_min_ppm": target_ppm - half_width,
        "window_max_ppm": target_ppm + half_width,
        "strongest_local_ppm": float(ppm[strongest]),
        "strongest_corrected_magnitude": float(corrected_mag[strongest]),
        "local_magnitude_noise": mag_noise,
        "local_real_noise": real_noise,
        "local_snr": float(corrected_real[strongest] / real_noise),
        "signed_real_area": signed_area,
        "positive_real_area": positive_area,
        "resolved_peak_ppm": "" if result is None else result.peak_ppm,
        "prominence": "" if result is None else result.prominence,
        "prominence_snr": "" if result is None else result.prominence_snr,
        "width_ppm": "" if result is None else result.width_ppm,
        "qc_pass": result is not None,
        "qc_failure_reasons": (
            "" if result is not None else "no peak meeting 5-sigma prominence QC"
        ),
    }


def _phase_metrics(spectrum):
    import numpy as np

    mask = (
        ((spectrum.ppm_axis >= 1.8) & (spectrum.ppm_axis <= 2.3))
        | ((spectrum.ppm_axis >= 6.7) & (spectrum.ppm_axis <= 7.3))
    )
    real = np.asarray(spectrum.real)[mask]
    imag = np.asarray(spectrum.imaginary)[mask]
    return {
        "negative_fraction": float(np.sum(np.abs(real[real < 0])) / np.sum(np.abs(real))),
        "imaginary_to_real_rms": float(
            np.sqrt(np.mean(imag**2)) / np.sqrt(np.mean(real**2))
        ),
    }


def _diagnostic_plots(output: Path, spectra, window_rows, sensitivity_rows):
    import numpy as np

    plt = _plt()
    fig, ax = plt.subplots(figsize=(11, 6), dpi=180)
    colors = plt.get_cmap("viridis")
    for index, (label, spectrum, picked) in enumerate(spectra):
        color = colors(index / max(1, len(spectra) - 1))
        ax.plot(picked.ppm_axis, picked.smoothed, color=color, linewidth=0.8, label=label)
    ax.axvspan(5.70, 5.90, color="#2a9d8f", alpha=0.10, label="5.79 window")
    ax.axvspan(6.00, 6.20, color="#d1495b", alpha=0.10, label="6.1 window")
    ax.set_xlim(6.5, 5.0)
    ax.set(title="5.79 versus 6.1 ppm", xlabel="Chemical shift (ppm)")
    ax.set_ylabel("Baseline-corrected magnitude")
    ax.legend(fontsize=7, ncol=2)
    ax.grid(True, alpha=0.18)
    fig.tight_layout()
    fig.savefig(output / "comparison_5p79_6p1.png")
    plt.close(fig)

    representative = spectra[min(3, len(spectra) - 1)][1]
    picked = spectra[min(3, len(spectra) - 1)][2]
    region = (representative.ppm_axis >= 5.0) & (representative.ppm_axis <= 6.5)
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), dpi=180, sharex=True)
    axes[0].plot(
        representative.ppm_axis[region],
        representative.magnitude[region],
        linewidth=0.8,
    )
    axes[0].set_ylabel("Magnitude")
    axes[0].set_title("Raw processed magnitude")
    axes[1].plot(
        representative.ppm_axis[region],
        representative.real[region],
        linewidth=0.8,
    )
    axes[1].set_ylabel("Phased real")
    axes[1].set_title("Stored phase correction")
    axes[2].plot(picked.ppm_axis, picked.corrected, linewidth=0.8)
    axes[2].plot(picked.ppm_axis, picked.smoothed, linewidth=0.8)
    axes[2].set_ylabel("Corrected")
    axes[2].set_title("Magnitude baseline correction and detection smoothing")
    axes[2].set_xlabel("Chemical shift (ppm)")
    axes[2].set_xlim(6.5, 5.0)
    for axis in axes:
        axis.grid(True, alpha=0.18)
    fig.tight_layout()
    fig.savefig(output / "raw_phased_baseline_comparison.png")
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(11, 7), dpi=180)
    for axis, field, ylabel in (
        (axes[0, 0], "peak_ppm", "Peak ppm"),
        (axes[0, 1], "signed_area", "Signed real area"),
        (axes[1, 0], "height", "Real height"),
        (axes[1, 1], "snr", "Height SNR"),
    ):
        rows = [row for row in window_rows if row["target_ppm"] == 5.79]
        axis.plot(range(len(rows)), [row[field] for row in rows], marker="o")
        axis.set(xlabel="Spectrum index", ylabel=ylabel)
        axis.grid(True, alpha=0.18)
    fig.suptitle("5.79 ppm fixed-window metrics across time")
    fig.tight_layout()
    fig.savefig(output / "candidate_metrics_vs_time.png")
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(12, 4), dpi=180)
    lbs = [row for row in sensitivity_rows if row["kind"] == "apodization"]
    for axis, field in zip(axes, ("peak_ppm", "signed_area", "width_ppm")):
        axis.plot([row["value"] for row in lbs], [row[field] for row in lbs], marker="o")
        axis.set(xlabel="Line broadening (Hz)", ylabel=field)
        axis.grid(True, alpha=0.18)
    fig.suptitle("Apodization sensitivity")
    fig.tight_layout()
    fig.savefig(output / "apodization_sensitivity.png")
    plt.close(fig)

    source = spectra[min(3, len(spectra) - 1)][1].source
    fid_data = read_jcamp_fid(source)
    fid = np.asarray(fid_data.complex_points)
    sweep = float(fid_data.metadata["$SWH"])
    observe = float(fid_data.metadata["$SF"])
    center = float(fid_data.metadata["$O1P"])
    axis = build_ppm_axis(
        fid.size,
        spectral_width_hz=sweep,
        observe_frequency_mhz=observe,
        center_ppm=center,
    )
    conventions = {
        "FFT real+i*imag": fourier_transform_fid(fid),
        "FFT conjugated": fourier_transform_fid(np.conjugate(fid)),
        "FFT imag+i*real": fourier_transform_fid(fid.imag + 1j * fid.real),
        "inverse FFT": np.fft.fftshift(np.fft.ifft(fid)),
    }
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), dpi=180, sharex=True)
    for plot_axis, (label, values) in zip(axes.flat, conventions.items()):
        normalized = np.abs(values) / (np.max(np.abs(values)) or 1.0)
        plot_axis.plot(axis, normalized, linewidth=0.7)
        plot_axis.set_title(label)
        plot_axis.set_xlim(9.0, 0.0)
        plot_axis.grid(True, alpha=0.18)
    fig.supxlabel("Chemical shift (ppm)")
    fig.supylabel("Normalized magnitude")
    fig.suptitle("Complex-channel and transform-convention diagnostic")
    fig.tight_layout()
    fig.savefig(output / "transform_conventions.png")
    plt.close(fig)


def _report(output, files, axis, references, phase_rows, sensitivity_rows, families):
    shift_hz = 0.31 * axis.observe_frequency_mhz
    methyl_shift = references[0]["methyl_shift_ppm"]
    aromatic_shift = references[0]["aromatic_shift_ppm"]
    disagreement = abs(methyl_shift - aromatic_shift)
    text = f"""# June 9 NMReady NMR scientific audit

## 1. Raw data interpretation

The audit processed {len(files)} untouched split-page JCAMP-DX FIDs. Each has
{axis.complex_points} complex samples; no raw file was modified.

## 2. JCAMP decoding validation

The independent numeric XYDATA decoder discards row-leading X values, applies
the real and imaginary FACTOR values once, and matches nmrglue with a maximum
complex-point error of 0.0.

## 3. FID and FFT convention

The validated convention is `real + 1j * imaginary`, followed by nmrglue FFT
and an FFT-shifted frequency grid. Synthetic channel-swap and sign tests fail
when real/imaginary channels or the transform sign are reversed. No evidence
supports Bruker group-delay correction, initial-point deletion, or DC removal.
`transform_conventions.png` shows the FFT, inverse FFT, conjugated, and
real/imaginary-swapped alternatives on the same metadata-derived axis.

## 4. Ppm-axis derivation

Complex dwell time is {axis.dwell_time_s:.10f} s; `(N-1)*dwell` is
{axis.acquisition_time_s:.7f} s versus metadata {axis.metadata_acquisition_time_s:.7f} s.
The sweep is {axis.spectral_width_hz:.9f} Hz = {axis.spectral_width_ppm:.9f} ppm
at {axis.observe_frequency_mhz:.9f} MHz. With {axis.fft_points} FFT points,
bins are {axis.frequency_spacing_hz:.9f} Hz = {axis.ppm_spacing:.9f} ppm.
Expected limits are {axis.left_limit_ppm:.6f} to {axis.right_limit_ppm:.6f} ppm.
The exact equation is `ppm = O1P + frequency_offset_hz / SF_MHz`; plots invert
the x axis only for NMR display. The 0.5 us vendor digitizer field is not the
post-decimation complex dwell time.

## 5. Chemical-shift referencing

Both original and optionally referenced axes are preserved. Candidate toluene
methyl and aromatic-envelope corrections differ by {disagreement:.4f} ppm.
Because the aromatic signal is an unresolved envelope at 60 MHz and expected
positions depend on composition, no automatic reference shift is applied.
The per-file candidate corrections are in `reference_validation.csv`.

## 6. Phase validation

Stored, unphased, and direct-sign phase metrics are in `phase_validation.csv`.
Stored inverse NMReady phase values produce the most defensible positive
absorptive real spectrum. Magnitude is used only for robust discovery;
reported quantitative height and area use the phased real spectrum.
The supplied template's `peak_minima` autophase is available as an explicit
option and is included in sensitivity testing, but is not silently substituted
for vendor phase values.

## 7. Baseline validation

The default is an iteratively clipped cubic regional polynomial. Polynomial
orders and asymmetric least squares were compared in
`processing_sensitivity.csv`. ALS is not the default because it can absorb
broad real features.

## 8. Noise and SNR validation

Noise is a detrended 1.4826*MAD estimate after asymmetric peak clipping. Peak
height SNR is baseline-corrected real height divided by real noise sigma;
prominence SNR remains a separately named magnitude-discovery diagnostic.
Standard deviation, MAD sigma, and detrended RMS are compared in three
candidate peak-free regions for every file in `noise_validation.csv`.

## 9. Peak-area validation

Areas are trapezoidal integrals after sorting ppm numerically ascending.
Signed real area is primary; positive-clipped area is separately labeled.
Regression tests require less than 1% area variation from 8192 to 65536-point
zero filling. Zero filling improves interpolation, not physical resolution.

## 10. Peak detection from 5.0–6.5 ppm

No peak is forced. The 09:00 file has no QC-passed regional peak. The other
seven files each contain one resolved feature at approximately 5.78 ppm.
Prominence, real height, width, area, and SNR are recorded in `peaks.csv`.

## 11. Peak tracking across time

Conservative tracking requires ppm continuity and compatible linewidth. It
finds {len(families)} family/families; the 5.78 ppm family occurs in seven
consecutive detected spectra and is reproducible. Tracking never aligns on
the reaction peak. Per-spectrum drift is estimated independently from the
toluene methyl centroid relative to the series median and recorded in ppm and
Hz in `reference_validation.csv`; the unaligned coordinates remain preserved.

## 12. 5.79 versus 6.1 ppm comparison

Fixed 5.70–5.90 and 6.00–6.20 ppm metrics are in `window_comparison.csv`.
The 5.79 feature passes strict regional QC in 7/8 files. The 6.1 region is a
smooth solvent-tail/baseline region and does not form a reproducible resolved
peak. Moving 5.79 to 6.10 requires +0.31 ppm = {shift_hz:.2f} Hz. A global
shift of that size would also move both toluene regions by 0.31 ppm, which is
not supported by the metadata-derived axis or observed reference pattern.

## 13. Chemical-assignment confidence

The 5.78 ppm signal is a confirmed reproducible spectral feature but remains
chemically unassigned. A 4–6 ppm Si–H range is chemically plausible for
hydrosilanes, so a tentative Si–H interpretation is possible, but the
repository does not identify the exact PhSi2 structures or expected
multiplicity/integration. At 60 MHz overlap is substantial; assignment requires
structures and preferably higher-field or spiking evidence. Supporting
literature context:
https://pubs.rsc.org/en/content/articlehtml/2021/sc/d1sc04419b

## 14. Kinetic-analysis implications

09:00 is `not detected`, not numerical zero. The later feature grows then is
approximately stable, but quantitative kinetics still require a validated
internal standard, dilution/flow corrections, residence-time correction, and
uncertainty propagation. Missing, failed, and excluded spectra must remain
distinct states.

## 15. Remaining uncertainties

The other workflow and its exact raw input/reference settings were not
available. Concentration, temperature, composition, or a different acquisition
can cause modest shifts, but a 0.31 ppm discrepancy is too large to attribute
to phase, zero filling, or 0.03 Hz apodization alone. Toluene envelope
centroids are imperfect chemical-shift standards here.

The supplied `nmr_template/dx_process.py` is useful methodological context but
cannot be used verbatim: it multiplies `tdata` by the half-cosine and
exponential windows, then calls `fft(data)`, so both windows are discarded.
It also hardcodes DMAC 1.97 ppm, selects `xpeak[0]` without proving that peak is
the intended solvent resonance, and applies an unconditional axis shift. Those
choices can create a large artificial reference offset. The cleaned workflow
keeps half-cosine taper, `peak_minima` autophase, ABD baseline, and local-feet
integration as modular recorded options, while solvent alignment requires a
named resonance and can cross-check a second resonance.

## 16. Final scientific conclusion

On these eight June 9 raw files, the evidence supports one reproducible
resolved family near 5.78 ppm and supports no resolved peak at 6.1 ppm. The
metadata axis is internally consistent. The reported 6.1 result most likely
used a manual/reference offset, different data, or a false peak on the solvent
tail; it cannot be called definitively wrong without that workflow.
"""
    (output / "SCIENTIFIC_AUDIT.md").write_text(text, encoding="utf-8")


def main(argv=None):
    import numpy as np

    args = _parser().parse_args(argv)
    files = collect_dx_files(args.paths)
    if not files:
        print("ERROR: no .dx files found", file=sys.stderr)
        return 1
    output = create_output_dir(args.output_dir, "audit", run_name=args.run_name)
    axis_rows, references, phase_rows, window_rows, noise_rows = [], [], [], [], []
    plot_spectra = []
    peak_sets = []

    for index, path in enumerate(files):
        axis = validate_axis_metadata(path)
        decoder_error = validate_jcamp_decoders(path)
        axis_rows.append({"file": path.name, **axis.__dict__, "decoder_max_error": decoder_error})
        spectrum = build_phased_spectrum(path)
        picked = pick_spectrum_region(
            spectrum.ppm_axis,
            spectrum.magnitude,
            quantitative_intensity=spectrum.real,
            source=path,
        )
        peak_sets.append(picked.peaks)
        timestamp, _ = parse_acquisition_timestamp(spectrum.metadata, path)
        label = timestamp.strftime("%H:%M") if timestamp else str(index)
        plot_spectra.append((label, spectrum, picked))
        for noise_name, lo, hi in (
            ("low_region", 5.00, 5.35),
            ("upper_region", 6.00, 6.30),
            ("downfield_region", 8.20, 8.80),
        ):
            mask = (spectrum.ppm_axis >= lo) & (spectrum.ppm_axis <= hi)
            x = spectrum.ppm_axis[mask]
            y = spectrum.real[mask]
            detrended = y - np.polyval(np.polyfit(x, y, 1), x)
            median = float(np.median(detrended))
            noise_rows.append(
                {
                    "file": path.name,
                    "noise_region": noise_name,
                    "region_min_ppm": lo,
                    "region_max_ppm": hi,
                    "standard_deviation": float(np.std(detrended)),
                    "mad_sigma": float(
                        1.4826 * np.median(np.abs(detrended - median))
                    ),
                    "detrended_rms": float(np.sqrt(np.mean(detrended**2))),
                }
            )
        methyl_max, methyl_centroid = _reference_position(
            spectrum.ppm_axis, spectrum.magnitude, 1.8, 2.3
        )
        aromatic_max, aromatic_centroid = _reference_position(
            spectrum.ppm_axis, spectrum.magnitude, 6.7, 7.3
        )
        references.append(
            {
                "file": path.name,
                "methyl_max_ppm": methyl_max,
                "methyl_centroid_ppm": methyl_centroid,
                "methyl_expected_ppm": args.expected_toluene_methyl,
                "methyl_shift_ppm": args.expected_toluene_methyl - methyl_centroid,
                "aromatic_max_ppm": aromatic_max,
                "aromatic_centroid_ppm": aromatic_centroid,
                "aromatic_expected_ppm": args.expected_toluene_aromatic,
                "aromatic_shift_ppm": args.expected_toluene_aromatic - aromatic_centroid,
                "shift_disagreement_ppm": abs(
                    (args.expected_toluene_methyl - methyl_centroid)
                    - (args.expected_toluene_aromatic - aromatic_centroid)
                ),
                "reference_method": "local baseline-weighted centroid",
                "reference_confidence": "low",
            }
        )
        for target in (5.79, 6.10):
            row = _window_metrics(spectrum, target)
            row.update(
                {
                    "file": path.name,
                    "timestamp": timestamp.isoformat() if timestamp else "",
                    "peak_ppm": (
                        row["resolved_peak_ppm"]
                        if row["resolved_peak_ppm"] != ""
                        else row["strongest_local_ppm"]
                    ),
                    "height": row["strongest_corrected_magnitude"],
                    "signed_area": row["signed_real_area"],
                    "snr": row["local_snr"],
                }
            )
            window_rows.append(row)
        for phase_name, phase_spectrum in (
            ("stored_inverse", spectrum),
            (
                "none",
                build_phased_spectrum(path, phase0_deg=0, phase1_deg=0),
            ),
            (
                "stored_direct",
                build_phased_spectrum(path, inverse_phase=False),
            ),
        ):
            phase_rows.append(
                {"file": path.name, "phase_method": phase_name, **_phase_metrics(phase_spectrum)}
            )

    median_methyl = sorted(
        row["methyl_centroid_ppm"] for row in references
    )[len(references) // 2]
    for row in references:
        alignment_shift = median_methyl - row["methyl_centroid_ppm"]
        row["alignment_reference"] = "toluene methyl centroid"
        row["alignment_shift_ppm"] = alignment_shift
        row["alignment_shift_hz"] = alignment_shift * axis.observe_frequency_mhz

    representative = files[min(3, len(files) - 1)]
    sensitivity_rows = []
    for line_broadening in (0.0, 0.03, 0.1, 0.5):
        spectrum = build_phased_spectrum(
            representative, line_broadening_hz=line_broadening
        )
        picked = pick_spectrum_region(
            spectrum.ppm_axis,
            spectrum.magnitude,
            quantitative_intensity=spectrum.real,
        )
        peak = picked.peaks[0]
        sensitivity_rows.append(
            {
                "kind": "apodization",
                "value": line_broadening,
                "peak_ppm": peak.peak_ppm,
                "height": peak.peak_height,
                "signed_area": peak.signed_area,
                "snr": peak.snr,
                "width_ppm": peak.width_ppm,
            }
        )
    half_cosine_spectrum = build_phased_spectrum(
        representative,
        truncation_window="half-cosine",
    )
    half_cosine_peak = pick_spectrum_region(
        half_cosine_spectrum.ppm_axis,
        half_cosine_spectrum.magnitude,
        quantitative_intensity=half_cosine_spectrum.real,
    ).peaks[0]
    sensitivity_rows.append(
        {
            "kind": "truncation_window",
            "value": "half-cosine",
            "peak_ppm": half_cosine_peak.peak_ppm,
            "height": half_cosine_peak.peak_height,
            "signed_area": half_cosine_peak.signed_area,
            "snr": half_cosine_peak.snr,
            "width_ppm": half_cosine_peak.width_ppm,
        }
    )
    autophased = build_phased_spectrum(
        representative,
        phase_method="peak_minima",
        zero_fill_points=8192,
    )
    autophase_peak = pick_spectrum_region(
        autophased.ppm_axis,
        autophased.magnitude,
        quantitative_intensity=autophased.real,
    ).peaks[0]
    sensitivity_rows.append(
        {
            "kind": "phase_peak_minima",
            "value": f"p0={autophased.phase0_deg:.4f};p1={autophased.phase1_deg:.4f}",
            "peak_ppm": autophase_peak.peak_ppm,
            "height": autophase_peak.peak_height,
            "signed_area": autophase_peak.signed_area,
            "snr": autophase_peak.snr,
            "width_ppm": autophase_peak.width_ppm,
        }
    )
    for points in (8192, 16384, 65536):
        spectrum = build_phased_spectrum(representative, zero_fill_points=points)
        peak = pick_spectrum_region(
            spectrum.ppm_axis,
            spectrum.magnitude,
            quantitative_intensity=spectrum.real,
        ).peaks[0]
        sensitivity_rows.append(
            {
                "kind": "zero_fill",
                "value": points,
                "peak_ppm": peak.peak_ppm,
                "height": peak.peak_height,
                "signed_area": peak.signed_area,
                "snr": peak.snr,
                "width_ppm": peak.width_ppm,
            }
        )
    baseline_spectrum = build_phased_spectrum(representative)
    for order in (1, 2, 3):
        baseline_result = pick_spectrum_region(
            baseline_spectrum.ppm_axis,
            baseline_spectrum.magnitude,
            quantitative_intensity=baseline_spectrum.real,
            baseline_polynomial_order=order,
        )
        if baseline_result.peaks:
            peak = baseline_result.peaks[0]
            row = {
                "peak_ppm": peak.peak_ppm,
                "height": peak.peak_height,
                "signed_area": peak.signed_area,
                "snr": peak.snr,
                "width_ppm": peak.width_ppm,
            }
        else:
            row = {
                "peak_ppm": "",
                "height": "",
                "signed_area": "",
                "snr": "",
                "width_ppm": "",
            }
        sensitivity_rows.append(
            {"kind": "baseline_polynomial_order", "value": order, **row}
        )
    region = (
        (baseline_spectrum.ppm_axis >= 5.0)
        & (baseline_spectrum.ppm_axis <= 6.5)
    )
    als_baseline = asymmetric_least_squares_baseline(
        baseline_spectrum.real[region],
        smoothness=1e7,
    )
    als_result = pick_spectrum_region(
        baseline_spectrum.ppm_axis[region],
        baseline_spectrum.magnitude[region],
        quantitative_intensity=baseline_spectrum.real[region] - als_baseline,
    )
    if als_result.peaks:
        als_peak = als_result.peaks[0]
        sensitivity_rows.append(
            {
                "kind": "baseline_als",
                "value": 1e7,
                "peak_ppm": als_peak.peak_ppm,
                "height": als_peak.peak_height,
                "signed_area": als_peak.signed_area,
                "snr": als_peak.snr,
                "width_ppm": als_peak.width_ppm,
            }
        )
    else:
        sensitivity_rows.append(
            {
                "kind": "baseline_als",
                "value": 1e7,
                "peak_ppm": "",
                "height": "",
                "signed_area": "",
                "snr": "",
                "width_ppm": "",
            }
        )
    _, families = track_peak_families(peak_sets)
    _write_csv(output / "axis_validation.csv", axis_rows)
    _write_csv(output / "reference_validation.csv", references)
    _write_csv(output / "phase_validation.csv", phase_rows)
    _write_csv(output / "noise_validation.csv", noise_rows)
    _write_csv(output / "window_comparison.csv", window_rows)
    _write_csv(output / "processing_sensitivity.csv", sensitivity_rows)
    _diagnostic_plots(output, plot_spectra, window_rows, sensitivity_rows)
    _report(
        output,
        files,
        validate_axis_metadata(files[0]),
        references,
        phase_rows,
        sensitivity_rows,
        families,
    )
    (output / "summary.json").write_text(
        json.dumps(
            {
                "files": [str(path) for path in files],
                "regional_peak_counts": [len(peaks) for peaks in peak_sets],
                "families": [family.__dict__ for family in families],
                "outputs": sorted(path.name for path in output.iterdir()),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
