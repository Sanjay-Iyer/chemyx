"""NMR JCAMP-DX parsing, Fourier processing, and peak checks.

The NMReady files used by this project contain complex time-domain data in two
JCAMP NTUPLES pages.  Processing is deliberately explicit and reproducible:

1. apply the JCAMP ``FACTOR`` values to the encoded real/imaginary integers;
2. apply exponential line broadening;
3. zero-fill to the file's Bruker ``$SI`` size (unless explicitly overridden);
4. Fourier transform and build the chemical-shift axis from ``$SWH``, ``$SF``,
   and ``$O1P``; and
5. use the phase-insensitive magnitude spectrum for automated peak checks.

Magnitude mode matches the instrument RPC's signal-spectrum convention and is
stable for unattended monitoring.  Baseline-aware peak detection is available
for either a known target or every resolved peak in a chemical-shift region.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class NmrProcessingError(Exception):
    """Raised when a spectrum cannot be parsed or processed."""


@dataclass(frozen=True)
class FidData:
    path: Path
    metadata: dict[str, str]
    real: list[float]
    imag: list[float]

    @property
    def complex_points(self):
        return [complex(r, i) for r, i in zip(self.real, self.imag)]


@dataclass(frozen=True)
class SpectrumData:
    source: Path
    metadata: dict[str, str]
    ppm_axis: object
    magnitude: object
    processed_points: int = 0
    line_broadening_hz: float = 0.0
    real: object | None = None
    imaginary: object | None = None
    phase0_deg: float | None = None
    phase1_deg: float | None = None
    observe_frequency_mhz: float = 0.0
    spectral_width_hz: float = 0.0
    center_ppm: float = 0.0
    phase_method: str = ""
    truncation_window: str = "none"


@dataclass(frozen=True)
class PeakResult:
    source: Path
    target_ppm: float
    peak_ppm: float
    peak_height: float
    baseline: float
    noise: float
    snr: float
    prominence: float
    prominence_snr: float
    width_ppm: float
    peaks_considered: int
    points_in_window: int
    peak_area: float = 0.0
    raw_peak_height: float = 0.0
    baseline_window_ppm: float = 0.5
    baseline_polynomial_order: int = 2


@dataclass(frozen=True)
class RegionPeak:
    """One resolved peak found by baseline-aware regional peak picking."""

    peak_ppm: float
    peak_height: float
    raw_peak_height: float
    baseline: float
    noise: float
    snr: float
    prominence: float
    prominence_snr: float
    width_ppm: float
    interpolated_ppm: float
    interpolation_quality: float
    signed_area: float
    positive_area: float
    classification: str


@dataclass(frozen=True)
class RegionPeakPickingResult:
    """Peak-picking result plus arrays needed for transparent review plots."""

    source: Path | None
    region_min_ppm: float
    region_max_ppm: float
    noise: float
    ppm_axis: object
    magnitude: object
    baseline: object
    corrected: object
    smoothed: object
    peaks: tuple[RegionPeak, ...]


@dataclass(frozen=True)
class AxisValidation:
    """Independently derived digital and chemical-shift axis parameters."""

    complex_points: int
    dwell_time_s: float
    acquisition_time_s: float
    spectral_width_hz: float
    spectral_width_ppm: float
    observe_frequency_mhz: float
    center_ppm: float
    fft_points: int
    frequency_spacing_hz: float
    ppm_spacing: float
    left_limit_ppm: float
    right_limit_ppm: float
    metadata_acquisition_time_s: float
    dwell_width_relative_error: float


@dataclass(frozen=True)
class PeakFamily:
    """Conservatively linked peak positions across a spectrum series."""

    family_id: str
    observations: int
    first_spectrum_index: int
    last_spectrum_index: int
    median_ppm: float
    ppm_range: float
    median_width_ppm: float
    reproducible: bool


@dataclass(frozen=True)
class LocalIntegral:
    """Variable-width integral above a line joining the peak feet."""

    left_ppm: float
    right_ppm: float
    signed_area: float
    positive_area: float
    left_intensity: float
    right_intensity: float
    points: int


@dataclass(frozen=True)
class SolventAlignment:
    """Explicit, non-destructive chemical-shift reference result."""

    original_ppm: object
    referenced_ppm: object
    solvent: str
    resonance: str
    reference_peak_observed_ppm: float
    reference_peak_expected_ppm: float
    applied_shift_ppm: float
    reference_method: str
    reference_confidence: str
    validation_shift_ppm: float | None = None
    shift_disagreement_ppm: float | None = None
    applied_shift_hz: float | None = None
    reference_qc: bool = True
    reference_qc_failure_reason: str = ""
    reference_peak_snr: float | None = None
    reference_peak_prominence_snr: float | None = None
    reference_peak_width_ppm: float | None = None
    reference_peak_height: float | None = None


@dataclass(frozen=True)
class ReferenceRegionFit:
    """One independently fitted region used to test a reference model."""

    region_name: str
    observed_peak_ppm: float | None
    expected_peak_ppm: float
    required_shift_ppm: float | None
    required_shift_hz: float | None
    peak_height: float | None
    peak_width_hz: float | None
    peak_snr: float | None
    prominence_snr: float | None
    fit_model: str
    fit_quality: float | None
    overlap_risk: str
    reference_confidence: str
    qc_pass: bool
    qc_failure_reason: str


@dataclass(frozen=True)
class ReferenceModelResult:
    """Fail-closed result from a multi-region chemical-shift model."""

    original_ppm: object
    referenced_ppm: object
    reference_model: str
    solvent_identity: str
    solvent_isotopic_form: str
    proposed_shift_ppm: float
    proposed_shift_hz: float
    applied_shift_ppm: float
    applied_shift_hz: float
    reference_region_count: int
    reference_region_agreement: float | None
    reference_confidence: str
    reference_qc_pass: bool
    reference_qc_failure_reasons: tuple[str, ...]
    region_fits: tuple[ReferenceRegionFit, ...]


SOLVENT_REFERENCES_PPM = {
    "toluene": {"methyl": 2.09, "aromatic": 7.00},
    "dmf": {"formyl": 8.03, "methyl_trans": 2.92, "methyl_cis": 2.75},
    "dmac": {"methyl_acetyl": 1.97},
}

# These model values are deliberately source-qualified. They are hypotheses,
# not universal constants: chemical shifts depend on concentration, solvent,
# temperature, and referencing convention.
REFERENCE_MODEL_DEFINITIONS = {
    "protonated_toluene_low_field_neat": {
        "required_identity": ("protonated_toluene", "h8"),
        "source": (
            "Thermo Fisher teaching spectrum, neat toluene at 45/82 MHz: "
            "https://assets.thermofisher.com/TFS-Assets/CAD/"
            "Reference-Materials/pS45-pS80-Simple-Distillation-of-"
            "Cyclohexane-and-Toluene.pdf"
        ),
        "regions": {
            "methyl": {"expected_ppm": 2.09, "search_window_ppm": (1.75, 2.55)},
            "aromatic": {
                "expected_ppm": 7.00,
                "search_window_ppm": (6.60, 7.50),
            },
        },
    },
    "protonated_toluene_dilute_cdcl3": {
        "required_identity": ("protonated_toluene", "h8"),
        "source": (
            "PubChem/NMRShiftDB 90 MHz spectrum in CDCl3: methyl 2.34 ppm, "
            "aromatic multiplet approximately 7.10-7.27 ppm; diagnostic only "
            "for samples actually measured under comparable conditions. "
            "https://pubchem.ncbi.nlm.nih.gov/compound/toluene"
        ),
        "regions": {
            "methyl": {"expected_ppm": 2.34, "search_window_ppm": (1.75, 2.65)},
            "aromatic": {
                "expected_ppm": 7.19,
                "search_window_ppm": (6.60, 7.60),
            },
        },
    },
    "toluene_d8_residual": {
        "required_identity": ("toluene_d8", "d8"),
        "source": (
            "Residual-proton values 2.089, 6.974, 7.014, 7.095 ppm: "
            "https://chem.ch.huji.ac.il/nmr/whatisnmr/chemshift.html; "
            "consistent with Gottlieb et al., JOC 1997, 62, 7512."
        ),
        "regions": {
            "methyl": {"expected_ppm": 2.089, "search_window_ppm": (1.75, 2.40)},
            # At 60 MHz the three residual aromatic positions are not reliably
            # separable in this mixture; 7.014 is the center hypothesis.
            "aromatic_envelope": {
                "expected_ppm": 7.014,
                "search_window_ppm": (6.60, 7.40),
            },
        },
    },
}


def read_jcamp_fid(path) -> FidData:
    """Read and scale an NMReady JCAMP-DX FID's split NTUPLES pages.

    nmrglue returns this file type as ``[real_array, imaginary_array]`` and
    applies the JCAMP ``FACTOR`` values during decoding.
    """
    dx_path = Path(path)
    ng = _nmrglue()
    try:
        raw_metadata, pages = ng.jcampdx.read(str(dx_path))
    except Exception as exc:
        raise NmrProcessingError(f"Could not read JCAMP-DX FID {dx_path}: {exc}") from exc

    if not isinstance(pages, (list, tuple)) or len(pages) < 2:
        raise NmrProcessingError(
            f"Expected split real/imaginary JCAMP pages in {dx_path}"
        )
    np = _numpy()
    real_array = np.asarray(pages[0], dtype=float).reshape(-1)
    imag_array = np.asarray(pages[1], dtype=float).reshape(-1)
    if real_array.size == 0 or imag_array.size == 0:
        raise NmrProcessingError(f"No FID pages found in {dx_path}")

    metadata: dict[str, str] = {}
    with dx_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line.startswith("##"):
                key, value = _split_header(line)
                metadata[key] = value
    for key, value in raw_metadata.items():
        if not str(key).startswith("_"):
            metadata.setdefault(str(key), _metadata_text(value))
    expected = int(
        round(_metadata_float(metadata, "NPOINTS", default=real_array.size))
    )
    if real_array.size != imag_array.size or real_array.size != expected:
        raise NmrProcessingError(
            f"Expected {expected} equal complex points; nmrglue decoded "
            f"{real_array.size} real and {imag_array.size} imaginary"
        )
    return FidData(
        dx_path,
        metadata,
        real_array.tolist(),
        imag_array.tolist(),
    )


def read_jcamp_fid_custom(path) -> FidData:
    """Independently decode this NMReady file's uncompressed split XYDATA.

    Each numeric row begins with a packed row-leading X value followed by four
    encoded signal values. The X value is deliberately discarded. JCAMP
    ``FACTOR`` scaling is applied exactly once after both pages are collected.
    This validator is intentionally narrow and rejects compressed JCAMP tokens
    rather than silently guessing.
    """
    dx_path = Path(path)
    metadata: dict[str, str] = {}
    pages: dict[int, list[float]] = {1: [], 2: []}
    active_page: int | None = None
    in_data = False
    with dx_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("##"):
                key, value = _split_header(line)
                metadata[key] = value
                if key.upper() == "PAGE":
                    try:
                        active_page = int(value.split("=", 1)[-1])
                    except ValueError as exc:
                        raise NmrProcessingError(
                            f"Unsupported JCAMP PAGE value {value!r}"
                        ) from exc
                    in_data = False
                elif key.upper() == "DATA TABLE":
                    in_data = active_page in pages
                elif in_data:
                    in_data = False
                continue
            if not in_data or active_page not in pages:
                continue
            tokens = line.split()
            if len(tokens) < 2:
                continue
            try:
                float(tokens[0])
                row_values = [float(token) for token in tokens[1:]]
            except ValueError as exc:
                raise NmrProcessingError(
                    "Custom validator only supports numeric NMReady XYDATA rows"
                ) from exc
            pages[active_page].extend(row_values)

    factors = [float(value) for value in metadata.get("FACTOR", "").split(",")]
    if len(factors) < 3:
        raise NmrProcessingError("JCAMP FACTOR must include X, real, and imaginary")
    n = min(len(pages[1]), len(pages[2]))
    expected = int(round(_metadata_float(metadata, "NPOINTS", default=n)))
    if n != expected or len(pages[1]) != len(pages[2]):
        raise NmrProcessingError(
            f"Expected {expected} equal complex points; decoded "
            f"{len(pages[1])} real and {len(pages[2])} imaginary"
        )
    return FidData(
        path=dx_path,
        metadata=metadata,
        real=[value * factors[1] for value in pages[1]],
        imag=[value * factors[2] for value in pages[2]],
    )


def validate_jcamp_decoders(path, *, absolute_tolerance=1e-9) -> float:
    """Return maximum custom-versus-nmrglue point error or raise on mismatch."""
    np = _numpy()
    via_nmrglue = read_jcamp_fid(path)
    custom = read_jcamp_fid_custom(path)
    nmrglue_complex = np.asarray(via_nmrglue.complex_points)
    custom_complex = np.asarray(custom.complex_points)
    if nmrglue_complex.shape != custom_complex.shape:
        raise NmrProcessingError("Custom and nmrglue JCAMP shapes differ")
    max_error = float(np.max(np.abs(nmrglue_complex - custom_complex)))
    if max_error > float(absolute_tolerance):
        raise NmrProcessingError(
            f"Custom and nmrglue JCAMP values differ by {max_error:g}"
        )
    return max_error


def build_ppm_axis(
    points,
    *,
    spectral_width_hz,
    observe_frequency_mhz,
    center_ppm,
):
    """Construct the ascending internal ppm grid used by the NMReady data.

    Equation: ``ppm = center_ppm + frequency_offset_hz / observe_frequency_mhz``.
    NMR plots invert this ascending numerical axis at display time.
    """
    np = _numpy()
    points = int(points)
    if points <= 0:
        raise NmrProcessingError("points must be positive")
    if float(spectral_width_hz) <= 0 or float(observe_frequency_mhz) <= 0:
        raise NmrProcessingError("spectral width and observe frequency must be positive")
    offsets = np.fft.fftshift(
        np.fft.fftfreq(points, d=1.0 / float(spectral_width_hz))
    )
    return float(center_ppm) + offsets / float(observe_frequency_mhz)


def exponential_window(points, *, dwell_time_s, line_broadening_hz):
    """Return ``exp(-pi * LB_hz * time_s)`` for complex FID samples."""
    np = _numpy()
    if int(points) <= 0 or float(dwell_time_s) <= 0:
        raise NmrProcessingError("points and dwell_time_s must be positive")
    if float(line_broadening_hz) < 0:
        raise NmrProcessingError("line_broadening_hz must be non-negative")
    time_s = np.arange(int(points), dtype=float) * float(dwell_time_s)
    return np.exp(-np.pi * float(line_broadening_hz) * time_s)


def half_cosine_truncation_window(points):
    """Return the template's one-sided Hanning-like truncation window.

    This is ``0.5 + 0.5*cos(pi*x/n)`` for x=1..n. It is a half-cosine taper,
    not NumPy's symmetric Hann window. It forces the final FID point to zero
    but broadens resonances, so it is an explicit option rather than default.
    """
    np = _numpy()
    if int(points) <= 0:
        raise NmrProcessingError("points must be positive")
    x = np.arange(1, int(points) + 1, dtype=float)
    return 0.5 + 0.5 * np.cos(np.pi * x / int(points))


def autophase_spectrum(
    spectrum,
    *,
    method="peak_minima",
    initial_phase0_deg=0.0,
    initial_phase1_deg=0.0,
    peak_width=100,
):
    """Automatically phase a complex spectrum and return data, p0, and p1."""
    np = _numpy()
    values = np.asarray(spectrum, dtype=np.complex128)
    if values.ndim != 1 or values.size == 0:
        raise NmrProcessingError("spectrum must be a non-empty 1-D array")
    try:
        phased, phases = _nmrglue().proc_autophase.autops(
            values,
            method,
            p0=float(initial_phase0_deg),
            p1=float(initial_phase1_deg),
            peak_width=int(peak_width),
            return_phases=True,
            disp=False,
        )
    except Exception as exc:
        raise NmrProcessingError(f"Automatic phasing failed: {exc}") from exc
    return phased, float(phases[0]), float(phases[1])


def automatic_baseline_points(
    intensity,
    *,
    sections=128,
    noise_factor=3.0,
    window_points=60,
    noise_quantile=0.05,
):
    """Return baseline coordinates using sectionwise peak-to-peak noise.

    The historical template used the absolute minimum section span. The
    default 5th percentile is less vulnerable to one accidentally flat
    section; set ``noise_quantile=0`` to reproduce the template exactly.
    A point is accepted only when its local peak-to-peak span is below
    ``noise_factor * section_noise``.
    """
    np = _numpy()
    values = np.asarray(intensity, dtype=float)
    if values.ndim != 1 or values.size < int(sections):
        raise NmrProcessingError("intensity must contain at least one point per section")
    if int(sections) < 2 or float(noise_factor) <= 0 or int(window_points) < 3:
        raise NmrProcessingError("invalid ABD section, factor, or window parameter")
    if not 0 <= float(noise_quantile) <= 1:
        raise NmrProcessingError("noise_quantile must lie between zero and one")
    section_values = np.array_split(values, int(sections))
    spans = np.asarray([np.ptp(section) for section in section_values])
    section_noise = float(np.quantile(spans, float(noise_quantile)))
    if section_noise <= 0 or not np.isfinite(section_noise):
        positive = spans[spans > 0]
        section_noise = float(np.min(positive)) if positive.size else 1.0
    half_window = max(1, int(window_points) // 2)
    accepted = np.zeros(values.size, dtype=bool)
    for index in range(half_window, values.size - half_window):
        local = values[index - half_window : index + half_window + 1]
        accepted[index] = np.ptp(local) < section_noise * float(noise_factor)
    coordinates = np.flatnonzero(accepted)
    if coordinates.size < 3:
        raise NmrProcessingError("ABD found too few baseline points")
    return coordinates, values[coordinates], section_noise


def subtract_abd_polynomial_baseline(
    intensity,
    *,
    sections=128,
    noise_factor=3.0,
    window_points=60,
    polynomial_order=1,
    noise_quantile=0.05,
):
    """Fit the ABD-selected points and return corrected data and baseline."""
    np = _numpy()
    values = np.asarray(intensity, dtype=float)
    coordinates, baseline_values, noise = automatic_baseline_points(
        values,
        sections=sections,
        noise_factor=noise_factor,
        window_points=window_points,
        noise_quantile=noise_quantile,
    )
    if int(polynomial_order) < 0:
        raise NmrProcessingError("polynomial_order must be non-negative")
    coefficients = np.polyfit(coordinates, baseline_values, int(polynomial_order))
    baseline = np.polyval(coefficients, np.arange(values.size, dtype=float))
    return values - baseline, baseline, coordinates, noise


def integrate_above_local_baseline(
    ppm_axis,
    intensity,
    *,
    left_ppm,
    right_ppm,
) -> LocalIntegral:
    """Integrate a variable-width region above a line joining boundary feet."""
    np = _numpy()
    ppm = np.asarray(ppm_axis, dtype=float)
    values = np.asarray(intensity, dtype=float)
    if ppm.shape != values.shape or ppm.ndim != 1:
        raise NmrProcessingError("ppm_axis and intensity must be matching 1-D arrays")
    lo, hi = sorted((float(left_ppm), float(right_ppm)))
    mask = (ppm >= lo) & (ppm <= hi)
    indices = np.flatnonzero(mask)
    if indices.size < 3:
        raise NmrProcessingError("integration region must contain at least 3 points")
    x = ppm[indices]
    y = values[indices]
    order = np.argsort(x)
    x, y = x[order], y[order]
    left_intensity = float(y[0])
    right_intensity = float(y[-1])
    local_baseline = np.linspace(left_intensity, right_intensity, y.size)
    corrected = y - local_baseline
    return LocalIntegral(
        left_ppm=lo,
        right_ppm=hi,
        signed_area=float(_trapezoid(np, corrected, x)),
        positive_area=float(_trapezoid(np, np.maximum(corrected, 0.0), x)),
        left_intensity=left_intensity,
        right_intensity=right_intensity,
        points=int(x.size),
    )


def align_solvent_axis(
    ppm_axis,
    intensity,
    *,
    solvent,
    resonance,
    threshold_fraction=0.9,
    search_half_width_ppm=0.35,
    validation_resonance=None,
    maximum_reference_disagreement_ppm=0.03,
    minimum_snr=5.0,
    minimum_prominence_snr=5.0,
    minimum_width_ppm=0.002,
    maximum_width_ppm=0.30,
    maximum_shift_ppm=0.5,
    observe_frequency_mhz=None,
) -> SolventAlignment:
    """Return an explicitly shifted axis from a specified solvent resonance.

    Unlike the historical template, this does not assume the first globally
    picked peak is the solvent. It searches only around the named resonance,
    uses parabolic interpolation, and can cross-check a second resonance.
    """
    np = _numpy()
    ppm = np.asarray(ppm_axis, dtype=float)
    values = np.asarray(intensity, dtype=float)
    solvent_key = str(solvent).lower()
    resonance_key = str(resonance).lower()
    try:
        expected = SOLVENT_REFERENCES_PPM[solvent_key][resonance_key]
    except KeyError as exc:
        raise NmrProcessingError(
            f"Unknown solvent/resonance {solvent!r}/{resonance!r}"
        ) from exc

    primary = align_validated_reference(
        ppm,
        values,
        expected_ppm=expected,
        search_window_ppm=2.0 * float(search_half_width_ppm),
        minimum_snr=minimum_snr,
        minimum_prominence_snr=minimum_prominence_snr,
        minimum_width_ppm=minimum_width_ppm,
        maximum_width_ppm=maximum_width_ppm,
        maximum_shift_ppm=maximum_shift_ppm,
        threshold_fraction=threshold_fraction,
        observe_frequency_mhz=observe_frequency_mhz,
    )
    observed = primary.reference_peak_observed_ppm
    shift = primary.applied_shift_ppm
    validation_shift = disagreement = None
    confidence = "medium"
    if validation_resonance is not None:
        validation_key = str(validation_resonance).lower()
        try:
            validation_expected = SOLVENT_REFERENCES_PPM[solvent_key][validation_key]
        except KeyError as exc:
            raise NmrProcessingError(
                f"Unknown validation resonance {validation_resonance!r}"
            ) from exc
        validation = align_validated_reference(
            ppm,
            values,
            expected_ppm=validation_expected,
            search_window_ppm=2.0 * float(search_half_width_ppm),
            minimum_snr=minimum_snr,
            minimum_prominence_snr=minimum_prominence_snr,
            minimum_width_ppm=minimum_width_ppm,
            maximum_width_ppm=maximum_width_ppm,
            maximum_shift_ppm=maximum_shift_ppm,
            threshold_fraction=threshold_fraction,
            observe_frequency_mhz=observe_frequency_mhz,
        )
        validation_shift = validation.applied_shift_ppm
        disagreement = abs(shift - validation_shift)
        if disagreement > float(maximum_reference_disagreement_ppm):
            raise NmrProcessingError(
                "reference_qc failed: primary and validation resonances "
                f"disagree by {disagreement:.6f} ppm"
            )
        confidence = "high"
    return SolventAlignment(
        original_ppm=ppm.copy(),
        referenced_ppm=ppm + shift,
        solvent=solvent_key,
        resonance=resonance_key,
        reference_peak_observed_ppm=observed,
        reference_peak_expected_ppm=float(expected),
        applied_shift_ppm=shift,
        reference_method="windowed tallest peak with parabolic interpolation",
        reference_confidence=confidence,
        validation_shift_ppm=validation_shift,
        shift_disagreement_ppm=disagreement,
        applied_shift_hz=primary.applied_shift_hz,
        reference_qc=primary.reference_qc,
        reference_qc_failure_reason=primary.reference_qc_failure_reason,
        reference_peak_snr=primary.reference_peak_snr,
        reference_peak_prominence_snr=primary.reference_peak_prominence_snr,
        reference_peak_width_ppm=primary.reference_peak_width_ppm,
        reference_peak_height=primary.reference_peak_height,
    )


def align_validated_reference(
    ppm_axis,
    intensity,
    *,
    expected_ppm,
    search_window_ppm,
    minimum_snr,
    minimum_prominence_snr,
    minimum_width_ppm,
    maximum_width_ppm,
    maximum_shift_ppm,
    threshold_fraction=0.0,
    observe_frequency_mhz=None,
) -> SolventAlignment:
    """Shift an axis only after a restricted reference peak passes QC.

    The operation is intentionally fail-closed. A candidate must be a local
    maximum inside the user-declared window and pass height SNR, prominence
    SNR, linewidth, and maximum-shift checks. No global first-peak heuristic is
    used. Failure raises :class:`NmrProcessingError`, leaving callers free to
    retain the metadata axis and record the rejected correction.
    """
    np = _numpy()
    signal = _scipy_signal()
    ppm = np.asarray(ppm_axis, dtype=float)
    values = np.asarray(intensity, dtype=float)
    if ppm.shape != values.shape or ppm.ndim != 1:
        raise NmrProcessingError("ppm_axis and intensity must be matching 1-D arrays")
    numeric = (
        float(search_window_ppm),
        float(minimum_snr),
        float(minimum_prominence_snr),
        float(minimum_width_ppm),
        float(maximum_width_ppm),
        float(maximum_shift_ppm),
    )
    if numeric[0] <= 0 or numeric[1] < 0 or numeric[2] < 0:
        raise NmrProcessingError("invalid validated-reference window or SNR limit")
    if numeric[3] <= 0 or numeric[4] < numeric[3] or numeric[5] < 0:
        raise NmrProcessingError("invalid validated-reference width or shift limit")

    half_width = 0.5 * float(search_window_ppm)
    mask = np.abs(ppm - float(expected_ppm)) <= half_width
    indices = np.flatnonzero(mask & np.isfinite(values))
    if indices.size < 7:
        raise NmrProcessingError("validated reference search window is outside axis")
    local = values[indices]
    local_ppm = ppm[indices]
    ppm_step = float(np.median(np.abs(np.diff(local_ppm))))
    difference = np.diff(local)
    noise = float(1.4826 * np.median(np.abs(difference - np.median(difference))))
    noise /= np.sqrt(2.0)
    if not np.isfinite(noise) or noise <= 0:
        noise = float(np.std(difference) / np.sqrt(2.0)) or 1.0
    baseline = float(np.quantile(local, 0.10))
    prominence_floor = float(minimum_prominence_snr) * noise
    height_floor = max(
        baseline + float(minimum_snr) * noise,
        float(np.max(local)) * float(threshold_fraction),
    )
    peaks, properties = signal.find_peaks(
        local,
        height=height_floor,
        prominence=prominence_floor,
    )
    if not peaks.size:
        raise NmrProcessingError(
            "reference_qc failed: no restricted-window peak met SNR and "
            "prominence requirements"
        )
    widths = signal.peak_widths(local, peaks, rel_height=0.5)[0] * ppm_step
    valid_ranks = [
        rank
        for rank, width in enumerate(widths)
        if float(minimum_width_ppm) <= float(width) <= float(maximum_width_ppm)
    ]
    if not valid_ranks:
        raise NmrProcessingError(
            "reference_qc failed: candidate linewidth is outside allowed range"
        )
    best_rank = max(
        valid_ranks,
        key=lambda rank: float(properties["prominences"][rank]),
    )
    local_index = int(peaks[best_rank])
    observed = float(local_ppm[local_index])
    if 0 < local_index < local.size - 1:
        fit_x = local_ppm[local_index - 1 : local_index + 2]
        fit_y = local[local_index - 1 : local_index + 2]
        coefficients = np.polyfit(fit_x, fit_y, 2)
        if coefficients[0] < 0:
            vertex = float(-coefficients[1] / (2.0 * coefficients[0]))
            if min(fit_x) <= vertex <= max(fit_x):
                observed = vertex
    shift = float(expected_ppm) - observed
    if abs(shift) > float(maximum_shift_ppm):
        raise NmrProcessingError(
            "reference_qc failed: required shift "
            f"{shift:.6f} ppm exceeds {float(maximum_shift_ppm):.6f} ppm"
        )
    peak_height = float(local[local_index] - baseline)
    prominence = float(properties["prominences"][best_rank])
    frequency = (
        None if observe_frequency_mhz is None else float(observe_frequency_mhz)
    )
    return SolventAlignment(
        original_ppm=ppm.copy(),
        referenced_ppm=ppm + shift,
        solvent="user_validated",
        resonance="restricted_window_peak",
        reference_peak_observed_ppm=observed,
        reference_peak_expected_ppm=float(expected_ppm),
        applied_shift_ppm=shift,
        reference_method="validated restricted-window peak",
        reference_confidence="high",
        applied_shift_hz=None if frequency is None else shift * frequency,
        reference_qc=True,
        reference_peak_snr=peak_height / noise,
        reference_peak_prominence_snr=prominence / noise,
        reference_peak_width_ppm=float(widths[best_rank]),
        reference_peak_height=peak_height,
    )


def evaluate_reference_model(
    ppm_axis,
    intensity,
    *,
    reference_model,
    observe_frequency_mhz,
    solvent_identity="unknown",
    solvent_isotopic_form="unknown",
    require_multiple_reference_regions=True,
    minimum_snr=5.0,
    minimum_prominence_snr=5.0,
    minimum_width_ppm=0.002,
    maximum_width_ppm=0.50,
    maximum_shift_ppm=0.50,
    maximum_reference_disagreement_ppm=0.05,
    expected_regions=None,
) -> ReferenceModelResult:
    """Evaluate a source-qualified multi-region reference, failing closed.

    Passing spectral fits are not sufficient when the physical solvent or
    isotopic form is unknown. In that case region diagnostics are returned but
    ``referenced_ppm`` remains identical to ``original_ppm``.
    """
    np = _numpy()
    signal = _scipy_signal()
    ppm = np.asarray(ppm_axis, dtype=float)
    values = np.asarray(intensity, dtype=float)
    if ppm.shape != values.shape or ppm.ndim != 1:
        raise NmrProcessingError("ppm_axis and intensity must be matching 1-D arrays")
    if expected_regions is None:
        try:
            definition = REFERENCE_MODEL_DEFINITIONS[str(reference_model)]
        except KeyError as exc:
            raise NmrProcessingError(
                f"Unknown reference model {reference_model!r}"
            ) from exc
        regions = definition["regions"]
        required_identity, required_isotope = definition["required_identity"]
    else:
        regions = expected_regions
        required_identity = str(solvent_identity)
        required_isotope = str(solvent_isotopic_form)

    fits = []
    for region_name, region in regions.items():
        expected = float(region["expected_ppm"])
        low, high = sorted(float(item) for item in region["search_window_ppm"])
        search_width = high - low
        try:
            alignment = align_validated_reference(
                ppm,
                values,
                expected_ppm=expected,
                search_window_ppm=search_width,
                minimum_snr=minimum_snr,
                minimum_prominence_snr=minimum_prominence_snr,
                minimum_width_ppm=minimum_width_ppm,
                maximum_width_ppm=maximum_width_ppm,
                maximum_shift_ppm=maximum_shift_ppm,
                observe_frequency_mhz=observe_frequency_mhz,
            )
            observed = alignment.reference_peak_observed_ppm
            center_index = int(np.argmin(np.abs(ppm - observed)))
            fit_quality = None
            if 0 < center_index < ppm.size - 1:
                fit_x = ppm[center_index - 1 : center_index + 2]
                fit_y = values[center_index - 1 : center_index + 2]
                coefficients = np.polyfit(fit_x, fit_y, 2)
                predicted = np.polyval(coefficients, fit_x)
                denominator = float(np.sum((fit_y - np.mean(fit_y)) ** 2))
                fit_quality = (
                    1.0
                    if denominator == 0
                    else max(
                        0.0,
                        1.0
                        - float(np.sum((fit_y - predicted) ** 2)) / denominator,
                    )
                )
            local_mask = (ppm >= low) & (ppm <= high)
            local = values[local_mask]
            local_prominence = max(float(np.max(local)) * 0.02, 1e-12)
            local_peaks, _ = signal.find_peaks(
                local,
                prominence=local_prominence,
            )
            overlap_risk = "high" if local_peaks.size > 2 else "medium"
            confidence = "medium" if overlap_risk == "high" else "high"
            fits.append(
                ReferenceRegionFit(
                    region_name=str(region_name),
                    observed_peak_ppm=observed,
                    expected_peak_ppm=expected,
                    required_shift_ppm=alignment.applied_shift_ppm,
                    required_shift_hz=alignment.applied_shift_hz,
                    peak_height=alignment.reference_peak_height,
                    peak_width_hz=(
                        None
                        if alignment.reference_peak_width_ppm is None
                        else alignment.reference_peak_width_ppm
                        * float(observe_frequency_mhz)
                    ),
                    peak_snr=alignment.reference_peak_snr,
                    prominence_snr=alignment.reference_peak_prominence_snr,
                    fit_model="three-point quadratic maximum",
                    fit_quality=fit_quality,
                    overlap_risk=overlap_risk,
                    reference_confidence=confidence,
                    qc_pass=True,
                    qc_failure_reason="",
                )
            )
        except NmrProcessingError as exc:
            fits.append(
                ReferenceRegionFit(
                    region_name=str(region_name),
                    observed_peak_ppm=None,
                    expected_peak_ppm=expected,
                    required_shift_ppm=None,
                    required_shift_hz=None,
                    peak_height=None,
                    peak_width_hz=None,
                    peak_snr=None,
                    prominence_snr=None,
                    fit_model="three-point quadratic maximum",
                    fit_quality=None,
                    overlap_risk="unknown",
                    reference_confidence="none",
                    qc_pass=False,
                    qc_failure_reason=str(exc),
                )
            )

    failures = []
    passed = [fit for fit in fits if fit.qc_pass]
    required_count = 2 if require_multiple_reference_regions else 1
    if len(passed) < required_count:
        failures.append(
            f"only {len(passed)} reference region(s) passed; "
            f"{required_count} required"
        )
    shifts = [
        float(fit.required_shift_ppm)
        for fit in passed
        if fit.required_shift_ppm is not None
    ]
    disagreement = None
    proposed_shift = 0.0
    if shifts:
        proposed_shift = float(np.median(shifts))
        disagreement = float(max(shifts) - min(shifts))
        if disagreement > float(maximum_reference_disagreement_ppm):
            failures.append(
                "reference regions disagree by "
                f"{disagreement:.6f} ppm, above "
                f"{float(maximum_reference_disagreement_ppm):.6f} ppm"
            )
        if abs(proposed_shift) > float(maximum_shift_ppm):
            failures.append("proposed reference shift exceeds configured maximum")
    identity = str(solvent_identity).lower()
    isotopic_form = str(solvent_isotopic_form).lower()
    if identity != str(required_identity).lower():
        failures.append(
            f"solvent identity is {solvent_identity!r}; model requires "
            f"{required_identity!r}"
        )
    if isotopic_form != str(required_isotope).lower():
        failures.append(
            f"solvent isotopic form is {solvent_isotopic_form!r}; model "
            f"requires {required_isotope!r}"
        )
    qc_pass = not failures
    applied_shift = proposed_shift if qc_pass else 0.0
    confidence = (
        "high"
        if qc_pass and all(fit.reference_confidence == "high" for fit in passed)
        else ("medium" if qc_pass else "none")
    )
    return ReferenceModelResult(
        original_ppm=ppm.copy(),
        referenced_ppm=ppm + applied_shift,
        reference_model=str(reference_model),
        solvent_identity=str(solvent_identity),
        solvent_isotopic_form=str(solvent_isotopic_form),
        proposed_shift_ppm=proposed_shift,
        proposed_shift_hz=proposed_shift * float(observe_frequency_mhz),
        applied_shift_ppm=applied_shift,
        applied_shift_hz=applied_shift * float(observe_frequency_mhz),
        reference_region_count=len(passed),
        reference_region_agreement=disagreement,
        reference_confidence=confidence,
        reference_qc_pass=qc_pass,
        reference_qc_failure_reasons=tuple(failures),
        region_fits=tuple(fits),
    )


def fourier_transform_fid(fid):
    """Apply nmrglue's complex FFT in the convention used by this workflow."""
    np = _numpy()
    data = np.asarray(fid, dtype=np.complex128)
    if data.ndim != 1 or data.size == 0:
        raise NmrProcessingError("fid must be a non-empty 1-D complex array")
    return _nmrglue().proc_base.fft(data)


