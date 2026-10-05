"""Review artifacts from fixed automatic evidence and the existing manual analysis."""
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from . import nmr_validation as v, phase_gallery as g
from .phase_audit import write_json, write_rows
from .additional_phase_methods import COLORS, load_context
from .phase_quality import score_spectrum
from .phase_labels import DISPLAY, LEGEND

AUTOMATIC = ('production','acme','peak_minima','combined_objective_v1',
             'symmetry_objective','ernst_integral_p0','deep_phaser')


def saved_methods(folder):
    """No estimator calls: arrays and phase parameters must already exist."""
    folder=Path(folder)
    rows={r['method']:r for r in v.read_rows(folder/'all_phase_methods_results.csv')}
    out={}
    for method in AUTOMATIC:
        if method not in rows:
            continue  # A portable review may explicitly omit unavailable methods.
        with np.load(folder/'analysis'/method/'spectral_evidence.npz') as a:
            out[method]={k:a[k].copy() for k in a.files}
            out[method]['metrics']=rows[method]
    return out


def inverse_start(row, fraction):
    """Saved index-zero convention -> GUI inverse P0 at its selected pivot."""
    sign=1 if row['direction']=='inverse' else -1
    p0,p1=sign*float(row['P0']),sign*float(row['P1'])
    return p0+p1*fraction,p1


