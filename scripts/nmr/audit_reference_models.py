"""Audit absolute chemical-shift reference models for an NMReady DX series.

This is deliberately a diagnostic workflow. Candidate reference models are
fitted independently in the methyl and aromatic regions, but a shift is not
treated as production-valid unless the declared sample identity, isotopic
form, and multi-region agreement all pass QC.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from chemyx_lab.analysis.nmr import (
    REFERENCE_MODEL_DEFINITIONS,
    build_phased_spectrum,
    evaluate_reference_model,
    half_cosine_truncation_window,
    pick_spectrum_region,
    read_jcamp_fid,
    validate_axis_metadata,
)

from _common import collect_dx_files, create_output_dir, parse_acquisition_timestamp


MODELS = tuple(REFERENCE_MODEL_DEFINITIONS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="DX files or directories")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results") / "analysis" / "nmr_reference_audit",
    )
    parser.add_argument(
        "--run-name",
        default="06-09-26_revised-reference-audit",
    )
    parser.add_argument("--pdf-image", type=Path)
    parser.add_argument("--pdf-target-ppm", type=float, default=6.10)
    parser.add_argument("--pdf-methyl-ppm", type=float, default=2.40)
    parser.add_argument("--pdf-aromatic-ppm", type=float, default=7.30)
    parser.add_argument("--pdf-reading-uncertainty-ppm", type=float, default=0.08)
    parser.add_argument("--line-broadening-hz", type=float, default=0.03)
    parser.add_argument("--zero-fill-points", type=int, default=65536)
    parser.add_argument("--maximum-model-disagreement-ppm", type=float, default=0.05)
    return parser


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot

    return pyplot


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(metadata: dict[str, str], *keys: str) -> float | str:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            try:
                return float(str(value).split(",")[0].strip())
            except ValueError:
                return value
    return ""


def _target_peak(spectrum):
    picked = pick_spectrum_region(
        spectrum.ppm_axis,
        spectrum.magnitude,
        region_min_ppm=5.0,
        region_max_ppm=6.5,
        min_prominence_snr=5.0,
        min_distance_ppm=0.04,
        min_width_ppm=0.015,
        baseline_polynomial_order=3,
        smoothing_window_ppm=0.006,
        quantitative_intensity=spectrum.real,
        source=spectrum.source,
    )
    candidates = [
        peak for peak in picked.peaks if 5.60 <= peak.interpolated_ppm <= 5.95
    ]
    return picked, (max(candidates, key=lambda peak: peak.snr) if candidates else None)


def _normalized(values):
    import numpy as np

    values = np.asarray(values, dtype=float)
    scale = float(np.max(np.abs(values)))
    return values if scale == 0 else values / scale


def _stack_plot(
    output: Path,
    spectra,
    *,
    shifts,
    title: str,
    filename: str,
    limits=(7.6, 1.4),
    annotate_target=False,
) -> None:
    import numpy as np

    plt = _plt()
    fig, ax = plt.subplots(figsize=(12, 7), dpi=180)
    colors = plt.get_cmap("viridis")
    for index, item in enumerate(spectra):
        spectrum = item["spectrum"]
        axis = np.asarray(spectrum.ppm_axis) + float(shifts[index])
        values = _normalized(spectrum.real)
        offset = index * 1.25
        ax.plot(
            axis,
            values + offset,
            color=colors(index / max(1, len(spectra) - 1)),
            linewidth=0.75,
            label=item["label"],
        )
        if annotate_target and item["target_ppm"] is not None:
            ax.plot(
                item["target_ppm"] + float(shifts[index]),
                offset + 0.04,
                marker="v",
                color="black",
                markersize=4,
            )
    ax.set_xlim(*limits)
    ax.set(xlabel="Chemical shift (ppm)", ylabel="Normalized, vertically offset")
    ax.set_title(title)
    ax.grid(True, alpha=0.16)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(output / filename)
    plt.close(fig)


def _region_overlay(output: Path, spectra, lo, hi, title, filename) -> None:
    import numpy as np

    plt = _plt()
    fig, ax = plt.subplots(figsize=(11, 5), dpi=180)
    colors = plt.get_cmap("plasma")
    for index, item in enumerate(spectra):
        spectrum = item["spectrum"]
        axis = np.asarray(spectrum.ppm_axis)
        mask = (axis >= lo) & (axis <= hi)
        values = _normalized(np.asarray(spectrum.real)[mask])
        ax.plot(
            axis[mask],
            values,
            color=colors(index / max(1, len(spectra) - 1)),
            linewidth=0.8,
            alpha=0.85,
            label=item["label"],
        )
    ax.set_xlim(hi, lo)
    ax.set(xlabel="Metadata-derived chemical shift (ppm)", ylabel="Local normalized real")
    ax.set_title(title)
    ax.grid(True, alpha=0.16)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(output / filename)
    plt.close(fig)


def _diagnostic_plots(output, spectra, model_rows, relative_rows, args) -> None:
    import numpy as np

    zeros = [0.0] * len(spectra)
    _stack_plot(
        output,
        spectra,
        shifts=zeros,
        title="Original metadata-derived axis (production coordinates)",
        filename="01_original_metadata_axis.png",
        annotate_target=True,
    )
    _stack_plot(
        output,
        spectra,
        shifts=zeros,
        title="Current referenced axis: no additional reference applied",
        filename="02_current_fail_closed_axis.png",
        annotate_target=True,
    )
    for number, model in enumerate(MODELS, 3):
        rows = [row for row in model_rows if row["reference_model"] == model]
        shifts = [float(row["proposed_shift_ppm"]) for row in rows]
        _stack_plot(
            output,
            spectra,
            shifts=shifts,
            title=(
                f"Diagnostic candidate: {model}\n"
                "Proposed shifts shown; not applied to production data"
            ),
            filename=f"{number:02d}_{model}_diagnostic.png",
            annotate_target=True,
        )
    pdf_shifts = [float(row["pdf_consensus_shift_ppm"]) for row in relative_rows]
    _stack_plot(
        output,
        spectra,
        shifts=pdf_shifts,
        title="Diagnostic PDF-aligned axis (image-read coordinates; not a calibration)",
        filename="06_pdf_aligned_diagnostic.png",
        annotate_target=True,
    )
    _region_overlay(
        output,
        spectra,
        5.0,
        6.5,
        "Detected 5.78 ppm family and 6.1 ppm comparison region",
        "07_target_region_overlay.png",
    )
    _region_overlay(
        output,
        spectra,
        1.7,
        2.6,
        "Toluene methyl region on the metadata-derived axis",
        "08_methyl_region_overlay.png",
    )
    _region_overlay(
        output,
        spectra,
        6.6,
        7.6,
        "Toluene aromatic envelope on the metadata-derived axis",
        "09_aromatic_region_overlay.png",
    )

    plt = _plt()
    fig, ax = plt.subplots(figsize=(11, 5), dpi=180)
    for model in MODELS:
        rows = [row for row in model_rows if row["reference_model"] == model]
        ax.plot(
            range(len(rows)),
            [row["proposed_shift_ppm"] for row in rows],
            marker="o",
            label=f"{model} proposed",
        )
        ax.plot(
            range(len(rows)),
            [row["applied_shift_ppm"] for row in rows],
            linestyle="--",
            alpha=0.7,
            label=f"{model} applied",
        )
    ax.set(xlabel="Spectrum index", ylabel="Shift (ppm)")
    ax.set_title("Proposed versus applied shifts (all applied shifts remain zero)")
    ax.grid(True, alpha=0.16)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(output / "10_proposed_vs_applied_shift.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 5), dpi=180)
    for model in MODELS:
        rows = [row for row in model_rows if row["reference_model"] == model]
        ax.plot(
            range(len(rows)),
            [row["reference_region_agreement"] for row in rows],
            marker="o",
            label=model,
        )
    ax.axhline(
        args.maximum_model_disagreement_ppm,
        color="black",
        linestyle="--",
        label="maximum allowed disagreement",
    )
    ax.set(xlabel="Spectrum index", ylabel="Methyl/aromatic shift disagreement (ppm)")
    ax.set_title("Independent reference-region agreement")
    ax.grid(True, alpha=0.16)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(output / "11_reference_region_disagreement.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 5), dpi=180)
    current = [item["target_ppm"] for item in spectra]
    x = np.arange(len(current))
    ax.plot(x, current, marker="o", linewidth=2, label="metadata axis")
    for model in MODELS:
        rows = [row for row in model_rows if row["reference_model"] == model]
        shifted = [
            np.nan if target is None else target + row["proposed_shift_ppm"]
            for target, row in zip(current, rows)
        ]
        ax.plot(x, shifted, marker=".", label=f"{model} diagnostic")
    ax.axhline(args.pdf_target_ppm, color="black", linestyle=":", label="PDF-read target")
    ax.set(xlabel="Spectrum index", ylabel="Target-family ppm")
    ax.set_title("A uniform reference shift moves the same peak; it does not create one")
    ax.grid(True, alpha=0.16)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(output / "12_target_before_after_models.png")
    plt.close(fig)

    valid = [row for row in relative_rows if row["target_ppm"] != ""]
    fig, ax = plt.subplots(figsize=(11, 5), dpi=180)
    ax.plot(
        range(len(valid)),
        [row["target_minus_methyl_hz"] for row in valid],
        marker="o",
        label="target - methyl",
    )
    ax.plot(
        range(len(valid)),
        [row["aromatic_minus_target_hz"] for row in valid],
        marker="o",
        label="aromatic - target",
    )
    ax.axhspan(
        float(valid[0]["pdf_target_minus_methyl_hz_low"]),
        float(valid[0]["pdf_target_minus_methyl_hz_high"]),
        alpha=0.12,
        color="tab:blue",
        label="PDF-read target-methyl uncertainty",
    )
    ax.set(xlabel="Detected spectrum index", ylabel="Frequency separation (Hz)")
    ax.set_title("Reference-invariant peak separations")
    ax.grid(True, alpha=0.16)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "13_relative_frequency_separations.png")
    plt.close(fig)

    representative = next(
        (
            item
            for item in spectra
            if "1045" in item["spectrum"].source.name
        ),
        spectra[len(spectra) // 2],
    )
    unwindowed = representative["spectrum"]
    tapered = build_phased_spectrum(
        unwindowed.source,
        line_broadening_hz=args.line_broadening_hz,
        zero_fill_points=args.zero_fill_points,
        phase_method="stored",
        truncation_window="half-cosine",
    )
    mask = (unwindowed.ppm_axis >= 1.6) & (unwindowed.ppm_axis <= 2.8)
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), dpi=180, sharex=True)
    axes[0].plot(
        unwindowed.ppm_axis[mask],
        _normalized(unwindowed.real[mask]),
        linewidth=0.8,
        label="raw FID + 0.03 Hz exponential",
    )
    axes[1].plot(
        tapered.ppm_axis[mask],
        _normalized(tapered.real[mask]),
        linewidth=0.8,
        color="tab:orange",
        label="half-cosine-tapered FID + 0.03 Hz exponential",
    )
    for axis in axes:
        axis.set_xlim(2.8, 1.6)
        axis.grid(True, alpha=0.16)
        axis.legend(fontsize=8)
        axis.set_ylabel("Local normalized real")
    axes[0].set_title("1045 truncation/ringing sensitivity")
    axes[1].set_xlabel("Chemical shift (ppm)")
    fig.tight_layout()
    fig.savefig(output / "14_ringing_window_sensitivity.png")
    plt.close(fig)

    fid_data = read_jcamp_fid(unwindowed.source)
    fid = np.asarray(fid_data.complex_points)
    window = half_cosine_truncation_window(fid.size)
    fig, ax = plt.subplots(figsize=(11, 4), dpi=180)
    start = int(fid.size * 0.90)
    ax.plot(np.arange(start, fid.size), np.abs(fid[start:]), label="raw FID magnitude")
    ax.plot(
        np.arange(start, fid.size),
        np.abs(fid[start:] * window[start:]),
        label="half-cosine tapered",
    )
    ax.set(xlabel="Acquired complex-point index", ylabel="FID magnitude")
    ax.set_title("End of the 1045 FID: truncation diagnostic")
    ax.grid(True, alpha=0.16)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output / "15_fid_endpoint.png")
    plt.close(fig)


def _report(output, files, metadata_rows, model_rows, relative_rows, args) -> None:
    import numpy as np

    valid = [row for row in relative_rows if row["target_ppm"] != ""]
    targets = np.asarray([float(row["target_ppm"]) for row in valid])
    methyl = np.asarray([float(row["methyl_observed_ppm"]) for row in valid])
    pdf_shift = np.asarray(
        [float(row["pdf_consensus_shift_ppm"]) for row in relative_rows]
    )
    frequencies = np.asarray(
        [float(row["observe_frequency_mhz"]) for row in relative_rows]
    )
    model_summary = {}
    for model in MODELS:
        rows = [row for row in model_rows if row["reference_model"] == model]
        model_summary[model] = {
            "proposed": float(np.median([row["proposed_shift_ppm"] for row in rows])),
            "agreement": float(
                np.median([row["reference_region_agreement"] for row in rows])
            ),
            "passes": sum(bool(row["reference_qc_pass"]) for row in rows),
        }
    shift_hz = float(np.median(pdf_shift * frequencies))
    text = f"""# Revised June 9 chemical-shift reference audit

