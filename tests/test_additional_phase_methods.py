"""Mathematical, provenance, isolation and GUI checks for added comparators."""
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from chemyx_lab.analysis import phase_quality as q
from chemyx_lab.analysis import phase_optimizers as o
from chemyx_lab.analysis import additional_phase_methods as a
from chemyx_lab.analysis import phase_gallery as g
from chemyx_lab.analysis import nmr_validation as v
from chemyx_lab.analysis.phase_audit import sha256


def synthetic():
    x=np.linspace(0,12,4096)
    z=np.zeros(len(x),complex)
    for center,height,width in ((2.,1.,.025),(7.,.7,.035)):
        distance=(x-center)/width
        z+=height/(1-1j*distance)
    return x,z


def test_quality_components_respect_absorption_dispersion_and_polarity():
    x,z=synthetic();ctx=q.prepare_context(x,z)
    normal=q.score_spectrum(z,ctx);inverted=q.score_spectrum(-z,ctx)
    wrong=q.score_spectrum(z*np.exp(1j*np.deg2rad(70)),ctx)
    assert normal['negative_area_fraction']<.02
    assert inverted['negative_area_fraction']>.8
    assert normal['imaginary_residual']>.2  # odd physical dispersion does not vanish
    assert normal['even_imaginary_fraction']<wrong['even_imaginary_fraction']
    assert normal['peak_asymmetry']<wrong['peak_asymmetry']
    for key in ('negative_area_fraction','even_imaginary_fraction','peak_asymmetry','baseline_penalty'):
        assert 0<=normal[key]<=1


def test_quality_normalization_is_amplitude_invariant_and_windows_fixed():
    x,z=synthetic();ctx=q.prepare_context(x,z);scaled=q.prepare_context(x,20*z)
    assert ctx.metadata['windows']==scaled.metadata['windows']
    s=q.score_spectrum(z,ctx);t=q.score_spectrum(20*z,scaled)
    for key in ('negative_area_fraction','imaginary_residual','even_imaginary_fraction','peak_asymmetry','baseline_penalty'):
        assert s[key]==pytest.approx(t[key],rel=1e-10,abs=1e-12)
    indices=ctx.indices.copy();q.score_spectrum(1j*z,ctx)
    np.testing.assert_array_equal(indices,ctx.indices)


def test_custom_objective_weights_and_deterministic_optimization():
    x,z=synthetic();k=np.arange(len(x))/len(x);observed=z*np.exp(1j*np.deg2rad(45-50*k))
    ctx=q.prepare_context(x,observed)
    cfg={'local_starts':2,'grid_p0_step_deg':90,'grid_p1_step_deg':180}
    first=o.optimize_phase(ctx,'combined_objective_v1',(-45,50),cfg)
    second=o.optimize_phase(ctx,'combined_objective_v1',(-45,50),cfg)
    assert first==second and first['success']
    assert first['objective_value']==pytest.approx(sum(first['objective_components'][key]*o.COMBINED_WEIGHTS[key] for key in o.COMBINED_WEIGHTS))
    assert first['objective_value']<o.objective((0,0),ctx,'combined_objective_v1')
    with pytest.raises(ValueError):o.objective((0,0),ctx,'combined_objective_v1',{'negative':1})
    with pytest.raises(ValueError):o.optimize_phase(ctx,'apsl')


def test_failure_and_missing_signal_are_explicit_not_fabricated():
    x,z=synthetic();ctx=q.prepare_context(x,z)
    failed=o.optimize_phase(ctx,'symmetry_objective',config={'maxiter':1,'maxfev':10,'local_starts':1})
    assert not failed['success'] and failed['status']=='nonconverged'
    ctx.windows=[]
    absent=o.optimize_phase(ctx,'symmetry_objective')
    assert not absent['success'] and absent['p0_deg'] is None
    with pytest.raises(ValueError):q.prepare_context(x,np.zeros_like(z))
    assert not o.ernst_integral_p0(x,np.zeros_like(z))['success']


def test_phase_application_uses_original_full_fft_indices():
    import nmrglue as ng
    x,z=synthetic();ctx=q.prepare_context(x,z);p0,p1=-37.2,86.1
    expected=ng.proc_base.ps(z,p0=p0,p1=p1)[ctx.indices]
    np.testing.assert_allclose(q.phase_samples((p0,p1),ctx),expected,rtol=1e-14,atol=1e-14)
    # Reverse/endpoints conversion used by actual COLMAR inference must match
    # exactly the inverse full-FFT convention, including the 1/N endpoint term.
    left,right=94.,-46.;n=len(z);delta=right-left
    common=ng.proc_base.ps(z,p0=right-delta/n,p1=-delta,inv=True)
    reversed_phased=z[::-1]*np.exp(-1j*np.deg2rad(left+delta*np.arange(n)/n))
    np.testing.assert_allclose(common,reversed_phased[::-1],rtol=1e-13,atol=1e-13)


