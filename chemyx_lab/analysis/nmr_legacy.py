"""Isolated reproduction of the supplied legacy NMReady processing notebook.

This module exists for historical comparison only. It intentionally preserves
questionable behavior such as FFT of the unprocessed FID, complex-to-real
baseline casting, maximum normalization, and first-threshold-peak referencing.
Production processing must use :mod:`chemyx_lab.analysis.nmr`.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .nmr import NmrProcessingError


@dataclass(frozen=True)
class LegacyProcessingResult:
    """Exact legacy arrays, reference decision, and processing provenance."""

    source: Path
    original_ppm: np.ndarray
    referenced_ppm: np.ndarray
    spectrum: np.ndarray
    fft_input: str
    window_function: str
    line_broadening_hz: float
    fft_size: int
    phase_method: str
    phase0_deg: float
    phase1_deg: float
    baseline_method: str
    normalization_method: str
    raw_reference_candidate_ppm: float | None
    assumed_reference_ppm: float
    applied_reference_shift_ppm: float
    applied_reference_shift_hz: float
    selected_peak_index: int | None
    selected_peak_height: float | None
    reference_selection_reason: str
    reference_qc_pass: bool
    reference_qc_failure_reason: str
    baseline_point_count: int
    processing_warnings: tuple[str, ...] = ()


def _nmrglue():
    try:
        import nmrglue
    except ImportError as exc:
        raise NmrProcessingError("Legacy reproduction requires nmrglue") from exc
    return nmrglue


def legacy_axis(metadata, pages) -> np.ndarray:
    """Reproduce ``get_xax`` and the legacy universal-dictionary edits."""
    ng = _nmrglue()
    universal = ng.fileio.jcampdx.guess_udic(metadata, pages)
    offset = float(metadata[".SOLVENTREFERENCE"][0])
    width_hz = float(metadata["$SW"][0]) * float(universal[0]["obs"])
    universal[0]["sw"] = width_hz
    universal[0]["offset"] = offset
    points = len(pages[0])
    high_ppm = offset + width_hz / (2.0 * universal[0]["obs"])
    low_ppm = offset - width_hz / (2.0 * universal[0]["obs"])
    return np.linspace(low_ppm, high_ppm, points)


def legacy_abd(
    data,
    *,
    sections=128,
    noise_factor=3.0,
    window_points=60,
):
    """Reproduce legacy ABD, including complex-to-real value loss."""
    values = np.asarray(data)
    section_size = int(values.shape[0] / int(sections))
    sigma = np.zeros(int(sections))
    selected_values = np.zeros(values.shape[0])
    complex_warning = getattr(
        getattr(np, "exceptions", np),
        "ComplexWarning",
        getattr(np, "ComplexWarning", Warning),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", complex_warning)
        for section in range(int(sections)):
            local = values[
                section * section_size : (section + 1) * section_size
            ]
            sigma[section] = np.max(local) - np.min(local)
        noise_level = float(np.min(sigma))
        for index in range(
            int(window_points) + 1,
            values.shape[0] - (int(window_points) + 1),
        ):
            local = values[
                int(index - window_points / 2) : int(index + window_points / 2)
            ]
            local_span = np.max(local) - np.min(local)
            if local_span < noise_level * float(noise_factor):
                selected_values[index] = values[index]
    coordinates = np.flatnonzero(selected_values != 0.0)
    return coordinates.astype(float), selected_values[coordinates], noise_level


def legacy_polynomial_baseline(data, coordinates, baseline_values, order=1):
    """Reproduce the legacy off-by-one polynomial coordinate convention."""
    values = np.asarray(data)
    if len(coordinates) <= int(order):
        raise NmrProcessingError("Legacy ABD found too few baseline points")
    points = values.shape[-1]
    evaluation_x = np.linspace(1, points, points)
    coefficients = np.polyfit(coordinates, baseline_values, int(order))
    return values - np.polyval(coefficients, evaluation_x)


def legacy_local_integral_sum(
    ppm_axis,
    spectrum,
    left_ppm,
    right_ppm,
    *,
    observe_frequency_mhz=1.0,
):
    """Reproduce the old point-sum integration and return comparison areas."""
    axis = np.asarray(ppm_axis, dtype=float)
    real = np.asarray(spectrum).real
    index1 = int(np.argmin(np.abs(axis - float(left_ppm))))
    index2 = int(np.argmin(np.abs(axis - float(right_ppm))))
    start, stop = sorted((index1, index2))
    x = axis[start:stop]
    y = real[start:stop]
    if x.size < 3:
        raise NmrProcessingError("Legacy integration window has too few points")
    baseline = np.linspace(y[0], y[-1], y.size)
    corrected = y - baseline
    order = np.argsort(x)
    signed_ppm = float(np.trapezoid(corrected[order], x[order]))
    return {
        "area_sum_points": float(np.sum(corrected)),
        "area_trapezoid_ppm": signed_ppm,
        "area_trapezoid_hz": signed_ppm * float(observe_frequency_mhz),
    }


def legacy_select_reference(
    ppm_axis,
    spectrum,
    *,
    assumed_reference_ppm,
    threshold=0.9,
):
    """Reproduce the legacy first-threshold-record reference selection.

    The returned peak order is whatever ``nmrglue.peakpick.pick`` supplies; it
    is deliberately not re-ranked by height or solvent identity.
    """
    ng = _nmrglue()
    ppm = np.asarray(ppm_axis, dtype=float)
    values = np.asarray(spectrum)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        try:
            peaks = ng.peakpick.pick(values, float(threshold))
        except IndexError:
            # nmrglue 0.11 raises while packing an empty peak table. The
            # historical script would crash here; compatibility mode records
            # the failed selection so batch diagnostics can continue.
            peaks = None
    peak_indices = (
        np.asarray([], dtype=int)
        if peaks is None
        else np.asarray(peaks["X_AXIS"], dtype=int)
    )
    warning_messages = tuple(
        f"{item.category.__name__}: {item.message}" for item in captured
    )
    if not peak_indices.size:
        return {
            "peak_indices": peak_indices,
            "selected_index": None,
            "candidate_ppm": None,
            "selected_height": None,
            "shift_ppm": 0.0,
            "warnings": warning_messages,
        }
    selected_index = int(peak_indices[0])
    candidate_ppm = float(ppm[selected_index])
    return {
        "peak_indices": peak_indices,
        "selected_index": selected_index,
        "candidate_ppm": candidate_ppm,
        "selected_height": float(values.real[selected_index]),
        "shift_ppm": float(assumed_reference_ppm) - candidate_ppm,
        "warnings": warning_messages,
    }


def process_legacy(
    path,
    *,
    assumed_reference_ppm=1.97,
    line_broadening_hz=2.0,
    intended_fft=False,
    maximum_allowed_shift_ppm=0.2,
) -> LegacyProcessingResult:
    """Run exact or intended legacy processing on one JCAMP-DX file.

    ``intended_fft=False`` reproduces the stale-variable bug
    ``fft(data)``. ``True`` applies FFT to the half-cosine and exponentially
    apodized ``tdata`` as the surrounding legacy code evidently intended.
    """
    ng = _nmrglue()
    source = Path(path)
    metadata, pages = ng.fileio.jcampdx.read(str(source))
    if not isinstance(pages, (list, tuple)) or len(pages) < 2:
        raise NmrProcessingError("Legacy input requires real and imaginary pages")
    data = np.asarray(pages[0]) + 1j * np.asarray(pages[1])
    axis = legacy_axis(metadata, pages)
    universal = ng.fileio.jcampdx.guess_udic(metadata, pages)
    width_hz = float(metadata["$SW"][0]) * float(universal[0]["obs"])

    points = data.size
    xpt = np.linspace(1, points, points)
    window = 0.5 + 0.5 * np.cos(np.pi * xpt / points)
    processed_fid = data * window
    processed_fid = ng.proc_base.em(
        processed_fid,
        float(line_broadening_hz) / width_hz,
    )
    fft_input = "windowed_and_exponential_fid" if intended_fft else "original_fid"
    transformed = ng.proc_base.fft(processed_fid if intended_fft else data)
    phased, phases = ng.proc_autophase.autops(
        transformed,
        "peak_minima",
        return_phases=True,
        disp=False,
    )
    coordinates, baseline_values, _ = legacy_abd(phased)
    corrected = legacy_polynomial_baseline(
        phased,
        coordinates,
        baseline_values,
        order=1,
    )
    scale = float(np.max(corrected.real))
    if not np.isfinite(scale) or scale <= 0:
        raise NmrProcessingError("Legacy normalization maximum is not positive")
    normalized = corrected / scale

    selection = legacy_select_reference(
        axis,
        normalized,
        assumed_reference_ppm=assumed_reference_ppm,
    )
    peak_indices = selection["peak_indices"]
    if peak_indices.size:
        selected_index = selection["selected_index"]
        candidate_ppm = selection["candidate_ppm"]
        selected_height = selection["selected_height"]
        shift = selection["shift_ppm"]
        failure_reasons = []
        if abs(shift) > float(maximum_allowed_shift_ppm):
            failure_reasons.append("reference shift exceeds configured maximum")
        if len(peak_indices) > 1:
            failure_reasons.append("multiple peaks exceeded the 0.9 threshold")
        qc_pass = not failure_reasons
        failure = "; ".join(failure_reasons)
        reason = (
            "first X-axis entry returned by nmrglue peak picking after "
            "maximum-real normalization"
        )
    else:
        selected_index = None
        candidate_ppm = None
        selected_height = None
        shift = 0.0
        qc_pass = False
        failure = "no peak exceeded the 0.9 threshold"
        reason = "no reference candidate selected"

    observe_mhz = float(universal[0]["obs"])
    return LegacyProcessingResult(
        source=source,
        original_ppm=axis,
        referenced_ppm=axis + shift,
        spectrum=normalized,
        fft_input=fft_input,
        window_function="0.5 + 0.5*cos(pi*x/n)",
        line_broadening_hz=float(line_broadening_hz),
        fft_size=points,
        phase_method="nmrglue peak_minima",
        phase0_deg=float(phases[0]),
        phase1_deg=float(phases[1]),
        baseline_method="legacy ABD 128/3/60 + linear polynomial",
        normalization_method="divide by maximum real intensity",
        raw_reference_candidate_ppm=candidate_ppm,
        assumed_reference_ppm=float(assumed_reference_ppm),
        applied_reference_shift_ppm=shift,
        applied_reference_shift_hz=shift * observe_mhz,
        selected_peak_index=selected_index,
        selected_peak_height=selected_height,
        reference_selection_reason=reason,
        reference_qc_pass=qc_pass,
        reference_qc_failure_reason=failure,
        baseline_point_count=int(len(coordinates)),
        processing_warnings=selection["warnings"],
    )
