"""Portable raw import and checkpoint integration for the current reviewer."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
from PySide6 import QtWidgets

from chemyx_lab.analysis import nmr_validation as v
from chemyx_lab.analysis.phase5_review import prepare_review
from chemyx_lab.analysis.phase_audit import sha256
from chemyx_lab.analysis.phase_gallery import discover_acquisitions
from chemyx_lab.analysis.manual_phase_workflow import saved_methods

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/nmr'))
import phase5  # noqa: E402


@pytest.fixture(scope='module')
def review(tmp_path_factory):
    work = tmp_path_factory.mktemp('phase5')
    raw = work / 'work laptop' / 'raw' / 'example.dx'
    raw.parent.mkdir(parents=True)
    raw.write_bytes((ROOT / 'chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx').read_bytes())
    before = sha256(raw)
    root = prepare_review(raw, work / 'reviews')
    assert sha256(raw) == before
    return raw, root


@pytest.fixture(scope='module')
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_raw_import_identity_methods_and_title(review):
    raw, root = review
    records = discover_acquisitions(root)
    assert len(records) == 1
    record = records[0]
    assert record['timestamp_source'] in ('LONG DATE header', '$DATE epoch header')
    assert record['raw_sha256'] == sha256(raw) == sha256(record['source_path'])
    assert record['source_path'] != str(raw)
    folder = Path(record['gallery_folder'])
    methods = saved_methods(folder)
    assert 'production' in methods and 'deep_phaser' not in methods
    with np.load(folder / 'automated/spectral_evidence.npz') as arrays:
        for method in methods.values():
            np.testing.assert_array_equal(method['unphased'], arrays['unphased'])
            np.testing.assert_array_equal(method['ppm'], arrays['ppm'])
    manifest = v.read_rows(folder / 'automated/plot_manifest.csv')
    assert manifest
    for row in manifest:
        assert row['visible_title'].startswith(record['dataset_display_name'] + ' ')
        assert row['dataset_display_name'] == record['dataset_display_name']
    assert not list(folder.rglob('checkpoint.json'))


def test_missing_timestamp_and_bad_input_fail_before_outputs(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='No raw JCAMP'):
        prepare_review(tmp_path / 'missing.dx', tmp_path / 'reviews')
    raw = tmp_path / 'sequence-1030.dx'
    raw.write_text('not acquisition metadata')
    monkeypatch.setattr(v, 'read_jcamp_fid', lambda _: SimpleNamespace(metadata={}))
    with pytest.raises(ValueError, match='authoritative'):
        prepare_review(raw, tmp_path / 'reviews')
    assert not (tmp_path / 'reviews').exists()


def test_current_validation_controls_without_deep_and_checkpoint(review, qapp):
    raw, root = review
    record = discover_acquisitions(root)[0]
    window = phase5.Phase5Window(record['gallery_folder'], validation_dir=root)
    try:
        window.show()
        qapp.processEvents()
        assert window.model.path == Path(record['source_path'])
        assert window.acquisition_selector.count() == 1
        assert not window.trace_checks['deep_phaser'].isEnabled()
        index = window.basic_comparison.findData('deep_phaser')
        assert not window.basic_comparison.model().item(index).isEnabled()
        assert window._confirm_open()  # Pristine controls need no confirmation.
        window.advanced_toggle.setChecked(True)
        window._show_phase_quality()
        window._show_saved_phase_parameters()
        for stage in range(window.processing_view.count()):
            window.processing_view.setCurrentIndex(stage)
            for mode in range(window.spectrum_view.count()):
                window.spectrum_view.setCurrentIndex(mode)
                window._refresh_absorption_traces()
        before = {str(p): sha256(p) for p in Path(record['gallery_folder']).rglob('*')
                  if p.is_file() and 'manual_review' not in p.parts}
        checkpoint = window.save_checkpoint('Software test', role='test_phase_identity')
        assert (checkpoint / 'manual_vs_all_methods.csv').exists()
        assert all(sha256(p) == digest for p, digest in before.items())
        assert sha256(raw) == record['raw_sha256']
        rows = v.read_rows(checkpoint / 'manual_vs_all_methods.csv')
        assert not any(row['method'] == 'deep_phaser' for row in rows)
        assert json.loads((checkpoint / 'checkpoint.json').read_text())['raw_sha256'] == sha256(raw)
    finally:
        window.close()
        qapp.processEvents()


def test_no_argument_picker_cancel_is_clean(qapp, monkeypatch):
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileName', lambda *a: ('', ''))
    assert phase5.main([]) == 0


def test_threaded_processing_keeps_application_active(review, qapp, monkeypatch):
    raw, root = review
    calls = []
    def prepared(source, output):
        calls.append(source)
        return root
    monkeypatch.setattr(phase5, 'prepare_review', prepared)
    assert phase5.prepare_with_progress(raw, root.parent) == root
    assert calls == [raw]


def test_folder_import_deduplicates_switches_and_preserves_previous_review(review, qapp):
    raw, root = review
    before = {str(p): sha256(p) for p in root.rglob('*') if p.is_file()}
    duplicate = raw.with_name('copy.DX')
    duplicate.write_bytes(raw.read_bytes())
    second = raw.with_name('another.dx')
    second.write_bytes((ROOT / 'chemyx_lab/testing/fixtures/no_resonance_phsi2_20260609_0900.dx').read_bytes())
    fresh = prepare_review(raw.parent, root.parent)
    assert fresh != root
    records = discover_acquisitions(fresh)
    assert len(records) == 2
    assert [r['timestamp'] for r in records] == sorted(r['timestamp'] for r in records)
    assert all(sha256(p) == digest for p, digest in before.items())
    window = phase5.Phase5Window(records[0]['gallery_folder'], validation_dir=fresh)
    try:
        for index, record in enumerate(records):
            window.acquisition_selector.setCurrentIndex(index)
            assert sha256(window.model.path) == record['raw_sha256']
            assert not window.trace_checks['deep_phaser'].isEnabled()
        window.load_review(root)
        assert window.validation_dir == root
        assert window.acquisition_selector.count() == 1
        old_folder, old_values = window.acquisition_dir, window._current_values()
        with pytest.raises(FileNotFoundError):
            window.load_review(raw.parent)
        assert window.acquisition_dir == old_folder and window._current_values() == old_values
    finally:
        window.close()