def validate_axis_metadata(path, *, fft_points=None) -> AxisValidation:
    """Reconstruct acquisition timing and ppm limits from raw metadata."""
    fid = read_jcamp_fid(path)
    points = len(fid.real)
    swh = _metadata_float(fid.metadata, "$SWH", "$SWEEP WIDTH")
    sf = _metadata_float(
        fid.metadata, "$SF", "$SFO1", ".OBSERVE FREQUENCY"
    )
    center = _metadata_float(
        fid.metadata, "$O1P", "$SPECTRALCENTER", default=5.0
    )
    dwell = _metadata_float(fid.metadata, "DELTAX", default=1.0 / swh)
    acquisition = (points - 1) * dwell
    metadata_acquisition = _metadata_float(
        fid.metadata, ".ACQUISITION TIME", default=acquisition
    )
    size = (
        int(round(_metadata_float(fid.metadata, "$SI", default=points)))
        if fft_points is None
        else int(fft_points)
    )
    axis = build_ppm_axis(
        size,
        spectral_width_hz=swh,
        observe_frequency_mhz=sf,
        center_ppm=center,
    )
    np = _numpy()
    if not np.all(np.diff(axis) > 0):
        raise NmrProcessingError("ppm axis must be numerically ascending")
    return AxisValidation(
        complex_points=points,
        dwell_time_s=dwell,
        acquisition_time_s=acquisition,
        spectral_width_hz=swh,
        spectral_width_ppm=swh / sf,
        observe_frequency_mhz=sf,
        center_ppm=center,
        fft_points=size,
        frequency_spacing_hz=swh / size,
        ppm_spacing=swh / sf / size,
        left_limit_ppm=center + 0.5 * swh / sf,
        right_limit_ppm=center - 0.5 * swh / sf,
        metadata_acquisition_time_s=metadata_acquisition,
        dwell_width_relative_error=abs((1.0 / dwell) - swh) / swh,
    )


