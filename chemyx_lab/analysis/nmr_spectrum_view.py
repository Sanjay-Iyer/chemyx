"""Read-only spectrum views of existing processing evidence; no fitting here."""
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from . import nmr_validation as v, phase_gallery as g
from .phase_audit import write_json, write_rows, save_figure


def processed_view(arrays, *, target=None, peaks=(), parameters=None, source_label=''):
    ppm=arrays['ppm'].copy();z=arrays['phased'].copy()
    global_real=arrays['quantitative_real'].copy()
    region=arrays['region_ppm'].copy();regional=arrays['regional_quantitative'].copy()
    indices=np.searchsorted(ppm,region)
    np.testing.assert_array_equal(ppm[indices],region)
    final=global_real.copy();final[indices]=regional
    baseline=arrays['baseline'].copy()
    baseline[indices]=z.real[indices]-regional
    return {'ppm':ppm,'phased':z,'global_real':global_real,
            'global_baseline':arrays['baseline'].copy(),'final':final,'baseline':baseline,
            'region_ppm':region,'regional_quantitative':regional,'region_indices':indices,
            'target':target or {},'peaks':list(peaks),'parameters':parameters or {},'source_label':source_label}


def load_processed_view(folder, *, parameters=None, source_label=''):
    folder=Path(folder)
    with np.load(folder/'spectral_evidence.npz') as a:
        arrays={k:a[k].copy() for k in a.files}
    meta=folder/'processing_metadata.json'
    if parameters is None and meta.exists():parameters=g.json_read(meta)['parameters']
    target=g.json_read(folder/'target_result.json')
    peaks=v.read_rows(folder/'peak_results.csv') if (folder/'peak_results.csv').exists() else []
    return processed_view(arrays,target=target,peaks=peaks,parameters=parameters,source_label=source_label)


def target_peak(view):
    target=view['target'];ppm=target.get('peak_ppm')
    if ppm is None or not v.truth(target.get('detected')):return None
    candidates=[row for row in view['peaks'] if v.truth(row.get('qc_pass'))]
    if not candidates:return None
    return min(candidates,key=lambda row:abs(float(row['interpolated_ppm'])-float(ppm)))


def export_final_spectrum(output,identity,baseline_review):
    """Persist a faithful display of an already completed manual analysis."""
    output=Path(output);view=load_processed_view(output,source_label=output.name)
    np.savez_compressed(output/'final_processed_spectrum.npz',ppm=view['ppm'],
        phased_real=view['phased'].real,global_baseline=view['global_baseline'],
        analysis_baseline=view['baseline'],global_corrected_real=view['global_real'],
        final_display_real=view['final'],region_ppm=view['region_ppm'],
        regional_quantitative=view['regional_quantitative'])
    np.savetxt(output/'final_processed_spectrum.csv',np.column_stack((view['ppm'],view['phased'].real,
        view['baseline'],view['final'],view['global_real'])),delimiter=',',fmt='%.17g',
        header='ppm,phased_real,analysis_baseline,final_display_real,global_corrected_real',comments='')
    checkpoint=g.json_read(output/'checkpoint.json')
    checkpoint.update(baseline_review=baseline_review,baseline_changed=False,
        analysis_source='Manual phased Real / Absorption -> unchanged baseline/analysis pipeline',
        final_display_scope='Exact regional quantitative trace in the analysis region; global-corrected real outside',
        final_spectrum_file='final_processed_spectrum.npz')
    write_json(output/'checkpoint.json',checkpoint)
    fig,axes=plt.subplots(2,2,figsize=(13,8),layout='constrained')
    bounds=[(float(view['ppm'].min()),float(view['ppm'].max())),g.TARGET_RANGE]
    peak=target_peak(view)
    for col,(lo,hi) in enumerate(bounds):
        axes[0,col].plot(view['ppm'],view['final'],color='#0072B2',lw=.8,label='Final Real / Absorption')
        axes[1,col].plot(view['ppm'],view['phased'].real,color='#777777',lw=.7,label='Before baseline')
        axes[1,col].plot(view['ppm'],view['baseline'],color='#D55E00',lw=.8,label='Analysis baseline')
        for ax in axes[:,col]:
            mask=(view['ppm']>=lo)&(view['ppm']<=hi)
            data=view['final'][mask] if ax is axes[0,col] else np.concatenate((view['phased'].real[mask],view['baseline'][mask]))
            bottom,top=float(data.min()),float(data.max());pad=.08*max(top-bottom,1)
            ax.set(xlim=(hi,lo),ylim=(bottom-pad,top+pad),xlabel='Chemical shift (ppm)',ylabel='Intensity (a.u.)')
            ax.axhline(0,color='gray',lw=.5);ax.legend(fontsize=8)
        if peak:
            axes[0,col].axvspan(float(peak['integration_left_ppm']),float(peak['integration_right_ppm']),
                               color='#009E73',alpha=.12,label='Integration bounds')
            axes[0,col].plot(float(view['target']['peak_ppm']),float(view['target']['height']),
                              'o',color='#009E73',ms=5)
        for bound in (view['region_ppm'].min(),view['region_ppm'].max()):
            axes[0,col].axvline(bound,color='#777777',ls=':',lw=.6)
        center=float(view['parameters'].get('simple_target_ppm',5.8))
        width=float(view['parameters'].get('simple_window_ppm',.1))
        axes[0,col].axvspan(center-width,center+width,color='#CCAA00',alpha=.08,label='Target window')
        for row in view['peaks']:
            passed=v.truth(row.get('qc_pass'))
            axes[0,col].plot(float(row['interpolated_ppm']),float(row['height']),
                'o' if passed else 'x',color='#009E73' if passed else '#D55E00',ms=4)
        axes[0,col].legend(fontsize=8)
        axes[0,col].set_title('Full final spectrum' if col==0 else 'Exact target-analysis signal')
        axes[1,col].set_title('Baseline inspection; regional correction is confined to analysis ROI')
    manifest=[]
    save_figure(fig,output/'final_processed_spectrum',identity['dataset_display_name'],
        f"Final Processed Real / Absorption — same saved analysis | {identity['acquisition_id']} | {identity['timestamp']}",manifest)
    write_rows(output/'final_spectrum_figure_manifest.csv',manifest)
    write_json(output/'final_spectrum_context.json',{'analysis_source':checkpoint['analysis_source'],
        'baseline_review':baseline_review,'baseline_changed':False,'region_ppm':[float(view['region_ppm'].min()),float(view['region_ppm'].max())],
        'global_baseline_method':view['parameters'].get('baseline_method'),
        'regional_polynomial_order':view['parameters'].get('baseline_order'),
        'target':view['target'],'display_scope':checkpoint['final_display_scope'],
        'final_analysis_scope':'Target peak quantitative trace inside analysis ROI; global correction elsewhere',
        'completion_source':'Global corrected Real -> separate fixed-window integration/local baseline and retrospective date-prefix replay'})
