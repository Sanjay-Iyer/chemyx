"""Method-robustness refinement of the June 9 algorithm-validation package.

Question: how much does phase-algorithm choice change the scientific conclusion?

Inputs are this package's saved tables and arrays plus read-only raw JCAMP headers. Nothing here
changes FFT processing, a phase estimate, an area, or the historical completion rule; the rule is
replayed unchanged through chemyx_lab.analysis.completion. Candidate endpoint metrics are
exploratory research metrics and never replace the production endpoint. No phase method is treated
as ground truth: comparisons use the cross-method median of the seven automatic methods.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy import stats

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from chemyx_lab.analysis import additional_phase_methods as apm
from chemyx_lab.analysis.completion import _current_decision, detect_completion
from chemyx_lab.analysis.phase_quality import phase_samples, prepare_context, score_samples, score_spectrum
from chemyx_lab.analysis.target_peak_config import CompletionConfig

AUTOMATIC = ('production', 'acme', 'peak_minima', 'combined_objective_v1', 'symmetry_objective',
             'ernst_integral_p0', 'deep_phaser')
METHODS = ('unphased', *AUTOMATIC)
SHORT = {'unphased': 'Unphased', 'production': 'DX metadata', 'acme': 'ACME', 'peak_minima': 'Peak minima',
         'combined_objective_v1': 'Combined', 'symmetry_objective': 'Symmetry', 'ernst_integral_p0': 'Ernst P0',
         'deep_phaser': 'DEEP'}
STEMS = {'unphased': 'unphased', 'production': 'dx_metadata', 'acme': 'acme', 'peak_minima': 'peak_minima',
         'combined_objective_v1': 'combined', 'symmetry_objective': 'symmetry', 'ernst_integral_p0': 'ernst',
         'deep_phaser': 'deep'}
COLORS = {m: apm.COLORS[m] for m in METHODS}  # identical method identity to every other package figure
MARKERS = {'unphased': 'o', 'production': 's', 'acme': '^', 'peak_minima': 'v', 'combined_objective_v1': 'D',
           'symmetry_objective': 'P', 'ernst_integral_p0': 'X', 'deep_phaser': '*'}
SERIES = ('picked', 'fixed')
SERIES_ID = {'picked': 'picked_moving_boundary', 'fixed': 'completion_fixed_window'}
SERIES_LABEL = {'picked': 'Picked target area (moving boundaries; the Fig. 05 measurement)',
                'fixed': 'Fixed-window completion area (5.70–5.90 ppm; historical endpoint input)'}
SERIES_SHORT = {'picked': 'Picked target area', 'fixed': 'Fixed-window completion area'}
SHAPE = ('negative_area_fraction', 'dispersive_metric', 'peak_asymmetry', 'even_imaginary_fraction')
CONTAMINATION = ('dispersive_metric', 'even_imaginary_fraction')
METRIC_LABEL = {'negative_area_fraction': 'negative area', 'dispersive_metric': 'odd-Real (dispersive)',
                'peak_asymmetry': 'asymmetry', 'even_imaginary_fraction': 'even-Imaginary'}
# Terms each optimizer directly or partly minimizes. Favourable scores on these are in-sample evidence.
OBJECTIVE_OVERLAP = {
    'combined_objective_v1': {'negative_area_fraction': 'direct', 'even_imaginary_fraction': 'direct',
                              'peak_asymmetry': 'direct', 'dispersive_metric': 'L2 analogue of its asymmetry term'},
    'symmetry_objective': {'peak_asymmetry': 'direct', 'dispersive_metric': 'L2 analogue of its asymmetry term',
                           'negative_area_fraction': 'direct (0.1 weight)'},
    'acme': {'negative_area_fraction': 'negative-intensity penalty'},
    'peak_minima': {'dispersive_metric': 'partly: equalizes minima beside the single tallest peak',
                    'peak_asymmetry': 'partly: equalizes minima beside the single tallest peak'}}
OBJECTIVE_TEXT = {
    'production': 'none (stored vendor phase)', 'acme': 'entropy + negative-intensity penalty',
    'peak_minima': 'equal minima beside the tallest peak',
    'combined_objective_v1': '0.25 each: negative area, even Imaginary, asymmetry, baseline',
    'symmetry_objective': 'asymmetry + 0.1 negative area', 'ernst_integral_p0': 'integral criterion, P0 only',
    'deep_phaser': 'neural network (COLMARvista DEEP)'}
OBJECTIVE_OVERLAP_REGIONS = {'peak_minima': ('2ppm',)}  # its objective acts on the single tallest band: 2 ppm in every acquisition
CONSISTENT = 6          # >=6 of 8 acquisitions in one direction counts as a consistent change
DECISION_GATE = 5.0     # historical percent gate, used only to anchor descriptive categories
INK, INK2, MUTED, GRID = '#0b0b0b', '#52514e', '#898781', '#e1e0d9'
ACCENT, ACCENT_DARK, NEUTRAL = '#2a78d6', '#184f95', '#f0efec'
REGION_METRIC_COLORS = {('2ppm', 'dispersive_metric'): '#184f95', ('2ppm', 'even_imaginary_fraction'): '#6da7ec',
                        ('7ppm', 'dispersive_metric'): '#d95926', ('7ppm', 'even_imaginary_fraction'): '#eda100'}
OUTCOME_COLORS = ('#104281', '#2a78d6', '#86b6ef')  # validated ordinal ramp: earlier qualification is darker


# ---------------------------------------------------------------------------- small I/O helpers
def rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def plain(value):
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def write_rows(path, table):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in table for k in r))
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader()
        for r in table:
            w.writerow({k: (json.dumps(plain(r[k])) if isinstance(r.get(k), (dict, list, tuple))
                            else plain(r.get(k))) for k in keys})


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plain(value), indent=2, allow_nan=False), encoding='utf-8')


def wrap(angle):
    return (angle+180.) % 360.-180.


# ---------------------------------------------------------------------------- inputs (fail closed)
def load(base):
    base = Path(base)
    manifest = rows(base/'input_manifest.csv')
    if len(manifest) != 8:
        raise ValueError('Expected the eight June 9 acquisitions')
    if any(r['timestamp_source'] != 'LONG DATE header' for r in manifest):
        raise ValueError('Fail closed: every plotted time must come from the JCAMP LONG DATE header')
    order = [r['acquisition_id'] for r in manifest]
    t = np.array([float(r['elapsed_hours']) for r in manifest])
    if np.any(np.diff(t) <= 0):
        raise ValueError('Acquisitions are not in metadata time order')
    ts = rows(base/'timeseries/target_area_timeseries.csv')
    if [r['acquisition_id'] for r in ts] != order:
        raise ValueError('Target-area table order differs from the manifest')
    picked = {m: np.array([float(r[STEMS[m]+'_area_au_ppm']) for r in ts]) for m in METHODS}
    fixed_rows = rows(base/'timeseries/completion_fixed_window_timeseries.csv')
    fixed, se, fixed_q = {}, {}, {}
    for m in METHODS:
        rr = [r for r in fixed_rows if r['method'] == m]
        if [r['acquisition_id'] for r in rr] != order:
            raise ValueError(f'Fixed-window table order differs from the manifest for {m}')
        fixed[m] = np.array([float(r['area']) for r in rr])
        se[m] = np.array([float(r['area_standard_error']) for r in rr])
        fixed_q[m] = [r['completion_quality_pass'] == 'True' for r in rr]
    per = {(r['acquisition_id'], r['method']): r for r in rows(base/'tables/per_acquisition_method_metrics.csv')}
    picked_q = {m: [per[(i, m)]['target_qc_pass'] == 'True' and per[(i, m)]['target_detected'] == 'True' for i in order]
                for m in METHODS}
    for table in (picked, fixed, se):
        for m, values in table.items():
            if values.shape != (8,) or not np.all(np.isfinite(values)):
                raise ValueError(f'Missing or non-finite value for {m}; no substitute is made')
    config = json.loads((base/'logs/historical_target_config.json').read_text(encoding='utf-8-sig'))
    return SimpleNamespace(base=base, manifest=manifest, order=order, t=t,
        clock=[r['acquisition_time'][11:19] for r in manifest], dataset=manifest[0]['dataset_display_name'],
        picked=picked, fixed=fixed, se=se, quality={'picked': picked_q, 'fixed': fixed_q}, metrics=per,
        improvements=rows(base/'tables/improvement_vs_unphased.csv'), summary=rows(base/'tables/method_summary.csv'),
        completion=rows(base/'plateau_analysis/completion_by_method.csv'),
        trace=rows(base/'plateau_analysis/full_criterion_trace.csv'),
        cfg=CompletionConfig(**config['completion']), minimum_snr=float(config['minimum_snr']))


def series(d, key):
    return d.picked if key == 'picked' else d.fixed


def weak_signal(d):
    """Acquisitions whose median automatic-method target S/N is below the historical minimum S/N."""
    snr = np.array([[float(d.metrics[(i, m)]['target_snr']) for i in d.order] for m in AUTOMATIC])
    return np.median(snr, axis=0) < d.minimum_snr


def late_interval_facts(d):
    """Area changes over the last two intervals (after the first evaluable acquisition), per automatic method."""
    pct = {key: {m: percent_changes(series(d, key)[m]) for m in AUTOMATIC} for key in SERIES}
    risers = [m for m in AUTOMATIC if pct['fixed'][m][-2] > 0]
    droppers = [m for m in AUTOMATIC if pct['fixed'][m][-1] <= -10 and pct['picked'][m][-1] <= -10]
    drops = [-pct[key][m][-1] for m in droppers for key in SERIES]
    others = '; '.join(f'{SHORT[m]}: {pct["picked"][m][-1]:+.1f}% picked, {pct["fixed"][m][-1]:+.1f}% fixed window'
                       for m in AUTOMATIC if m not in droppers)
    text = (f'the fixed-window area rises {min(pct["fixed"][m][-2] for m in risers):.1f}–{max(pct["fixed"][m][-2] for m in risers):.1f}% at '
            f'{d.clock[-2][:5]} for {len(risers)} of 7 automatic methods, and for {len(droppers)} of 7 both area definitions drop '
            f'{min(drops):.0f}–{max(drops):.0f}% at {d.clock[-1][:5]}' + (f' ({others})' if others else ''))
    assert len(risers) >= 6 and len(droppers) >= 6, 'late departures no longer apply to most methods; revise the narrative'
    words = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven']
    exceptions = '; '.join(f'{SHORT[m]} declines far less at {d.clock[-1][:5]} ({pct["picked"][m][-1]:+.1f}% picked, {pct["fixed"][m][-1]:+.1f}% '
                           f'fixed window, versus {-min(drops):.0f}% to {-max(drops):.0f}% for the others)' for m in AUTOMATIC if m not in droppers)
    agree = (f'{words[len(droppers)]} of the seven automatic methods\' normalized trajectories agree on the rise, the plateau-like region and the '
             'final decline' + (f'; {exceptions}' if exceptions else ''))
    return SimpleNamespace(risers=risers, droppers=droppers, text=text, agree=agree)


def endpoint_window(d):
    """First acquisition the historical rule can evaluate and the start of its recent window there."""
    c = d.cfg
    first = next(i for i in range(len(d.t)) if i+1 >= c.minimum_observations and d.t[i]-d.t[0] >= c.minimum_elapsed_hours)
    return first, max(0, first-c.recent_window+1)


# ---------------------------------------------------------------------------- Tasks 4-8: target agreement
def spread(d):
    table, summary = [], {}
    for key in SERIES:
        values = series(d, key); A = np.vstack([values[m] for m in AUTOMATIC])
        med, lo, hi = np.median(A, 0), A.min(0), A.max(0); mean, sd = A.mean(0), A.std(0, ddof=1)
        for i, ident in enumerate(d.order):
            u = values['unphased'][i]; both = np.append(A[:, i], u)
            table.append({'area_series': SERIES_ID[key], 'acquisition_id': ident,
                'acquisition_time': d.manifest[i]['acquisition_time'], 'elapsed_hours': d.t[i],
                'timestamp_source': d.manifest[i]['timestamp_source'], 'n_automatic_methods': len(AUTOMATIC),
                'median_area_au_ppm': med[i], 'minimum_area_au_ppm': lo[i], 'maximum_area_au_ppm': hi[i],
                'method_at_minimum': SHORT[AUTOMATIC[int(np.argmin(A[:, i]))]],
                'method_at_maximum': SHORT[AUTOMATIC[int(np.argmax(A[:, i]))]],
                'absolute_range_au_ppm': hi[i]-lo[i], 'percent_range_of_median': 100*(hi[i]-lo[i])/med[i],
                'mean_area_au_ppm': mean[i], 'sd_area_au_ppm': sd[i], 'cv_percent': 100*sd[i]/mean[i],
                'unphased_area_au_ppm': u, 'unphased_deviation_percent_from_median': 100*(u-med[i])/med[i],
                'range_including_unphased_au_ppm': both.max()-both.min(),
                'range_including_unphased_percent_of_automatic_median': 100*(both.max()-both.min())/med[i],
                **{f'{STEMS[m]}_area_au_ppm': values[m][i] for m in METHODS}})
        summary[key] = {'median': med, 'minimum': lo, 'maximum': hi, 'range': hi-lo,
                        'range_pct': 100*(hi-lo)/med, 'cv_pct': 100*sd/mean}
    return table, summary


def chemistry_vs_algorithm(d, sp):
    first, start = endpoint_window(d); late = np.arange(start, len(d.t))
    summary, intervals = [], []
    for key in SERIES:
        s = sp[key]; med, rng = s['median'], s['range']
        D = med.max()-med.min(); Smed, Smax = float(np.median(rng)), float(rng.max())
        Dl = med[late].max()-med[late].min(); Sl = float(np.median(rng[late]))
        Sw = float(np.median(rng[~weak_signal(d)]))  # spread statistic without the weak acquisition; the run's range keeps it
        summary.append({'area_series': SERIES_ID[key],
            'sequence_dynamic_range_au_ppm': D, 'sequence_minimum_time': d.clock[int(np.argmin(med))],
            'sequence_maximum_time': d.clock[int(np.argmax(med))],
            'median_cross_method_range_au_ppm': Smed, 'maximum_cross_method_range_au_ppm': Smax,
            'maximum_range_time': d.clock[int(np.argmax(rng))],
            'R_dynamic_range_over_median_algorithm_range': D/Smed, 'R_conservative_over_maximum_algorithm_range': D/Smax,
            'R_excluding_weak_signal': D/Sw,
            'endpoint_window_start': d.clock[start], 'endpoint_window_end': d.clock[-1],
            'endpoint_window_dynamic_range_au_ppm': Dl, 'endpoint_window_median_cross_method_range_au_ppm': Sl,
            'R_endpoint_window': Dl/Sl,
            'definition': ('D = max-min of the cross-method median trajectory; algorithm range = max-min of the seven '
                           'automatic methods at one acquisition; R = D / median algorithm range; R_conservative = D / largest algorithm '
                           'range; R_excluding_weak_signal = D over all acquisitions / median algorithm range over acquisitions without a weak '
                           f'target signal ({", ".join(c for c, w in zip(d.clock, weak_signal(d)) if w)} excluded from the spread statistic only); '
                           'R_endpoint_window = max-min of the median trajectory inside the endpoint window / median algorithm range there. The '
                           'endpoint window is every acquisition entering a recent window the historical rule can evaluate. Descriptive ratios; '
                           'no significance is implied.')})
        values = series(d, key); A = np.vstack([values[m] for m in AUTOMATIC])
        delta = np.diff(A, axis=1); pct = 100*delta/A[:, :-1]
        ud = np.diff(values['unphased']); up = 100*ud/values['unphased'][:-1]
        for j in range(1, len(d.t)):
            dj, pj = delta[:, j-1], pct[:, j-1]
            signs = np.sign(dj)
            intervals.append({'area_series': SERIES_ID[key], 'interval': f'{d.clock[j-1]}→{d.clock[j]}',
                'from_time': d.clock[j-1], 'to_time': d.clock[j], 'in_endpoint_window': bool(j > start),
                'median_change_au_ppm': np.median(dj), 'minimum_change_au_ppm': dj.min(), 'maximum_change_au_ppm': dj.max(),
                'cross_method_change_range_au_ppm': dj.max()-dj.min(), 'median_percent_change': np.median(pj),
                'minimum_percent_change': pj.min(), 'maximum_percent_change': pj.max(),
                'cross_method_percent_range_pp': pj.max()-pj.min(),
                'method_at_minimum_percent': SHORT[AUTOMATIC[int(np.argmin(pj))]],
                'method_at_maximum_percent': SHORT[AUTOMATIC[int(np.argmax(pj))]],
                'abs_median_change_over_cross_method_range': abs(np.median(dj))/(dj.max()-dj.min()),
                'direction_agreement': ('all increase' if np.all(signs > 0) else 'all decrease' if np.all(signs < 0)
                                        else 'methods disagree on direction'),
                'unphased_change_au_ppm': ud[j-1], 'unphased_percent_change': up[j-1]})
    return summary, intervals


def normalized(d):
    table, agreement = [], []
    for key in SERIES:
        values = series(d, key)
        frac = {m: values[m]/values[m].max() for m in METHODS}
        scaled = {m: (values[m]-values[m].min())/(values[m].max()-values[m].min()) for m in METHODS}
        F = np.vstack([frac[m] for m in AUTOMATIC]); medf = np.median(F, 0)
        for m in METHODS:
            for i, ident in enumerate(d.order):
                table.append({'area_series': SERIES_ID[key], 'method': m, 'display_method': SHORT[m],
                    'acquisition_id': ident, 'acquisition_time': d.manifest[i]['acquisition_time'],
                    'area_au_ppm': values[m][i], 'fraction_of_own_maximum': frac[m][i],
                    'scaled_0_to_1_by_own_range': scaled[m][i],
                    'fraction_deviation_from_median_pp': 100*(frac[m][i]-medf[i]),
                    'time_of_own_maximum': d.clock[int(np.argmax(values[m]))]})
        for i, ident in enumerate(d.order):
            agreement.append({'area_series': SERIES_ID[key], 'acquisition_id': ident,
                'acquisition_time': d.manifest[i]['acquisition_time'], 'median_fraction_of_maximum': medf[i],
                'minimum_fraction': F[:, i].min(), 'maximum_fraction': F[:, i].max(),
                'cross_method_range_pp': 100*(F[:, i].max()-F[:, i].min()),
                'method_at_minimum': SHORT[AUTOMATIC[int(np.argmin(F[:, i]))]],
                'method_at_maximum': SHORT[AUTOMATIC[int(np.argmax(F[:, i]))]]})
    return table, agreement


def deviations(d, sp):
    weak = weak_signal(d); table = []
    for key in SERIES:
        med = sp[key]['median']; values = series(d, key)
        for m in METHODS:
            for i, ident in enumerate(d.order):
                table.append({'area_series': SERIES_ID[key], 'method': m, 'display_method': SHORT[m],
                    'included_in_cross_method_median': m in AUTOMATIC, 'acquisition_id': ident,
                    'acquisition_time': d.manifest[i]['acquisition_time'], 'area_au_ppm': values[m][i],
                    'cross_method_median_au_ppm': med[i], 'deviation_percent_from_median': 100*(values[m][i]-med[i])/med[i],
                    'weak_signal_acquisition': bool(weak[i])})
    return table


def correlation_pair(x, y):
    return float(np.corrcoef(x, y)[0, 1]), float(stats.spearmanr(x, y).statistic)


def target_stability(d, sp, norm_rows):
    """Per-method target agreement with the ensemble; closeness is centrality, not accuracy."""
    weak = weak_signal(d); out = {}
    snr = {m: np.array([float(d.metrics[(i, m)]['target_snr']) for i in d.order]) for m in METHODS}
    ppm = {m: np.array([float(d.metrics[(i, m)]['target_peak_ppm']) for i in d.order]) for m in METHODS}
    detected = {m: sum(d.metrics[(i, m)]['target_detected'] == 'True' for i in d.order) for m in METHODS}
    snr_med = np.median([snr[m] for m in AUTOMATIC], 0); ppm_med = np.median([ppm[m] for m in AUTOMATIC], 0)
    for m in METHODS:
        row = {'method': m, 'display_method': SHORT[m],
               'role': 'automatic phase method' if m in AUTOMATIC else 'no-phase reference (not in median)',
               'target_detection': f'{detected[m]}/8', 'detection_count': detected[m]}
        for key in SERIES:
            values = series(d, key); med = sp[key]['median']; dev = 100*(values[m]-med)/med
            others = [o for o in AUTOMATIC if o != m]
            loo = np.median([values[o] for o in others], 0)
            r, rho = correlation_pair(values[m], med); r_loo, rho_loo = correlation_pair(values[m], loo)
            frac = np.array([x['fraction_deviation_from_median_pp'] for x in norm_rows
                             if x['area_series'] == SERIES_ID[key] and x['method'] == m])
            p = 'picked' if key == 'picked' else 'fixed'
            row.update({f'{p}_median_abs_deviation_pct': float(np.median(np.abs(dev))),
                f'{p}_median_signed_deviation_pct': float(np.median(dev)),
                f'{p}_max_abs_deviation_pct': float(np.max(np.abs(dev))),
                f'{p}_max_abs_deviation_time': d.clock[int(np.argmax(np.abs(dev)))],
                f'{p}_max_abs_deviation_excluding_weak_pct': float(np.max(np.abs(dev[~weak]))),
                f'{p}_pearson_r_vs_median': r, f'{p}_spearman_rho_vs_median': rho,
                f'{p}_pearson_r_vs_leave_one_out_median': r_loo, f'{p}_spearman_rho_vs_leave_one_out_median': rho_loo,
                f'{p}_max_abs_normalized_shape_deviation_pp': float(np.max(np.abs(frac))),
                f'{p}_time_of_maximum': d.clock[int(np.argmax(values[m]))]})
        sdev = 100*(snr[m]-snr_med)/snr_med
        row.update({'snr_median_abs_deviation_pct': float(np.median(np.abs(sdev))),
                    'snr_max_abs_deviation_pct': float(np.max(np.abs(sdev))),
                    'ppm_median_abs_offset_from_median': float(np.median(np.abs(ppm[m]-ppm_med))),
                    'ppm_max_abs_offset_from_median': float(np.max(np.abs(ppm[m]-ppm_med)))})
        shape = row['picked_max_abs_normalized_shape_deviation_pp']
        row['target_trend_category'] = ('Strong' if shape <= DECISION_GATE/2 and detected[m] == 8 else
                                        'Acceptable' if shape <= DECISION_GATE else 'Mixed' if shape <= 2*DECISION_GATE else 'Weak')
        out[m] = row
    return out


# ---------------------------------------------------------------------------- Tasks 2A and 9: phase shape
def phase_quality(d):
    summary = {r['method']: r for r in d.summary}; out = []
    for m in AUTOMATIC:
        for region, prefix in (('2ppm', 'p2'), ('7ppm', 'p7')):
            for metric in SHAPE:
                dd = [r for r in d.improvements if r['method'] == m and r['region'] == region and r['metric'] == prefix+'_'+metric]
                pc = np.array([float(r['percent_change']) for r in dd if r['percent_change'] not in ('', None)])
                median = float(np.median(pc)) if pc.size else float('nan')
                saved = summary[m][f'{region}_{metric}_median_percent_change']
                if saved not in ('', None) and not np.isclose(median, float(saved), rtol=1e-12, atol=1e-12):
                    raise ValueError(f'Median shape change disagrees with method_summary for {m} {region} {metric}')
                unph = np.array([float(r['unphased_value']) for r in dd]); val = np.array([float(r['method_value']) for r in dd])
                out.append({'method': m, 'display_method': SHORT[m], 'region': region, 'metric': metric,
                    'metric_label': METRIC_LABEL[metric], 'median_percent_change_vs_unphased': median,
                    'lower_than_unphased_count': sum(r['flag'] == 'lower' for r in dd),
                    'higher_than_unphased_count': sum(r['flag'] == 'higher' for r in dd), 'acquisitions': len(dd),
                    'median_unphased_value': float(np.median(unph)), 'median_method_value': float(np.median(val)),
                    'objective_overlap': (OBJECTIVE_OVERLAP.get(m, {}).get(metric, 'independent')
                                          if region in OBJECTIVE_OVERLAP_REGIONS.get(m, (region,)) else 'independent'),
                    'interpretation_limit': 'lower is favourable only for positive isolated absorptive peaks; multiplets/centres can confound'})
    return out


def region_category(pq, m, region):
    """Consistency-based category from the two contamination terms (odd Real, even Imaginary)."""
    rr = [r for r in pq if r['method'] == m and r['region'] == region and r['metric'] in CONTAMINATION]
    low = sum(r['lower_than_unphased_count'] >= CONSISTENT for r in rr)
    high = sum(r['higher_than_unphased_count'] >= CONSISTENT for r in rr)
    if low == 2: return 'Better'
    if low == 1 and high == 0: return 'Partly better'
    if low == 0 and high == 0: return '≈ Unphased'
    if low == 1 and high == 1: return 'Mixed'
    return 'Worse'


def phase_category(c2, c7):
    good, bad = {'Better', 'Partly better'}, {'Worse', 'Mixed'}
    if c2 == c7 == 'Better': return 'Strong'
    if not ({c2, c7} & bad) and {c2, c7} & good: return 'Acceptable'
    if {c2, c7} & good and {c2, c7} & bad: return 'Mixed'
    return 'Weak'


def effective_phase(d):
    """Phase actually applied at the shared 2 ppm, target and 7 ppm magnitude centres."""
    axes = {}
    for ident in d.order:
        with np.load(d.base/'spectra'/ident/'raw_fid_and_common_fft.npz') as z:
            axes[ident] = z['ppm'].copy()
    table, summary = [], []
    centres = {'2ppm': 'p2_magnitude_center_ppm', 'target': 'target_phase_magnitude_center_ppm', '7ppm': 'p7_magnitude_center_ppm'}
    for m in AUTOMATIC:
        phases = {region: [] for region in centres}
        for ident in d.order:
            r = d.metrics[(ident, m)]; sign = -1. if r['inverse_phase'] == 'True' else 1.; axis = axes[ident]; n = len(axis)
            for region, column in centres.items():
                k = int(np.argmin(np.abs(axis-float(r[column]))))
                phi = wrap(sign*(float(r['p0_deg'])+float(r['p1_deg'])*k/n)); phases[region].append(phi)
                table.append({'method': m, 'display_method': SHORT[m], 'acquisition_id': ident, 'region': region,
                    'centre_ppm': axis[k], 'p0_deg': float(r['p0_deg']), 'p1_deg': float(r['p1_deg']),
                    'inverse_phase': r['inverse_phase'] == 'True', 'effective_phase_deg': phi})
        for region, values in phases.items():
            v = np.array(values); centre = float(np.angle(np.mean(np.exp(1j*np.deg2rad(v))), deg=True))
            dev = wrap(v-centre)
            summary.append({'method': m, 'display_method': SHORT[m], 'region': region,
                'circular_mean_effective_phase_deg': centre, 'rms_deviation_across_acquisitions_deg': float(np.sqrt(np.mean(dev**2))),
                'max_abs_deviation_deg': float(np.abs(dev).max()),
                'mean_abs_change_between_consecutive_acquisitions_deg': float(np.mean(np.abs(wrap(np.diff(v))))),
                'note': 'DX metadata is constant by construction; constancy is not correctness'})
    return table, summary


# ---------------------------------------------------------------------------- strong-peak scoring sensitivity (Stage 2 review)
STRONG_WINDOWS = {'2ppm': (1.80, 2.35), '7ppm': (6.70, 7.30)}
SENSITIVITY_THRESHOLDS = (5, 6, 7)
ODD_TERMS = {'odd_real': 'dispersive_metric', 'asymmetry': 'peak_asymmetry'}
VARIANTS = (('package_decimated', 'decimated', 0, None), ('full_resolution', 'full', 0, None),
            ('full_resolution_minus1', 'full', -1, None), ('full_resolution_plus1', 'full', 1, None),
            ('full_resolution_minus8', 'full', -8, None), ('full_resolution_plus8', 'full', 8, None),
            ('decimated_fixed_A', 'decimated', 0, 0), ('decimated_fixed_B', 'decimated', 0, 1))
VARIANT_LABEL = {'package_decimated': 'Package scoring (0.005 ppm grid, centre per acquisition)',
                 'full_resolution': 'Full resolution, same centre rule', 'full_resolution_minus1': 'Full resolution, centre −1 point',
                 'full_resolution_plus1': 'Full resolution, centre +1 point', 'full_resolution_minus8': 'Full resolution, centre −8 points',
                 'full_resolution_plus8': 'Full resolution, centre +8 points', 'decimated_fixed_A': 'Package grid, centre fixed at A',
                 'decimated_fixed_B': 'Package grid, centre fixed at B'}


def shape_window(context, bounds, shift=0, fixed_index=None):
    """Same rule as the package diagnostic: magnitude-maximum centre in fixed bounds, ±0.18 ppm symmetric window.
    fixed_index pins the centre to one full-resolution FFT index (robust to sub-point axis offsets between acquisitions)."""
    eligible = np.flatnonzero((context.ppm >= bounds[0]) & (context.ppm <= bounds[1]))
    centre = (int(np.argmin(np.abs(context.indices-fixed_index))) if fixed_index is not None
              else int(eligible[np.argmax(np.abs(context.fft[eligible]))]))+shift
    dx = float(np.median(np.diff(context.ppm)))
    radius = min(int(.18/dx), centre-int(eligible[0]), int(eligible[-1])-centre)
    return replace(context, centers=[centre], windows=[np.arange(centre-radius, centre+radius+1)]), centre


def change_flag(reference, value):
    if np.isclose(value, reference, rtol=1e-10, atol=1e-12): return 'unchanged'
    return 'lower' if value < reference else 'higher'


def generic_region(counts_low, counts_high, threshold):
    low = sum(c >= threshold for c in counts_low); high = sum(c >= threshold for c in counts_high)
    if low == 2: return 'Better'
    if low == 1 and high == 0: return 'Partly better'
    if low == 0 and high == 0: return '≈ Unphased'
    if low == 1 and high == 1: return 'Mixed'
    return 'Worse'


def strong_peak_sensitivity(d):
    """Re-score the saved phased spectra with alternative window centring and scoring resolution (read-only)."""
    contexts, spectra, axes = {}, {}, {}
    for ident in d.order:
        with np.load(d.base/'spectra'/ident/'raw_fid_and_common_fft.npz') as z:
            axis, z0 = z['ppm'].copy(), z['unphased_complex_fft'].copy()
        axes[ident] = axis
        contexts[ident] = {'decimated': prepare_context(axis, z0), 'full': prepare_context(axis, z0, {'maximum_scoring_points': len(axis)})}
        spectra[ident] = {}
        for m in METHODS:
            with np.load(d.base/'spectra'/ident/STEMS[m]/'spectral_evidence.npz') as z:
                spectra[ident][m] = z['phased'].copy()
    package_index = {reg: [int(contexts[i]['decimated'].indices[shape_window(contexts[i]['decimated'], b)[1]]) for i in d.order]
                     for reg, b in STRONG_WINDOWS.items()}
    package_centres = {reg: [float(axes[i][k]) for i, k in zip(d.order, package_index[reg])] for reg in STRONG_WINDOWS}
    fixed = {reg: sorted(v for v, _ in Counter(vals).most_common(2)) for reg, vals in package_index.items()}
    scores, centre_rows = {}, []
    for ident in d.order:
        for name, grid, shift, which in VARIANTS:
            for reg, bounds in STRONG_WINDOWS.items():
                fixed_index = fixed[reg][which] if which is not None and len(fixed[reg]) > which else None
                local, centre = shape_window(contexts[ident][grid], bounds, shift, fixed_index)
                centre_rows.append({'variant': name, 'acquisition_id': ident, 'acquisition_time': d.clock[d.order.index(ident)], 'region': reg,
                                    'centre_ppm': float(contexts[ident][grid].ppm[centre]),
                                    'centre_full_resolution_index': int(contexts[ident][grid].indices[centre]), 'grid': grid})
                for m in METHODS:
                    s = score_spectrum(spectra[ident][m], local)
                    scores[(name, ident, m, reg)] = {k: float(s[k]) for k in SHAPE}
    per = d.metrics
    worst = max(abs(scores[('package_decimated', i, m, reg)][k]-float(per[(i, m)][('p2' if reg == '2ppm' else 'p7')+'_'+k]))
                for i in d.order for m in METHODS for reg in STRONG_WINDOWS for k in SHAPE)
    if worst > 1e-9:
        raise ValueError(f'Package-variant rescoring does not reproduce the saved shape metrics (max |diff| {worst})')
    term_rows = []
    for name, *_ in VARIANTS:
        for m in AUTOMATIC:
            for reg in STRONG_WINDOWS:
                for metric in SHAPE:
                    ref = np.array([scores[(name, i, 'unphased', reg)][metric] for i in d.order])
                    val = np.array([scores[(name, i, m, reg)][metric] for i in d.order])
                    flags = [change_flag(r_, v_) for r_, v_ in zip(ref, val)]
                    pct = 100*(val-ref)/np.abs(ref)
                    term_rows.append({'variant': name, 'variant_label': VARIANT_LABEL[name], 'method': m, 'display_method': SHORT[m],
                        'region': reg, 'metric': metric, 'median_percent_change_vs_unphased': float(np.median(pct)),
                        'min_percent_change': float(pct.min()), 'max_percent_change': float(pct.max()),
                        'lower_count': flags.count('lower'), 'higher_count': flags.count('higher'),
                        'pattern_by_acquisition': ''.join({'lower': 'L', 'higher': 'H', 'unchanged': '='}[f] for f in flags)})
    terms = {(r['variant'], r['method'], r['region'], r['metric']): r for r in term_rows}
    category_rows, summary_rows = [], []
    independent = [m for m in AUTOMATIC if not ({k for k in OBJECTIVE_OVERLAP.get(m, {})} & set(CONTAMINATION))]
    for name, *_ in VARIANTS:
        for threshold in SENSITIVITY_THRESHOLDS:
            for odd_key, odd_metric in ODD_TERMS.items():
                pair = (odd_metric, 'even_imaginary_fraction')
                cats, worst_case = {}, {}
                for m in AUTOMATIC:
                    regions = {}
                    for reg in STRONG_WINDOWS:
                        rr = [terms[(name, m, reg, k)] for k in pair]
                        regions[reg] = generic_region([r['lower_count'] for r in rr], [r['higher_count'] for r in rr], threshold)
                    cats[m] = phase_category(regions['2ppm'], regions['7ppm'])
                    worst_case[m] = max(terms[(name, m, reg, k)]['median_percent_change_vs_unphased'] for reg in STRONG_WINDOWS for k in pair)
                    category_rows.append({'variant': name, 'threshold_of_8': threshold, 'odd_term': odd_key, 'method': m,
                        'display_method': SHORT[m], 'region_2ppm': regions['2ppm'], 'region_7ppm': regions['7ppm'], 'category': cats[m],
                        'worst_core_term_median_pct': worst_case[m], 'independent_of_objective': m in independent})
                acceptable = [SHORT[m] for m in independent if cats[m] in ('Strong', 'Acceptable')]
                smallest = min(independent, key=lambda m: worst_case[m])
                summary_rows.append({'variant': name, 'variant_label': VARIANT_LABEL[name], 'threshold_of_8': threshold, 'odd_term': odd_key,
                    'independent_strong_or_acceptable': ', '.join(acceptable) or 'none',
                    'independent_with_smallest_worst_case': SHORT[smallest],
                    'worst_case_by_independent_method': '; '.join(f'{SHORT[m]} {worst_case[m]:+.1f}%' for m in independent),
                    **{f'category_{STEMS[m]}': cats[m] for m in AUTOMATIC}})
    optimum_rows = []
    grid_phi = np.round(np.arange(-60., 60.0001, .25), 2)
    eff = {}
    for m in AUTOMATIC:
        for ident in d.order:
            r = d.metrics[(ident, m)]; sign = -1. if r['inverse_phase'] == 'True' else 1.
            for reg, column in (('2ppm', 'p2_magnitude_center_ppm'), ('7ppm', 'p7_magnitude_center_ppm')):
                k = int(np.argmin(np.abs(axes[ident]-float(r[column]))))
                eff[(m, ident, reg)] = wrap(sign*(float(r['p0_deg'])+float(r['p1_deg'])*k/len(axes[ident])))
    for ident in d.order:
        for name in ('package_decimated', 'decimated_fixed_A', 'decimated_fixed_B'):
            which = {'package_decimated': None, 'decimated_fixed_A': 0, 'decimated_fixed_B': 1}[name]
            for reg, bounds in STRONG_WINDOWS.items():
                ctx = contexts[ident]['decimated']
                local, centre = shape_window(ctx, bounds, 0, fixed[reg][which] if which is not None else None)
                values = [score_samples(phase_samples((phi, 0.), ctx), local) for phi in grid_phi]
                best = {metric: float(grid_phi[int(np.argmin([v[metric] for v in values]))]) for metric in CONTAMINATION}
                row = {'variant': name, 'acquisition_id': ident, 'acquisition_time': d.clock[d.order.index(ident)], 'region': reg,
                       'centre_ppm': float(ctx.ppm[centre]), 'optimum_constant_phase_odd_real_deg': best['dispersive_metric'],
                       'optimum_constant_phase_even_imaginary_deg': best['even_imaginary_fraction']}
                if name == 'package_decimated':
                    for m in AUTOMATIC:
                        row[f'{STEMS[m]}_applied_phase_deg'] = eff[(m, ident, reg)]
                        row[f'{STEMS[m]}_minus_odd_real_optimum_deg'] = wrap(eff[(m, ident, reg)]-best['dispersive_metric'])
                optimum_rows.append(row)
    fixed_ppm = {reg: [float(np.median([axes[i][k] for i in d.order])) for k in fixed[reg]] for reg in STRONG_WINDOWS}
    return SimpleNamespace(terms=term_rows, categories=category_rows, summary=summary_rows, centres=centre_rows, optimum=optimum_rows,
                           fixed=fixed, fixed_ppm=fixed_ppm, package_index=package_index, package_centres=package_centres, independent=independent)


def sensitivity_facts(d, sens):
    """Numbers the narrative quotes about centring; computed, then asserted against the claims."""
    pc = sens.package_index['2ppm']; A, B = sens.fixed_ppm['2ppm']
    alternates = all(pc[i] != pc[i+1] for i in range(len(pc)-1)) and set(pc) == set(sens.fixed['2ppm'])
    opt = [r for r in sens.optimum if r['region'] == '2ppm']
    shift = [abs(next(r for r in opt if r['variant'] == 'decimated_fixed_A' and r['acquisition_id'] == i)['optimum_constant_phase_odd_real_deg']
                 - next(r for r in opt if r['variant'] == 'decimated_fixed_B' and r['acquisition_id'] == i)['optimum_constant_phase_odd_real_deg'])
             for i in d.order]
    pack = [r for r in opt if r['variant'] == 'package_decimated']
    combined_track = max(abs(r['combined_minus_odd_real_optimum_deg']) for r in pack)
    pack7 = [r for r in sens.optimum if r['region'] == '7ppm' and r['variant'] == 'package_decimated']
    def swing(variant):
        series = [next(r for r in opt if r['variant'] == variant and r['acquisition_id'] == i)['optimum_constant_phase_odd_real_deg'] for i in d.order]
        return float(np.mean(np.abs(np.diff(series))))
    def distance(rows_, metric):
        return float(np.mean([abs(r[f'optimum_constant_phase_{metric}_deg']) for r in rows_]))
    def pattern(m, variant, metric):
        return next(r for r in sens.terms if r['method'] == m and r['variant'] == variant and r['region'] == '2ppm'
                    and r['metric'] == metric)['pattern_by_acquisition']
    def alternating(p):
        return all(a != b for a, b in zip(p, p[1:]))
    core = ('dispersive_metric', 'peak_asymmetry', 'even_imaginary_fraction')
    fixed_variants = ('decimated_fixed_A', 'decimated_fixed_B')
    package_alternates = all(alternating(pattern(m, 'package_decimated', t)) for m in ('acme', 'peak_minima') for t in core)
    fixed_patterns = [pattern(m, v, t) for m in ('acme', 'peak_minima') for v in fixed_variants for t in core]
    fixed_one_direction = min(max(p.count('H'), p.count('L')) for p in fixed_patterns)
    fixed_alternation_gone = not any(alternating(p) for p in fixed_patterns)
    sym_even_package_alternates = alternating(pattern('symmetry_objective', 'package_decimated', 'even_imaginary_fraction'))
    sym_even_fixed_alternating = sum(alternating(pattern('symmetry_objective', v, 'even_imaginary_fraction')) for v in fixed_variants)
    full = {r['acquisition_id']: r['centre_ppm'] for r in sens.centres if r['variant'] == 'full_resolution' and r['region'] == '2ppm'}
    full_steps = np.abs(np.diff([full[i] for i in d.order]))
    S = {(r['variant'], r['threshold_of_8'], r['odd_term']): r for r in sens.summary}
    cat = {(r['variant'], r['threshold_of_8'], r['odd_term'], r['method']): r['category'] for r in sens.categories}
    acme_smallest_everywhere = all(r['independent_with_smallest_worst_case'] == 'ACME' for r in sens.summary)
    facts_ = SimpleNamespace(A=A, B=B, alternates=alternates, grid_step=abs(B-A),
        optimum_shift_median=float(np.median(shift)), combined_track=combined_track,
        optimum_2ppm_median=float(np.median([r['optimum_constant_phase_odd_real_deg'] for r in pack])),
        optimum_7ppm_odd_median=float(np.median([r['optimum_constant_phase_odd_real_deg'] for r in pack7])),
        optimum_7ppm_even_median=float(np.median([r['optimum_constant_phase_even_imaginary_deg'] for r in pack7])),
        full_step_range=(float(full_steps.min()), float(full_steps.max())), S=S, cat=cat,
        swing_package=swing('package_decimated'), swing_fixed=(swing('decimated_fixed_A'), swing('decimated_fixed_B')),
        distance_2ppm=(distance(pack, 'odd_real'), distance(pack, 'even_imaginary')), distance_7ppm=(distance(pack7, 'odd_real'), distance(pack7, 'even_imaginary')),
        n_scorings=len({(r['variant'], r['odd_term']) for r in sens.summary}),
        fixed_one_direction=fixed_one_direction, sym_even_fixed_alternating=sym_even_fixed_alternating,
        acme_smallest_everywhere=acme_smallest_everywhere, n_summary=len(sens.summary))
    assert alternates, 'the package 2 ppm centre no longer alternates between two grid points; revise the narrative'
    assert acme_smallest_everywhere, 'ACME no longer has the smallest worst case in every variant; revise the narrative'
    assert package_alternates and fixed_alternation_gone and fixed_one_direction >= 6,         'ACME/Peak minima 2 ppm alternation is no longer explained by the centre; revise the narrative'
    assert sym_even_package_alternates and sym_even_fixed_alternating >= 1, 'Symmetry 2 ppm even-Imaginary pattern changed; revise the narrative'
    return facts_


# ---------------------------------------------------------------------------- Tasks 11-20: endpoint metrics
def ols(t, a):
    t = np.asarray(t, float); a = np.asarray(a, float); tc = t-t.mean()
    sxx = float(np.dot(tc, tc)); slope = float(np.dot(tc, a-a.mean())/sxx)
    residual = a-(a.mean()+slope*tc); dof = len(t)-2
    se = float(np.sqrt(np.dot(residual, residual)/dof/sxx)) if dof > 0 else float('nan')
    tcrit = float(stats.t.ppf(.975, dof)) if dof > 0 else float('nan')
    return slope, se, slope-tcrit*se, slope+tcrit*se, tcrit


def rolling(t, a, i, n=3):
    tt, aa = t[i-n+1:i+1], a[i-n+1:i+1]; mean = float(aa.mean())
    slope, se, lo, hi, tcrit = ols(tt, aa)
    return {'slope_au_ppm_per_h': slope, 'slope_se': se, 'slope_ci95_low': lo, 'slope_ci95_high': hi, 't_critical_df1': tcrit,
            'normalized_slope_pct_per_h': 100*slope/mean, 'cv_pct': 100*float(aa.std(ddof=1))/mean,
            'range_pct': 100*float(aa.max()-aa.min())/mean, 'window_mean_au_ppm': mean}


def percent_changes(a):
    return 100*np.diff(a)/a[:-1]


RULES = []
for _x in (3., 4., 5., 6., 7.5, 10.):
    RULES.append(dict(family='historical', rule_id=f'historical_gate_{_x:g}pct', threshold=_x, units='% per interval',
        persistence=False, label=f'Historical rule, {_x:g}% gate'))
for _x in (3., 5., 7.5, 10.):
    RULES.append(dict(family='historical_persistent', rule_id=f'historical_gate_{_x:g}pct_confirmed', threshold=_x,
        units='% per interval', persistence=True, label=f'Historical {_x:g}% + next-acquisition confirmation'))
for _k in (1, 2, 3):
    for _x in (3., 5., 7.5, 10.):
        RULES.append(dict(family=f'consecutive_{_k}', rule_id=f'consecutive_{_k}_intervals_{_x:g}pct', threshold=_x, k=_k,
            units='% per interval', persistence=_k > 1, label=f'{_k} interval{"s" if _k > 1 else ""} ≤ {_x:g}%'))
for _family, _values, _units, _name in (('rolling_slope', (5., 10., 15., 20.), '%/h', '3-point |slope|/mean'),
                                         ('rolling_cv', (2.5, 3.5, 5., 7.5), '%', '3-point CV'),
                                         ('rolling_range', (5., 7.5, 10., 15.), '%', '3-point range/mean')):
    for _persist in (False, True):
        for _x in _values:
            RULES.append(dict(family=_family+('_persistent' if _persist else ''),
                rule_id=f'{_family}_{_x:g}{"_confirmed" if _persist else ""}', threshold=_x, units=_units, persistence=_persist,
                label=f'{_name} ≤ {_x:g}{_units}' + (' (confirmed)' if _persist else '')))
FAMILY_LABEL = {'historical': 'Historical rule (unchanged code; gate varied)',
    'historical_persistent': 'Historical rule + next-acquisition confirmation',
    'consecutive_1': 'Single interval |Δ| ≤ gate', 'consecutive_2': 'Two consecutive intervals ≤ gate',
    'consecutive_3': 'Three consecutive intervals ≤ gate (no slope gates)',
    'rolling_slope': 'Rolling 3-point normalized slope', 'rolling_slope_persistent': 'Rolling slope, confirmed',
    'rolling_cv': 'Rolling 3-point CV', 'rolling_cv_persistent': 'Rolling CV, confirmed',
    'rolling_range': 'Rolling 3-point range', 'rolling_range_persistent': 'Rolling range, confirmed'}


def qualifies(d, rule, a, quality):
    """Per-prefix decision for one rule; decisions use only data available at that acquisition."""
    first, _ = endpoint_window(d); n = len(a); decide = [False]*n; metric = [float('nan')]*n
    cfg = replace(d.cfg, percent_change_threshold=rule['threshold'])
    family = rule['family'].replace('_persistent', '')
    for i in range(n):
        if family == 'historical':
            status, _, complete, _, _, terms, _ = _current_decision(d.t[:i+1], a[:i+1], quality[:i+1], cfg)
            pct = percent_changes(a[:i+1])[-d.cfg.consecutive_stable_measurements:] if i >= 1 else np.array([])
            metric[i] = float(np.max(np.abs(pct))) if pct.size else float('nan'); decide[i] = bool(complete)
            continue
        if i < first or not all(quality[max(0, i-2):i+1]):
            if i >= 2 and family.startswith('rolling'):
                metric[i] = rolling(d.t, a, i)[{'rolling_slope': 'normalized_slope_pct_per_h', 'rolling_cv': 'cv_pct',
                                                 'rolling_range': 'range_pct'}[family]]
            continue
        if family.startswith('consecutive'):
            pct = percent_changes(a[:i+1])[-rule['k']:]; metric[i] = float(np.max(np.abs(pct)))
        else:
            value = rolling(d.t, a, i)[{'rolling_slope': 'normalized_slope_pct_per_h', 'rolling_cv': 'cv_pct',
                                        'rolling_range': 'range_pct'}[family]]
            metric[i] = abs(value) if family == 'rolling_slope' else value
        decide[i] = metric[i] <= rule['threshold']
    if rule['persistence'] and rule['family'] != 'consecutive_2' and rule['family'] != 'consecutive_3':
        decide = [bool(decide[i] and i-1 >= first and decide[i-1]) for i in range(n)]
    return decide, metric


def modal_outcome(values):
    """Most common first-qualifying index; ties go to the earliest qualification, never last."""
    return max(Counter(values).items(), key=lambda kv: (kv[1], -(kv[0] if kv[0] is not None else 99)))


def outcome_label(d, index):
    return d.clock[index] if index is not None else 'never'


def evaluate_rules(d):
    first, _ = endpoint_window(d); evaluable = list(range(first, len(d.t)))
    detail, summary = [], []
    for key in SERIES:
        values = series(d, key); quality = d.quality[key]
        for rule in RULES:
            outcomes = {}
            for m in METHODS:
                decide, metric = qualifies(d, rule, values[m], quality[m])
                hits = [i for i in evaluable if decide[i]]; f = hits[0] if hits else None
                later = [i for i in evaluable if f is not None and i > f]
                sustained = None if f is None or not later else all(decide[i] for i in later)
                departure = ('not applicable: never qualifies' if f is None else 'no later acquisition' if not later else
                             'sustained to last acquisition' if sustained else 'qualification not sustained (later departure)')
                change = (float(np.max(np.abs(values[m][later]-values[m][f]))/values[m][f]*100) if later else float('nan'))
                row = {'rule_family': rule['family'], 'rule_id': rule['rule_id'], 'rule_label': rule['label'],
                    'threshold': rule['threshold'], 'threshold_units': rule['units'], 'persistence': rule['persistence'],
                    'area_series': SERIES_ID[key], 'method': m, 'display_method': SHORT[m],
                    'role': 'automatic phase method' if m in AUTOMATIC else 'no-phase reference',
                    'first_qualifying_time': outcome_label(d, f), 'first_qualifying_index': f,
                    'decision_pattern': ''.join('Q' if decide[i] else '·' for i in evaluable),
                    'decision_pattern_times': ' '.join(d.clock[i][:5] for i in evaluable),
                    'qualifying_prefixes': len(hits), 'evaluable_prefixes': len(evaluable),
                    'later_departure_status': departure, 'max_later_change_from_first_qualifying_pct': change,
                    'metric_at_first_qualification': metric[f] if f is not None else float('nan'),
                    'margin_at_first_qualification': rule['threshold']-metric[f] if f is not None else float('nan'),
                    **{f'metric_at_{d.clock[i]}': metric[i] for i in evaluable},
                    'is_historical_criterion': rule['rule_id'] == 'historical_gate_5pct' and key == 'fixed',
                    'status': 'historical criterion (unchanged)' if rule['rule_id'] == 'historical_gate_5pct' and key == 'fixed'
                              else 'exploratory candidate; not a production endpoint'}
                if rule['family'] == 'historical':
                    result = detect_completion(d.t, values[m], quality_pass=quality[m], timestamps=d.clock,
                                               config=replace(d.cfg, percent_change_threshold=rule['threshold'])).as_dict()
                    row['historical_code_status'] = result['status']
                    row['historical_code_departure_warning'] = any('depart' in w for w in result['quality_warnings'])
                    if (result['completion_index'] is None) != (f is None) or (f is not None and result['completion_index'] != f):
                        raise ValueError('Prefix replay disagrees with detect_completion')
                detail.append(row); outcomes[m] = f
            auto = [outcomes[m] for m in AUTOMATIC]; counts = Counter(auto)
            modal, modal_n = modal_outcome(auto)
            qual = [r for r in detail[-len(METHODS):] if r['method'] in AUTOMATIC and r['first_qualifying_index'] is not None]
            sustained = [r for r in qual if r['later_departure_status'] == 'sustained to last acquisition']
            times = [d.t[r['first_qualifying_index']] for r in qual]
            summary.append({'rule_family': rule['family'], 'family_label': FAMILY_LABEL[rule['family']],
                'rule_id': rule['rule_id'], 'rule_label': rule['label'], 'threshold': rule['threshold'],
                'threshold_units': rule['units'], 'area_series': SERIES_ID[key],
                'automatic_outcomes': '; '.join(f'{outcome_label(d, k)}×{v}' for k, v in
                                                sorted(counts.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))),
                'modal_outcome': outcome_label(d, modal), 'methods_agreeing_with_modal_of_7': modal_n,
                'distinct_outcomes': len(counts), 'modal_is_a_qualification_time': modal is not None,
                'qualifying_automatic_methods': len(qual), 'qualifying_methods_sustained': len(sustained),
                'first_qualification_spread_hours': (max(times)-min(times)) if times else float('nan'),
                'unphased_outcome': outcome_label(d, outcomes['unphased']),
                'agreement_class': ('high (≥6/7)' if modal_n >= 6 else 'partial (4–5/7)' if modal_n >= 4 else 'split (≤3/7)'),
                'interpretation': ('agree on a qualification time' if modal is not None and modal_n >= 6 else
                                   'agree that the rule never qualifies (uninformative)' if modal is None and modal_n >= 6 else
                                   'phase method changes the endpoint'),
                'is_historical_criterion': rule['rule_id'] == 'historical_gate_5pct' and key == 'fixed'})
    return detail, summary


def metric_traces(d):
    out = []
    for key in SERIES:
        values = series(d, key)
        for m in METHODS:
            a = values[m]; pct = percent_changes(a)
            for i, ident in enumerate(d.order):
                row = {'area_series': SERIES_ID[key], 'method': m, 'display_method': SHORT[m], 'acquisition_id': ident,
                       'acquisition_time': d.manifest[i]['acquisition_time'], 'elapsed_hours': d.t[i], 'area_au_ppm': a[i],
                       'adjacent_percent_change': pct[i-1] if i else float('nan')}
                if i >= 2: row.update(rolling(d.t, a, i))
                if key == 'fixed' and i:
                    combined = math.hypot(d.se[m][i], d.se[m][i-1])
                    row.update({'white_noise_se_au_ppm': d.se[m][i], 'combined_interval_se_au_ppm': combined,
                                'uncertainty_aware_z': abs(a[i]-a[i-1])/combined})
                out.append(row)
    return out


def threshold_sensitivity(d, grid=None):
    """Historical rule with only percent_change_threshold varied; exact transition points."""
    grid = np.round(np.arange(3., 10.0001, .01), 2) if grid is None else grid
    table, transitions, agreement = [], [], []
    for key in SERIES:
        values = series(d, key); quality = d.quality[key]
        def first(m, x):
            r = detect_completion(d.t, values[m], quality_pass=quality[m], timestamps=d.clock,
                                  config=replace(d.cfg, percent_change_threshold=float(x)))
            return r.completion_index
        for m in METHODS:
            pct = np.abs(percent_changes(values[m]))
            candidates = sorted({float(p) for p in pct if 3. <= p <= 10.})
            previous = first(m, 3.)
            for c in candidates:
                at, above = first(m, c), first(m, np.nextafter(c, np.inf))
                below = first(m, np.nextafter(c, -np.inf))
                if at != below or above != at:
                    transitions.append({'area_series': SERIES_ID[key], 'method': m, 'display_method': SHORT[m],
                        'transition_threshold_pct': c, 'outcome_below': outcome_label(d, below),
                        'outcome_at_and_above': outcome_label(d, above),
                        'note': 'gate is inclusive (|change| ≤ gate qualifies)'})
            for x in grid:
                table.append({'area_series': SERIES_ID[key], 'method': m, 'display_method': SHORT[m],
                              'percent_gate': float(x), 'first_completion_index': first(m, x),
                              'first_completion_time': outcome_label(d, first(m, x))})
        for x in grid:
            auto = [r['first_completion_index'] for r in table if r['area_series'] == SERIES_ID[key]
                    and r['percent_gate'] == float(x) and r['method'] in AUTOMATIC]
            c = Counter(auto); modal, n = c.most_common(1)[0]
            agreement.append({'area_series': SERIES_ID[key], 'percent_gate': float(x), 'modal_outcome': outcome_label(d, modal),
                              'automatic_methods_agreeing': n, 'distinct_outcomes': len(c)})
    return table, transitions, agreement


def gate_margins(d):
    """Every historical gate at every evaluable prefix, with margins in the gate's own units."""
    first, _ = endpoint_window(d); out = []; c = d.cfg
    for key in SERIES:
        values = series(d, key); quality = d.quality[key]
        for m in METHODS:
            a = values[m]
            for i in range(first, len(d.t)):
                status, _, complete, reason, _, terms, _ = _current_decision(d.t[:i+1], a[:i+1], quality[m][:i+1], c)
                pct = percent_changes(a[:i+1])[-c.consecutive_stable_measurements:]
                worst = float(np.max(np.abs(pct)))
                margins = {'percent_gate': (c.percent_change_threshold-worst)/c.percent_change_threshold,
                    'absolute_slope_gate': (c.absolute_slope_threshold_per_hour-abs(terms['recent_slope_per_hour']))/c.absolute_slope_threshold_per_hour,
                    'relative_slope_gate': (c.relative_slope_threshold_percent_per_hour-abs(terms['recent_relative_slope_percent_per_hour']))/c.relative_slope_threshold_percent_per_hour}
                out.append({'area_series': SERIES_ID[key], 'method': m, 'display_method': SHORT[m],
                    'acquisition_time': d.manifest[i]['acquisition_time'], 'status': status, 'qualifies': bool(complete),
                    'recent_interval_percent_changes': [round(float(p), 4) for p in pct],
                    'max_abs_recent_interval_pct': worst, 'percent_gate': c.percent_change_threshold,
                    'percent_gate_margin_pp': c.percent_change_threshold-worst,
                    'recent_slope_au_ppm_per_h': terms['recent_slope_per_hour'],
                    'absolute_slope_gate': c.absolute_slope_threshold_per_hour,
                    'absolute_slope_margin': c.absolute_slope_threshold_per_hour-abs(terms['recent_slope_per_hour']),
                    'relative_slope_pct_per_h': terms['recent_relative_slope_percent_per_hour'],
                    'relative_slope_gate': c.relative_slope_threshold_percent_per_hour,
                    'relative_slope_margin_pct_per_h': c.relative_slope_threshold_percent_per_hour-abs(terms['recent_relative_slope_percent_per_hour']),
                    'binding_gate': min(margins, key=margins.get), 'binding_relative_margin': min(margins.values()),
                    'reason': reason})
    return out