def apply_reference_shift(
    original_ppm,
    *,
    observed_reference_ppm,
    expected_reference_ppm,
):
    """Return a copied referenced axis and the explicit additive shift."""
    np = _numpy()
    shift = float(expected_reference_ppm) - float(observed_reference_ppm)
    return np.asarray(original_ppm, dtype=float) + shift, shift


def track_peak_families(
    peak_sets,
    *,
    max_shift_ppm=0.03,
    maximum_gap=1,
    width_ratio_limit=2.5,
    minimum_reproducible_observations=2,
):
    """Link regional peaks by continuity and compatible line width.

    Returns ``(assignments, families)``. ``assignments`` mirrors ``peak_sets``
    and contains a family ID for each input peak. A family is not matched
    across a gap larger than ``maximum_gap`` and a broad feature cannot absorb
    a narrow peak unless their width ratio is within ``width_ratio_limit``.
    """
    np = _numpy()
    if float(max_shift_ppm) <= 0 or int(maximum_gap) < 0:
        raise NmrProcessingError("tracking shift must be positive and gap non-negative")
    if float(width_ratio_limit) < 1:
        raise NmrProcessingError("width_ratio_limit must be at least one")

    states: list[dict] = []
    assignments: list[tuple[str, ...]] = []
    for spectrum_index, peaks in enumerate(peak_sets):
        current_ids: list[str] = []
        used_families: set[str] = set()
        for peak in peaks:
            candidates = []
            for state in states:
                if state["family_id"] in used_families:
                    continue
                gap = spectrum_index - state["last_index"] - 1
                if gap > int(maximum_gap):
                    continue
                ppm_difference = abs(float(peak.peak_ppm) - state["last_ppm"])
                if ppm_difference > float(max_shift_ppm):
                    continue
                prior_width = max(state["last_width"], 1e-12)
                new_width = max(float(peak.width_ppm), 1e-12)
                width_ratio = max(prior_width, new_width) / min(
                    prior_width, new_width
                )
                if width_ratio > float(width_ratio_limit):
                    continue
                cost = ppm_difference / float(max_shift_ppm) + abs(
                    np.log(width_ratio)
                )
                candidates.append((cost, state))
            if candidates:
                _, state = min(candidates, key=lambda item: item[0])
            else:
                state = {
                    "family_id": f"P{len(states) + 1:03d}",
                    "first_index": spectrum_index,
                    "positions": [],
                    "widths": [],
                }
                states.append(state)
            state["last_index"] = spectrum_index
            state["last_ppm"] = float(peak.peak_ppm)
            state["last_width"] = float(peak.width_ppm)
            state["positions"].append(float(peak.peak_ppm))
            state["widths"].append(float(peak.width_ppm))
            used_families.add(state["family_id"])
            current_ids.append(state["family_id"])
        assignments.append(tuple(current_ids))

    families = tuple(
        PeakFamily(
            family_id=state["family_id"],
            observations=len(state["positions"]),
            first_spectrum_index=state["first_index"],
            last_spectrum_index=state["last_index"],
            median_ppm=float(np.median(state["positions"])),
            ppm_range=float(max(state["positions"]) - min(state["positions"])),
            median_width_ppm=float(np.median(state["widths"])),
            reproducible=len(state["positions"])
            >= int(minimum_reproducible_observations),
        )
        for state in states
    )
    return tuple(assignments), families


