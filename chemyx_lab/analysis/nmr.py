"""Minimal NMR JCAMP-DX parsing and scipy-based peak checks."""

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


def read_jcamp_fid(path) -> FidData:
    """Read an NMReady JCAMP-DX FID file with real and imaginary pages."""
    dx_path = Path(path)
    metadata: dict[str, str] = {}
    real: list[float] = []
    imag: list[float] = []
    page = None

    with dx_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("$$"):
                continue

            if line.startswith("##"):
                key, value = _split_header(line)
                metadata[key] = value
                if key == "PAGE":
                    page = 1 if "N=1" in value else 2 if "N=2" in value else None
                elif key.startswith("END"):
                    page = None
                continue

            if page not in (1, 2):
                continue
            numbers = _parse_numbers(line)
            if len(numbers) < 2:
                continue
            values = numbers[1:]
            if page == 1:
                real.extend(values)
            else:
                imag.extend(values)

    n = min(len(real), len(imag))
    if n == 0:
        raise NmrProcessingError(f"No FID pages found in {dx_path}")
    return FidData(dx_path, metadata, real[:n], imag[:n])


def analyze_dx_peak(
    path,
    target_ppm=6.1,
    window_ppm=0.12,
    line_broadening_hz=0.3,
    min_prominence_snr=0.0,
    min_distance_ppm=0.01,
    integration_window_ppm=None,
):
    """Return a scipy ``find_peaks`` estimate near ``target_ppm``."""
    np = _numpy()
    signal = _scipy_signal()
    spectrum = build_magnitude_spectrum(path, line_broadening_hz=line_broadening_hz)
    ppm_axis = spectrum.ppm_axis
    magnitude = spectrum.magnitude
    n = len(magnitude)

    peak_mask = np.abs(ppm_axis - float(target_ppm)) <= float(window_ppm)
    if not np.any(peak_mask):
        raise NmrProcessingError(
            f"No points found within {window_ppm} ppm of {target_ppm} ppm"
        )

    peak_indices = np.flatnonzero(peak_mask)
    local = magnitude[peak_indices]
    local_ppm = ppm_axis[peak_indices]

    baseline_mask = (
        (np.abs(ppm_axis - float(target_ppm)) > float(window_ppm) * 1.5)
        & (np.abs(ppm_axis - float(target_ppm)) < float(window_ppm) * 5.0)
    )
    baseline_values = magnitude[baseline_mask]
    if baseline_values.size == 0:
        baseline_values = magnitude

    baseline = float(np.median(baseline_values))
    noise = float(np.std(baseline_values - baseline)) or 1.0
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
            "No scipy peaks found within "
            f"{window_ppm} ppm of {target_ppm} ppm. Try a wider --window or lower "
            "--min-prominence-snr."
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
    peak_height = float(magnitude[best_idx])
    snr = (peak_height - baseline) / noise
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
    corrected = np.maximum(magnitude[area_mask] - baseline, 0.0)
    order = np.argsort(area_ppm)
    area_x = area_ppm[order]
    area_y = corrected[order]
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
    )


def build_magnitude_spectrum(path, line_broadening_hz=0.3) -> SpectrumData:
    """Read a DX file and return a magnitude spectrum with a ppm axis."""
    np = _numpy()
    fid = read_jcamp_fid(path)
    data = np.asarray(fid.complex_points, dtype=np.complex128)

    swh_hz = _metadata_float(fid.metadata, "$SWH", "$SWEEP WIDTH")
    center_ppm = _metadata_float(fid.metadata, "$O1P", "$SPECTRALCENTER", default=5.0)
    sf_mhz = _metadata_float(
        fid.metadata,
        "$SF",
        "$SFO1",
        ".OBSERVE FREQUENCY",
        default=60.0,
    )

    n = len(data)
    time_axis = np.arange(n, dtype=float) / float(swh_hz)
    window = np.exp(-float(line_broadening_hz) * np.pi * time_axis)
    spectrum = np.fft.fftshift(np.fft.fft(data * window))
    magnitude = np.abs(spectrum)

    freq_hz = np.linspace(-swh_hz / 2.0, swh_hz / 2.0, n, endpoint=False)
    ppm_axis = center_ppm - (freq_hz / sf_mhz)
    return SpectrumData(fid.path, fid.metadata, ppm_axis, magnitude)


def plot_peak_region(
    path,
    result: PeakResult,
    output_dir,
    target_ppm=6.1,
    detection_window_ppm=0.12,
    plot_window_ppm=0.5,
    line_broadening_hz=0.3,
):
    """Save a PNG review plot around the target peak region."""
    np = _numpy()
    plt = _matplotlib_pyplot()
    spectrum = build_magnitude_spectrum(path, line_broadening_hz=line_broadening_hz)
    ppm_axis = spectrum.ppm_axis
    magnitude = spectrum.magnitude

    plot_mask = np.abs(ppm_axis - float(target_ppm)) <= float(plot_window_ppm)
    if not np.any(plot_mask):
        raise NmrProcessingError(
            f"No points found within plot window {plot_window_ppm} ppm of {target_ppm} ppm"
        )

    x = ppm_axis[plot_mask]
    y = magnitude[plot_mask]
    scale = float(np.max(np.abs(y))) or 1.0
    y_scaled = y / scale

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    target_label = f"{float(target_ppm):.3f}".replace(".", "p")
    png_path = output_path / f"{Path(path).stem}_target_{target_label}ppm.png"

    fig, ax = plt.subplots(figsize=(9.5, 5.2), dpi=140)
    ax.plot(x, y_scaled, color="#1f77b4", linewidth=1.2)
    ax.axvspan(
        float(target_ppm) - float(detection_window_ppm),
        float(target_ppm) + float(detection_window_ppm),
        color="#f2c94c",
        alpha=0.22,
        label="detection window",
    )
    ax.axvline(float(target_ppm), color="#555555", linestyle="--", linewidth=1.0, label="target")
    ax.axvline(result.peak_ppm, color="#d62728", linewidth=1.4, label="detected peak")
    ax.axhline(result.baseline / scale, color="#777777", linestyle=":", linewidth=1.0, label="baseline")
    ax.scatter([result.peak_ppm], [result.peak_height / scale], color="#d62728", s=28, zorder=4)

    ax.set_title(
        f"{Path(path).name} | peak {result.peak_ppm:.4f} ppm | "
        f"SNR {result.snr:.2f} | prominence SNR {result.prominence_snr:.2f}"
    )
    ax.set_xlabel("ppm")
    ax.set_ylabel("relative magnitude")
    ax.set_xlim(float(target_ppm) + float(plot_window_ppm), float(target_ppm) - float(plot_window_ppm))
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
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


def _metadata_float(metadata: dict[str, str], *keys, default=None) -> float:
    for key in keys:
        if key in metadata:
            raw = metadata[key].split("$$", 1)[0].strip()
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
