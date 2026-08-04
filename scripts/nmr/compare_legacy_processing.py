"""Compare current, exact-legacy, intended-legacy, and hybrid NMR processing."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from chemyx_lab.analysis.nmr import (
    build_phased_spectrum,
    integrate_above_local_baseline,
    pick_spectrum_region,
)
from chemyx_lab.analysis.nmr_legacy import process_legacy
from chemyx_lab.analysis.nmr_legacy import legacy_local_integral_sum
from chemyx_lab.analysis.plot_titles import dataset_plot_title

from _common import collect_dx_files, create_output_dir


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results") / "analysis" / "nmr_legacy_comparison",
    )
    parser.add_argument(
        "--run-name",
        default="06-09-26_legacy-comparison",
    )
    return parser


def _sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_csv(path: Path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _metrics(
    *,
    source,
    method,
    ppm,
    magnitude,
    real,
    observe_mhz,
    fft_input,
    window_function,
    line_broadening_hz,
    fft_size,
    phase_method,
    phase0,
    phase1,
    baseline_method,
    reference_method,
    reference_shift_ppm,
    normalization_method,
):
    picked = pick_spectrum_region(
        ppm,
        magnitude,
        quantitative_intensity=real,
        region_min_ppm=5.0,
        region_max_ppm=6.5,
        min_prominence_snr=3.0,
    )
    candidates = [
        peak for peak in picked.peaks if abs(peak.peak_ppm - 5.79) <= 0.20
    ]
    peak = max(candidates, key=lambda item: item.prominence) if candidates else None
    if peak is None:
        return {
            "file": Path(source).name,
            "processing_method": method,
            "fft_input": fft_input,
            "window_function": window_function,
            "line_broadening_hz": line_broadening_hz,
            "fft_size": fft_size,
            "phase_method": phase_method,
            "phase0": phase0,
            "phase1": phase1,
            "baseline_method": baseline_method,
            "reference_method": reference_method,
            "reference_shift_ppm": reference_shift_ppm,
            "normalization_method": normalization_method,
            "peak_position_ppm": "",
            "peak_height": "",
            "peak_area": "",
            "peak_prominence": "",
            "peak_snr": "",
            "peak_width_ppm": "",
            "peak_width_hz": "",
            "qc_pass": False,
            "qc_failure_reason": "no regional peak within 0.20 ppm of 5.79",
        }
    local = integrate_above_local_baseline(
        ppm,
        real,
        left_ppm=peak.interpolated_ppm - peak.width_ppm,
        right_ppm=peak.interpolated_ppm + peak.width_ppm,
    )
    quantitative_qc = (
        peak.snr >= 3.0 and local.signed_area > 0 and peak.peak_height > 0
    )
    return {
        "file": Path(source).name,
        "processing_method": method,
        "fft_input": fft_input,
        "window_function": window_function,
        "line_broadening_hz": line_broadening_hz,
        "fft_size": fft_size,
        "phase_method": phase_method,
        "phase0": phase0,
        "phase1": phase1,
        "baseline_method": baseline_method,
        "reference_method": reference_method,
        "reference_shift_ppm": reference_shift_ppm,
        "normalization_method": normalization_method,
        "peak_position_ppm": peak.interpolated_ppm,
        "peak_height": peak.peak_height,
        "peak_area": local.signed_area,
        "peak_prominence": peak.prominence,
        "peak_snr": peak.snr,
        "peak_width_ppm": peak.width_ppm,
        "peak_width_hz": peak.width_ppm * observe_mhz,
        "qc_pass": quantitative_qc,
        "qc_failure_reason": (
            ""
            if quantitative_qc
            else "magnitude candidate failed real-spectrum SNR/area QC"
        ),
    }


def _plots(output: Path, representative):
    import numpy as np
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    windows = (
        ("full_spectrum", 10.0, 0.0),
        ("regional_5p0_6p5", 6.5, 5.0),
        ("candidate_5p70_5p90", 5.90, 5.70),
        ("target_6p00_6p20", 6.20, 6.00),
        ("reference_regions", 7.30, 1.70),
    )
    for filename, high, low in windows:
        fig, ax = plt.subplots(figsize=(11, 6), dpi=180)
        for method, ppm, real in representative:
            mask = (ppm >= low) & (ppm <= high)
            visible = np.asarray(real)[mask]
            scale = float(np.max(np.abs(visible))) if visible.size else 1.0
            ax.plot(
                np.asarray(ppm)[mask],
                visible / (scale or 1.0),
                linewidth=0.85,
                label=method,
            )
        ax.set_xlim(high, low)
        ax.set_xlabel("Chemical shift (ppm)")
        ax.set_ylabel("Locally normalized real intensity")
        ax.set_title(
            dataset_plot_title(
                filename.replace("_", " "),
                output_path=output / f"{filename}.png",
            )
        )
        ax.grid(True, alpha=0.18)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(output / f"{filename}.png")
        plt.close(fig)


def _legacy_report(output: Path, rows, references, integration_rows):
    exact = [row for row in rows if row["processing_method"] == "B_exact_legacy"]
    intended = [
        row for row in rows if row["processing_method"] == "C_intended_legacy"
    ]
    shifts = [row["applied_reference_shift_ppm"] for row in references if row["legacy_variant"] == "python_DMAC_1p97"]
    area_groups = {}
    for row in integration_rows:
        if row["qc_pass"]:
            area_groups.setdefault(row["file"], []).append(
                float(row["area_trapezoid_ppm"])
            )
    area_variations = [
        100.0 * (max(values) - min(values)) / (sum(values) / len(values))
        for values in area_groups.values()
    ]
    report = f"""# Legacy NMReady processing audit

