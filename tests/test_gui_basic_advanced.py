"""Chemist workflow against isolated copies of all eight real June 9 FIDs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import shutil
from pathlib import Path
import numpy as np
import pytest
from PySide6 import QtWidgets
from chemyx_lab.analysis import nmr_validation as v,phase_gallery as g
from chemyx_lab.analysis.manual_phase_workflow import AUTOMATIC,inverse_start
from chemyx_lab.analysis.phase_audit import write_json,sha256
sys.path.insert(0,str(v.ROOT/'scripts/nmr'))
import validation_phase_gui as gui
ROOT=v.DEFAULT_OUTPUT/'phase_validation'


@pytest.fixture
def june9_workspace(tmp_path):
    records=[r.copy() for r in g.discover_acquisitions() if r['acquisition_date']=='2026-06-09']
    gallery=tmp_path/'phase_validation';gallery.mkdir()
    for record in records:
        source=ROOT/record['acquisition_id'];folder=gallery/record['acquisition_id'];folder.mkdir()
        for name in ('00_metadata.json','phase_method_results.csv','all_phase_methods_results.csv'):
            shutil.copyfile(source/name,folder/name)
        shutil.copytree(source/'automated',folder/'automated')
        for method in AUTOMATIC:shutil.copytree(source/'analysis'/method,folder/'analysis'/method)
        raw=folder/'raw_source.dx';shutil.copyfile(source/'raw_source.dx',raw)
        meta=g.json_read(folder/'automated/processing_metadata.json');meta['source_path']=str(raw)
        write_json(folder/'automated/processing_metadata.json',meta)
        record['gallery_folder']=str(folder)
    write_json(gallery/'PHASE_VALIDATION_MANIFEST.json',records)
    return gallery


@pytest.fixture
def window(june9_workspace):
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w=gui.ValidationPhaseWindow(june9_workspace/'20260609_110823_d14e2a0c',validation_dir=june9_workspace.parent)
    w.show();app.processEvents()
    yield w
    w.close();app.processEvents()


def test_all_june9_basic_advanced_identity_defaults_and_diagnostics(window,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Display action ran an analysis/optimizer')
    monkeypatch.setattr(v,'analyze',forbidden)
    assert window.acquisition_selector.count()==8
    for i in range(8):
        window.acquisition_selector.setCurrentIndex(i)
        assert sha256(window.model.path)==window.records[i]['raw_sha256']
        assert not window.advanced_toggle.isChecked()
        assert window.spectrum_view.currentData()=='real'
        assert not window.pivot.isVisible() and not window.information.isVisible()
        assert all(not bar.isVisible() for bar in window._advanced_bars)
        assert all(spin.isVisible() for spin in window.basic_phase_controls)
        phase=window._current_values();source=window._analysis_view()
        window.advanced_toggle.setChecked(True)
        assert window.pivot.isVisible() and window.information.isVisible()
        assert all(bar.isVisible() for bar in window._advanced_bars)
        for view in (1,2,3,0):
            window.spectrum_view.setCurrentIndex(view)
            assert window._current_values()==phase and window._analysis_view() is source
        window.advanced_toggle.setChecked(False)
        assert window._current_values()==phase


def test_live_basic_phase_undo_reset_and_exact_advanced_start(window):
    start=window._current_values();z=window._manual_complex().copy()
    window.basic_phase_controls[0].setValue(start[0]+2)
    assert window._current_values()[0]==start[0]+2
    assert not np.array_equal(z,window._manual_complex())
    assert window.p0_slider.value()==round((start[0]+2)*10)
    window.basic_phase_controls[1].setValue(start[1]-3)
    assert window.p1_slider.value()==round((start[1]-3)*10)
    window._undo_phase();assert window._current_values()==(start[0]+2,start[1],start[2])
    window._undo_phase();assert window._current_values()==start
    np.testing.assert_array_equal(window._manual_complex(),z)
    window.basic_phase_controls[0].setValue(64)
    window._reset_starting_phase();assert window._current_values()==start
    window.advanced_toggle.setChecked(True)
    fixed={n:a['phased'].copy() for n,a in window.fixed_methods.items()}
    for name in AUTOMATIC:
        window.solo_selector.setCurrentIndex(window.solo_selector.findData(name))
        window._copy_selected_automatic()
        assert window.starting_method==name
        assert window._current_values()[:2]==inverse_start(window.fixed_methods[name]['metrics'],0)
        phase=window._current_values()
        window.advanced_toggle.setChecked(False);window.advanced_toggle.setChecked(True)
        assert window._current_values()==phase  # Basic decimals never round stored phase.
    window._reset_stored();assert window._current_values()==start
    window._show_saved_phase_parameters()
    assert window.parameter_dialog.findChild(QtWidgets.QTableWidget).rowCount()==7
    assert window._current_values()==start
    window.parameter_dialog.close()
    for name in AUTOMATIC:np.testing.assert_array_equal(window.fixed_methods[name]['phased'],fixed[name])


def test_basic_selected_comparison_keeps_live_manual_and_same_analysis(window,monkeypatch):
    monkeypatch.setattr(v,'analyze',lambda *a,**kw:pytest.fail('Comparison recalculated analysis'))
    source=window._analysis_view();phase=window._current_values()
    for name in AUTOMATIC:
        window.basic_comparison.setCurrentIndex(window.basic_comparison.findData(name))
        assert {n for n,c in window.trace_checks.items() if c.isChecked()}=={name,'manual'}
        assert window.real_curves[name].isVisible() and window.real_curves['manual'].isVisible()
        assert window._analysis_view() is source and window._current_values()==phase
    window.processing_view.setCurrentIndex(1)
    assert all(not spin.isEnabled() for spin in window.basic_phase_controls)
    np.testing.assert_array_equal(window.raw_context_curve.getData()[1],source['phased'].real)
    window._view_final_spectrum()
    np.testing.assert_array_equal(window.real_curves['production'].getData()[1],source['final'])
    assert 'DX metadata phase [saved]' in window.analysis_label.text()


def test_checkpoint_final_restore_and_human_reference_comparison(window,monkeypatch):
    protected=[p for p in window.acquisition_dir.rglob('*') if p.is_file() and
               ('analysis' in p.parts or 'automated' in p.parts or p.name in
                ('raw_source.dx','phase_method_results.csv','all_phase_methods_results.csv','phase_quality_metrics.csv'))]
    hashes={p:sha256(p) for p in protected}
    count=sum(len(g.manual_status(Path(r['gallery_folder']))[1]) for r in g.discover_acquisitions())
    monkeypatch.setattr(QtWidgets.QInputDialog,'getText',lambda *a,**kw:('SOFTWARE FIXTURE ONLY',True))
    monkeypatch.setattr(QtWidgets.QInputDialog,'getMultiLineText',lambda *a,**kw:('Isolated Basic/Advanced workflow test, not a human scientific review',True))
    monkeypatch.setattr(QtWidgets.QMessageBox,'critical',lambda *a:pytest.fail(str(a)))
    window.basic_phase_controls[0].setValue(64.5)
    saved_phase=window._current_values()
    window._recalculate_manual()
    cp=window.loaded_checkpoint
    assert cp.is_relative_to(window.validation_dir) and cp.name=='review_001'
    assert window.processing_view.currentData()=='final' and window.spectrum_view.currentData()=='real'
    with np.load(cp/'spectral_evidence.npz') as a:
        np.testing.assert_array_equal(window.real_curves['manual'].getData()[1][window.saved_manual_view['region_indices']],a['regional_quantitative'])
    assert window.restore_latest_button.isEnabled() and window.basic_comparison_button.isEnabled()
    manifest=v.read_rows(cp/'all_methods_manual_figure_manifest.csv')
    assert len(manifest)==1
    assert manifest[0]['visible_title'].startswith(manifest[0]['dataset_display_name']+' ')
    assert Path(manifest[0]['path']).is_file()
    assert 'Saved Automatic Methods and Unphased Reference' in manifest[0]['visible_title']
    manifest=v.read_rows(cp/'all_methods_manual_figure_manifest.csv')
    assert len(manifest)==1
    assert manifest[0]['visible_title'].startswith(manifest[0]['dataset_display_name']+' ')
    assert Path(manifest[0]['path']).is_file()
    assert 'Saved Automatic Methods and Unphased Reference' in manifest[0]['visible_title']
    window.processing_view.setCurrentIndex(0);window.basic_phase_controls[0].setValue(70)
    window._restore_latest_checkpoint()
    assert window._current_values()==saved_phase
    assert window.starting_method=='loaded_checkpoint:review_001'
    window._open_manual_comparison();assert window.comparison_table.rowCount()==8
    assert hashes=={p:sha256(p) for p in protected}
    assert sum(len(g.manual_status(Path(r['gallery_folder']))[1]) for r in g.discover_acquisitions())==count