def uncertainty_summary(d, traces, sp):
    z = np.array([r['uncertainty_aware_z'] for r in traces if r['area_series'] == 'completion_fixed_window'
                  and r['method'] in AUTOMATIC and 'uncertainty_aware_z' in r])
    se = np.concatenate([d.se[m] for m in AUTOMATIC])
    first, start = endpoint_window(d)
    return {'white_noise_se_median_au_ppm': float(np.median(se)), 'white_noise_se_max_au_ppm': float(se.max()),
            'cross_method_range_median_au_ppm': float(np.median(sp['fixed']['range'][start:])),
            'ratio_cross_method_range_to_se': float(np.median(sp['fixed']['range'][start:])/np.median(se)),
            'z_minimum_automatic': float(z.min()), 'z_median_automatic': float(np.median(z)),
            'status': 'not usable as an endpoint gate',
            'reason': ('The only stored area uncertainty is white-noise propagation, documented as an approximate lower bound: '
                       'zero filling correlates points and phase/baseline/integration systematics are excluded. It is about '
                       f'{np.median(sp["fixed"]["range"][start:])/np.median(se):.0f}× smaller than the cross-method processing spread, '
                       'so |ΔA|/u would call every interval significant. A defensible uncertainty needs replicate acquisitions '
                       'of a static sample.')}


