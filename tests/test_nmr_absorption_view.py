"""Display-only checks against retained real instrument evidence."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import numpy as np
import pytest
from PySide6 import QtWidgets
from chemyx_lab.analysis import nmr_validation as v, phase_gallery as g
from chemyx_lab.analysis.nmr_spectrum_view import load_processed_view, target_peak
from chemyx_lab.analysis.manual_phase_workflow import AUTOMATIC

sys.path.insert(0,str(v.ROOT/'scripts/nmr'))
import validation_phase_gui as gui
FOLDER=v.DEFAULT_OUTPUT/'phase_validation/20260609_110823_d14e2a0c'


@pytest.fixture
def window():
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w=gui.ValidationPhaseWindow(FOLDER,validation_dir=v.DEFAULT_OUTPUT)
    w.show();app.processEvents()
    yield w
    w.close();app.processEvents()


def test_final_view_is_exact_quantitative_evidence_with_explicit_roi():
    view=load_processed_view(FOLDER/'analysis/production')
    with np.load(FOLDER/'analysis/production/spectral_evidence.npz') as a:
        outside=np.ones(a['ppm'].size,dtype=bool);outside[view['region_indices']]=False
        np.testing.assert_array_equal(view['final'][view['region_indices']],a['regional_quantitative'])
        np.testing.assert_array_equal(view['final'][outside],a['quantitative_real'][outside])
        np.testing.assert_allclose(view['phased'].real-view['baseline'],view['final'],rtol=1e-12,atol=1e-10)
        view['final'][:]=0
        assert np.any(a['quantitative_real']!=0)  # Views own their copies.


def test_absorption_default_and_standard_axis(window):
    assert window.spectrum_view.currentData()=='real'
    assert window.processing_view.currentData()=='before'
    assert not window.imaginary_plot.isVisible()
    assert window.overlay_plot.isVisible()
    assert window.overlay_plot.getViewBox().state['xInverted']
    assert [n for n,c in window.trace_checks.items() if c.isChecked()]==['manual']
    assert window.starting_method=='production'
    np.testing.assert_allclose(window._manual_complex(),window.fixed_methods['production']['phased'],rtol=1e-11,atol=1e-7)
    assert window.overlay_plot.plotItem.titleLabel.text.startswith('06-09-26 ')


def test_diagnostic_modes_preserve_phase_analysis_and_exact_saved_arrays(window,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Display action reran analysis')
    monkeypatch.setattr(v,'analyze',forbidden)
    phase=window._current_values();source=window._analysis_view()
    fixed={n:a['phased'].copy() for n,a in window.fixed_methods.items()}
    window._set_comparisons(('unphased',*AUTOMATIC,'manual'))
    for index,key in ((1,'imaginary'),(2,'complex'),(3,'magnitude'),(0,'real')):
        window.spectrum_view.setCurrentIndex(index)
        assert window.imaginary_plot.isVisible()==(key=='complex')
        for n in ('unphased',*AUTOMATIC,'manual'):
            z=window._displayed_complex(n)
            expected=z.imag if key=='imaginary' else np.abs(z) if key=='magnitude' else z.real
            np.testing.assert_array_equal(window.real_curves[n].getData()[1],expected)
        assert window._current_values()==phase
        assert window._analysis_view() is source
    window.spectrum_view.setCurrentIndex(3)
    magnitude=window.real_curves['manual'].getData()[1].copy()
    window.phase_controls[0].setValue(window.phase_controls[0].value()+8)
    window.phase_controls[1].setValue(window.phase_controls[1].value()-9)
    np.testing.assert_allclose(window.real_curves['manual'].getData()[1],magnitude,rtol=1e-12,atol=1e-9)
    for n in AUTOMATIC:np.testing.assert_array_equal(window.fixed_methods[n]['phased'],fixed[n])
    assert window._analysis_view() is source
    window._show_phase_quality()
    assert window.quality_dialog.findChild(QtWidgets.QTableWidget).rowCount()==7
    window.quality_dialog.close()


def test_production_baseline_final_views_without_manual_checkpoint(window,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Baseline display reran analysis')
    monkeypatch.setattr(v,'analyze',forbidden)
    window.saved_manual_view=None;window.loaded_checkpoint=None
    source=window.processed_methods['production'];phase=window._current_values()
    window.processing_view.setCurrentIndex(1)
    np.testing.assert_array_equal(window.real_curves['production'].getData()[1],source['baseline'])
    np.testing.assert_array_equal(window.raw_context_curve.getData()[1],source['phased'].real)
    assert not window.real_curves['manual'].isVisible()
    assert not window.save_review_button.isEnabled()
    window._view_final_spectrum()
    np.testing.assert_array_equal(window.real_curves['production'].getData()[1],source['final'])
    assert 'Final Processed Real / Absorption' in window.overlay_plot.plotItem.titleLabel.text
    window.baseline_overlay.setChecked(True)
    np.testing.assert_array_equal(window.baseline_context_curve.getData()[1],source['baseline'])
    peak=target_peak(source)
    lines=[x.value() for x in window.analysis_markers if isinstance(x,gui.phase4.pg.InfiniteLine)]
    assert lines==[float(source['region_ppm'].min()),float(source['region_ppm'].max()),
                   float(peak['integration_left_ppm']),float(peak['integration_right_ppm'])]
    window.spectrum_view.setCurrentIndex(3)
    assert not window.baseline_context_curve.isVisible()
    assert window._current_values()==phase and window._analysis_view() is source
    window.processing_view.setCurrentIndex(0)
    assert window.phase_controls[0].isEnabled()
    assert [n for n,c in window.trace_checks.items() if c.isChecked()]==['manual']


def test_comparison_shortcuts_and_diagnostics_button(window):
    window._set_comparisons(AUTOMATIC)
    assert all(window.real_curves[n].isVisible() for n in AUTOMATIC)
    window._set_comparisons(('manual',))
    assert not any(window.real_curves[n].isVisible() for n in AUTOMATIC)
    window.diagnostics_button.setChecked(True)
    assert window.spectrum_view.currentData()=='complex' and window.imaginary_plot.isVisible()
    window.diagnostics_button.setChecked(False)
    assert window.spectrum_view.currentData()=='real' and not window.imaginary_plot.isVisible()
