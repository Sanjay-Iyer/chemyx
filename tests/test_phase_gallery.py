"""Checks for visible, numerical, same-FID gallery and real checkpoint workflow."""
from pathlib import Path
import importlib.util
import json
import shutil
import numpy as np
import pytest
from chemyx_lab.analysis import phase_gallery as gallery
from chemyx_lab.analysis import nmr_validation as v
from chemyx_lab.analysis.phase_audit import sha256,write_json

ROOT=v.DEFAULT_OUTPUT/'phase_validation'


def test_all_27_outputs_same_raw_arrays_parameters_and_metrics():
    records=gallery.json_read(ROOT/'PHASE_VALIDATION_MANIFEST.json')
    assert len(records)==27 and len({a['raw_sha256'] for a in records})==27
    for a in records:
        folder=ROOT/a['acquisition_id'];meta=gallery.json_read(folder/'00_metadata.json')
        assert sha256(folder/'raw_source.dx')==a['raw_sha256']
        assert meta['timestamp_source']=='LONG DATE header'
        rows={r['method']:r for r in v.read_rows(folder/'phase_method_results.csv')}
        reference=None
        parameters={r['method']:r for r in v.read_rows(folder/'phase_parameters.csv')}
        for method in gallery.METHODS:
            array=np.loadtxt(folder/(gallery.STEMS[method]+'.csv'),delimiter=',',skiprows=1)
            with np.load(folder/'analysis'/method/'spectral_evidence.npz') as saved:
                np.testing.assert_array_equal(array[:,0],saved['ppm'])
                np.testing.assert_array_equal(array[:,1]+1j*array[:,2],saved['phased'])
                np.testing.assert_array_equal(array[:,4],saved['baseline'])
                np.testing.assert_array_equal(array[:,5],saved['quantitative_real'])
                if reference is None:reference=saved['unphased'].copy()
                else:np.testing.assert_array_equal(reference,saved['unphased'])
            target=gallery.json_read(folder/'analysis'/method/'target_result.json')
            for table_key,result_key in [('peak_area','area'),('snr','snr'),('peak_height','height')]:
                assert float(rows[method][table_key])==target[result_key]
            assert v.truth(rows[method]['detected'])==target['detected']
            assert rows[method]['raw_sha256']==a['raw_sha256']
            phase=gallery.json_read(folder/'analysis'/method/'phase_parameters.json')
            assert float(parameters[method]['p0_deg'])==phase['p0_deg']
            assert float(parameters[method]['p1_deg'])==phase['p1_deg']
        old=gallery.json_read(v.DEFAULT_OUTPUT/'acquisitions'/a['acquisition_id']/'automated/phase_parameters.json')
        assert float(parameters['production']['p0_deg'])==old['p0_deg']
        assert float(parameters['production']['p1_deg'])==old['p1_deg']
        for name in [*(s+'.png' for s in gallery.STEMS.values()),'06_phase_comparison_full.png',
            '07_phase_comparison_overlay.png','08_target_peak_phase_comparison.png','09_complex_phase_diagnostic.png']:
            assert (folder/name).is_file()
        assert (folder/'manual_review/checkpoints').is_dir()


def test_every_gallery_figure_records_visible_authoritative_dataset():
    rows=v.read_rows(ROOT/'FIGURE_MANIFEST.csv')
    assert len(rows)==227  # 27 * 8 numbered figures + 10 montage paths + June9
    for row in rows:
        assert Path(row['path']).is_file()
        assert row['visible_title'].startswith(row['dataset_display_name']+' ')
        assert row['dataset_display_name']!='Unspecified dataset'


@pytest.fixture
def june9_review_fixture(tmp_path):
    records=[a for a in gallery.json_read(ROOT/'PHASE_VALIDATION_MANIFEST.json') if a['acquisition_date']=='2026-06-09']
    test_gallery=tmp_path/'phase_validation';test_gallery.mkdir()
    for record in records:
        original=ROOT/record['acquisition_id'];folder=test_gallery/record['acquisition_id']
        (folder/'automated').mkdir(parents=True)
        (folder/'manual_review/checkpoints').mkdir(parents=True)
        for name in ('00_metadata.json','phase_method_results.csv'):
            shutil.copyfile(original/name,folder/name)
        for name in ('spectral_evidence.npz','processing_metadata.json'):
            shutil.copyfile(original/'automated'/name,folder/'automated'/name)
        record['gallery_folder']=str(folder)
    write_json(test_gallery/'PHASE_VALIDATION_MANIFEST.json',records)
    return test_gallery/'20260609_110823_d14e2a0c'


