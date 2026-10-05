"""Basic/Advanced presentation and reversible manual phase editing."""
from copy import deepcopy
from pathlib import Path
from PySide6 import QtWidgets
import phase4
from chemyx_lab.analysis.manual_phase_workflow import AUTOMATIC, DISPLAY
from chemyx_lab.analysis.phase_gallery import manual_status
from chemyx_lab.analysis.phase_labels import DX_METADATA_HELP


class ChemistBasicView:
    def _install_basic_view(self):
        self._phase_history=[];self._history_suspended=False
        self._last_phase_state=self._phase_state()
        self._diagnostic_index=0
        self._comparison_pins=set();self._basic_demo_target=None;self._basic_reference_selection=set()
        self._advanced_bars=[bar for bar in self.findChildren(QtWidgets.QToolBar) if bar.windowTitle() in {
            'Real and imaginary traces','Manual start and precision controls','Trace provenance',
            'Spectrum View and Diagnostics','Comparison shortcuts'}]
        self.basic_bar=QtWidgets.QToolBar('Basic phase workflow',self)
        first=self.findChildren(QtWidgets.QToolBar)[0]
        self.insertToolBar(first,self.basic_bar)
        self.insertToolBarBreak(first)
        self.advanced_toggle=QtWidgets.QCheckBox('Advanced controls')
        self.advanced_toggle.setToolTip('Open pivot, exact phase entries, complex diagnostics, all saved comparisons and provenance')
        self.basic_bar.addWidget(self.advanced_toggle)
        self.basic_bar.addWidget(QtWidgets.QLabel('Saved comparator: '))
        self.basic_comparison=QtWidgets.QComboBox()
        self.basic_comparison.addItem('None — single spectrum',None)
        for name in AUTOMATIC:self.basic_comparison.addItem(DISPLAY[name]+' [saved]',name)
        self.basic_comparison.setItemData(1,DX_METADATA_HELP,phase4.QtCore.Qt.ToolTipRole)
        self.basic_comparison.currentIndexChanged.connect(self._basic_comparison_changed)
        self.basic_bar.addWidget(self.basic_comparison)
        self.undo_button=QtWidgets.QPushButton('Undo phase')
        self.undo_button.clicked.connect(self._undo_phase);self.basic_bar.addWidget(self.undo_button)
        self.reset_start_button=QtWidgets.QPushButton('Reset to starting phase')
        self.reset_start_button.clicked.connect(self._reset_starting_phase);self.basic_bar.addWidget(self.reset_start_button)
        self.restore_latest_button=QtWidgets.QPushButton('Restore latest checkpoint')
        self.restore_latest_button.clicked.connect(self._restore_latest_checkpoint);self.basic_bar.addWidget(self.restore_latest_button)
        # The last saved numerical table remains accessible in Basic too.
        basic_compare=QtWidgets.QPushButton('Open manual comparison')
        basic_compare.clicked.connect(self._open_manual_comparison)
        self.basic_comparison_button=basic_compare;self.basic_bar.addWidget(basic_compare)
        # BEFORE is never hidden behind the Advanced toggle.
        self.phase_comparison_bar=QtWidgets.QWidget(self)
        self.phase_comparison_bar.setObjectName('Before/after phase demonstration')
        comparison_grid=QtWidgets.QGridLayout(self.phase_comparison_bar)
        comparison_grid.setContentsMargins(0,0,0,0)
        self.centralWidget().layout().insertWidget(0,self.phase_comparison_bar)
        self.before_fft_check=QtWidgets.QCheckBox('Unphased FFT — BEFORE phase correction')
        self.before_fft_check.setToolTip('Raw complex FID after the shared FFT processing, before PHC0/PHC1 or any automatic phase correction. Visual reference only.')
        self.metadata_phase_check=QtWidgets.QCheckBox('DX metadata phase')
        self.metadata_phase_check.setToolTip(DX_METADATA_HELP)
        for widget in (self.before_fft_check,self.metadata_phase_check):
            widget.toggled.connect(self._basic_reference_changed)
        comparison_grid.addWidget(self.before_fft_check,0,0,1,2)
        comparison_grid.addWidget(self.metadata_phase_check,0,2)
        self.before_only_button=QtWidgets.QPushButton('Show BEFORE only')
        self.before_metadata_button=QtWidgets.QPushButton('Show BEFORE vs DX metadata')
        self.before_manual_button=QtWidgets.QPushButton('Show BEFORE vs Manual')
        for column,(button,target) in enumerate(((self.before_only_button,'unphased'),(self.before_metadata_button,'production'),
                                                (self.before_manual_button,'manual'))):
            button.clicked.connect(lambda _checked=False,n=target:self._show_phase_before_after(n))
            button.setToolTip('Before-baseline Real / Absorption comparison only. Phase controls and saved analysis do not change. Explicitly checked Advanced automatic comparisons stay pinned.')
            comparison_grid.addWidget(button,1,column)
        self.phase_comparison_help=QtWidgets.QLabel(
            'Phase comparison: Unphased FFT = complex FID after FFT, before phase correction; '
            'DX metadata phase = PHC0/PHC1 stored in .dx; automatic methods = independently calculated corrections; '
            'Manual/current = your live phase. Comparisons do not select the analysis source.')
        self.phase_comparison_help.setWordWrap(True)
        self.centralWidget().layout().insertWidget(2,self.phase_comparison_help)
        for name,check in self.trace_checks.items():
            check.toggled.connect(lambda on,n=name:self._pin_advanced_comparison(n,on))
        self.copy_selected_button=QtWidgets.QPushButton('Copy selected automatic → Manual')
        self.copy_selected_button.clicked.connect(self._copy_selected_automatic)
        self.solo_selector.currentIndexChanged.connect(self._sync_basic_controls)
        self._advanced_bars[-1].addWidget(self.copy_selected_button)
        phase_parameters=QtWidgets.QPushButton('Saved phase parameters')
        phase_parameters.clicked.connect(self._show_saved_phase_parameters)
        self._advanced_bars[1].addWidget(phase_parameters)
        selected_manual=QtWidgets.QPushButton('Selected method + Manual')
        selected_manual.clicked.connect(lambda:self._set_comparisons((self.solo_selector.currentData(),'manual')))
        self._advanced_bars[-1].addWidget(selected_manual)
        clear=QtWidgets.QPushButton('Clear all comparisons')
        clear.clicked.connect(lambda:self._set_comparisons(()));self._advanced_bars[-1].addWidget(clear)
        self.basic_phase_controls=[]
        manual_group=next(group for group in self.findChildren(QtWidgets.QGroupBox) if group.title()=='Manual Phase')
        grid=manual_group.layout()
        for index in range(2):
            spin=QtWidgets.QDoubleSpinBox();spin.setRange(-100000,100000);spin.setDecimals(3);spin.setSingleStep(.1)
            spin.setToolTip('Phase in degrees; full-precision entry is available under Advanced controls')
            spin.valueChanged.connect(lambda value,i=index:self.phase_controls[i].setValue(value))
            grid.addWidget(spin,index,3);self.basic_phase_controls.append(spin)
        self._pivot_widgets=[grid.itemAtPosition(2,0).widget(),self.pivot]
        self.pivot.setToolTip('Advanced: moving pivot alone changes the physical phase. It does not preserve the current spectrum; reassess P0/P1.')
        self._detail_groups=[group for group in self.findChildren(QtWidgets.QGroupBox) if group.title() in {'Manual phasing checklist','Information'}]
        self._legacy_buttons=[button for button in self.findChildren(QtWidgets.QPushButton) if button.text() in {
            'Open .DX','Browse Runs','Open Phase Review','Run Automated Processing','Select Output Directory'}]
        self.reset_production_button=next(button for button in self.findChildren(QtWidgets.QPushButton) if button.text()=='Reset to stored phase')
        self.reset_production_button.setText('Reset to DX metadata phase')
        self.reset_production_button.setToolTip(DX_METADATA_HELP)
        self.copy_button.setText('Copy DX metadata phase → Manual')
        self.copy_button.setToolTip(DX_METADATA_HELP)
        self.advanced_toggle.toggled.connect(self._advanced_changed)
        self._advanced_changed(False)
        self._sync_basic_controls()

    def _phase_state(self):
        return {'values':self._current_values(),'starting_method':self.starting_method,
                'starting_phase':deepcopy(self.starting_phase)}

    def _commit_phase_context(self):
        if hasattr(self,'_last_phase_state'):self._last_phase_state=self._phase_state()

    def _track_phase_edit(self):
        if not hasattr(self,'_last_phase_state') or self._history_suspended:return
        state=self._phase_state()
        if state['values']!=self._last_phase_state['values']:
            self._phase_history.append(self._last_phase_state)
            self._phase_history=self._phase_history[-100:]
        self._last_phase_state=state
        self._sync_basic_controls()

    def _sync_basic_controls(self,*_):
        if not hasattr(self,'basic_phase_controls'):return
        before=self.processing_view.currentData()=='before' and not self._busy
        for spin,value in zip(self.basic_phase_controls,self._current_values()):
            spin.blockSignals(True);spin.setValue(value);spin.blockSignals(False);spin.setEnabled(before)
        self.undo_button.setEnabled(before and bool(self._phase_history))
        self.reset_start_button.setEnabled(before);self.reset_production_button.setEnabled(before)
        self.copy_button.setEnabled(before)
        self.copy_selected_button.setEnabled(before and self.solo_selector.currentData() in AUTOMATIC)
        self.restore_latest_button.setEnabled(bool(manual_status(self.acquisition_dir)[1]) and not self._busy)
        self.basic_comparison_button.setEnabled(self.open_comparison_button.isEnabled())

    def _undo_phase(self):
        if not self._phase_history or self.processing_view.currentData()!='before':return
        state=self._phase_history.pop();self._history_suspended=True
        try:
            self.starting_method=state['starting_method'];self.starting_phase=deepcopy(state['starting_phase'])
            self._set_phase_controls(phase4.PhaseCandidate('undo',*state['values']))
        finally:self._history_suspended=False
        self._commit_phase_context();self._sync_basic_controls()

    def _reset_starting_phase(self):
        p=self.starting_phase
        self._set_phase_controls(phase4.PhaseCandidate('starting phase',p['p0_deg'],p['p1_deg'],p['pivot_ppm']))

    def _copy_selected_automatic(self):
        name=self.solo_selector.currentData()
        if name not in AUTOMATIC:return
        self.processing_view.setCurrentIndex(0)
        index=self.start_selector.findData(name)
        self.start_selector.setCurrentIndex(index);self._start_from_selected()

    def _show_saved_phase_parameters(self):
        dialog=QtWidgets.QDialog(self);dialog.setWindowTitle('Saved automatic phase parameters — read only')
        dialog.resize(850,340);layout=QtWidgets.QVBoxLayout(dialog)
        table=QtWidgets.QTableWidget(len(self.fixed_methods),5)
        table.setHorizontalHeaderLabels(['Method','Stored P0 (degrees)','Stored P1 (degrees)','Direction','Pivot / convention'])
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for i,name in enumerate(self.fixed_methods):
            row=self.fixed_methods[name]['metrics']
            for j,value in enumerate((DISPLAY[name],row['P0'],row['P1'],row['direction'],row['convention'])):
                table.setItem(i,j,QtWidgets.QTableWidgetItem(str(value)))
        table.resizeColumnsToContents();layout.addWidget(table)
        help_text=QtWidgets.QLabel('Stored index-zero parameters apply to the same full FFT with k/N. Direct and inverse signs differ. '
            'Start manual controls from converts to the inverse GUI convention. Moving pivot alone changes physical phase.')
        help_text.setWordWrap(True);layout.addWidget(help_text);self.parameter_dialog=dialog;dialog.show()

    def _restore_latest_checkpoint(self):
        paths=manual_status(self.acquisition_dir)[1]
        if paths:self._load_checkpoint_file(paths[-1])

    def _basic_comparison_changed(self,*_):
        if not hasattr(self,'before_fft_check'):return
        selected=self.basic_comparison.currentData()
        source='manual' if self.processing_view.currentData()=='before' or self.saved_manual_view else 'production'
        if self.sender() is self.basic_comparison:self._basic_demo_target=None
        names={self._basic_demo_target or source,*self._comparison_pins,*self._basic_reference_selection}
        if selected:names.add(selected)
        self._set_comparisons(names)

    def _basic_reference_changed(self,on):
        if self._busy:self._sync_basic_reference_controls();return
        name='unphased' if self.sender() is self.before_fft_check else 'production'
        if on:self._basic_reference_selection.add(name)
        else:self._basic_reference_selection.discard(name)
        if on:self.processing_view.setCurrentIndex(0)
        if not on and self._basic_demo_target==name:
            self._basic_demo_target=None
        if not on and self.basic_comparison.currentData()==name:
            self.basic_comparison.blockSignals(True);self.basic_comparison.setCurrentIndex(0);self.basic_comparison.blockSignals(False)
        self._basic_comparison_changed()

    def _show_phase_before_after(self,target):
        if self._busy:return
        self.processing_view.setCurrentIndex(0)
        self.spectrum_view.setCurrentIndex(0)
        self.basic_comparison.blockSignals(True);self.basic_comparison.setCurrentIndex(0);self.basic_comparison.blockSignals(False)
        self._basic_demo_target=target
        self._basic_reference_selection={'unphased'}
        self._set_comparisons({'unphased',target,*self._comparison_pins})

    def _pin_advanced_comparison(self,name,on):
        if not self.advanced_toggle.isChecked():return
        if name in ('unphased','production'):
            if on:self._basic_reference_selection.add(name)
            else:
                self._basic_reference_selection.discard(name)
                if self._basic_demo_target==name:self._basic_demo_target=None
                if self.basic_comparison.currentData()==name:
                    self.basic_comparison.blockSignals(True);self.basic_comparison.setCurrentIndex(0);self.basic_comparison.blockSignals(False)
        if name in AUTOMATIC:
            if on:self._comparison_pins.add(name)
            else:self._comparison_pins.discard(name)

    def _sync_basic_reference_controls(self):
        if not hasattr(self,'before_fft_check'):return
        for widget,name in ((self.before_fft_check,'unphased'),(self.metadata_phase_check,'production')):
            widget.blockSignals(True);widget.setChecked(self.trace_checks[name].isChecked());widget.blockSignals(False)

    def _advanced_changed(self,on):
        for bar in self._advanced_bars:bar.setVisible(on)
        for widget in (*self._pivot_widgets,*self._detail_groups,*self._legacy_buttons,self.copy_button):widget.setVisible(on)
        if not on:
            self._diagnostic_index=self.spectrum_view.currentIndex();self.spectrum_view.setCurrentIndex(0)
            self._basic_comparison_changed()
        else:self.spectrum_view.setCurrentIndex(self._diagnostic_index)
        self._refresh_traces()

    def _show_analysis_text(self,detail):
        if not hasattr(self,'advanced_toggle') or self.advanced_toggle.isChecked():
            self.analysis_label.setText(detail);return
        if self.processing_view.currentData()=='before':
            self.analysis_label.setText('Manual / Absorption before baseline. Adjust P0/P1, then save to calculate the final analysis. '
                'Imaginary and magnitude, available under Advanced, are diagnostic views only.')
        else:
            view=self._analysis_view();target=view['target']
            def number(key):
                try:return f"{float(target.get(key)):.6g}"
                except (TypeError,ValueError):return 'not detected'
            self.analysis_label.setText(f"Analysis source: {view['source_label']}. Target {number('peak_ppm')} ppm; area {number('area')}; "
                f"S/N {number('snr')}; detected {target.get('detected')}. Regional correction inside "
                f"{view['region_ppm'].min():.1f}–{view['region_ppm'].max():.1f} ppm; global correction elsewhere. "
                'Completion uses a separate fixed-window calculation. Unsaved phase edits excluded.')