def test_ernst_equation25_recovers_global_error():
    x=np.linspace(-5,5,4097);z=1/(1-1j*x/.1);observed=z*np.exp(1j*np.deg2rad(57))
    phase=o.ernst_integral_p0(x,observed)
    assert phase['success'] and phase['p0_deg']==pytest.approx(-57)
    assert phase['p1_deg']==0


def test_all_added_arrays_metrics_and_protected_existing_methods():
    root=v.DEFAULT_OUTPUT;gallery=root/'phase_validation';records=g.discover_acquisitions(root)
    assert len(records)==27
    checks=v.read_rows(gallery/'ADDITIONAL_METHOD_VERIFICATION.csv')
    assert checks and all(v.truth(r['same_fft']) and v.truth(r['same_axis']) and v.truth(r['downstream_parameters_unchanged']) for r in checks)
    for record in records:
        folder=gallery/record['acquisition_id'];context=a.load_context(folder)
        rows={r['method']:r for r in v.read_rows(folder/'all_phase_methods_results.csv')}
        meta=g.json_read(folder/'additional_phase_methods.json')
        for method in meta['implemented_spectra']:
            with np.load(folder/'analysis'/method/'spectral_evidence.npz') as arrays:
                score=q.score_spectrum(arrays['phased'],context)
                assert rows[method]['raw_sha256']==record['raw_sha256']
                for key in ('negative_area_fraction','imaginary_residual','peak_asymmetry','dispersive_metric'):
                    assert float(rows[method][key])==score[key]
                if method in a.STEMS:
                    csv=np.loadtxt(folder/(a.STEMS[method]+'.csv'),delimiter=',',skiprows=1)
                    np.testing.assert_array_equal(csv[:,0],arrays['ppm'])
                    np.testing.assert_array_equal(csv[:,1]+1j*csv[:,2],arrays['phased'])
        for unavailable in a.UNAVAILABLE:
            assert unavailable not in rows and not (folder/'analysis'/unavailable/'spectral_evidence.npz').exists()
    # Inspect historical hashes without rewriting an existing audit log. Only
    # user-requested GUI documentation and mutable review indexes are exempt.
    log=root/'logs/additional_phase_methods'
    allowed_indexes={'PHASE_VALIDATION_INDEX.md','PHASE_VALIDATION_GALLERY.html',
                     'PHASE_VALIDATION_MANIFEST.json','OUTPUT_FILES.csv','OUTPUT_SUMMARY.json'}
    for name in ('protected_before.csv','raw_copies_before.csv','historical_processed_before.csv'):
        for row in v.read_rows(log/name):
            path=Path(row['path'])
            allowed=(path.parent==gallery and path.name in allowed_indexes) or path==gallery/'MANUAL_PHASE_REVIEW_GUIDE.md'
            assert sha256(path)==row['sha256_before'] or allowed,str(path)


def test_new_figures_carry_authoritative_dataset_titles():
    rows=v.read_rows(v.DEFAULT_OUTPUT/'phase_validation/ADDITIONAL_FIGURE_MANIFEST.csv')
    assert len(rows)>=27*8+3
    for row in rows:
        assert Path(row['path']).is_file()
        assert row['visible_title'].startswith(row['dataset_display_name']+' ')
        assert row['dataset_display_name']!='Unspecified dataset'


def test_june9_replay_retains_original_outcomes_and_adds_methods():
    folder=v.DEFAULT_OUTPUT/'phase_validation/june09_completion_review'
    rows={r['method']:r for r in v.read_rows(folder/'june09_all_phase_methods_results.csv')}
    assert rows['production']['completion_timestamp'].startswith('2026-06-09T11:08:23')
    assert not v.truth(rows['acme']['complete'])
    assert rows['peak_minima']['completion_timestamp'].startswith('2026-06-09T11:36:54')
    assert {'combined_objective_v1','symmetry_objective','ernst_integral_p0'}<=set(rows)
    assert all(not v.truth(r['physical_historical_stop']) for r in rows.values())
    times=v.read_rows(folder/'all_methods_sequence_timing.csv')
    assert len(times)==8 and all(r['timestamp_source']=='LONG DATE header' for r in times)


def test_gui_all_method_selection_preserves_exact_production_start():
    import sys
    from PySide6 import QtWidgets
    sys.path.insert(0,str(v.ROOT/'scripts/nmr'))
    import validation_phase_gui as gui
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    folder=v.DEFAULT_OUTPUT/'phase_validation/20260609_110823_d14e2a0c'
    window=gui.ValidationPhaseWindow(folder)
    try:
        np.testing.assert_array_equal(window.model.phased(*window._current_values()),window.automated_trace[1])
        for name in ('production','acme','peak_minima','combined_objective_v1','symmetry_objective','ernst_integral_p0','deep_phaser'):
            index=window.phase_method_selector.findData(name);assert index>=0
            window.phase_method_selector.setCurrentIndex(index);window.selected_method_overlay.setChecked(True)
            assert window.extra_method_curves[name].isVisible()
            assert sum(c.isVisible() for c in window.extra_method_curves.values())==1
        window.phase_method_selector.setCurrentIndex(0)
        assert all(window.extra_method_curves[name].isVisible() for name in window.available_overlay_methods)
    finally:window.close();app.processEvents()


