"""Known-truth mathematics, paired inputs, review policy and frozen evidence."""
import csv
import json
from copy import deepcopy
from pathlib import Path
import hashlib
import numpy as np
import nmrglue as ng
import pytest
from chemyx_lab.analysis import synthetic_phase_benchmark as b
from chemyx_lab.analysis import phase_review_priority as p
from chemyx_lab.analysis import phase_gallery as g
from chemyx_lab.analysis import nmr_validation as v
from chemyx_lab.analysis.nmr import read_jcamp_fid, fourier_transform_fid
from chemyx_lab.analysis.phase_audit import sha256, save_figure
from chemyx_lab.analysis.plot_titles import format_dataset_plot_title


@pytest.fixture
def settings():
    return dict(acquired_points=8192, processed_points=65536, spectral_width_hz=1250.,
                observe_frequency_mhz=60.5508184, center_ppm=5., line_broadening_hz=.03,
                dominant_height=80676.76, target_height=100.33)


@pytest.mark.parametrize('condition', b.CONDITIONS)
def test_deterministic_physical_complex_fid(condition, settings):
    clean, raw, meta = b.generate_fid(condition, 20261002, settings)
    other_clean, other_raw, other_meta = b.generate_fid(condition, 20261002, settings)
    np.testing.assert_array_equal(clean, other_clean)
    np.testing.assert_array_equal(raw, other_raw)
    assert meta == other_meta
    assert raw.shape == (8192,) and np.iscomplexobj(raw)
    assert np.any(raw.imag) and np.isfinite(raw).all()
    assert not np.array_equal(raw, b.generate_fid(condition, 20261003, settings)[1])
    # Independently reconstruct the clean time-domain resonances, not FFT samples.
    t=np.arange(8192)/settings['spectral_width_hz']
    expected=sum(r['fid_amplitude']*np.exp(-np.pi*r['linewidth_hz']*t)*
                 np.exp(2j*np.pi*(r['ppm']-settings['center_ppm'])*settings['observe_frequency_mhz']*t)
                 for r in meta['resonances'])
    expected[0]*=.5
    np.testing.assert_allclose(clean, expected, atol=1e-12, rtol=1e-12)


@pytest.mark.parametrize('p0,p1', [(-120,-160),(-60,79),(0,0),(120,160),(179,-79),(-179,20)])
def test_exact_phase_injection_and_processed_fid_roundtrip(settings,p0,p1):
    raw=b.generate_fid('moderate_noise',20261002,settings)[1]
    reference=np.fft.fftshift(np.fft.fft(raw,65536))
    expected=reference*np.exp(1j*np.deg2rad(p0+p1*np.arange(65536)/65536))
    observed=ng.proc_base.ps(reference,p0=p0,p1=p1)
    np.testing.assert_allclose(observed,expected,rtol=1e-13,atol=1e-8)
    distorted_fid=np.fft.ifft(np.fft.ifftshift(observed))
    np.testing.assert_allclose(fourier_transform_fid(distorted_fid),observed,rtol=1e-12,atol=1e-8)
    recovered=ng.proc_base.ps(observed,p0=-p0,p1=-p1)
    np.testing.assert_allclose(recovered,reference,rtol=1e-12,atol=1e-8)
    assert b.phase_errors((-p0,-p1),(-p0,-p1),65536)['phase_ramp_rms_deg']==0


def test_equivalent_conventions_pivots_and_wrap_edges():
    assert b.direct_phase(59.5,-79,True)==(-59.5,79)
    assert b.direct_phase(25,40,False,.5)==(5,40)
    assert b.direct_phase(-25,-40,True,.5)==(5,40)
    for recovered in ((365,40),(5,40+360*1024)):
        errors=b.phase_errors(recovered,(5,40),1024)
        assert errors['phase_ramp_rms_deg']<1e-7
        assert errors['p0_error_deg']==0 and errors['p1_error_deg']==0
    assert b.phase_errors((5,400),(5,40),1024)['phase_ramp_rms_deg']>100
    assert b.phase_errors((-179,0),(179,0),1024)['phase_ramp_rms_deg']==2


def test_objective_exception_is_explicit_and_other_methods_continue(monkeypatch):
    from scipy import optimize
    from chemyx_lab.analysis import phase_quality as q
    cases=v.read_rows(b.BENCH/'synthetic_cases.csv');case=cases[0]
    with np.load(b.BENCH/'cases'/case['case_id']/'synthetic_arrays.npz') as z:
        observed=z['observed_fft'];ppm=z['ppm']
    def fmin(objective,*args,**kwargs):
        if objective.__name__=='_ps_peak_minima_score':
            raise ValueError('zero-size array to reduction operation minimum which has no identity')
        return np.array([0.,0.]),0.,1,3,0
    monkeypatch.setattr(optimize,'fmin',fmin)
    monkeypatch.setattr(b.opt,'optimize_phase',lambda context,method,start:dict(method=method,success=True))
    statuses=b.estimate_classical(case,observed,q.prepare_context(ppm,observed))
    assert statuses['acme']['success'] and statuses['combined_objective_v1']['success']
    failed=statuses['peak_minima']
    assert not failed['success'] and failed['status']=='optimizer_exception'
    assert failed['p0_deg'] is None and failed['p1_deg'] is None
    assert 'zero-size array' in failed['message']


