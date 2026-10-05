"""Current validation phase/baseline reviewer with simple raw .DX import.

Run without arguments to pick a raw file. Each import freshly processes into
results/phase5_review, preserving raw inputs and older processed results.
Use --validation-dir to reopen a prepared review or an existing validation.
This tool is offline and never connects to lab hardware.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
from PySide6 import QtCore, QtGui, QtWidgets

from validation_phase_gui import ValidationPhaseWindow
from chemyx_lab.analysis.phase5_review import DEFAULT_REVIEW_ROOT, prepare_review
from chemyx_lab.analysis.phase_gallery import discover_acquisitions, json_read
from chemyx_lab.analysis.phase_audit import sha256
from chemyx_lab.analysis.manual_phase_workflow import saved_methods
from chemyx_lab.analysis.manual_phase_workflow import AUTOMATIC


class PreparationWorker(QtCore.QThread):
    finished_review = QtCore.Signal(object)

    def __init__(self, source, output_root):
        super().__init__()
        self.source, self.output_root = source, output_root

    def run(self):
        try:
            self.finished_review.emit((prepare_review(self.source, self.output_root), None))
        except Exception as exc:
            self.finished_review.emit((None, str(exc)))


def prepare_with_progress(source, output_root, parent=None):
    progress = QtWidgets.QProgressDialog(
        'Processing raw FID and phase comparators…\nResults will be saved in a new review folder.',
        '', 0, 0, parent)
    progress.setCancelButton(None)
    progress.setWindowTitle('Prepare Phase 5 review')
    progress.setWindowModality(QtCore.Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.show()
    loop = QtCore.QEventLoop()
    result = []
    worker = PreparationWorker(source, output_root)

    def completed(value):
        result.extend(value)
        loop.quit()

    worker.finished_review.connect(completed)
    worker.start()
    loop.exec()
    worker.wait()
    progress.close()
    root, error = result
    if error:
        raise ValueError(error)
    return root


class Phase5Window(ValidationPhaseWindow):
    # The historical gallery index assumes its original cohort and figures.
    # Portable reviews keep that index separate from checkpoint calculations.
    refresh_gallery_index = False

    def __init__(self, acquisition_dir, reviewer=None, validation_dir=None,
                 output_root=DEFAULT_REVIEW_ROOT):
        self.review_output_root = Path(output_root)
        super().__init__(acquisition_dir, reviewer, validation_dir)
        for bar in self.findChildren(QtWidgets.QToolBar):
            for action in bar.actions():
                if isinstance(action, QtWidgets.QWidgetAction) and action.defaultWidget() is self.critical_only:
                    action.setVisible(False)
        toolbar = QtWidgets.QToolBar('Open data', self)
        first = self.findChildren(QtWidgets.QToolBar)[0]
        self.insertToolBar(first, toolbar)
        self.insertToolBarBreak(first)
        for label, callback in (
            ('Open raw .DX', self.open_raw_file),
            ('Process raw folder', self.open_raw_folder),
            ('Open saved review folder', self.open_review_folder),
            ('Open review output', self.open_output),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        self._sync_available_methods()
        self._set_phase5_identity()

    def _load_validation_acquisition(self, folder):
        super()._load_validation_acquisition(folder)
        self._sync_available_methods()
        self._set_phase5_identity()

    def _set_phase5_identity(self):
        self.setWindowTitle('Phase 5 | ' + self.windowTitle().removeprefix('Phase 5 | '))
        self.status.setText(f'Review folder: {self.validation_dir}. '
                            'Automatic results are saved; manual edits are live until you save a checkpoint. '
                            'Completion uses only acquisitions in this review.')

    def _sync_available_methods(self):
        for name in AUTOMATIC:
            available = name in self.fixed_methods
            check = self.trace_checks[name]
            check.setEnabled(available)
            if not available:
                check.setChecked(False)
                check.setToolTip('Unavailable in this review; no saved result exists')
        for attr in ('start_selector', 'basic_comparison', 'solo_selector'):
            combo = getattr(self, attr, None)
            if combo is None:
                continue
            for index in range(combo.count()):
                name = combo.itemData(index)
                if name not in AUTOMATIC:
                    continue
                item = combo.model().item(index)
                item.setEnabled(name in self.fixed_methods)
                if name not in self.fixed_methods:
                    item.setToolTip('Unavailable in this review; no saved result exists')
        self.phase_availability_label.setText(
            f'{len(self.fixed_methods)} automatic results available; unavailable methods are disabled.')

    def load_review(self, root):
        records = discover_acquisitions(root)
        if not records:
            raise ValueError('This review has no acquisitions')
        # Reject an incompatible processed directory before touching live
        # controls. A production CSV folder is not a validation review package.
        folder = Path(records[0]['gallery_folder'])
        meta = json_read(folder / 'automated/processing_metadata.json')
        if sha256(meta['source_path']) != meta['raw_sha256']:
            raise ValueError('Raw input differs from the saved review')
        saved_methods(folder)
        # Load before replacing the acquisition selector's context.
        previous_root = self.validation_dir
        previous_folder = self.acquisition_dir
        previous_values = self._current_values()
        try:
            self.validation_dir = Path(root).resolve()
            self._load_validation_acquisition(records[0]['gallery_folder'])
        except Exception:
            self.validation_dir = previous_root
            self._load_validation_acquisition(previous_folder)
            from phase4 import PhaseCandidate
            self._set_phase_controls(PhaseCandidate('Unsaved controls', *previous_values))
            raise
        self.records = records
        self.critical_only.setChecked(False)
        self._populate_selector()

    def import_raw(self, source):
        root = prepare_with_progress(source, self.review_output_root, self)
        self.load_review(root)
        return root

    def _confirm_open(self):
        # Keep checkpoint creation explicit; opening another acquisition does
        # not turn exploratory controls into a human reference automatically.
        current = dict(zip(('p0_deg', 'p1_deg', 'pivot_ppm'), self._current_values()))
        if self.loaded_checkpoint:
            saved = json_read(self.loaded_checkpoint / 'checkpoint.json')['manual_phase']
            saved = {'p0_deg': saved['effective_index_zero_p0_deg'] + saved['p1_deg'] *
                     self.model.pivot_fraction(saved['pivot_ppm']),
                     'p1_deg': saved['p1_deg'], 'pivot_ppm': saved['pivot_ppm']}
        else:
            saved = self.starting_phase
        if all(abs(current[key] - float(saved[key])) < 1e-9 for key in current):
            return True
        return QtWidgets.QMessageBox.question(
            self, 'Open another dataset',
            'Opening another dataset discards unsaved phase adjustments. Continue?',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes

    def open_raw_file(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Select raw NMReady JCAMP-DX file', '', 'JCAMP-DX (*.dx)')
        if filename and self._confirm_open():
            self._open_safely(lambda: self.import_raw(filename))

    def open_raw_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Select raw .DX dataset folder')
        if folder and self._confirm_open():
            self._open_safely(lambda: self.import_raw(folder))

    def open_review_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Select saved Phase 5 or validation folder')
        if folder and self._confirm_open():
            self._open_safely(lambda: self.load_review(folder))

    def _open_safely(self, callback):
        try:
            callback()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Could not open data', str(exc))

    def open_output(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.validation_dir)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, nargs='?', help='Raw .dx file or raw dataset directory')
    parser.add_argument('--validation-dir', type=Path, help='Reopen an existing review; no preprocessing')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_REVIEW_ROOT,
                        help='Parent for new review folders (default: results/phase5_review)')
    parser.add_argument('--reviewer')
    parser.add_argument('--smoke-screenshot', type=Path)
    args = parser.parse_args(argv)
    if args.source and args.validation_dir:
        parser.error('Choose a raw source or --validation-dir')
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Match the validation GUI's readable font, including offscreen Windows Qt.
    font_path = Path('C:/Windows/Fonts/arial.ttf')
    if font_path.is_file():
        font_id = QtGui.QFontDatabase.addApplicationFont(str(font_path))
        families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QtGui.QFont(families[0], 9))
    if args.validation_dir:
        root = args.validation_dir
    else:
        source = args.source
        if source is None:
            chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, 'Phase 5 — select raw NMReady JCAMP-DX file', '', 'JCAMP-DX (*.dx)')
            if not chosen:
                return 0
            source = Path(chosen)
        try:
            root = prepare_with_progress(source, args.output_dir)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(None, 'Could not prepare review', str(exc))
            return 1
    try:
        records = discover_acquisitions(root)
        if not records:
            raise ValueError('This review has no acquisitions')
        window = Phase5Window(records[0]['gallery_folder'], args.reviewer, root, args.output_dir)
    except Exception as exc:
        QtWidgets.QMessageBox.critical(None, 'Could not load review', str(exc))
        return 1
    window.show()
    if args.smoke_screenshot:
        def capture():
            args.smoke_screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.smoke_screenshot))
            app.quit()
        QtCore.QTimer.singleShot(1000, capture)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