## 1. Audit scope and preservation

This audit reprocessed {len(files)} untouched JCAMP-DX FIDs. SHA-256 hashes and
metadata are in `metadata.csv`. The production axis is preserved; diagnostic
models are overlays, not silent mutations.

## 2. What is actually in the June 9 data

One resolved peak family is detected in {len(valid)}/{len(files)} spectra at
{np.median(targets):.5f} ppm (range {np.min(targets):.5f}–{np.max(targets):.5f})
on the metadata-derived axis. The 09:00 spectrum has no QC-passed member.
There is no second family at 6.1 ppm in these raw FIDs.

## 3. Instrument metadata

The files report an NMReady 60 observe frequency near
{float(metadata_rows[0]["observe_frequency_mhz"]):.6f} MHz, a 1250 Hz sweep,
O1P/spectral center 5.0 ppm, 8192 acquired complex points, and 65536 processed
points. The metadata says `Toluene`; `$LOCKOFFSET` is about 2.08 ppm. That is
evidence for the selected instrument profile, not independent proof of the
sample's isotopic composition or an absolute internal standard.

## 4. Reference models tested

Three source-qualified hypotheses were tested in both methyl and aromatic
regions:

- protonated toluene, low-field/neat: 2.09 and 7.00 ppm;
- protonated toluene under a dilute CDCl3 example: 2.34 and about 7.19 ppm;
- residual proton signals associated with toluene-d8: 2.089 and 7.014 ppm.

