"""Minimal NMR JCAMP-DX parsing and 6.1 ppm peak checks."""

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
class PeakResult:
    source: Path
    target_ppm: float
    peak_ppm: float
    peak_height: float
    baseline: float
    noise: float
    snr: float
    points_in_window: int


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


def analyze_dx_peak(path, target_ppm=6.1, window_ppm=0.12, line_broadening_hz=0.3):
    """Return a simple magnitude-spectrum peak estimate near ``target_ppm``."""
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

    peak_mask = np.abs(ppm_axis - float(target_ppm)) <= float(window_ppm)
    if not np.any(peak_mask):
        raise NmrProcessingError(
            f"No points found within {window_ppm} ppm of {target_ppm} ppm"
        )

    peak_indices = np.flatnonzero(peak_mask)
    local = magnitude[peak_indices]
    best_idx = peak_indices[int(np.argmax(local))]

    baseline_mask = (
        (np.abs(ppm_axis - float(target_ppm)) > float(window_ppm) * 1.5)
        & (np.abs(ppm_axis - float(target_ppm)) < float(window_ppm) * 5.0)
    )
    baseline_values = magnitude[baseline_mask]
    if baseline_values.size == 0:
        baseline_values = magnitude

    baseline = float(np.median(baseline_values))
    noise = float(np.std(baseline_values - baseline)) or 1.0
    peak_height = float(magnitude[best_idx])
    snr = (peak_height - baseline) / noise

    return PeakResult(
        source=fid.path,
        target_ppm=float(target_ppm),
        peak_ppm=float(ppm_axis[best_idx]),
        peak_height=peak_height,
        baseline=baseline,
        noise=noise,
        snr=float(snr),
        points_in_window=int(peak_indices.size),
    )


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