def test_explicit_manual_checkpoint_replays_changes_and_preserves_automatic(june9_review_fixture):
    folder=june9_review_fixture
    before={str(p):sha256(p) for p in (folder/'automated').rglob('*') if p.is_file()}
    rows=v.read_rows(folder/'phase_method_results.csv');starting=gallery.json_read(folder/'manual_review/starting_phase.json') if (folder/'manual_review/starting_phase.json').exists() else gallery.json_read(folder/'00_metadata.json')['production_phase']
    source=gallery.json_read(folder/'automated/processing_metadata.json')['source_path'];raw_hash=sha256(source)
    first=gallery.save_gallery_checkpoint(folder,p0_deg=starting['p0_deg']+5,p1_deg=starting['p1_deg'],
        pivot_ppm=starting['pivot_ppm'],pivot_fraction=0,reviewer='Software fixture only',role='software_test')
    assert first.name=='review_001'
    comparison=v.read_rows(first/'completion_comparison.csv')
    full=next(r for r in comparison if r['scope']=='full_available_date_sequence')
    assert v.truth(full['production_complete']) and not v.truth(full['manual_complete'])
    second=gallery.save_gallery_checkpoint(folder,p0_deg=starting['p0_deg'],p1_deg=starting['p1_deg'],
        pivot_ppm=starting['pivot_ppm'],pivot_fraction=0,reviewer='Software fixture only',role='software_test')
    assert second.name=='review_002'
    assert before=={str(p):sha256(p) for p in (folder/'automated').rglob('*') if p.is_file()}
    assert raw_hash==sha256(source)
    after=v.read_rows(folder/'phase_method_results.csv')
    assert [{k:r[k] for k in rows[0]} for r in after[:4]]==rows
    assert gallery.manual_status(folder)[0]=='NOT REVIEWED'
    for output in (first,second):
        for name in ('manual_phase.png','manual_phase.csv','production_vs_manual_overlay.png',
            'production_vs_manual_peak_metrics.csv','checkpoint.json','completion_comparison.csv'):
            assert (output/name).is_file()
    identity=gallery.json_read(second/'target_result.json')
    assert identity['area']==float(rows[1]['peak_area'])


def test_gui_selector_resolves_all_27_and_four_critical_fids():
    from PySide6 import QtWidgets
    import sys
    script_dir=str(v.ROOT/'scripts/nmr')
    if script_dir not in sys.path:sys.path.insert(0,script_dir)
    import validation_phase_gui as gui
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    folder=ROOT/'20260609_110823_d14e2a0c'
    window=gui.ValidationPhaseWindow(folder,validation_dir=v.DEFAULT_OUTPUT)
    try:
        assert window.acquisition_selector.count()==27
        assert window._current_values()[:2]==(59.5,-79.0)
        np.testing.assert_array_equal(window.model.phased(*window._current_values()),window.automated_trace[1])
        for i in range(window.acquisition_selector.count()):
            window.acquisition_selector.setCurrentIndex(i)
            row=window.records[i]
            assert sha256(window.model.path)==row['raw_sha256']
            np.testing.assert_array_equal(window.model.phased(*window._current_values()),window.automated_trace[1])
        selected=next(i for i,r in enumerate(window.records) if r['acquisition_id']=='20260609_110823_d14e2a0c')
        window.acquisition_selector.setCurrentIndex(selected)
        window.critical_only.setChecked(True)
        assert window.acquisition_selector.count()==4
        window.acquisition_selector.setCurrentIndex(2)
        assert window.acquisition_dir.name=='20260609_112434_5122d5c5'
        meta=gallery.json_read(window.acquisition_dir/'00_metadata.json')
        assert sha256(window.model.path)==meta['raw_sha256']
        np.testing.assert_array_equal(window.model.phased(*window._current_values()),window.automated_trace[1])
        window.method_overlay.setChecked(True)
        assert window.acme_curve.isVisible() and window.peak_minima_curve.isVisible()
    finally:window.close();app.processEvents()


def test_checkpoint_output_cannot_overwrite_automatic(june9_review_fixture):
    folder=june9_review_fixture
    phase=gallery.json_read(folder/'00_metadata.json')['production_phase']
    with pytest.raises(ValueError,match='inside'):
        v.save_manual_checkpoint(folder,p0_deg=phase['p0_deg'],p1_deg=phase['p1_deg'],
            checkpoint_root=folder.parent/'outside')
    with pytest.raises(ValueError,match='automatic'):
        v.save_manual_checkpoint(folder,p0_deg=phase['p0_deg'],p1_deg=phase['p1_deg'],
            checkpoint_root=folder/'automated')
