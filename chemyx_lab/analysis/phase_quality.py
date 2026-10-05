"""Validation-only phase diagnostics on fixed, phase-invariant resonance windows.

The imaginary channel of an absorptive peak is normally odd, not zero. Only
its even component is a phase-leakage penalty. Nothing here changes the
production baseline, peak picker, integration or completion rules.
"""
from dataclasses import dataclass
import hashlib
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter
from scipy.signal import find_peaks, peak_widths


QUALITY_CONFIG = {
    'version': 'phase_quality_v1', 'ppm_domain': [0.0, 12.0],
    'maximum_scoring_points': 4096, 'maximum_peaks': 8,
    'minimum_peak_separation_ppm': 0.25, 'minimum_prominence_fraction': 0.025,
    'minimum_prominence_noise': 8.0, 'window_halfwidth_ppm': [0.04, 0.18],
    'noise_threshold_sigma': 3.0, 'quiet_fraction': 0.35,
    'description': 'Fixed magnitude-selected windows and quiet points shared by every method; diagnostic detrending only.',
}


@dataclass
class QualityContext:
    ppm: np.ndarray
    indices: np.ndarray
    fft: np.ndarray
    full_points: int
    noise: float
    centers: list
    windows: list
    quiet: np.ndarray
    baseline_design: np.ndarray
    baseline_inverse: np.ndarray
    scale: float
    metadata: dict


def prepare_context(ppm, fft, config=None):
    """Choose once from complex magnitude; never reselect after phasing."""
    cfg = {**QUALITY_CONFIG, **(config or {})}
    ppm = np.asarray(ppm, dtype=float); fft = np.asarray(fft, dtype=complex)
    if ppm.ndim != 1 or fft.shape != ppm.shape or len(ppm) < 32:
        raise ValueError('Quality scoring requires aligned one-dimensional complex spectrum')
    if not np.all(np.isfinite(fft)) or not np.all(np.isfinite(ppm)) or np.any(np.diff(ppm) <= 0):
        raise ValueError('Non-finite data or nonascending ppm axis')
    stride = max(1, int(np.ceil(len(ppm) / cfg['maximum_scoring_points'])))
    indices = np.arange(0, len(ppm), stride)
    domain = (ppm[indices] >= cfg['ppm_domain'][0]) & (ppm[indices] <= cfg['ppm_domain'][1])
    indices = indices[domain]; x = ppm[indices]; z = fft[indices]
    if len(x) < 32 or np.max(np.abs(z)) <= 0:
        raise ValueError('No usable signal in quality-scoring domain')
    dx = float(np.median(np.diff(x)))
    noise = float(np.median(np.abs(np.diff(z))) / np.sqrt(2 * np.log(2)))
    noise = max(noise, np.max(np.abs(z)) * 1e-12)
    mag = gaussian_filter1d(np.abs(z), max(0.005 / dx, 0.5))
    background = median_filter(mag, size=max(3, int(0.4 / dx) | 1), mode='nearest')
    prominence = max(cfg['minimum_prominence_noise'] * noise,
                     cfg['minimum_prominence_fraction'] * float(np.max(mag)))
    candidates, properties = find_peaks(mag, prominence=prominence,
        distance=max(1, int(cfg['minimum_peak_separation_ppm'] / dx)))
    chosen = sorted(candidates, key=lambda i: (-float(mag[i] - background[i]), int(i)))[:cfg['maximum_peaks']]
    # A weak target may not pass prominence. Keep its magnitude center visible
    # in diagnostics, but identify it explicitly as a forced target window.
    target = np.flatnonzero((x >= 5.7) & (x <= 5.9))
    forced = None
    if len(target):
        target_index = int(target[np.argmax(mag[target])])
        if not any(abs(x[c] - x[target_index]) < 0.18 for c in chosen):
            chosen.append(target_index); forced = target_index
    centers = []; windows = []; descriptions = []
    for c in sorted(chosen):
        width = float(peak_widths(mag, [c], rel_height=0.5)[0][0]) if c in candidates else 0.0
        half = float(np.clip(1.5 * width * dx, *cfg['window_halfwidth_ppm']))
        radius = min(int(half / dx), c, len(x) - c - 1)
        if radius < 4:
            continue
        centers.append(int(c)); windows.append(np.arange(c - radius, c + radius + 1))
        descriptions.append({'center_ppm': float(x[c]), 'left_ppm': float(x[c-radius]),
            'right_ppm': float(x[c+radius]), 'forced_target_window': bool(c == forced),
            'center_full_fft_index': int(indices[c]), 'radius_scoring_points': radius})
    quiet = np.flatnonzero(mag <= np.quantile(mag, cfg['quiet_fraction']))
    design = np.column_stack((np.ones(len(x)), (x - x.mean()) / np.ptp(x)))
    inverse = np.linalg.pinv(design[quiet])
    scale = max(float(np.quantile(mag, .99)), noise)
    metadata = {'configuration': cfg, 'fft_sha256': hashlib.sha256(fft.tobytes()).hexdigest(),
        'axis_sha256': hashlib.sha256(ppm.tobytes()).hexdigest(), 'fft_points': len(fft),
        'scoring_stride': stride, 'scoring_points': len(x), 'noise_complex_difference': noise,
        'noise_estimator': 'median |complex first difference| / sqrt(2 ln 2); descriptive robust scale, not production S/N noise',
        'windows': descriptions, 'quiet_full_indices': indices[quiet].tolist(),
        'p1_constraint_warning': bool(len(centers) < 2 or (np.ptp([x[c] for c in centers]) if centers else 0) < 1.0),
        'limitations': 'Magnitude centers/overlapping multiplets may not be true symmetric real-peak centers. Imaginary absolute energy is descriptive, not expected to vanish.'}
    return QualityContext(x, indices, z, len(fft), noise, centers, windows, quiet,
                          design, inverse, scale, metadata)


