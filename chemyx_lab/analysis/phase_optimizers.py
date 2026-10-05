"""Explicitly custom validation comparators; not replacements for production."""
from dataclasses import asdict, dataclass
import numpy as np
from scipy.optimize import minimize
from .phase_quality import phase_samples, score_samples

COMBINED_WEIGHTS = {'negative': 0.25, 'even_imaginary': 0.25, 'asymmetry': 0.25, 'baseline': 0.25}
OPTIMIZER_CONFIG = {'bounds_deg': [[-180., 180.], [-360., 360.]],
    'grid_p0_step_deg': 45., 'grid_p1_step_deg': 90., 'local_starts': 4,
    'local_method': 'Powell', 'maxiter': 180, 'maxfev': 6000,
    'xtol': 1e-5, 'ftol': 1e-9, 'seed': 'none; fixed grid, stable ordering'}


def components(metrics):
    return {'negative': metrics['negative_area_fraction'],
        'even_imaginary': metrics['even_imaginary_fraction'],
        'asymmetry': metrics['peak_asymmetry'], 'baseline': metrics['baseline_penalty']}


def objective(parameters, context, method, weights=None):
    metrics = score_samples(phase_samples(parameters, context), context)
    terms = components(metrics)
    if method == 'combined_objective_v1':
        weights = COMBINED_WEIGHTS if weights is None else weights
        if set(weights) != set(terms) or any(not np.isfinite(w) or w < 0 for w in weights.values()) or sum(weights.values()) <= 0:
            raise ValueError('Weights must be finite nonnegative values for all four components')
        total = sum(weights.values())
        return float(sum(weights[k] * terms[k] / total for k in terms))
    if method == 'symmetry_objective':
        return float(terms['asymmetry'] + .1 * terms['negative'])
    raise ValueError('Unavailable phase optimizer: ' + method)


def optimize_phase(context, method, starting_phase=(0., 0.), config=None, weights=None):
    cfg = {**OPTIMIZER_CONFIG, **(config or {})}
    status = {'method': method, 'configuration': cfg, 'starting_phase_direct_deg': list(map(float, starting_phase)),
        'weights': COMBINED_WEIGHTS if method == 'combined_objective_v1' and weights is None else weights,
        'source': 'custom implementation; not APSL, DISPA or Hilbert phasing',
        'success': False, 'p0_deg': None, 'p1_deg': None}
    if method not in ('combined_objective_v1', 'symmetry_objective'):
        raise ValueError('Unavailable phase optimizer: ' + method)
    if not context.windows:
        status.update(message='No fixed resonance windows; no result fabricated', status='unavailable_signal')
        return status
    lower, upper = np.asarray(cfg['bounds_deg']).T
    def evaluate(p):
        return objective(p, context, method, weights)
    grid = [np.array([p0, p1]) for p0 in np.arange(lower[0], upper[0]+.1, cfg['grid_p0_step_deg'])
            for p1 in np.arange(lower[1], upper[1]+.1, cfg['grid_p1_step_deg'])]
    start = np.clip(np.asarray(starting_phase, dtype=float), lower, upper)
    grid.append(start)
    ranked = sorted(enumerate(grid), key=lambda item: (evaluate(item[1]), item[0]))
    starts = [p for _, p in ranked[:cfg['local_starts']]]
    records = []; best = None
    try:
        for p in starts:
            result = minimize(evaluate, p, method=cfg['local_method'], bounds=cfg['bounds_deg'],
                options={k: cfg[k] for k in ('maxiter','maxfev','xtol','ftol')})
            finite = bool(np.all(np.isfinite(result.x)) and np.isfinite(result.fun))
            records.append({'start': p.tolist(), 'success': bool(result.success), 'message': str(result.message),
                'objective': float(result.fun) if finite else None, 'nit': int(result.nit), 'nfev': int(result.nfev),
                'p0_deg': float(result.x[0]) if finite else None, 'p1_deg': float(result.x[1]) if finite else None})
            if finite and (best is None or result.fun < best.fun):
                best = result
    except Exception as exc:
        status.update(status='optimizer_exception', message=str(exc), local_runs=records)
        return status
    if best is None:
        status.update(status='nonfinite_optimizer_result', message='No finite optimized phase', local_runs=records)
        return status
    status.update(success=bool(best.success), status='converged' if best.success else 'nonconverged',
        message=str(best.message), p0_deg=float(best.x[0]), p1_deg=float(best.x[1]), inverse_phase=False,
        objective_value=float(best.fun), objective_components=components(score_samples(phase_samples(best.x, context), context)),
        iterations=int(best.nit), function_evaluations=int(sum(r['nfev'] for r in records)), local_runs=records,
        phase_convention='direct exp(+i*pi/180*(P0+P1*k/N)); index-zero pivot',
        p1_constraint_warning=context.metadata['p1_constraint_warning'])
    return status


def ernst_integral_p0(ppm, fft):
    """Ernst 1969 equation 25 using measured quadrature; restricted P0 only."""
    integral = np.trapezoid(np.asarray(fft), np.asarray(ppm))
    norm = float(np.trapezoid(np.abs(fft), ppm))
    if norm <= 0 or abs(integral) < norm * 1e-12:
        return {'method': 'ernst_integral_p0', 'success': False, 'status': 'integral_cancellation',
                'p0_deg': None, 'p1_deg': None, 'message': 'Global complex integral too small to define phase'}
    p0 = -float(np.rad2deg(np.angle(integral)))
    return {'method': 'ernst_integral_p0', 'success': True, 'status': 'analytic', 'p0_deg': p0,
        'p1_deg': 0., 'inverse_phase': False, 'iterations': 0, 'function_evaluations': 1,
        'source': 'Ernst 1969 equation 25; atan2 branch chooses positive absorption integral',
        'integration_ppm_bounds': [float(np.min(ppm)), float(np.max(ppm))],
        'integral_real': float(integral.real), 'integral_imaginary': float(integral.imag),
        'integral_to_absolute_area_ratio': float(abs(integral)/norm),
        'limitations': 'P0 only, P1=0; global baseline and finite spectral limits affect integral; no Hilbert reconstruction because measured quadrature exists'}
