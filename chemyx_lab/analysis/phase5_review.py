"""Prepare machine-local raw JCAMP data for the current validation reviewer."""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from . import nmr_validation as v, phase_gallery as g
from . import additional_phase_methods as extra
from .phase_audit import provenance, sha256, write_json, write_rows
from .plot_titles import resolve_dataset_display_name

DEFAULT_REVIEW_ROOT = v.ROOT / 'results/phase5_review'


def prepare_review(source, output_root=DEFAULT_REVIEW_ROOT):
    """Fresh, append-only review of a file or directory; never reuse old processing.

    All phase methods share the production FFT, axis, and downstream processor.
    DEEP is explicitly omitted: its external runtime/models are not portable.
    """
    source = Path(source).resolve()
    sources = sorted(source.rglob('*')) if source.is_dir() else [source]
    sources = [p for p in sources if p.is_file() and p.suffix.lower() == '.dx']
    if not sources:
        raise ValueError(f'No raw JCAMP-DX (.dx) files found: {source}')
    # Validate every timestamp before creating output. Filename/mtime cannot
    # silently become acquisition time, even for a standalone review.
    acquisitions = {}
    for path in sources:
        fid = v.read_jcamp_fid(path)
        stamp, field = v.acquisition_time(fid.metadata)
        digest = sha256(path)
        if digest in acquisitions:
            continue
        args = v.production_args(path)
        if args.phase_method != 'stored' or args.phase0 is not None or args.phase1 is not None:
            raise ValueError('Phase 5 starts from DX metadata phase; set phase_method to stored '
                             'and remove phase0/phase1 overrides in the NMR configuration')
        # Use the configured dataset identity, with authoritative metadata as
        # fallback, through the same title resolver as production processing.
        mod = v.pipeline()
        configured = (mod._statistics_config([]).dataset_display_name or
                      mod._target_peak_config([]).dataset_display_name)
        dataset = resolve_dataset_display_name(
            configured, metadata={'dataset_name': stamp.strftime('%m-%d-%y')},
            input_paths=path)
        acquisitions[digest] = {
            'acquisition_id': stamp.strftime('%Y%m%d_%H%M%S') + '_' + digest[:8],
            'timestamp': stamp.isoformat(), 'timestamp_source': field,
            'acquisition_date': stamp.date().isoformat(), 'raw_sha256': digest,
            'source_path': str(path), 'dataset_display_name': dataset,
            'primary_cohort': False, 'historical_result_paths': [],
        }
    records = sorted(acquisitions.values(), key=lambda a: a['timestamp'])
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='review_', dir=output_root))
    gallery = root / 'phase_validation'
    gallery.mkdir()
    all_results = {}
    contexts = {}
    all_statuses = {}
    try:
        for a in records:
            folder = gallery / a['acquisition_id']
            folder.mkdir()
            raw = folder / 'raw_source.dx'
            shutil.copyfile(a['source_path'], raw)
            if sha256(raw) != a['raw_sha256']:
                raise ValueError('Raw source changed during import')
            a.update(gallery_folder=str(folder), original_source_path=a['source_path'],
                     source_path=str(raw), june09_critical=False)
            prod = v.analyze(raw)
            v.export_result(prod, folder / 'automated', a, provenance(), figures=False, csv_arrays=False)
            results = {'production': prod}
            statuses = {}
            for result, status in v.alternative_phases(prod):
                name = status['method']
                statuses[name] = {'success': result is not None, 'status':
                                  'converged' if status.get('converged') else 'not_converged', **status}
                if result is not None:
                    results[name] = result
            results['unphased'] = v.analyze(raw, prod['args'], phase=(0., 0., False, 'none'),
                                            unphased=prod['unphased'])
            for name, result in results.items():
                g.export_arrays(folder, name, result)
            context = extra.load_context(folder)
            for name in extra.NEW_METHODS:
                status = extra.method_status(folder, name, context, prod, a, deep=False)
                statuses[name] = status
                if status['success']:
                    result = v.analyze(raw, prod['args'], phase=(status['p0_deg'], status['p1_deg'],
                                       status.get('inverse_phase', False), name), unphased=prod['unphased'])
                    results[name] = result
                    g.export_arrays(folder, name, result, stem=extra.STEMS[name])
            write_json(folder / '00_metadata.json', a)
            write_json(folder / 'additional_phase_methods.json', {
                'implemented_spectra': list(results), 'new_method_status': statuses,
                'raw_sha256': a['raw_sha256'], 'provenance': provenance(),
            })
            write_json(folder / 'phase_quality_context.json', context.metadata)
            (folder / 'manual_review/checkpoints').mkdir(parents=True)
            all_results[a['acquisition_id']] = results
            contexts[a['acquisition_id']] = context
            all_statuses[a['acquisition_id']] = statuses
        for date in dict.fromkeys(a['acquisition_date'] for a in records):
            group = [a for a in records if a['acquisition_date'] == date]
            available = set.intersection(*(set(all_results[a['acquisition_id']]) for a in group))
            replays = {name: v.replay_target_series([all_results[a['acquisition_id']][name] for a in group])
                       for name in sorted(available)}
            for index, a in enumerate(group):
                ident = a['acquisition_id']
                results = all_results[ident]
                decisions = {name: report.decision_trace[index] for name, report in replays.items()}
                rows = extra.write_tables(gallery / ident, a, results, contexts[ident], decisions,
                                          all_statuses[ident])
                write_rows(gallery / ident / 'phase_method_results.csv', rows)
        write_json(root / 'acquisition_manifest.json', records)
        write_json(gallery / 'PHASE_VALIDATION_MANIFEST.json', records)
        write_json(root / 'review_provenance.json', {
            **provenance(), 'schema': 'chemyx.phase5-review.v1', 'input': str(source),
            'processing': 'fresh production processing; old processed folders are not imported',
            'completion_scope': 'Selected files only; one file cannot establish sequence completion',
            'deep_phaser': 'not_requested: external models/runtime are not required',
        })
    except Exception as exc:
        write_json(root / 'PREPARATION_FAILED.json', {'error': str(exc)})
        raise
    return root