Expected values are hypotheses tied to measurement conditions, not universal
toluene constants.

## 5. Multi-region fitting and QC

Each methyl and aromatic region is independently restricted-window peak-picked
and fitted with a three-point quadratic maximum. `reference_region_fits.csv`
records observed/expected ppm, shift in ppm and Hz, height, width, SNR,
prominence SNR, fit quality, overlap risk, and failure reason.

## 6. Candidate-model results

- Low-field protonated-toluene median proposed shift:
  {model_summary["protonated_toluene_low_field_neat"]["proposed"]:+.5f} ppm;
  regional disagreement
  {model_summary["protonated_toluene_low_field_neat"]["agreement"]:.5f} ppm.
- Dilute-CDCl3 example median proposed shift:
  {model_summary["protonated_toluene_dilute_cdcl3"]["proposed"]:+.5f} ppm;
  disagreement
  {model_summary["protonated_toluene_dilute_cdcl3"]["agreement"]:.5f} ppm.
- Toluene-d8 residual model median proposed shift:
  {model_summary["toluene_d8_residual"]["proposed"]:+.5f} ppm;
  disagreement
  {model_summary["toluene_d8_residual"]["agreement"]:.5f} ppm.

The low-field protonated-toluene model is the best numerical match to the
metadata axis. The very intense toluene bands are also more consistent with
ordinary protonated toluene as a major component than with trace residual
protons in clean toluene-d8, but intensity alone is not definitive proof.

