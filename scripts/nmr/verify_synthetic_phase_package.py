"""Final evidence/inventory checks; never reprocess real or synthetic spectra."""
from pathlib import Path
import json
import hashlib
import numpy as np
import _bootstrap
from chemyx_lab.analysis import synthetic_phase_benchmark as b, phase_gallery as g, nmr_validation as v
from chemyx_lab.analysis.phase_audit import sha256, write_rows, write_json


def main():
    before=v.read_rows(b.BENCH/'logs/real_outputs_before.csv');checks=[]
    for row in before:
        actual=sha256(row['path'])
        checks.append({**row,'after_sha256':actual,'unchanged':actual==row['sha256']})
    write_rows(b.BENCH/'logs/real_outputs_after.csv',checks)
    bad=[r for r in checks if not r['unchanged'] and not v.truth(r['index_update_allowed'])]
    assert not bad,bad
    previous=v.DEFAULT_OUTPUT/'logs/additional_phase_methods/source_snapshot'
    comparator_sources=('chemyx_lab/analysis/additional_phase_methods.py','chemyx_lab/analysis/phase_quality.py',
        'chemyx_lab/analysis/phase_optimizers.py','scripts/nmr/deep_phaser_offline.cjs','scripts/nmr/deep_phaser_native.py')
    for name in comparator_sources:assert sha256(v.ROOT/name)==sha256(previous/name),name
    cases=v.read_rows(b.BENCH/'synthetic_cases.csv');results=v.read_rows(b.BENCH/'synthetic_results.csv')
    assert len(cases)==234 and len(results)==1872
    method_keys=set(b.ESTIMATORS)|{'metadata_correct_oracle','metadata_nominal_anchor'}
    roundtrip=0.;control_area_errors=[];failures=[]
    for row in cases:
        folder=b.BENCH/'cases'/row['case_id'];meta=g.json_read(folder/'metadata.json')
        assert sha256(meta['source_path'])==meta['raw_sha256']
        with np.load(folder/'synthetic_arrays.npz') as arrays:
            observed=arrays['observed_fft']
            assert hashlib.sha256(observed.tobytes()).hexdigest()==meta['observed_fft_sha256']
            roundtrip=max(roundtrip,float(np.max(np.abs(b.fourier_transform_fid(arrays['processed_phase_distorted_fid'])-observed))))
        local=[r for r in results if r['case_id']==row['case_id']]
        assert {r['method'] for r in local}==method_keys and len(local)==8
        assert {r['observed_fft_sha256'] for r in local}=={meta['observed_fft_sha256']}
        control=next(r for r in local if r['method']=='metadata_correct_oracle')
        assert float(control['phase_ramp_rms_deg'])<1e-9
        control_area_errors.append(float(control['area_absolute_error']))
        assert not v.truth(control['detection_error'])
        for r in local:
            if not v.truth(r['success']):failures.append({'case_id':r['case_id'],'method':r['method'],'status':r['status']})
            else:assert v.truth(r['same_downstream_parameters'])
    assert roundtrip<1e-8
    assert max(control_area_errors)<1e-6
    queue=v.read_rows(b.BENCH.parent/'MANUAL_REVIEW_PRIORITY.csv')
    assert len(queue)==27 and len({r['raw_sha256'] for r in queue})==27
    assert all(r['manual_review_status']=='MANUAL REVIEW PENDING' for r in queue)
    assert all(not g.manual_status(b.BENCH.parent/r['acquisition_id'])[1] for r in queue)
    manifest=v.read_rows(b.BENCH/'FIGURE_MANIFEST.csv');assert len(manifest)==54
    for row in manifest:
        assert Path(row['path']).exists() and row['visible_title'].startswith(row['dataset_display_name']+' ')
    # Separate additions manifest; original OUTPUT_FILES.csv stays frozen.
    new_real=[b.BENCH.parent/name for name in ('SYNTHETIC_GROUND_TRUTH_SUMMARY.md','MANUAL_REVIEW_PRIORITY.csv',
        'MANUAL_REVIEW_PRIORITY.md','MANUAL_REVIEW_PRIORITY_DESIGN.json','MANUAL_REVIEW_JUNE09.md','MANUAL_REVIEW_AUGUST.md',
        'documentation/DEEP_PHASER_LOW_FIELD_APPLICABILITY.md','PHASE_VALIDATION_INDEX.md','PHASE_VALIDATION_GALLERY.html')]
    new_real += [b.BENCH.parent/r['acquisition_id']/'method_consensus.json' for r in queue]
    write_rows(b.BENCH.parent/'GROUND_TRUTH_ADDITIONS_MANIFEST.csv',[{'path':str(p.resolve()),'sha256':sha256(p),'bytes':p.stat().st_size} for p in new_real])
    sources=[v.ROOT/name for name in ('chemyx_lab/analysis/synthetic_phase_benchmark.py',
        'chemyx_lab/analysis/phase_review_priority.py','chemyx_lab/analysis/phase_gallery.py',
        'scripts/nmr/prepare_ground_truth_benchmark.py','scripts/nmr/run_synthetic_phase_benchmark.py',
        'scripts/nmr/summarize_synthetic_phase_benchmark.py','scripts/nmr/build_manual_review_priority.py',
        'scripts/nmr/verify_synthetic_phase_package.py','tests/test_synthetic_phase_benchmark.py',
        'scripts/nmr/finalize_additional_phase_report.py')]
    write_json(b.BENCH/'logs/source_hashes.json',{str(p):sha256(p) for p in sources})
    report={'frozen_files_checked':len(checks),'unchanged':sum(r['unchanged'] for r in checks),
        'allowed_index_updates':sum(not r['unchanged'] and v.truth(r['index_update_allowed']) for r in checks),
        'unexpected_changes':len(bad),'synthetic_cases':len(cases),'independent_raw_fid_hashes':len({r['raw_sha256'] for r in cases}),
        'unchanged_comparator_source_snapshot_checks':len(comparator_sources),
        'method_results':len(results),'execution_optimizer_failures':failures,'fft_roundtrip_max_abs_error':roundtrip,
        'correct_metadata_control_max_area_abs_difference':max(control_area_errors),
        'correct_metadata_control_detection_disagreements':0,'real_reviews_pending':len(queue),'manual_checkpoints':0,
        'figure_files':len(manifest),'new_real_artifact_count':len(new_real)}
    write_json(b.BENCH/'logs/FINAL_INTEGRITY.json',report)
    # The verifier's own redirected log is still being written. Exclude it
    # and the self-referential manifest, while including the completed report.
    additions=[p for p in b.BENCH.rglob('*') if p.is_file() and p.name not in ('OUTPUT_FILES.csv','final_verification.log')]
    write_rows(b.BENCH/'OUTPUT_FILES.csv',[{'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':sha256(p)} for p in sorted(additions)])
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
