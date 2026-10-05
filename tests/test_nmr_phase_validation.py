"""Numerical and historical-safety tests for the NMR audit infrastructure."""
from pathlib import Path
import json
from types import SimpleNamespace
from unittest.mock import patch

import nmrglue as ng
import numpy as np
import pytest

from chemyx_lab.analysis.nmr import asymmetric_least_squares_baseline, pick_spectrum_region
from chemyx_lab.analysis import nmr_validation as validation
from chemyx_lab.analysis.phase_audit import sha256, save_figure

ROOT=Path(__file__).resolve().parents[1]
DX=ROOT/'results/raw/nmr/06-09-26/CEC-PhSi2-flow(sequence-1100)-06-09-26.dx'

@pytest.fixture(scope='module')
def processed():
    return validation.analyze(DX)


def test_production_reproducibility_and_separate_before_after_arrays(processed):
    repeated=validation.analyze(DX)
    np.testing.assert_array_equal(processed['quantitative'],repeated['quantitative'])
    assert processed['peaks']==repeated['peaks']
    assert not np.shares_memory(processed['unphased'],processed['spectrum'].real)
    assert not np.allclose(processed['unphased'].real,processed['spectrum'].real)


def test_shared_downstream_matches_original_production_operations(processed):
    spec,args=processed['spectrum'],processed['args']
    quantitative=spec.real-asymmetric_least_squares_baseline(spec.real)
    picked=pick_spectrum_region(spec.ppm_axis,quantitative,region_min_ppm=args.region_min,
        region_max_ppm=args.region_max,min_prominence_snr=args.min_prominence_snr,
        min_distance_ppm=args.min_peak_distance_ppm,min_width_ppm=args.min_peak_width_ppm,
        baseline_polynomial_order=args.baseline_order,smoothing_window_ppm=args.smoothing_window_ppm,
        quantitative_intensity=quantitative,source=DX)
    np.testing.assert_array_equal(processed['quantitative'],quantitative)
    assert processed['picked'].peaks==picked.peaks


def test_phase_metadata_centered_direct_equivalence(processed):
    row=validation.phase_field_reconciliation(processed['spectrum'].metadata,processed['unphased'])
    assert row['production_phase_fields']=='$PHC0,$PHC1 only'
    assert row['phasecorrection_p0']==-20
    assert row['phasecorrection_p1']==79
    assert row['phc0']==59.5
    assert row['phc1']==-79
    assert row['equivalent_centered_direct_convention']
    assert row['centered_direct_vs_production_max_relative_error']<1e-14


def test_pure_phase_changes_preserve_magnitude(processed):
    rotated=ng.proc_base.ps(processed['unphased'],p0=71,p1=-23,inv=True)
    np.testing.assert_allclose(np.abs(rotated),np.abs(processed['unphased']),rtol=5e-15,atol=1e-9)


def test_cohort_deduplication_uses_hash_not_filename(tmp_path):
    entry=json.loads(validation.DEFAULT_INVENTORY.read_text(encoding='utf-8-sig'))[0]
    path=tmp_path/'inventory.json'
    path.write_text(json.dumps([entry,entry]))
    cohort=validation.load_cohort(path)
    assert len(cohort)==1
    assert cohort[0]['duplicate_inventory_rows']==1


def test_missing_metadata_fails_closed():
    with pytest.raises(ValueError,match='authoritative'):
        validation.acquisition_time({'TITLE':'sequence-1015'})


