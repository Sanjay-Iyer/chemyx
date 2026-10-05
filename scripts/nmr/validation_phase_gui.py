"""Offline absorption-spectrum review with optional complex diagnostics."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import phase4
from absorption_view import ChemistSpectrumView
from basic_phase_view import ChemistBasicView
from chemyx_lab.analysis.nmr_validation import save_manual_checkpoint, DEFAULT_OUTPUT
from chemyx_lab.analysis.phase_gallery import discover_acquisitions, save_gallery_checkpoint, manual_status
from chemyx_lab.analysis.phase_audit import sha256
from chemyx_lab.analysis.manual_phase_workflow import AUTOMATIC, DISPLAY, saved_methods, inverse_start
from chemyx_lab.analysis.phase_labels import DX_METADATA_HELP, LEGEND, TABLE_DISPLAY, display_review_status


class ValidationPhaseWindow(ChemistBasicView,ChemistSpectrumView,phase4.Phase4Window):
    def __init__(self, acquisition_dir, reviewer=None, validation_dir=DEFAULT_OUTPUT):
        self.acquisition_dir=Path(acquisition_dir).resolve()
        self.validation_dir=Path(validation_dir).resolve()
        self.records=discover_acquisitions(self.validation_dir)
        self.reviewer=reviewer
        self.loaded_checkpoint=None
        meta=json.loads((self.acquisition_dir/'automated/processing_metadata.json').read_text())
        super().__init__(Path(meta['source_path']))
        self.setWindowTitle("NMR validation — manual expert review (offline)")
        self.pivot.setDecimals(12)
        toolbar=self.addToolBar("Validation acquisition and checkpoint")
        toolbar.addWidget(QtWidgets.QLabel('Acquisition: '))
        self.acquisition_selector=QtWidgets.QComboBox()
        self.acquisition_selector.setMinimumWidth(540)
        toolbar.addWidget(self.acquisition_selector)
        self.critical_only=QtWidgets.QCheckBox('June 9 critical only')
        toolbar.addWidget(self.critical_only)
        load=QtWidgets.QPushButton("Load checkpoint")
        load.clicked.connect(self.load_checkpoint)
        toolbar.addWidget(load)
        self.checkpoint_label=QtWidgets.QLabel("Current checkpoint: unsaved manual controls")
        toolbar.addWidget(self.checkpoint_label)
        self.method_overlay=QtWidgets.QCheckBox('Overlay ACME / peak minima')
        self.acme_curve=self.overlay_plot.plot(pen=phase4.pg.mkPen('#D55E00',width=1),name='ACME (direct)',autoDownsample=True)
        self.peak_minima_curve=self.overlay_plot.plot(pen=phase4.pg.mkPen('#009E73',width=1),name='Peak minima (direct)',autoDownsample=True)
        self.method_overlay.toggled.connect(self._toggle_methods)
        self.extra_method_curves={}
        self.addToolBarBreak()
        extra_toolbar=self.addToolBar('All implemented phase methods')
        extra_toolbar.addWidget(QtWidgets.QLabel('Additional comparison: '))
        self.phase_method_selector=QtWidgets.QComboBox()
        self.phase_method_selector.setMinimumWidth(280)
        extra_toolbar.addWidget(self.phase_method_selector)
        self.selected_method_overlay=QtWidgets.QCheckBox('Overlay selected / all methods')
        extra_toolbar.addWidget(self.selected_method_overlay)
        self.phase_availability_label=QtWidgets.QLabel('')
        extra_toolbar.addWidget(self.phase_availability_label)
        self.phase_method_selector.currentIndexChanged.connect(self._toggle_selected_methods)
        self.selected_method_overlay.toggled.connect(self._toggle_selected_methods)
        extra_toolbar.hide()
        self.method_overlay.hide()
        self._install_manual_workflow()
        self._install_absorption_view()
        for label in self.findChildren(QtWidgets.QLabel):
            if label.text()=='NMR Phase Review — Original vs Automated vs Manual':
                label.setText('NMR Manual Phase & Baseline Review')
        # This viewer is tied to the validation selector; unrelated browse/open
        # actions must not silently replace the FID while retaining its old ID.
        for button in self.findChildren(QtWidgets.QPushButton):
            if button.text() in {'Open .DX','Browse Runs','Open Phase Review','Select Output Directory'}:
                button.setEnabled(False)
                button.setToolTip('Choose a validation acquisition using the dropdown above')
        for group in self.findChildren(QtWidgets.QGroupBox):
            if group.title()=='Saved Phase Corrections (Manual)':
                group.setTitle('Optional draft candidates — not review checkpoints')
        self.acquisition_selector.currentIndexChanged.connect(self._select_acquisition)
        self.critical_only.toggled.connect(self._populate_selector)
        self._load_validation_acquisition(self.acquisition_dir)
        self._populate_selector()
        self._install_basic_view()

    def _install_manual_workflow(self):
        from chemyx_lab.analysis.additional_phase_methods import LABELS,COLORS
        self.fixed_methods={}
        self.trace_checks={};self.real_curves={};self.imag_curves={}
        self.imaginary_plot=self._new_plot('Imaginary — same phase / same FFT')
        self.overlay_plot.setXLink(None)
        self.imaginary_plot.setXLink(self.overlay_plot)
        self.overlay_plot.getViewBox().sigXRangeChanged.connect(self._rescale_complex_view)
        self.imaginary_plot.addLegend().setColumnCount(3)
        self.overlay_plot.plotItem.legend.setColumnCount(3)
        self.overlay_widget.layout().addWidget(self.imaginary_plot,1)
        self.addToolBarBreak()
        traces=self.addToolBar('Real and imaginary traces')
        traces.addWidget(QtWidgets.QLabel('Comparison: '))
        for name in ('unphased',*AUTOMATIC,'manual'):
            label=DISPLAY[name]+(' [live]' if name=='manual' else ' [saved]')
            check=QtWidgets.QCheckBox(label)
            check.setToolTip('LIVE CALCULATION: manual complex phase rotation only' if name=='manual' else
                             'PRECOMPUTED RESULT LOADED: exact saved complex arrays; no phase optimizer runs')
            if name=='production':check.setToolTip(DX_METADATA_HELP)
            check.setChecked(name in ('production','manual'))
            check.toggled.connect(self._refresh_traces)
            traces.addWidget(check);self.trace_checks[name]=check
            pen=phase4.pg.mkPen('#000000' if name=='manual' else COLORS.get(name,'#777777'),width=1.5 if name=='manual' else 1)
            self.real_curves[name]=self.overlay_plot.plot(pen=pen,name=label,autoDownsample=True)
            self.imag_curves[name]=self.imaginary_plot.plot(pen=pen,name=label,autoDownsample=True)
        self.addToolBarBreak()
        controls=self.addToolBar('Manual start and precision controls')
        controls.addWidget(QtWidgets.QLabel('Start manual controls from: '))
        self.start_selector=QtWidgets.QComboBox()
        for name in AUTOMATIC:self.start_selector.addItem(DISPLAY[name]+' [saved]',name)
        controls.addWidget(self.start_selector)
        self.start_selector.currentIndexChanged.connect(self._start_from_selected)
        # activated also fires when the user chooses the current item to reset it.
        self.start_selector.activated.connect(self._start_from_selected)
        self.phase_controls=[]
        self.phase_exact=[None,None]
        for label in ('P0','P1'):
            controls.addWidget(QtWidgets.QLabel(label+' (degrees): '))
            spin=QtWidgets.QDoubleSpinBox();spin.setDecimals(12);spin.setRange(-100000,100000);spin.setSingleStep(.1)
            spin.setMinimumWidth(155)
            spin.valueChanged.connect(lambda _value,index=len(self.phase_controls):self._phase_spin_changed(index))
            controls.addWidget(spin);self.phase_controls.append(spin)
        self.p0_slider.valueChanged.connect(lambda value:self.phase_controls[0].setValue(value/10))
        self.p1_slider.valueChanged.connect(lambda value:self.phase_controls[1].setValue(value/10))
        self.ambiguous_check=QtWidgets.QCheckBox('Mark review ambiguous')
        controls.addWidget(self.ambiguous_check)
        self.open_comparison_button=QtWidgets.QPushButton('Open manual comparison')
        self.open_comparison_button.setToolTip('Comparison from the last saved checkpoint; unsaved adjustments are not included')
        self.open_comparison_button.setEnabled(False)
        self.open_comparison_button.clicked.connect(self._open_manual_comparison)
        controls.addWidget(self.open_comparison_button)
        self.addToolBarBreak()
        provenance_bar=self.addToolBar('Trace provenance')
        provenance_bar.addWidget(QtWidgets.QLabel(
            '[saved] = precomputed complex array. [live] = manual phase rotation. Save reruns the same downstream analysis.'))
        for group in self.findChildren(QtWidgets.QGroupBox):
            if group.title()=='Saved Phase Corrections (Manual)':
                for child in group.findChildren(QtWidgets.QWidget):child.hide()
                group.setTitle('Manual phasing checklist')
                help_label=QtWidgets.QLabel('1. Start from DX metadata phase.\n2. Inspect Real / Absorption.\n'
                    '3. Adjust P0 for overall absorption.\n4. Adjust P1 for relative phase across the spectrum.\n'
                    '5. Pivot only when needed.\n6. Inspect target closely.\n7. Compare ACME/Combined/Symmetry/DEEP.\n'
                    '8. Open diagnostics if needed.\n9. Do not maximize height alone.\n10. Save; inspect Final and baseline.')
                help_label.setWordWrap(True);group.layout().addWidget(help_label)
        self.overlay_checkbox.setChecked(True)
        self.overlay_checkbox.hide()
        self.stacked_widget.hide()
        self.overlay_plot.plotItem.legend.clear()
        for plot in (self.overlay_plot,self.imaginary_plot):
            plot.plotItem.legend.anchor((1,0),(1,0),offset=(-10,10))
        for name in ('overlay_original','overlay_automated','overlay_manual','acme_curve','peak_minima_curve'):
            getattr(self,name).setVisible(False)

    def _set_phase_controls(self,candidate):
        if not hasattr(self,'phase_controls'):return super()._set_phase_controls(candidate)
        widgets=(*self.phase_controls,self.p0_slider,self.p1_slider,self.pivot)
        for widget in widgets:widget.blockSignals(True)
        self.phase_controls[0].setValue(candidate.p0_deg);self.phase_controls[1].setValue(candidate.p1_deg)
        self.p0_slider.setValue(round(candidate.p0_deg*10));self.p1_slider.setValue(round(candidate.p1_deg*10))
        self.pivot.setValue(candidate.pivot_ppm)
        self.phase_exact=[candidate.p0_deg,candidate.p1_deg]
        for widget in widgets:widget.blockSignals(False)
        self._update_plots()

    def _phase_spin_changed(self,index):
        self.phase_exact[index]=None
        slider=(self.p0_slider,self.p1_slider)[index]
        slider.blockSignals(True)
        slider.setValue(round(self.phase_controls[index].value()*10))
        slider.blockSignals(False)
        self._update_plots()

    def _start_from_selected(self,*_):
        name=self.start_selector.currentData()
        if name not in self.fixed_methods:return
        pivot=float(self.model.ppm[0])
        p0,p1=inverse_start(self.fixed_methods[name]['metrics'],self.model.pivot_fraction(pivot))
        self._set_phase_controls(phase4.PhaseCandidate(name,p0,p1,pivot))
        self.starting_method=name
        self.starting_phase=dict(zip(('p0_deg','p1_deg','pivot_ppm'),self._current_values()))
        self.ambiguous_check.setChecked(False)
        self._commit_phase_context()

    def _copy_automated_to_manual(self):
        if getattr(self,'fixed_methods',{}):
            self.start_selector.setCurrentIndex(0);self._start_from_selected()
        else:super()._copy_automated_to_manual()

    def _reset_stored(self):
        self._copy_automated_to_manual()

    def _manual_complex(self):
        p0,p1,pivot=self._current_values()
        return phase4.ng.proc_base.ps(self.model.fft_spectrum,
            p0=self.model.effective_p0(p0,p1,pivot),p1=p1,inv=True)

    def _update_plots(self,*args):
        super()._update_plots(*args)
        if hasattr(self,'trace_checks'):self._refresh_traces()
        self._track_phase_edit()

    def _refresh_traces(self,*_):
        self._refresh_absorption_traces()

    def _rescale_complex_view(self,*_):
        self._rescale_absorption_view()

    def _set_view(self,bounds):
        super()._set_view(bounds)
        if hasattr(self,'trace_checks'):
            lo,hi=(float(self.model.ppm.min()),float(self.model.ppm.max())) if bounds is None else sorted(bounds)
            self.overlay_plot.setXRange(lo,hi,padding=.02)
            self._rescale_complex_view()

    def _open_manual_comparison(self):
        if not self.loaded_checkpoint:return
        from chemyx_lab.analysis.nmr_validation import read_rows,truth
        path=self.loaded_checkpoint/'manual_vs_all_methods.csv'
        if not path.is_file():return
        if hasattr(self,'comparison_dialog'):self.comparison_dialog.close()
        self.comparison_dialog=QtWidgets.QDialog(self)
        self.comparison_dialog.setWindowTitle(f'{self.model.dataset_display_name} | {self.acquisition_dir.name} | Manual comparison {self.loaded_checkpoint.name}')
        self.comparison_dialog.resize(1060,480)
        layout=QtWidgets.QVBoxLayout(self.comparison_dialog)
        path_label=QtWidgets.QLabel(str(self.loaded_checkpoint))
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(path_label)
        rows=read_rows(path)
        self.comparison_table=QtWidgets.QTableWidget(len(rows),6)
        self.comparison_table.setHorizontalHeaderLabels(
            ['Method','ΔArea vs manual','ΔS/N vs manual','ΔPeak ppm','Detection','Completion'])
        self.comparison_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        def numeric(value):
            try:return f'{float(value):.6g}'
            except (TypeError,ValueError):return '—'
        for i,row in enumerate(rows):
            values=[TABLE_DISPLAY.get(row['method'],row['method']),numeric(row['delta_area_vs_manual']),
                    numeric(row['delta_snr_vs_manual']),numeric(row['delta_ppm_vs_manual']),
                    str(truth(row['detected'])),str(truth(row['retrospective_completion']))+' / '+row['completion_classification']]
            for j,value in enumerate(values):self.comparison_table.setItem(i,j,QtWidgets.QTableWidgetItem(value))
        self.comparison_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.comparison_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.comparison_table)
        context=QtWidgets.QLabel('Deltas = automatic minus manual. Completion is retrospective: automatic rows use saved same-method date prefixes; '
            'manual uses the recorded reviewed/DX metadata phase sequence. See sequence_contributors.csv; this does not verify a physical stop.')
        context.setWordWrap(True);layout.addWidget(context)
        buttons=QtWidgets.QHBoxLayout()
        for label,filename in [('Open full CSV','manual_vs_all_methods.csv'),('Open target plot','manual_vs_all_methods.png'),
                               ('Open comparison explanation','MANUAL_COMPARISON.md')]:
            button=QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _checked=False,p=self.loaded_checkpoint/filename:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p))))
            buttons.addWidget(button)
        layout.addLayout(buttons);self.comparison_dialog.show()

    def save_checkpoint(self,reviewer,notes='',role='manual_reference'):
        p0,p1,pivot=self._current_values()
        return save_gallery_checkpoint(self.acquisition_dir,p0_deg=p0,p1_deg=p1,pivot_ppm=pivot,
            pivot_fraction=self.model.pivot_fraction(pivot),reviewer=reviewer,notes=notes,role=role,
            starting_method=self.starting_method,starting_phase=self.starting_phase,
            ambiguous=self.ambiguous_check.isChecked(),baseline_review=self.baseline_review.currentData(),
            refresh_gallery_index=getattr(self,'refresh_gallery_index',True))

    def _current_values(self):
        p0,p1,pivot=super()._current_values()
        if hasattr(self,'phase_controls'):
            p0,p1=(exact if exact is not None else spin.value()
                   for spin,exact in zip(self.phase_controls,self.phase_exact))
        # Spinbox display precision must not perturb the index-zero starting pivot.
        axis=self.model.ppm
        if abs(pivot-float(axis.min()))<1e-10:pivot=float(axis.min())
        if abs(pivot-float(axis.max()))<1e-10:pivot=float(axis.max())
        return p0,p1,pivot

    def _load_validation_acquisition(self, folder):
        if hasattr(self,'_phase_history'):self._history_suspended=True
        self.acquisition_dir=Path(folder).resolve()
        self.fixed_methods={}
        self.processed_methods={}
        self.saved_manual_view=None
        self.last_manual_output=None
        self.loaded_checkpoint=None
        auto=self.acquisition_dir/"automated"
        meta=json.loads((auto/"processing_metadata.json").read_text())
        with np.load(auto/"spectral_evidence.npz") as saved:
            data={k:saved[k].copy() for k in saved.files}
        display_path=self.acquisition_dir/"gui_stored_phase_before_baseline.csv"
        if not display_path.exists():
            np.savetxt(display_path,np.column_stack((data["ppm"],data["phased"].real)),
                       delimiter=",",fmt="%.17g",header="referenced_ppm,real",comments="")
        phase=meta["phase"]
        result=phase4.AutomatedPhaseResult(source_dx=Path(meta["source_path"]),
            source_filename=Path(meta["source_path"]).name,output_directory=auto,
            summary_path=auto/"processing_metadata.json",p0_deg=phase["p0_deg"],p1_deg=phase["p1_deg"],
            phase_direction="inverse" if phase["inverse_phase"] else "direct",phase_method=phase["method"],
            line_broadening_hz=float(meta["parameters"]["line_broadening_hz"] or 0),
            zero_fill_points=len(data["ppm"]),spectrum_csv=display_path,
            dataset_display_name=meta["dataset_display_name"],processing_settings=meta["parameters"])
        self._load_source(Path(meta["source_path"]),result)
        np.testing.assert_array_equal(self.model.fft_spectrum,data["unphased"],
            err_msg="GUI must preview the exact retained production FFT")
        self.model.dataset_display_name=meta["dataset_display_name"]
        self.output_directory=(self.acquisition_dir/"manual_review/checkpoints" if (self.acquisition_dir/'00_metadata.json').exists()
                               else self.acquisition_dir/"manual_reviews")
        self._copy_automated_to_manual()
        self.automated_plot.setTitle("DX metadata phase — Before Baseline")
        self.manual_plot.setTitle("Manual — Phase Preview, Before Baseline")
        self.run_button.setEnabled(False)
        self.run_button.setToolTip("Saved validation result is retained; this viewer does not rebuild it")
        self.save_review_button.setText("Accept current phase as manual review")
        self.manual_analysis_button.setText("Save checkpoint + rerun analysis")
        self.status.setText("Phase previews share the same FFT and precede baseline correction. Save a checkpoint to rerun the full peak analysis.")
        status,review_paths=manual_status(self.acquisition_dir)
        self.checkpoint_label.setText('MANUAL REVIEW PENDING — unsaved controls' if status=='NOT REVIEWED'
            else display_review_status(status)+' — current controls start from DX metadata phase')
        self.setWindowTitle(f"{meta['dataset_display_name']} | {meta['actual_acquisition_timestamp']} | Manual phase review")
        for name,curve in (('acme',self.acme_curve),('peak_minima',self.peak_minima_curve)):
            path=self.acquisition_dir/'analysis'/name/'spectral_evidence.npz'
            if path.exists():
                with np.load(path) as result:
                    np.testing.assert_array_equal(result['ppm'],self.model.ppm)
                    np.testing.assert_array_equal(result['unphased'],self.model.fft_spectrum)
                    curve.setData(result['ppm'],result['phased'].real)
            else:curve.setData([],[])
        self.method_overlay.setEnabled((self.acquisition_dir/'analysis/acme/spectral_evidence.npz').exists())
        self._toggle_methods(self.method_overlay.isChecked())
        self._load_additional_methods()
        self.fixed_methods=saved_methods(self.acquisition_dir)
        self.start_selector.blockSignals(True);self.start_selector.setCurrentIndex(0);self.start_selector.blockSignals(False)
        self._start_from_selected()
        for curve in (*self.extra_method_curves.values(),self.acme_curve,self.peak_minima_curve):curve.hide()
        original=Path(meta['parameters']['paths'][0]).name
        self.source_identity=f"FID {meta['acquisition_id']} | LONG DATE {meta['actual_acquisition_timestamp']}\n{original}\nSHA256 {meta['raw_sha256']}"
        self.overlay_plot.setTitle(meta['dataset_display_name']+' Real — fixed automatic / live manual')
        self.imaginary_plot.setTitle(meta['dataset_display_name']+' Imaginary — fixed automatic / live manual')
        self._refresh_information()
        self.loaded_checkpoint=review_paths[-1].parent if review_paths else None
        self.open_comparison_button.setEnabled(bool(self.loaded_checkpoint and (self.loaded_checkpoint/'manual_vs_all_methods.csv').exists()))
        self._prepare_absorption_acquisition()
        self._set_view(None)
        # Show actual target extrema, rather than clipping them by percentile.
        traces=((self.original_plot,self.model.original_spectrum),
                (self.automated_plot,self.automated_trace[1]),
                (self.manual_plot,self.model.phased(*self._current_values())))
        mask=(self.model.ppm>=5.6)&(self.model.ppm<=6.0)
        values=np.concatenate([np.asarray(y)[mask] for _,y in traces])
        low,high=float(values.min()),float(values.max());pad=.08*max(high-low,1)
        for plot,_ in traces:plot.setYRange(low-pad,high+pad)
        if hasattr(self,'_phase_history'):
            self._comparison_pins.clear();self._basic_demo_target=None;self._basic_reference_selection.clear()
            self._phase_history=[];self._history_suspended=False;self._commit_phase_context()
            self.basic_comparison.blockSignals(True);self.basic_comparison.setCurrentIndex(0);self.basic_comparison.blockSignals(False)
            self._sync_basic_controls()

    def _refresh_information(self):
        super()._refresh_information()
        if hasattr(self,'source_identity'):
            self.information.setPlainText(self.source_identity+'\n\n'+self.information.toPlainText())

    def _populate_selector(self,*_):
        current=self.acquisition_dir
        self.acquisition_selector.blockSignals(True)
        self.acquisition_selector.clear()
        selected=0
        for record in self.records:
            if self.critical_only.isChecked() and not record.get('june09_critical'):continue
            label=f"{record['timestamp']} | {record['acquisition_id']}"+(' [June9 critical]' if record.get('june09_critical') else '')
            folder=record['gallery_folder']
            self.acquisition_selector.addItem(label,folder)
            if Path(folder).resolve()==current:selected=self.acquisition_selector.count()-1
        self.acquisition_selector.setCurrentIndex(selected)
        self.acquisition_selector.blockSignals(False)
        if self.acquisition_selector.count() and Path(self.acquisition_selector.currentData()).resolve()!=current:
            self._select_acquisition(selected)

    def _select_acquisition(self,index):
        if index<0:return
        folder=self.acquisition_selector.itemData(index)
        try:self._load_validation_acquisition(folder)
        except Exception as exc:QtWidgets.QMessageBox.critical(self,'Could not load acquisition',str(exc))

    def _toggle_methods(self,checked):
        self.acme_curve.setVisible(checked)
        self.peak_minima_curve.setVisible(checked)
        if checked:self.overlay_checkbox.setChecked(True)

    def _load_additional_methods(self):
        from chemyx_lab.analysis.additional_phase_methods import LABELS,COLORS
        self.phase_method_selector.blockSignals(True)
        self.phase_method_selector.clear()
        self.phase_method_selector.addItem('All available automatic methods','all')
        available=[]
        metadata=self.acquisition_dir/'additional_phase_methods.json'
        methods=json.loads(metadata.read_text())['implemented_spectra'] if metadata.exists() else ['production','acme','peak_minima']
        for name in methods:
            if name=='unphased':continue
            path=self.acquisition_dir/'analysis'/name/'spectral_evidence.npz'
            if not path.is_file():continue
            if name not in self.extra_method_curves:
                self.extra_method_curves[name]=self.overlay_plot.plot(pen=phase4.pg.mkPen(COLORS.get(name,'#333333'),width=1),name=DISPLAY.get(name,name),autoDownsample=True)
            with np.load(path) as arrays:
                np.testing.assert_array_equal(arrays['ppm'],self.model.ppm)
                np.testing.assert_array_equal(arrays['unphased'],self.model.fft_spectrum)
                self.extra_method_curves[name].setData(arrays['ppm'],arrays['phased'].real)
            self.phase_method_selector.addItem(DISPLAY.get(name,name),name);available.append(name)
        self.available_overlay_methods=available
        for name,curve in self.extra_method_curves.items():curve.setVisible(False)
        self.phase_method_selector.blockSignals(False)
        self.phase_availability_label.setText(f'{len(available)} automatic results available. Manual stays separate.')
        self.selected_method_overlay.setEnabled(bool(available))
        self._toggle_selected_methods()

    def _toggle_selected_methods(self,*_):
        selected=self.phase_method_selector.currentData()
        show=self.selected_method_overlay.isChecked()
        # Keep every method name visible in the expanded comparison.
        self.overlay_plot.plotItem.legend.setColumnCount(3 if show else 1)
        for name,curve in self.extra_method_curves.items():
            curve.setVisible(show and name in self.available_overlay_methods and selected in ('all',name))
        if show:self.overlay_checkbox.setChecked(True)
        if show:
            low,high=sorted(self.original_plot.viewRange()[0])
            mask=(self.model.ppm>=low)&(self.model.ppm<=high)
            visible=[]
            for curve in [self.overlay_original,self.overlay_automated,self.overlay_manual,*self.extra_method_curves.values()]:
                if curve.isVisible():
                    _,y=curve.getData()
                    if y is not None and len(y)==len(mask):visible.extend(np.asarray(y)[mask])
            if visible:
                bottom,top=float(min(visible)),float(max(visible));pad=.08*max(top-bottom,1)
                self.overlay_plot.setYRange(bottom-pad,top+pad)

    def _save_phase_review(self):
        self._recalculate_manual()

    def _recalculate_manual(self):
        if self._busy:return
        p0,p1,pivot=self._current_values()
        try:
            meta=json.loads((self.acquisition_dir/'automated/processing_metadata.json').read_text())
            if sha256(self.model.path)!=meta['raw_sha256']:
                raise ValueError('Displayed FID does not match selected acquisition')
            reviewer=self.reviewer
            if not reviewer:
                reviewer,ok=QtWidgets.QInputDialog.getText(self,"Reviewer identity","Reviewer/operator (optional):")
                if not ok: return
            reviewer=reviewer.strip() or None
            notes,ok=QtWidgets.QInputDialog.getMultiLineText(self,"Review notes","Optional notes:")
            if not ok: return
            self._busy=True
            self.save_review_button.setEnabled(False)
            self.manual_analysis_button.setEnabled(False)
            self.acquisition_selector.setEnabled(False)
            self.critical_only.setEnabled(False)
            self.status.setText('Saving checkpoint and rerunning the same peak/QC path and date completion sequence. Please wait…')
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            QtWidgets.QApplication.processEvents()
            self.last_manual_output=self.save_checkpoint(reviewer,notes)
            self.loaded_checkpoint=self.last_manual_output
            self.checkpoint_label.setText(display_review_status(manual_status(self.acquisition_dir)[0])+' | '+self.loaded_checkpoint.name)
            self.open_comparison_button.setEnabled(True)
            self.status.setText(f"Saved {self.loaded_checkpoint}: manual plot/CSV, peak comparison and date-sequence completion replay retained.")
            self._refresh_information()
            self._view_final_spectrum()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self,"Checkpoint failed",str(exc))
        finally:
            if self._busy:
                self._busy=False
                QtWidgets.QApplication.restoreOverrideCursor()
                self.save_review_button.setEnabled(True)
                self.manual_analysis_button.setEnabled(True)
                self.acquisition_selector.setEnabled(True)
                self.critical_only.setEnabled(True)
                self._refresh_traces()

    def load_checkpoint(self):
        filename,_=QtWidgets.QFileDialog.getOpenFileName(self,"Load validation checkpoint",
            str(self.output_directory),"Checkpoint (checkpoint.json)")
        if not filename:return
        self._load_checkpoint_file(filename)

    def _load_checkpoint_file(self,filename):
        try:
            payload=json.loads(Path(filename).read_text())
            meta=json.loads((self.acquisition_dir/"automated/processing_metadata.json").read_text())
            if payload["raw_sha256"]!=meta["raw_sha256"]:raise ValueError("Checkpoint belongs to another acquisition")
            phase=payload["manual_phase"]
            # Convert any exact-k/N CLI checkpoint to the GUI's preserved pivot mapping.
            p0=phase["effective_index_zero_p0_deg"]+phase["p1_deg"]*self.model.pivot_fraction(phase["pivot_ppm"])
            self._set_phase_controls(phase4.PhaseCandidate(Path(filename).parent.name,p0,phase["p1_deg"],phase["pivot_ppm"]))
            self.loaded_checkpoint=Path(filename).parent
            self.starting_method='loaded_checkpoint:'+Path(filename).parent.name
            self.starting_phase=dict(zip(('p0_deg','p1_deg','pivot_ppm'),self._current_values()))
            self.open_comparison_button.setEnabled((self.loaded_checkpoint/'manual_vs_all_methods.csv').exists())
            self._load_manual_final()
            index=self.baseline_review.findData(payload.get('baseline_review','not_reviewed'))
            self.baseline_review.setCurrentIndex(max(index,0))
            self.checkpoint_label.setText("Current checkpoint: "+self.loaded_checkpoint.name)
            self._refresh_traces()
            self._commit_phase_context()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self,"Cannot load checkpoint",str(exc))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("acquisition_dir",type=Path,nargs='?')
    parser.add_argument('--validation-dir',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--acquisition',help='Acquisition ID; raw path and DX metadata phase are resolved automatically')
    parser.add_argument("--reviewer")
    parser.add_argument("--smoke-screenshot",type=Path)
    args=parser.parse_args(argv)
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Offscreen Windows Qt can choose a symbol-only default font. Use a readable
    # installed family for both the normal viewer and its saved smoke capture.
    font_path=Path('C:/Windows/Fonts/arial.ttf')
    if font_path.is_file():
        font_id=QtGui.QFontDatabase.addApplicationFont(str(font_path))
        families=QtGui.QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QtGui.QFont(families[0], 9))
    records=discover_acquisitions(args.validation_dir)
    if args.acquisition:
        record=next((r for r in records if r['acquisition_id']==args.acquisition),None)
        if record is None:parser.error('Unknown acquisition ID')
        folder=Path(record['gallery_folder'])
    elif args.acquisition_dir:folder=args.acquisition_dir
    else:
        record=next((r for r in records if r['timestamp'].startswith('2026-06-09T11:08:23')),records[0])
        folder=Path(record['gallery_folder'])
    window=ValidationPhaseWindow(folder,args.reviewer,args.validation_dir)
    window.show()
    if args.smoke_screenshot:
        def capture():
            args.smoke_screenshot.parent.mkdir(parents=True,exist_ok=True)
            window.grab().save(str(args.smoke_screenshot))
            app.quit()
        QtCore.QTimer.singleShot(1000,capture)
    return app.exec()

if __name__=="__main__":
    raise SystemExit(main())