# ---------------------------------------------------------------------------- Task 17: internal reference
def jcamp_header(path, keys):
    found = {}
    with open(path, 'rb') as f:
        for raw in f:
            line = raw.decode('latin-1').rstrip()
            if not line.startswith('##'):
                if found and line[:1].isdigit(): break
                continue
            key, _, value = line[2:].partition('=')
            if key in keys and key not in found:
                found[key] = value.split('$$')[0].strip()
    return found


def internal_reference(d):
    windows = {'2ppm': (1.80, 2.35), '7ppm': (6.70, 7.30), 'target': (5.70, 5.90)}
    keys = ('.SOLVENT NAME', '$RECVR_GAIN', '$EXPERIMENT_SETTING_Receiver Gain (dB)', '$SCANS', '$X_PULSE',
            'TEMPERATURE', '$TOTAL DURATION', 'SAMPLE DESCRIPTION', 'TITLE')
    table = []
    for i, rec in enumerate(d.manifest):
        folder = d.base/'spectra'/rec['acquisition_id']
        with np.load(folder/'raw_fid_and_common_fft.npz') as z:
            axis, fft = z['ppm'].copy(), z['unphased_complex_fft'].copy()
        dx = float(np.median(np.diff(axis)))
        row = {'acquisition_id': rec['acquisition_id'], 'acquisition_time': rec['acquisition_time'],
               **{f'header_{k.strip(".$").replace(" ", "_").lower()}': v for k, v in jcamp_header(rec['source_path'], keys).items()}}
        for region, (lo, hi) in windows.items():
            mask = (axis >= lo) & (axis <= hi)
            row[f'{region}_magnitude_area_phase_invariant'] = float(np.abs(fft[mask]).sum()*dx)
            for m in METHODS:
                with np.load(folder/STEMS[m]/'spectral_evidence.npz') as z:
                    row[f'{region}_{STEMS[m]}_quantitative_real_area'] = float(z['quantitative_real'][mask].sum()*dx)
        table.append(row)
    mag = {r: np.array([x[f'{r}_magnitude_area_phase_invariant'] for x in table]) for r in ('2ppm', '7ppm')}
    real_cv = {r: {m: float(100*np.std([x[f'{r}_{STEMS[m]}_quantitative_real_area'] for x in table], ddof=1) /
                       np.mean([x[f'{r}_{STEMS[m]}_quantitative_real_area'] for x in table])) for m in METHODS} for r in ('2ppm', '7ppm')}
    gains = {x.get('header_recvr_gain') for x in table}; scans = {x.get('header_scans') for x in table}
    pulses = {x.get('header_x_pulse') for x in table}
    temps = [float(x['header_temperature']) for x in table if x.get('header_temperature')]
    first_drop = {r: 100*(mag[r][1]-mag[r][0])/mag[r][0] for r in mag}
    audit = {'decision': 'REJECTED: no strong resonance qualifies as an internal reference for these data',
        'magnitude_area_cv_pct': {r: float(100*mag[r].std(ddof=1)/mag[r].mean()) for r in mag},
        'magnitude_change_first_interval_pct': first_drop,
        'magnitude_change_first_to_last_pct': {r: float(100*(mag[r][-1]-mag[r][0])/mag[r][0]) for r in mag},
        'quantitative_real_area_cv_pct_by_method': real_cv,
        'receiver_gain_values': sorted(g for g in gains if g), 'scan_values': sorted(s for s in scans if s),
        'pulse_values': sorted(p for p in pulses if p), 'temperature_range_c': [min(temps), max(temps)] if temps else None,
        'reasons': [
            'No internal standard was added or recorded (configs/nmr/analysis.yaml: internal_standard null, solvent identity unknown); the JCAMP solvent field is an instrument-profile setting.',
            'The strong bands are solvent bands (toluene per metadata) in a flow experiment; the phase-invariant magnitude areas themselves change '
            f'by {first_drop["2ppm"]:+.1f}% (2 ppm) and {first_drop["7ppm"]:+.1f}% (7 ppm) when the target arrives (09:13→10:07), so solvent '
            'concentration in the detected volume is not constant.',
            'The two candidate references disagree with each other (different relative changes), so at least one is not a stable reference; '
            'the aromatic window can also contain aromatic protons of the phenylsilane chemistry.',
            'Receiver gain, scans, pulse width and temperature are recorded identical across all eight FIDs, so there is no documented '
            'instrumental scaling drift to correct.',
            'The strong-band Real areas depend on phase method and the downstream baseline (CV across time ranges '
            f'{min(real_cv["2ppm"].values()):.1f}–{max(real_cv["2ppm"].values()):.1f}% at 2 ppm by method), so a ratio would add method dependence.'],
        'integration': 'sum over fixed windows 1.80–2.35 / 6.70–7.30 ppm × ppm step; magnitude from the common unphased FFT (phase-invariant); '
                       'quantitative Real from saved arrays. Diagnostic only, not a new processing step.'}
    return table, audit


# ---------------------------------------------------------------------------- Task 18: change points
def hinge_fit(t, a, knots):
    columns = [np.ones_like(t), t] + [np.maximum(0., t-k) for k in knots]
    X = np.column_stack(columns); beta, *_ = np.linalg.lstsq(X, a, rcond=None)
    residual = a-X@beta
    return float(residual@residual), X.shape[1]


def bic(sse, n, k):
    return n*math.log(max(sse, 1e-12)/n)+k*math.log(n)


def change_points(d):
    _, start = endpoint_window(d); out = []
    for key in SERIES:
        values = series(d, key)
        for m in METHODS:
            for window, idx in (('full sequence', np.arange(len(d.t))), ('endpoint window', np.arange(start, len(d.t)))):
                t, a = d.t[idx], values[m][idx]; n = len(t)
                lin, k_lin = hinge_fit(t, a, [])
                single = sorted(((hinge_fit(t, a, [t[j]])[0], j) for j in range(1, n-1)))
                best_sse, best_j = single[0]; second_sse, second_j = single[1]
                row = {'area_series': SERIES_ID[key], 'method': m, 'display_method': SHORT[m], 'window': window,
                       'points': n, 'linear_sse': lin, 'best_single_breakpoint': d.clock[idx[best_j]],
                       'best_single_sse': best_sse, 'runner_up_breakpoint': d.clock[idx[second_j]],
                       'runner_up_sse_ratio': second_sse/best_sse if best_sse > 0 else float('inf'),
                       'delta_bic_linear_minus_single': bic(lin, n, 2)-bic(best_sse, n, 3),
                       'points_after_best_breakpoint': int(n-1-best_j)}
                if window == 'full sequence':
                    pairs = sorted(((hinge_fit(t, a, [t[i], t[j]])[0], i, j) for i in range(1, n-2) for j in range(i+1, n-1)))
                    sse2, i2, j2 = pairs[0]
                    row.update({'best_two_breakpoints': f'{d.clock[idx[i2]]} & {d.clock[idx[j2]]}', 'best_two_sse': sse2,
                                'delta_bic_single_minus_two': bic(best_sse, n, 3)-bic(sse2, n, 4),
                                'points_after_second_breakpoint': int(n-1-j2)})
                out.append(row)
    return out


def change_point_summary(cp):
    result = {}
    for key in SERIES:
        for window in ('full sequence', 'endpoint window'):
            auto = [r for r in cp if r['area_series'] == SERIES_ID[key] and r['window'] == window and r['method'] in AUTOMATIC]
            single = Counter(r['best_single_breakpoint'] for r in auto)
            entry = {'single_breakpoint_counts': dict(single), 'single_modal_share_of_7': single.most_common(1)[0][1],
                     'median_runner_up_sse_ratio': float(np.median([r['runner_up_sse_ratio'] for r in auto]))}
            if window == 'full sequence':
                pairs = Counter(r['best_two_breakpoints'] for r in auto)
                entry.update({'two_breakpoint_counts': dict(pairs),
                              'max_points_after_second_breakpoint': max(r['points_after_second_breakpoint'] for r in auto)})
            result[f'{SERIES_ID[key]} / {window}'] = entry
    return result


# ---------------------------------------------------------------------------- figures
TIME_LABEL = 'Acquisition time, 9 June 2026 (UTC−04; JCAMP LONG DATE)'


def tidy(ax, grid='y'):
    if grid:
        ax.grid(axis=grid, color=GRID, lw=.8)
    ax.set_axisbelow(True)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color('#b5b4ae')
    ax.tick_params(colors=INK2, labelsize=9.5)


def time_axis(ax, d, show=True):
    ax.set_xticks(d.t)
    ax.set_xticklabels([c[:5] for c in d.clock] if show else [])
    ax.set_xlim(d.t[0]-.1, d.t[-1]+.1)


def caption(fig, text, y=.012):
    fig.text(.012, y, text, fontsize=8.6, color=INK2, ha='left', va='bottom', wrap=True)


def fig_method_spread(pw, d, sp):
    weak = weak_signal(d)
    fig, axes = plt.subplots(2, 2, figsize=(14.5, 8.2), sharex='col', gridspec_kw={'height_ratios': [3, 1.2]})
    truncated = []
    for col, key in enumerate(SERIES):
        ax, bx = axes[0, col], axes[1, col]; values = series(d, key); s = sp[key]
        for m in AUTOMATIC:
            ax.plot(d.t, values[m], color='#c6c5bf', lw=.9, zorder=2)
        ax.fill_between(d.t, s['minimum'], s['maximum'], color=ACCENT, alpha=.16, lw=0, zorder=1)
        ax.plot(d.t, values['unphased'], color=MUTED, lw=1.3, ls=(0, (4, 2.5)), zorder=3)
        ax.plot(d.t, s['median'], color=ACCENT_DARK, lw=2.2, marker='o', ms=5.5, mec='white', mew=1.1, zorder=4)
        highest = Counter(AUTOMATIC[int(np.argmax([values[m][i] for m in AUTOMATIC]))] for i in range(len(d.t)))
        lowest = Counter(AUTOMATIC[int(np.argmin([values[m][i] for m in AUTOMATIC]))] for i in range(len(d.t)))
        (top_m, top_n), (low_m, low_n) = highest.most_common(1)[0], lowest.most_common(1)[0]
        ax.text(.02, .97, f'Band top: {SHORT[top_m]} at {top_n}/8 acquisitions\nBand bottom: {SHORT[low_m]} at {low_n}/8',
                transform=ax.transAxes, va='top', fontsize=9, color=INK2)
        ax.set_title(SERIES_SHORT[key]+(' — moving boundaries (as Fig. 05)' if key == 'picked'
                                        else ' — 5.70–5.90 ppm (historical endpoint input)'), loc='left', fontsize=11.5)
        ax.set_ylabel('Target area (a.u.·ppm)'); ax.set_ylim(0, max(s['maximum'].max(), values['unphased'].max())*1.12)
        tidy(ax)
        good = ~weak; top = float(s['range_pct'][good].max())*1.45
        for x, v, w, clock in zip(d.t, s['range_pct'], weak, d.clock):
            h = min(v, top*.93)
            bx.bar(x, h, width=.075, color=NEUTRAL if w else ACCENT, edgecolor=MUTED if w else ACCENT, lw=.8, zorder=2)
            bx.text(x, h+top*.03, f'{v:.1f}%'+('*' if w else ''), ha='center', va='bottom', fontsize=8.2, color=INK2)
            if v > h:  # truncated bar: two white slashes mark the break
                for dy in (-.05*top, .05*top):
                    bx.plot([x-.045, x+.045], [.72*h+dy-.03*top, .72*h+dy+.03*top], color='white', lw=2.4, zorder=3, solid_capstyle='butt')
                truncated.append(f'{SERIES_SHORT[key].lower()} {clock[:5]} bar truncated at {h:.1f}% (true value {v:.1f}%)')
        bx.set_ylim(0, top); bx.set_ylabel('Method range\n(% of median)'); tidy(bx); time_axis(bx, d)
        bx.set_xlabel(TIME_LABEL)
    handles = [Patch(facecolor=ACCENT, alpha=.16, label='Range of the 7 automatic phase methods'),
               Line2D([], [], color=ACCENT_DARK, lw=2.2, marker='o', mec='white', label='Cross-method median'),
               Line2D([], [], color='#c6c5bf', lw=.9, label='Individual automatic methods'),
               Line2D([], [], color=MUTED, lw=1.3, ls=(0, (4, 2.5)), label='Unphased reference (not in median)')]
    axes[0, 0].legend(handles=handles, loc='lower right', fontsize=8.6, frameon=False)
    caption(fig, 'Band = minimum to maximum of DX metadata, ACME, Peak minima, Combined, Symmetry, Ernst P0 and DEEP at each acquisition; '
            'no method is treated as truth. Lower panels: (max − min)/median. * 09:13 has a weak target (median S/N below 8): its percentage is '
            'unstable and its bar is shown light'+(f'; break mark = {"; ".join(truncated)}' if truncated else '')+
            '. Values: tables/method_spread_by_time.csv.')
    pw.save(fig, 'boss_summary/08_method_spread_vs_time.png', 'Cross-method target area over time', d.base/'tables/method_spread_by_time.csv')


def fig_chemistry_vs_algorithm(pw, d, chem, intervals):
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(15, 6.2), gridspec_kw={'width_ratios': [1, 1.25]})
    tone = {'picked': ACCENT, 'fixed': '#eb6834'}
    rp = next(r for r in chem if r['area_series'] == 'picked_moving_boundary')
    rf = next(r for r in chem if r['area_series'] == 'completion_fixed_window')
    groups = [('Chemical change over the whole run\n(max − min of the median trajectory)', 'sequence_dynamic_range_au_ppm'),
              ('Typical algorithm spread at one time\n(median of the 8 cross-method ranges)', 'median_cross_method_range_au_ppm'),
              (f'Chemical change inside the endpoint window\n({rp["endpoint_window_start"][:5]}–{rp["endpoint_window_end"][:5]}: the recent '
               'windows the historical rule evaluates)', 'endpoint_window_dynamic_range_au_ppm'),
              ('Typical algorithm spread in that window', 'endpoint_window_median_cross_method_range_au_ppm')]
    y0 = np.array([3.6, 2.6, 1.2, .2]); h = .34
    for k, key in enumerate(SERIES):
        row = next(r for r in chem if r['area_series'] == SERIES_ID[key])
        vals = [row[c] for _, c in groups]; y = y0+(h/2 if k == 0 else -h/2)
        ax.barh(y, vals, height=h*.92, color=tone[key], label=SERIES_SHORT[key], zorder=2)
        for yy, v in zip(y, vals):
            ax.text(v+.6, yy, f'{v:.1f}', va='center', fontsize=8.8, color=INK2)
    ax.set_yticks(y0, [g for g, _ in groups], fontsize=9.2)
    ax.set_xlabel('Target area (a.u.·ppm)'); tidy(ax, grid='x'); ax.set_xlim(0, 50)
    ax.text(7.5, y0[1]+.08, f'whole-run change ≈ {rp["R_dynamic_range_over_median_algorithm_range"]:.0f}× this spread (picked)\n'
            f'and {rf["R_dynamic_range_over_median_algorithm_range"]:.0f}× (fixed window)', va='center', fontsize=9.2, color=INK)
    ax.text(7.5, y0[1]-.4, f'without the weak-signal spread: {rp["R_excluding_weak_signal"]:.1f}× / {rf["R_excluding_weak_signal"]:.1f}×\n'
            f'against the largest spread: {rp["R_conservative_over_maximum_algorithm_range"]:.1f}× / '
            f'{rf["R_conservative_over_maximum_algorithm_range"]:.1f}×', va='center', fontsize=8.4, color=INK2)
    ax.text(7.5, y0[3], f'endpoint-window change only {rp["R_endpoint_window"]:.1f}× this spread (picked)\n'
            f'and {rf["R_endpoint_window"]:.1f}× (fixed window)', va='center', fontsize=9.2, color=INK)
    ax.legend(loc='center right', bbox_to_anchor=(1, .44), fontsize=8.8, frameon=False)  # empty band between the second and third groups
    ax.set_title('Size of the time change versus the algorithm effect', loc='left', fontsize=11.5)
    picked_rows = [r for r in intervals if r['area_series'] == 'picked_moving_boundary']
    labels = [f'{r["from_time"][:5]}→\n{r["to_time"][:5]}' for r in picked_rows]
    x = np.arange(len(labels)); first = [r['in_endpoint_window'] for r in picked_rows].index(True)
    bx.axvspan(first-.5, len(labels)-.5, color=NEUTRAL, zorder=0)
    bx.text(first-.4, .97, 'endpoint window', transform=bx.get_xaxis_transform(), fontsize=8.8, color=INK2, va='top')
    bx.axhline(0, color=INK, lw=1.1, zorder=1)
    for k, key in enumerate(SERIES):
        rr = [r for r in intervals if r['area_series'] == SERIES_ID[key]]
        med = np.array([r['median_change_au_ppm'] for r in rr]); lo = np.array([r['minimum_change_au_ppm'] for r in rr])
        hi = np.array([r['maximum_change_au_ppm'] for r in rr]); xx = x+(-.12 if k == 0 else .12)
        bx.errorbar(xx, med, yerr=[med-lo, hi-med], fmt='o' if k == 0 else 's', color=tone[key], ms=6, capsize=3.5, lw=1.4,
                    mec='white', mew=1, label=f'{SERIES_SHORT[key]}: median and min–max of 7 methods', zorder=3)
    bx.set_xticks(x, labels, fontsize=8.6); bx.set_ylabel('Change from previous acquisition (a.u.·ppm)'); tidy(bx)
    bx.legend(loc='lower left', fontsize=8.6, frameon=False)
    bx.set_title('Each interval: chemistry change and its spread across methods', loc='left', fontsize=11.5)
    caption(fig, 'Descriptive comparison, no significance test. Whiskers spanning zero mean methods disagree on the direction of that interval\'s change. '
            'Values: tables/chemistry_vs_algorithm.csv and tables/interval_change_vs_method_spread.csv.')
    pw.save(fig, 'boss_summary/09_chemistry_vs_algorithm_effect.png', 'Chemistry change versus algorithm spread',
            d.base/'tables/chemistry_vs_algorithm.csv')