def score_samples(samples, context):
    """Dimensionless shape terms on identical windows; no downstream override."""
    z = np.asarray(samples, dtype=complex)
    if z.shape != context.fft.shape or not np.all(np.isfinite(z)):
        raise ValueError('Quality samples do not match context')
    coefficients = context.baseline_inverse @ z[context.quiet]
    trend = context.baseline_design @ coefficients
    negative = real_total = imag_total = mag_total = even_imag = asym_num = asym_den = 0.0
    odd_real_energy = real_energy = imag_energy = 0.0
    count = 0
    for window in context.windows:
        local = z[window].copy(); n = len(local); edge = max(2, n // 8)
        left = np.median(local[:edge].real) + 1j*np.median(local[:edge].imag)
        right = np.median(local[-edge:].real) + 1j*np.median(local[-edge:].imag)
        local -= np.linspace(left, right, n)  # diagnostic nuisance baseline only
        r = local.real; d = local.imag; reverse = local[::-1]
        r_odd = (r - reverse.real) / 2; d_even = (d + reverse.imag) / 2
        significant = np.maximum(np.abs(r) - 3 * context.noise, 0)
        negative += float(np.maximum(-r - 3 * context.noise, 0).sum())
        real_total += float(significant.sum()); imag_total += float(np.abs(d).sum())
        mag_total += float(np.abs(local).sum())
        even_imag += float(np.abs(d_even).sum())
        asym_num += float(np.abs(r - reverse.real).sum())
        asym_den += float((np.abs(r) + np.abs(reverse.real)).sum())
        odd_real_energy += float(np.dot(r_odd, r_odd)); real_energy += float(np.dot(r, r))
        imag_energy += float(np.dot(d, d)); count += n
    epsilon = context.noise * max(count, 1)
    baseline_raw = float(np.median(np.abs(trend.real))) / context.scale
    slope_raw = float(abs(coefficients[1].real)) / context.scale
    return {'negative_area_fraction': negative / max(real_total, epsilon),
        'imaginary_residual': imag_total / max(mag_total, epsilon),
        'imaginary_to_real_ratio': imag_total / max(real_total, epsilon),
        'imaginary_rms': float(np.sqrt(imag_energy / max(count, 1))),
        'imaginary_absolute_integral': imag_total * float(np.median(np.diff(context.ppm))),
        'even_imaginary_fraction': even_imag / max(mag_total, epsilon),
        'peak_asymmetry': asym_num / max(asym_den, epsilon),
        'dispersive_metric': float(np.sqrt(odd_real_energy / max(real_energy, epsilon**2 / max(count, 1)))),
        'baseline_offset_normalized': baseline_raw,
        'baseline_slope_normalized': slope_raw,
        'baseline_penalty': (baseline_raw + slope_raw) / (1 + baseline_raw + slope_raw),
        'selected_resonances': len(context.windows), 'p1_constraint_warning': context.metadata['p1_constraint_warning']}


def phase_samples(parameters, context):
    p0, p1 = map(float, parameters)
    return context.fft * np.exp(1j * np.deg2rad(p0 + p1 * context.indices / context.full_points))


def score_spectrum(phased, context):
    phased = np.asarray(phased)
    if phased.shape != (context.full_points,):
        raise ValueError('Spectrum length differs from original FFT')
    return score_samples(phased[context.indices], context)