def test_expanded_manual_hook_requires_explicit_human_checkpoint(tmp_path):
    folder=tmp_path/'acquisition';folder.mkdir();output=tmp_path/'review_001';output.mkdir()
    (folder/'all_phase_methods_results.csv').write_text('method\nproduction\n')
    # A software fixture is not accepted as a human result. Check before any
    # raw/automatic data is loaded or table is modified.
    (output/'checkpoint.json').write_text(json.dumps({'analysis_role':'software_test','explicit_gui_save':False}))
    # The checkpoint function itself is covered by existing isolation tests;
    # here use a fixture with real copied metadata to exercise expanded guards.
    source=v.DEFAULT_OUTPUT/'phase_validation/20260609_110823_d14e2a0c'
    import shutil
    shutil.copyfile(source/'00_metadata.json',folder/'00_metadata.json')
    shutil.copytree(source/'automated',folder/'automated')
    shutil.copytree(source/'analysis/production',folder/'analysis/production')
    before=(folder/'all_phase_methods_results.csv').read_bytes()
    a.refresh_manual_method_comparison(folder,output,None)
    assert (folder/'all_phase_methods_results.csv').read_bytes()==before


def test_explicit_checkpoint_comparison_isolated_from_real_human_reviews(tmp_path):
    # Exercise the accepted save path only inside a clearly labelled temporary
    # software fixture. No checkpoint is created in the real validation gallery.
    import shutil
    source=v.DEFAULT_OUTPUT/'phase_validation/20260609_110823_d14e2a0c'
    folder=tmp_path/'SOFTWARE_FIXTURE_ONLY';folder.mkdir()
    output=folder/'manual/review_fixture';output.mkdir(parents=True)
    for name in ('00_metadata.json','all_phase_methods_results.csv'):
        shutil.copyfile(source/name,folder/name)
    shutil.copytree(source/'automated',folder/'automated')
    shutil.copytree(source/'analysis/production',folder/'analysis/production')
    meta=g.json_read(folder/'automated/processing_metadata.json')
    prod=v.analyze(meta['source_path'],v.production_args(meta['source_path'],meta['parameters']))
    manual=v.analyze(meta['source_path'],prod['args'],phase=(64.5,-79.,True,'manual'),unphased=prod['unphased'])
    (output/'checkpoint.json').write_text(json.dumps({'analysis_role':'manual_reference','explicit_gui_save':True,'test_fixture':True,'reviewer':'SOFTWARE FIXTURE ONLY'}))
    (output/'completion_comparison.csv').write_text('scope,manual_complete,manual_classification,boolean_changed\nthrough_this_acquisition,True,complete,False\n')
    protected=folder/'analysis/production/spectral_evidence.npz';before=sha256(protected)
    a.refresh_manual_method_comparison(folder,output,manual)
    rows=v.read_rows(folder/'all_phase_methods_results.csv')
    assert len(rows)==9 and sum(r['method'].startswith('manual_') for r in rows)==1
    comparisons=v.read_rows(output/'all_automatic_vs_manual_metrics.csv')
    assert len(comparisons)==8 and {r['automatic_method'] for r in comparisons}==set((*g.METHODS,*a.NEW_METHODS))
    assert (output/'all_automatic_vs_manual_overlay.png').is_file()
    assert sha256(protected)==before
    assert sum(len(g.manual_status(v.DEFAULT_OUTPUT/'phase_validation'/r['acquisition_id'])[1]) for r in g.discover_acquisitions(v.DEFAULT_OUTPUT))==0


def test_actual_deep_backends_agree_and_models_are_pinned():
    logs=v.DEFAULT_OUTPUT/'logs/additional_phase_methods'
    comparison=g.json_read(logs/'deep_backend_comparison.json')
    assert comparison['same_phase'] and comparison['same_prediction_count']
    assert comparison['maximum_probability_absolute_difference']<1e-4
    folder=v.DEFAULT_OUTPUT/'phase_validation/20260609_110823_d14e2a0c'
    status=g.json_read(folder/'analysis/deep_phaser/optimizer_result.json')
    assert status['success'] and status['raw_sha256']==g.json_read(folder/'00_metadata.json')['raw_sha256']
    assert status['source_hashes'] and all(sha256(Path(path))==digest for path,digest in status['source_hashes'].items())


def test_missing_deep_runtime_records_unavailable_without_spectrum(tmp_path,monkeypatch):
    monkeypatch.setattr(a,'NODE',tmp_path/'absent_node.exe')
    status=a.run_deep(tmp_path,{}, {'raw_sha256':'fixture'})
    assert not status['success'] and status['status']=='unavailable_runtime_or_model'
    assert (tmp_path/'analysis/deep_phaser/optimizer_result.json').is_file()
    assert not (tmp_path/'analysis/deep_phaser/spectral_evidence.npz').exists()