def fig_normalized(pw, d, agreement):
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 6.2), sharey=True)
    for ax, key in zip(axes, SERIES):
        values = series(d, key)
        agg = [a for a in agreement if a['area_series'] == SERIES_ID[key]]
        ax.fill_between(d.t, [a['minimum_fraction'] for a in agg], [a['maximum_fraction'] for a in agg], color=ACCENT, alpha=.12, lw=0)
        for m in METHODS:
            f = values[m]/values[m].max()
            ax.plot(d.t, f, color=COLORS[m], lw=1.4 if m != 'unphased' else 1.2, ls='-' if m != 'unphased' else (0, (4, 2.5)),
                    marker=MARKERS[m], ms=6 if MARKERS[m] != '*' else 8.5, mec='white', mew=.7,
                    label=SHORT[m]+(' (not in band)' if m == 'unphased' else ''), zorder=3)
        k = int(np.argmax([a['cross_method_range_pp'] for a in agg])); a = agg[k]
        ax.text(.02, .97, f'Largest spread after normalization:\n{a["cross_method_range_pp"]:.1f} percentage points at '
                f'{a["acquisition_time"][11:16]}\n({a["method_at_minimum"]} lowest, {a["method_at_maximum"]} highest)',
                transform=ax.transAxes, va='top', fontsize=8.8, color=INK2)
        ax.set_title(SERIES_SHORT[key], loc='left', fontsize=11.5); ax.set_ylim(0, 1.08); tidy(ax); time_axis(ax, d)
        ax.set_xlabel(TIME_LABEL)
    axes[0].set_ylabel('Area ÷ the method\'s own maximum')
    axes[1].legend(loc='lower right', fontsize=8.6, ncol=2, frameon=False, bbox_to_anchor=(1, .12))
    fig.text(.5, .905, 'Normalized to compare trajectory shape; not absolute quantitation.', ha='center', fontsize=12, color=INK,
             fontweight='bold')
    caption(fig, 'Each method is divided by its own maximum over the run. Shaded band = range of the 7 automatic methods after normalization. '
            'Values: tables/normalized_target_trends.csv.')
    pw.save(fig, 'boss_summary/10_normalized_target_trends.png', 'Normalized target trends by phase method',
            d.base/'tables/normalized_target_trends.csv')


def fig_heatmap(pw, d, dev_rows):
    weak = weak_signal(d); limit = 10.
    fig, (*axes, cax) = plt.subplots(1, 3, figsize=(15.5, 6.4), gridspec_kw={'width_ratios': [1, 1, .035]})
    for ax, key in zip(axes, SERIES):
        M = np.array([[r['deviation_percent_from_median'] for r in dev_rows if r['area_series'] == SERIES_ID[key] and r['method'] == m]
                      for m in METHODS])
        image = ax.imshow(M, cmap='RdBu_r', vmin=-limit, vmax=limit, aspect='auto')
        for i in range(M.shape[0]):
            for j in range(M.shape[1]):
                v = M[i, j]; label = f'{v:+.1f}'
                ax.text(j, i, '0.0' if label in ('+0.0', '-0.0') else label, ha='center', va='center', fontsize=8.6,
                        color='white' if abs(v) >= .65*limit else INK, fontweight='bold' if abs(v) >= DECISION_GATE else 'normal')
        ax.axhline(.5, color=INK, lw=1.4)
        ax.set_yticks(range(len(METHODS)), [SHORT[m]+(' (not in median)' if m == 'unphased' else '') for m in METHODS], fontsize=9.5)
        ax.set_xticks(range(len(d.t)), [c[:5]+('*' if w else '') for c, w in zip(d.clock, weak)], fontsize=9.5)
        ax.set_xlabel(TIME_LABEL); ax.set_title(SERIES_SHORT[key], loc='left', fontsize=11.5)
        for side in ax.spines.values():
            side.set_visible(False)
    bar = fig.colorbar(image, cax=cax, extend='both')
    bar.set_label('Deviation from cross-method median (%)')
    caption(fig, 'Cell = 100 × (method − median of the 7 automatic methods) / median. Bold = |deviation| ≥ 5%. Colours saturate at ±10%; '
            '* = weak target signal (median S/N below 8), where percentages are unstable. Values: tables/target_area_deviation_from_median.csv.')
    pw.save(fig, 'boss_summary/11_target_area_deviation_heatmap.png', 'Target-area deviation from cross-method median',
            d.base/'tables/target_area_deviation_from_median.csv')


def fig_phase_quality(pw, d, pq, assessment):
    order = [(r, m) for r in ('2ppm', '7ppm') for m in CONTAMINATION]
    fig, ax = plt.subplots(figsize=(14.5, 8.4))
    ax.axvspan(-100, 0, color='#eef4fb', zorder=0); ax.axvspan(0, 180, color='#fbf0ea', zorder=0)
    ax.text(-50, -.95, 'less contamination than Unphased', ha='center', fontsize=10, color=INK2)
    ax.text(90, -.95, 'more contamination than Unphased', ha='center', fontsize=10, color=INK2)
    h = .19
    for row, m in enumerate(AUTOMATIC):
        for k, (region, metric) in enumerate(order):
            r = next(x for x in pq if x['method'] == m and x['region'] == region and x['metric'] == metric)
            v = r['median_percent_change_vs_unphased']; y = row+(k-1.5)*h
            overlap = r['objective_overlap']
            hatch = '////' if overlap.startswith(('direct', 'L2')) else '....' if overlap.startswith('partly') else None
            ax.barh(y, v, height=h*.9, color=REGION_METRIC_COLORS[(region, metric)], hatch=hatch, edgecolor='white', lw=0, zorder=2)
            count = r['lower_than_unphased_count'] if v < 0 else r['higher_than_unphased_count']
            ax.text(v+(2 if v >= 0 else -2), y, f'{v:+.0f}% ({count}/8)', va='center', ha='left' if v >= 0 else 'right',
                    fontsize=7.8, color=INK2, zorder=3)
        ax.text(183, row, category_label(m, assessment), va='center', ha='left', fontsize=10, fontweight='bold', color=INK)
    ax.axvline(0, color=INK, lw=2.2, zorder=4)
    ax.text(0, -.55, 'zero = same as Unphased', ha='center', va='center', fontsize=9.5, color=INK, fontweight='bold', zorder=5,
            bbox=dict(boxstyle='round,pad=.25', facecolor='white', edgecolor=INK, lw=.8))
    ax.set_yticks(range(len(AUTOMATIC)), [SHORT[m] for m in AUTOMATIC], fontsize=11)
    ax.set_xlim(-100, 180); ax.set_ylim(len(AUTOMATIC)-.3, -1.25)
    ax.set_xlabel('Median change versus Unphased across 8 acquisitions (%)'); tidy(ax, grid='x')
    ax.text(183, -.95, 'Category', fontsize=10, color=INK2)
    handles = [Patch(facecolor=REGION_METRIC_COLORS[k], label=f'{k[0][:-3]} ppm {METRIC_LABEL[k[1]]}') for k in order]
    handles += [Patch(facecolor='white', edgecolor=INK2, hatch='////', label='term is in the method\'s own objective (in-sample)'),
                Patch(facecolor='white', edgecolor=INK2, hatch='....', label='partly related to the objective')]
    ax.legend(handles=handles, loc='upper center', bbox_to_anchor=(.5, -.09), fontsize=8.8, frameon=False, ncol=3)
    caption(fig, 'Median of per-acquisition 100 × (method − Unphased)/Unphased for the ~2 ppm and ~7 ppm bands (package scoring windows). (n/8) = acquisitions moving '
            'in the bar\'s direction; consistent = ≥6/8. Categories shift with window centring, resolution and threshold (WHICH_ALGORITHM_IS_BEST.md section A; '
            'tables/strong_peak_sensitivity_summary.csv). Lower terms favour absorptive shape only for isolated positive peaks.')
    pw.save(fig, 'boss_summary/12_phase_quality_vs_unphased.png', 'Strong-peak phase quality versus Unphased',
            d.base/'tables/strong_peak_phase_quality_vs_unphased.csv')


def category_label(m, assessment):
    c = assessment[m]['phase_category']
    return {'combined_objective_v1': f'{c} (in-sample)', 'symmetry_objective': f'{c} (partly in-sample)'}.get(m, c)


AGREEMENT_COLORS = {3: '#cde2fb', 4: '#9ec5f4', 5: '#6da7ec', 6: '#3987e5', 7: '#1c5cab'}


def agreement_matrix(ax, summary, key, title):
    families = list(dict.fromkeys(r['rule_family'] for r in summary))
    width = max(sum(r['rule_family'] == f and r['area_series'] == SERIES_ID[key] for r in summary) for f in families)
    for i, family in enumerate(families):
        cells = [r for r in summary if r['rule_family'] == family and r['area_series'] == SERIES_ID[key]]
        for j, r in enumerate(cells):
            n = r['methods_agreeing_with_modal_of_7']; informative = r['modal_is_a_qualification_time']
            face = AGREEMENT_COLORS[max(3, n)] if informative else '#e7e6e1'
            ax.add_patch(plt.Rectangle((j, i), .96, .92, facecolor=face, edgecolor='white', lw=0,
                                       hatch=None if informative else '\\\\\\', zorder=1))
            dark = informative and n >= 6
            unit = '%/h' if r['threshold_units'] == '%/h' else '%'
            text = (f'≤{r["threshold"]:g}{unit}\n{r["modal_outcome"][:5]} · {n}/7\n'
                    + (f'{r["qualifying_methods_sustained"]}/{r["qualifying_automatic_methods"]} sustained' if informative else 'no endpoint'))
            ax.text(j+.48, i+.46, text, ha='center', va='center', fontsize=7.3, color='white' if dark else INK, zorder=2)
            if r['is_historical_criterion']:
                ax.add_patch(plt.Rectangle((j-.02, i-.04), 1., 1., fill=False, edgecolor=INK, lw=2.4, zorder=3))
    ax.set_xlim(-.05, width); ax.set_ylim(len(families), -.1)
    ax.set_yticks(np.arange(len(families))+.46, [FAMILY_LABEL[f] for f in families], fontsize=9)
    ax.set_xticks([]); ax.set_title(title, loc='left', fontsize=11.5)
    for side in ax.spines.values():
        side.set_visible(False)


def fig_endpoint_robustness(pw, d, summary):
    fig, ax = plt.subplots(figsize=(14.5, 9.6))
    agreement_matrix(ax, summary, 'fixed', 'Fixed-window completion area (the historical endpoint input) — 7 automatic phase methods')
    handles = [Patch(facecolor=AGREEMENT_COLORS[n], label=f'{n}/7 methods give the same first-qualifying time') for n in (3, 4, 5, 6, 7)]
    handles += [Patch(facecolor='#e7e6e1', hatch='\\\\\\', edgecolor='white', label='most methods never qualify (uninformative agreement)'),
                Patch(facecolor='white', edgecolor=INK, lw=2.4, label='historical criterion (5% gate)')]
    ax.legend(handles=handles, loc='upper center', bbox_to_anchor=(.42, -.01), ncol=4, fontsize=8.6, frameon=False)
    caption(fig, 'Each cell: gate, most common first-qualifying time among the 7 automatic methods, how many share it, and how many of the qualifying methods stay '
            'qualified at every later acquisition. Exploratory candidates; the historical rule is unchanged. Rules share the historical eligibility '
            '(≥6 points, ≥1 h). Detail: plateau_analysis/endpoint_metric_comparison.csv.')
    pw.save(fig, 'boss_summary/13_endpoint_robustness.png', 'Endpoint robustness across phase methods',
            d.base/'plateau_analysis/endpoint_rule_robustness_summary.csv')


def outcome_cmap(d):
    first, _ = endpoint_window(d)
    labels = [d.clock[i][:5] for i in range(first, len(d.t))]+['never']
    colors = list(OUTCOME_COLORS[:len(labels)-1])+[NEUTRAL]
    return labels, ListedColormap(colors), BoundaryNorm(np.arange(len(labels)+1)-.5, len(labels))


def fig_threshold_sensitivity(pw, d, table, transitions, agreement):
    labels, cmap, norm = outcome_cmap(d); first, _ = endpoint_window(d)
    fig, axes = plt.subplots(2, 1, figsize=(14.5, 9.4))
    for ax, key in zip(axes, ('fixed', 'picked')):
        grid = sorted({r['percent_gate'] for r in table if r['area_series'] == SERIES_ID[key]})
        M = np.array([[(r['first_completion_index']-first) if r['first_completion_index'] is not None else len(labels)-1
                       for r in table if r['area_series'] == SERIES_ID[key] and r['method'] == m] for m in METHODS])
        step = grid[1]-grid[0]
        ax.imshow(M, cmap=cmap, norm=norm, aspect='auto', extent=(grid[0]-step/2, grid[-1]+step/2, len(METHODS)-.5, -.5),
                  interpolation='nearest')
        ax.axhline(.5, color='white', lw=3)
        for i in range(len(METHODS)):
            ax.axhline(i+.5, color='white', lw=1)
        ax.axvline(5., color=INK, lw=2.2)
        for tr in (t for t in transitions if t['area_series'] == SERIES_ID[key]):
            i = METHODS.index(tr['method'])
            ax.plot([tr['transition_threshold_pct']]*2, [i-.42, i+.42], color='white', lw=1.6)
            ax.text(tr['transition_threshold_pct']+.04, i, f'{tr["transition_threshold_pct"]:.2f}', fontsize=8.2, color=INK,
                    va='center', ha='left', bbox=dict(boxstyle='round,pad=.15', facecolor='white', edgecolor='none', alpha=.85))
        ax.set_yticks(range(len(METHODS)), [SHORT[m]+(' (reference)' if m == 'unphased' else '') for m in METHODS], fontsize=9.5)
        ax.set_xlabel('Percent-change gate used by the historical rule (all other gates unchanged)')
        agg = next(a for a in agreement if a['area_series'] == SERIES_ID[key] and a['percent_gate'] == 5.)
        title = ('A. Fixed-window completion area (historical endpoint input)' if key == 'fixed'
                 else 'B. Same rule on the moving-boundary picked area (sensitivity check only)')
        ax.set_title(f'{title} — at 5%, {agg["automatic_methods_agreeing"]}/7 automatic methods share {agg["modal_outcome"][:5]}',
                     loc='left', fontsize=11)
        ax.set_xticks(np.arange(3, 10.01, .5))
        for side in ax.spines.values():
            side.set_visible(False)
    handles = [Patch(facecolor=c, edgecolor='#b5b4ae', label=('first completion at '+l if l != 'never' else 'never completes'))
               for l, c in zip(labels, list(OUTCOME_COLORS[:len(labels)-1])+[NEUTRAL])]
    handles.append(Line2D([], [], color=INK, lw=2.2, label='historical 5% gate'))
    axes[-1].legend(handles=handles, loc='upper center', bbox_to_anchor=(.5, -.2), ncol=len(handles), fontsize=9, frameon=False)
    caption(fig, 'Unchanged historical code (≥6 points, ≥1 h, last-4 slope gates, QC); only the percent gate varies. White ticks and labels mark the exact gate '
            'at which a method\'s outcome flips (inclusive ≤). Values: plateau_analysis/threshold_sensitivity.csv and threshold_transitions.csv.')
    pw.save(fig, 'boss_summary/14_endpoint_threshold_sensitivity.png', 'Endpoint threshold sensitivity',
            d.base/'plateau_analysis/threshold_sensitivity.csv')


def fig_interval_changes(pw, d, traces):
    _, start = endpoint_window(d)
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 6))
    for ax, key in zip(axes, ('fixed', 'picked')):
        rr = [r for r in traces if r['area_series'] == SERIES_ID[key]]
        ax.axhspan(-DECISION_GATE, DECISION_GATE, color=NEUTRAL, zorder=0)
        ax.axhline(0, color=INK, lw=1)
        idx = list(range(start+1, len(d.t)))
        for k, m in enumerate(METHODS):
            y = [next(r['adjacent_percent_change'] for r in rr if r['method'] == m and r['acquisition_id'] == d.order[i]) for i in idx]
            ax.plot(np.arange(len(idx))+(k-3.5)*.07, y, linestyle='none', marker=MARKERS[m], ms=7 if MARKERS[m] != '*' else 10,
                    color=COLORS[m], mec='white', mew=.6, label=SHORT[m], zorder=3)
        ax.set_xticks(range(len(idx)), [f'{d.clock[i-1][:5]}→{d.clock[i][:5]}' for i in idx], fontsize=9)
        ax.set_ylabel('Change from previous acquisition (%)'); tidy(ax)
        ax.set_title(SERIES_SHORT[key]+('' if key == 'picked' else ' (historical input)'), loc='left', fontsize=11.5)
    axes[1].legend(loc='lower left', fontsize=8.4, ncol=2, frameon=False)
    caption(fig, 'Adjacent-interval percent change per method inside the endpoint window; shaded band = ±5% historical gate. '
            'Values: plateau_analysis/candidate_metric_traces.csv.')
    pw.save(fig, 'plateau_analysis/interval_changes_by_method.png', 'Adjacent interval changes by phase method',
            d.base/'plateau_analysis/candidate_metric_traces.csv')


def fig_metric_trajectories(pw, d, traces):
    first, _ = endpoint_window(d); idx = list(range(first-1, len(d.t)))  # one not-yet-eligible point for context
    fig, axes = plt.subplots(3, 2, figsize=(14.5, 10.5), sharex=True)
    specs = (('normalized_slope_pct_per_h', '3-point slope ÷ mean (%/h)', (5, 10, 15, 20), True),
             ('cv_pct', '3-point CV (%)', (2.5, 3.5, 5, 7.5), False), ('range_pct', '3-point range ÷ mean (%)', (5, 7.5, 10, 15), False))
    for row, (metric, label, gates, signed) in enumerate(specs):
        for col, key in enumerate(('fixed', 'picked')):
            ax = axes[row, col]; rr = [r for r in traces if r['area_series'] == SERIES_ID[key]]
            ax.axvspan(d.t[0]-1, d.t[first]-.06, color=NEUTRAL, zorder=0)
            for g in gates:
                for s in ((1, -1) if signed else (1,)):
                    ax.axhline(s*g, color=MUTED, lw=.7, ls=(0, (2, 2)), zorder=1)
            for m in METHODS:
                y = [next(r.get(metric, float('nan')) for r in rr if r['method'] == m and r['acquisition_id'] == d.order[i]) for i in idx]
                ax.plot(d.t[idx], y, color=COLORS[m], marker=MARKERS[m], ms=5 if MARKERS[m] != '*' else 8, lw=1.1,
                        ls='-' if m != 'unphased' else (0, (4, 2.5)), label=SHORT[m], zorder=3)
            ax.set_ylabel(label); tidy(ax)
            ax.set_xticks(d.t[idx]); ax.set_xticklabels([d.clock[i][:5] for i in idx] if row == 2 else [])
            ax.set_xlim(d.t[idx[0]]-.05, d.t[-1]+.05)
            if row == 0:
                ax.set_title(SERIES_SHORT[key], loc='left', fontsize=11.5)
    axes[2, 0].set_xlabel(TIME_LABEL); axes[2, 1].set_xlabel(TIME_LABEL)
    axes[0, 1].legend(loc='lower left', fontsize=8.2, ncol=2, frameon=False)
    caption(fig, 'Dotted lines = evaluated tolerances (none is adopted). Each point uses the three most recent acquisitions only. Shaded = not '
            'yet eligible under the shared historical gate (<6 points or <1 h). Values: plateau_analysis/candidate_metric_traces.csv.')
    pw.save(fig, 'plateau_analysis/candidate_metric_trajectories.png', 'Candidate endpoint metrics over time',
            d.base/'plateau_analysis/candidate_metric_traces.csv')


def fig_outcome_matrix(pw, d, detail):
    labels, cmap, norm = outcome_cmap(d); first, _ = endpoint_window(d)
    rules = list(dict.fromkeys(r['rule_id'] for r in detail))
    fig, axes = plt.subplots(1, 2, figsize=(15, 16))
    for ax, key in zip(axes, ('fixed', 'picked')):
        M = np.full((len(rules), len(METHODS)), np.nan); text = {}
        for r in detail:
            if r['area_series'] != SERIES_ID[key]:
                continue
            i, j = rules.index(r['rule_id']), METHODS.index(r['method'])
            M[i, j] = (r['first_qualifying_index']-first) if r['first_qualifying_index'] is not None else len(labels)-1
            text[(i, j)] = ((r['first_qualifying_time'][:5] if r['first_qualifying_index'] is not None else '—')
                            + ('*' if r['later_departure_status'].startswith('qualification not sustained') else ''))
        ax.imshow(M, cmap=cmap, norm=norm, aspect='auto')
        for (i, j), s in text.items():
            ax.text(j, i, s, ha='center', va='center', fontsize=7, color='white' if M[i, j] in (0, 1) else INK)
        ax.axvline(.5, color='white', lw=3)
        ax.set_xticks(range(len(METHODS)), [SHORT[m] for m in METHODS], rotation=35, ha='right', fontsize=9)
        ax.set_yticks(range(len(rules)), [next(r['rule_label'] for r in detail if r['rule_id'] == rid) for rid in rules], fontsize=7.6)
        ax.set_title(SERIES_SHORT[key], loc='left', fontsize=11.5)
        for side in ax.spines.values():
            side.set_visible(False)
    caption(fig, 'First qualifying acquisition per rule and method (— = never; * = qualification not sustained at a later acquisition). Exploratory; '
            'the historical rule is unchanged. Values: plateau_analysis/endpoint_metric_comparison.csv.')
    pw.save(fig, 'plateau_analysis/endpoint_outcome_matrix.png', 'Endpoint outcome by rule and phase method',
            d.base/'plateau_analysis/endpoint_metric_comparison.csv')


def fig_internal_reference(pw, d, ref_rows, audit, sp):
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(14.5, 5.8), gridspec_kw={'width_ratios': [1.3, 1]})
    for region, color, marker in (('2ppm', '#184f95', 'o'), ('7ppm', '#d95926', 's')):
        v = np.array([r[f'{region}_magnitude_area_phase_invariant'] for r in ref_rows])
        ax.plot(d.t, 100*(v/v[0]-1), color=color, marker=marker, lw=1.6, label=f'~{region[:-3]} ppm band (phase-invariant magnitude area)')
    med = sp['picked']['median']
    ax.axvspan(d.t[0], d.t[1], color=NEUTRAL, zorder=0)
    ax.text((d.t[0]+d.t[1])/2, 6, f'target arrives:\nmedian area {med[0]:.1f} → {med[1]:.1f} a.u.·ppm', ha='center', va='top',
            fontsize=8.6, color=INK2)
    ax.axhline(0, color=INK, lw=.9); ax.set_ylabel('Change relative to 09:13 (%)'); tidy(ax); time_axis(ax, d); ax.set_xlabel(TIME_LABEL)
    ax.set_ylim(-25, 8); ax.legend(loc='lower right', fontsize=8.6, frameon=False)
    ax.set_title('Strong solvent bands are not constant', loc='left', fontsize=11.5)
    x = np.arange(len(METHODS)); w = .38
    for k, (region, color) in enumerate((('2ppm', '#184f95'), ('7ppm', '#d95926'))):
        cv = [audit['quantitative_real_area_cv_pct_by_method'][region][m] for m in METHODS]
        bx.bar(x+(k-.5)*w, cv, width=w*.92, color=color, label=f'~{region[:-3]} ppm band', zorder=2)
    bx.set_xticks(x, [SHORT[m] for m in METHODS], rotation=35, ha='right', fontsize=9)
    bx.set_ylabel('CV over 8 acquisitions of the Real band area (%)'); tidy(bx); bx.legend(fontsize=8.6, frameon=False)
    bx.set_title('…and their Real areas depend on phase method', loc='left', fontsize=11.5)
    caption(fig, 'Rejected internal reference: no internal standard was recorded; receiver gain, scans, pulse width and temperature are identical in all FIDs; '
            'the toluene bands change when the target arrives in the flow stream. Values: tables/internal_reference_audit.csv.')
    pw.save(fig, 'plateau_analysis/internal_reference_audit.png', 'Internal-reference audit of strong bands',
            d.base/'tables/internal_reference_audit.csv')