def test_zero_phase_preserves_array_and_jcamp_export(tmp_path,settings):
    raw=b.generate_fid('moderate_noise',12,settings)[1]
    source=tmp_path/'synthetic.dx';b.write_jcamp(source,raw,settings)
    text=source.read_text();assert 'NOT INSTRUMENT DATA' in text and '##DATA TYPE=NMR FID' in text
    np.testing.assert_allclose(read_jcamp_fid(source).complex_points,raw,atol=7.1e-10,rtol=0)
    fft=np.fft.fftshift(np.fft.fft(raw,65536))
    np.testing.assert_array_equal(ng.proc_base.ps(fft,p0=0,p1=0),fft)


@pytest.mark.parametrize('values,expected', [([True]*7,(7,0,True)),
    ([True]*4+[False]*3,(4,3,True)),([False]*7,(7,0,False)),([True,False],(1,1,None)),([], (0,0,None))])
def test_consensus_counts_and_ties(values,expected):
    result=p.consensus(values)
    assert (result['agreement_count'],result['minority_count'],result['majority'])==expected


def test_priority_policy_order_tiers_and_explicit_components():
    rows=[]
    for i in range(8):
        rows.append(dict(acquisition_id=f'id{i}',timestamp=f'2026-06-09 10:0{i}:00',
            phase_ramp_disagreement_deg=float(i),area_spread_percent=float(i),snr_spread_percent=float(i),
            quality_disagreement=float(i),decision_disagreement_fraction=0.,
            detection_disagreement_count=0,completion_disagreement_count=0,
            largest_phase_disagreement_methods='a vs b'))
    ranked,design=p.rank_disagreements(deepcopy(rows))
    assert [r['acquisition_id'] for r in ranked]==[f'id{i}' for i in range(7,-1,-1)]
    for r in ranked:
        assert r['manual_review_priority_score']==pytest.approx(25*(r['phase_disagreement_score']+
            r['quantification_disagreement_score']+r['quality_disagreement_score']+r['decision_disagreement_score']))
    assert design['tier_A_score_threshold_observed_q75']==np.quantile([r['manual_review_priority_score'] for r in ranked],.75)
    assert ranked[0]['priority_tier']=='A' and ranked[-1]['priority_tier']=='C'
    rows[0].update(detection_disagreement_count=1,detection_consensus='6/7 detected')
    override,_=p.rank_disagreements(deepcopy(rows))
    assert next(r for r in override if r['acquisition_id']=='id0')['priority_tier']=='A'
    np.testing.assert_array_equal(p.empirical_scale([0,0,0]),[0,0,0])
    np.testing.assert_allclose(p.empirical_scale([0,1,1,2]),[0,.5,.5,1])


def test_new_plot_path_identifies_dataset_in_all_formats(tmp_path):
    import matplotlib;matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots();ax.plot([1,2],[1,2]);manifest=[]
    title=save_figure(fig,tmp_path/'figure',' SYN_001 test ','SYN_001 test Phase recovery',manifest,formats=('png','svg'))
    assert title=='SYN_001 test Phase recovery'
    assert len(manifest)==2
    for item in manifest:
        assert item['dataset_display_name']=='SYN_001 test' and item['visible_title']==title
        assert Path(item['path']).exists()
    assert title in (tmp_path/'figure.svg').read_text()


def test_saved_case_design_inputs_reproducibility_and_oracle_downstream():
    cases=v.read_rows(b.BENCH/'synthetic_cases.csv');assert len(cases)==234
    assert len({r['raw_sha256'] for r in cases})==40
    assert set(r['condition'] for r in cases)==set(b.CONDITIONS)
    case=g.json_read(b.BENCH/'cases'/cases[0]['case_id']/'metadata.json')
    clean,raw,meta=b.generate_fid(case['condition'],case['seed'],case['physical_metadata']['settings'])
    with np.load(b.BENCH/'cases'/case['case_id']/'synthetic_arrays.npz') as z:
        np.testing.assert_allclose(raw,z['raw_complex_fid'],atol=7.1e-10,rtol=0)
        observed=z['observed_fft'];correct=z['known_correct_phase_fft']
    assert hashlib.sha256(observed.tobytes()).hexdigest()==case['observed_fft_sha256']
    args=b.production_arguments(case['source_path'])
    oracle=v.analyze(case['source_path'],args,phase=(0,0,False,'oracle'),unphased=correct)
    applied=v.analyze(case['source_path'],args,phase=(case['true_corrective_direct_p0_deg'],case['true_corrective_direct_p1_deg'],False,'oracle'),unphased=observed)
    for key in ('height','area','snr','peak_ppm'):
        # Tiny complex roundoff propagates through iterative baseline fitting.
        assert applied['target'][key]==pytest.approx(oracle['target'][key],rel=1e-7,abs=1e-7)
    assert applied['target']['detected']==oracle['target']['detected']
    status=g.json_read(b.BENCH/'cases'/case['case_id']/'optimizer/peak_minima.json')
    assert status['initial_direct_deg']==[-59.5,79.]
    assert status['configuration']['maxiter']==1000 and status['configuration']['maxfun']==2000
    assert status['peak_width_points']==317  # 0.10 ppm on the retained instrument FFT grid


