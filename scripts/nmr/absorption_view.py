"""Chemist-facing display controls over immutable saved analysis evidence."""
from pathlib import Path
import numpy as np
from PySide6 import QtCore,QtWidgets
import phase4
from chemyx_lab.analysis import phase_gallery as g,nmr_validation as v
from chemyx_lab.analysis.manual_phase_workflow import AUTOMATIC,DISPLAY
from chemyx_lab.analysis.nmr_spectrum_view import processed_view,load_processed_view,target_peak
from chemyx_lab.analysis.plot_titles import format_dataset_plot_title
from chemyx_lab.analysis.phase_labels import LEGEND, DX_METADATA_HELP


class ChemistSpectrumView:
    def _install_absorption_view(self):
        self.processed_methods={};self.saved_manual_view=None;self._phase_checks=None
        self.addToolBarBreak()
        views=self.addToolBar('Spectrum View and Diagnostics')
        views.addWidget(QtWidgets.QLabel('Spectrum View: '))
        self.spectrum_view=QtWidgets.QComboBox()
        for label,key,help_text in (
            ('Real / Absorption','real','Primary phased NMR spectrum used for normal interpretation and downstream analysis.'),
            ('Imaginary diagnostic','imaginary','Quadrature channel used to inspect phase quality and residual absorption-like contamination.'),
            ('Real + Imaginary diagnostic','complex','Shows both complex channels together for detailed phase inspection.'),
            ('Magnitude diagnostic','magnitude','√(Real² + Imaginary²). Phase-insensitive diagnostic; not the default quantitative spectrum.')):
            self.spectrum_view.addItem(label,key)
            self.spectrum_view.setItemData(self.spectrum_view.count()-1,help_text,QtCore.Qt.ToolTipRole)
        views.addWidget(self.spectrum_view)
        views.addWidget(QtWidgets.QLabel('Diagnostics: '))
        self.diagnostics_button=QtWidgets.QPushButton('Show Phase Diagnostics');self.diagnostics_button.setCheckable(True)
        self.diagnostics_button.toggled.connect(lambda on:self.spectrum_view.setCurrentIndex(2 if on else 0))
        views.addWidget(self.diagnostics_button)
        quality=QtWidgets.QPushButton('Phase quality metrics [saved]');quality.clicked.connect(self._show_phase_quality)
        views.addWidget(quality)
        self.spectrum_view.currentIndexChanged.connect(self._apply_spectrum_view)
        self.addToolBarBreak()
        compare=self.addToolBar('Comparison shortcuts')
        compare.addWidget(QtWidgets.QLabel('Comparison: '))
        self.solo_selector=QtWidgets.QComboBox()
        for name in ('unphased',*AUTOMATIC,'manual'):self.solo_selector.addItem(DISPLAY[name],name)
        self.solo_selector.setCurrentIndex(8);compare.addWidget(self.solo_selector)
        for label,names in [('Solo selected',None),('Clear comparisons',('manual',)),
                             ('DX metadata + Manual',('production','manual')),('Show All automatic methods',AUTOMATIC)]:
            button=QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _checked=False,n=names:self._set_comparisons((self.solo_selector.currentData(),) if n is None else n))
            compare.addWidget(button)
        self.addToolBarBreak()
        baseline=self.addToolBar('Baseline / Process / Analyze')
        baseline.addWidget(QtWidgets.QLabel('Baseline / Analysis: '))
        self.processing_view=QtWidgets.QComboBox()
        self.processing_view.addItem('Before baseline — PHASE','before')
        self.processing_view.addItem('Baseline — saved PROCESS','baseline')
        self.processing_view.addItem('After baseline / Final — saved PROCESS','final')
        baseline.addWidget(self.processing_view)
        self.processing_view.currentIndexChanged.connect(self._processing_changed)
        self.baseline_overlay=QtWidgets.QCheckBox('Show fitted baseline')
        self.baseline_overlay.toggled.connect(self._refresh_absorption_traces);baseline.addWidget(self.baseline_overlay)
        baseline.addWidget(QtWidgets.QLabel('Baseline review: '))
        self.baseline_review=QtWidgets.QComboBox()
        self.baseline_review.addItem('Not reviewed','not_reviewed')
        self.baseline_review.addItem('Accept unchanged','accepted_unchanged')
        self.baseline_review.addItem('Flag for review','flagged_for_review')
        self.baseline_review.setToolTip('Recorded with your next checkpoint. Display/review only; numerical baseline parameters are unchanged.')
        baseline.addWidget(self.baseline_review)
        self.view_final_button=QtWidgets.QPushButton('View Final Spectrum')
        self.view_final_button.clicked.connect(self._view_final_spectrum);baseline.addWidget(self.view_final_button)
        self.analysis_label=QtWidgets.QLabel('PHASE: live manual Real / Absorption before baseline; save to rerun analysis.')
        self.analysis_label.setWordWrap(True)
        self.centralWidget().layout().insertWidget(2,self.analysis_label)
        self.raw_context_curve=self.overlay_plot.plot(pen=phase4.pg.mkPen('#777777',width=1),autoDownsample=True)
        self.baseline_context_curve=self.overlay_plot.plot(pen=phase4.pg.mkPen('#D55E00',width=1,style=QtCore.Qt.DashLine),autoDownsample=True)
        self.analysis_markers=[]
        self.trace_checks['unphased'].setText('Unphased FFT — BEFORE phase correction')
        self.trace_checks['unphased'].setToolTip('Exact saved complex FFT before PHC0/PHC1 or any other phase rotation. Visual reference only; analysis uses the saved manual phase.')
        self.trace_checks['production'].setToolTip(DX_METADATA_HELP)
        self._apply_spectrum_view()

    def _prepare_absorption_acquisition(self):
        meta=g.json_read(self.acquisition_dir/'automated/processing_metadata.json')
        self.processed_methods={}
        for name,arrays in self.fixed_methods.items():
            folder=self.acquisition_dir/'analysis'/name
            row=arrays['metrics']
            target=(g.json_read(folder/'target_result.json') if (folder/'target_result.json').exists() else
                    {'peak_ppm':row['target_peak_ppm'],'height':row['peak_height'],'area':row['peak_area'],
                     'snr':row['snr'],'detected':row['detected']})
            peaks=v.read_rows(folder/'peak_results.csv') if (folder/'peak_results.csv').exists() else []
            self.processed_methods[name]=processed_view(arrays,target=target,peaks=peaks,
                parameters=meta['parameters'],source_label=DISPLAY[name]+' [saved]')
        self.saved_manual_view=None
        if self.loaded_checkpoint:self._load_manual_final()
        self._phase_checks=None
        for widget,index in ((self.processing_view,0),(self.spectrum_view,0),(self.baseline_review,0)):
            widget.blockSignals(True);widget.setCurrentIndex(index);widget.blockSignals(False)
        self.baseline_overlay.setChecked(False)
        self._set_comparisons(('manual',))
        self._apply_spectrum_view()

    def _load_manual_final(self):
        if not self.loaded_checkpoint:return
        cp=g.json_read(self.loaded_checkpoint/'checkpoint.json')
        meta=g.json_read(self.acquisition_dir/'automated/processing_metadata.json')
        if cp['raw_sha256']!=meta['raw_sha256']:raise ValueError('Final spectrum belongs to another acquisition')
        self.saved_manual_view=load_processed_view(self.loaded_checkpoint,source_label='Manual checkpoint '+self.loaded_checkpoint.name)
        np.testing.assert_array_equal(self.saved_manual_view['ppm'],self.model.ppm)

    def _set_comparisons(self,names):
        for name,check in self.trace_checks.items():
            check.blockSignals(True);check.setChecked(name in names);check.blockSignals(False)
        self._refresh_absorption_traces()

    def _processing_changed(self,*_):
        if self.processing_view.currentData()!='before':
            if self._phase_checks is None:self._phase_checks=tuple(n for n,c in self.trace_checks.items() if c.isChecked())
            self._set_comparisons(('manual',) if self.saved_manual_view else ('production',))
        elif self._phase_checks is not None:
            old=self._phase_checks;self._phase_checks=None;self._set_comparisons(old)
        self._apply_spectrum_view()

    def _view_final_spectrum(self):
        if self.loaded_checkpoint:self._load_manual_final()
        self.spectrum_view.setCurrentIndex(0)
        self.processing_view.setCurrentIndex(2)
        self._processing_changed()

    def _apply_spectrum_view(self,*_):
        mode=self.spectrum_view.currentData()
        self.spectrum_view.setToolTip(self.spectrum_view.currentData(QtCore.Qt.ToolTipRole))
        self.imaginary_plot.setVisible(mode=='complex')
        self.diagnostics_button.blockSignals(True);self.diagnostics_button.setChecked(mode!='real');self.diagnostics_button.blockSignals(False)
        self._refresh_absorption_traces()

    def _analysis_view(self):
        return self.saved_manual_view or self.processed_methods.get('production')

    def _displayed_complex(self,name):
        if name=='unphased':return self.fixed_methods['production']['unphased']
        if name=='manual':
            return self._manual_complex() if self.processing_view.currentData()=='before' else self.saved_manual_view['phased'] if self.saved_manual_view else None
        return self.fixed_methods[name]['phased']

    def _refresh_absorption_traces(self,*_):
        if not getattr(self,'processed_methods',{}) or not self.fixed_methods:return
        mode=self.spectrum_view.currentData();stage=self.processing_view.currentData()
        active=self._analysis_view()
        self.overlay_plot.plotItem.legend.clear();self.imaginary_plot.plotItem.legend.clear()
        for name,check in self.trace_checks.items():
            if name=='manual':
                check.setText('Manual/current [live]' if stage=='before' else
                              'Manual [saved]' if self.saved_manual_view else 'Manual [not saved]')
                check.setToolTip('Live phase rotation before baseline' if stage=='before' else
                                 'Last saved manual processing evidence; unsaved phase edits are excluded')
            z=self._displayed_complex(name);show=check.isChecked() and z is not None
            if z is None:z=self.fixed_methods['production']['phased']
            if mode=='magnitude':y=np.abs(z)
            elif mode=='imaginary':y=z.imag
            elif stage=='before' or name=='unphased':y=z.real
            else:
                # The absent manual trace stays hidden until a checkpoint exists.
                saved=(self.saved_manual_view or self.processed_methods['production']) if name=='manual' else self.processed_methods[name]
                y=saved['baseline'] if stage=='baseline' else saved['final']
            self.real_curves[name].setData(self.model.ppm,y)
            self.imag_curves[name].setData(self.model.ppm,z.imag)
            self.real_curves[name].setVisible(show);self.imag_curves[name].setVisible(show)
            label=LEGEND.get(name,check.text())
            if stage!='before' and name=='manual':label='Manual [saved '+self.loaded_checkpoint.name+']' if self.loaded_checkpoint else 'Manual not saved'
            if show:
                self.overlay_plot.plotItem.legend.addItem(self.real_curves[name],label)
                self.imaginary_plot.plotItem.legend.addItem(self.imag_curves[name],label)
        for curve in (self.overlay_original,self.overlay_automated,self.overlay_manual):curve.hide()
        self.stacked_widget.hide()
        real_mode=mode in ('real','complex')
        self.raw_context_curve.setVisible(real_mode and stage=='baseline')
        self.baseline_context_curve.setVisible(real_mode and stage=='final' and self.baseline_overlay.isChecked())
        self.raw_context_curve.setData(active['ppm'],active['phased'].real)
        self.baseline_context_curve.setData(active['ppm'],active['baseline'])
        if self.raw_context_curve.isVisible():self.overlay_plot.plotItem.legend.addItem(self.raw_context_curve,'Raw phased Real [analysis source]')
        if self.baseline_context_curve.isVisible():self.overlay_plot.plotItem.legend.addItem(self.baseline_context_curve,'Analysis baseline [analysis source]')
        label={'real':'Real / Absorption Spectrum','complex':'Real / Absorption Spectrum',
               'imaginary':'Imaginary / Dispersion diagnostic','magnitude':'Magnitude — phase-insensitive diagnostic'}[mode]
        if real_mode and stage=='final':label='Final Processed Real / Absorption Spectrum'
        elif real_mode and stage=='baseline':label='Baseline inspection — Raw phased Real + Analysis baseline'
        self.overlay_plot.setTitle(format_dataset_plot_title(self.model.dataset_display_name,label))
        self.imaginary_plot.setTitle(format_dataset_plot_title(self.model.dataset_display_name,'Imaginary / Dispersion diagnostic'))
        can_save=stage=='before'
        if stage!='before' and self.saved_manual_view:
            cp=g.json_read(self.loaded_checkpoint/'checkpoint.json')['manual_phase']
            can_save=all(abs(a-float(cp[k]))<1e-10 for a,k in zip(self._current_values(),('p0_deg','p1_deg','pivot_ppm')))
        for widget in (*self.phase_controls,self.p0_slider,self.p1_slider,self.pivot,self.start_selector):widget.setEnabled(stage=='before' and not self._busy)
        self.save_review_button.setEnabled(can_save and not self._busy);self.manual_analysis_button.setEnabled(can_save and not self._busy)
        self._show_analysis_text('PHASE: live manual Real / Absorption before baseline. Save to rerun the unchanged analysis.' if stage=='before' else
            f"PROCESS / ANALYZE source: {active['source_label']}. Target ppm {active['target'].get('peak_ppm')}; "
            f"height {active['target'].get('height')}; area {active['target'].get('area')}; S/N {active['target'].get('snr')}; detection {active['target'].get('detected')}. "
            f"Exact regional quantitative Real inside {active['region_ppm'].min():.3f}–{active['region_ppm'].max():.3f} ppm; global correction elsewhere. "
            'Imaginary/magnitude stay pre-baseline diagnostics. '
            'Unsaved phase edits are retained; return Before baseline to edit. Baseline decisions are recorded with the next save. '
            'Target measurements use regional correction; completion replay separately uses the global-corrected trace and fixed-window integration.')
        self._draw_analysis_markers(active,real_mode and stage=='final')
        self._rescale_absorption_view()
        self._sync_basic_controls()
        self._sync_basic_reference_controls()

    def _draw_analysis_markers(self,view,visible):
        for item in self.analysis_markers:self.overlay_plot.removeItem(item)
        self.analysis_markers=[]
        if not visible:return
        p=view['parameters'];center=float(p.get('simple_target_ppm',5.8));width=float(p.get('simple_window_ppm',.1))
        for bound in (view['region_ppm'].min(),view['region_ppm'].max()):
            item=phase4.pg.InfiniteLine(float(bound),angle=90,pen=phase4.pg.mkPen('#777777',style=QtCore.Qt.DotLine))
            self.overlay_plot.addItem(item);self.analysis_markers.append(item)
        band=phase4.pg.LinearRegionItem((center-width,center+width),movable=False,brush=phase4.pg.mkBrush(230,200,0,20))
        self.overlay_plot.addItem(band);self.analysis_markers.append(band)
        for row in view['peaks']:
            color='#009E73' if v.truth(row.get('qc_pass')) else '#D55E00'
            item=phase4.pg.ScatterPlotItem([float(row['interpolated_ppm'])],[float(row['height'])],
                symbol='o' if v.truth(row.get('qc_pass')) else 'x',size=7,pen=color,brush=color)
            self.overlay_plot.addItem(item);self.analysis_markers.append(item)
        peak=target_peak(view)
        if peak:
            for key in ('integration_left_ppm','integration_right_ppm'):
                item=phase4.pg.InfiniteLine(float(peak[key]),angle=90,pen=phase4.pg.mkPen('#009E73',style=QtCore.Qt.DashLine))
                self.overlay_plot.addItem(item);self.analysis_markers.append(item)

    def _rescale_absorption_view(self,*_):
        if not getattr(self,'processed_methods',{}):return
        lo,hi=sorted(self.overlay_plot.viewRange()[0]);mask=(self.model.ppm>=lo)&(self.model.ppm<=hi)
        if not mask.any():return
        for plot,curves in ((self.overlay_plot,[*self.real_curves.values(),self.raw_context_curve,self.baseline_context_curve]),
                            (self.imaginary_plot,list(self.imag_curves.values()))):
            values=[c.getData()[1][mask] for c in curves if c.isVisible() and c.getData()[1] is not None]
            if values:
                y=np.concatenate(values);low,high=float(y.min()),float(y.max());pad=.08*max(high-low,1)
                plot.setYRange(low-pad,high+pad)

    def _show_phase_quality(self):
        dialog=QtWidgets.QDialog(self);dialog.setWindowTitle('Phase quality metrics — saved evidence');dialog.resize(1200,410)
        layout=QtWidgets.QVBoxLayout(dialog);table=QtWidgets.QTableWidget(7,8)
        table.setHorizontalHeaderLabels(['Saved method','Negative fraction','Even imaginary','Odd Real / lobes',
                                        'Total imaginary residual','Asymmetry','Baseline trend penalty','Optimizer status'])
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for i,name in enumerate(AUTOMATIC):
            row=self.fixed_methods[name]['metrics']
            status=row.get('optimizer_status','retained')
            original=self.validation_dir/'acquisitions'/self.acquisition_dir.name/'alternative_methods'/name/'optimizer.json'
            if original.is_file():
                status=('converged' if g.json_read(original).get('converged') else 'not converged')+' [original optimizer record]'
            for j,value in enumerate((DISPLAY[name],row['negative_area_fraction'],row['even_imaginary_fraction'],row['dispersive_metric'],
                                      row['imaginary_residual'],row['peak_asymmetry'],row['baseline_penalty'],status)):
                table.setItem(i,j,QtWidgets.QTableWidgetItem(str(value)))
        table.resizeColumnsToContents();layout.addWidget(table)
        info=QtWidgets.QLabel('Diagnostic indicators, not proof of correctness. Total imaginary includes expected dispersion; do not minimize it to zero. '
            'Combined/Symmetry directly optimize several displayed metrics; ACME penalizes negative Real. Pooled scores mainly reflect strong off-target resonances. '
            'Convergence does not establish physical accuracy. Unknown frozen-summary status is missing provenance, not failure; original optimizer records are shown when available. '
            'Manual metrics are available after an explicit save in Open manual comparison.')
        info.setWordWrap(True);layout.addWidget(info);self.quality_dialog=dialog;dialog.show()