def fig_deck_trend(pw, d, sp, chem):
    """Slide-sized companion of Figs. 08 and 10 (picked area only, larger type) for the presentation."""
    values = d.picked; s = sp['picked']; row = next(r for r in chem if r['area_series'] == 'picked_moving_boundary')
    with plt.rc_context({'font.size': 14}):
        fig, (ax, bx) = plt.subplots(1, 2, figsize=(13.33, 5.6))
        for m in AUTOMATIC:
            ax.plot(d.t, values[m], color='#c6c5bf', lw=1, zorder=2)
        ax.fill_between(d.t, s['minimum'], s['maximum'], color=ACCENT, alpha=.18, lw=0, zorder=1, label='range of 7 automatic methods')
        ax.plot(d.t, s['median'], color=ACCENT_DARK, lw=2.6, marker='o', ms=6, mec='white', mew=1.2, zorder=4, label='cross-method median')
        ax.plot(d.t, values['unphased'], color=MUTED, lw=1.4, ls=(0, (4, 2.5)), zorder=3, label='Unphased (not in median)')
        ax.text(.03, .97, f'change over the run: {row["sequence_dynamic_range_au_ppm"]:.1f}\ntypical method spread: '
                f'{row["median_cross_method_range_au_ppm"]:.1f}\n(≈{row["R_dynamic_range_over_median_algorithm_range"]:.0f}× smaller)',
                transform=ax.transAxes, va='top', fontsize=13, color=INK)
        ax.set_ylabel('Picked target area (a.u.·ppm)'); ax.set_ylim(0, s['maximum'].max()*1.15)
        ax.legend(loc='lower right', fontsize=11.5, frameon=False); tidy(ax); time_axis(ax, d)
        ax.set_title('Area, all methods', loc='left', fontsize=15)
        for m in METHODS:
            bx.plot(d.t, values[m]/values[m].max(), color=COLORS[m], lw=1.6, ls='-' if m != 'unphased' else (0, (4, 2.5)),
                    marker=MARKERS[m], ms=6 if MARKERS[m] != '*' else 9, mec='white', mew=.7, label=SHORT[m])
        bx.set_ylabel('Area ÷ method\'s own maximum'); bx.set_ylim(0, 1.08); tidy(bx); time_axis(bx, d)
        bx.set_title('Shape only (normalized; not quantitation)', loc='left', fontsize=15)
        bx.legend(loc='lower right', fontsize=11, ncol=2, frameon=False)
        for a in (ax, bx):
            a.tick_params(labelsize=11.5); a.tick_params(axis='x', labelrotation=45)
        pw.save(fig, 'boss_summary/.build/assets/trend_robustness_deck.png', 'Target trend: chemistry change versus algorithm spread',
                d.base/'tables/method_spread_by_time.csv')



def fig_deck_phase_quality(pw, d, pq, assessment):
    """Slide-sized companion of Fig. 12 (same data, larger type)."""
    order = [(r, m) for r in ('2ppm', '7ppm') for m in CONTAMINATION]
    with plt.rc_context({'font.size': 14}):
        fig, ax = plt.subplots(figsize=(9.6, 7.0))
        ax.axvspan(-100, 0, color='#eef4fb', zorder=0); ax.axvspan(0, 175, color='#fbf0ea', zorder=0)
        h = .2
        for row, m in enumerate(AUTOMATIC):
            for k, (region, metric) in enumerate(order):
                r = next(x for x in pq if x['method'] == m and x['region'] == region and x['metric'] == metric)
                v = r['median_percent_change_vs_unphased']; y = row+(k-1.5)*h
                overlap = r['objective_overlap']
                hatch = '////' if overlap.startswith(('direct', 'L2')) else '....' if overlap.startswith('partly') else None
                ax.barh(y, v, height=h*.9, color=REGION_METRIC_COLORS[(region, metric)], hatch=hatch, edgecolor='white', lw=0, zorder=2)
                ax.text(v+(2.5 if v >= 0 else -2.5), y, f'{v:+.0f}%', va='center', ha='left' if v >= 0 else 'right', fontsize=9.5,
                        color=INK2, zorder=3)
            star = '*' if m in ('combined_objective_v1', 'symmetry_objective') else ''
            ax.text(178, row, assessment[m]['phase_category']+star, va='center', ha='left', fontsize=12.5, fontweight='bold', color=INK)
        ax.axvline(0, color=INK, lw=2.4, zorder=4)
        ax.text(0, -.72, 'zero = same as Unphased', ha='center', va='center', fontsize=12, fontweight='bold', color=INK, zorder=5,
                bbox=dict(boxstyle='round,pad=.25', facecolor='white', edgecolor=INK, lw=.8))
        ax.text(-50, -1.25, 'less contamination', ha='center', fontsize=12.5, color=INK2)
        ax.text(87, -1.25, 'more contamination', ha='center', fontsize=12.5, color=INK2)
        ax.set_yticks(range(len(AUTOMATIC)), [SHORT[m] for m in AUTOMATIC], fontsize=13.5)
        ax.set_xlim(-100, 175); ax.set_ylim(len(AUTOMATIC)-.35, -1.5)
        ax.set_xlabel('Median change versus Unphased, 8 acquisitions (%)'); tidy(ax, grid='x'); ax.tick_params(labelsize=12)
        handles = [Patch(facecolor=REGION_METRIC_COLORS[k], label=f'{k[0][:-3]} ppm {METRIC_LABEL[k[1]].split(" ")[0]}') for k in order]
        handles.append(Patch(facecolor='white', edgecolor=INK2, hatch='////', label='in-sample term (* category partly in-sample)'))
        ax.legend(handles=handles, loc='upper center', bbox_to_anchor=(.5, -.1), fontsize=11, frameon=False, ncol=2)
        pw.save(fig, 'boss_summary/.build/assets/phase_quality_deck.png', 'Strong-peak contamination versus Unphased',
                d.base/'tables/strong_peak_phase_quality_vs_unphased.csv')


def fig_deck_threshold(pw, d, table, transitions, agreement):
    """Slide-sized companion of Fig. 14 (same data, larger type)."""
    labels, cmap, norm = outcome_cmap(d); first, _ = endpoint_window(d)
    with plt.rc_context({'font.size': 14}):
        fig, axes = plt.subplots(2, 1, figsize=(9.6, 6.6))
        for ax, key in zip(axes, ('fixed', 'picked')):
            grid = sorted({r['percent_gate'] for r in table if r['area_series'] == SERIES_ID[key]})
            M = np.array([[(r['first_completion_index']-first) if r['first_completion_index'] is not None else len(labels)-1
                           for r in table if r['area_series'] == SERIES_ID[key] and r['method'] == m] for m in METHODS])
            step = grid[1]-grid[0]
            ax.imshow(M, cmap=cmap, norm=norm, aspect='auto', extent=(grid[0]-step/2, grid[-1]+step/2, len(METHODS)-.5, -.5),
                      interpolation='nearest')
            for i in range(len(METHODS)):
                ax.axhline(i+.5, color='white', lw=1.2)
            ax.axvline(5., color=INK, lw=2.6)
            for tr in (t for t in transitions if t['area_series'] == SERIES_ID[key]):
                i = METHODS.index(tr['method'])
                ax.text(tr['transition_threshold_pct']+.05, i, f'{tr["transition_threshold_pct"]:.2f}', fontsize=10.5, color=INK, va='center',
                        ha='left', bbox=dict(boxstyle='round,pad=.12', facecolor='white', edgecolor='none', alpha=.9))
            ax.set_yticks(range(len(METHODS)), [SHORT[m] for m in METHODS], fontsize=11.5)
            ax.set_xticks(np.arange(3, 10.01, 1.)); ax.tick_params(axis='x', labelsize=12)
            agg = next(a for a in agreement if a['area_series'] == SERIES_ID[key] and a['percent_gate'] == 5.)
            ax.set_title(('Fixed-window area (historical input)' if key == 'fixed' else 'Picked area (sensitivity check)')
                         + f' — at 5%: {agg["automatic_methods_agreeing"]}/7 agree', loc='left', fontsize=13.5)
            for side in ax.spines.values():
                side.set_visible(False)
        axes[-1].set_xlabel('Percent-change gate (%), other historical gates unchanged')
        handles = [Patch(facecolor=c, edgecolor='#b5b4ae', label=(l if l != 'never' else 'never'))
                   for l, c in zip(labels, list(OUTCOME_COLORS[:len(labels)-1])+[NEUTRAL])]
        handles.append(Line2D([], [], color=INK, lw=2.6, label='5% gate'))
        axes[-1].legend(handles=handles, loc='upper center', bbox_to_anchor=(.45, -.3), ncol=len(handles), fontsize=11.5, frameon=False,
                        title='first completion', title_fontsize=11.5)
        pw.save(fig, 'boss_summary/.build/assets/threshold_sensitivity_deck.png', 'Endpoint gate sensitivity',
                d.base/'plateau_analysis/threshold_sensitivity.csv')


def fig_effective_phase(pw, d, eff_rows):
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.6), sharey=True)
    for ax, region in zip(axes, ('2ppm', 'target', '7ppm')):
        for m in AUTOMATIC:
            y = [r['effective_phase_deg'] for r in eff_rows if r['method'] == m and r['region'] == region]
            ax.plot(d.t, y, color=COLORS[m], marker=MARKERS[m], ms=6 if MARKERS[m] != '*' else 9, lw=1.2, label=SHORT[m])
        ax.axhline(0, color=MUTED, lw=1, ls=(0, (4, 2.5)))
        ax.set_title({'2ppm': '~2 ppm band centre', 'target': '~5.8 ppm target centre', '7ppm': '~7 ppm band centre'}[region],
                     loc='left', fontsize=11.5)
        tidy(ax); time_axis(ax, d); ax.tick_params(axis='x', labelrotation=45)
    axes[0].set_ylabel('Phase applied at that position (degrees)')
    axes[1].set_xlabel(TIME_LABEL)
    axes[1].legend(loc='upper center', bbox_to_anchor=(.5, -.24), fontsize=8.8, ncol=7, frameon=False)
    caption(fig, 'Effective phase = sign × (P0 + P1·k/N) at the shared magnitude centre; dashed 0° = Unphased. Combined and Symmetry alternate at 2 ppm mainly because their '
            'objective\'s window centre alternates between two adjacent scoring-grid points (in-sample tracking; tables/strong_peak_metric_optimum.csv). '
            'Values: tables/effective_phase_by_acquisition.csv.')
    pw.save(fig, 'method_comparisons/effective_phase_by_acquisition.png', 'Effective applied phase by acquisition',
            d.base/'tables/effective_phase_by_acquisition.csv')


# ---------------------------------------------------------------------------- per-method assessment (Task 2)
def contamination_text(pq, m, region):
    parts = []
    for metric in CONTAMINATION:
        r = next(x for x in pq if x['method'] == m and x['region'] == region and x['metric'] == metric)
        v = r['median_percent_change_vs_unphased']
        count = r['lower_than_unphased_count'] if v < 0 else r['higher_than_unphased_count']
        tag = '' if r['objective_overlap'] == 'independent' else ' †'
        parts.append(f'{METRIC_LABEL[metric]} {v:+.1f}% ({count}/8 {"lower" if v < 0 else "higher"}){tag}')
    return '; '.join(parts)


def endpoint_assessment(d, m, margins, detail):
    first, _ = endpoint_window(d)
    gate = next(g for g in margins if g['area_series'] == 'completion_fixed_window' and g['method'] == m
                and g['acquisition_time'] == d.manifest[first]['acquisition_time'])
    outcome = next(r for r in d.completion if r['method'] == m)
    when = outcome['first_retrospective_completion'][11:19] if outcome['first_retrospective_completion'] else None
    departure = outcome['later_departure_warning'] == 'True'
    last_only = when == d.clock[-1]
    near = min(abs(gate['percent_gate_margin_pp']), abs(gate['relative_slope_margin_pct_per_h'])) < 1.
    fixed_rows = [r for r in detail if r['area_series'] == 'completion_fixed_window']
    rules = list(dict.fromkeys(r['rule_id'] for r in fixed_rows))
    agree = 0
    for rid in rules:
        auto = [r['first_qualifying_index'] for r in fixed_rows if r['rule_id'] == rid and r['method'] in AUTOMATIC]
        modal = modal_outcome(auto)[0]
        agree += next(r['first_qualifying_index'] for r in fixed_rows if r['rule_id'] == rid and r['method'] == m) == modal
    if near or last_only:
        category = 'Weak'
    elif when and departure:
        category = 'Mixed'
    else:
        category = 'Acceptable'
    if when:
        result = f'{when} ({outcome["status"]}' + (', later departure' if departure else '') + \
                 (', final acquisition — no later confirmation' if last_only else '') + ')'
    else:
        result = f'never ({outcome["status"]})'
    return {'historical_completion': result, 'endpoint_category': category,
            'critical_prefix': d.clock[first], 'critical_percent_changes': gate['recent_interval_percent_changes'],
            'percent_gate_margin_pp': gate['percent_gate_margin_pp'], 'relative_slope_margin_pct_per_h': gate['relative_slope_margin_pct_per_h'],
            'absolute_slope_margin_au_ppm_per_h': gate['absolute_slope_margin'], 'binding_gate': gate['binding_gate'],
            'within_1_unit_of_a_gate': near, 'qualifies_only_at_final_acquisition': last_only,
            'candidate_rule_agreement': f'{agree}/{len(rules)}', 'candidate_rule_agreement_fraction': agree/len(rules)}


def assess(d, pq, stability, eff_summary, margins, detail):
    summary = {r['method']: r for r in d.summary}; out = {}
    for m in METHODS:
        row = {'method': m, 'display_method': SHORT[m], 'objective': OBJECTIVE_TEXT.get(m, 'none: FFT before phase correction'),
               'convergence': sorted({d.metrics[(i, m)]['convergence_status'] for i in d.order}),
               'successful_acquisitions': sum(d.metrics[(i, m)]['success'] == 'True' for i in d.order)}
        if m in AUTOMATIC:
            c2, c7 = region_category(pq, m, '2ppm'), region_category(pq, m, '7ppm')
            eff = {r['region']: r['rms_deviation_across_acquisitions_deg'] for r in eff_summary if r['method'] == m}
            row.update({'phase_2ppm': c2, 'phase_7ppm': c7, 'phase_category': phase_category(c2, c7),
                        'phase_2ppm_evidence': contamination_text(pq, m, '2ppm'), 'phase_7ppm_evidence': contamination_text(pq, m, '7ppm'),
                        'objective_overlap_with_core_terms': sorted(k for k in OBJECTIVE_OVERLAP.get(m, {}) if k in CONTAMINATION),
                        'quiet_3p6_4p5_prebaseline_rms_median_change_pct': float(summary[m]['quiet_3p6_4p5_prebaseline_rms_median_percent_change']),
                        'quiet_10_11_prebaseline_rms_median_change_pct': float(summary[m]['quiet_10_11_prebaseline_rms_median_percent_change']),
                        'effective_phase_rms_2ppm_deg': eff['2ppm'], 'effective_phase_rms_target_deg': eff['target'],
                        'effective_phase_rms_7ppm_deg': eff['7ppm']})
        else:
            row.update({'phase_2ppm': 'reference', 'phase_7ppm': 'reference', 'phase_category': 'reference',
                        'phase_2ppm_evidence': 'reference (no phase applied)', 'phase_7ppm_evidence': 'reference (no phase applied)'})
        row.update({k: v for k, v in stability[m].items() if k not in ('method', 'display_method')})
        row.update(endpoint_assessment(d, m, margins, detail))
        out[m] = row
    independent = [m for m in AUTOMATIC if not out[m]['objective_overlap_with_core_terms']]
    acceptable = [m for m in independent if out[m]['phase_category'] in ('Strong', 'Acceptable')]
    for m in METHODS:
        out[m]['overall_interpretation'] = interpretation(m, out, acceptable)
    return out


def interpretation(m, out, acceptable, sens_facts=None):
    r = out[m]
    if m == 'unphased':
        return ('No-phase reference. Its target areas sit within the method spread (median |deviation| '
                f'{r["picked_median_abs_deviation_pct"]:.1f}%), but its trend shape deviates up to {r["picked_max_abs_normalized_shape_deviation_pp"]:.1f} pp at the end.')
    bits = []
    if m == 'production':
        bits.append('historical stored phase, not ground truth')
    if m in acceptable and len(acceptable) == 1:
        bits.append('smallest worst-case strong-peak change of the independent methods in every scoring variant tested; its 2 ppm changes '
                    'alternate with the diagnostic window centre, and its category is not unique under other reasonable scoring choices')
    elif r['phase_category'] == 'Strong' and r['objective_overlap_with_core_terms']:
        bits.append('largest strong-peak reductions only with the package windows, whose centres its own objective windows share to within one '
                    'scoring-grid point (in-sample); '
                    'Acceptable at full-resolution scoring')
    elif r['phase_category'] == 'Acceptable' and r['objective_overlap_with_core_terms']:
        bits.append('favourable at 7 ppm; its odd-Real gains are partly in-sample, and it is Mixed at full-resolution scoring')
    elif r['phase_category'] == 'Mixed':
        bits.append(f'region-dependent phase quality (2 ppm {r["phase_2ppm"].lower()}, 7 ppm {r["phase_7ppm"].lower()})')
    elif r['phase_category'] == 'Weak':
        bits.append(f'weakest strong-peak shape (7 ppm {r["phase_7ppm"].lower()})')
    if r.get('effective_phase_rms_2ppm_deg', 0) > 10:
        bits.append(f'2 ppm phase alternates (±{r["effective_phase_rms_2ppm_deg"]:.0f}° rms), mainly tracking its objective\'s alternating window centre')
    if r['target_trend_category'] in ('Mixed', 'Weak'):
        bits.append(f'least consistent target trend ({r["picked_max_abs_normalized_shape_deviation_pp"]:.1f} pp shape deviation)')
    elif abs(r['picked_median_signed_deviation_pct']) > DECISION_GATE/2:
        bits.append(f'target trend matches the ensemble but areas sit {r["picked_median_signed_deviation_pct"]:+.1f}% from the median')
    else:
        bits.append('target trend matches the ensemble')
    bits.append({'Weak': 'historical endpoint decided within 1 unit of a gate or only at the final point',
                 'Mixed': 'historical endpoint qualifies with margin at 11:08 but later departs',
                 'Acceptable': 'historical endpoint robust for this method'}[r['endpoint_category']])
    text = '; '.join(bits)
    return text[0].upper()+text[1:]+'.'


# ---------------------------------------------------------------------------- orchestration
def compute(d):
    spread_rows, sp = spread(d)
    chem, intervals = chemistry_vs_algorithm(d, sp)
    norm_rows, norm_agree = normalized(d)
    dev_rows = deviations(d, sp)
    stability = target_stability(d, sp, norm_rows)
    pq = phase_quality(d)
    eff_rows, eff_summary = effective_phase(d)
    detail, summary = evaluate_rules(d)
    traces = metric_traces(d)
    thr_rows, transitions, thr_agree = threshold_sensitivity(d)
    margins = gate_margins(d)
    uncertainty = uncertainty_summary(d, traces, sp)
    ref_rows, ref_audit = internal_reference(d)
    cp = change_points(d)
    sens = strong_peak_sensitivity(d)
    assessment = assess(d, pq, stability, eff_summary, margins, detail)
    return SimpleNamespace(spread_rows=spread_rows, sp=sp, chem=chem, intervals=intervals, norm_rows=norm_rows, norm_agree=norm_agree,
        dev_rows=dev_rows, stability=stability, pq=pq, eff_rows=eff_rows, eff_summary=eff_summary, detail=detail, summary=summary,
        traces=traces, thr_rows=thr_rows, transitions=transitions, thr_agree=thr_agree, margins=margins, uncertainty=uncertainty,
        ref_rows=ref_rows, ref_audit=ref_audit, cp=cp, cp_summary=change_point_summary(cp), assessment=assessment,
        sens=sens, sens_facts=sensitivity_facts(d, sens))


def boss_agreement_rows(d, res):
    table = []
    for m in METHODS:
        a = res.assessment[m]
        table.append({'method': SHORT[m], 'role': a['role'],
            'median_abs_target_area_deviation_from_cross_method_median_pct': round(a['picked_median_abs_deviation_pct'], 2),
            'max_abs_target_area_deviation_pct': round(a['picked_max_abs_deviation_pct'], 2),
            'max_deviation_time': a['picked_max_abs_deviation_time'],
            'max_abs_target_area_deviation_excluding_weak_pct': round(a['picked_max_abs_deviation_excluding_weak_pct'], 2),
            'target_detection': a['target_detection'],
            'pearson_r_vs_cross_method_median_including_self': round(a['picked_pearson_r_vs_median'], 4),
            'pearson_r_vs_leave_one_out_median': round(a['picked_pearson_r_vs_leave_one_out_median'], 4),
            'phase_quality_2ppm': f'{a["phase_2ppm"]}: {a["phase_2ppm_evidence"]}',
            'phase_quality_7ppm': f'{a["phase_7ppm"]}: {a["phase_7ppm_evidence"]}',
            'completion_result_historical_rule': a['historical_completion'],
            'median_signed_area_deviation_pct': round(a['picked_median_signed_deviation_pct'], 2),
            'spearman_rho_vs_leave_one_out_median': round(a['picked_spearman_rho_vs_leave_one_out_median'], 3),
            'fixed_window_median_abs_deviation_pct': round(a['fixed_median_abs_deviation_pct'], 2),
            'notes': ('Area = picked target area (moving boundaries). Cross-method median = median of the 7 automatic methods (Unphased not '
                      'included). abs = absolute deviation. max_abs includes the weak-signal acquisition '
                      f'({", ".join(c for c, w in zip(d.clock, weak_signal(d)) if w)}); the _excluding_weak column omits it. Pearson r vs the '
                      'cross-method median includes the method itself in that median (not Unphased); the leave-one-out column removes it. '
                      '† = term is (partly) in the method\'s own objective. Agreement with the median is centrality, not accuracy.')})
    return table


def rejected_rows():
    base = {'threshold': None, 'threshold_units': '', 'persistence': False, 'area_series': 'both', 'method': 'all',
            'display_method': 'all', 'role': '', 'first_qualifying_time': 'not evaluated'}
    return [{**base, 'rule_family': 'uncertainty_aware_change', 'rule_id': 'uncertainty_aware_change',
             'rule_label': '|ΔA| / propagated area uncertainty', 'later_departure_status': 'not evaluated',
             'status': 'NOT USABLE: no defensible area uncertainty (white-noise SE is a documented lower bound); see uncertainty_aware_change.csv'},
            {**base, 'rule_family': 'internal_reference_ratio', 'rule_id': 'internal_reference_ratio',
             'rule_label': 'A_target / A_reference (2 ppm or 7 ppm band)', 'later_departure_status': 'not evaluated',
             'status': 'REJECTED: strong bands are not validated internal references; see internal_reference_audit.json'}]


def write_tables(out, d, res):
    out = Path(out)
    write_rows(out/'tables/method_spread_by_time.csv', res.spread_rows)
    write_rows(out/'tables/chemistry_vs_algorithm.csv', res.chem)
    write_rows(out/'tables/interval_change_vs_method_spread.csv', res.intervals)
    write_rows(out/'tables/normalized_target_trends.csv', res.norm_rows)
    write_rows(out/'tables/normalized_shape_agreement.csv', res.norm_agree)
    write_rows(out/'tables/target_area_deviation_from_median.csv', res.dev_rows)
    write_rows(out/'tables/strong_peak_phase_quality_vs_unphased.csv', res.pq)
    write_rows(out/'tables/strong_peak_sensitivity_terms.csv', res.sens.terms)
    write_rows(out/'tables/strong_peak_sensitivity_categories.csv', res.sens.categories)
    write_rows(out/'tables/strong_peak_sensitivity_summary.csv', res.sens.summary)
    write_rows(out/'tables/strong_peak_window_centres.csv', res.sens.centres)
    write_rows(out/'tables/strong_peak_metric_optimum.csv', res.sens.optimum)
    write_rows(out/'tables/effective_phase_by_acquisition.csv', res.eff_rows)
    write_rows(out/'tables/effective_phase_reproducibility.csv', res.eff_summary)
    write_rows(out/'tables/method_agreement_detail.csv', list(res.stability.values()))
    write_rows(out/'tables/algorithm_assessment.csv', list(res.assessment.values()))
    write_rows(out/'boss_summary/METHOD_AGREEMENT_SUMMARY.csv', boss_agreement_rows(d, res))
    write_rows(out/'tables/internal_reference_audit.csv', res.ref_rows)
    write_rows(out/'plateau_analysis/endpoint_metric_comparison.csv', res.detail+rejected_rows())
    write_rows(out/'plateau_analysis/endpoint_rule_robustness_summary.csv', res.summary)
    write_rows(out/'plateau_analysis/candidate_metric_traces.csv', res.traces)
    write_rows(out/'plateau_analysis/threshold_sensitivity.csv', res.thr_rows)
    write_rows(out/'plateau_analysis/threshold_transitions.csv', res.transitions)
    write_rows(out/'plateau_analysis/threshold_agreement.csv', res.thr_agree)
    write_rows(out/'plateau_analysis/historical_gate_margins.csv', res.margins)
    write_rows(out/'plateau_analysis/uncertainty_aware_change.csv',
               [r for r in res.traces if r['area_series'] == 'completion_fixed_window' and 'uncertainty_aware_z' in r])
    write_json(out/'plateau_analysis/uncertainty_aware_change_summary.json', res.uncertainty)
    write_json(out/'plateau_analysis/internal_reference_audit.json', res.ref_audit)
    write_rows(out/'plateau_analysis/change_point_analysis.csv', res.cp)
    write_json(out/'plateau_analysis/change_point_summary.json', res.cp_summary)
    write_json(out/'tables/refinement_definitions.json', definitions(d))