def test_manual_checkpoint_isolation_provenance_and_comparison(tmp_path,processed):
    cohort=validation.load_cohort()
    acq=next(a for a in cohort if a['source_path'].lower()==str(DX).lower())
    root=tmp_path/acq['acquisition_id']
    validation.export_result(processed,root/'automated',acq,validation.provenance(),figures=False)
    before={str(p):sha256(p) for p in (root/'automated').rglob('*') if p.is_file()}
    raw_before=sha256(DX)
    spec=processed['spectrum']
    first=validation.save_manual_checkpoint(root,p0_deg=spec.phase0_deg+5,p1_deg=spec.phase1_deg,
        reviewer='Software test only',role='test_phase_perturbation')
    second=validation.save_manual_checkpoint(root,p0_deg=spec.phase0_deg,p1_deg=spec.phase1_deg,
        reviewer='Software test only',role='test_phase_identity')
    assert first.name=='review_001' and second.name=='review_002'
    assert raw_before==sha256(DX)
    assert before=={str(p):sha256(p) for p in (root/'automated').rglob('*') if p.is_file()}
    meta=json.loads((first/'checkpoint.json').read_text())
    assert meta['raw_sha256']==raw_before
    assert meta['downstream_parameters_unchanged']
    rows=validation.read_rows(first/'automated_vs_manual.csv')
    area=next(r for r in rows if r['metric']=='area')
    assert abs(float(area['absolute_difference']))>.001
    identity=json.loads((second/'target_result.json').read_text())
    assert identity==processed['target']


def test_manual_phase_calls_same_downstream_processor(processed):
    mod=validation.pipeline()
    with patch.object(mod,'process_spectrum_for_peaks',wraps=mod.process_spectrum_for_peaks) as call:
        altered=validation.analyze(DX,processed['args'],phase=(65,-79,True,'manual'),unphased=processed['unphased'])
    assert call.call_count==1
    assert altered['target']['area']!=processed['target']['area']
    assert processed['args'].phase_method=='stored'


def test_alternative_methods_do_not_overwrite_automated(processed):
    before=processed['spectrum'].real.copy()
    comparators=validation.alternative_phases(processed)
    assert {meta['method'] for _,meta in comparators}=={'acme','peak_minima'}
    assert all(result is not None for result,_ in comparators)
    assert all('convergence_warnflag' in meta and 'optimizer_settings' in meta for _,meta in comparators)
    np.testing.assert_array_equal(before,processed['spectrum'].real)


def test_historical_june9_completion_reconstruction():
    sources=sorted((ROOT/'results/raw/nmr/06-09-26').glob('*.dx'))
    results=[validation.analyze(p) for p in sources]
    results.sort(key=lambda r:validation.acquisition_time(r['spectrum'].metadata)[0])
    report=validation.replay_target_series(results)
    assert report.completion.complete
    assert report.completion.completion_index==5
    assert report.completion.completion_timestamp.startswith('2026-06-09T11:08:23')
    assert report.completion.metrics['recent_slope_per_hour']==pytest.approx(3.347167277,rel=1e-8)


def test_all_figure_formats_share_visible_dataset_identity(tmp_path):
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots();ax.plot([0,1],[2,3])
    manifest=[]
    title=save_figure(fig,tmp_path/'audit',' 06-09-26 ','  Phase   Audit ',manifest)
    assert title=='06-09-26 Phase Audit'
    assert len(manifest)==3
    assert {r['visible_title'] for r in manifest}=={title}
    assert {r['dataset_display_name'].strip() for r in manifest}=={'06-09-26'}
    svg=(tmp_path/'audit.svg').read_text()
    assert title in svg


def test_controller_audit_retains_exact_magnitude_measurement(tmp_path):
    from dataclasses import asdict
    from chemyx_lab.analysis.nmr import analyze_dx_peak, build_magnitude_spectrum
    from chemyx_lab.analysis.phase_audit import retain_monitoring_audit
    analysis={'line_broadening_hz':.3,'detection_window_ppm':.12}
    result=analyze_dx_peak(DX,target_ppm=5.8,window_ppm=.12,
        line_broadening_hz=.3)
    raw_before=sha256(DX)
    meta=retain_monitoring_audit(DX,result,analysis,{},tmp_path/'monitor')
    spectrum=build_magnitude_spectrum(DX,line_broadening_hz=.3)
    with np.load(tmp_path/'monitor/monitoring_evidence.npz') as evidence:
        np.testing.assert_array_equal(evidence['magnitude'],spectrum.magnitude)
        np.testing.assert_array_equal(evidence['magnitude'],np.abs(evidence['unphased']))
    assert meta['peak_result']==asdict(result)
    assert meta['raw_sha256']==raw_before==sha256(DX)