def analyze_dx_peak(
    path,
    target_ppm=6.1,
    window_ppm=0.12,
    line_broadening_hz=None,
    min_prominence_snr=0.0,
    min_distance_ppm=0.01,
    integration_window_ppm=None,
    zero_fill_points=None,
    baseline_window_ppm=0.5,
    baseline_polynomial_order=2,
):
    """Return a baseline-corrected peak estimate near ``target_ppm``.

    ``peak_height`` and ``peak_area`` are baseline-corrected values.
    ``raw_peak_height`` retains the uncorrected magnitude at the selected point.
    If no peak meets ``min_prominence_snr``, :class:`NmrProcessingError` is
    raised instead of returning the largest noise wiggle in the window.
    """
    np = _numpy()
    signal = _scipy_signal()
    spectrum = build_magnitude_spectrum(
        path,
        line_broadening_hz=line_broadening_hz,
        zero_fill_points=zero_fill_points,
    )
    ppm_axis = spectrum.ppm_axis
    magnitude = spectrum.magnitude
    n = len(magnitude)

    peak_mask = np.abs(ppm_axis - float(target_ppm)) <= float(window_ppm)
    if not np.any(peak_mask):
        raise NmrProcessingError(
            f"No points found within {window_ppm} ppm of {target_ppm} ppm"
        )

    peak_indices = np.flatnonzero(peak_mask)
    baseline_curve, noise = estimate_local_baseline(
        ppm_axis,
        magnitude,
        target_ppm=float(target_ppm),
        detection_window_ppm=float(window_ppm),
        baseline_window_ppm=float(baseline_window_ppm),
        polynomial_order=int(baseline_polynomial_order),
    )
    corrected = magnitude - baseline_curve
    local = corrected[peak_indices]
    local_ppm = ppm_axis[peak_indices]
    prominence_threshold = max(0.0, float(min_prominence_snr)) * noise
    ppm_step = float(np.median(np.abs(np.diff(ppm_axis)))) if n > 1 else float(window_ppm)
    distance_points = max(1, int(round(float(min_distance_ppm) / ppm_step)))

    peaks, props = signal.find_peaks(
        local,
        distance=distance_points,
        prominence=prominence_threshold,
    )
    if peaks.size == 0:
        raise NmrProcessingError(
            "No resolved peak met the prominence threshold within "
            f"{window_ppm:g} ppm of {target_ppm:g} ppm "
            f"(required prominence SNR {float(min_prominence_snr):g})."
        )

    prominences = props.get("prominences")
    if prominences is None:
        prominences = np.zeros(peaks.size, dtype=float)

    widths = signal.peak_widths(local, peaks, rel_height=0.5)[0]
    best_rank = max(
        range(peaks.size),
        key=lambda rank: (
            float(prominences[rank]),
            -abs(float(local_ppm[int(peaks[rank])]) - float(target_ppm)),
        ),
    )
    best_local_idx = int(peaks[best_rank])
    best_idx = int(peak_indices[best_local_idx])
    raw_peak_height = float(magnitude[best_idx])
    baseline = float(baseline_curve[best_idx])
    peak_height = float(corrected[best_idx])
    snr = peak_height / noise
    width_ppm = float(widths[best_rank] * ppm_step)
    prominence = float(prominences[best_rank])
    # Integrate baseline-corrected magnitude in a fixed target window. A fixed
    # window and receiver gain make this suitable for
    # relative kinetic comparisons across a run.
    area_window = float(
        window_ppm if integration_window_ppm is None else integration_window_ppm
    )
    area_mask = np.abs(ppm_axis - float(target_ppm)) <= area_window
    area_ppm = ppm_axis[area_mask]
    area_y_corrected = np.maximum(corrected[area_mask], 0.0)
    order = np.argsort(area_ppm)
    area_x = area_ppm[order]
    area_y = area_y_corrected[order]
    peak_area = float(
        np.sum((area_y[1:] + area_y[:-1]) * np.diff(area_x) * 0.5)
    )

    return PeakResult(
        source=spectrum.source,
        target_ppm=float(target_ppm),
        peak_ppm=float(ppm_axis[best_idx]),
        peak_height=peak_height,
        baseline=baseline,
        noise=noise,
        snr=float(snr),
        prominence=prominence,
        prominence_snr=float(prominence / noise),
        width_ppm=width_ppm,
        peaks_considered=int(peaks.size),
        points_in_window=int(peak_indices.size),
        peak_area=peak_area,
        raw_peak_height=raw_peak_height,
        baseline_window_ppm=float(baseline_window_ppm),
        baseline_polynomial_order=int(baseline_polynomial_order),
    )