## Executive result

The legacy workflow did **not** discover a resolved 6.1 ppm peak. The saved
notebook contains a commented fixed-window list with 6.1 in `xpk`, calls 6.1
the reference in prose, and actively sets `int_ref_ppm = 6.1`. The active
`xpk` list saved later is `[6.68, 4.88, 4.21]`, so the final normalization cell
would fail at `xpk.index(6.1)` in a clean run. In an interactive session where
an earlier 6.1-containing list remained in memory, it would integrate and
normalize a predetermined window. Neither case is independent peak detection.

Running the exact Python legacy reference algorithm on the June 9 files applies
shifts from {min(shifts):.6f} to {max(shifts):.6f} ppm. These shifts move the
5.79 ppm feature toward lower ppm, not toward 6.1 ppm. The notebook's DMF
1.33 ppm assumption produces an even larger shift toward lower ppm.

## Complete legacy workflow

1. `Tk.askdirectory()` requests a folder. `os.listdir()` is not sorted; files
   containing a hard-coded substring are retained, and `file_list[0]` is
   treated as the first spectrum.
2. `nmrglue.jcampdx.read()` decodes the JCAMP pages.
3. `data[0] + 1j*data[1]` correctly constructs the complex FID.
4. A nonstandard `offset` key is added to `guess_udic()`:
   `.SOLVENTREFERENCE` becomes `offset`, while `$SW * obs` becomes sweep width
   in Hz. nmrglue's returned dictionary does not originally contain `offset`;
   its standard carrier field is `car` in Hz.
5. `offset` is used as the spectral **center**, not a Bruker high-ppm offset.
   For these files `.SOLVENTREFERENCE=5.0` happens to equal `$O1P=5.0`, but the
   field name does not guarantee that semantic equivalence.
6. `linspace(center-SW/2, center+SW/2, N)` returns an increasing low-to-high
   ppm array. `invert_xaxis()` later displays high ppm at the left. There is no
   double reversal for nmrglue's shifted FFT, though inclusive endpoints differ
   slightly from an FFT-bin grid.
7. The half-cosine window and 2 Hz exponential broadening are calculated.
8. **Confirmed stale-variable bug:** `fft(data)` transforms the original FID,
   so both preceding windows are discarded. Intended behavior is `fft(tdata)`.
