"""Read-only historical adapters and supplementary diagnostics; no processing redesign."""
from __future__ import annotations
import argparse
import csv
import errno
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import nmrglue as ng
import numpy as np
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from chemyx_lab.analysis import nmr_validation as v, phase_gallery as g
from chemyx_lab.analysis import additional_phase_methods as a
from chemyx_lab.analysis.nmr import read_jcamp_fid, _build_complex_spectrum
from chemyx_lab.analysis.phase_quality import prepare_context, score_spectrum
from chemyx_lab.analysis.statistics_report import SpectrumStat, PeakObservation
from chemyx_lab.analysis.target_peak_report import build_target_peak_analysis
from chemyx_lab.analysis.completion import _current_decision
from chemyx_lab.analysis.plot_titles import format_dataset_plot_title, resolve_dataset_display_name

METHODS = ('unphased', 'production', 'acme', 'peak_minima', 'combined_objective_v1',
           'symmetry_objective', 'ernst_integral_p0', 'deep_phaser')
LABELS = dict(zip(METHODS, ('Unphased FFT', 'DX metadata', 'ACME', 'Peak minima',
                           'Combined objective', 'Symmetry objective', 'Ernst P0', 'DEEP Phaser')))
LABELS['manual'] = 'Manual checkpoint'
STEMS = dict(zip(METHODS, ('unphased', 'dx_metadata', 'acme', 'peak_minima', 'combined', 'symmetry', 'ernst', 'deep')))
STEMS['manual'] = 'manual'
COLORS = {**a.COLORS, 'manual': '#E69F00'}
WINDOWS = {'2ppm': (1.80, 2.35), '7ppm': (6.70, 7.30), 'target': (5.70, 5.90)}
QUIET = {'quiet_3p6_4p5': (3.60, 4.50), 'quiet_10_11': (10.0, 11.0)}
SHAPE = ('negative_area_fraction', 'dispersive_metric', 'peak_asymmetry', 'even_imaginary_fraction')
CORE = ['nmr.py', 'nmr_validation.py', 'phase_quality.py', 'phase_optimizers.py',
        'additional_phase_methods.py', 'statistics_report.py', 'target_peak_report.py',
        'completion.py', 'time_series.py', 'target_peak_config.py']

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def json_safe(value):
    if isinstance(value, dict): return {str(k): json_safe(vv) for k, vv in value.items()}
    if isinstance(value, (tuple, list)): return [json_safe(vv) for vv in value]
    if isinstance(value, np.ndarray): return json_safe(value.tolist())
    if isinstance(value, np.generic): return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value): return None
    if isinstance(value, Path): return str(value)
    return value

def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(value), indent=2, allow_nan=False), encoding='utf-8')