def finalize_review(folder, output, manual, *, starting_method, starting_phase,
                    ambiguous=False):
    folder,output=Path(folder),Path(output)
    checkpoint=g.json_read(output/'checkpoint.json')
    phase=checkpoint['manual_phase']
    adjusted=any(abs(float(phase[k])-float(starting_phase[k]))>1e-10
                 for k in ('p0_deg','p1_deg','pivot_ppm'))
    status=('MANUAL REVIEW — AMBIGUOUS' if ambiguous else
            'MANUAL REVIEW — DX METADATA PHASE ACCEPTED' if starting_method=='production' and not adjusted
            else 'MANUAL REVIEW — ADJUSTED')
    checkpoint.update(starting_method=starting_method,starting_manual_phase=starting_phase,
                      adjusted=adjusted,ambiguous=bool(ambiguous),review_status=status,
                      review_completion_status='MANUAL REVIEW — AMBIGUOUS' if ambiguous else 'MANUAL REVIEW COMPLETE')
    write_json(output/'checkpoint.json',checkpoint)
    methods=saved_methods(folder)
    with np.load(output/'spectral_evidence.npz') as a:
        ppm,z=a['ppm'].copy(),a['phased'].copy()
    for name,values in methods.items():
        np.testing.assert_array_equal(values['ppm'],ppm)
    for component,values in [('real',z.real),('imaginary',z.imag)]:
        np.savetxt(output/f'manual_{component}_spectrum.csv',np.column_stack((ppm,values)),
                   delimiter=',',fmt='%.17g',header=f'ppm,{component}',comments='')
    quality=score_spectrum(z,load_context(folder))
    completion=next(r for r in v.read_rows(output/'completion_comparison.csv')
                    if r['scope']=='through_this_acquisition')
    target=manual['target']
    m={'P0':phase['effective_index_zero_p0_deg'],'P1':phase['p1_deg'],'direction':'inverse',
       'target_peak_ppm':target['peak_ppm'],'peak_height':target['height'],'peak_area':target['area'],
       'snr':target['snr'],'detected':target['detected'],
       'completion_boolean':completion['manual_complete'],
       'completion_classification':completion['manual_classification'],**quality}
    methods['manual']={'ppm':ppm,'phased':z,'metrics':m}
    t=np.arange(len(ppm))/len(ppm)
    mask=(ppm>=0)&(ppm<=12)
    manual_ramp=-(float(m['P0'])+float(m['P1'])*t)
    rows=[]
    for name,values in methods.items():
        r=values['metrics'];sign=-1 if r['direction']=='inverse' else 1
        ramp=sign*(float(r['P0'])+float(r['P1'])*t)
        delta=(ramp-manual_ramp+180)%360-180
        def diff(key):
            try:return float(r[key])-float(m[key])
            except (TypeError,ValueError):return None
        rows.append({'method':name,'P0':r['P0'],'P1':r['P1'],'direction':r['direction'],
            'phase_ramp_rms_difference_vs_manual_deg':float(np.sqrt(np.mean(delta[mask]**2))),
            'phase_ramp_region_ppm':'0–12; circular RMS; k/N',
            'target_peak_ppm':r['target_peak_ppm'],'peak_height':r['peak_height'],
            'peak_area':r['peak_area'],'snr':r['snr'],
            'negative_fraction':r['negative_area_fraction'],'negative_area_fraction':r['negative_area_fraction'],
            'imaginary_residual':r['imaginary_residual'],
            'asymmetry':r['peak_asymmetry'],'detected':r['detected'],
            'retrospective_completion':r['completion_boolean'],
            'completion_classification':r['completion_classification'],
            'delta_area_vs_manual':diff('peak_area'),'delta_snr_vs_manual':diff('snr'),
            'delta_ppm_vs_manual':diff('target_peak_ppm'),
            'completion_scope':'Automatic: saved same-method date prefix; manual: latest reviewed date prefix, DX metadata phase elsewhere',
            'physical_stop_verified':False})
    write_rows(output/'manual_vs_all_methods.csv',rows)
    identity=g.json_read(folder/'00_metadata.json');manifest=[]
    def plot(stem,bounds,groups):
        fig,axes=plt.subplots(2,len(groups),figsize=(7*len(groups),8),layout='constrained',
                              sharex=True,sharey='row',squeeze=False)
        for col,group in enumerate(groups):
            for row,component in enumerate(('real','imag')):
                ax=axes[row,col]
                for name in (*(n for n in group if n in methods),'manual'):
                    ax.plot(ppm,getattr(methods[name]['phased'],component),
                            label='Manual [saved checkpoint]' if name=='manual' else LEGEND.get(name,name),
                            color='#000000' if name=='manual' else COLORS.get(name,'#CC79A7'),
                            lw=1.5 if name=='manual' else .8)
                ax.set(xlim=(bounds[1],bounds[0]),xlabel='Chemical shift (ppm)',
                       ylabel=f'{component.capitalize()} intensity (a.u.)',title=component.capitalize())
                ax.axhline(0,color='gray',lw=.5);ax.legend(fontsize=8,ncol=2)
                visible=(ppm>=bounds[0])&(ppm<=bounds[1])
                y=np.concatenate([getattr(methods[n]['phased'],component)[visible] for n in methods])
                lo,hi=float(y.min()),float(y.max());pad=.08*max(hi-lo,1)
                ax.set_ylim(lo-pad,hi+pad)
        g.save_plot(fig,output,stem,identity,
                    'Manual vs All Methods — '+('Target Region' if len(groups)>1 else 'Full Spectrum'),manifest)
    plot('manual_vs_all_methods',g.TARGET_RANGE,[AUTOMATIC[:3],AUTOMATIC[3:]])
    plot('manual_full_spectrum',(float(ppm.min()),float(ppm.max())),[('production',)])
    write_rows(output/'manual_workflow_figure_manifest.csv',manifest)
    (output/'MANUAL_COMPARISON.md').write_text(
        '# Manual comparison\n\nSee manual_vs_all_methods.csv and manual_vs_all_methods.png (coordinated real/imaginary target panels). '
        'Deltas are automatic minus manual. Phase RMS uses direct physical ramps, wrapped to ±180°, over 0–12 ppm. '
        'P0/P1 are index-zero parameters with the displayed direction; checkpoint.json separately retains GUI pivot controls. '
        'Automatic completion is the preserved same-method date-prefix result; manual completion uses the recorded mixed review/DX metadata phase sequence. '
        'Neither verifies a physical controller stop. All target/baseline/SNR/QC calculations use the existing shared analysis.\n',encoding='utf-8')
    return status


