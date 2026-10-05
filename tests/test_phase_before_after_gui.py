"""Before/after presentation against isolated copies of real NMReady FIDs."""
import re
import numpy as np
import pytest
from PySide6 import QtWidgets
from test_gui_basic_advanced import june9_workspace, window
from chemyx_lab.analysis import nmr_validation as v
from chemyx_lab.analysis.manual_phase_workflow import AUTOMATIC
from chemyx_lab.analysis.phase_labels import LEGEND, DX_METADATA_HELP, display_review_status


def visible_names(w):
    return {n for n, curve in w.real_curves.items() if curve.isVisible()}


def test_labels_and_before_controls_visible_without_advanced(window):
    assert not window.advanced_toggle.isChecked()
    for widget in (window.advanced_toggle,window.basic_comparison,window.reset_production_button,
                   *window.basic_phase_controls,window.before_fft_check, window.metadata_phase_check, window.before_only_button,
                   window.before_metadata_button, window.before_manual_button, window.phase_comparison_help):
        assert widget.isVisible(), getattr(widget,'text',lambda:widget.objectName())()
    assert window.before_fft_check.text() == 'Unphased FFT — BEFORE phase correction'
    assert window.reset_production_button.text() == 'Reset to DX metadata phase'
    assert window.reset_production_button.toolTip() == DX_METADATA_HELP
    window.advanced_toggle.setChecked(True)
    for widget in window.findChildren(QtWidgets.QWidget):
        if isinstance(widget, (QtWidgets.QLabel, QtWidgets.QPushButton, QtWidgets.QCheckBox)):
            assert not re.search(r'\bproduction\b', widget.text(), re.I), widget.text()
        elif isinstance(widget, QtWidgets.QComboBox):
            assert all(not re.search(r'\bproduction\b', widget.itemText(i), re.I) for i in range(widget.count()))
    assert window.start_selector.itemText(0).startswith('DX metadata phase')
    assert display_review_status('MANUAL REVIEW — PRODUCTION ACCEPTED') == 'MANUAL REVIEW — DX METADATA PHASE ACCEPTED'


def test_every_june9_before_trace_is_exact_common_fft_without_phase(window, monkeypatch):
    monkeypatch.setattr(v, 'analyze', lambda *a, **kw: pytest.fail('Display ran analysis'))
    for i in range(8):
        window.acquisition_selector.setCurrentIndex(i)
        window.before_only_button.click()
        assert visible_names(window) == {'unphased'}
        z = window._displayed_complex('unphased')
        np.testing.assert_array_equal(z, window.model.fft_spectrum)
        for name in AUTOMATIC:
            np.testing.assert_array_equal(z, window.fixed_methods[name]['unphased'])
        assert not np.array_equal(z, window.fixed_methods['production']['phased'])
        np.testing.assert_array_equal(window.real_curves['unphased'].getData()[1], z.real)
        assert window.overlay_plot.plotItem.titleLabel.text.startswith(window.model.dataset_display_name + ' ')
        assert [label.text for _, label in window.overlay_plot.plotItem.legend.items] == [LEGEND['unphased']]


def test_quick_comparisons_preserve_live_phase_analysis_and_pinned_methods(window, monkeypatch):
    monkeypatch.setattr(v, 'analyze', lambda *a, **kw: pytest.fail('Display ran analysis'))
    source = window._analysis_view()
    window.basic_phase_controls[0].setValue(window._current_values()[0] + 5)
    phase = window._current_values()
    fixed = {name: a['phased'].copy() for name, a in window.fixed_methods.items()}
    window.basic_comparison.setCurrentIndex(window.basic_comparison.findData('deep_phaser'))
    window.before_metadata_button.click()
    assert visible_names(window) == {'unphased', 'production'}
    assert window.before_fft_check.isChecked() and window.metadata_phase_check.isChecked()
    labels = {label.text for _, label in window.overlay_plot.plotItem.legend.items}
    assert labels == {LEGEND['unphased'], LEGEND['production']}
    window.before_manual_button.click()
    assert visible_names(window) == {'unphased', 'manual'}
    np.testing.assert_array_equal(window.real_curves['manual'].getData()[1], window._manual_complex().real)
    window.advanced_toggle.setChecked(True)
    window.trace_checks['acme'].setChecked(True)  # An explicit Advanced check pins it.
    window.before_metadata_button.click()
    assert visible_names(window) == {'unphased', 'production', 'acme'}
    window.before_manual_button.click()
    assert visible_names(window) == {'unphased', 'manual', 'acme'}
    window.trace_checks['acme'].setChecked(False)
    window.advanced_toggle.setChecked(False)
    assert visible_names(window) == {'unphased', 'manual'}
    window.before_fft_check.setChecked(False)
    assert visible_names(window) == {'manual'}
    window.advanced_toggle.setChecked(True)
    window.trace_checks['unphased'].setChecked(True)
    window.trace_checks['production'].setChecked(True)
    window.advanced_toggle.setChecked(False)
    assert visible_names(window) == {'unphased','production','manual'}
    window.advanced_toggle.setChecked(True)
    window.trace_checks['unphased'].setChecked(False)
    window.trace_checks['production'].setChecked(False)
    window.advanced_toggle.setChecked(False)
    assert visible_names(window) == {'manual'}
    assert window._analysis_view() is source and window._current_values() == phase
    for name in AUTOMATIC:
        np.testing.assert_array_equal(window.fixed_methods[name]['phased'], fixed[name])


def test_basic_independent_references_and_reset_do_not_select_analysis(window, monkeypatch):
    monkeypatch.setattr(v, 'analyze', lambda *a, **kw: pytest.fail('Reference ran analysis'))
    source = window._analysis_view()
    start = window._current_values()
    window.before_fft_check.setChecked(True)
    window.metadata_phase_check.setChecked(True)
    window.basic_comparison.setCurrentIndex(window.basic_comparison.findData('acme'))
    assert visible_names(window) == {'unphased', 'production', 'acme', 'manual'}
    window.basic_phase_controls[0].setValue(start[0] + 7)
    window.reset_production_button.click()
    assert window._current_values() == start
    np.testing.assert_array_equal(window._manual_complex(), window.fixed_methods['production']['phased'])
    window._view_final_spectrum()
    np.testing.assert_array_equal(window.real_curves['production'].getData()[1], source['final'])
    assert 'DX metadata phase [saved]' in window.analysis_label.text()
    window.before_manual_button.click()
    assert window.processing_view.currentData() == 'before'
    assert window.spectrum_view.currentData() == 'real'
    assert window._analysis_view() is source
    assert window.loaded_checkpoint is None  # Reference choices never save a result.