9. `peak_minima` automatic phase is applied.
10. ABD splits data into 128 sections, uses the absolute minimum peak-to-peak
    span as noise, and accepts locally flat points. It receives complex data;
    assignment into real arrays discards the imaginary component.
11. A first-order polynomial is fitted. Exact zero-valued baseline points are
    discarded, and too few points can make `polyfit` fail.
12. The complex spectrum is divided by its maximum real value. Zero or negative
    maxima are not guarded in the legacy source.
13. nmrglue picks all signals above 0.9 after normalization. `xpeak[0]` is the
    first returned X-axis record, not a scientifically validated solvent peak.
14. That point is forcibly assigned to DMAc 1.97 ppm in the Python script or
    to the notebook's alleged DMF 1.33 ppm. The whole axis is translated.
15. Variable-width integration joins endpoint intensities with a line but uses
    `sum(y-baseline)`, a point sum whose magnitude changes with digital size.
16. The notebook plots reversed NMR axes and stores tables with deprecated
    `DataFrame.append`; export is mostly commented out.

## Confirmed implementation bugs

- Windowing and exponential broadening are discarded by `fft(data)`.
- Complex ABD values are silently cast to real while warnings are globally
  suppressed.
- ABD discards legitimate zero-valued baseline points.
- Polynomial fitting and reference indexing do not handle empty results.
- Peak-selection order is assumed to imply solvent identity.
- File discovery is nondeterministic and unsorted.
- Point-sum integration is not an integral with respect to ppm or Hz.
- Local-baseline construction divides endpoint difference by N rather than
  N-1, so it does not land exactly on the second foot.
- The code can normalize by zero or a nonrepresentative maximum.

## Scientifically questionable assumptions

- `.SOLVENTREFERENCE` is treated as a spectral center.
- DMAc/DMF reference values are applied to files whose metadata says toluene.
- A global 90%-height threshold is assumed to identify one solvent resonance.
- The first returned peak is assumed stable across mixtures and time.
- A fixed 6.1 ppm integral is called a reference without resolved-peak QC.
- Maximum normalization is used before quantitative integration.

## Method comparison

`processing_comparison.csv` contains {len(rows)} rows. Method A is the current
metadata/stored-phase workflow. Method B exactly reproduces the stale FFT bug.
Method C transforms the intended windowed/apodized FID. Method D uses the
half-cosine and automatic phase as diagnostics but retains metadata referencing
and real-spectrum quantitative integration.

Exact legacy rows passing regional candidate QC: {sum(bool(row['qc_pass']) for row in exact)}/{len(exact)}.
Intended legacy rows passing regional candidate QC: {sum(bool(row['qc_pass']) for row in intended)}/{len(intended)}.

With one fixed pair of integration feet per file, real-data trapezoidal areas
vary by at most {max(area_variations):.2f}% from 8192 to 65536 points. The
remaining variation reflects coarse sampling of broad/noisy peak feet; it is
reported rather than normalized away. Point sums grow roughly with the number
of digital points and are therefore not comparable across zero filling.

## Why 6.1 appeared

The notebook contains the commented template `xpk = [..., 6.1, ...]` with a
fixed half-width of 0.12 ppm, states in prose that 6.1 is the reference, and
actively chooses `int_ref_ppm = 6.1`. Its next step is intended to rescale all
integrals so that this fixed-window integral equals 3.0. In the saved execution
order that cell raises `ValueError`, because the active `xpk` list omits 6.1.
Thus 6.1 is a predetermined integration/normalization coordinate or a stale
notebook-state error--not the output of peak picking.

## Final scientific conclusion

- **Did legacy detect a real 6.1 peak?** No.
- **Did it shift the 5.79 peak toward 6.1?** No for these files; exact measured
  DMAc and DMF shifts move it toward lower ppm.
