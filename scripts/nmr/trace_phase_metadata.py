"""Inspect raw-FID phase fields and produce a same-FID representative trace."""
import _bootstrap  # noqa: F401
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from chemyx_lab.analysis.nmr_validation import (DEFAULT_OUTPUT,load_cohort,analyze,
    phase_field_reconciliation,historical_candidates,production_args,alternative_phases)
from chemyx_lab.analysis.nmr import read_jcamp_fid
from chemyx_lab.analysis.phase_audit import write_rows,write_json,save_figure,sha256


def build_trace():
    root=DEFAULT_OUTPUT
    cohort=load_cohort()
    rows=[]
    for a in cohort:
        result=analyze(a['source_path'])
        rows.append({'acquisition_id':a['acquisition_id'],**phase_field_reconciliation(
            result['spectrum'].metadata,result['unphased'])})
    write_rows(root/'tables/phase_field_reconciliation.csv',rows)
    manifest=[]
    representatives=[next(a for a in cohort if a['timestamp'].startswith('2026-06-09T11:08:23')),
                     next(a for a in cohort if a['timestamp'].startswith('2026-08-10T17:27:21'))]
    for a in representatives:
        result=analyze(a['source_path']);s=result['spectrum'];fid=read_jcamp_fid(s.source)
        destination=root/'documentation/representative_trace'/a['acquisition_id']
        destination.mkdir(parents=True,exist_ok=True)
        historical=None; history=historical_candidates(a)
        if history:historical=analyze(s.source,production_args(s.source,history[0][2]['parameters']))
        candidates=alternative_phases(result)
        acme=next(r for r,m in candidates if m['method']=='acme')
        manual=None
        reviews=root/'acquisitions'/a['acquisition_id']/'manual_reviews'
        if reviews.exists():
            paths=sorted(reviews.glob('review_*/spectral_evidence.npz'))
            if paths:
                with np.load(paths[-1]) as data:manual=np.asarray(data['phased']).copy()
        columns=[s.ppm_axis,result['unphased'].real,result['unphased'].imag,s.real,s.imaginary,result['quantitative']]
        names=['ppm','fft_no_phase_real','fft_no_phase_imaginary','stored_metadata_phased_real',
               'stored_metadata_phased_imaginary','production_baseline_corrected_real']
        if historical:
            columns.append(historical['quantitative']);names.append('historical_parameter_reconstruction_real')
        if acme:
            columns.extend([acme['spectrum'].real,acme['quantitative']]);names.extend(['acme_phased_real','acme_baseline_corrected_real'])
        if manual is not None:
            columns.append(manual.real);names.append('saved_exploratory_reference_phased_real')
        np.savetxt(destination/'same_fid_frequency_trace.csv',np.column_stack(columns),delimiter=',',fmt='%.17g',
                   header=','.join(names),comments='')
        sweep=float(fid.metadata['$SWH'])
        np.savetxt(destination/'raw_complex_fid.csv',np.column_stack((np.arange(len(fid.real))/sweep,fid.real,fid.imag)),
                   delimiter=',',fmt='%.17g',header='time_seconds,raw_fid_real,raw_fid_imaginary',comments='')
        fig,axes=plt.subplots(2,3,figsize=(15,8),layout='constrained')
        axes[0,0].plot(np.arange(len(fid.real))/sweep,fid.real,lw=.5,label='Real FID')
        axes[0,0].plot(np.arange(len(fid.imag))/sweep,fid.imag,lw=.5,label='Imaginary FID')
        axes[0,0].set(title='Actual raw time-domain FID',xlabel='Time (s)',ylabel='FID amplitude (a.u.)')
        axes[0,0].legend(fontsize=7)
        curves=[(result['unphased'].real,'FFT with NO phase correction'),
                (s.real,f'FFT + stored PHC0={s.phase0_deg:g}, PHC1={s.phase1_deg:g}'),
                (historical['quantitative'] if historical else result['quantitative'],
                 'Historical parameter reconstruction + baseline'),
                (acme['spectrum'].real if acme else s.real,'Independent ACME, before baseline')]
        for ax,(y,title) in zip(list(axes.flat)[1:5],curves):
            ax.plot(s.ppm_axis,y,lw=.8);ax.set(title=title,xlim=(6.5,5),xlabel='Chemical shift (ppm)',ylabel='Intensity (a.u.)')
            local=np.asarray(y)[(s.ppm_axis>=5)&(s.ppm_axis<=6.5)]
            span=max(float(np.ptp(local)),1)
            ax.set_ylim(float(local.min())-.08*span,float(local.max())+.08*span)
            ax.axhline(0,color='gray',lw=.5)
        if manual is not None:
            axes[1,2].plot(s.ppm_axis,s.real,label='Stored phase',lw=.8)
            axes[1,2].plot(s.ppm_axis,manual.real,label='Saved exploratory reference',lw=.8)
            axes[1,2].set(title='Same FID; saved phase reference (reviewer unknown)',xlim=(6.5,5),xlabel='Chemical shift (ppm)',ylabel='Intensity (a.u.)')
            axes[1,2].legend(fontsize=7)
            mask=(s.ppm_axis>=5)&(s.ppm_axis<=6.5)
            local=np.concatenate((np.asarray(s.real)[mask],manual.real[mask]));span=max(float(np.ptp(local)),1)
            axes[1,2].set_ylim(float(local.min())-.08*span,float(local.max())+.08*span)
        else:
            axes[1,2].axis('off');axes[1,2].text(.05,.8,'Identified manual expert review pending.\nNo manual correction is fabricated.',transform=axes[1,2].transAxes)
        save_figure(fig,destination/'raw_fid_to_phase_trace',a['dataset_display_name'],
                    'Raw FID → Unphased FFT → Stored Phase → Historical Reconstruction',manifest)
        write_json(destination/'trace_provenance.json',{
            'source_path':a['source_path'],'raw_sha256':a['raw_sha256'],'actual_timestamp':a['timestamp'],
            'data_type':fid.metadata.get('DATA TYPE'),'complex_points':len(fid.real),
            'complex_sample_dwell_seconds':1/sweep,'last_sample_seconds':(len(fid.real)-1)/sweep,
            'pre_fft_processing':'0.03 Hz exponential apodization and 65536 point zero fill; raw FID CSV has neither',
            'frequency_trace_csv_columns':names,'phase_field_comparison':phase_field_reconciliation(s.metadata,result['unphased']),
            'historical_context':'reconstruction with saved parameters; no independent original frequency trace inferred',
            'manual_status':'historical exploratory settings with unidentified reviewer' if manual is not None else 'pending human expert review'})
    write_rows(root/'documentation/representative_trace/figure_manifest.csv',manifest)
    equivalent=sum(bool(r.get('equivalent_centered_direct_convention')) for r in rows)
    doc=f'''# Raw FID and phase-field reconciliation

All {len(rows)} cohort files are time-domain NMR FID exports. The numerical
REAL/IMAGINARY tables describe the complex FID, not an already phased spectrum.
There is no separate frequency-domain trace established by this inventory.
Production decoding applies FACTOR, then apodizes, zero-fills, FFTs, and only
then applies accompanying phase metadata.

Production reads `$PHC0` and `$PHC1` directly. It does not read
`$PHASECORRECTION`, `$PIVOT`, `$PH_mod`, or the commented NMReady `.PHASE` lines
to calculate phase. No transformation between these fields occurs in Python.
The transform is already encoded in the two exported phase representations.

For the June9 example: `$PHASECORRECTION=(-20,+79)` and `PHC0=+59.5`,
`PHC1=-79`. The byte-level values obey:

`PHC0 = -PHASECORRECTION_P0 + PHASECORRECTION_P1/2`

`PHC1 = -PHASECORRECTION_P1`

The production inverse rotation therefore applies phase angle
`-59.5 +79*k/N = -20 +79*(k/N-1/2)` degrees. This is exactly the same
as a direct rotation with -20 degrees at the FFT midpoint and a +79 degree
ramp across the full sweep. {equivalent}/{len(rows)} acquisitions satisfy the
same numerical identity within relative complex-spectrum tolerance 1e-10.
Individual residuals and maximum array errors are in
`tables/phase_field_reconciliation.csv`.

This establishes different sign/pivot parameterizations algebraically. It does
not establish the undocumented vendor meaning of `$PIVOT=0` or `$PH_mod=1`,
whether a value was chosen manually in instrument display software, or which
exporter stage calculated it. Those require the NMReady export specification
or confirmation from Nanalysis; no Python reader of those fields was found.
Do not claim phase values were independently optimized by our stored method.

The vendor's [phase-correction article](https://www.nanalysis.com/nmready-blog/2022/3/28/nmr-data-processing-phase-correction)
explains zero/first-order phase and pivot conceptually but does not specify
this JCAMP field conversion. [nmrglue's phase API](https://nmrglue.readthedocs.io/en/latest/reference/generated/nmrglue.process.proc_base.ps.html)
documents degree units and inverse correction; the exact installed 0.11 source
was inspected and governs the validation equation.

Two representative packages accompany this guide: June9 11:08:23 (the saved
retrospective completion criterion) and August10 17:27:21 (an acquisition with
saved September exploratory phase settings). Each retains the raw complex FID,
unphased FFT, exact stored-metadata FFT, historical-parameter reconstruction,
independent ACME correction and available same-FID manual/reference correction.
An identified expert manual phase does not exist for the June9 example; its
manual panel explicitly remains pending. Historical processed peak tables
are compared numerically; reconstructed spectra are not relabelled as an
independently retained original historical spectrum.
'''
    (root/'documentation/RAW_FID_PHASE_FIELDS.md').write_text(doc,encoding='utf-8')
    print(f'Phase-field equivalence: {equivalent}/{len(rows)} acquisitions')

if __name__=='__main__':build_trace()