def export_preserved_comparisons(folder,output,manual):
    """Checkpoint-only derivative tables; original saved comparisons stay frozen."""
    folder,output=Path(folder),Path(output)
    identity=g.json_read(folder/'00_metadata.json')
    rows=v.read_rows(folder/'all_phase_methods_results.csv')
    automatic=[]
    for original in rows:
        if original['method'].startswith('manual_'):continue
        if original['raw_sha256']!=identity['raw_sha256'] or original['timestamp']!=identity['timestamp']:
            raise ValueError('Saved method identity disagrees with authoritative metadata')
        row=original.copy()
        row['source_table_acquisition_id']=original['acquisition_id']
        if row['acquisition_id']!=identity['acquisition_id']:
            row['derivative_identity_note']='Recovered from verified raw SHA256 + LONG DATE; frozen ID field preserved'
        row['acquisition_id']=identity['acquisition_id'];automatic.append(row)
    checkpoint=g.json_read(output/'checkpoint.json');phase=checkpoint['manual_phase']
    prefix=next(r for r in v.read_rows(output/'completion_comparison.csv') if r['scope']=='through_this_acquisition')
    quality=score_spectrum(manual['spectrum'].real+1j*manual['spectrum'].imaginary,load_context(folder))
    target=manual['target']
    row={'method':'manual_'+output.name,'P0':phase['effective_index_zero_p0_deg'],'P1':phase['p1_deg'],
         'direction':'inverse','acquisition_id':identity['acquisition_id'],'timestamp':identity['timestamp'],
         'raw_sha256':identity['raw_sha256'],'target_peak_ppm':target['peak_ppm'],'peak_height':target['height'],
         'peak_area':target['area'],'snr':target['snr'],'detected':target['detected'],
         'completion_boolean':prefix['manual_complete'],'completion_classification':prefix['manual_classification'],
         'checkpoint_path':str(output),'reference_role':('human/manual reference; not absolute ground truth'
             if checkpoint.get('analysis_role')=='manual_reference' else 'SOFTWARE FIXTURE ONLY; not a human reference'),**quality}
    write_rows(output/'all_phase_methods_with_manual.csv',[*automatic,row])
    write_rows(output/'phase_quality_with_manual.csv',[*automatic,row])
    comparisons=[]
    def difference(value,stored):
        try:return float(value)-float(stored)
        except (TypeError,ValueError):return None
    for saved in automatic:
        comparisons.append({'automatic_method':saved['method'],'manual_checkpoint':output.name,
            'automatic_area':saved['peak_area'],'manual_area':target['area'],
            'manual_minus_automatic_area':difference(target['area'],saved['peak_area']),
            'automatic_snr':saved['snr'],'manual_snr':target['snr'],
            'manual_minus_automatic_snr':difference(target['snr'],saved['snr']),
            'automatic_detection':saved['detected'],'manual_detection':target['detected'],
            'automatic_prefix_completion':saved['completion_boolean'],'manual_prefix_completion':prefix['manual_complete'],
            'unphased_role':'before-phase reference only' if saved['method']=='unphased' else ''})
    write_rows(output/'all_automatic_vs_manual_metrics.csv',comparisons)
    methods=saved_methods(folder);ppm=methods['production']['ppm']
    traces={'unphased':methods['production']['unphased'],
            **{name:data['phased'] for name,data in methods.items()},
            'manual':manual['spectrum'].real+1j*manual['spectrum'].imaginary}
    fig,axes=plt.subplots(1,2,figsize=(14,5),layout='constrained');manifest=[]
    mask=(ppm>=g.TARGET_RANGE[0])&(ppm<=g.TARGET_RANGE[1])
    for ax,component in zip(axes,('real','imag')):
        for name,z in traces.items():
            ax.plot(ppm,getattr(z,component),label='Manual [saved checkpoint]' if name=='manual' else LEGEND[name],
                    color='#000000' if name=='manual' else COLORS.get(name,'#777777'),lw=1.5 if name=='manual' else .8)
        values=np.concatenate([getattr(z,component)[mask] for z in traces.values()])
        lo,hi=float(values.min()),float(values.max());pad=.08*max(hi-lo,1)
        ax.set(xlim=g.TARGET_RANGE[::-1],ylim=(lo-pad,hi+pad),title=component.capitalize(),
               xlabel='Chemical shift (ppm)',ylabel='Intensity (a.u.)')
        ax.legend(fontsize=7,ncol=2)
    g.save_plot(fig,output,'all_automatic_vs_manual_overlay',identity,
                'Saved Automatic Methods and Unphased Reference vs '+('Human/Manual Checkpoint' if checkpoint.get('analysis_role')=='manual_reference'
                    else 'SOFTWARE FIXTURE ONLY'),manifest)
    write_rows(output/'all_methods_manual_figure_manifest.csv',manifest)
    checkpoint['manual_comparison_table']='all_phase_methods_with_manual.csv'
    write_json(output/'checkpoint.json',checkpoint)
