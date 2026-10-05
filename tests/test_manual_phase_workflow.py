"""Temporary offline workflow checks; never create a real human checkpoint."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import shutil
from pathlib import Path
import numpy as np
import pytest
from PySide6 import QtWidgets
from chemyx_lab.analysis import nmr_validation as v, phase_gallery as g
from chemyx_lab.analysis.manual_phase_workflow import AUTOMATIC,inverse_start
from chemyx_lab.analysis.phase_audit import sha256,write_json

sys.path.insert(0,str(v.ROOT/'scripts/nmr'))
import validation_phase_gui as gui
ROOT=v.DEFAULT_OUTPUT/'phase_validation'


def test_all_acquisitions_fixed_complex_overlays_exact_starts_live_manual(monkeypatch):
    from chemyx_lab.analysis import phase_optimizers,additional_phase_methods
    def forbidden(*a,**kw):raise AssertionError('Automatic optimizer called by GUI')
    monkeypatch.setattr(phase_optimizers,'optimize_phase',forbidden)
    monkeypatch.setattr(additional_phase_methods,'run_deep',forbidden)
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window=gui.ValidationPhaseWindow(ROOT/'20260609_110823_d14e2a0c')
    try:
        window.show();app.processEvents()
        assert window.spectrum_view.currentData()=='real' and not window.imaginary_plot.isVisible()
        window.spectrum_view.setCurrentIndex(2)
        window._set_view((5.6,6.0));app.processEvents()
        for plot in (window.overlay_plot,window.imaginary_plot):
            lo,hi=plot.viewRange()[0]
            assert (lo+hi)/2==pytest.approx(5.8,abs=.005)
            assert lo<=5.6 and hi>=6.0
        assert window.acquisition_selector.count()==27
        for i in range(27):
            window.acquisition_selector.setCurrentIndex(i)
            assert len(window.fixed_methods)==7
            assert sha256(window.model.path)==window.records[i]['raw_sha256']
            for name in AUTOMATIC:
                with np.load(window.acquisition_dir/'analysis'/name/'spectral_evidence.npz') as a:
                    np.testing.assert_array_equal(window.fixed_methods[name]['phased'],a['phased'])
        first=next(i for i,r in enumerate(window.records) if r['acquisition_id']=='20260609_110823_d14e2a0c')
        window.acquisition_selector.setCurrentIndex(first)
        for index,name in enumerate(AUTOMATIC):
            window.start_selector.setCurrentIndex(index);window._start_from_selected()
            assert window._current_values()[:2]==inverse_start(window.fixed_methods[name]['metrics'],0)
            assert '[saved]' in window.trace_checks[name].text()
            np.testing.assert_allclose(window._manual_complex(),window.fixed_methods[name]['phased'],rtol=1e-11,atol=1e-7)
        window.phase_controls[0].setValue(window.phase_controls[0].value()+10)
        window.start_selector.activated.emit(window.start_selector.currentIndex())
        assert window._current_values()[:2]==inverse_start(window.fixed_methods[AUTOMATIC[-1]]['metrics'],0)
        fixed={name:values['phased'].copy() for name,values in window.fixed_methods.items()}
        for check in window.trace_checks.values():check.setChecked(True)
        old=window._manual_complex().copy()
        window.phase_controls[0].setValue(window.phase_controls[0].value()+3)
        window.phase_controls[1].setValue(window.phase_controls[1].value()-5)
        window.pivot.setValue(5.8)
        assert not np.array_equal(old,window._manual_complex())
        for name in AUTOMATIC:
            np.testing.assert_array_equal(window.real_curves[name].getData()[1],fixed[name].real)
            np.testing.assert_array_equal(window.imag_curves[name].getData()[1],fixed[name].imag)
        np.testing.assert_array_equal(window.imag_curves['manual'].getData()[1],window._manual_complex().imag)
        window.critical_only.setChecked(True);assert window.acquisition_selector.count()==4
    finally:window.close();app.processEvents()


@pytest.fixture
def review_folder(tmp_path):
    records=[r.copy() for r in g.discover_acquisitions() if r['acquisition_date']=='2026-06-09']
    gallery=tmp_path/'phase_validation';gallery.mkdir()
    for r in records:
        source=ROOT/r['acquisition_id'];folder=gallery/r['acquisition_id'];folder.mkdir()
        for name in ('00_metadata.json','phase_method_results.csv','all_phase_methods_results.csv'):
            shutil.copyfile(source/name,folder/name)
        shutil.copytree(source/'automated',folder/'automated')
        for method in AUTOMATIC:
            destination=folder/'analysis'/method;destination.mkdir(parents=True)
            shutil.copyfile(source/'analysis'/method/'spectral_evidence.npz',destination/'spectral_evidence.npz')
        r['gallery_folder']=str(folder)
    write_json(gallery/'PHASE_VALIDATION_MANIFEST.json',records)
    return gallery/'20260609_110823_d14e2a0c'


def test_append_only_accept_adjust_resume_ambiguous_analysis_and_immutable_arrays(review_folder,monkeypatch):
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window=gui.ValidationPhaseWindow(review_folder,validation_dir=review_folder.parent.parent)
    protected=[p for p in review_folder.rglob('*') if p.is_file() and
               ('automated' in p.relative_to(review_folder).parts or 'analysis' in p.relative_to(review_folder).parts or
                p.name in ('phase_method_results.csv','all_phase_methods_results.csv','phase_quality_metrics.csv'))]
    before={p:sha256(p) for p in protected}
    real_count=sum(len(g.manual_status(Path(r['gallery_folder']))[1]) for r in g.discover_acquisitions())
    try:
        # Exercise the genuine human-status route solely in a pytest temporary folder.
        monkeypatch.setattr(QtWidgets.QInputDialog,'getText',lambda *a,**k:('',True))
        monkeypatch.setattr(QtWidgets.QInputDialog,'getMultiLineText',lambda *a,**k:('SOFTWARE FIXTURE ONLY: temporary optional-reviewer acceptance test',True))
        def fail_dialog(*args):raise AssertionError(args[-1])
        monkeypatch.setattr(QtWidgets.QMessageBox,'critical',fail_dialog)
        window._recalculate_manual()
        first=window.last_manual_output
        assert first.name=='review_001'
        checkpoint=g.json_read(first/'checkpoint.json')
        assert checkpoint['adjusted'] is False and checkpoint['starting_method']=='production'
        assert checkpoint['review_status']=='MANUAL REVIEW — DX METADATA PHASE ACCEPTED'
        assert checkpoint['reviewer'] is None
        assert checkpoint['review_completion_status']=='MANUAL REVIEW COMPLETE'
        assert window.processing_view.currentData()=='final'
        with np.load(first/'spectral_evidence.npz') as evidence:
            np.testing.assert_array_equal(window.saved_manual_view['regional_quantitative'],evidence['regional_quantitative'])
        window._open_manual_comparison()
        assert window.comparison_table.rowCount()==8
        assert [window.comparison_table.item(i,0).text() for i in range(8)]==[
            'DX metadata','ACME','Peak minima','Combined','Symmetry','Ernst P0','DEEP Phaser','Manual/current']
        assert window.comparison_table.item(0,1).text()=='0'
        assert window.open_comparison_button.isEnabled()
        target=g.json_read(first/'target_result.json')
        original=g.json_read(ROOT/'20260609_110823_d14e2a0c/analysis/production/target_result.json')
        assert target==original
        saved=window.saved_manual_view['final'].copy()
        window.processing_view.setCurrentIndex(0)
        window.phase_controls[0].setValue(63)
        window._view_final_spectrum()
        np.testing.assert_array_equal(window.real_curves['manual'].getData()[1],saved)
        assert not window.save_review_button.isEnabled()
        assert window._current_values()[0]==63
        window.processing_view.setCurrentIndex(0)
        window._reset_stored()
        rows=v.read_rows(first/'manual_vs_all_methods.csv')
        assert [r['method'] for r in rows]==[*AUTOMATIC,'manual']
        assert all(float(r['negative_area_fraction'])==float(r['negative_fraction']) for r in rows)
        prod=rows[0]
        assert float(prod['delta_area_vs_manual'])==0 and float(prod['delta_snr_vs_manual'])==0
        assert float(prod['delta_ppm_vs_manual'])==0
        assert g.manual_status(review_folder)[0]=='MANUAL REVIEW — DX METADATA PHASE ACCEPTED'
        window.processing_view.setCurrentIndex(0)
        window.phase_controls[0].setValue(64.5)
        window.baseline_review.setCurrentIndex(1)
        second=window.save_checkpoint('SOFTWARE FIXTURE ONLY','Temporary adjusted test',role='manual_reference')
        assert second.name=='review_002'
        assert g.json_read(second/'checkpoint.json')['adjusted'] is True
        assert g.json_read(second/'checkpoint.json')['baseline_review']=='accepted_unchanged'
        assert g.manual_status(review_folder)[0]=='MANUAL REVIEW — ADJUSTED'
        assert 'MANUAL REVIEW — ADJUSTED' in (review_folder.parent/'PHASE_VALIDATION_INDEX.md').read_text(encoding='utf-8')
        assert 'MANUAL REVIEW — ADJUSTED' in (review_folder.parent/'PHASE_VALIDATION_GALLERY.html').read_text(encoding='utf-8')
        window._view_final_spectrum()  # Still first saved output until explicitly loaded.
        monkeypatch.setattr(QtWidgets.QFileDialog,'getOpenFileName',lambda *a,**k:(str(second/'checkpoint.json'),''))
        window.load_checkpoint()
        assert window.starting_method=='loaded_checkpoint:review_002'
        assert window.baseline_review.currentData()=='accepted_unchanged'
        with np.load(second/'spectral_evidence.npz') as evidence:
            np.testing.assert_array_equal(window.real_curves['manual'].getData()[1][window.saved_manual_view['region_indices']],evidence['regional_quantitative'])
        window.ambiguous_check.setChecked(True)
        window.baseline_review.setCurrentIndex(2)
        third=window.save_checkpoint('SOFTWARE FIXTURE ONLY','Temporary ambiguous checkpoint-resume test')
        assert third.name=='review_003'
        assert g.json_read(third/'checkpoint.json')['baseline_review']=='flagged_for_review'
        assert g.json_read(third/'checkpoint.json')['adjusted'] is False
        assert g.manual_status(review_folder)[0]=='MANUAL REVIEW — AMBIGUOUS'
        assert g.json_read(third/'target_result.json')==g.json_read(second/'target_result.json')
        for output in (first,second,third):
            for name in ('manual_real_spectrum.csv','manual_imaginary_spectrum.csv','manual_phase.png',
                         'manual_full_spectrum.png','manual_vs_all_methods.png','manual_vs_all_methods.csv',
                         'completion_comparison.csv','sequence_contributors.csv','MANUAL_COMPARISON.md'):
                assert (output/name).is_file()
            for name in ('final_processed_spectrum.npz','final_processed_spectrum.csv','final_processed_spectrum.png',
                         'final_processed_spectrum.svg','final_processed_spectrum.pdf'):
                assert (output/name).is_file()
            assert g.json_read(output/'checkpoint.json')['baseline_changed'] is False
            assert g.json_read(output/'checkpoint.json')['frozen_automatic_tables_preserved'] is True
            assert len(v.read_rows(output/'all_phase_methods_with_manual.csv'))==9
            assert len(v.read_rows(output/'all_automatic_vs_manual_metrics.csv'))==8
            final_manifest=v.read_rows(output/'final_spectrum_figure_manifest.csv')
            assert len(final_manifest)==3 and len({m['visible_title'] for m in final_manifest})==1
            for m in final_manifest:
                assert m['visible_title'].startswith(m['dataset_display_name']+' ')
            manifests=v.read_rows(output/'manual_workflow_figure_manifest.csv')
            assert len(manifests)==2
            for m in manifests:assert m['visible_title'].startswith(m['dataset_display_name']+' ')
            cp=g.json_read(output/'checkpoint.json');p=cp['manual_phase']
            meta=g.json_read(review_folder/'automated/processing_metadata.json')
            independent=v.analyze(meta['source_path'],v.production_args(meta['source_path'],meta['parameters']),
                                  phase=(p['effective_index_zero_p0_deg'],p['p1_deg'],True,'manual'))
            assert independent['target']==g.json_read(output/'target_result.json')
        assert before=={p:sha256(p) for p in protected}
        assert sum(len(g.manual_status(Path(r['gallery_folder']))[1]) for r in g.discover_acquisitions())==real_count
    finally:window.close();app.processEvents()