def definitions(d):
    first, start = endpoint_window(d)
    return {'automatic_phase_methods': [SHORT[m] for m in AUTOMATIC], 'reference': 'Unphased FFT (not part of any median)',
        'cross_method_median': 'median of the 7 automatic phase methods at each acquisition; no method is ground truth',
        'percent_range_of_median': '100 × (max − min) / median across the 7 automatic methods',
        'cv_percent': '100 × sample SD (ddof=1) / mean across the 7 automatic methods',
        'deviation_percent_from_median': '100 × (method − median) / median',
        'R': 'dynamic range of the median trajectory (max − min) ÷ median of the per-acquisition cross-method ranges; descriptive',
        'R_conservative_over_maximum_algorithm_range': 'dynamic range of the median trajectory ÷ the largest per-acquisition cross-method range',
        'R_excluding_weak_signal': ('dynamic range of the median trajectory over all acquisitions ÷ median cross-method range over acquisitions '
                                    'without a weak target signal (the weak acquisition is excluded from the spread statistic only)'),
        'R_endpoint_window': 'max − min of the median trajectory inside the endpoint window ÷ median cross-method range in that window',
        'endpoint_window': f'{d.clock[start]}–{d.clock[-1]}: every acquisition inside a recent window the historical rule can evaluate '
                           f'(first evaluable acquisition {d.clock[first]})',
        'weak_signal_acquisition': f'median automatic-method target S/N below the historical minimum S/N ({d.minimum_snr:g})',
        'consistent_change': f'≥{CONSISTENT} of 8 acquisitions changed in the same direction versus Unphased',
        'region_category': 'Better = both contamination terms consistently lower; Partly better = one consistently lower, other not consistently changed; '
                           '≈ Unphased = neither consistently changed; Mixed = one consistently lower, one consistently higher; Worse = at least one '
                           'consistently higher and none consistently lower',
        'independence': 'a term is independent for a method when it is not in that method\'s objective; ACME and Peak minima start from the DX phase',
        'strong_peak_sensitivity': 'categories re-scored under 8 window-centring/resolution variants, thresholds 5-7/8 and odd-Real vs asymmetry; '
                                   'see tables/strong_peak_sensitivity_*.csv',
        'phase_category': 'Strong = Better in both regions; Acceptable = no Worse/Mixed region and at least one improved; '
                          'Mixed = improved in one region, Worse/Mixed in the other; Weak = no improved region',
        'target_trend_category': f'max |normalized-shape deviation| ≤ {DECISION_GATE/2:g} pp and 8/8 detections = Strong; ≤ {DECISION_GATE:g} pp '
                                 f'Acceptable; ≤ {2*DECISION_GATE:g} pp Mixed; otherwise Weak (anchored to the 5% decision gate)',
        'endpoint_category': 'Weak = historical outcome within 1 unit (pp or %/h) of a gate at the first evaluable acquisition, or '
                             'qualifies only at the final acquisition; Mixed = qualifies with margin but later departs; Acceptable otherwise',
        'endpoint_rules': 'online prefix replay; shared historical eligibility (≥6 points, ≥1 h, recent QC); "confirmed" = qualifies at two '
                          'consecutive evaluable acquisitions and is reported at the confirming one',
        'rolling_window': '3 most recent acquisitions; OLS slope with t(0.975, df=1) = 12.71 confidence interval (reported, too wide to use)',
        'objective_overlap': OBJECTIVE_OVERLAP, 'objective_overlap_regions': {**OBJECTIVE_OVERLAP_REGIONS, 'note': 'Peak minima overlap applies at 2 ppm only; others in both regions'},
        'decision_gate_anchor_pct': DECISION_GATE}


def figures(pw, d, res):
    fig_method_spread(pw, d, res.sp)
    fig_chemistry_vs_algorithm(pw, d, res.chem, res.intervals)
    fig_normalized(pw, d, res.norm_agree)
    fig_heatmap(pw, d, res.dev_rows)
    fig_phase_quality(pw, d, res.pq, res.assessment)
    fig_endpoint_robustness(pw, d, res.summary)
    fig_threshold_sensitivity(pw, d, res.thr_rows, res.transitions, res.thr_agree)
    fig_interval_changes(pw, d, res.traces)
    fig_metric_trajectories(pw, d, res.traces)
    fig_outcome_matrix(pw, d, res.detail)
    fig_internal_reference(pw, d, res.ref_rows, res.ref_audit, res.sp)
    fig_effective_phase(pw, d, res.eff_rows)
    fig_deck_trend(pw, d, res.sp, res.chem)
    fig_deck_phase_quality(pw, d, res.pq, res.assessment)
    fig_deck_threshold(pw, d, res.thr_rows, res.transitions, res.thr_agree)


def run(out, pw, base=None):
    """Compute, tabulate, plot and document. `pw` is the package PlotWriter (dataset titles + manifest)."""
    out = Path(out); d = load(base or out)
    res = compute(d)
    write_tables(out, d, res)
    figures(pw, d, res)
    write_documents(out, d, res)
    return res


def merge_manifest(out, entries):
    path = Path(out)/'logs/figure_manifest.csv'
    current = rows(path) if path.exists() else []
    replaced = {e['path'] for e in entries}
    write_rows(path, [r for r in current if r['path'] not in replaced]+entries)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=BASE, help='package directory (read and written)')
    args = p.parse_args(); out = args.output.resolve()
    spec = importlib.util.spec_from_file_location('algorithm_validation_core', BASE/'code/algorithm_validation.py')
    core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
    pw = core.PlotWriter(SimpleNamespace(output=out), rows(out/'input_manifest.csv'))
    run(out, pw)
    merge_manifest(out, pw.manifest)
    print(f'Refinement complete: {len(pw.manifest)} figures; outputs in {out}', flush=True)


# ---------------------------------------------------------------------------- documents
def md_table(columns, table):
    head = '| '+' | '.join(columns)+' |\n|'+'|'.join(['---']*len(columns))+'|\n'
    return head+'\n'.join('| '+' | '.join(str(r.get(c, '')) for c in columns)+' |' for r in table)


def pct(v, digits=1):
    return f'{v:+.{digits}f}%'


def snr_spread(d, weak):
    snr = np.array([[float(d.metrics[(i, m)]['target_snr']) for i in d.order] for m in AUTOMATIC])
    return [float(100*(snr[:, i].max()-snr[:, i].min())/np.median(snr[:, i])) for i in range(len(d.order)) if not weak[i]]


def facts(d, res):
    """Every number the narrative quotes, computed once and asserted before any prose is written."""
    A = res.assessment; chem = {r['area_series']: r for r in res.chem}
    picked, fixed = chem['picked_moving_boundary'], chem['completion_fixed_window']
    sp_picked = [r for r in res.spread_rows if r['area_series'] == 'picked_moving_boundary']
    sp_fixed = [r for r in res.spread_rows if r['area_series'] == 'completion_fixed_window']
    weak = weak_signal(d)
    pairs, pairs_fixed, fixed_series = [], [], series(d, 'fixed')
    for i, m in enumerate(METHODS):
        for o in METHODS[i+1:]:
            pairs.append(correlation_pair(d.picked[m], d.picked[o]))
            pairs_fixed.append(correlation_pair(fixed_series[m], fixed_series[o]))
    norm_final = {}
    for key in SERIES:
        agg = sorted((a for a in res.norm_agree if a['area_series'] == SERIES_ID[key]), key=lambda a: a['acquisition_time'])
        norm_final[key] = agg[-1]['cross_method_range_pp']
        assert int(np.argmax([a['cross_method_range_pp'] for a in agg])) == len(agg)-1, 'normalized spread is no longer largest at the end'
    fx = [r for r in res.thr_agree if r['area_series'] == 'completion_fixed_window']
    minimum = min(r['automatic_methods_agreeing'] for r in fx)
    worst = [r['percent_gate'] for r in fx if r['automatic_methods_agreeing'] == minimum]
    trans = {(t['area_series'], t['method']): t['transition_threshold_pct'] for t in res.transitions}
    hist = {r['method']: r for r in d.completion}
    crit = {m: next(g for g in res.margins if g['area_series'] == 'completion_fixed_window' and g['method'] == m
                    and g['acquisition_time'] == d.manifest[endpoint_window(d)[0]]['acquisition_time']) for m in METHODS}
    interval = {r['to_time']: r for r in res.intervals if r['area_series'] == 'completion_fixed_window'}
    core = {('2ppm', 'p2_dispersive_metric'), ('2ppm', 'p2_even_imaginary_fraction'), ('7ppm', 'p7_dispersive_metric'), ('7ppm', 'p7_even_imaginary_fraction')}
    higher_count = {m: sum(r['flag'] == 'higher' for r in d.improvements if r['method'] == m and (r['region'], r['metric']) in core) for m in AUTOMATIC}
    def per_acq(m, metric):
        v = [float(r['percent_change']) for r in d.improvements if r['method'] == m and r['metric'] == metric]
        return min(v), max(v)
    pqd = {(r['method'], r['region'], r['metric']): r for r in res.pq}
    neg = {m: (pqd[(m, '2ppm', 'negative_area_fraction')]['median_percent_change_vs_unphased'],
               pqd[(m, '2ppm', 'negative_area_fraction')]['higher_than_unphased_count']) for m in ('combined_objective_v1', 'symmetry_objective')}
    deep7 = (pqd[('deep_phaser', '7ppm', 'dispersive_metric')]['median_percent_change_vs_unphased'],
             pqd[('deep_phaser', '7ppm', 'dispersive_metric')]['higher_than_unphased_count'])
    auto_hist = [hist[m]['first_retrospective_completion'] for m in AUTOMATIC]
    hist_counts = (sum(bool(x) and x[11:19] == d.clock[endpoint_window(d)[0]] for x in auto_hist),
                   sum(bool(x) and x[11:19] == d.clock[-1] for x in auto_hist), sum(not x for x in auto_hist))
    centre3 = ('production', 'acme', 'ernst_integral_p0')
    effd = {(r['method'], r['region']): r for r in res.eff_summary}
    target_phases = [effd[(m, 'target')]['circular_mean_effective_phase_deg'] for m in centre3]
    f = SimpleNamespace(A=A, picked=picked, fixed=fixed, weak_times=[d.clock[i] for i in range(8) if weak[i]],
        picked_range_pct=[r['percent_range_of_median'] for r, w in zip(sp_picked, weak) if not w],
        fixed_range_pct=[r['percent_range_of_median'] for r, w in zip(sp_fixed, weak) if not w],
        picked_cv=[r['cv_percent'] for r, w in zip(sp_picked, weak) if not w],
        picked_range_all=[r['percent_range_of_median'] for r in sp_picked],
        snr_range=(min(A[m]['snr_median_abs_deviation_pct'] for m in AUTOMATIC), max(A[m]['snr_median_abs_deviation_pct'] for m in AUTOMATIC)),
        snr_spread=snr_spread(d, weak),
        min_pearson=min(p[0] for p in pairs), min_spearman=min(p[1] for p in pairs), min_pearson_fixed=min(p[0] for p in pairs_fixed),
        picked_cv_all=[r['cv_percent'] for r in sp_picked], norm_final=norm_final,
        ppm_max=max(A[m]['ppm_max_abs_offset_from_median'] for m in METHODS),
        fx_min_agreement=minimum, fx_worst_gates=(min(worst), max(worst)), trans=trans, hist=hist, crit=crit, interval=interval,
        detections=sum(A[m]['detection_count'] for m in AUTOMATIC), higher_count=higher_count,
        acme_2ppm_range=per_acq('acme', 'p2_dispersive_metric'), sym_even_range=per_acq('symmetry_objective', 'p2_even_imaginary_fraction'),
        neg=neg, deep7=deep7, hist_counts=hist_counts, late=late_interval_facts(d),
        max_sustained=max(r['qualifying_methods_sustained'] for r in res.summary),
        centre_median_max=max(A[m]['picked_median_abs_deviation_pct'] for m in centre3),
        centre_max_max=max(A[m]['picked_max_abs_deviation_pct'] for m in centre3),
        cs_max=max(A[m]['picked_max_abs_deviation_pct'] for m in ('combined_objective_v1', 'symmetry_objective')),
        target_phase_range=(min(target_phases), max(target_phases)))
    # Claims the prose makes; a rerun on different data must fail here rather than print stale text.
    assert f.detections == 56 and all(A[m]['detection_count'] == 8 for m in METHODS)
    assert picked['R_dynamic_range_over_median_algorithm_range'] > 10 and fixed['R_dynamic_range_over_median_algorithm_range'] > 5
    assert picked['R_endpoint_window'] < 3 and fixed['R_endpoint_window'] < 3
    independent_ok = [m for m in AUTOMATIC if not A[m]['objective_overlap_with_core_terms'] and A[m]['phase_category'] in ('Strong', 'Acceptable')]
    assert independent_ok == ['acme'], independent_ok
    assert A['combined_objective_v1']['phase_category'] == 'Strong' and A['combined_objective_v1']['objective_overlap_with_core_terms']
    assert A['peak_minima']['phase_category'] == 'Weak' and A['production']['phase_2ppm'] == 'Worse'
    assert all(A[m]['endpoint_category'] in ('Weak', 'Mixed') for m in METHODS)
    assert f.min_pearson > .99 and 4. < f.fx_worst_gates[0] < 6.
    return f


