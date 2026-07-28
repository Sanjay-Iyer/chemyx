"""Whole-spectrum similarity metrics and optional PCA.

These operate on a *series* of spectra placed on one common, aligned ppm grid
(NMR spectra acquired at different times can differ slightly in digital
resolution; comparing them point-by-point requires a shared axis).  The
metrics answer "how much did the whole spectrum change from the first / previous
one" without committing to any peak model.

PCA is offered as a **variance-decomposition** tool only: its scores and
loadings summarise where a series varies most, which is useful for spotting
drift or regime changes, but a principal component is a statistical axis, not a
chemical species.  Chemical identification requires the peak-level analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


def _np():
    import numpy as np

    return np


def _trapz(np, y, x):
    integrator = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return integrator(y, x)


@dataclass(frozen=True)
class CommonGrid:
    """A shared ppm grid plus every spectrum resampled onto it (row per spectrum)."""

    ppm: object  # 1-D numpy array, ascending
    matrix: object  # 2-D numpy array, shape (n_spectra, n_points)


def common_grid(
    spectra: Sequence[tuple[Sequence[float], Sequence[float]]],
) -> CommonGrid:
    """Resample every ``(ppm, intensity)`` spectrum onto one overlapping grid.

    The grid spans the ppm range common to all inputs, sampled at the finest
    (smallest median spacing) of the inputs.  Each spectrum is linearly
    interpolated after sorting to ascending ppm, so descending NMR axes are
    handled transparently.  Raises ``ValueError`` when the inputs do not
    overlap.
    """
    np = _np()
    if len(spectra) == 0:
        raise ValueError("no spectra provided")
    los, his, spacings = [], [], []
    prepared = []
    for ppm, intensity in spectra:
        p = np.asarray(list(ppm), dtype=float)
        y = np.asarray(list(intensity), dtype=float)
        order = np.argsort(p)
        p, y = p[order], y[order]
        finite = np.isfinite(p) & np.isfinite(y)
        p, y = p[finite], y[finite]
        if p.size < 2:
            raise ValueError("a spectrum has fewer than two finite points")
        los.append(float(p[0]))
        his.append(float(p[-1]))
        spacings.append(float(np.median(np.diff(p))))
        prepared.append((p, y))
    lo = max(los)
    hi = min(his)
    if not hi > lo:
        raise ValueError("spectra do not share an overlapping ppm range")
    step = min(s for s in spacings if s > 0)
    n_points = max(2, int(round((hi - lo) / step)) + 1)
    grid = np.linspace(lo, hi, n_points)
    matrix = np.vstack([np.interp(grid, p, y) for p, y in prepared])
    return CommonGrid(ppm=grid, matrix=matrix)


def _cosine(a, b) -> float:
    np = _np()
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na <= 0 or nb <= 0:
        return float("nan")
    return float(np.dot(a, b) / (na * nb))


def _pearson(a, b) -> float:
    np = _np()
    if a.size < 2:
        return float("nan")
    sa, sb = float(np.std(a)), float(np.std(b))
    if sa <= 0 or sb <= 0:
        return float("nan")
    return float(np.mean((a - a.mean()) * (b - b.mean())) / (sa * sb))


def spectral_similarity(grid: CommonGrid) -> list[dict[str, float]]:
    """Per-spectrum similarity to the first and previous spectrum.

    Columns per row:

    * ``correlation_to_first`` / ``correlation_to_previous`` -- Pearson r.
    * ``cosine_similarity_to_first`` -- cosine of the angle between raw vectors.
    * ``spectral_rmse_to_first`` -- root-mean-square point difference.
    * ``integrated_absolute_difference`` -- trapezoid of ``|s_i - s_1|`` over ppm.
    * ``spectral_angle`` -- ``arccos(cosine)`` in degrees.

    Row 0 (the reference) has correlation/cosine 1 and zero differences.
    """
    np = _np()
    m = grid.matrix
    x = grid.ppm
    first = m[0]
    rows: list[dict[str, float]] = []
    for i in range(m.shape[0]):
        row = m[i]
        cos_first = _cosine(row, first)
        angle = (
            float(np.degrees(np.arccos(np.clip(cos_first, -1.0, 1.0))))
            if np.isfinite(cos_first)
            else float("nan")
        )
        rows.append(
            {
                "correlation_to_first": _pearson(row, first) if i > 0 else 1.0,
                "correlation_to_previous": _pearson(row, m[i - 1]) if i > 0 else 1.0,
                "cosine_similarity_to_first": cos_first,
                "spectral_rmse_to_first": float(np.sqrt(np.mean((row - first) ** 2))),
                "integrated_absolute_difference": float(
                    _trapz(np, np.abs(row - first), x)
                ),
                "spectral_angle": angle,
            }
        )
    return rows


@dataclass(frozen=True)
class PCAResult:
    """Mean-centered PCA of a spectrum series (rows = spectra)."""

    scores: object  # (n_spectra, k)
    loadings: object  # (k, n_points)
    explained_variance_ratio: object  # (k,)
    mean: object  # (n_points,)
    n_components: int


def pca(grid: CommonGrid, *, n_components: int = 3) -> PCAResult:
    """Mean-centered PCA via SVD; ``k`` is clamped to the data rank.

    Returns scores (per spectrum), loadings (per component), and the fraction of
    total variance each component explains.  Requires at least two spectra.
    """
    np = _np()
    m = np.asarray(grid.matrix, dtype=float)
    if m.shape[0] < 2:
        raise ValueError("PCA needs at least two spectra")
    mean = m.mean(axis=0)
    centered = m - mean
    u, s, vt = np.linalg.svd(centered, full_matrices=False)
    k = int(min(n_components, s.size))
    variance = s ** 2
    total = float(variance.sum()) or 1.0
    return PCAResult(
        scores=u[:, :k] * s[:k],
        loadings=vt[:k],
        explained_variance_ratio=variance[:k] / total,
        mean=mean,
        n_components=k,
    )