- **Was 6.1 predetermined?** Yes, as an integration and normalization target.
- **Changes affecting chemical shift:** axis metadata interpretation and manual
  reference translation. FFT sign/channel order can mirror the entire axis.
- **Changes affecting line shape/intensity:** windowing, line broadening,
  phase, baseline, normalization, and point-sum versus trapezoidal integration.
- **Keep:** optional autophase diagnostics, explicit baseline alternatives,
  local-feet integration, and explicitly validated solvent/reference checks.
- **Reject:** first-threshold-peak referencing, solvent hard-coding, global
  warning suppression, magnitude/complex quantitative integration, and
  point-sum areas.
"""
    (output / "LEGACY_AUDIT.md").write_text(report, encoding="utf-8")


def _source_audit(output: Path):
    audit = """# Line-by-line scientific audit of the legacy sources

Python line numbers refer to `nmr_template/dx_process.py`; notebook locations
refer to saved cell and source-line numbers.

| Source | Intent and actual behavior | Scientific effect | Relevance to 5.79 versus 6.1 |
|---|---|---|---|
| Python 28-36 | Opens a Tk directory chooser and returns an unchecked path. | No spectral effect; unsuitable for reproducible headless batches. | None. |
| Python 38-46 | nmrglue reads two pages; `real + 1j*imag` correctly forms the complex FID. | Channel order controls transform sign. | This code does not swap the channels. |
| Python 49-54 | Overwrites `udic`: `.SOLVENTREFERENCE` is treated as center; `$SW*obs` becomes Hz. | Valid here only because `.SOLVENTREFERENCE`, `$O1P`, and `$SPECTRALCENTER` are all 5.0. | A semantic mismatch could translate ppm, but none exists in these files. |
| Python 60-86 | ABD uses the minimum section span and accepts locally flat points; complex values are cast into real arrays and exact zeros are dropped. | Can absorb broad peaks/tails and hide casting warnings. | Can change an apparent shoulder, not globally translate ppm. |
| Python 89-99 | Fits a polynomial at zero-based coordinates but evaluates it at 1..N. | One-point baseline intercept error; too few points can fail. | Changes baseline, height, area, and prominence only. |
| Python 110-117 | Creates an inclusive, increasing low-to-high ppm array. | `invert_xaxis()` later gives conventional high-ppm-left display. | No hidden double reversal. |
| Python 119-126 | Unused helper shifts the minimum-ppm peak above 80% to a supplied value. | Unvalidated translation. | Could shift every feature, but `proc_dx` does not call it. |
| Python 139-155 | Uses unsorted `os.listdir()` and substring `pm`. | Time order is nondeterministic. | Can scramble kinetics, not peak position. |
| Python 160-173 | Takes unsorted first file, sets 2 Hz LB and solvent DMAc. | Hard-coded assumptions. | DMAc conflicts with June 9 toluene metadata. |
| Python 181-193 | Computes half-cosine taper and exponential broadening in `tdata`. | Intended to reduce truncation at the cost of broader lines. | Affects line shape/SNR only. |
| Python 195-196 | Executes `fft(data)`, discarding the processed `tdata`. | Confirmed stale-variable bug. | Changes height, width, SNR, area, and resolution, not a uniform ppm shift. |
| Python 198-199 | Applies `peak_minima` automatic p0/p1 phase. | May be unstable for mixtures and solvent tails. | Can create a weak local maximum but does not translate the axis. |
| Python 201-204 | Passes complex spectrum to real ABD, then subtracts a linear fit. | Imaginary values are discarded during baseline selection. | Can alter shoulders and baseline curvature. |
| Python 206-212 | Divides by maximum real and peak-picks at absolute 0.9. | Candidate identity depends on phase and dominant peak; empty/multiple results are not handled. | Can select the wrong reference peak. |
| Python 214-220 | Forces first returned peak to DMAc 1.97 and translates the whole axis. | Scientifically unvalidated reference correction. | June 9 shift is -0.108 to -0.110 ppm, moving 5.79 toward 5.67, not 6.1. |
| Notebook cell 7:15,57-61 | Labels solvent DMF and forces first peak to 1.33 ppm. | 1.33 is not a standard DMF methyl/formyl reference; metadata say toluene. | Applies about -0.75 ppm, strongly away from 6.1. |
| Notebook cell 13:15 | Contains a fully commented `xpk=[...,6.1,...]` and 0.12 ppm half-width. | Shows an expected fixed window, not detection. | Direct evidence that 6.1 was predetermined. |
| Notebook cell 15:33 | Active `xpk` is `[6.68,4.88,4.21]`. | It omits 6.1. | The later 6.1 lookup cannot succeed in clean saved order. |
| Notebook cell 20:1-24 | Joins endpoint intensities but divides slope by N and uses `sum(y-baseline)`. | Not a true ppm/Hz integral and changes with digital size. | Can report a tail integral without a resolved peak. |
| Notebook cell 22 | Prose assumes 6.1 is a stable reference. | Unsupported chemical assumption. | Predetermined interpretation. |
| Notebook cell 23:9-30 | Sets `int_ref_ppm=6.1`, looks it up in `xpk`, and intends to scale that integral to 3.0. | Raises `ValueError` with the saved active list; stale notebook memory could hide it. | 6.1 is a normalization target or stale-state bug, not peak-picking output. |