def test_all_real_consensus_unique_hash_ids_and_pending_reviews():
    gallery=b.BENCH.parent;rows=v.read_rows(gallery/'MANUAL_REVIEW_PRIORITY.csv')
    assert len(rows)==27 and len({r['raw_sha256'] for r in rows})==27
    assert '20260609_100743_03e14950' in {r['acquisition_id'] for r in rows}
    assert {r['manual_review_status'] for r in rows}=={'MANUAL REVIEW PENDING'}
    for r in rows:
        saved=g.json_read(gallery/r['acquisition_id']/'method_consensus.json')
        assert saved['raw_sha256']==r['raw_sha256'] and saved['rank']==int(r['rank'])
        assert not g.manual_status(gallery/r['acquisition_id'])[1]
        assert r['timestamp_source']=='LONG DATE header'
    assert len([r for r in rows if r['special_review_group']=='JUNE09_FIRST'])==4


def test_real_historical_outputs_and_scientific_implementations_frozen():
    rows=v.read_rows(b.BENCH/'logs/real_outputs_before.csv');assert len(rows)>2900
    for r in rows:
        if not v.truth(r['index_update_allowed']):
            assert sha256(r['path'])==r['sha256'],r['path']
    previous=v.DEFAULT_OUTPUT/'logs/additional_phase_methods/source_snapshot'
    for name in ('chemyx_lab/analysis/additional_phase_methods.py','chemyx_lab/analysis/phase_quality.py',
                 'chemyx_lab/analysis/phase_optimizers.py','scripts/nmr/deep_phaser_offline.cjs',
                 'scripts/nmr/deep_phaser_native.py'):
        assert sha256(v.ROOT/name)==sha256(previous/name),name
    previous=v.DEFAULT_OUTPUT/'logs/additional_phase_methods/source_snapshot'
    for name in ('chemyx_lab/analysis/additional_phase_methods.py','chemyx_lab/analysis/phase_quality.py',
                 'chemyx_lab/analysis/phase_optimizers.py','scripts/nmr/deep_phaser_offline.cjs',
                 'scripts/nmr/deep_phaser_native.py'):
        assert sha256(v.ROOT/name)==sha256(previous/name),name


def test_completed_benchmark_paired_inputs_and_correct_metadata_control():
    path=b.BENCH/'synthetic_results.csv'
    if not path.exists():pytest.skip('Full collection follows completion of all inference jobs')
    rows=v.read_rows(path);assert len(rows)==1872
    for case in {r['case_id'] for r in rows}:
        group=[r for r in rows if r['case_id']==case]
        assert len(group)==8 and len({r['observed_fft_sha256'] for r in group})==1
        control=next(r for r in group if r['method']=='metadata_correct_oracle')
        assert float(control['phase_ramp_rms_deg'])<1e-9
        assert float(control['area_absolute_error'])<1e-6
        assert not v.truth(control['detection_error'])
        assert all(v.truth(r['same_downstream_parameters']) for r in group if v.truth(r['success']))
        for row in group:
            if v.truth(row['success']):
                assert v.truth(row['known_physical_target_present'])
                assert v.truth(row['known_target_not_detected'])!=v.truth(row['target_detected'])
                assert v.truth(row['detection_discrepancy_vs_noisefree'])==(
                    v.truth(row['target_detected'])!=v.truth(row['noisefree_target_detected']))


def test_completed_figures_visible_identity_and_formats():
    path=b.BENCH/'FIGURE_MANIFEST.csv'
    if not path.exists():pytest.skip('Figures follow completion of all inference jobs')
    rows=v.read_rows(path);assert len(rows)==54
    for r in rows:
        title=r['visible_title'];identity=r['dataset_display_name']
        assert title.startswith(identity+' ') and title==format_dataset_plot_title(identity,title)
        image=Path(r['path']);assert image.exists()
        if image.suffix=='.svg':assert title in image.read_text(encoding='utf-8')