def pick_spectrum_region(
    ppm_axis,
    intensity,
    *,
    region_min_ppm=5.0,
    region_max_ppm=6.5,
    min_prominence_snr=5.0,
    min_distance_ppm=0.04,
    min_width_ppm=0.015,
    baseline_polynomial_order=3,
    smoothing_window_ppm=0.006,
    quantitative_intensity=None,
    source=None,
) -> RegionPeakPickingResult:
    """Find every resolved peak in a ppm region without assuming its position.

    A low-order polynomial is fit iteratively across the requested region.
    Positive peak-like residuals and large negative artifacts are excluded
    from later fit iterations.  Peak picking is then performed on a lightly
    smoothed, baseline-corrected trace using robust MAD noise and explicit
    prominence, separation, and width criteria.

    Automated picking uses magnitude intensity so phase errors cannot hide a
    resonance.  The returned arrays make the baseline and decisions directly
    auditable in plots.
    """
    np = _numpy()
    signal = _scipy_signal()
    ppm = np.asarray(ppm_axis, dtype=float)
    values = np.asarray(intensity, dtype=float)
    quantitative = (
        values
        if quantitative_intensity is None
        else np.asarray(quantitative_intensity, dtype=float)
    )
    if ppm.shape != values.shape or ppm.ndim != 1:
        raise NmrProcessingError("ppm_axis and intensity must be matching 1-D arrays")
    if quantitative.shape != ppm.shape:
        raise NmrProcessingError("quantitative_intensity must match ppm_axis")

    region_lo = float(min(region_min_ppm, region_max_ppm))
    region_hi = float(max(region_min_ppm, region_max_ppm))
    if not region_hi > region_lo:
        raise NmrProcessingError("region_max_ppm must differ from region_min_ppm")
    if float(min_prominence_snr) < 0:
        raise NmrProcessingError("min_prominence_snr must be non-negative")
    if float(min_distance_ppm) <= 0 or float(min_width_ppm) <= 0:
        raise NmrProcessingError("peak distance and width must be positive")
    if float(smoothing_window_ppm) <= 0:
        raise NmrProcessingError("smoothing_window_ppm must be positive")
    if int(baseline_polynomial_order) < 0:
        raise NmrProcessingError("baseline_polynomial_order must be non-negative")

    region_mask = (
        (ppm >= region_lo)
        & (ppm <= region_hi)
        & np.isfinite(ppm)
        & np.isfinite(values)
    )
    region_indices = np.flatnonzero(region_mask)
    required = max(int(baseline_polynomial_order) + 2, 7)
    if region_indices.size < required:
        raise NmrProcessingError(
            f"Not enough points in requested region {region_lo:g}..{region_hi:g} ppm"
        )

    local_ppm = ppm[region_indices]
    local_values = values[region_indices]
    local_quantitative = quantitative[region_indices]
    center = 0.5 * (region_lo + region_hi)
    x = local_ppm - center
    selected = np.ones(local_values.size, dtype=bool)
    polynomial_order = int(baseline_polynomial_order)
    coefficients = np.polyfit(x, local_values, polynomial_order)
    noise = 1.0

    for _ in range(10):
        fit_order = min(polynomial_order, int(np.count_nonzero(selected)) - 1)
        coefficients = np.polyfit(
            x[selected],
            local_values[selected],
            fit_order,
        )
        residual = local_values - np.polyval(coefficients, x)
        selected_residual = residual[selected]
        median = float(np.median(selected_residual))
        noise = float(
            1.4826 * np.median(np.abs(selected_residual - median))
        )
        if not np.isfinite(noise) or noise <= 0:
            noise = float(np.std(selected_residual)) or 1.0
        updated = (
            np.isfinite(residual)
            & (residual >= median - 4.0 * noise)
            & (residual <= median + 2.5 * noise)
        )
        if np.count_nonzero(updated) < required or np.array_equal(updated, selected):
            break
        selected = updated

    local_baseline = np.polyval(coefficients, x)
    local_corrected = local_values - local_baseline
    quantitative_coefficients = np.polyfit(
        x[selected],
        local_quantitative[selected],
        min(polynomial_order, int(np.count_nonzero(selected)) - 1),
    )
    quantitative_baseline = np.polyval(quantitative_coefficients, x)
    quantitative_corrected = local_quantitative - quantitative_baseline
    quantitative_residual = quantitative_corrected[selected]
    quantitative_median = float(np.median(quantitative_residual))
    quantitative_noise = float(
        1.4826
        * np.median(np.abs(quantitative_residual - quantitative_median))
    )
    if not np.isfinite(quantitative_noise) or quantitative_noise <= 0:
        quantitative_noise = float(np.std(quantitative_residual)) or 1.0
    ppm_step = float(np.median(np.abs(np.diff(local_ppm))))
    smoothing_points = max(5, int(round(float(smoothing_window_ppm) / ppm_step)))
    if smoothing_points % 2 == 0:
        smoothing_points += 1
    smoothing_points = min(
        smoothing_points,
        local_corrected.size if local_corrected.size % 2 else local_corrected.size - 1,
    )
    if smoothing_points >= 5:
        local_smoothed = signal.savgol_filter(
            local_corrected,
            window_length=smoothing_points,
            polyorder=min(3, smoothing_points - 2),
        )
    else:
        local_smoothed = local_corrected.copy()

    distance_points = max(1, int(round(float(min_distance_ppm) / ppm_step)))
    width_points = max(1.0, float(min_width_ppm) / ppm_step)
    local_peaks, properties = signal.find_peaks(
        local_smoothed,
        prominence=float(min_prominence_snr) * noise,
        distance=distance_points,
        width=width_points,
    )
    prominences = properties.get("prominences", np.zeros(local_peaks.size))
    widths = properties.get("widths", np.zeros(local_peaks.size))

    peaks = []
    for rank, local_index in enumerate(local_peaks):
        idx = int(local_index)
        prominence = float(prominences[rank])
        peak_height = float(quantitative_corrected[idx])
        interpolated_ppm = float(local_ppm[idx])
        interpolation_quality = 0.0
        if 0 < idx < local_smoothed.size - 1:
            fit_x = local_ppm[idx - 1 : idx + 2]
            fit_y = local_smoothed[idx - 1 : idx + 2]
            quadratic = np.polyfit(fit_x, fit_y, 2)
            if quadratic[0] < 0:
                vertex = float(-quadratic[1] / (2.0 * quadratic[0]))
                if min(fit_x) <= vertex <= max(fit_x):
                    interpolated_ppm = vertex
            fitted_y = np.polyval(quadratic, fit_x)
            denominator = float(np.sum((fit_y - np.mean(fit_y)) ** 2))
            interpolation_quality = (
                1.0
                if denominator == 0
                else max(
                    0.0,
                    1.0 - float(np.sum((fit_y - fitted_y) ** 2)) / denominator,
                )
            )
        width_ppm = float(widths[rank] * ppm_step)
        integration_half_width = max(width_ppm, float(min_width_ppm))
        integration_mask = (
            local_ppm >= interpolated_ppm - integration_half_width
        ) & (local_ppm <= interpolated_ppm + integration_half_width)
        area_x = local_ppm[integration_mask]
        area_y = quantitative_corrected[integration_mask]
        order = np.argsort(area_x)
        signed_area = float(_trapezoid(np, area_y[order], area_x[order]))
        positive_area = float(
            _trapezoid(np, np.maximum(area_y[order], 0.0), area_x[order])
        )
        classification = (
            "resolved_peak"
            if prominence / noise >= float(min_prominence_snr)
            and width_ppm >= float(min_width_ppm)
            and signed_area > 0
            else "unresolved_feature"
        )
        peaks.append(
            RegionPeak(
                peak_ppm=float(local_ppm[idx]),
                peak_height=peak_height,
                raw_peak_height=float(local_quantitative[idx]),
                baseline=float(quantitative_baseline[idx]),
                noise=quantitative_noise,
                snr=float(peak_height / quantitative_noise),
                prominence=prominence,
                prominence_snr=float(prominence / noise),
                width_ppm=width_ppm,
                interpolated_ppm=interpolated_ppm,
                interpolation_quality=interpolation_quality,
                signed_area=signed_area,
                positive_area=positive_area,
                classification=classification,
            )
        )
    peaks.sort(key=lambda peak: peak.peak_ppm, reverse=True)

    return RegionPeakPickingResult(
        source=Path(source) if source is not None else None,
        region_min_ppm=region_lo,
        region_max_ppm=region_hi,
        noise=noise,
        ppm_axis=local_ppm,
        magnitude=local_values,
        baseline=local_baseline,
        corrected=local_corrected,
        smoothed=local_smoothed,
        peaks=tuple(peaks),
    )