## Axis semantics

`guess_udic()` does not return an `offset` key for these JCAMP files; nmrglue's
standard carrier entry is `car` in Hz. The legacy code adds `offset`, and only
its own `get_xax()` interprets that value as a center in ppm. It therefore does
not follow nmrglue's normal unit-conversion convention. It must also not be
confused with Bruker `$OFFSET`, which denotes a spectral edge. The custom
interpretation is numerically consistent for June 9 only because the
center-like header fields all equal 5.0 ppm. The returned axis increases
numerically; plot inversion changes display direction only.
"""
    (output / "LEGACY_SOURCE_AUDIT.md").write_text(audit, encoding="utf-8")


def main(argv=None):
    import numpy as np

    args = _parser().parse_args(argv)
    files = collect_dx_files(args.paths)
    if not files:
        print("ERROR: no .dx files found", file=sys.stderr)
        return 1
    output = create_output_dir(args.output_dir, "legacy", run_name=args.run_name)
    rows, reference_rows, integration_rows = [], [], []
    representative = []

    for file_index, path in enumerate(files):
        current = build_phased_spectrum(path)
        current_row = _metrics(
            source=path,
            method="A_current_validated",
            ppm=current.ppm_axis,
            magnitude=current.magnitude,
            real=current.real,
            observe_mhz=current.observe_frequency_mhz,
            fft_input="stored FID after exponential apodization",
            window_function="exponential",
            line_broadening_hz=current.line_broadening_hz,
            fft_size=current.processed_points,
            phase_method="stored NMReady inverse",
            phase0=current.phase0_deg,
            phase1=current.phase1_deg,
            baseline_method="iterative regional polynomial",
            reference_method="metadata",
            reference_shift_ppm=0.0,
            normalization_method="none",
        )
        current_row["raw_sha256"] = _sha256(path)
        rows.append(current_row)

        exact = process_legacy(path, assumed_reference_ppm=1.97, intended_fft=False)
        intended = process_legacy(path, assumed_reference_ppm=1.97, intended_fft=True)
        exact_pre_row = _metrics(
            source=path,
            method="legacy_pre_reference_diagnostic",
            ppm=exact.original_ppm,
            magnitude=np.abs(exact.spectrum),
            real=exact.spectrum.real,
            observe_mhz=current.observe_frequency_mhz,
            fft_input=exact.fft_input,
            window_function=exact.window_function,
            line_broadening_hz=exact.line_broadening_hz,
            fft_size=exact.fft_size,
            phase_method=exact.phase_method,
            phase0=exact.phase0_deg,
            phase1=exact.phase1_deg,
            baseline_method=exact.baseline_method,
            reference_method="none (pre-reference diagnostic)",
            reference_shift_ppm=0.0,
            normalization_method=exact.normalization_method,
        )
        for method, result in (
            ("B_exact_legacy", exact),
            ("C_intended_legacy", intended),
        ):
            row = _metrics(
                source=path,
                method=method,
                ppm=result.referenced_ppm,
                magnitude=np.abs(result.spectrum),
                real=result.spectrum.real,
                observe_mhz=current.observe_frequency_mhz,
                fft_input=result.fft_input,
                window_function=result.window_function,
                line_broadening_hz=result.line_broadening_hz,
                fft_size=result.fft_size,
                phase_method=result.phase_method,
                phase0=result.phase0_deg,
                phase1=result.phase1_deg,
                baseline_method=result.baseline_method,
                reference_method="first >0.9 peak forced to DMAc 1.97 ppm",
                reference_shift_ppm=result.applied_reference_shift_ppm,
                normalization_method=result.normalization_method,
            )
            row["raw_sha256"] = _sha256(path)
            rows.append(row)

        hybrid = build_phased_spectrum(
            path,
            zero_fill_points=8192,
            truncation_window="half-cosine",
            phase_method="peak_minima",
        )
        hybrid_row = _metrics(
            source=path,
            method="D_hybrid_validated",
            ppm=hybrid.ppm_axis,
            magnitude=hybrid.magnitude,
            real=hybrid.real,
            observe_mhz=hybrid.observe_frequency_mhz,
            fft_input="half-cosine + stored exponential FID",
            window_function="half-cosine and exponential",
            line_broadening_hz=hybrid.line_broadening_hz,
            fft_size=hybrid.processed_points,
            phase_method="nmrglue peak_minima diagnostic",
            phase0=hybrid.phase0_deg,
            phase1=hybrid.phase1_deg,
            baseline_method="iterative regional polynomial",
            reference_method="metadata",
            reference_shift_ppm=0.0,
            normalization_method="none",
        )
        hybrid_row["raw_sha256"] = _sha256(path)
        rows.append(hybrid_row)

        for variant, assumed_reference in (
            ("python_DMAC_1p97", 1.97),
            ("notebook_DMF_1p33", 1.33),
        ):
            candidate = exact.raw_reference_candidate_ppm
            shift = 0.0 if candidate is None else assumed_reference - candidate
            reference_rows.append(
                {
                    "file": path.name,
                    "legacy_variant": variant,
                    "raw_reference_candidate_ppm": candidate,
                    "assumed_reference_ppm": assumed_reference,
                    "applied_reference_shift_ppm": shift,
                    "applied_reference_shift_hz": (
                        shift * current.observe_frequency_mhz
                    ),
                    "selected_peak_index": exact.selected_peak_index,
                    "selected_peak_height": exact.selected_peak_height,
                    "reference_selection_reason": exact.reference_selection_reason,
                    "legacy_algorithm_qc_pass": exact.reference_qc_pass,
                    "reference_qc_pass": False,
                    "reference_qc_failure_reason": (
                        exact.reference_qc_failure_reason
                        + (
                            "; metadata solvent is toluene, not DMF/DMAc"
                            if exact.reference_qc_failure_reason
                            else "metadata solvent is toluene, not DMF/DMAc"
                        )
                    ).strip("; "),
                    "feature_pre_reference_ppm": (
                        exact_pre_row["peak_position_ppm"]
                    ),
                    "feature_post_reference_ppm": (
                        ""
                        if exact_pre_row["peak_position_ppm"] == ""
                        else float(exact_pre_row["peak_position_ppm"]) + shift
                    ),
                    "current_metadata_peak_ppm": current_row["peak_position_ppm"],
                }
            )

        zero_fill_sizes = (8192, 16384, 32768, 65536)
        zero_filled_spectra = {
            points: build_phased_spectrum(path, zero_fill_points=points)
            for points in zero_fill_sizes
        }
        integration_reference = _metrics(
            source=path,
            method="zero_fill_integration_reference",
            ppm=zero_filled_spectra[65536].ppm_axis,
            magnitude=zero_filled_spectra[65536].magnitude,
            real=zero_filled_spectra[65536].real,
            observe_mhz=zero_filled_spectra[65536].observe_frequency_mhz,
            fft_input="exponentially apodized FID",
            window_function="exponential",
            line_broadening_hz=zero_filled_spectra[65536].line_broadening_hz,
            fft_size=65536,
            phase_method="stored NMReady inverse",
            phase0=zero_filled_spectra[65536].phase0_deg,
            phase1=zero_filled_spectra[65536].phase1_deg,
            baseline_method="local feet",
            reference_method="metadata",
            reference_shift_ppm=0.0,
            normalization_method="none",
        )
        reference_center = integration_reference["peak_position_ppm"]
        reference_half_width = (
            ""
            if reference_center == ""
            else max(float(integration_reference["peak_width_ppm"]), 0.015)
        )
        for fft_points, zero_filled in zero_filled_spectra.items():
            candidate = _metrics(
                source=path,
                method=f"zero_fill_{fft_points}",
                ppm=zero_filled.ppm_axis,
                magnitude=zero_filled.magnitude,
                real=zero_filled.real,
                observe_mhz=zero_filled.observe_frequency_mhz,
                fft_input="exponentially apodized FID",
                window_function="exponential",
                line_broadening_hz=zero_filled.line_broadening_hz,
                fft_size=fft_points,
                phase_method="stored NMReady inverse",
                phase0=zero_filled.phase0_deg,
                phase1=zero_filled.phase1_deg,
                baseline_method="local feet",
                reference_method="metadata",
                reference_shift_ppm=0.0,
                normalization_method="none",
            )
            if candidate["peak_position_ppm"] == "" or reference_center == "":
                integration_rows.append(
                    {
                        "file": path.name,
                        "fft_size": fft_points,
                        "peak_position_ppm": "",
                        "left_ppm": "",
                        "right_ppm": "",
                        "area_sum_points": "",
                        "area_trapezoid_ppm": "",
                        "area_trapezoid_hz": "",
                        "qc_pass": False,
                    }
                )
                continue
            center = float(candidate["peak_position_ppm"])
            left_ppm = float(reference_center) - float(reference_half_width)
            right_ppm = float(reference_center) + float(reference_half_width)
            integral = legacy_local_integral_sum(
                zero_filled.ppm_axis,
                zero_filled.real,
                left_ppm,
                right_ppm,
                observe_frequency_mhz=zero_filled.observe_frequency_mhz,
            )
            integration_rows.append(
                {
                    "file": path.name,
                    "fft_size": fft_points,
                    "peak_position_ppm": center,
                    "left_ppm": left_ppm,
                    "right_ppm": right_ppm,
                    **integral,
                    "qc_pass": True,
                }
            )

        if file_index == min(3, len(files) - 1):
            representative = [
                ("A current", current.ppm_axis, current.real),
                ("B exact legacy", exact.referenced_ppm, exact.spectrum.real),
                (
                    "C intended legacy",
                    intended.referenced_ppm,
                    intended.spectrum.real,
                ),
                ("D hybrid", hybrid.ppm_axis, hybrid.real),
            ]

    _write_csv(output / "processing_comparison.csv", rows)
    _write_csv(output / "legacy_reference_shifts.csv", reference_rows)
    _write_csv(output / "integration_zero_fill_comparison.csv", integration_rows)
    _plots(output, representative)
    _legacy_report(output, rows, reference_rows, integration_rows)
    _source_audit(output)
    (output / "summary.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "mode": "legacy_reproduction_only",
                "recommendation": "not_recommended_for_quantitative_analysis",
                "files": [str(path) for path in files],
                "methods": [
                    "A_current_validated",
                    "B_exact_legacy",
                    "C_intended_legacy",
                    "D_hybrid_validated",
                ],
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
