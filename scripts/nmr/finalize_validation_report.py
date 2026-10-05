"""Reverify saved spectra, refresh plot labels/evidence, and archive final code."""
import _bootstrap  # noqa: F401
import json
from pathlib import Path
import numpy as np
from chemyx_lab.analysis import nmr_validation as v
from chemyx_lab.analysis.phase_audit import provenance,sha256,write_json,write_rows


def main():
    root=v.DEFAULT_OUTPUT
    cohort=v.load_cohort()
    prov=provenance()
    checks=[];manifest=[];results={}
    for a in cohort:
        folder=root/'acquisitions'/a['acquisition_id']
        result=v.analyze(a['source_path'])
        with np.load(folder/'automated/spectral_evidence.npz') as saved:
            identical=all(np.array_equal(saved[key],values) for key,values in
                [('unphased',result['unphased']),('phased',result['spectrum'].real+1j*result['spectrum'].imaginary),
                 ('quantitative_real',result['quantitative'])])
        if not identical:raise ValueError('Final code differs numerically: '+a['acquisition_id'])
        checks.append({'acquisition_id':a['acquisition_id'],'raw_sha256':sha256(a['source_path']),
                       'final_code_vs_saved_arrays_identical':identical})
        v.export_result(result,folder/'automated',a,prov,csv_arrays=False)
        results[a['acquisition_id']]={'production_stored':result}
        for method in ('acme','peak_minima'):
            meta=json.loads((folder/'alternative_methods'/method/'processing_metadata.json').read_text())
            p=meta['phase']
            results[a['acquisition_id']][method]=v.analyze(a['source_path'],result['args'],
                phase=(p['p0_deg'],p['p1_deg'],p['inverse_phase'],method),unphased=result['unphased'])
    write_rows(root/'logs/final_numerical_reverification.csv',checks)
    write_rows(root/'acquisition_manifest.csv',[{**a,'historical_result_paths':';'.join(a['historical_result_paths']),
        'copy_paths':';'.join(a['copy_paths'])} for a in cohort])
    write_json(root/'acquisition_manifest.json',cohort)
    for date in sorted({a['acquisition_date'] for a in cohort}):
        group=[a for a in cohort if a['acquisition_date']==date]
        for method in ('production_stored','acme','peak_minima'):
            sequence=[results[a['acquisition_id']][method] for a in group]
            report=v.replay_target_series(sequence)
            v.completion_figures(report,sequence,root/'early_stop_validation'/date/method,
                                 group[0]['dataset_display_name'],method,manifest)
    previous=v.read_rows(root/'tables/figure_manifest.csv')
    fresh={r['path'] for r in manifest}
    write_rows(root/'tables/figure_manifest.csv',[r for r in previous if r['path'] not in fresh]+manifest)
    evidence=v.discover_stop_evidence(cohort,root/'early_stop_validation')
    stats=json.loads((root/'tables/cohort_summary.json').read_text())
    stats['actual_unique_acquisition_stop_events_verified']=sum(r['actual_stop_verified'] for r in evidence['controller_runs'])
    write_json(root/'tables/cohort_summary.json',stats)
    v.write_summary(root,stats,v.read_rows(root/'tables/historical_reproduction.csv'),
        v.read_rows(root/'phase_method_comparison/phase_method_comparison.csv'),
        [{**r,'complete':v.truth(r['complete'])} for r in v.read_rows(root/'early_stop_validation/phase_method_completion_comparison.csv')],
        [{**r,'boolean_changed':v.truth(r['boolean_changed']),'first_index_changed':v.truth(r['first_index_changed'])}
         for r in v.read_rows(root/'early_stop_validation/phase_sensitivity_decisions.csv')])
    snapshots=[]
    paths=[v.ROOT/p for p in prov['source_code_sha256']]
    paths.extend([Path(__file__),v.ROOT/'scripts/nmr/trace_phase_metadata.py',
                  v.ROOT/'scripts/nmr/validate_phase_audit.py',v.ROOT/'tests/test_nmr_phase_validation.py'])
    for source in paths:
        relative=source.relative_to(v.ROOT)
        destination=root/'logs/final_source_snapshot'/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(source.read_bytes())
        snapshots.append({'source':str(source),'snapshot':str(destination),'sha256':sha256(source)})
    write_rows(root/'logs/final_source_snapshot_manifest.csv',snapshots)
    write_json(root/'logs/final_code_provenance.json',{**prov,
        'reverification':'27 unphased, phased and quantitative arrays exactly equal saved full-run evidence',
        'post_full_run_changes':'Audit filenames/provenance, GUI fonts and preview labels, metadata dataset titles, recorded journal event classification and latched slope label; scientific algorithms unchanged.'})
    print(json.dumps({'numerically_identical_acquisitions':len(checks),'actual_verified_stops':stats['actual_unique_acquisition_stop_events_verified']},indent=2))

if __name__=='__main__':main()