def which_algorithm(d, res, f):
    A = f.A; sf = res.sens_facts; S = sf.S
    eff = {(r['method'], r['region']): r for r in res.eff_summary}
    def phase_cell(m):
        a = A[m]
        if m == 'unphased':
            return 'Reference (no phase applied)'
        note = (' — in-sample (its objective\'s windows lie inside these windows, centred within one grid point); Acceptable at full resolution' if m == 'combined_objective_v1' else
                ' — partly in-sample; Mixed at full resolution' if m == 'symmetry_objective' else
                ' — smallest worst case of the independent methods in every variant tested' if m == 'acme' else '')
        return f'**{a["phase_category"]}**{note}. 2 ppm {a["phase_2ppm"]}: {a["phase_2ppm_evidence"]}. 7 ppm {a["phase_7ppm"]}: {a["phase_7ppm_evidence"]}'
    def target_cell(m):
        a = A[m]
        return (f'**{a["target_trend_category"]}** — area vs median: median |Δ| {a["picked_median_abs_deviation_pct"]:.2f}%, max '
                f'{a["picked_max_abs_deviation_pct"]:.2f}% ({a["picked_max_abs_deviation_time"][:5]}); shape ≤ '
                f'{a["picked_max_abs_normalized_shape_deviation_pp"]:.2f} pp; r = {a["picked_pearson_r_vs_leave_one_out_median"]:.4f}, '
                f'ρ = {a["picked_spearman_rho_vs_leave_one_out_median"]:.2f} (vs median of the others); detected {a["target_detection"]}')
    def endpoint_cell(m):
        a = A[m]; g = f.crit[m]
        changes = ', '.join(f'{x:+.2f}%' for x in g['recent_interval_percent_changes'])
        return (f'**{a["endpoint_category"]}** — historical rule: {a["historical_completion"]}. At {a["critical_prefix"][:5]}: intervals {changes} '
                f'(5% gate margin {g["percent_gate_margin_pp"]:+.2f} pp; relative-slope margin {g["relative_slope_margin_pct_per_h"]:+.2f} %/h). '
                f'Shares the 7-method modal outcome in {a["candidate_rule_agreement"]} candidate rules (centrality, not robustness)')
    table = [{'Method': SHORT[m], 'Strong-peak phase quality': phase_cell(m), 'Target trend stability': target_cell(m),
              'Endpoint robustness': endpoint_cell(m), 'Overall interpretation': A[m]['overall_interpretation']} for m in METHODS]
    pq = {(r['method'], r['region'], r['metric']): r for r in res.pq}
    def term(m, reg, metric):
        r = pq[(m, reg, metric)]; v = r['median_percent_change_vs_unphased']
        n = r['lower_than_unphased_count'] if v < 0 else r['higher_than_unphased_count']
        return f'{v:+.1f}% ({n}/8 {"lower" if v < 0 else "higher"}){"" if r["objective_overlap"] == "independent" else " †"}'
    section_a = [{'Method': SHORT[m], 'Category (package)': A[m]['phase_category'],
                  **{f'{reg[:-3]} ppm {lab}': term(m, reg, met) for reg in ('2ppm', '7ppm')
                     for lab, met in (('odd-Real', 'dispersive_metric'), ('even-Imag', 'even_imaginary_fraction'), ('asymmetry', 'peak_asymmetry'))}}
                 for m in AUTOMATIC]
    var_rows = []
    for name, *_ in VARIANTS:
        r = S[(name, 6, 'odd_real')]
        var_rows.append({'Scoring variant (≥6/8, odd-Real)': VARIANT_LABEL[name],
                         **{SHORT[m]: r[f'category_{STEMS[m]}'] for m in AUTOMATIC},
                         'Independent Strong/Acceptable': r['independent_strong_or_acceptable']})
    rule_rows = []
    for threshold in SENSITIVITY_THRESHOLDS:
        for odd_key, label in (('odd_real', 'odd-Real'), ('asymmetry', 'asymmetry')):
            r = S[('package_decimated', threshold, odd_key)]
            rule_rows.append({'Package scoring rule': f'≥{threshold}/8, {label}', **{SHORT[m]: r[f'category_{STEMS[m]}'] for m in AUTOMATIC},
                              'Independent Strong/Acceptable': r['independent_strong_or_acceptable']})
    eff_rows = [{'Method': SHORT[m],
                 **{f'{reg} mean (°)': f'{eff[(m, reg)]["circular_mean_effective_phase_deg"]:.1f}' for reg in ('2ppm', 'target', '7ppm')},
                 **{f'{reg} rms / max (°)': f'{eff[(m, reg)]["rms_deviation_across_acquisitions_deg"]:.1f} / {eff[(m, reg)]["max_abs_deviation_deg"]:.1f}'
                    for reg in ('2ppm', 'target', '7ppm')}} for m in AUTOMATIC]
    quiet = [{'Method': SHORT[m], 'Quiet 3.6–4.5 ppm Real RMS': pct(A[m]['quiet_3p6_4p5_prebaseline_rms_median_change_pct']),
              'Quiet 10–11 ppm Real RMS': pct(A[m]['quiet_10_11_prebaseline_rms_median_change_pct']),
              'Converged / successful': f'{A[m]["convergence"][0]}, {A[m]["successful_acquisitions"]}/8'} for m in AUTOMATIC]
    margin_rows = [{'Method': SHORT[m], 'Last three intervals at 11:08:23': ', '.join(f'{x:+.2f}%' for x in f.crit[m]['recent_interval_percent_changes']),
                    '5% gate margin (pp)': f'{f.crit[m]["percent_gate_margin_pp"]:+.2f}',
                    'Relative-slope margin (%/h)': f'{f.crit[m]["relative_slope_margin_pct_per_h"]:+.2f}',
                    'Qualifies at 11:08:23': f.crit[m]['qualifies'], 'Historical outcome': A[m]['historical_completion']} for m in METHODS]
    steady = [m for m in ('acme', 'peak_minima', 'deep_phaser')]
    steady_rms = max(eff[(m, '2ppm')]['rms_deviation_across_acquisitions_deg'] for m in steady)
    steady_max = ', '.join(f'{SHORT[m]} {max(eff[(m, reg)]["max_abs_deviation_deg"] for reg in ("2ppm", "target", "7ppm")):.1f}°' for m in steady)
    centre_track = ', '.join(f'{SHORT[m]} {eff[(m, "2ppm")]["circular_mean_effective_phase_deg"]:.0f}°' for m in steady)
    n_variants = sf.n_summary
    lines = ['# Which phase algorithm is best for the June 9 dataset?', '',
        f'{d.dataset} · eight real NMReady 60 MHz FIDs · seven automatic phase methods plus Unphased · generated by `code/robustness_refinement.py`', '',
        '## Short answer', '',
        '**There is no universal winner, and the evidence does not support declaring one.** The verdict is **Option B for the target measurement** '
        '(six of the seven automatic methods give near-identical target trends and their differences are minor) and **Option C only for the exact '
        'retrospective endpoint**.', '',
        f'- **Strong-peak shape: ACME has the smallest worst-case change of the independent methods** (DX metadata, ACME, Ernst P0 and DEEP) in all '
        f'{sf.n_scorings} scoring variants tested (8 window-centring/resolution choices × odd-Real or asymmetry; the worst case does not depend on the '
        f'consistency threshold). Under the package scoring no ACME core-term median is above Unphased and no term is higher in ≥6/8 '
        f'acquisitions, but ACME is still higher than Unphased in {f.higher_count["acme"]}/32 individual acquisition × term comparisons. Its ~2 ppm '
        f'changes alternate between acquisitions (odd-Real {f.acme_2ppm_range[0]:+.0f}% to {f.acme_2ppm_range[1]:+.0f}%) mainly because the shared diagnostic '
        'window centre alternates between two adjacent scoring-grid points. Its lead is real but not unique: with a ≥7/8 consistency rule, asymmetry '
        'instead of odd-Real, or a different window centre, Ernst P0 and/or DEEP also rate Acceptable (section A).',
        '- **Combined** shows the largest reductions only with the package\'s diagnostic windows: its own narrower optimization windows lie inside '
        'them, centred within one scoring-grid point, so the advantage is in-sample; at full-resolution scoring it is Acceptable. Its 2 ppm phase '
        f'alternates mainly because that objective\'s window centre alternates. A smaller alternation remains with the centre fixed (mean '
        f'consecutive swing of the metric optimum about {np.mean(sf.swing_fixed):.0f}° with either fixed centre versus {sf.swing_package:.0f}° with the '
        'alternating package centre) and comes from the 2 ppm band itself; none of this tests whether the instrument phase changes.',
        f'- **Target:** every automatic method detects the peak in 8/8 spectra and reproduces the same time course (minimum pairwise Pearson r '
        f'{f.min_pearson:.4f}). The cross-method area range is {min(f.picked_range_pct):.1f}–{max(f.picked_range_pct):.1f}% of the median for the picked '
        f'area and {min(f.fixed_range_pct):.1f}–{max(f.fixed_range_pct):.1f}% for the fixed-window completion area, excluding the weak 09:13 spectrum. '
        f'DX metadata, ACME, Ernst P0, Combined, Symmetry and DEEP give near-identical trends (DEEP offset {A["deep_phaser"]["picked_median_signed_deviation_pct"]:+.1f}%); '
        'Peak minima is the least consistent.',
        '- **Endpoint:** the exact retrospective endpoint depends on the method because the historical 5% gate sits inside the interval-to-interval '
        'spread between methods, not because the chemistry differs.', '',
        '## Option A, B or C?', '',
        '- **Option A — one algorithm clearly best: not supported.** Under the package scoring no independent method improves both strong-peak regions '
        '(under some window-centring variants ACME would, so this particular reason depends on centring). Option A fails regardless, because the target '
        'trends coincide and no method gives a robust endpoint.',
        f'- **Option B — a small group performs well and target differences are minor: supported for the target measurement**, where the group is six of '
        f'the seven automatic methods (all except Peak minima). The whole-run target '
        f'change is {f.picked["R_dynamic_range_over_median_algorithm_range"]:.0f}× the typical cross-method spread for the picked area '
        f'({f.picked["R_excluding_weak_signal"]:.1f}× with the weak 09:13 spread excluded; {f.picked["R_conservative_over_maximum_algorithm_range"]:.1f}× against the largest '
        f'spread) and {f.fixed["R_dynamic_range_over_median_algorithm_range"]:.0f}× for the fixed-window area '
        f'({f.fixed["R_excluding_weak_signal"]:.1f}×; {f.fixed["R_conservative_over_maximum_algorithm_range"]:.1f}×). DX metadata, ACME and Ernst P0 sit at the '
        f'ensemble centre (median deviation ≤{f.centre_median_max:.1f}%, max ≤{f.centre_max_max:.1f}%); Combined and Symmetry stay within '
        f'{f.cs_max:.2f}%; DEEP is offset by {A["deep_phaser"]["picked_median_signed_deviation_pct"]:+.1f}% with an intact trend; Peak minima is the least '
        f'consistent (max {A["peak_minima"]["picked_max_abs_deviation_pct"]:.1f}% at the weak 09:13 spectrum, '
        f'{A["peak_minima"]["picked_max_abs_deviation_excluding_weak_pct"]:.1f}% otherwise).',
        f'- **Option C — algorithm choice changes the scientific answer: only for the exact endpoint.** Under the unchanged historical rule, '
        f'{f.hist_counts[0]} of 7 automatic methods complete at 11:08:23, {f.hist_counts[1]} only at the final acquisition and {f.hist_counts[2]} never; '
        f'inside the endpoint window the chemistry changes are only {f.picked["R_endpoint_window"]:.1f}× (picked) and {f.fixed["R_endpoint_window"]:.1f}× '
        '(fixed window) the algorithm spread.', '',
        '## Summary table', '',
        'Categories use the package scoring and the explicit rules at the end of this page; section A shows how the strong-peak categories change under '
        'other reasonable scoring choices. † = the term is (partly) in that method\'s own objective, so a favourable value is in-sample.', '',
        md_table(['Method', 'Strong-peak phase quality', 'Target trend stability', 'Endpoint robustness', 'Overall interpretation'], table), '',
        '## A. Best phase shape (~2 ppm and ~7 ppm strong bands)', '',
        'Median percent change versus Unphased over 8 acquisitions, with the number of acquisitions moving in that direction: odd-Real (dispersive) '
        'contamination and asymmetry of the Real channel, and even-Imaginary contamination. Categories use odd-Real and even-Imaginary. See '
        '`12_phase_quality_vs_unphased.png` and `tables/strong_peak_phase_quality_vs_unphased.csv`.', '',
        md_table(['Method', 'Category (package)']+[f'{reg} ppm {lab}' for reg in ('2', '7') for lab in ('odd-Real', 'even-Imag', 'asymmetry')], section_a), '',
        '- **Independence.** A term is independent for a method when it is not in that method\'s objective. ACME and Peak minima are local Nelder–Mead '
        'searches started from the DX metadata phase: a dependence on the starting point, not an evaluation circularity. Peak minima\'s objective acts on '
        'the single tallest band (the 2 ppm band in every acquisition), so its overlap is marked at 2 ppm only. For an ideal line both contamination terms '
        'scale with |sin θ| of the residual phase, so they are not separate pieces of evidence.',
        f'- **Negative area.** Combined ({f.neg["combined_objective_v1"][0]:+.0f}%, {f.neg["combined_objective_v1"][1]}/8 higher) and Symmetry '
        f'({f.neg["symmetry_objective"][0]:+.0f}%, {f.neg["symmetry_objective"][1]}/8 higher) raise the 2 ppm negative-area fraction although both objectives '
        'contain it. The objectives sum their terms over three windows (2 ppm, target, 7 ppm), so the trade-off can be across windows as well as across terms.',
        f'- **DEEP** is consistently better at 2 ppm, but its 7 ppm odd-Real term rises ({f.deep7[0]:+.1f}%, {f.deep7[1]}/8 higher). **Peak minima** is worse '
        'on all four 7 ppm terms.', '',
        '### Sensitivity of the strong-peak ranking', '',
        f'The package diagnostic centres each symmetric ±0.18 ppm window on the magnitude maximum of the common FFT, chosen on a {sf.grid_step:.5f} ppm '
        f'scoring grid. At 2 ppm that centre **alternates between two adjacent grid points** ({sf.A:.4f} and {sf.B:.4f} ppm) on consecutive acquisitions, '
        f'and a one-point shift moves the metric-optimal phase by a median {sf.optimum_shift_median:.0f}°. This drives the alternating 4/8 splits of ACME and '
        f'Peak minima at 2 ppm: with either centre held fixed the strict alternation disappears, and each of their 2 ppm terms moves in the same direction '
        f'in at least {sf.fixed_one_direction}/8 acquisitions (which direction depends on the centre chosen). Symmetry\'s 2 ppm even-Imaginary term '
        f'({f.sym_even_range[0]:+.0f}% to {f.sym_even_range[1]:+.0f}%) also alternates, but Symmetry\'s own applied 2 ppm phase alternates as well, so the '
        f'term still alternates with {"one of the two" if sf.sym_even_fixed_alternating == 1 else "both"} fixed centres. Even '
        f'at full resolution the magnitude centre shifts by {sf.full_step_range[0]:.4f}–{sf.full_step_range[1]:.4f} ppm between consecutive acquisitions. '
        'Below, the saved phased spectra are re-scored with the same functionals and window rule under other centring and resolution choices '
        '(`tables/strong_peak_sensitivity_summary.csv`, `strong_peak_sensitivity_terms.csv`, `strong_peak_window_centres.csv`, '
        '`strong_peak_metric_optimum.csv`):', '',
        md_table(['Scoring variant (≥6/8, odd-Real)']+[SHORT[m] for m in AUTOMATIC]+['Independent Strong/Acceptable'], var_rows), '',
        md_table(['Package scoring rule']+[SHORT[m] for m in AUTOMATIC]+['Independent Strong/Acceptable'], rule_rows), '',
        f'- **What is robust:** ACME has the smallest worst-case core-term median among the independent methods in all {sf.n_scorings} scorings (8 '
        'centring/resolution choices × 2 Real-channel terms; the consistency threshold does not affect a worst case of medians, so the '
        f'{n_variants} rows of the summary table repeat them). Its absolute category ranges from Strong to Mixed, and with other reasonable choices '
        'Ernst P0 and/or DEEP are also Acceptable.',
        f'- **Combined is Strong only with the package windows**, which contain its narrower objective windows (centred within one grid point); it is Acceptable at full resolution and '
        'with a ≥7/8 rule. Symmetry falls to Mixed at full resolution.',
        '- **Statistical weight:** a 6/8 split has a two-sided sign-test p of 0.29 (7/8: 0.07; 8/8: 0.008), so "consistent" is a weak criterion with 8 '
        'acquisitions.', '',
        '**Baseline behaviour** (descriptive only; a rotation can lower Real RMS without lowering noise, and downstream baseline correction is not '
        'credited to phasing):', '', md_table(['Method', 'Quiet 3.6–4.5 ppm Real RMS', 'Quiet 10–11 ppm Real RMS', 'Converged / successful'], quiet), '',
        '**Convergence and phase reproducibility.** All 56 automatic runs succeeded. Assuming the instrument phase is nearly constant over the 2.4 h '
        'session (an assumption; DX metadata\'s identical stored phase is constant by construction and does not test it), the phase each method applies '
        'at the band centres is a stability check (`method_comparisons/effective_phase_by_acquisition.png`):', '',
        md_table(['Method']+[f'{r} mean (°)' for r in ('2ppm', 'target', '7ppm')]+[f'{r} rms / max (°)' for r in ('2ppm', 'target', '7ppm')], eff_rows), '',
        f'- ACME, Peak minima and DEEP apply steady corrections (2 ppm rms ≤ {steady_rms:.1f}°; maxima over all three positions {steady_max}). Ernst P0 '
        f'is steady to {eff[("ernst_integral_p0", "2ppm")]["rms_deviation_across_acquisitions_deg"]:.1f}° rms '
        f'({eff[("ernst_integral_p0", "2ppm")]["max_abs_deviation_deg"]:.1f}° max). Combined and Symmetry alternate at 2 ppm mainly because their objective\'s '
        f'window centre alternates: Combined\'s applied 2 ppm phase stays within {sf.combined_track:.1f}° of the odd-Real optimum of the package window in '
        f'every acquisition (in-sample tracking). With the centre fixed, the optimum still swings by a mean of about {np.mean(sf.swing_fixed):.0f}° '
        f'(versus {sf.swing_package:.0f}° with the alternating centre) between consecutive acquisitions, so part of the alternation comes from the 2 ppm '
        'band itself; the instrument phase is not tested.',
        f'- **Mechanism.** The metric-optimal constant phase of the Unphased spectrum lies on average {sf.distance_2ppm[0]:.1f}° (odd-Real) and '
        f'{sf.distance_2ppm[1]:.1f}° (even-Imaginary) from 0° at 2 ppm, alternating in sign with the centre, versus {sf.distance_7ppm[0]:.1f}° and '
        f'{sf.distance_7ppm[1]:.1f}° at 7 ppm (medians {sf.optimum_7ppm_odd_median:+.1f}° and {sf.optimum_7ppm_even_median:+.1f}°), so Unphased is '
        f'closer to optimal at 2 ppm than at 7 ppm. ACME, Peak minima and DEEP apply small, steady 2 ppm corrections ({centre_track}). DX '
        f'metadata applies {eff[("production", "2ppm")]["circular_mean_effective_phase_deg"]:.0f}° at 2 ppm and Ernst (P0 only, no linear term) '
        f'{eff[("ernst_integral_p0", "2ppm")]["circular_mean_effective_phase_deg"]:.0f}° everywhere: both are worse than Unphased at 2 ppm. Peak minima '
        f'({eff[("peak_minima", "7ppm")]["circular_mean_effective_phase_deg"]:+.1f}°) and DEEP ({eff[("deep_phaser", "7ppm")]["circular_mean_effective_phase_deg"]:+.1f}°) '
        'apply the phases furthest from the 7 ppm optimum, and their 7 ppm odd-Real terms rise.', '',
        '## B. Best target measurement stability (~5.8 ppm)', '',
        md_table(['Method', 'Detected', 'Median |Δ area|', 'Max |Δ area|', 'Median signed Δ', 'S/N median |Δ|', 'Max |Δ ppm|', 'Shape (pp)',
                  'r / ρ vs others', 'Category'],
                 [{'Method': SHORT[m], 'Detected': A[m]['target_detection'], 'Median |Δ area|': f'{A[m]["picked_median_abs_deviation_pct"]:.2f}%',
                   'Max |Δ area|': f'{A[m]["picked_max_abs_deviation_pct"]:.2f}% ({A[m]["picked_max_abs_deviation_time"][:5]})',
                   'Median signed Δ': pct(A[m]['picked_median_signed_deviation_pct'], 2), 'S/N median |Δ|': f'{A[m]["snr_median_abs_deviation_pct"]:.1f}%',
                   'Max |Δ ppm|': f'{A[m]["ppm_max_abs_offset_from_median"]:.4f}', 'Shape (pp)': f'{A[m]["picked_max_abs_normalized_shape_deviation_pp"]:.2f}',
                   'r / ρ vs others': f'{A[m]["picked_pearson_r_vs_leave_one_out_median"]:.4f} / {A[m]["picked_spearman_rho_vs_leave_one_out_median"]:.2f}',
                   'Category': A[m]['target_trend_category']} for m in METHODS]), '',
        'Deviations are from the median of the seven automatic methods (picked target area). "Shape" = largest deviation after each method is divided '
        'by its own maximum. **Closeness to the median is centrality, not accuracy**: DX metadata, ACME and Ernst P0 apply almost the same phase at the '
        f'target ({f.target_phase_range[0]:.0f}° to {f.target_phase_range[1]:.0f}°), so they cluster and define the ensemble centre. DEEP reads '
        f'systematically higher ({A["deep_phaser"]["picked_median_signed_deviation_pct"]:+.1f}%) with an intact trend; Peak minima has the least consistent '
        'trend. Spearman ρ is lower than Pearson r because the late plateau points change order between methods; the very high r values are dominated by '
        'the large rise from 09:13 to 10:38.', '',
        '## C. Best endpoint robustness', '',
        'The unchanged historical criterion (fixed 5.70–5.90 ppm area; ≥6 points, ≥1 h, last-four slope gates, three intervals ≤5%) at its first '
        'evaluable acquisition, 11:08:23:', '',
        md_table(['Method', 'Last three intervals at 11:08:23', '5% gate margin (pp)', 'Relative-slope margin (%/h)', 'Qualifies at 11:08:23',
                  'Historical outcome'], margin_rows), '',
        f'{sum(abs(f.crit[m]["percent_gate_margin_pp"]) < 1 for m in METHODS)} of 8 decisions sit within 1 percentage point of the 5% gate. Varying only '
        f'the gate, the automatic methods flip between {min(f.trans[("completion_fixed_window", m)] for m in AUTOMATIC):.2f}% and '
        f'{max(f.trans[("completion_fixed_window", m)] for m in AUTOMATIC):.2f}%, and agreement drops to {f.fx_min_agreement}/7 at gates '
        f'{f.fx_worst_gates[0]:.2f}–{f.fx_worst_gates[1]:.2f}% (`14_endpoint_threshold_sensitivity.png`). Sharing the modal outcome of the candidate rules '
        'measures centrality: DX metadata and Ernst P0 score highest because they sit at the ensemble centre, not because their endpoints are robust. '
        'Endpoint robustness here is limited by the rule and the data rather than by the choice of phase method. See '
        '`plateau_analysis/ENDPOINT_METRICS_EXPLORATORY.md`.', '',
        '## Category rules (applied identically to every method)', '',
        f'- **Region** (odd-Real and even-Imaginary; consistent = ≥{CONSISTENT}/8 acquisitions): Better = both consistently lower; Partly better = one '
        'consistently lower, the other not consistently changed; ≈ Unphased = neither consistent; Mixed = one consistently lower, one consistently '
        'higher; Worse = at least one term consistently higher and none consistently lower.',
        '- **Strong-peak phase quality**: Strong = Better in both regions; Acceptable = at least one improved region and no Mixed/Worse region; Mixed = '
        'an improved region plus a Mixed/Worse region; Weak = no improved region.',
        f'- **Target trend stability**: largest normalized-shape deviation ≤{DECISION_GATE/2:g} pp with 8/8 detections = Strong; ≤{DECISION_GATE:g} pp = '
        f'Acceptable; ≤{2*DECISION_GATE:g} pp = Mixed; otherwise Weak. These bounds are anchored to the 5% decision gate.',
        '- **Endpoint robustness**: Weak = the historical outcome is within 1 unit (pp or %/h) of a gate at the first evaluable acquisition, or the '
        'method qualifies only at the final acquisition; Mixed = qualifies with margin but later departs; Acceptable = otherwise.', '',
        '## Limits', '',
        '- One dataset, eight acquisitions, one sample chemistry. No method has been established as ground truth, and agreement does not prove accuracy.',
        '- Shape terms assume isolated, positive, symmetric absorptive bands; the ~2 and ~7 ppm windows contain solvent multiplet structure.',
        '- The strong-peak categories depend on the diagnostic window centring and resolution, the consistency threshold and the Real-channel term '
        '(sensitivity tables above); only the ordering statement about ACME\'s worst case held in every variant tested.',
        '- The categories are descriptive conveniences. Read the numbers.']
    (Path(res.out)/'boss_summary/WHICH_ALGORITHM_IS_BEST.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def endpoint_report(d, res, f):
    S = {(r['rule_id'], r['area_series']): r for r in res.summary}
    def agree(rule, key='completion_fixed_window'):
        r = S[(rule, key)]
        if not r['modal_is_a_qualification_time']:
            return f'never {r["methods_agreeing_with_modal_of_7"]}/7'
        return f'{r["modal_outcome"][:5]} {r["methods_agreeing_with_modal_of_7"]}/7 (sustained {r["qualifying_methods_sustained"]}/{r["qualifying_automatic_methods"]})'
    fam = {}
    for r in res.summary:
        fam.setdefault((r['rule_family'], r['area_series']), []).append(r)
    def family_line(family, key='completion_fixed_window'):
        cells = fam[(family, key)]
        informative = sum(c['modal_is_a_qualification_time'] and c['methods_agreeing_with_modal_of_7'] >= 6 for c in cells)
        return f'{informative}/{len(cells)} gates give ≥6/7 agreement on a qualification time'
    rows_ = [
        {'Endpoint metric': 'Historical rule (unchanged, 5% gate)', 'Robust across phase methods?': f'No — {agree("historical_gate_5pct")}',
         'Strength': 'Multi-gate (6 points, 1 h, slope, 3 intervals); already in use', 'Weakness': 'The 5% gate coincides with the 10:38→10:53 interval changes (4.80–5.60% for 4 methods); qualifications depart later'},
        {'Endpoint metric': 'Historical rule, other gates (3–10%)', 'Robust across phase methods?': f'Only away from 5%: {family_line("historical")}',
         'Strength': 'Shows where the rule is stable (≥6%: 6/7 at 11:08)',
         'Weakness': f'Agreement minimum {f.fx_min_agreement}/7 at {f.fx_worst_gates[0]:.2f}–{f.fx_worst_gates[1]:.2f}%; no data-derived gate'},
        {'Endpoint metric': 'Historical + next-acquisition confirmation (persistence)', 'Robust across phase methods?': f'Agree only that no endpoint is reached ({agree("historical_gate_5pct_confirmed")} at 5%)',
         'Strength': 'Prevents the early 11:08 calls that later depart', 'Weakness': 'With 8 points it removes almost every endpoint; uninformative here'},
        {'Endpoint metric': 'Consecutive intervals ≤ gate (k = 1, 2, 3)', 'Robust across phase methods?': f'k=1: {family_line("consecutive_1")}; k=2: {family_line("consecutive_2")}; k=3: {family_line("consecutive_3")}',
         'Strength': 'Simple; k=2 or 3 adds persistence', 'Weakness': 'Single-interval agreement (7/7 at 11:08 for 5–10%) is not sustained; k=2/3 at 5% falls back to 4/7'},
        {'Endpoint metric': 'Rolling 3-point slope (normalized)', 'Robust across phase methods?': f'Partly — {family_line("rolling_slope")}',
         'Strength': 'Uses three points; continuous measure', 'Weakness': '95% CI needs t(df=1)=12.7, so it is unusable; %/h gates are arbitrary'},
        {'Endpoint metric': 'Rolling 3-point CV', 'Robust across phase methods?': f'Mostly — {family_line("rolling_cv")}',
         'Strength': 'Threshold-stable agreement (≥6/7 over 3.5–7.5%)', 'Weakness': 'Nearly equivalent to range for 3 points; not sustained after 11:08'},
        {'Endpoint metric': 'Rolling 3-point range', 'Robust across phase methods?': f'Mostly — {family_line("rolling_range")}',
         'Strength': '"All recent points within X%"; easiest to explain; ≥6/7 over 7.5–15% on both area definitions',
         'Weakness': 'Qualification at 11:08 is followed by departures (fixed-window rise at 11:24, drop at 11:36); tolerance needs measured precision'},
        {'Endpoint metric': 'Rolling range/CV + confirmation', 'Robust across phase methods?': f'At lenient gates — range: {family_line("rolling_range_persistent")}',
         'Strength': 'Moves calls to 11:24 with 6–7/7 agreement', 'Weakness': 'Still contradicted by the final drop at 11:36'},
        {'Endpoint metric': 'Uncertainty-aware change (absolute ΔA ÷ area uncertainty)', 'Robust across phase methods?': 'Not usable',
         'Strength': 'Right idea once precision is known', 'Weakness': f'Only a white-noise lower-bound SE exists (median {res.uncertainty["white_noise_se_median_au_ppm"]:.4f} a.u.·ppm, ~{res.uncertainty["ratio_cross_method_range_to_se"]:.0f}× smaller than the processing spread)'},
        {'Endpoint metric': 'Target ÷ internal reference', 'Robust across phase methods?': 'Rejected',
         'Strength': 'Would remove global scaling drift', 'Weakness': 'No internal standard; solvent bands change 14–17% when the target arrives; Real band areas are phase-dependent'},
        {'Endpoint metric': 'Change-point / segmented trend', 'Robust across phase methods?': 'Not informative',
         'Strength': 'Locates the rise→plateau transition', 'Weakness': 'Breakpoint differs between area definitions; the late break rests on the single final point'}]
    t5 = f.trans
    lines = ['# Exploratory endpoint and plateau metrics — June 9 robustness study', '',
        '**Status: exploratory research metrics only.** The historical endpoint criterion is unchanged and remains the production rule. No candidate '
        'below is adopted, and no new production threshold is chosen.', '',
        '## Historical endpoint criterion (unchanged)', '',
        'Input: fixed 5.70–5.90 ppm area of the globally baseline-corrected Real spectrum with a local linear footline. Completion at an acquisition '
        'requires ≥6 observations, ≥1 h elapsed, recent completion QC, earlier growth, the last three adjacent changes each ≤5%, and a last-four OLS '
        'slope with |slope| ≤ 5 a.u.·ppm/h and ≤ 10 %/h. It is replayed here through the unchanged `chemyx_lab.analysis.completion` code; the prefix '
        'replay reproduces the saved `completion_by_method.csv` exactly.', '',
        'Why it is sensitive (`plateau_analysis/historical_gate_margins.csv`): at the first evaluable acquisition (11:08:23) the decisive interval '
        f'10:38:47→10:53:46 changes by {f.interval["10:53:46"]["minimum_percent_change"]:.2f}% to {f.interval["10:53:46"]["maximum_percent_change"]:.2f}% across the seven '
        'methods. DX metadata (4.80%), Ernst P0 (4.83%), DEEP (5.13%), ACME (5.60%) and Unphased (5.30%) all fall within 0.6 points of the 5% gate.', '',
        '## Robustness summary (Task 19)', '',
        md_table(['Endpoint metric', 'Robust across phase methods?', 'Strength', 'Weakness'], rows_), '',
        'Agreement = how many of the seven automatic methods share the most common first-qualifying acquisition. "Sustained" = the qualifying methods '
        'stay qualified at every later acquisition. Every candidate shares the historical eligibility (≥6 points, ≥1 h, QC), so differences come from '
        'the stability test itself. Full matrix: `endpoint_metric_comparison.csv`; summary: `endpoint_rule_robustness_summary.csv`; figures '
        '`13_endpoint_robustness.png` and `endpoint_outcome_matrix.png`.', '',
        '## Task 12 — rolling three-point slope', '',
        'OLS slope over the three most recent acquisitions, normalized by their mean (%/h), with SE and 95% CI. With three points the CI uses '
        't(0.975, df = 1) = 12.71 and spans tens of %/h, so it is reported but has no gating value. Evaluated gates: 5, 10, 15 and 20 %/h. '
        'At 11:08:23 the normalized slopes are 6–19 %/h (fixed window): a 10 %/h gate splits the methods, while 15–20 %/h agree on 11:08 but are not '
        'sustained, because the slopes reach +14 to +17 %/h at 11:24 and −26 to −34 %/h at 11:36 for most methods.', '',
        '## Task 13 — rolling coefficient of variation', '',
        'CV = SD/mean × 100 over the latest three points. Evaluated gates: 2.5, 3.5, 5 and 7.5%. Agreement is ≥6/7 on 11:08 for gates of 3.5% and '
        'above on the fixed window, and for every gate on the picked area; qualification is followed by departure for at least 6 of 7 methods. With three points, '
        'CV is about 0.50–0.58 × the range, so it carries almost no extra information.', '',
        '## Task 14 — rolling range', '',
        '(max − min)/mean × 100 over the latest three points: "all recent measurements are within X% of one another". Evaluated gates: 5, 7.5, 10 and 15%. '
        'Together with the rolling CV (nearly equivalent for three points) it is the most threshold-stable metric on both area definitions: ≥6/7 '
        f'agreement for every gate of 7.5% and above. Its weakness is shared by every rule on these data: {f.late.text}, so a plateau declared '
        'at 11:08 does not hold.', '',
        '## Task 15 — persistence', '',
        'Persistence was tested two ways: (1) requiring k = 1, 2 or 3 consecutive qualifying intervals at gates of 3, 5, 7.5 and 10%; (2) requiring '
        'the full historical rule, or a rolling metric, to qualify at two consecutive acquisitions. **On this dataset persistence did not produce a '
        'method-robust endpoint.** Going from one to two intervals lowers agreement at 5% (7/7 → 4/7). Confirming the historical rule turns its '
        'split (4/7) into agreement that no endpoint is reached (7/7 "never"), which is uninformative. Confirmed rolling metrics at lenient gates '
        'move the call to 11:24 with 6–7/7 agreement, but the 11:36 drop still contradicts it.', '',
        '## Task 16 — uncertainty-aware change', '',
        f'The only stored area uncertainty is a white-noise propagation, documented as an approximate lower bound: zero filling correlates points, and '
        f'phase, baseline and integration systematics are excluded. Its median is {res.uncertainty["white_noise_se_median_au_ppm"]:.4f} a.u.·ppm, about '
        f'{res.uncertainty["ratio_cross_method_range_to_se"]:.0f}× smaller than the median cross-method processing range in the endpoint window '
        f'({res.uncertainty["cross_method_range_median_au_ppm"]:.2f} a.u.·ppm). |ΔA|/u has a median of {res.uncertainty["z_median_automatic"]:.0f} and a minimum '
        f'of {res.uncertainty["z_minimum_automatic"]:.1f}, so every interval would look "significant". **This metric cannot yet be used.** It needs an '
        'empirical precision from replicate acquisitions of a static sample (same flow cell and parameters). Values: `uncertainty_aware_change.csv`.', '',
        '## Task 17 — internal reference normalization', '',
        '**Rejected:** no strong resonance qualifies as an internal reference for these data. Evidence (`internal_reference_audit.json`, `tables/internal_reference_audit.csv`, '
        '`internal_reference_audit.png`):', '']+[f'- {reason}' for reason in res.ref_audit['reasons']]+['',
        '## Task 18 — change-point / segmented trend', '',
        'Continuous hinge (piecewise-linear) models with breakpoints at observed acquisitions were compared with a straight line by SSE and BIC '
        '(`change_point_analysis.csv`, `change_point_summary.json`). The rise-to-plateau break lands at different acquisitions for the two area '
        'definitions (fixed window: 10:38 for 7/7 single-break fits; picked area: 10:53 or 10:38, with the runner-up only about 5% worse). In the '
        'endpoint window the best break (11:24 for 6/7) is defined by the single final acquisition. **With eight points this adds nothing defensible '
        'to the endpoint question, so the analysis stops here.**', '',
        '## Task 20 — threshold sensitivity of the historical rule', '',
        'Only `percent_change_threshold` was varied, from 3% to 10% in 0.01% steps; all other gates were unchanged. Exact flip points '
        '(`threshold_transitions.csv`):', '',
        md_table(['Method', 'Fixed window (historical input)', 'Picked area (sensitivity)'],
                 [{'Method': SHORT[m],
                   'Fixed window (historical input)': f'{t5[("completion_fixed_window", m)]:.2f}%' if ("completion_fixed_window", m) in t5 else 'no flip in 3–10%',
                   'Picked area (sensitivity)': f'{t5[("picked_moving_boundary", m)]:.2f}%' if ("picked_moving_boundary", m) in t5 else 'no flip in 3–10%'}
                  for m in METHODS]), '',
        f'**The 5% gate sits in the most sensitive region for the fixed-window input**: agreement among the automatic methods falls to '
        f'{f.fx_min_agreement}/7 at {f.fx_worst_gates[0]:.2f}–{f.fx_worst_gates[1]:.2f}%, and four automatic methods flip between 4.80% and 5.60%. '
        'Applied to the moving-boundary picked area, the same unchanged rule gives 6/7 agreement on 11:24:34 for any gate between 4.41% and 6.53%. '
        'The measurement definition therefore matters as much as the threshold. This is an observation for validation, not a reason to switch inputs.', '',
        '## Best candidate for further validation (not a production change)', '',
        '**A rolling three-point range with a confirmation requirement, evaluated on both area definitions**, with the tolerance set from measured '
        'acquisition-to-acquisition precision rather than a fixed 5%. Reasons: together with the nearly equivalent rolling CV it is the most '
        'threshold-stable of the evaluated metrics across phase methods; it uses three acquisitions instead of one interval; and it is the easiest '
        'to explain. Before any adoption it needs (1) replicate '
        'acquisitions of a static sample to set the tolerance, (2) runs with an independently confirmed endpoint, and (3) a prospective comparison '
        f'with the historical rule. On June 9 no rule produced an endpoint that stayed qualified for more than {f.max_sustained} of 7 methods: '
        f'{f.late.text}. This dataset may not contain a sustained plateau at all.']
    (Path(res.out)/'plateau_analysis/ENDPOINT_METRICS_EXPLORATORY.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return rows_


def agreement_plot_audit(d, res):
    picked = series(d, 'picked'); dx = picked['production']
    lo_area, hi_area = float(min(dx.min(), *(picked[m].min() for m in METHODS))), float(max(dx.max(), *(picked[m].max() for m in METHODS)))
    big = max(({'method': m, 'time': d.clock[i][:5], 'pct': 100*(picked[m][i]-dx[i])/abs(dx[i]), 'diff': picked[m][i]-dx[i]}
               for m in METHODS if m != 'production' for i in range(len(dx))), key=lambda r: abs(r['pct']))
    lines = ['# Audit of the old target-area agreement plot (`08_target_area_agreement.png`)', '',
        'The plot is now kept only as a secondary historical-reference view: `S1_agreement_with_historical_DX_processing.png`, visibly titled '
        '"Agreement with historical DX-metadata processing" (source `method_comparisons/target_area_agreement.png`).', '',
        '## What it shows', '',
        '- **Left panel — x axis:** acquisition time (JCAMP LONG DATE). **y axis:** 100 × (method area − DX-metadata area) / |DX-metadata area| for '
        'the picked (moving-boundary) target area. **One point** = one method at one acquisition. DX metadata is a flat line at 0% by construction.',
        '- **Right panel — x axis:** the DX-metadata picked area. **y axis:** each comparator\'s picked area, with a y = x line. **One point** = one '
        'method at one acquisition. DX points lie exactly on the line by construction.',
        '- **Why DX was the reference:** DX metadata is the stored vendor phase that earlier ("production") processing applied, so the plot answered a '
        'continuity question: how different would each method be from what was historically computed?', '',
        '## Why it was confusing for the boss question', '',
        '1. It privileges DX metadata. Every other method\'s difference reads like an error relative to DX, but DX is not ground truth. On the strong-peak '
        'diagnostics DX is worse than Unphased at ~2 ppm.',
        '2. It answers "different from DX?" rather than "how much does the choice of algorithm matter?". The spread among methods is not visible as one '
        'quantity.',
        f'3. On the scatter, the {lo_area:.0f} → {hi_area:.0f} a.u.·ppm range compresses differences of up to {abs(big["pct"]):.1f}% into points near the line: '
        f'the largest relative difference ({SHORT[big["method"]]} at {big["time"]}, {big["pct"]:+.1f}% ≈ {abs(big["diff"]):.2f} a.u.·ppm) is '
        f'{"invisible" if abs(big["diff"]) < .02*(hi_area-lo_area) else "hard to see"} at this scale.', '',
        '## The replacement', '',
        '- `08_method_spread_vs_time.png`: the cross-method median of the seven automatic methods, their min–max band and the percent range at every '
        'acquisition, with no privileged method.',
        '- `11_target_area_deviation_heatmap.png`: each method\'s deviation from the cross-method median at every acquisition.',
        '- `09_chemistry_vs_algorithm_effect.png` and `10_normalized_target_trends.png`: the size of the chemistry change compared with the algorithm effect, '
        'and the trajectory shapes.']
    (Path(res.out)/'method_comparisons/TARGET_AREA_AGREEMENT_PLOT_AUDIT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def boss_summary(d, res, f):
    A = f.A
    lines = [f'# Boss summary — {d.dataset} NMR phasing: how much does the algorithm matter?', '',
        f'**Bottom line.** The choice of phase algorithm changes peak shape and the exact target area (cross-method range {min(f.picked_range_pct):.1f}–'
        f'{max(f.picked_range_pct):.1f}% of the median for the picked area and {min(f.fixed_range_pct):.1f}–{max(f.fixed_range_pct):.1f}% for the fixed-window '
        'area, excluding the weak 09:13 spectrum), but not the chemistry: all seven '
        'automatic methods detect the ~5.8 ppm target in 8/8 spectra and trace nearly the same time course. The fragile part is the exact retrospective '
        'endpoint, because the historical 5% gate sits inside the small spread that different algorithms produce. The next major improvement is most '
        'likely a more robust endpoint metric rather than a search for the "correct" phase algorithm.', '',
        '## The story in five points', '',
        f'1. **Phasing changes peak shape, but no method is robustly best.** On the strong ~2 and ~7 ppm bands, ACME has the smallest worst-case change '
        f'of the independent methods in every scoring variant tested and is consistently better at 7 ppm under the package scoring; its 2 ppm changes alternate with the diagnostic '
        f'window centre. Combined reduces the evaluated terms most only with its own optimization windows (in-sample). DX metadata and Ernst P0 are worse '
        f'than Unphased at 2 ppm; Peak minima is worse at 7 ppm. The strong-peak categories shift with reasonable scoring choices '
        f'(`12_phase_quality_vs_unphased.png`; `WHICH_ALGORITHM_IS_BEST.md` section A).',
        f'2. **The scientific target is robust.** 56/56 automatic results detect it. Across methods the area differs by '
        f'{min(f.picked_range_pct):.1f}–{max(f.picked_range_pct):.1f}% of the median per acquisition (CV {min(f.picked_cv):.1f}–{max(f.picked_cv):.1f}%), '
        f'excluding the weak 09:13 spectrum; the S/N range across methods is {min(f.snr_spread):.1f}–{max(f.snr_spread):.1f}% of the median; and the '
        f'peak position differs from the cross-method median by at most {f.ppm_max:.4f} ppm (`08_method_spread_vs_time.png`, '
        f'`11_target_area_deviation_heatmap.png`).',
        f'3. **The time-series chemistry is extremely consistent.** The target change over the run is '
        f'{f.picked["R_dynamic_range_over_median_algorithm_range"]:.0f}× the typical algorithm spread (picked area; '
        f'{f.fixed["R_dynamic_range_over_median_algorithm_range"]:.0f}× for the fixed-window completion area). Minimum pairwise correlation is '
        f'{f.min_pearson:.4f} for the picked area ({f.min_pearson_fixed:.4f} for the fixed-window area). {f.late.agree}; the normalized spread '
        f'is largest at that final acquisition ({f.norm_final["picked"]:.1f} and {f.norm_final["fixed"]:.1f} percentage points) '
        f'(`09_chemistry_vs_algorithm_effect.png`, `10_normalized_target_trends.png`).',
        f'4. **Exact endpoint timing is less robust.** Inside the endpoint window the chemistry changes are only {f.picked["R_endpoint_window"]:.1f}× '
        f'(picked) and {f.fixed["R_endpoint_window"]:.1f}× (fixed window) the algorithm spread. Under the unchanged historical rule, DX metadata, Combined, '
        f'Symmetry and Ernst P0 complete at 11:08:23 (all later depart from the plateau), Peak minima only at the final acquisition, and ACME and DEEP '
        f'never. The decisive 10:38:47→10:53:46 change is 4.80% (DX), 5.60% (ACME) and 5.13% (DEEP) against a 5% gate '
        f'(`06_retrospective_completion.png`, `14_endpoint_threshold_sensitivity.png`).',
        '5. **Therefore** the larger remaining issue is defining an endpoint criterion that is less sensitive to small quantitative processing '
        'differences. Multi-point rolling metrics (range or CV) agree across methods over much wider tolerance ranges, but on June 9 no rule\'s '
        f'qualification stays valid: {f.late.text}. '
        'Validation needs replicate acquisitions and runs with a '
        'confirmed endpoint (`13_endpoint_robustness.png`, `../plateau_analysis/ENDPOINT_METRICS_EXPLORATORY.md`).', '',
        '## Which algorithm is best?', '',
        'There is no universal winner: Option B for the target measurement (six of seven automatic methods give near-identical target trends; '
        'differences are minor) and '
        'Option C only for the exact retrospective endpoint. See '
        '[WHICH_ALGORITHM_IS_BEST.md](WHICH_ALGORITHM_IS_BEST.md) and [METHOD_AGREEMENT_SUMMARY.csv](METHOD_AGREEMENT_SUMMARY.csv).', '',
        '## Figures', '',
        '| # | File | What it shows |', '|---|---|---|',
        '| 1–4 | `01_full_before_after.png` … `04_target_before_after.png` | Prespecified 11:08:23 example: Unphased (gray) against DX metadata, ACME and Combined (legends moved upper-right) |',
        '| 5 | `05_target_area_timeseries.png` | Picked target area for every method over time |',
        '| 6 | `06_retrospective_completion.png` | Historical-rule completion outcome by method (unchanged rule) |',
        '| 7 | `07_phase_quality_changes.png` | Detailed heatmap of all four strong-peak shape terms (superseded for presentation by 12) |',
        '| 8 | `08_method_spread_vs_time.png` | **Main result:** cross-method median, min–max band and % spread at every acquisition |',
        '| 9 | `09_chemistry_vs_algorithm_effect.png` | Chemistry change versus algorithm spread, whole run and endpoint window |',
        '| 10 | `10_normalized_target_trends.png` | Trajectory shapes normalized to each method\'s maximum (not quantitation) |',
        '| 11 | `11_target_area_deviation_heatmap.png` | Each method\'s % deviation from the cross-method median |',
        '| 12 | `12_phase_quality_vs_unphased.png` | Does phasing reduce strong-peak contamination relative to Unphased? |',
        '| 13 | `13_endpoint_robustness.png` | Which endpoint definitions are least sensitive to the phase method (exploratory) |',
        '| 14 | `14_endpoint_threshold_sensitivity.png` | Historical rule with the percent gate varied from 3% to 10% |',
        '| S1 | `S1_agreement_with_historical_DX_processing.png` | Secondary historical view: agreement with DX-metadata processing (see the audit below) |', '',
        'DX metadata is the stored vendor phase and is not ground truth; no method is treated as truth. Old plot audit: '
        '[TARGET_AREA_AGREEMENT_PLOT_AUDIT.md](../method_comparisons/TARGET_AREA_AGREEMENT_PLOT_AUDIT.md). '
        'Numbers: [method spread](../tables/method_spread_by_time.csv) · [chemistry vs algorithm](../tables/chemistry_vs_algorithm.csv) · '
        '[endpoint comparison](../plateau_analysis/endpoint_metric_comparison.csv) · [full validation summary](../validation_summary.md).']
    (Path(res.out)/'boss_summary/BOSS_SUMMARY.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def presentation_data(d, res, f, endpoint_rows):
    A = f.A
    data = {'dataset': d.dataset, 'generated_utc': datetime.now(timezone.utc).isoformat(),
        'methods': [SHORT[m] for m in METHODS],
        'phase': [{'method': SHORT[m], 'category': A[m]['phase_category'], 'two_ppm': A[m]['phase_2ppm'], 'seven_ppm': A[m]['phase_7ppm'],
                   'in_sample_terms': A[m]['objective_overlap_with_core_terms']} for m in AUTOMATIC],
        'target': {'detections': f.detections, 'range_pct_of_median': [min(f.picked_range_pct), max(f.picked_range_pct)],
                   'fixed_range_pct_of_median': [min(f.fixed_range_pct), max(f.fixed_range_pct)],
                   'cv_pct': [min(f.picked_cv), max(f.picked_cv)], 'weak_excluded': f.weak_times, 'snr_median_abs_dev_pct': list(f.snr_range),
                   'ppm_max_offset': f.ppm_max, 'min_pairwise_pearson': f.min_pearson, 'min_pairwise_spearman': f.min_spearman,
                   'weak_snr_threshold': d.minimum_snr},
        'chemistry_vs_algorithm': res.chem,
        'strong_peak_sensitivity': [r for r in res.sens.summary if r['threshold_of_8'] == 6 and r['odd_term'] == 'odd_real'],
        'categories': [{'method': SHORT[m], 'phase': A[m]['phase_category'], 'target': A[m]['target_trend_category'],
                        'endpoint': A[m]['endpoint_category'], 'historical_completion': A[m]['historical_completion'],
                        'median_abs_area_dev_pct': A[m]['picked_median_abs_deviation_pct'],
                        'median_signed_area_dev_pct': A[m]['picked_median_signed_deviation_pct'],
                        'shape_dev_pp': A[m]['picked_max_abs_normalized_shape_deviation_pp']} for m in METHODS],
        'endpoint': {'transitions_fixed': {SHORT[m]: f.trans.get(('completion_fixed_window', m)) for m in METHODS},
                     'transitions_picked': {SHORT[m]: f.trans.get(('picked_moving_boundary', m)) for m in METHODS},
                     'min_agreement_fixed': f.fx_min_agreement, 'min_agreement_gates': list(f.fx_worst_gates),
                     'critical_interval_pct': {SHORT[m]: f.crit[m]['recent_interval_percent_changes'] for m in METHODS},
                     'metric_table': endpoint_rows, 'uncertainty': res.uncertainty, 'internal_reference': res.ref_audit['decision']},
        'acme_higher_than_unphased': {'count': f.higher_count['acme'], 'of': 32},
        'historical_outcome_counts': {'first_evaluable': d.clock[endpoint_window(d)[0]], 'complete_first_evaluable': f.hist_counts[0],
                                      'final_acquisition_only': f.hist_counts[1], 'never': f.hist_counts[2]},
        'last_intervals_pct': {SERIES_ID[key]: {'intervals': [f'{d.clock[-3]}→{d.clock[-2]}', f'{d.clock[-2]}→{d.clock[-1]}'],
                                                **{SHORT[m]: [float(x) for x in percent_changes(series(d, key)[m])[-2:]] for m in METHODS}}
                               for key in SERIES},
        'figures': {n: f'boss_summary/{n}' for n in ('08_method_spread_vs_time.png', '09_chemistry_vs_algorithm_effect.png',
                    '10_normalized_target_trends.png', '11_target_area_deviation_heatmap.png', '12_phase_quality_vs_unphased.png',
                    '13_endpoint_robustness.png', '14_endpoint_threshold_sensitivity.png')}}
    write_json(Path(res.out)/'boss_summary/refinement_data.json', data)


def context_for_new_llm(d, res, f, endpoint_rows):
    A = f.A
    hist = '; '.join(f'{SHORT[m]} {A[m]["historical_completion"]}' for m in METHODS)
    lines = ['# Context for a new LLM — June 9 NMR algorithm-validation package', '',
        f'Package: `results/100426_algovalidation` in repository `C:\\code\\chemyx_pump`. Dataset display name: `{d.dataset}`. Written by '
        '`code/robustness_refinement.py` from saved package data; regenerate it rather than editing it by hand.', '',
        '## Project purpose', '',
        'Automated processing of real low-field **NMReady 60 MHz** benchtop ¹H spectra recorded during a flow experiment run by an automated '
        'syringe-pump workflow (`chemyx_lab`). The scientific target is one resonance at about **5.8 ppm** whose area is followed over time; a '
        'retrospective completion/plateau rule decides when the reaction signal has stopped changing. This package asks how much the choice of '
        'phase-correction algorithm affects those conclusions.', '',
        '## Raw data', '',
        '`results/raw/nmr/06-09-26/*.dx` — eight JCAMP-DX files (`DATA TYPE = NMR FID`). Each contains:', '',
        '- the complex time-domain FID: 8192 real + 8192 imaginary points;',
        '- acquisition metadata, including the authoritative time `LONG DATE` (filename tokens such as `sequence-1015` are nominal schedule labels, '
        'not acquisition times), observe frequency, sweep width, receiver gain (12 dB in all eight), scans (4) and temperature (33.0 °C);',
        '- DX-stored phase metadata (`$PHASECORRECTION`), which earlier processing converted to P0/P1.', '',
        f'Acquisition times (LONG DATE, UTC−04): {", ".join(d.clock)}. The 09:13:16 spectrum has a weak target (median S/N about 3.3, below the '
        'historical minimum of 8).', '',
        '## Processing and phase methods', '',
        '- **Unphased FFT**: exponential apodization (0.03 Hz), zero fill to 65536 points, FFT, no phase rotation. All methods start from exactly this '
        'complex spectrum.',
        '- **DX metadata**: the stored PHC0/PHC1 applied to the common FFT (P0 59.5°, P1 −79°, inverse convention; identical for all eight FIDs). '
        'Earlier material called this "Production". Do not use that name in new user-facing material, and do not treat it as ground truth.',
        '- **ACME** (entropy + negative penalty; nmrglue), **Peak minima** (equal minima beside the tallest peak; nmrglue), **Combined objective** '
        '(0.25 each of negative area, even Imaginary, asymmetry and baseline), **Symmetry objective** (asymmetry + 0.1 negative area), **Ernst P0** '
        '(integral criterion, zero-order only) and **DEEP Phaser** (COLMARvista neural network, offline). All 56 automatic runs succeeded.',
        '- Phase is applied as S[k]·exp(±i·π/180·(P0 + P1·k/N)), N = 65536; the sign is negative for the inverse convention.',
        '- Two target areas exist by design: the **picked target area** (moving integration boundaries, regional polynomial baseline; Fig. 05) and '
        'the **fixed-window completion area** (5.70–5.90 ppm, global ALS-baselined Real plus local linear footline), which is the input to the '
        'historical completion rule.', '',
        '## Major result', '',
        'All phase methods detect the target and reproduce highly similar target-area trajectories, but peak-shape metrics and the exact '
        'retrospective completion differ. There is no universal best algorithm (Option B for the target; Option C only for the exact endpoint). '
        'ACME has the smallest worst-case strong-peak change of the independent methods in every scoring variant tested, but its category is not '
        'unique under other reasonable scoring choices. Combined scores best only on the terms and windows it optimizes (in-sample), and its 2 ppm '
        'phase alternates, mainly with its objective\'s window centre. The 2 ppm diagnostic window centre itself alternates between two adjacent scoring-grid '
        'points (see `boss_summary/WHICH_ALGORITHM_IS_BEST.md` section A and `tables/strong_peak_sensitivity_summary.csv`).', '',
        '## Key verified numbers', '',
        f'- Detection: 56/56 automatic method × acquisition results (8/8 for every method, including Unphased).',
        f'- Picked target area, spread across the 7 automatic methods: {min(f.picked_range_pct):.2f}–{max(f.picked_range_pct):.2f}% of the median per acquisition '
        f'excluding 09:13 ({min(f.picked_range_all):.2f}–{max(f.picked_range_all):.2f}% including it); CV {min(f.picked_cv):.2f}–{max(f.picked_cv):.2f}% '
        f'excluding 09:13 ({min(f.picked_cv_all):.2f}–{max(f.picked_cv_all):.2f}% including it). '
        f'Fixed-window completion area: {min(f.fixed_range_pct):.2f}–{max(f.fixed_range_pct):.2f}% excluding 09:13. (The previous report expressed the '
        'picked spread relative to DX: 4.32–10.63%.)',
        f'- S/N: cross-method range {min(f.snr_spread):.2f}–{max(f.snr_spread):.2f}% of the median per acquisition excluding 09:13 (earlier report, range / DX '
        f'including 09:13: 1.97–17.32%); per-method median deviation from the cross-method median {f.snr_range[0]:.1f}–{f.snr_range[1]:.1f}%.',
        f'- Peak position: every method within {f.ppm_max:.4f} ppm of the cross-method median.',
        f'- Trend: minimum pairwise Pearson r {f.min_pearson:.4f} and Spearman ρ {f.min_spearman:.3f} across all 28 method pairs including Unphased '
        f'(picked area; fixed-window minimum Pearson r {f.min_pearson_fixed:.4f}); {f.late.agree[0].lower()+f.late.agree[1:]}; '
        f'whole-run change {f.picked["R_dynamic_range_over_median_algorithm_range"]:.1f}× (picked) and {f.fixed["R_dynamic_range_over_median_algorithm_range"]:.1f}× '
        f'(fixed) the median cross-method range; only {f.picked["R_endpoint_window"]:.2f}× and {f.fixed["R_endpoint_window"]:.2f}× inside the endpoint window '
        '(10:26–11:36).',
        f'- Historical completion (unchanged rule): {hist}.',
        f'- Threshold sensitivity (fixed window): flips at Symmetry {f.trans[("completion_fixed_window", "symmetry_objective")]:.2f}%, Combined '
        f'{f.trans[("completion_fixed_window", "combined_objective_v1")]:.2f}%, Peak minima {f.trans[("completion_fixed_window", "peak_minima")]:.2f}%, DX '
        f'{f.trans[("completion_fixed_window", "production")]:.2f}%, Ernst {f.trans[("completion_fixed_window", "ernst_integral_p0")]:.2f}%, DEEP '
        f'{f.trans[("completion_fixed_window", "deep_phaser")]:.2f}%, ACME {f.trans[("completion_fixed_window", "acme")]:.2f}% (Unphased '
        f'{f.trans[("completion_fixed_window", "unphased")]:.2f}%); agreement minimum {f.fx_min_agreement}/7 near 4.8%.', '',
        '## Important scientific cautions', '',
        '- **No method has been established as ground truth.** Agreement, correlation, optimizer success and closeness to the median are not accuracy.',
        '- Shape terms assume isolated positive absorptive bands. The custom objectives (Combined, Symmetry) partly optimize the evaluated terms.',
        '- Completion classifications are retrospective; no physical stop was established. Candidate endpoint metrics are exploratory and do not replace '
        'the historical rule.',
        '- 2,054 protected historical files (pytest temp folders under `results/NMR_validation_100226/logs/pytest_*`) are unreadable to this '
        'Windows account (owner-only ACLs from a different sandbox principal). The integrity check reports them as unverifiable, not unchanged, and '
        'tolerates access denial only for the exact paths in the reviewed baseline `verification/ACCESS_DENIED_BASELINE.json` (never raw input).', '',
        '## Output package', '',
        '| Path | Content |', '|---|---|',
        '| `README.md` | start here; one-command rerun |',
        '| `boss_summary/` | boss figures 01–14 and S1, `BOSS_SUMMARY.md`, `WHICH_ALGORITHM_IS_BEST.md`, `METHOD_AGREEMENT_SUMMARY.csv`, presentation |',
        '| `tables/` | per-acquisition metrics, method spread, deviations, normalized trends, strong-peak changes, effective phase, internal-reference audit |',
        '| `timeseries/` | target-area and fixed-window completion series |',
        '| `plateau_analysis/` | historical criterion traces, exploratory endpoint metrics, threshold sensitivity, gate margins, change points |',
        '| `spectra/`, `peak_regions/` | per-acquisition arrays (`.npz`) and figures for every method |',
        '| `method_comparisons/` | cohort comparisons, effective phase, old agreement-plot audit |',
        '| `verification/` | integrity checks, tests, independent review notes (`NN_*_review.md`), `FINAL_AUDIT_RESULTS.md` |',
        '| `logs/` | figure manifest (dataset titles + hashes), software versions, superseded v1 outputs |', '',
        'Rerun everything, verifying saved arrays and regenerating tables, figures and reports:', '',
        '```powershell',
        '& "C:\\Users\\iyer95\\miniconda3\\envs\\ai\\python.exe" "C:\\code\\chemyx_pump\\scripts\\nmr\\run_algorithm_validation.py" --input '
        '"C:\\code\\chemyx_pump\\results\\raw\\nmr\\06-09-26" --output "C:\\code\\chemyx_pump\\results\\100426_algovalidation"',
        '```', '',
        'Refinement only (reads saved tables and arrays; fast): `python code/robustness_refinement.py --output <package>`. Tests: '
        '`python -m pytest verification/test_validation_adapter.py verification/test_integrity_check.py verification/test_robustness_refinement.py -q -p no:cacheprovider`. Presentation: '
        'see `boss_summary/PRESENTATION_BUILD.md`.', '',
        '## GUI', '',
        'The manual review GUI is implemented with **PySide6 / Qt for Python** (`scripts/nmr/validation_phase_gui.py`, `scripts/nmr/phase4.py`). It '
        'loads the saved automatic methods for side-by-side comparison and allows editable P0/P1 manual phasing; explicit saves become human '
        'checkpoints. Exactly one genuine checkpoint exists (11:08:23, saved 2026-10-04); it is a single reference, not a validated time series.', '',
        '## Suggested next steps', '',
        '1. Acquire replicate FIDs of a static sample to measure area precision, then set any endpoint tolerance from it.',
        '2. Validate a rolling three-point range rule with confirmation on runs that have an independently confirmed endpoint.',
        '3. Keep the historical rule as production until such validation exists; never substitute filename or file-modification time for LONG DATE.']
    (Path(res.out)/'CONTEXT_FOR_NEW_LLM.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def write_documents(out, d, res):
    res.out = Path(out)
    f = facts(d, res)
    which_algorithm(d, res, f)
    endpoint_rows = endpoint_report(d, res, f)
    agreement_plot_audit(d, res)
    boss_summary(d, res, f)
    presentation_data(d, res, f, endpoint_rows)
    context_for_new_llm(d, res, f, endpoint_rows)


if __name__ == '__main__':
    main()