def pick_dx_region_peaks(
    path,
    *,
    region_min_ppm=5.0,
    region_max_ppm=6.5,
    line_broadening_hz=None,
    zero_fill_points=None,
    min_prominence_snr=5.0,
    min_distance_ppm=0.04,
    min_width_ppm=0.015,
    baseline_polynomial_order=3,
    smoothing_window_ppm=0.006,
) -> RegionPeakPickingResult:
    """Process a JCAMP-DX FID and find all resolved peaks in a ppm region."""
    spectrum = build_magnitude_spectrum(
        path,
        line_broadening_hz=line_broadening_hz,
        zero_fill_points=zero_fill_points,
    )
    return pick_spectrum_region(
        spectrum.ppm_axis,
        spectrum.magnitude,
        region_min_ppm=region_min_ppm,
        region_max_ppm=region_max_ppm,
        min_prominence_snr=min_prominence_snr,
        min_distance_ppm=min_distance_ppm,
        min_width_ppm=min_width_ppm,
        baseline_polynomial_order=baseline_polynomial_order,
        smoothing_window_ppm=smoothing_window_ppm,
        source=spectrum.source,
    )


def build_magnitude_spectrum(
    path,
    line_broadening_hz=None,
    zero_fill_points=None,
    truncation_window="none",
) -> SpectrumData:
    """Read a DX FID and return its magnitude spectrum and ppm axis.

    When omitted, line broadening comes from ``$LB``/``$APODIZATION`` and the
    output size comes from ``$SI``.  This reproduces the processing parameters
    stored by the instrument while still allowing explicit overrides.
    """
    np = _numpy()
    fid, ppm_axis, spectrum, processed_points, line_broadening_hz = (
        _build_complex_spectrum(
            path,
            line_broadening_hz=line_broadening_hz,
            zero_fill_points=zero_fill_points,
            truncation_window=truncation_window,
        )
    )
    magnitude = np.abs(spectrum)
    return SpectrumData(
        fid.path,
        fid.metadata,
        ppm_axis,
        magnitude,
        processed_points=processed_points,
        line_broadening_hz=float(line_broadening_hz),
        real=np.real(spectrum),
        imaginary=np.imag(spectrum),
        observe_frequency_mhz=_metadata_float(
            fid.metadata, "$SF", "$SFO1", ".OBSERVE FREQUENCY", default=60.0
        ),
        spectral_width_hz=_metadata_float(
            fid.metadata, "$SWH", "$SWEEP WIDTH"
        ),
        center_ppm=_metadata_float(
            fid.metadata, "$O1P", "$SPECTRALCENTER", default=5.0
        ),
        truncation_window=str(truncation_window),
    )