## 7. Fail-closed decision

All applied model shifts are exactly 0.0 ppm because the repository contains no
independent sample-preparation record confirming protonated toluene versus
toluene-d8, and no verified TMS/internal-standard identity. A candidate can fit
spectrally and still fail physical-identity QC. See `reference_models.csv`.

## 8. PDF image comparison

The supplied raster was read approximately as target {args.pdf_target_ppm:.2f},
methyl {args.pdf_methyl_ppm:.2f}, and aromatic {args.pdf_aromatic_ppm:.2f} ppm,
each with at least ±{args.pdf_reading_uncertainty_ppm:.2f} ppm graphical
uncertainty. Relative to the raw-data metadata axis, the consensus image offset
is {float(np.median(pdf_shift)):+.3f} ppm ({shift_hz:+.2f} Hz). Exact values
cannot be recovered from a screenshot; the original vector PDF or its processed
coordinate table is required.

## 9. Is the PDF peak a different physical peak?

No separate peak is needed to explain it. On the image-read coordinates,
target-minus-methyl is {args.pdf_target_ppm - args.pdf_methyl_ppm:.3f} ppm.
In the raw-data spectra it is {float(np.median(targets - methyl)):.3f} ppm.
Those invariant separations agree within screenshot-reading uncertainty.
The PDF therefore most likely displays the same physical 5.78 ppm family on a
uniformly shifted horizontal axis.