def write_rows(path, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader()
        w.writerows([{k: (json.dumps(json_safe(val)) if isinstance(val, (dict, list, tuple)) else json_safe(val))
                     for k, val in row.items()} for row in rows])

def rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f: return list(csv.DictReader(f))

def protected_listing(args, walker=os.walk):
    """Files under the protected roots plus directories this account cannot list (never silently skipped)."""
    paths, unreadable = [], []
    for base in (args.input, args.historical_root):
        for folder, _, files in walker(base, onerror=lambda error: unreadable.append(str(Path(error.filename).resolve()))):
            paths.extend(Path(folder, name).resolve() for name in sorted(files))
    return paths, sorted(unreadable)

def scientific_source_hashes():
    source = {str(ROOT/'chemyx_lab/analysis'/name): digest(ROOT/'chemyx_lab/analysis'/name) for name in CORE}
    for name in ('scripts/nmr/process_fid.py', 'configs/nmr/analysis.yaml'):
        source[str(ROOT/name)] = digest(ROOT/name)
    return source

def snapshot(args):
    paths, unreadable = protected_listing(args)
    print(f'Hashing {len(paths)} raw/historical files', flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        frozen = dict(zip((str(p) for p in paths), pool.map(digest, paths)))
    return {'frozen_files': frozen, 'scientific_sources': scientific_source_hashes(), 'unreadable_directories': unreadable}

ACCESS_BASELINE = 'verification/ACCESS_DENIED_BASELINE.json'
ACCESS_BASELINE_PIN = '4f343f2bf9cd6624008f919c851a46e14f6f75eaa66abc266c49c7e303f7a4b0'  # reviewed 2026-10-04 (2,054 files, 19 dirs)

def baseline_digest(baseline):
    return hashlib.sha256(json.dumps({'files': sorted(baseline['files']), 'directories': sorted(baseline['directories'])},
                                     sort_keys=True).encode()).hexdigest()

def verify_protected(args, expected, baseline=None, hasher=None, lister=os.listdir, walker=os.walk, pinned=None):
    """Re-hash the initial snapshot. An access-denied file is tolerated only if it is in the reviewed baseline, is not
    raw input, and sits under a baseline directory that is still unlistable; anything else fails."""
    hasher = hasher or digest
    files = expected['frozen_files']; raw_root = Path(args.input).resolve()
    allowed_files = set(baseline['files']) if baseline else set()
    allowed_dirs = set(baseline['directories']) if baseline else set()
    def probe(path):
        if os.path.isdir(path): return 'not_a_file'
        try: return hasher(path)
        except FileNotFoundError: return 'missing'
        except IsADirectoryError: return 'not_a_file'
        except PermissionError: return 'access_denied'
    def unlistable(folder):
        try: lister(folder); return False
        except PermissionError: return True
    def tolerated(path):
        if path not in allowed_files or Path(path).is_relative_to(raw_root): return False
        return any(str(a) in allowed_dirs and unlistable(str(a)) for a in Path(path).parents)
    with ThreadPoolExecutor(max_workers=8) as pool:
        observed = dict(zip(files, pool.map(probe, files)))
    present, unreadable_dirs = protected_listing(args, walker)
    sources = scientific_source_hashes()
    denied = [p for p in files if observed[p] == 'access_denied']
    result = {'raw_and_historical_file_count': len(files),
        'verified_unchanged_count': sum(observed[p] == h for p, h in files.items()),
        'changed_frozen_files': [p for p, h in files.items() if observed[p] not in ('access_denied', 'missing', 'not_a_file', h)],
        'missing_frozen_files': [p for p in files if observed[p] == 'missing'],
        'no_longer_a_file': [p for p in files if observed[p] == 'not_a_file'],
        'access_denied_outside_baseline': [p for p in denied if not tolerated(p)],
        'added_historical_or_raw_files': sorted(set(map(str, present))-set(files)),
        'changed_scientific_sources': [p for p, h in expected['scientific_sources'].items() if sources.get(p) != h],
        'unverifiable_access_denied_files': [p for p in denied if tolerated(p)],
        'unreadable_directories': unreadable_dirs,
        'new_unreadable_directories': [d for d in unreadable_dirs if d not in allowed_dirs],
        'access_baseline': ({'path': ACCESS_BASELINE, 'files': len(allowed_files), 'directories': len(allowed_dirs),
                             'files_sha256': baseline.get('files_sha256')} if baseline else None)}
    result['access_baseline_matches_reviewed_pin'] = None if not (baseline and pinned) else baseline_digest(baseline) == pinned
    result['passed'] = result['access_baseline_matches_reviewed_pin'] is not False and not any(result[k] for k in (
        'changed_frozen_files', 'missing_frozen_files', 'no_longer_a_file', 'access_denied_outside_baseline',
        'added_historical_or_raw_files', 'changed_scientific_sources', 'new_unreadable_directories'))
    result['fully_verified'] = result['passed'] and not (result['unverifiable_access_denied_files'] or unreadable_dirs)
    result['note'] = ('Access-denied files are reported as unverifiable, never as unchanged, and only within the reviewed baseline '
                      f'{ACCESS_BASELINE}. Raw input must always verify. Files added inside unreadable directories cannot be enumerated.')
    return result

def identities(args):
    found = {}; copies = {}
    legacy_path = args.historical_root/'phase_validation/cohort_phase_quality_metrics.csv'
    legacy_rows = rows(legacy_path) if legacy_path.exists() else []
    for source in sorted(args.input.rglob('*.dx')):
        fid = read_jcamp_fid(source); stamp, field = v.acquisition_time(fid.metadata)
        if field != 'LONG DATE header': raise ValueError(f'LONG DATE unavailable: {source}')
        if stamp.date().isoformat() != '2026-06-09': raise ValueError(f'Unexpected acquisition date: {source}')
        rawhash = digest(source); ident = stamp.strftime('%Y%m%d_%H%M%S')+'_'+rawhash[:8]
        if rawhash in found:
            if found[rawhash]['acquisition_time'] != stamp.isoformat(): raise ValueError('Duplicate hash with conflicting time')
            copies[rawhash].append(str(source)); continue
        old = args.historical_root/'phase_validation'/ident
        meta = read_json(old/'00_metadata.json')
        assert meta['raw_sha256'] == rawhash and meta['timestamp'] == stamp.isoformat()
        assert digest(old/'raw_source.dx') == rawhash
        if fid.metadata.get('DATA TYPE') != 'NMR FID' or len(fid.complex_points) != 8192:
            raise ValueError(f'Not expected complex raw FID: {source}')
        found[rawhash] = {'acquisition_id': ident, 'acquisition_time': stamp.isoformat(),
            'timestamp_source': field, 'long_date_raw': fid.metadata['LONG DATE'],
            'raw_sha256': rawhash, 'source_path': str(source.resolve()), 'complex_fid_points': len(fid.complex_points),
            'historical_folder': str(old.resolve()), 'historical_metadata_id': meta['acquisition_id'],
            'historical_summary_ids': sorted({r['acquisition_id'] for r in legacy_rows if r.get('raw_sha256') == rawhash and r.get('timestamp') == stamp.isoformat()}),
            'data_type': fid.metadata.get('DATA TYPE'), 'ntuple_type': fid.metadata.get('NTUPLES')}
        copies[rawhash] = [str(source.resolve())]
    result = sorted(found.values(), key=lambda r: r['acquisition_time'])
    if len(result) != 8 or len({r['acquisition_time'] for r in result}) != 8: raise ValueError('Expected eight unique June 9 acquisitions')
    start = datetime.fromisoformat(result[0]['acquisition_time'])
    cfg = v.pipeline()._target_peak_config([])
    for index, row in enumerate(result):
        row.update(sequence_index=index, elapsed_hours=(datetime.fromisoformat(row['acquisition_time'])-start).total_seconds()/3600,
                   duplicate_copy_paths=copies[row['raw_sha256']], dataset_display_name=resolve_dataset_display_name(
                       cfg.dataset_display_name, input_paths=args.input))
    write_rows(args.output/'input_manifest.csv', result)
    write_json(args.output/'logs/historical_target_config.json', asdict(cfg))
    return result

def load_arrays(folder):
    with np.load(folder/'spectral_evidence.npz') as data: return {k: data[k].copy() for k in data.files}

def status_for(args, rec, method, folder):
    if method in ('acme', 'peak_minima'):
        path = args.historical_root/'acquisitions'/rec['acquisition_id']/'alternative_methods'/method/'optimizer.json'
        status = read_json(path); success = status['converged']
    elif method in a.NEW_METHODS:
        path = folder/'optimizer_result.json'; status = read_json(path); success = status['success']
    else:
        path = folder/'phase_parameters.json'; status = {'status': 'no_optimizer_reference' if method == 'unphased' else 'stored_metadata', 'success': True}; success = True
    return {**status, 'success': bool(success), 'status_evidence': str(path.resolve())}

def record_failure(output, destination, provenance):
    """Archive stale successful artifacts only within this task-owned output."""
    output = Path(output).resolve(); destination = Path(destination).resolve()
    if output not in destination.parents: raise ValueError('Failure destination must stay in task output')
    if destination.exists():
        archive = output/'logs/failure_archives'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')/destination.relative_to(output)
        archive.parent.mkdir(parents=True, exist_ok=True)
        destination.rename(archive)
    write_json(destination/'provenance.json', provenance)

def fresh_methods(args, rec, common):
    """Invoke existing estimators without writing any historical file."""
    src = Path(rec['source_path']); old = Path(rec['historical_folder'])
    parameters = read_json(old/'automated/processing_metadata.json')['parameters']
    prod = v.analyze(src, v.production_args(src, parameters))
    out = args.output/'raw_recomputed'/rec['acquisition_id']; out.mkdir(parents=True, exist_ok=True)
    generated = {'production': (prod, {'success': True, 'status': 'metadata_applied'})}
    generated['unphased'] = (v.analyze(src, prod['args'], phase=(0., 0., False, 'unphased'), unphased=common),
                             {'success': True, 'status': 'no_optimizer_reference'})
    for result, status in v.alternative_phases(prod):
        generated[status['method']] = (result, {**status, 'success': bool(status['converged'])})
    context = prepare_context(prod['spectrum'].ppm_axis, common)
    for method in a.NEW_METHODS:
        status = a.method_status(out, method, context, prod, rec)
        result = None
        if status['success']:
            result = v.analyze(src, prod['args'], phase=(status['p0_deg'], status['p1_deg'], status.get('inverse_phase', False), method), unphased=common)
        generated[method] = (result, status)
    for method, (result, status) in generated.items():
        folder = out/'analysis'/method; folder.mkdir(parents=True, exist_ok=True)
        write_json(folder/'optimizer_result.json', status)
        if result is not None: g.export_arrays(out, method, result, stem=STEMS[method])
    return generated, out

def processing(args, records):
    data = {}; checks = []
    for rec in records:
        ident = rec['acquisition_id']; old = Path(rec['historical_folder'])
        parameters = read_json(old/'automated/processing_metadata.json')['parameters']
        fid, x, z0, points, lb = _build_complex_spectrum(Path(rec['source_path']),
            line_broadening_hz=parameters['line_broadening_hz'], zero_fill_points=parameters['zero_fill_points'],
            truncation_window=parameters['truncation_window'])
        rec.update(fft_points=points, line_broadening_hz=lb)
        rawdir = args.output/'spectra'/ident; rawdir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(rawdir/'raw_fid_and_common_fft.npz', raw_complex_fid=fid.complex_points, ppm=x, unphased_complex_fft=z0)
        generated, freshroot = fresh_methods(args, rec, z0) if args.recompute else ({}, None)
        data[ident] = {}
        selections = [(m, None) for m in METHODS]
        _, checkpoints = g.manual_status(old)
        if checkpoints: selections.append(('manual', checkpoints[-1]))
        for method, checkpoint in selections:
            historical = old/'analysis'/method
            if checkpoint:
                cp = read_json(checkpoint)
                assert cp['raw_sha256'] == rec['raw_sha256'] and cp['analysis_role'] == 'manual_reference' and cp['explicit_gui_save']
                source = checkpoint.parent; mp = cp['manual_phase']
                phase = {'p0_deg': mp['effective_index_zero_p0_deg'], 'p1_deg': mp['p1_deg'], 'inverse_phase': mp['inverse'], 'phase_denominator': points, 'method': 'manual'}
                status = {'success': True, 'status': 'explicit_human_checkpoint', 'status_evidence': str(checkpoint), 'reviewer': cp['reviewer'], 'saved_at_utc': cp['created_at_utc']}
            else:
                source = freshroot/'analysis'/method if args.recompute else historical
                status = ({**generated[method][1], 'status_evidence': str(source/'optimizer_result.json')} if args.recompute
                          else status_for(args, rec, method, source))
                if not status['success']:
                    data[ident][method] = {'status': status, 'source': source, 'phase': {}, 'arrays': None, 'target': {}}
                    record_failure(args.output, rawdir/STEMS[method], {'status': status, 'failure': True, **rec})
                    continue
                phase = read_json(source/'phase_parameters.json')
            arrays = load_arrays(source)
            np.testing.assert_array_equal(x, arrays['ppm']); np.testing.assert_array_equal(z0, arrays['unphased'])
            applied = ng.proc_base.ps(z0, p0=phase['p0_deg'], p1=phase['p1_deg'], inv=phase['inverse_phase'])
            np.testing.assert_array_equal(applied, arrays['phased'])
            if method == 'unphased':
                assert phase['p0_deg'] == phase['p1_deg'] == 0 and not phase['inverse_phase']
                np.testing.assert_array_equal(arrays['phased'], z0)
            np.testing.assert_allclose(arrays['phased'].real-arrays['quantitative_real'], arrays['baseline'], rtol=0, atol=0)
            target = read_json(source/'target_result.json'); peaks = rows(source/'peak_results.csv')
            destination = rawdir/STEMS[method]; destination.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(destination/'spectral_evidence.npz', **arrays, magnitude=np.abs(arrays['phased']))
            write_json(destination/'phase_parameters.json', phase); write_json(destination/'target_result.json', target)
            write_rows(destination/'peak_results.csv', peaks)
            provenance = {**rec, 'method': method, 'display_method': LABELS[method], 'phase': phase, 'status': status,
                'source_npz': str(source/'spectral_evidence.npz'), 'source_npz_sha256': digest(source/'spectral_evidence.npz'),
                'mode': 'fresh_existing_functions' if args.recompute and method != 'manual' else 'verified_saved_arrays',
                'reference_equation': 'FFTshift(FFT(em(raw complex FID, LB=0.03 Hz), zero_fill=65536)); no phase',
                'phase_equation': 'S[k] exp(sign*i*pi/180*(P0+P1*k/N)); sign=-1 inverse, +1 direct',
                'real_channel_use': 'regional quantitative Real for picked target; global quantitative Real for completion',
                'magnitude_use': 'diagnostics only'}
            write_json(destination/'provenance.json', provenance)
            data[ident][method] = {'arrays': arrays, 'target': target, 'peaks': peaks, 'phase': phase, 'status': status, 'source': source, 'destination': destination}
            checks.append({'acquisition_id': ident, 'method': method, 'common_fft_exact': True, 'axis_exact': True,
                'phase_replay_exact': True, 'baseline_identity_exact': True, 'raw_sha256': rec['raw_sha256'],
                'source_npz_sha256': provenance['source_npz_sha256']})
        print(f'Processed {rec["acquisition_time"]}: {len(data[ident])} methods', flush=True)
    write_rows(args.output/'input_manifest.csv', records)
    write_rows(args.output/'verification/processing_reproducibility_checks.csv', checks)
    failed = [{'acquisition_id': ident, 'method': method, 'status': result['status']}
              for ident, group in data.items() for method, result in group.items() if not result['status']['success']]
    write_json(args.output/'verification/processing_check_summary.json', {'checks': len(checks), 'failures': len(failed), 'failed_methods': failed,
        'references': 8, 'manual_acquisitions': sum('manual' in d for d in data.values()), 'mode': 'recompute' if args.recompute else 'reuse'})
    return data

def local_context(context, bounds):
    """Fixed dataset windows, magnitude center selected once per acquisition."""
    eligible = np.flatnonzero((context.ppm >= bounds[0]) & (context.ppm <= bounds[1]))
    center = int(eligible[np.argmax(np.abs(context.fft[eligible]))])
    dx = float(np.median(np.diff(context.ppm)))
    radius = min(int(.18/dx), center-int(eligible[0]), int(eligible[-1])-center)
    window = np.arange(center-radius, center+radius+1)
    if radius < 4: return None, center, radius
    return replace(context, centers=[center], windows=[window]), center, radius

def baseline_stats(x, y):
    """Descriptive offset/RMS and line-detrended residual, not new correction."""
    x = np.asarray(x); y = np.asarray(y)
    coeff = np.polyfit(x-x.mean(), y, 1)
    residual = y-np.polyval(coeff, x-x.mean())
    return {'mean_au': float(np.mean(y)), 'rms_about_mean_au': float(np.sqrt(np.mean((y-y.mean())**2))),
        'slope_au_per_ppm': float(coeff[0]), 'detrended_rms_au': float(np.sqrt(np.mean(residual**2)))}

def change(reference, value):
    if reference is None or value is None or not np.isfinite(reference) or not np.isfinite(value): return None, None
    delta = float(value-reference)
    return delta, (100*delta/abs(reference) if abs(reference) > 1e-12 else None)

def metrics(args, records, data):
    measurements = []; quiet_rows = []; comparisons = []; centers = []
    for rec in records:
        group = data[rec['acquisition_id']]; common = group['unphased']['arrays']
        context = prepare_context(common['ppm'], common['unphased'])
        locals_ = {region: local_context(context, bounds) for region, bounds in WINDOWS.items()}
        for region, (ctx, center, radius) in locals_.items():
            centers.append({'acquisition_id': rec['acquisition_id'], 'region': region, 'center_ppm': context.ppm[center],
                'diagnostic_left_ppm': context.ppm[center-radius], 'diagnostic_right_ppm': context.ppm[center+radius],
                'radius_scoring_points': radius, 'scoring_stride': context.metadata['scoring_stride'],
                'diagnostic_status': 'valid' if ctx is not None else 'insufficient_symmetric_support_radius_lt_4',
                'center_source': 'maximum common FFT magnitude on existing decimated scoring grid',
                'fixed_plot_left_ppm': WINDOWS[region][0], 'fixed_plot_right_ppm': WINDOWS[region][1]})
        for method, result in group.items():
            phase = result['phase']; target = result['target']; status = result['status']
            row = {k: rec[k] for k in ('acquisition_id', 'acquisition_time', 'elapsed_hours', 'raw_sha256', 'timestamp_source', 'dataset_display_name')}
            row.update(method=method, display_method=LABELS[method], p0_deg=phase.get('p0_deg'), p1_deg=phase.get('p1_deg'),
                inverse_phase=phase.get('inverse_phase'), success=status['success'], convergence_status=status.get('status', 'converged' if status['success'] else 'failed'),
                status_evidence=status['status_evidence'], source_arrays=str(result['source']/'spectral_evidence.npz'),
                target_area_definition='moving boundaries; positive regional-baseline-corrected Real; not completion area')
            if not status['success']:
                measurements.append(row); continue
            arrays = result['arrays']; x = arrays['ppm']; z = arrays['phased']
            global_score = score_spectrum(z, context)
            row.update({'global_'+k: val for k, val in global_score.items()})
            for region, (ctx, center, radius) in locals_.items():
                score = score_spectrum(z, ctx) if ctx is not None else {k: None for k in SHAPE}
                prefix = 'p2' if region == '2ppm' else 'p7' if region == '7ppm' else 'target_phase'
                row.update({prefix+'_'+k: score[k] for k in SHAPE})
                row[prefix+'_diagnostic_status'] = 'valid' if ctx is not None else 'insufficient_symmetric_support_radius_lt_4'
                row[prefix+'_magnitude_center_ppm'] = float(context.ppm[center])
                mask = (x >= WINDOWS[region][0]) & (x <= WINDOWS[region][1])
                pos = np.flatnonzero(mask)[np.argmax(z.real[mask])]
                row[prefix+'_real_max_ppm'] = float(x[pos]); row[prefix+'_real_max_height_au'] = float(z.real[pos])
                if ctx is None:
                    row[prefix+'_local_baseline_offset_au'] = None
                    row[prefix+'_local_edge_residual_rms_au'] = None
                else:
                    yy = z[ctx.indices][ctx.windows[0]].real; nedge = max(2, len(yy)//8)
                    left, right = np.median(yy[:nedge]), np.median(yy[-nedge:])
                    footline = np.linspace(left, right, len(yy)); residual = yy-footline
                    edges = np.r_[residual[:nedge], residual[-nedge:]]
                    row[prefix+'_local_baseline_offset_au'] = float((left+right)/2)
                    row[prefix+'_local_edge_residual_rms_au'] = float(np.sqrt(np.mean(edges**2)))
            candidates = [p for p in result['peaks'] if v.truth(p.get('qc_pass')) and v.truth(p.get('in_target_window'))]
            best = max(candidates, key=lambda p: float(p['snr'])) if candidates else None
            row.update(target_peak_ppm=target['peak_ppm'], target_height_au=target['height'], target_area_au_ppm=target['area'],
                target_snr=target['snr'], target_detected=target['detected'], target_baseline_au=target['baseline'],
                target_residual_mad_noise_au=target['noise'],
                target_integration_left_ppm=v.finite(best.get('integration_left_ppm')) if best else None,
                target_integration_right_ppm=v.finite(best.get('integration_right_ppm')) if best else None,
                target_qc_pass=v.truth(best['qc_pass']) if best else False,
                target_qc_flags=best.get('qc_failure_reasons', '') if best else 'no eligible QC-passing target',
                target_position_caution='weak earliest candidate may be a different feature' if rec['sequence_index'] == 0 else '')
            for region, bounds in QUIET.items():
                mask = (x >= bounds[0]) & (x <= bounds[1])
                for processing_stage, values in [('prebaseline_phased_real', z.real), ('post_global_baseline_real', arrays['quantitative_real'])]:
                    br = {'acquisition_id': rec['acquisition_id'], 'acquisition_time': rec['acquisition_time'], 'method': method,
                        'quiet_region': region, 'left_ppm': bounds[0], 'right_ppm': bounds[1], 'stage': processing_stage,
                        'caution': 'low-magnitude regions contain broad tails; not pure white noise ground truth', **baseline_stats(x[mask], values[mask])}
                    quiet_rows.append(br)
                    row[region+'_'+processing_stage+'_rms_au'] = br['rms_about_mean_au']
                    row[region+'_'+processing_stage+'_offset_au'] = br['mean_au']
            result['metrics'] = row; measurements.append(row)
        reference = group['unphased']['metrics']
        for method, result in group.items():
            if method == 'unphased' or not result['status']['success']: continue
            value = result['metrics']
            entries = [(region, prefix+'_'+metric, 'conditional_lower_is_better')
                       for region, prefix in [('2ppm', 'p2'), ('7ppm', 'p7'), ('target_5p8', 'target_phase')] for metric in SHAPE]
            entries += [('target_5p8', k, 'change_only_no_accuracy_direction') for k in
                        ('target_area_au_ppm', 'target_snr', 'target_height_au', 'target_peak_ppm', 'target_residual_mad_noise_au')]
            entries += [(region, region+'_'+stage+'_rms_au', 'descriptive_baseline_change_only')
                        for region in QUIET for stage in ('prebaseline_phased_real', 'post_global_baseline_real')]
            for region, metric, direction in entries:
                ref, val = reference.get(metric), value.get(metric); delta, percent = change(ref, val)
                flag = 'missing' if delta is None else 'undefined_percent_zero_reference' if percent is None else 'changed'
                if delta is not None and direction == 'conditional_lower_is_better':
                    flag = 'numerically_unchanged' if np.isclose(val, ref, rtol=1e-10, atol=1e-12) else 'lower' if delta < 0 else 'higher'
                comparisons.append({'acquisition_id': rec['acquisition_id'], 'acquisition_time': rec['acquisition_time'],
                    'method': method, 'region': region, 'metric': metric, 'unphased_value': ref, 'method_value': val,
                    'absolute_change': delta, 'percent_change': percent, 'direction_interpretation': direction, 'flag': flag,
                    'percent_status': 'undefined_zero_reference' if ref is not None and abs(ref)<=1e-12 else 'unavailable_metric' if delta is None else 'defined',
                    'interpretation_limit': 'lower shape terms support absorptive symmetry only for isolated positive peaks; center/multiplets can confound; target area has no preferred direction'})
    write_rows(args.output/'tables/per_acquisition_method_metrics.csv', measurements)
    write_rows(args.output/'tables/baseline_quality.csv', quiet_rows)
    write_rows(args.output/'tables/improvement_vs_unphased.csv', comparisons)
    write_rows(args.output/'verification/shared_diagnostic_windows.csv', centers)
    write_json(args.output/'verification/metric_definitions.json', {'plot_windows': WINDOWS, 'quiet_windows': QUIET,
        'shape_code': str(ROOT/'chemyx_lab/analysis/phase_quality.py'), 'shape_code_sha256': digest(ROOT/'chemyx_lab/analysis/phase_quality.py'),
        'symmetry_windows': 'common magnitude centers on existing decimated grid; halfwidth <=0.18 ppm, clipped to fixed plot windows; shared across methods',
        'minimum_symmetric_support': 'radius >=4 scoring points; smaller edge-truncated windows produce missing shape and local-edge baseline terms, never zero',
        'local_baseline': 'median complex edge values, linear diagnostic footline; not a downstream processing change',
        'local_rms': 'RMS of real residual at both edge eighths after diagnostic footline',
        'baseline_offset': 'mean of left/right median edge Real; broad tails may remain',
        'target_baseline_rms': 'target_residual_mad_noise_au is exact historical robust regional noise; target_phase_local_edge_residual_rms_au is a supplemental edge RMS',
        'percent_equation': '100*(method-unphased)/abs(unphased), undefined if abs(unphased)<=1e-12',
        'percent_direction': 'negative denotes decrease, not automatically improvement',
        'numerically_unchanged': 'numpy isclose rtol=1e-10, atol=1e-12; numerical tolerance only, not practical equivalence',
        'limits': 'Shape diagnostics are partly related to custom objective terms; separate strong regions and target/completion outputs reduce but do not eliminate circularity. No accuracy ground truth.'})
    print(f'Metrics: {len(measurements)} acquisition/method rows; {len(comparisons)} differences', flush=True)

def time_series(args, records, data):
    series = []; strong = []; pairs = []
    for rec in records:
        row = {k: rec[k] for k in ('acquisition_id', 'acquisition_time', 'elapsed_hours', 'timestamp_source', 'long_date_raw')}
        for method in (*METHODS, 'manual'):
            result = data[rec['acquisition_id']].get(method)
            row[STEMS[method]+'_area_au_ppm'] = result['target']['area'] if result and result['status']['success'] else None
            if result and result['status']['success']:
                for region, prefix in [('2ppm', 'p2'), ('7ppm', 'p7')]:
                    strong.append({'acquisition_id': rec['acquisition_id'], 'acquisition_time': rec['acquisition_time'],
                        'elapsed_hours': rec['elapsed_hours'], 'timestamp_source': rec['timestamp_source'], 'method': method,
                        'region': region, **{k: result['metrics'][prefix+'_'+k] for k in SHAPE}})
        series.append(row)
    for left, right in combinations(METHODS, 2):
        ll = np.array([r[STEMS[left]+'_area_au_ppm'] if r[STEMS[left]+'_area_au_ppm'] is not None else np.nan for r in series])
        rr = np.array([r[STEMS[right]+'_area_au_ppm'] if r[STEMS[right]+'_area_au_ppm'] is not None else np.nan for r in series])
        mask = np.isfinite(ll)&np.isfinite(rr); correlation = float(np.corrcoef(ll[mask], rr[mask])[0,1]) if mask.sum() >= 3 else None
        rank = float(spearmanr(ll[mask], rr[mask]).statistic) if mask.sum() >= 3 else None
        for i, rec in enumerate(records):
            delta, percent = change(ll[i], rr[i])
            pairs.append({'method_reference': left, 'method_comparison': right, 'acquisition_id': rec['acquisition_id'],
                'acquisition_time': rec['acquisition_time'], 'reference_area_au_ppm': ll[i], 'comparison_area_au_ppm': rr[i],
                'signed_difference_au_ppm': delta, 'percent_difference_relative_reference': percent,
                'pearson_r_eight_point_sequence': correlation, 'spearman_rank_r_eight_point_sequence': rank,
                'paired_count': int(mask.sum()), 'interpretation': 'correlation measures trend agreement, not accuracy or lack of bias'})
    write_rows(args.output/'timeseries/target_area_timeseries.csv', series)
    write_rows(args.output/'timeseries/strong_peak_phase_quality.csv', strong)
    write_rows(args.output/'tables/target_area_method_comparison.csv', pairs)
    print('Time series and pairwise agreement tables saved', flush=True)

def spectrum_stat(rec, result, index):
    ar = result['arrays']; phase = result['phase']
    params = read_json(Path(rec['historical_folder'])/'automated/processing_metadata.json')
    # Derive SF from authoritative raw metadata through the existing spectrum builder.
    from chemyx_lab.analysis.nmr import build_phased_spectrum
    sf = build_phased_spectrum(Path(rec['source_path']), line_broadening_hz=.03, zero_fill_points=65536).observe_frequency_mhz
    peaks = [PeakObservation(i+1, '', float(p['peak_ppm']), float(p.get('peak_height', p['height'])),
        float(p['width_ppm']), float(p['width_ppm'])*sf, float(p['signed_area']), float(p['positive_area']),
        float(p['snr']), float(p['prominence_snr']), p['classification'], float(p['prominence'])) for i,p in enumerate(result['peaks'])]
    return SpectrumStat(Path(rec['source_path']).name, rec['source_path'], index, datetime.fromisoformat(rec['acquisition_time']),
        sf, result['target']['noise'], 0., True, phase['p0_deg'], phase['p1_deg'], peaks,
        ar['region_ppm'], ar['regional_quantitative'], ar['ppm'], ar['quantitative_real'], rec['timestamp_source'])

def plateau(args, records, data):
    config = v.pipeline()._target_peak_config([]); outcomes = []; allfixed = []; alltrace = []
    for method in METHODS:
        results = [data[r['acquisition_id']][method] for r in records]
        if not all(rr['status']['success'] for rr in results):
            outcomes.append({'method': method, 'display_method': LABELS[method], 'complete': None,
                'first_retrospective_completion': None, 'status': 'incomplete_method_sequence', 'later_departure_warning': None,
                'notes': 'failed methods remain missing; no substitute phase'})
            unavailable_trace=[]
            for i,(rec,result) in enumerate(zip(records,results)):
                fixed_area=None
                if result['status']['success']:
                    measure=build_target_peak_analysis([spectrum_stat(rec,result,i)],config).measurements[0]
                    measure.update(spectrum_index=i,elapsed_time_hours=rec['elapsed_hours'])
                    fixed={'acquisition_id':rec['acquisition_id'],'method':method,**measure,'completion_quality_pass':None,
                        'area_definition':'fixed 5.70–5.90 global Real; completion QC unavailable for incomplete sequence'}
                    result['completion_measurement']=fixed;allfixed.append(fixed);fixed_area=measure['area']
                tr={'acquisition_id':rec['acquisition_id'],'method':method,'acquisition_time':rec['acquisition_time'],
                    'timestamp_source':rec['timestamp_source'],'fixed_window_area_au_ppm':fixed_area,
                    'current_prefix_qualifies':None,'current_prefix_status':'incomplete_method_sequence',
                    'current_prefix_reason':'Full method sequence unavailable; no missing acquisition or phase substituted',
                    'completion_quality_pass':None,**{('threshold_'+k):val for k,val in asdict(config.completion).items()}}
                unavailable_trace.append(tr);alltrace.append(tr)
            write_rows(args.output/f'plateau_analysis/{STEMS[method]}_criterion_trace.csv',unavailable_trace)
            write_json(args.output/f'plateau_analysis/{STEMS[method]}_completion.json',outcomes[-1])
            continue
        report = build_target_peak_analysis([spectrum_stat(r, rr, i) for i,(r,rr) in enumerate(zip(records,results))], config)
        times = [r['elapsed_hours'] for r in records]; values = [m['area'] for m in report.measurements]
        limit = config.completion.low_area_absolute if config.completion.low_area_absolute is not None else config.completion.low_area_fraction_of_max*max(values)
        quality = [bool(m['analysis_quality_pass'] and (m['peak_quality_pass'] or m['area'] <= limit)) for m in report.measurements]
        trace = []
        for i, (rec, measure, rate, original) in enumerate(zip(records, report.measurements, report.rates, report.decision_trace)):
            fixed = {'acquisition_id': rec['acquisition_id'], 'method': method, **measure,
                'completion_quality_pass': quality[i], 'completion_low_area_limit_full_sequence': limit,
                'area_definition': 'fixed 5.70–5.90; global quantitative Real; local linear footline'}
            allfixed.append(fixed)
            status, direction, current_complete, reason, evidence, terms, warnings = _current_decision(times[:i+1], values[:i+1], quality[:i+1], config.completion)
            tr = {'acquisition_id': rec['acquisition_id'], 'method': method, 'acquisition_time': rec['acquisition_time'],
                'timestamp_source': rec['timestamp_source'], 'fixed_window_area_au_ppm': measure['area'],
                'completion_quality_pass': quality[i], 'current_prefix_qualifies': current_complete,
                'current_prefix_status': status, 'current_prefix_reason': reason, 'current_prefix_evidence': evidence,
                **terms, **{('threshold_'+k): val for k,val in asdict(config.completion).items()},
                **{('latched_'+k): val for k,val in original.items()},
                'adjacent_percent_change': rate['percent_change_per_interval'], 'current_prefix_warnings': '; '.join(warnings)}
            trace.append(tr); alltrace.append(tr)
            results[i]['completion_measurement'] = fixed; results[i]['completion_trace'] = tr
        decision = report.completion.as_dict(); warn = decision['quality_warnings']
        outcome = {'method': method, 'display_method': LABELS[method], 'complete': decision['complete'],
            'first_retrospective_completion': decision['completion_timestamp'], 'completion_index': decision['completion_index'],
            'status': decision['status'], 'evidence_level': decision['evidence_level'],
            'later_departure_warning': any('depart' in w for w in warn), 'quality_warnings': '; '.join(warn),
            'notes': 'retrospective classification; no physical stop established', **decision['metrics']}
        outcomes.append(outcome)
        write_rows(args.output/f'plateau_analysis/{STEMS[method]}_criterion_trace.csv', trace)
        write_json(args.output/f'plateau_analysis/{STEMS[method]}_completion.json', decision)
    write_rows(args.output/'plateau_analysis/completion_by_method.csv', outcomes)
    write_rows(args.output/'plateau_analysis/full_criterion_trace.csv', alltrace)
    write_rows(args.output/'timeseries/completion_fixed_window_timeseries.csv', allfixed)
    data['_completion'] = outcomes
    print('Historical completion replay saved', flush=True)

class PlotWriter:
    LAYOUT = (0, .035, 1, .94)
    def __init__(self, args, records):
        self.args = args; self.dataset = records[0]['dataset_display_name']; self.manifest = []
        plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11, 'axes.spines.top': False,
                             'axes.spines.right': False, 'savefig.facecolor': 'white'})
    def save(self, fig, relative, title, source, acquisition=None, check=None):
        visible = format_dataset_plot_title(self.dataset, title)
        fig.suptitle(visible, fontsize=15, fontweight='bold')
        fig.tight_layout(rect=self.LAYOUT)
        if check is not None: check(fig)
        path = self.args.output/relative; path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(6):  # Windows reports a transiently mapped/previewed file as EINVAL or EACCES
            try:
                fig.savefig(path, dpi=170, metadata={'Title': visible, 'Description': str(source)}); break
            except OSError as error:
                if attempt == 5 or error.errno not in (errno.EINVAL, errno.EACCES): raise
                time.sleep(.5*(attempt+1))
        self.manifest.append({'path': str(path.resolve()), 'dataset_display_name': self.dataset,
            'visible_title': visible, 'source': str(source), 'acquisition_id': acquisition or '', 'format': 'PNG', 'sha256': digest(path)})
        plt.close(fig)

def draw_spectrum(ax, result, method, bounds, target=False, imaginary=False):
    if not result['status']['success']:
        ax.text(.5,.5,LABELS[method]+' FAILED\nSpectrum unavailable',transform=ax.transAxes,ha='center',va='center')
        ax.set_xlim(bounds[1],bounds[0]);return
    ar = result['arrays']; x = ar['region_ppm'] if target else ar['ppm']
    y = ar['regional_quantitative'] if target else ar['phased'].real
    ax.plot(x, y, color=COLORS[method], lw=1, label=LABELS[method])
    if imaginary and not target: ax.plot(x, ar['phased'].imag, color='#787878', lw=.8, alpha=.8, label='Imaginary (dispersive partner expected)')
    ax.axhline(0, color='#aaaaaa', lw=.6); ax.set_xlim(bounds[1], bounds[0])
    ax.set_xlabel('Chemical shift (ppm)'); ax.set_ylabel('Real intensity (a.u.)')
    mask = (x >= bounds[0]) & (x <= bounds[1]); yl = y[mask]
    if imaginary and not target: yl = np.r_[yl, ar['phased'].imag[mask]]
    lo, hi = min(0., float(np.min(yl))), max(0., float(np.max(yl))); span = max(hi-lo, 1.)
    ax.set_ylim(lo-.10*span, hi+.15*span)
    if target:
        metric = result['metrics']
        for k in ('target_integration_left_ppm', 'target_integration_right_ppm'):
            if metric[k] is not None: ax.axvline(metric[k], color='#888888', lw=.7, ls=':')
        ax.axvspan(5.70, 5.90, color='#999999', alpha=.06)

# Boss example legends: compact, upper-right, below the panel note. A panel falls back to just
# outside its upper-right edge only if the inside position would cover plotted signal.
LEGEND_STYLE = dict(fontsize=7, handlelength=1.3, handletextpad=.45, borderpad=.3,
                    labelspacing=.2, borderaxespad=.35, framealpha=.92)
LEGEND_CLEARANCE_PX = 4  # at the 100 dpi check canvas, about 1 mm around the legend frame
LEGEND_PLACEMENTS = (('upper_right_inside_below_note', dict(loc='upper right', bbox_to_anchor=(1., .895))),
                     ('outside_upper_right_above_axes', dict(loc='lower right', bbox_to_anchor=(1., 1.))),
                     ('outside_upper_right_beside_axes', dict(loc='upper left', bbox_to_anchor=(1.01, 1.))))

def upper_right_legend(ax, placement=0):
    return ax.legend(**LEGEND_PLACEMENTS[placement][1], **LEGEND_STYLE)

def segments_hitting(xy, rect):
    """Drawn polyline segments intersecting rect=(x0, y0, x1, y1), all in display pixels."""
    if len(xy) < 2: return 0
    p, q = xy[:-1], xy[1:]; x0, y0, x1, y1 = rect
    finite = np.all(np.isfinite(p), 1) & np.all(np.isfinite(q), 1)
    overlap = ((np.maximum(p[:, 0], q[:, 0]) >= x0) & (np.minimum(p[:, 0], q[:, 0]) <= x1) &
               (np.maximum(p[:, 1], q[:, 1]) >= y0) & (np.minimum(p[:, 1], q[:, 1]) <= y1))
    d = q-p; corners = np.array([[x0, y0], [x0, y1], [x1, y0], [x1, y1]])
    cross = d[:, None, 0]*(corners[None, :, 1]-p[:, None, 1])-d[:, None, 1]*(corners[None, :, 0]-p[:, None, 0])
    return int(np.count_nonzero(finite & overlap & (cross.min(1) <= 0) & (cross.max(1) >= 0)))

def legend_overlap(fig, ax):
    """What the legend box would hide: traces, zero line, notes, title or another panel."""
    fig.canvas.draw(); renderer = fig.canvas.get_renderer()
    box = ax.get_legend().get_window_extent(renderer).padded(LEGEND_CLEARANCE_PX); axbox = ax.get_window_extent(renderer)
    clip = (max(box.x0, axbox.x0), max(box.y0, axbox.y0), min(box.x1, axbox.x1), min(box.y1, axbox.y1))
    visible = clip[0] < clip[2] and clip[1] < clip[3]
    hits = {'trace_segments_hidden': 0, 'zero_line_segments_hidden': 0, 'integration_boundary_segments_hidden': 0}
    for line in ax.get_lines():
        x, y = (np.asarray(v, dtype=float) for v in line.get_data(orig=False))
        if not len(x):
            continue
        count = segments_hitting(line.get_transform().transform(np.column_stack([x, y])), clip) if visible else 0
        key = ('trace_segments_hidden' if len(x) > 2 else 'zero_line_segments_hidden' if np.allclose(y, y[0])
               else 'integration_boundary_segments_hidden' if np.allclose(x, x[0]) else 'trace_segments_hidden')
        hits[key] += count
    others = [o for o in fig.axes if o is not ax and o.get_visible()]
    result = {**hits, 'note_text_overlap': any(box.overlaps(t.get_window_extent(renderer)) for t in ax.texts),
        'title_overlap': bool(ax.get_title()) and box.overlaps(ax.title.get_window_extent(renderer)),
        'other_panel_overlap': any(box.overlaps(o.get_tightbbox(renderer)) for o in others),
        'inside_figure': bool(box.x0 >= 0 and box.y0 >= 0 and box.x1 <= fig.bbox.x1 and box.y1 <= fig.bbox.y1),
        'legend_axes_fraction': [round(float(v), 4) for v in ax.transAxes.inverted().transform([[box.x0, box.y0], [box.x1, box.y1]]).ravel()]}
    # Dotted integration boundaries are annotations, reported but not treated as hidden signal.
    result['passed'] = (result['trace_segments_hidden'] == 0 and result['zero_line_segments_hidden'] == 0 and not
        (result['note_text_overlap'] or result['title_overlap'] or result['other_panel_overlap']) and result['inside_figure'])
    return result

def legend_layout_check(fig, axes, layout, record, figure):
    """Keep the first placement per panel that hides no signal; fail closed otherwise."""
    chosen = {}
    for ax in axes:
        if ax.get_legend() is None: continue
        for index in range(len(LEGEND_PLACEMENTS)):
            if index:
                ax.get_legend().remove(); upper_right_legend(ax, index); fig.tight_layout(rect=layout)
            if legend_overlap(fig, ax)['passed']: break
        chosen[ax] = index
    for ax, index in chosen.items():  # a fallback re-lays out the figure, so re-check every panel
        final = legend_overlap(fig, ax)
        record.append({'figure': figure, 'panel': ax.get_title(), 'placement': LEGEND_PLACEMENTS[index][0], **final})
        if not final['passed']: raise RuntimeError(f'Legend would hide plotted signal: {figure} {ax.get_title()}')

def method_summary(args, records, data):
    allrows = []; diffs = rows(args.output/'tables/improvement_vs_unphased.csv')
    for method in (*METHODS, 'manual'):
        rr = [data[r['acquisition_id']][method] for r in records if method in data[r['acquisition_id']]]
        if not rr: continue
        good = [r for r in rr if r['status']['success']]
        comp = [d for d in diffs if d['method'] == method]
        row = {'method': method, 'display_method': LABELS[method], 'successful_acquisitions': len(good),
            'available_acquisitions': len(rr), 'cohort_acquisitions': 8, 'failure_count': sum(not r['status']['success'] for r in rr),
            'detection_count': sum(r['target']['detected'] for r in good), 'role': 'reference' if method == 'unphased' else 'single human reference' if method == 'manual' else 'automatic comparator'}
        for metric in ('target_area_au_ppm', 'target_snr'):
            vals = [abs(float(d['percent_change'])) for d in comp if d['metric'] == metric and d['percent_change']]
            row[metric+'_median_absolute_percent_change_vs_unphased'] = float(np.median(vals)) if vals else (0. if method == 'unphased' else None)
            row[metric+'_max_absolute_percent_change_vs_unphased'] = float(max(vals)) if vals else (0. if method == 'unphased' else None)
            delta = [abs(float(d['absolute_change'])) for d in comp if d['metric'] == metric and d['absolute_change']]
            row[metric+'_median_absolute_change_vs_unphased'] = float(np.median(delta)) if delta else (0. if method == 'unphased' else None)
            row[metric+'_max_absolute_change_vs_unphased'] = float(max(delta)) if delta else (0. if method == 'unphased' else None)
        for region in ('2ppm', '7ppm'):
            for metric in SHAPE:
                dd = [d for d in comp if d['region'] == region and d['metric'].endswith('_'+metric)]
                pc = [float(d['percent_change']) for d in dd if d['percent_change']]
                row[region+'_'+metric+'_median_percent_change'] = float(np.median(pc)) if pc else None
                row[region+'_'+metric+'_lower_count'] = sum(d['flag'] == 'lower' for d in dd)
                row[region+'_'+metric+'_higher_count'] = sum(d['flag'] == 'higher' for d in dd)
            direction = [row[region+'_'+m+'_median_percent_change'] for m in SHAPE]
            finite = [x for x in direction if x is not None]
            row[region+'_profile'] = ('reference' if method == 'unphased' else 'unavailable shape metrics' if not finite else
                'lower on all median shape terms' if finite and all(x < 0 for x in finite) else
                'higher on all median shape terms' if finite and all(x > 0 for x in finite) else 'mixed shape metrics')
        for region in QUIET:
            key = region+'_prebaseline_phased_real_rms_au'
            vals = [float(d['percent_change']) for d in comp if d['metric'] == key and d['percent_change']]
            row[region+'_prebaseline_rms_median_percent_change'] = float(np.median(vals)) if vals else None
        outcome = next((o for o in data['_completion'] if o['method'] == method), {})
        row.update(first_retrospective_completion=outcome.get('first_retrospective_completion'),
            later_departure_warning=outcome.get('later_departure_warning'), completion_status=outcome.get('status', 'not evaluated: single checkpoint'),
            warnings=outcome.get('quality_warnings', ''), interpretation='No single winner score; shape metrics conditional, area changes not accuracy')
        allrows.append(row)
    write_rows(args.output/'tables/method_summary.csv', allrows)
    write_rows(args.output/'boss_summary/BOSS_RESULTS_TABLE.csv', allrows)
    return allrows

def figures_and_reports(args, records, data):
    pw = PlotWriter(args, records); times = np.array([r['elapsed_hours'] for r in records])
    ticks = [r['acquisition_time'][11:16] for r in records]
    summaries = method_summary(args, records, data)
    for rec in records:
        group = data[rec['acquisition_id']]; stamp = rec['acquisition_time'][11:19]; ident = rec['acquisition_id']; name = ident[:15]
        full_bounds = (float(group['unphased']['arrays']['ppm'][0]), float(group['unphased']['arrays']['ppm'][-1]))
        for method, result in group.items():
            if not result['status']['success']:
                for region,bounds in [('full',full_bounds),*WINDOWS.items()]:
                    fig,ax=plt.subplots(figsize=(11,4.8));draw_spectrum(ax,result,method,bounds)
                    relative=f'spectra/{name}_{STEMS[method]}_full.png' if region=='full' else f'peak_regions/{name}_{STEMS[method]}_{"target_5p8ppm" if region=="target" else "peak_"+region}.png'
                    pw.save(fig,relative,stamp+' '+LABELS[method]+' '+region+' FAILED',result['status']['status_evidence'],ident)
                continue
            met = result['metrics']; phase = result['phase']; titlebase = f'{stamp} {LABELS[method]}'
            for region, bounds in [('full', full_bounds), *WINDOWS.items()]:
                target = region == 'target'; fig, ax = plt.subplots(figsize=(11,4.8))
                draw_spectrum(ax, result, method, bounds, target=target, imaginary=region in ('2ppm','7ppm'))
                if target:
                    ax.set_title(f'Picked area {met["target_area_au_ppm"]:.4g} a.u.·ppm | S/N {met["target_snr"]:.3g} | detected={met["target_detected"]}')
                    note = 'Regional quantitative Real; dotted lines = moving integration boundaries. Completion uses separate fixed-window global Real.'
                else:
                    ax.set_title(f'P0={phase["p0_deg"]:.3f}°, P1={phase["p1_deg"]:.3f}° | {"inverse" if phase["inverse_phase"] else "direct"}; index-zero k/N')
                    note = 'Prebaseline phased Real; magnitude is diagnostic only. Imaginary signal need not vanish.'
                fig.text(.04, .015, note, fontsize=9)
                relative = f'spectra/{name}_{STEMS[method]}_full.png' if region == 'full' else f'peak_regions/{name}_{STEMS[method]}_{"target_5p8ppm" if target else "peak_"+region}.png'
                pw.save(fig, relative, titlebase+' '+('Full Real / Absorption spectrum' if region == 'full' else region+' comparison'), result['destination']/'spectral_evidence.npz', ident)
        for target in (False, True):
            fig, axes = plt.subplots(4,2,figsize=(13,11),sharex=True,sharey=True)
            bounds = WINDOWS['target'] if target else full_bounds
            minima = []; maxima = []
            for ax, method in zip(axes.flat, METHODS):
                rr = group[method]
                if not rr['status']['success']: ax.set_title(LABELS[method]+' FAILED'); continue
                draw_spectrum(ax, group['unphased'], 'unphased', bounds, target=target)
                draw_spectrum(ax, rr, method, bounds, target=target)
                ar = rr['arrays']; xx = ar['region_ppm'] if target else ar['ppm']; yy = ar['regional_quantitative'] if target else ar['phased'].real
                mm = (xx >= bounds[0]) & (xx <= bounds[1]); minima.append(min(0.,float(yy[mm].min()))); maxima.append(float(yy[mm].max()))
                ax.set_title(LABELS[method], fontsize=11); ax.set_xlabel('ppm'); ax.set_ylabel('Real (a.u.)')
            if minima:
                lo,hi=min(minima),max(maxima); span=max(hi-lo,1.)
                for ax in axes.flat: ax.set_ylim(lo-.08*span,hi+.08*span)
            fig.text(.03,.008,'Each panel shares axes and overlays its method with the gray Unphased reference. '+('Regional target area; completion uses separate fixed-window global area.' if target else 'Prebaseline Real/Absorption.'),fontsize=9)
            pw.save(fig,f'{"peak_regions" if target else "spectra"}/{name}_all_methods_{"target" if target else "full"}_overlay.png',
                stamp+' All methods '+('target comparison' if target else 'full spectrum comparison'),args.output/'tables/per_acquisition_method_metrics.csv',ident)
        print('Detailed figures:', stamp, flush=True)
    # Sequence figure: split into two groups with identical axes and a repeated reference.
    def sequence_figure(relative):
        fig, axes = plt.subplots(2,1,figsize=(12,8),sharex=True,sharey=True)
        for ax, group in zip(axes, (METHODS[:4], ('unphased', *METHODS[4:]))):
            for method in group:
                yy = [data[r['acquisition_id']][method]['target'].get('area', np.nan) for r in records]
                ax.plot(times,yy,'o-',ms=4,lw=1.3,color=COLORS[method],label=LABELS[method])
            ax.legend(ncol=3,fontsize=10); ax.set_ylabel('Picked area (a.u.·ppm)'); ax.grid(alpha=.2)
        axes[-1].set_xticks(times,ticks); axes[-1].set_xlabel('Acquisition time, June 9 (UTC−04; JCAMP LONG DATE)')
        fig.text(.04,.012,'Moving-boundary regional target areas. Retrospective completion separately uses fixed 5.70–5.90 ppm global Real.',fontsize=9)
        pw.save(fig,relative,'Target area versus acquisition time — all methods',args.output/'timeseries/target_area_timeseries.csv')
    sequence_figure('timeseries/target_area_all_methods.png')
    maxarea = max(rr['target'].get('area',0) for r in records for rr in data[r['acquisition_id']].values())
    maxfixed = max(v.finite(rr.get('completion_measurement',{}).get('area'),0.) for r in records for rr in data[r['acquisition_id']].values())
    for method in METHODS:
        yy = [data[r['acquisition_id']][method]['target'].get('area', np.nan) for r in records]
        fixed = [data[r['acquisition_id']][method].get('completion_measurement',{}).get('area',np.nan) for r in records]
        fig,axes = plt.subplots(2,1,figsize=(11,7),sharex=True)
        axes[0].plot(times,yy,'o-',color=COLORS[method]); axes[0].set_ylabel('Picked target area (a.u.·ppm)'); axes[0].set_ylim(-.03*maxarea,1.12*maxarea)
        axes[1].plot(times,fixed,'o-',color=COLORS[method]); axes[1].set_ylabel('Completion area (a.u.·ppm)'); axes[1].set_ylim(-.03*maxfixed,1.12*maxfixed)
        outcome = next(o for o in data['_completion'] if o['method'] == method)
        idx = outcome.get('completion_index')
        if idx is not None:
            for ax in axes: ax.axvline(times[idx],color='#333333',ls='--',label='First retrospective completion'); ax.legend(fontsize=9)
        axes[0].set_title('Moving boundaries; regional polynomial-corrected Real')
        axes[1].set_title('Fixed 5.70–5.90 ppm; global ALS Real + linear footline')
        axes[-1].set_xticks(times,ticks); axes[-1].set_xlabel('JCAMP LONG DATE acquisition time (UTC−04)')
        fig.text(.04,.012,'Historical rule: ≥6 points, ≥1 h; last 4-point |slope|≤5 a.u.·ppm/h AND ≤10%/h; 3 intervals ≤5%, with QC. CI is evidence, not a gate.',fontsize=8.5)
        pw.save(fig,f'timeseries/{STEMS[method]}_target_area.png',LABELS[method]+' target area and retrospective completion',args.output/'timeseries/completion_fixed_window_timeseries.csv')
    for metric in ('dispersive_metric','peak_asymmetry','even_imaginary_fraction'):
        fig,axes = plt.subplots(2,2,figsize=(13,8),sharex=True,sharey='row')
        for row,(region,prefix) in enumerate([('2 ppm','p2'),('7 ppm','p7')]):
            for col,group in enumerate((METHODS[:4],('unphased',*METHODS[4:]))):
                ax=axes[row,col]
                for method in group:
                    ax.plot(times,[data[r['acquisition_id']][method].get('metrics',{}).get(prefix+'_'+metric,np.nan) for r in records],
                        'o-',ms=3,lw=1,color=COLORS[method],label=LABELS[method])
                ax.set_title(region); ax.set_ylabel(metric.replace('_',' ')); ax.set_xticks(times,ticks,fontsize=8);ax.grid(alpha=.2)
                if row==0:ax.legend(ncol=2,fontsize=8)
        fig.text(.04,.012,'Same magnitude-selected center per acquisition/method; prebaseline complex diagnostic. Lower is conditional on positive isolated resonance symmetry.',fontsize=9)
        pw.save(fig,f'timeseries/strong_peaks_{metric}.png','Strong resonance '+metric.replace('_',' ')+' over time',args.output/'timeseries/strong_peak_phase_quality.csv')
    fig,allaxes=plt.subplots(1,3,figsize=(13,6),gridspec_kw={'width_ratios':[1,1,.06]})
    axes=allaxes[:2]
    corrected=METHODS[1:]
    for ax,region in zip(axes,('2ppm','7ppm')):
        mat=np.array([[next(s for s in summaries if s['method']==method)[region+'_'+m+'_median_percent_change'] for m in SHAPE] for method in corrected],dtype=float)
        image_=ax.imshow(mat,vmin=-100,vmax=100,cmap='RdBu_r',aspect='auto')
        ax.set_xticks(range(4),['Negative','Odd Real','Asymmetry','Even Imag'],rotation=20,ha='right');ax.set_yticks(range(7),[LABELS[m] for m in corrected]);ax.set_title(region+' median % change')
        for i in range(7):
            for j in range(4):ax.text(j,i,f'{mat[i,j]:+.0f}%' if np.isfinite(mat[i,j]) else 'n/a',ha='center',va='center',fontsize=10,color='black')
    fig.colorbar(image_,cax=allaxes[2],label='Change vs Unphased (%)')
    fig.text(.04,.012,'Blue = lower term; red = higher. Colors saturate at ±100%; labels show actual values. Shape assumptions and reference denominators matter.',fontsize=9)
    pw.save(fig,'method_comparisons/phase_quality_change_vs_unphased.png','Strong resonance phase-quality changes versus Unphased',args.output/'tables/improvement_vs_unphased.csv')
    fig,ax=plt.subplots(figsize=(12,5))
    for i,outcome in enumerate(data['_completion']):
        idx=outcome.get('completion_index')
        if idx is not None:
            ax.scatter(times[idx],i,color=COLORS[outcome['method']],s=90)
            ax.text(times[idx]+.04,i,outcome['first_retrospective_completion'][11:19]+(' + later departure' if outcome['later_departure_warning'] else ''),va='center',fontsize=10)
        else:ax.text(.05,i,('Unavailable — ' if outcome['complete'] is None else 'Never qualifies — ')+outcome['status'],va='center',fontsize=10)
    ax.set_yticks(range(8),[LABELS[m] for m in METHODS]);ax.set_ylim(7.6,-.6);ax.set_xlim(0,3.65);ax.set_xticks(times,ticks,fontsize=9);ax.set_xlabel('JCAMP LONG DATE acquisition time (UTC−04)');ax.grid(axis='x',alpha=.2)
    fig.text(.03,.012,'Retrospective classification only. Wide slope CI yields moderate evidence; no physical controller stop established.',fontsize=9)
    pw.save(fig,'plateau_analysis/completion_by_method.png','First retrospective completion by method',args.output/'plateau_analysis/completion_by_method.csv')
    fig,axes=plt.subplots(1,2,figsize=(13,5))
    prod=np.array([data[r['acquisition_id']]['production']['target'].get('area',np.nan) for r in records])
    for method in METHODS:
        vv=np.array([data[r['acquisition_id']][method]['target'].get('area',np.nan) for r in records])
        axes[0].plot(times,100*(vv-prod)/np.abs(prod),'o-',ms=3,color=COLORS[method],label=LABELS[method])
        axes[1].scatter(prod,vv,s=25,color=COLORS[method],label=LABELS[method])
    axes[0].set_xticks(times,ticks,fontsize=8);axes[0].set_ylabel('Area difference from DX (%)');axes[0].set_xlabel('JCAMP LONG DATE acquisition time');axes[0].axhline(0,color='#999999',lw=.7)
    axes[1].plot([0,maxarea],[0,maxarea],color='#555555',ls='--',lw=.8);axes[1].set_xlabel('DX picked area (a.u.·ppm)');axes[1].set_ylabel('Comparator area (a.u.·ppm)');axes[1].legend(ncol=2,fontsize=8)
    fig.text(.04,.012,'Secondary historical-reference view. DX metadata = stored vendor phase used by earlier processing; not ground truth. Primary view: boss_summary/08_method_spread_vs_time.png.',fontsize=9)
    pw.save(fig,'method_comparisons/target_area_agreement.png','Agreement with historical DX-metadata processing',args.output/'tables/target_area_method_comparison.csv')
    boss_plots(pw,args,records,data,summaries)
    refinement_module().run(args.output,pw)  # method-robustness refinement: tables, figures 08-14, boss documents
    write_rows(args.output/'logs/figure_manifest.csv',pw.manifest)
    reports(args,records,data,summaries,pw.manifest)
    print(f'Generated {len(pw.manifest)} PNG figures',flush=True)

def refinement_module():
    spec=importlib.util.spec_from_file_location('robustness_refinement',Path(__file__).with_name('robustness_refinement.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def boss_plots(pw,args,records,data,summaries):
    example=next(r for r in records if r['acquisition_time'][11:19]=='11:08:23'); group=data[example['acquisition_id']]; legend_record=[]
    full_bounds=(float(group['unphased']['arrays']['ppm'][0]),float(group['unphased']['arrays']['ppm'][-1]))
    for number,(region,bounds) in enumerate([('full',full_bounds),('2ppm',WINDOWS['2ppm']),('7ppm',WINDOWS['7ppm']),('target',WINDOWS['target'])],1):
        fig,axes=plt.subplots(1,3,figsize=(14,4.8),sharey=True)
        for ax,method in zip(axes,('production','acme','combined_objective_v1')):
            draw_spectrum(ax,group['unphased'],'unphased',bounds,target=region=='target')
            draw_spectrum(ax,group[method],method,bounds,target=region=='target')
            if not group[method]['status']['success']:
                ax.set_title(LABELS[method]+' FAILED',fontsize=12);continue
            rr=group[method]['metrics']; ref=group['unphased']['metrics']
            if region in ('2ppm','7ppm'):
                prefix='p2' if region=='2ppm' else 'p7'; key=prefix+'_even_imaginary_fraction'
                delta,pc=change(ref[key],rr[key]); note=f'Even Imag fraction {ref[key]:.3f} → {rr[key]:.3f} ({pc:+.1f}%)'
            elif region=='target':note=f'Area {rr["target_area_au_ppm"]:.3f}; S/N {rr["target_snr"]:.1f}; detected {rr["target_detected"]}'
            else:note='Common raw FID; phase is the only input difference'
            ax.set_title(LABELS[method],fontsize=12);ax.text(.02,.96,note,transform=ax.transAxes,va='top',fontsize=8.5);upper_right_legend(ax)
        yy=[]
        for method in ('unphased','production','acme','combined_objective_v1'):
            if not group[method]['status']['success']:continue
            ar=group[method]['arrays'];xx=ar['region_ppm'] if region=='target' else ar['ppm'];zz=ar['regional_quantitative'] if region=='target' else ar['phased'].real
            yy.append(zz[(xx>=bounds[0])&(xx<=bounds[1])])
        lo=min(0.,min(float(y.min()) for y in yy));hi=max(float(y.max()) for y in yy);span=max(hi-lo,1.);lo-=span*.1;hi+=span*.15
        for ax in axes:ax.set_ylim(lo,hi)
        fig.text(.03,.012,'11:08:23 is prespecified for the completion comparison; three displayed comparators span stored, classical and custom methods. All methods/cohort are in detailed figures.',fontsize=8.5)
        relative=f'boss_summary/{number:02d}_{region}_before_after.png'
        pw.save(fig,relative,'11:08:23 '+region+' Unphased and phase comparators',args.output/'tables/per_acquisition_method_metrics.csv',example['acquisition_id'],
            check=lambda fig,axes=axes,relative=relative:legend_layout_check(fig,axes,pw.LAYOUT,legend_record,relative))
    write_json(args.output/'verification/legend_placement_check.json',{'passed':all(r['passed'] for r in legend_record),
        'rule':'upper-right inside below the panel note; outside the upper-right edge only if the inside box would hide a trace, the zero line, a note, the title or another panel',
        'method':'segment-rectangle intersection in display pixels after final tight_layout, legend frame padded by about 1 mm; dotted integration boundaries reported, not treated as signal',
        'panels':legend_record})
    with plt.rc_context({'font.size':15}):
        fig,axes=plt.subplots(1,2,figsize=(14,4.6))
        for ax,region in zip(axes,('2ppm','7ppm')):
            for method in ('unphased','production','acme','combined_objective_v1'):
                draw_spectrum(ax,group[method],method,WINDOWS[region])
            yy=[group[m]['arrays']['phased'].real[(group[m]['arrays']['ppm']>=WINDOWS[region][0])&(group[m]['arrays']['ppm']<=WINDOWS[region][1])]
                for m in ('unphased','production','acme','combined_objective_v1') if group[m]['status']['success']]
            lo=min(0.,min(float(y.min()) for y in yy));hi=max(float(y.max()) for y in yy);span=max(hi-lo,1.)
            ax.set_ylim(lo-.1*span,hi+.12*span);ax.set_title(region,fontsize=16);ax.legend(fontsize=12)
        pw.save(fig,'boss_summary/.build/assets/strong_resonance_zooms.png','11:08:23 Strong-resonance Real comparison',args.output/'tables/per_acquisition_method_metrics.csv',example['acquisition_id'])
    stale=args.output/'boss_summary/08_target_area_agreement.png'  # superseded by 08_method_spread_vs_time.png; v1 copy archived
    if stale.exists():
        archived=args.output/'logs/superseded_v1_20261004/boss_summary'/stale.name
        if not archived.exists(): archived.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(stale,archived)
        stale.unlink()
    for source,name in [('timeseries/target_area_all_methods.png','05_target_area_timeseries.png'),('plateau_analysis/completion_by_method.png','06_retrospective_completion.png'),
            ('method_comparisons/phase_quality_change_vs_unphased.png','07_phase_quality_changes.png'),
            ('method_comparisons/target_area_agreement.png','S1_agreement_with_historical_DX_processing.png')]:
        dest=args.output/'boss_summary'/name;shutil.copyfile(args.output/source,dest)
        item=next(r for r in pw.manifest if r['path']==str((args.output/source).resolve()))
        pw.manifest.append({**item,'path':str(dest.resolve()),'sha256':digest(dest),'source':str((args.output/source).resolve())})

def reports(args,records,data,summaries,manifest):
    def mdtable(columns,table):
        return '| '+' | '.join(columns)+' |\n|'+'|'.join(['---']*len(columns))+'|\n'+'\n'.join('| '+' | '.join(str(r.get(k,'')) for k in columns)+' |' for r in table)
    corrected=METHODS[1:]
    spreads=[]
    for r in records:
        rr=[data[r['acquisition_id']][m]['target'] for m in corrected if data[r['acquisition_id']][m]['status']['success']]
        anchor=data[r['acquisition_id']]['production']['target']; areas=[t['area'] for t in rr];snr=[t['snr'] for t in rr];ppm=[t['peak_ppm'] for t in rr if t['peak_ppm'] is not None]
        arange=max(areas)-min(areas) if areas else None;srange=max(snr)-min(snr) if snr else None
        spreads.append({'acquisition_time':r['acquisition_time'], 'automatic_area_spread_au_ppm':arange,
            'automatic_area_spread_percent_of_DX':100*arange/abs(anchor['area']) if arange is not None and anchor.get('area') else None,
            'automatic_snr_spread':srange,'automatic_snr_spread_percent_of_DX':100*srange/abs(anchor['snr']) if srange is not None and anchor.get('snr') else None,
            'automatic_detection_count':sum(t['detected'] for t in rr),'automatic_count':len(rr),'automatic_position_spread_ppm':max(ppm)-min(ppm) if ppm else None})
    write_rows(args.output/'tables/target_measurement_spread_by_acquisition.csv',spreads)
    traces=rows(args.output/'plateau_analysis/full_criterion_trace.csv')
    fixedrows=rows(args.output/'timeseries/completion_fixed_window_timeseries.csv')
    scale_rows=[]; fixed_diffs=[]
    dx=np.array([data[r['acquisition_id']]['production'].get('completion_measurement',{}).get('area',np.nan) for r in records])
    for method in METHODS:
        yy=np.array([data[r['acquisition_id']][method].get('completion_measurement',{}).get('area',np.nan) for r in records])
        valid=np.isfinite(dx)&np.isfinite(yy)
        multiplier=float(np.dot(dx[valid],yy[valid])/np.dot(dx[valid],dx[valid])) if valid.sum()>=2 and np.dot(dx[valid],dx[valid])>0 else np.nan
        residual=yy-multiplier*dx
        scale_rows.append({'method':method,'best_fit_constant_scale_vs_DX':multiplier,
            'paired_count':int(valid.sum()),'residual_rms_after_constant_scale_au_ppm':float(np.sqrt(np.mean(residual[valid]**2))) if valid.any() else None,
            'max_absolute_residual_after_scale_percent_of_DX_max':float(100*np.max(abs(residual[valid]))/np.nanmax(dx)) if valid.any() else None,
            'all_recent_four_completion_QC_at_first_eligible_prefix':all(data[r['acquisition_id']][method].get('completion_measurement',{}).get('completion_quality_pass') for r in records[2:6]) if all(data[r['acquisition_id']][method]['status']['success'] for r in records) else None,
            'interpretation':'nonzero residual distinguishes changing trend shape from constant area scaling; DX is comparison anchor'})
        for i,rec in enumerate(records):
            delta,percent=change(dx[i],yy[i]);fixed_diffs.append({'acquisition_id':rec['acquisition_id'],'acquisition_time':rec['acquisition_time'],
                'method':method,'DX_fixed_area_au_ppm':dx[i],'method_fixed_area_au_ppm':yy[i],'signed_difference_au_ppm':delta,
                'percent_difference_from_DX':percent,'residual_after_constant_scale_au_ppm':residual[i],
                'regional_target_baseline_difference_from_DX_au':change(data[rec['acquisition_id']]['production']['target'].get('baseline'),data[rec['acquisition_id']][method]['target'].get('baseline'))[0],
                'regional_target_noise_difference_from_DX_au':change(data[rec['acquisition_id']]['production']['target'].get('noise'),data[rec['acquisition_id']][method]['target'].get('noise'))[0]})
    write_rows(args.output/'plateau_analysis/constant_scale_vs_shape.csv',scale_rows)
    write_rows(args.output/'plateau_analysis/fixed_area_method_differences.csv',fixed_diffs)
    lines=['# Completion disagreement analysis','',
        'The historical rule consumes fixed-window global quantitative Real after a linear local footline. Picked regional areas shown in the main chemistry plot are a different measurement. Classification is retrospective; it does not establish a controller stop.','',
        '## Critical sixth observation (11:08:23)','',
        '|Method|10:38:47→10:53:46 change (%)|Last-four slope (a.u.·ppm/h)|Relative slope (%/h)|Stable intervals|Qualifies|','|---|---:|---:|---:|---:|---|']
    for method in METHODS:
        trace=[t for t in traces if t['method']==method];six=trace[5];previous=trace[4]
        def val(k):return f'{float(six[k]):.6g}' if six.get(k) else 'unavailable'
        adjacent=v.finite(previous.get('adjacent_percent_change'))
        lines.append(f'|{LABELS[method]}|{adjacent:.6g}|' + f'{val("recent_slope_per_hour")}|{val("recent_relative_slope_percent_per_hour")}|{six.get("stable_interval_count","")}|{six["current_prefix_qualifies"]}|' if adjacent is not None else f'|{LABELS[method]}|unavailable|unavailable|unavailable|unavailable|unavailable|')
    lines+=['','DX and some comparators qualify at observation 6 while Unphased/ACME/DEEP miss the three-interval stability requirement. The table exposes the threshold crossings rather than attributing them to sophistication or accuracy. Peak minima also fails the first eligible relative-slope gate (10.3168%/h >10) and an earlier interval changes by +14.049%. All methods pass recent-four measurement QC at the first eligible prefix, so the initial classification disagreement is not a missing-peak/SNR rejection.','',
        'For ACME, fixed area is 38.2992→40.4435 over the critical interval, versus DX 38.4064→40.2516. The differences span both acquisitions; a single constant scaling would preserve this relative interval change and therefore cannot explain the 5% crossing. DEEP is 41.3836→43.5062, likewise just over 5%. The four early qualifying methods later have departure warnings; the DX subsequent increment is +7.7597% and final area is 14.9946% below its first qualifying value. Peak minima qualifies only at the last available point, leaving no later confirmation.','',
        '## Constant scaling versus changing shape','',mdtable(['method','best_fit_constant_scale_vs_DX','residual_rms_after_constant_scale_au_ppm','max_absolute_residual_after_scale_percent_of_DX_max'],scale_rows),'',
        'The supplementary constant-scale fit uses least squares through zero solely as a comparison diagnostic; it changes no area or completion threshold. Per-acquisition fixed-area residuals, regional baseline offsets and noise differences are in fixed_area_method_differences.csv. Area scaling can alter the absolute-slope gate, while phase-dependent local shape and downstream baseline responses change individual increments. The present data do not independently partition phase bias from baseline bias or establish the true chemical endpoint. Later changes affect slope gates as well as stability counts, so the complete criterion traces remain the authoritative explanation.','',
        'The wider-than-band slope confidence interval produces moderate evidence. It is not silently converted to a hard rejection. The existing full-sequence low-area QC exception and white-noise area uncertainty are retained; uncertainty after zero filling is a lower bound, not an independently established precision estimate.']
    (args.output/'plateau_analysis/COMPLETION_DISAGREEMENT_ANALYSIS.md').write_text('\n'.join(lines),encoding='utf-8')
    avals=[r['automatic_area_spread_percent_of_DX'] for r in spreads if r['automatic_area_spread_percent_of_DX'] is not None]
    svals=[r['automatic_snr_spread_percent_of_DX'] for r in spreads if r['automatic_snr_spread_percent_of_DX'] is not None]
    minspread=min(avals) if avals else np.nan;maxspread=max(avals) if avals else np.nan
    snrmin=min(svals) if svals else np.nan;snrmax=max(svals) if svals else np.nan
    successful=sum(data[r['acquisition_id']][m]['status']['success'] for r in records for m in corrected)
    detected=sum(data[r['acquisition_id']][m]['target'].get('detected',False) for r in records for m in corrected)
    manual_count=sum('manual' in data[r['acquisition_id']] for r in records)
    refined=read_json(args.output/'boss_summary/refinement_data.json')['target']
    spread_text=(f'{refined["range_pct_of_median"][0]:.1f}–{refined["range_pct_of_median"][1]:.1f}% of the median for the picked area, '
                 f'{refined["fixed_range_pct_of_median"][0]:.1f}–{refined["fixed_range_pct_of_median"][1]:.1f}% for the fixed-window completion area')
    story=(f'Eight real June 9 complex FIDs were compared against their common FFT before phase correction. '
        f'Strong 2/7 ppm diagnostics show mixed method-dependent changes; no stored phase or optimizer is treated as ground truth. '
        f'{successful} of 56 automatic method/acquisition results are available, with {detected} detections. Across the seven automatic methods the target area spans {spread_text} at each acquisition (cross-method median, excluding the weak {", ".join(refined["weak_excluded"])} spectrum). '
        f'The earlier DX-relative spreads (area {minspread:.2f}–{maxspread:.2f}%, S/N {snrmin:.2f}–{snrmax:.2f}% of the DX result) are kept below for continuity only. '
        'The broad target-area trend is shared, while the historical fixed-window plateau classification depends on method and later departures weaken early completion evidence. '
        f'{manual_count} existing human checkpoint(s) add comparisons, but independently validated quantitation and a complete manual sequence remain useful.')
    if successful<56:
        story+=f' This run has {56-successful} failed automatic outcomes; unavailable complete sequences have no completion classification.'
    write_json(args.output/'boss_summary/presentation_data.json',{'dataset':records[0]['dataset_display_name'],'story':story,'summaries':summaries,
        'completion':data['_completion'],'spreads':spreads,'target_timeseries':rows(args.output/'timeseries/target_area_timeseries.csv'),
        'example': {m:rr.get('metrics',{'success':False}) for m,rr in data[next(r['acquisition_id'] for r in records if r['acquisition_time'][11:19]=='11:08:23')].items()}})
    result=['# June 9 real-data algorithm validation','',story,'',
        '**Refinement update.** DX metadata is not a reference. The cross-method analysis (median of the seven automatic methods), the strong-peak comparison with Unphased and the exploratory endpoint metrics are in `boss_summary/WHICH_ALGORITHM_IS_BEST.md`, `tables/method_spread_by_time.csv` and `plateau_analysis/ENDPOINT_METRICS_EXPLORATORY.md`. The DX-relative columns below are kept for continuity with the original report.','',
        '## Scope and reference','',
        'Raw `.dx` numbers are complex time-domain FIDs. Phase metadata accompanies them. Unphased means the established apodization/zero-fill/FFT with no phase applied. Seven phase comparators share this exact input. The sole genuine manual checkpoint is the October 4 save of June 9 11:08:23; it is not a manually validated time series.','',
        '## Target agreement','',mdtable(list(spreads[0]),spreads),'',
        'Area spread is the range across seven automatic methods divided by the DX area; it excludes Unphased and manual. Target positions at the weak earliest acquisition may represent a different feature, so detection alone is not chemical identity confirmation. Correlation is reported as trend agreement, not proof of accurate area.','',
        '## Strong-resonance interpretation','',
        'Supplementary fixed windows are 1.80–2.35 ppm and 6.70–7.30 ppm. Every method uses the same phase-invariant magnitude center per acquisition and symmetric diagnostic window. Existing negative-area, odd-Real, asymmetry and even-Imaginary terms are applied to prebaseline complex arrays. Expected odd Imaginary signal is retained. A decrease in a shape term is conditional evidence under positive isolated-resonance symmetry; these resonance/multiplet centers need not be perfect symmetry axes. The custom objectives share some diagnostic terms, so their score advantage is partly in-sample validation. No forced winner or unvalidated equivalence threshold is used.','',
        mdtable(['display_method','2ppm_profile','7ppm_profile','successful_acquisitions','detection_count'],summaries),'',
        '## Retrospective completion','',mdtable(['display_method','first_retrospective_completion','later_departure_warning','status','evidence_level','quality_warnings'],data['_completion']),'',
        'Picked target area uses moving boundaries and regional polynomial correction. Completion uses a separate fixed 5.70–5.90 ppm area from global quantitative Real with linear footline. See `plateau_analysis/COMPLETION_DISAGREEMENT_ANALYSIS.md` and the per-method criterion traces for exact gates. Neither classification nor first qualifying acquisition is a confirmed physical stop.','',
        '## Baseline interpretation','',
        'Quiet diagnostic windows 3.60–4.50 ppm and 10–11 ppm contain broad tails. Both prebaseline phased Real and post-global-baseline Real are tabulated separately. Channel rotation can alter Real RMS without reducing acquisition noise. Downstream baseline correction is not credited as a phase improvement.','',
        '## Evidence and reproducibility','',
        f'{len(manifest)} dataset-titled PNGs; {successful+8+manual_count} successful arrays: {successful+8} of 64 requested automatic/reference combinations plus {manual_count} human checkpoint(s). Failed-state panels and sequence statuses stay explicit. Source-array hashes, phase equations, original optimizer statuses and exact raw-FFT/phase replay checks accompany retained successful arrays. Reports and CSVs are regenerated by the offline runner; original raw and frozen outputs are hash-checked, except 2,054 historical pytest temp files that this account cannot read (reported as unverifiable within the reviewed baseline `verification/ACCESS_DENIED_BASELINE.json`).']
    (args.output/'validation_summary.md').write_text('\n'.join(result),encoding='utf-8')
    # boss_summary/BOSS_SUMMARY.md is written by the refinement; the v1 text is archived in logs/superseded_v1_20261004.
    command='& "C:\\Users\\iyer95\\miniconda3\\envs\\ai\\python.exe" "C:\\code\\chemyx_pump\\scripts\\nmr\\run_algorithm_validation.py" --input "C:\\code\\chemyx_pump\\results\\raw\\nmr\\06-09-26" --output "C:\\code\\chemyx_pump\\results\\100426_algovalidation"'
    readme=['# Algorithm validation — start here','',
        '[Boss summary](boss_summary/BOSS_SUMMARY.md) · [Which algorithm is best?](boss_summary/WHICH_ALGORITHM_IS_BEST.md) · [Context for a new LLM](CONTEXT_FOR_NEW_LLM.md) · [Exploratory endpoint metrics](plateau_analysis/ENDPOINT_METRICS_EXPLORATORY.md) · [Scientific summary](validation_summary.md) · [Main numerical CSV](tables/per_acquisition_method_metrics.csv)','',
        '## Central result','',
        f'How much does phase-algorithm choice affect the scientific conclusion? It changes lineshape and exact target area (cross-method range {spread_text}, excluding the weak 09:13 spectrum), but the broad target time series is essentially unchanged. The historical endpoint criterion is more sensitive than the underlying chemistry trend and should be evaluated for robustness. No phase method is treated as ground truth.','',
        '## One-command rerun','', 'Run from the repository in the existing ai environment. Default verifies and reuses saved numerical results while regenerating every table, figure and report, including the method-robustness refinement (`code/robustness_refinement.py`).','',
        '```powershell',command,'```','',
        "Refinement only (reads this package's saved tables and arrays; about 20 s): `python code/robustness_refinement.py --output <package>`. Tests: `python -m pytest verification/test_validation_adapter.py verification/test_integrity_check.py verification/test_robustness_refinement.py -q -p no:cacheprovider`.",'',
        'Append `--recompute` to invoke the existing phase estimators and quantitative functions from raw FIDs; optional offline DEEP inference requires its already-pinned local Node/runtime/model files. Failure remains explicit. No network inference or hardware access occurs. New inference artifacts go under `raw_recomputed`. Genuine manual saves remain human references and are loaded separately.','',
        'Stage controls: `--stage inputs|process|metrics|timeseries|plateau|figures|all`. These stages are for independent review; the default all executes the complete package. `--stage figures` recomputes tables as well. The initial frozen-file hash snapshot is retained; figures/all re-hashes every readable protected file and tolerates access-denied files only within the reviewed baseline `verification/ACCESS_DENIED_BASELINE.json`.','',
        'Presentation creation has a separate documented command in `boss_summary/PRESENTATION_BUILD.md`. It reads generated JSON/figures, not raw data.','',
        '## Definitions and limitations','',
        'Read verification/PREVIOUS_ANALYSIS_AUDIT.md and UNPHASED_REFERENCE_AUDIT.md first. Picked target areas and completion areas differ by design. All plotted time derives from LONG DATE. Percent differences have missing values at zero reference; area increases are not improvements. Quiet windows contain broad tails. Correlation, optimizer success and algorithm agreement do not establish ground truth.','',
        'Cross-method comparisons use the median of the seven automatic methods; Unphased is reported but never enters it. Candidate endpoint metrics are exploratory and do not replace the historical criterion.','',
        '## Integrity','',
        'The final check re-hashes the initial protected snapshot (`verification/INTEGRITY_BEFORE.json`). Protected files that this Windows account cannot read (owner-only pytest temp folders under `results/NMR_validation_100226/logs/pytest_*` and `logs/additional_phase_methods/pytest_regression`) are reported as unverifiable, never as unchanged, and only for the exact paths in the reviewed, digest-pinned baseline `verification/ACCESS_DENIED_BASELINE.json`; any readable change, deletion or addition, any new access-denied file or unreadable directory, and any edit to the baseline fails the run. Superseded v1 boss outputs are archived in `logs/superseded_v1_20261004/`.','',
        'All generated files except the thin authorized scripts/nmr runner live here. Existing raw files, scientific code and frozen historical outputs are preserved.']
    (args.output/'README.md').write_text('\n'.join(readme),encoding='utf-8')

def parse():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, default=ROOT/'results/raw/nmr/06-09-26')
    p.add_argument('--output', type=Path, default=ROOT/'results/100426_algovalidation')
    p.add_argument('--historical-root', type=Path, default=v.DEFAULT_OUTPUT)
    p.add_argument('--recompute', action='store_true', help='Invoke existing phase estimators and quantitative processing from raw; offline only.')
    p.add_argument('--stage', choices=('inputs', 'process', 'metrics', 'timeseries', 'plateau', 'figures', 'all'), default='all')
    return p.parse_args()

def main():
    args = parse()
    for name in ('input', 'output', 'historical_root'): setattr(args, name, getattr(args, name).resolve())
    if args.output == args.input or args.input in args.output.parents or args.output == args.historical_root or args.historical_root in args.output.parents:
        raise ValueError('Output must be independent of raw and historical package')
    args.output.mkdir(parents=True, exist_ok=True)
    first = args.output/'verification/INTEGRITY_BEFORE.json'
    before = read_json(first) if first.exists() else snapshot(args)
    if not first.exists(): write_json(first, before)
    write_json(args.output/'logs/current_run_integrity_before.json', before)
    versions = {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'matplotlib', 'nmrglue')}
    versions.update(python=sys.version, executable=sys.executable)
    git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, text=True, capture_output=True).stdout
    write_json(args.output/'logs/software_and_code.json', {'versions': versions, 'git_commit': git,
        'git_status': dirty, 'scientific_source_hashes': before['scientific_sources'],
        'runner_sha256': digest(ROOT/'scripts/nmr/run_algorithm_validation.py'), 'implementation_sha256': digest(__file__),
        'command': sys.argv, 'started_utc': datetime.now(timezone.utc).isoformat()})
    records = identities(args)
    if args.stage != 'inputs':
        data = processing(args, records)
        if args.stage not in ('process',):
            metrics(args, records, data)
            if args.stage not in ('metrics',):
                time_series(args, records, data)
                if args.stage not in ('timeseries',):
                    plateau(args, records, data)
                    if args.stage not in ('plateau',): figures_and_reports(args, records, data)
    if args.stage not in ('all', 'figures'):
        print('Stage complete; full final integrity is evaluated with --stage figures/all', flush=True)
        return
    baseline_path = args.output/ACCESS_BASELINE
    baseline = read_json(baseline_path) if baseline_path.exists() else None
    result = verify_protected(args, read_json(first), baseline, pinned=ACCESS_BASELINE_PIN)  # initial snapshot = reference
    write_json(args.output/'verification/FINAL_INTEGRITY_CHECK.json', {**result, 'checked_utc': datetime.now(timezone.utc).isoformat()})
    if not result['passed']: raise RuntimeError('Integrity check failed; see FINAL_INTEGRITY_CHECK.json')
    unverifiable = len(result['unverifiable_access_denied_files'])
    print(f'Integrity passed for {result["verified_unchanged_count"]} readable protected files'
          + (f'; {unverifiable} access-denied files are unverifiable from this account' if unverifiable else '') + '; outputs:', args.output, flush=True)

if __name__ == '__main__': main()