def build_phased_spectrum(
    path,
    line_broadening_hz=None,
    zero_fill_points=None,
    phase0_deg=None,
    phase1_deg=None,
    *,
    inverse_phase=True,
    phase_method="stored",
    truncation_window="none",
) -> SpectrumData:
    """Return a phase-corrected complex spectrum using nmrglue.

    NMReady's Bruker-compatible ``$PHC0``/``$PHC1`` values describe the phase
    correction stored with the dataset.  For these NMReady JCAMP files,
    ``inverse_phase=True`` maps that convention to :func:`nmrglue.proc_base.ps`
    and produces absorptive positive solvent lines.
    """
    np = _numpy()
    ng = _nmrglue()
    fid, ppm_axis, spectrum, processed_points, line_broadening_hz = (
        _build_complex_spectrum(
            path,
            line_broadening_hz=line_broadening_hz,
            zero_fill_points=zero_fill_points,
            truncation_window=truncation_window,
        )
    )
    if phase_method in {"stored", "manual"}:
        if phase_method == "manual" and (
            phase0_deg is None or phase1_deg is None
        ):
            raise NmrProcessingError(
                "manual phase_method requires phase0_deg and phase1_deg"
            )
        if phase0_deg is None:
            phase0_deg = _metadata_float(fid.metadata, "$PHC0", default=0.0)
        if phase1_deg is None:
            phase1_deg = _metadata_float(fid.metadata, "$PHC1", default=0.0)
        phased = ng.proc_base.ps(
            spectrum,
            p0=float(phase0_deg),
            p1=float(phase1_deg),
            inv=bool(inverse_phase),
        )
    elif phase_method in {"peak_minima", "automatic_peak_minima"}:
        phased, phase0_deg, phase1_deg = autophase_spectrum(
            spectrum,
            method="peak_minima",
            initial_phase0_deg=0.0 if phase0_deg is None else phase0_deg,
            initial_phase1_deg=0.0 if phase1_deg is None else phase1_deg,
        )
    elif phase_method == "none":
        phase0_deg, phase1_deg = 0.0, 0.0
        phased = spectrum
    else:
        raise NmrProcessingError(
            "phase_method must be stored, manual, automatic_peak_minima, "
            "peak_minima, or none"
        )
    return SpectrumData(
        fid.path,
        fid.metadata,
        ppm_axis,
        np.abs(phased),
        processed_points=processed_points,
        line_broadening_hz=float(line_broadening_hz),
        real=np.real(phased),
        imaginary=np.imag(phased),
        phase0_deg=float(phase0_deg),
        phase1_deg=float(phase1_deg),
        observe_frequency_mhz=_metadata_float(
            fid.metadata, "$SF", "$SFO1", ".OBSERVE FREQUENCY", default=60.0
        ),
        spectral_width_hz=_metadata_float(
            fid.metadata, "$SWH", "$SWEEP WIDTH"
        ),
        center_ppm=_metadata_float(
            fid.metadata, "$O1P", "$SPECTRALCENTER", default=5.0
        ),
        phase_method=str(phase_method),
        truncation_window=str(truncation_window),
    )


def _build_complex_spectrum(
    path,
    *,
    line_broadening_hz,
    zero_fill_points,
    truncation_window="none",
):
    """Decode, apodize, zero-fill, FFT, and construct the NMReady ppm axis."""
    np = _numpy()
    ng = _nmrglue()
    fid = read_jcamp_fid(path)
    data = np.asarray(fid.complex_points, dtype=np.complex128)

    swh_hz = _metadata_float(fid.metadata, "$SWH", "$SWEEP WIDTH")
    center_ppm = _metadata_float(
        fid.metadata, "$O1P", "$SPECTRALCENTER", default=5.0
    )
    sf_mhz = _metadata_float(
        fid.metadata,
        "$SF",
        "$SFO1",
        ".OBSERVE FREQUENCY",
        default=60.0,
    )

    acquired_points = len(data)
    if line_broadening_hz is None:
        line_broadening_hz = _metadata_float(
            fid.metadata,
            "$LB",
            "$APODIZATION",
            default=0.0,
        )
    if float(line_broadening_hz) < 0:
        raise NmrProcessingError("line_broadening_hz must be non-negative")

    if zero_fill_points is None:
        zero_fill_points = int(
            round(_metadata_float(fid.metadata, "$SI", default=acquired_points))
        )
    processed_points = max(acquired_points, int(zero_fill_points))

    if truncation_window == "half-cosine":
        data = data * half_cosine_truncation_window(acquired_points)
    elif truncation_window != "none":
        raise NmrProcessingError(
            "truncation_window must be 'none' or 'half-cosine'"
        )

    # nmrglue's em uses the same exp(-pi * LB * t) convention when lb is
    # supplied in cycles per acquired trace (LB_hz / spectral_width_hz).
    processed_fid = ng.proc_base.em(
        data, lb=float(line_broadening_hz) / float(swh_hz)
    )
    processed_fid = ng.proc_base.zf_size(processed_fid, processed_points)
    spectrum = fourier_transform_fid(processed_fid)

    # This sign is verified both from the Bruker-compatible axis metadata
    # ($OFFSET and $SW) and the observed toluene peaks near 6.99 and 2.08 ppm.
    ppm_axis = build_ppm_axis(
        processed_points,
        spectral_width_hz=swh_hz,
        observe_frequency_mhz=sf_mhz,
        center_ppm=center_ppm,
    )
    return fid, ppm_axis, spectrum, processed_points, float(line_broadening_hz)


def estimate_local_baseline(
    ppm_axis,
    intensity,
    *,
    target_ppm,
    detection_window_ppm=0.12,
    baseline_window_ppm=0.5,
    polynomial_order=2,
):
    """Estimate a smooth local baseline and robust residual noise.

    Points in the target window are excluded from the fit.  Iterative clipping
    removes positive peaks in the flanking region, which is important when a
    solvent shoulder or a second analyte peak is nearby.
    """
    np = _numpy()
    ppm = np.asarray(ppm_axis, dtype=float)
    values = np.asarray(intensity, dtype=float)
    if ppm.shape != values.shape or ppm.ndim != 1:
        raise NmrProcessingError("ppm_axis and intensity must be matching 1-D arrays")
    if float(detection_window_ppm) <= 0:
        raise NmrProcessingError("detection_window_ppm must be positive")
    if float(baseline_window_ppm) <= float(detection_window_ppm):
        raise NmrProcessingError(
            "baseline_window_ppm must be larger than detection_window_ppm"
        )
    if int(polynomial_order) < 0:
        raise NmrProcessingError("baseline_polynomial_order must be non-negative")

    distance = np.abs(ppm - float(target_ppm))
    fit_mask = (
        (distance <= float(baseline_window_ppm))
        & (distance > float(detection_window_ppm) * 1.25)
        & np.isfinite(values)
    )
    fit_indices = np.flatnonzero(fit_mask)
    required = int(polynomial_order) + 2
    if fit_indices.size < required:
        raise NmrProcessingError(
            "Not enough flanking points to estimate the local baseline"
        )

    x = ppm[fit_indices] - float(target_ppm)
    y = values[fit_indices]
    selected = np.ones(y.size, dtype=bool)
    coefficients = np.polyfit(x, y, int(polynomial_order))
    noise = 1.0

    for _ in range(8):
        coefficients = np.polyfit(
            x[selected],
            y[selected],
            min(int(polynomial_order), int(np.count_nonzero(selected)) - 1),
        )
        residual = y - np.polyval(coefficients, x)
        centered = residual[selected] - np.median(residual[selected])
        noise = float(1.4826 * np.median(np.abs(centered)))
        if not np.isfinite(noise) or noise <= 0:
            noise = float(np.std(centered)) or 1.0
        median = float(np.median(residual[selected]))
        updated = (residual >= median - 4.0 * noise) & (
            residual <= median + 2.5 * noise
        )
        if np.count_nonzero(updated) < required or np.array_equal(updated, selected):
            break
        selected = updated

    baseline = np.polyval(coefficients, ppm - float(target_ppm))
    return baseline, noise