## 10. Why phase, FFT, and apodization cannot explain 5.78 to 6.10

Phasing changes real/imaginary mixing and line shape, and apodization changes
resolution/ringing. Neither legitimately translates every resonance by a
uniform chemical-shift offset. A 5.78-to-6.10 change is a reference/axis
operation (about +0.32 ppm), not peak creation by Fourier processing.

## 11. Legacy-script arithmetic

The legacy formula is `new_axis = old_axis + (1.97 - selected_peak)`. If the
selected peak were 2.40 ppm, the shift would be -0.43 ppm, not +0.32 ppm.
With the observed June 9 methyl maximum near {float(np.median(methyl)):.3f}
ppm, it would apply about {1.97 - float(np.median(methyl)):+.3f} ppm. Therefore
that exact equation cannot explain a rightward +0.32 ppm display shift. The
1.97 ppm constant is labeled DMAc in the legacy code and must not be treated
as a general toluene or acetone-d6 reference.

## 12. Ringing/truncation assessment

The 10:45 trace contains strong oscillatory structure beside the solvent line,
consistent with truncation/ringing. `14_ringing_window_sensitivity.png` and
`15_fid_endpoint.png` compare the actual raw-FID result with a half-cosine
taper. The legacy stale-variable bug does discard its calculated windows, so
it can worsen ringing. The screenshot alone, however, cannot uniquely assign
all oscillations to that one bug; phase, FID endpoint, and instrument digital
filtering can contribute.

## 13. Production recommendation

Keep the metadata-derived axis as the production default. Preserve original
and candidate axes side by side. Only enable a model after documenting sample
solvent/isotopic form or adding a verified internal standard, requiring at
least two agreeing reference regions, and reviewing aromatic-envelope overlap.
Never calibrate from the first index above a global intensity threshold.

## 14. Final scientific conclusion

The June 9 FIDs contain one reproducible physical family near 5.78 ppm on the
current metadata axis. The supplied PDF most likely shows that same family near
6.0–6.1 ppm after a global display/reference shift. The current processing is
internally consistent and independently supported by the observed ~2.08 and
~6.99 ppm toluene regions, but absolute assignment remains explicitly
conditional until sample identity or an internal standard is documented.

### Source-qualified reference context

- Nanalysis NMReady 60/100 manual (manual chemical-shift entry and processed
  PDF export):
  https://www.wpi.edu/sites/default/files/2025-07/Nanalysis-100-60-user-manual.pdf
- Thermo Fisher low-field neat-toluene teaching spectrum (2.09, 7.00 ppm):
  https://assets.thermofisher.com/TFS-Assets/CAD/Reference-Materials/pS45-pS80-Simple-Distillation-of-Cyclohexane-and-Toluene.pdf
- PubChem/NMRShiftDB toluene spectrum under different conditions:
  https://pubchem.ncbi.nlm.nih.gov/compound/toluene
- Residual-solvent reference context:
  https://chem.ch.huji.ac.il/nmr/whatisnmr/chemshift.html