def asymmetric_least_squares_baseline(
    intensity,
    *,
    smoothness=1e6,
    asymmetry=0.001,
    iterations=10,
):
    """Estimate a baseline using asymmetric penalized least squares.

    This is provided as a sensitivity-analysis alternative, not as the default:
    broad NMR resonances can be partially absorbed into an ALS baseline.
    """
    np = _numpy()
    values = np.asarray(intensity, dtype=float)
    if values.ndim != 1 or values.size < 4:
        raise NmrProcessingError("intensity must be a 1-D array with at least 4 points")
    if float(smoothness) <= 0 or not 0 < float(asymmetry) < 1:
        raise NmrProcessingError("ALS smoothness must be positive and asymmetry in (0, 1)")
    if int(iterations) <= 0:
        raise NmrProcessingError("ALS iterations must be positive")
    try:
        from scipy import sparse
        from scipy.sparse.linalg import spsolve
    except ImportError as exc:
        raise NmrProcessingError("ALS baseline requires scipy") from exc

    difference = sparse.diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(values.size - 2, values.size))
    penalty = float(smoothness) * (difference.T @ difference)
    weights = np.ones(values.size)
    baseline = np.zeros_like(values)
    for _ in range(int(iterations)):
        weight_matrix = sparse.spdiags(weights, 0, values.size, values.size)
        baseline = spsolve((weight_matrix + penalty).tocsc(), weights * values)
        weights = np.where(
            values > baseline,
            float(asymmetry),
            1.0 - float(asymmetry),
        )
    return np.asarray(baseline)


def plot_peak_region(
    path,
    result: PeakResult,
    output_dir,
    target_ppm=6.1,
    detection_window_ppm=0.12,
    plot_window_ppm=0.5,
    line_broadening_hz=None,
    zero_fill_points=None,
):
    """Save a raw and baseline-corrected review plot around a target peak."""
    np = _numpy()
    plt = _matplotlib_pyplot()
    spectrum = build_magnitude_spectrum(
        path,
        line_broadening_hz=line_broadening_hz,
        zero_fill_points=zero_fill_points,
    )
    ppm_axis = spectrum.ppm_axis
    magnitude = spectrum.magnitude

    plot_mask = np.abs(ppm_axis - float(target_ppm)) <= float(plot_window_ppm)
    if not np.any(plot_mask):
        raise NmrProcessingError(
            f"No points found within plot window {plot_window_ppm} ppm of {target_ppm} ppm"
        )

    x = ppm_axis[plot_mask]
    y = magnitude[plot_mask]
    baseline_curve, _ = estimate_local_baseline(
        ppm_axis,
        magnitude,
        target_ppm=float(target_ppm),
        detection_window_ppm=float(detection_window_ppm),
        baseline_window_ppm=result.baseline_window_ppm,
        polynomial_order=result.baseline_polynomial_order,
    )
    local_baseline = baseline_curve[plot_mask]
    corrected = y - local_baseline
    scale = float(np.max(np.abs(y))) or 1.0
    y_scaled = y / scale
    baseline_scaled = local_baseline / scale
    corrected_scale = float(np.max(np.abs(corrected))) or 1.0

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    target_label = f"{float(target_ppm):.3f}".replace(".", "p")
    png_path = output_path / f"{Path(path).stem}_target_{target_label}ppm.png"

    fig, (ax, corrected_ax) = plt.subplots(
        2,
        1,
        figsize=(9.5, 7.2),
        dpi=140,
        sharex=True,
        gridspec_kw={"height_ratios": [1.0, 1.15]},
    )
    ax.plot(x, y_scaled, color="#1f77b4", linewidth=1.2)
    ax.plot(x, baseline_scaled, color="#777777", linestyle=":", linewidth=1.1,
            label="local polynomial baseline")
    ax.axvspan(
        float(target_ppm) - float(detection_window_ppm),
        float(target_ppm) + float(detection_window_ppm),
        color="#f2c94c",
        alpha=0.22,
        label="detection window",
    )
    ax.axvline(float(target_ppm), color="#555555", linestyle="--", linewidth=1.0, label="target")
    ax.axvline(result.peak_ppm, color="#d62728", linewidth=1.4, label="detected peak")
    ax.scatter(
        [result.peak_ppm],
        [result.raw_peak_height / scale],
        color="#d62728",
        s=28,
        zorder=4,
    )

    ax.set_title(
        f"{Path(path).name} | peak {result.peak_ppm:.4f} ppm | "
        f"SNR {result.snr:.2f} | prominence SNR {result.prominence_snr:.2f}"
    )
    ax.set_ylabel("relative magnitude")
    ax.set_xlim(float(target_ppm) + float(plot_window_ppm), float(target_ppm) - float(plot_window_ppm))
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)

    corrected_ax.plot(x, corrected / corrected_scale, color="#2a9d8f", linewidth=1.2)
    corrected_ax.axhline(0.0, color="#777777", linestyle=":", linewidth=0.9)
    corrected_ax.axvspan(
        float(target_ppm) - float(detection_window_ppm),
        float(target_ppm) + float(detection_window_ppm),
        color="#f2c94c",
        alpha=0.22,
    )
    corrected_ax.axvline(
        float(target_ppm), color="#555555", linestyle="--", linewidth=1.0
    )
    corrected_ax.axvline(result.peak_ppm, color="#d62728", linewidth=1.4)
    corrected_ax.scatter(
        [result.peak_ppm],
        [result.peak_height / corrected_scale],
        color="#d62728",
        s=28,
        zorder=4,
    )
    corrected_ax.set_xlabel("Chemical shift (ppm)")
    corrected_ax.set_ylabel("baseline-corrected\nrelative magnitude")
    corrected_ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(png_path)
    plt.close(fig)
    return png_path


def iter_dx_files(path):
    """Yield one or more .dx files from a file or directory path."""
    root = Path(path)
    if root.is_file():
        yield root
        return
    yield from sorted(root.rglob("*.dx"))


def _split_header(line: str) -> tuple[str, str]:
    body = line[2:]
    if "=" not in body:
        return body.strip(), ""
    key, value = body.split("=", 1)
    return key.strip(), value.strip()


def _parse_numbers(line: str) -> list[float]:
    values = []
    for token in line.split():
        try:
            values.append(float(token))
        except ValueError:
            pass
    return values


def _metadata_text(value) -> str:
    """Convert nmrglue's list-valued JCAMP metadata to a scalar string."""
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


def _metadata_float(metadata: dict[str, str], *keys, default=None) -> float:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            normalized_key = str(key).strip().upper()
            value = next(
                (
                    candidate_value
                    for candidate_key, candidate_value in metadata.items()
                    if str(candidate_key).strip().upper() == normalized_key
                ),
                None,
            )
        if value is not None:
            raw = str(value).split("$$", 1)[0].strip()
            try:
                return float(raw.split(",", 1)[0])
            except ValueError:
                continue
    if default is not None:
        return float(default)
    raise NmrProcessingError(f"Missing numeric metadata field; tried {keys}")


def _numpy():
    try:
        import numpy
    except ImportError as exc:
        raise NmrProcessingError(
            "NMR peak analysis requires numpy. Install dependencies with "
            "python -m pip install -r requirements.txt."
        ) from exc
    return numpy


def _trapezoid(np, y, x):
    """Trapezoidal integral, portable across NumPy versions.

    NumPy >= 2.0 exposes ``np.trapezoid``; older releases only have
    ``np.trapz``. Prefer the new name and fall back so the code runs on both.
    """
    fn = getattr(np, "trapezoid", None)
    if fn is None:
        fn = np.trapz
    return fn(y, x)


def _nmrglue():
    try:
        import nmrglue
    except ImportError as exc:
        raise NmrProcessingError(
            "NMR JCAMP processing requires nmrglue. Install dependencies with "
            "python -m pip install -r requirements.txt."
        ) from exc
    return nmrglue


def _scipy_signal():
    try:
        from scipy import signal
    except ImportError as exc:
        raise NmrProcessingError(
            "NMR peak finding now requires scipy. Install dependencies with "
            "python -m pip install -r requirements.txt."
        ) from exc
    return signal


def _matplotlib_pyplot():
    try:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot
    except ImportError as exc:
        raise NmrProcessingError(
            "NMR review plots require matplotlib. Install dependencies with "
            "python -m pip install -r requirements.txt."
        ) from exc
    return pyplot