"""
    (output / "REVISED_REFERENCE_AUDIT.md").write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    import numpy as np

    args = _parser().parse_args(argv)
    files = collect_dx_files(args.paths)
    if not files:
        print("ERROR: no DX files found", file=sys.stderr)
        return 1
    if args.pdf_reading_uncertainty_ppm <= 0:
        print("ERROR: PDF reading uncertainty must be positive", file=sys.stderr)
        return 2
    output = create_output_dir(args.output_dir, "reference-audit", args.run_name)
    if args.pdf_image:
        if not args.pdf_image.is_file():
            print(f"ERROR: PDF image not found: {args.pdf_image}", file=sys.stderr)
            return 2
        shutil.copy2(args.pdf_image, output / "supplied_processed_spectra.png")

    metadata_rows: list[dict] = []
    fit_rows: list[dict] = []
    model_rows: list[dict] = []
    relative_rows: list[dict] = []
    spectra = []

    for index, path in enumerate(files):
        spectrum = build_phased_spectrum(
            path,
            line_broadening_hz=args.line_broadening_hz,
            zero_fill_points=args.zero_fill_points,
            phase_method="stored",
            truncation_window="none",
        )
        axis = validate_axis_metadata(path, fft_points=args.zero_fill_points)
        timestamp, timestamp_source = parse_acquisition_timestamp(
            spectrum.metadata, path
        )
        label = timestamp.strftime("%H:%M") if timestamp else str(index)
        picked, target = _target_peak(spectrum)
        metadata_rows.append(
            {
                "file": path.name,
                "raw_sha256": _sha256(path),
                "timestamp": "" if timestamp is None else timestamp.isoformat(),
                "timestamp_source": timestamp_source,
                "observe_frequency_mhz": axis.observe_frequency_mhz,
                "spectral_width_hz": axis.spectral_width_hz,
                "spectral_width_ppm": axis.spectral_width_ppm,
                "spectral_center_ppm": axis.center_ppm,
                "complex_points": axis.complex_points,
                "processed_points": axis.fft_points,
                "frequency_spacing_hz": axis.frequency_spacing_hz,
                "ppm_spacing": axis.ppm_spacing,
                "left_limit_ppm": axis.left_limit_ppm,
                "right_limit_ppm": axis.right_limit_ppm,
                "header_solvent_name": spectrum.metadata.get(".SOLVENT NAME", ""),
                "header_shift_reference": spectrum.metadata.get(
                    ".SHIFT REFERENCE", ""
                ),
                "header_lockoffset": _number(spectrum.metadata, "$LOCKOFFSET"),
                "header_o1p": _number(spectrum.metadata, "$O1P"),
                "header_sf": _number(spectrum.metadata, "$SF"),
                "header_swh": _number(spectrum.metadata, "$SWH"),
                "header_phase0": _number(spectrum.metadata, "$PHC0"),
                "header_phase1": _number(spectrum.metadata, "$PHC1"),
            }
        )
        spectra.append(
            {
                "label": label,
                "spectrum": spectrum,
                "picked": picked,
                "target_ppm": None if target is None else target.interpolated_ppm,
            }
        )
        per_model = {}
        for model in MODELS:
            result = evaluate_reference_model(
                spectrum.ppm_axis,
                spectrum.magnitude,
                reference_model=model,
                observe_frequency_mhz=spectrum.observe_frequency_mhz,
                solvent_identity="unknown",
                solvent_isotopic_form="unknown",
                maximum_reference_disagreement_ppm=(
                    args.maximum_model_disagreement_ppm
                ),
            )
            per_model[model] = result
            model_rows.append(
                {
                    "file": path.name,
                    "timestamp": "" if timestamp is None else timestamp.isoformat(),
                    "reference_model": model,
                    "solvent_identity": result.solvent_identity,
                    "solvent_isotopic_form": result.solvent_isotopic_form,
                    "proposed_shift_ppm": result.proposed_shift_ppm,
                    "proposed_shift_hz": result.proposed_shift_hz,
                    "applied_shift_ppm": result.applied_shift_ppm,
                    "applied_shift_hz": result.applied_shift_hz,
                    "reference_region_count": result.reference_region_count,
                    "reference_region_agreement": (
                        ""
                        if result.reference_region_agreement is None
                        else result.reference_region_agreement
                    ),
                    "reference_confidence": result.reference_confidence,
                    "reference_qc_pass": result.reference_qc_pass,
                    "reference_qc_failure_reasons": "; ".join(
                        result.reference_qc_failure_reasons
                    ),
                }
            )
            for fit in result.region_fits:
                fit_rows.append(
                    {
                        "file": path.name,
                        "timestamp": (
                            "" if timestamp is None else timestamp.isoformat()
                        ),
                        "reference_model": model,
                        **asdict(fit),
                    }
                )

        low_field = per_model["protonated_toluene_low_field_neat"]
        by_region = {fit.region_name: fit for fit in low_field.region_fits}
        methyl_ppm = by_region["methyl"].observed_peak_ppm
        aromatic_ppm = by_region["aromatic"].observed_peak_ppm
        target_ppm = None if target is None else target.interpolated_ppm
        pdf_offsets = [
            args.pdf_methyl_ppm - methyl_ppm,
            args.pdf_aromatic_ppm - aromatic_ppm,
        ]
        if target_ppm is not None:
            pdf_offsets.append(args.pdf_target_ppm - target_ppm)
        pdf_consensus = float(np.median(pdf_offsets))
        uncertainty_hz = (
            2.0
            * args.pdf_reading_uncertainty_ppm
            * spectrum.observe_frequency_mhz
        )
        pdf_separation_hz = (
            args.pdf_target_ppm - args.pdf_methyl_ppm
        ) * spectrum.observe_frequency_mhz
        relative_rows.append(
            {
                "file": path.name,
                "timestamp": "" if timestamp is None else timestamp.isoformat(),
                "observe_frequency_mhz": spectrum.observe_frequency_mhz,
                "target_ppm": "" if target_ppm is None else target_ppm,
                "methyl_observed_ppm": methyl_ppm,
                "aromatic_observed_ppm": aromatic_ppm,
                "target_minus_methyl_ppm": (
                    "" if target_ppm is None else target_ppm - methyl_ppm
                ),
                "target_minus_methyl_hz": (
                    ""
                    if target_ppm is None
                    else (target_ppm - methyl_ppm)
                    * spectrum.observe_frequency_mhz
                ),
                "aromatic_minus_target_ppm": (
                    "" if target_ppm is None else aromatic_ppm - target_ppm
                ),
                "aromatic_minus_target_hz": (
                    ""
                    if target_ppm is None
                    else (aromatic_ppm - target_ppm)
                    * spectrum.observe_frequency_mhz
                ),
                "pdf_target_ppm_read": args.pdf_target_ppm,
                "pdf_methyl_ppm_read": args.pdf_methyl_ppm,
                "pdf_aromatic_ppm_read": args.pdf_aromatic_ppm,
                "pdf_reading_uncertainty_ppm": args.pdf_reading_uncertainty_ppm,
                "pdf_consensus_shift_ppm": pdf_consensus,
                "pdf_consensus_shift_hz": (
                    pdf_consensus * spectrum.observe_frequency_mhz
                ),
                "pdf_target_minus_methyl_hz_low": (
                    pdf_separation_hz - uncertainty_hz
                ),
                "pdf_target_minus_methyl_hz_high": (
                    pdf_separation_hz + uncertainty_hz
                ),
            }
        )
        print(
            f"  {path.name}: target "
            + ("not detected" if target_ppm is None else f"{target_ppm:.5f} ppm")
        )

    _write_csv(output / "metadata.csv", metadata_rows)
    _write_csv(output / "reference_region_fits.csv", fit_rows)
    _write_csv(output / "reference_models.csv", model_rows)
    _write_csv(output / "relative_frequency.csv", relative_rows)
    _write_csv(
        output / "pdf_comparison.csv",
        [
            {
                "source_image": (
                    "" if args.pdf_image is None else str(args.pdf_image)
                ),
                "source_image_sha256": (
                    "" if args.pdf_image is None else _sha256(args.pdf_image)
                ),
                "pdf_target_ppm_read": args.pdf_target_ppm,
                "pdf_methyl_ppm_read": args.pdf_methyl_ppm,
                "pdf_aromatic_ppm_read": args.pdf_aromatic_ppm,
                "reading_uncertainty_ppm": args.pdf_reading_uncertainty_ppm,
                "note": (
                    "Raster coordinates are approximate; use original vector "
                    "PDF or exported coordinate data for exact values."
                ),
            }
        ],
    )
    _diagnostic_plots(output, spectra, model_rows, relative_rows, args)
    _report(output, files, metadata_rows, model_rows, relative_rows, args)
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "raw_files": len(files),
        "detected_target_family_members": sum(
            item["target_ppm"] is not None for item in spectra
        ),
        "production_reference": "metadata (no additional shift)",
        "all_candidate_models_fail_closed": all(
            not row["reference_qc_pass"] for row in model_rows
        ),
        "outputs": sorted(path.name for path in output.iterdir()),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote revised reference audit to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
