"""Extend existing phase gallery with common diagnostics and extra comparators."""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import numpy as np
import matplotlib.pyplot as plt
from . import phase_gallery as g
from . import nmr_validation as v
from .phase_audit import sha256, write_json, write_rows, save_figure, provenance
from .phase_quality import prepare_context, score_spectrum, QUALITY_CONFIG
from .phase_optimizers import optimize_phase, ernst_integral_p0, OPTIMIZER_CONFIG, COMBINED_WEIGHTS

NEW_METHODS = ('combined_objective_v1','symmetry_objective','ernst_integral_p0','deep_phaser')
LABELS = {**g.LABELS, 'combined_objective_v1':'COMBINED OBJECTIVE v1',
    'symmetry_objective':'SYMMETRY_OBJECTIVE', 'ernst_integral_p0':'ERNST INTEGRAL (P0 ONLY)', 'deep_phaser':'DEEP Phaser'}
COLORS = {**g.COLORS, 'combined_objective_v1':'#CC79A7','symmetry_objective':'#9467BD',
    'ernst_integral_p0':'#8C564B','deep_phaser':'#17BECF'}
STEMS = {'combined_objective_v1':'10_combined_objective_phase','symmetry_objective':'11_symmetry_objective_phase',
    'ernst_integral_p0':'12_ernst_integral_p0_phase','deep_phaser':'13_deep_phaser_phase'}
TICK_LABELS = {'unphased':'Unphased','production':'Stored','acme':'ACME','peak_minima':'Peak\nminima',
    'combined_objective_v1':'Combined\nv1','symmetry_objective':'Symmetry',
    'ernst_integral_p0':'Ernst\nP0 only','deep_phaser':'DEEP\nPhaser'}
UNAVAILABLE = {'hilbert_linear':'NOT IMPLEMENTED — insufficient reproducible specification for full P0/P1 Hilbert phasing',
    'dispa':'NOT IMPLEMENTED — no verified isolated-resonance estimator/reference implementation',
    'edispa':'NOT IMPLEMENTED — full poster transform/weighting and polarity conventions not reproduced',
    'apsl':'NOT IMPLEMENTED — exact noniterative estimator unavailable; custom symmetry is separate'}
g.LABELS.update(LABELS); g.COLORS.update(COLORS)
NODE = Path(shutil.which('node') or '')


def spectrum_complex(result):
    return result['spectrum'].real + 1j*result['spectrum'].imaginary


def load_context(folder):
    with np.load(Path(folder)/'analysis/production/spectral_evidence.npz') as arrays:
        return prepare_context(arrays['ppm'], arrays['unphased'])


def run_deep(folder, production, metadata, *, timeout=900):
    output=Path(folder)/'analysis/deep_phaser'; output.mkdir(parents=True,exist_ok=True)
    status_path=output/'optimizer_result.json'
    external=v.DEFAULT_OUTPUT/'logs/additional_phase_methods/external_audit'
    required=[external/'tfjs-4.22.0.cjs',external/'colmarvista/js/1d.js',
        *(external/f'colmarvista/saved_model_p{i}/{name}' for i in (0,1) for name in ('model.json','group1-shard1of1.bin'))]
    if not NODE.is_file() or not all(p.is_file() for p in required):
        status={'method':'deep_phaser','success':False,'status':'unavailable_runtime_or_model',
            'message':'Local Node, exact models or pinned TFJS runtime missing; no substitute network'}
        write_json(status_path,status); return status
    source_hashes={str(p):sha256(p) for p in required}
    cache=g.json_read(status_path) if status_path.exists() else {}
    if cache.get('success') and cache.get('source_hashes')==source_hashes and cache.get('raw_sha256')==metadata['raw_sha256']:
        return cache
    # COLMAR arrays run high ppm to low ppm. Reverse only the representation
    # used by its classifiers; apply returned phase to the unchanged common FFT.
    z=production['unphased'][::-1]
    request=output/'model_input.json'
    write_json(request,{'real':z.real.tolist(),'imaginary':z.imag.tolist(),
        'raw_sha256':metadata['raw_sha256'],'maximum_predictions':500})
    try:
        completed=subprocess.run([str(NODE),str(v.ROOT/'scripts/nmr/deep_phaser_offline.cjs'),str(request),str(status_path)],
            capture_output=True,text=True,timeout=timeout)
        (output/'inference.log').write_text(completed.stdout+'\n'+completed.stderr,encoding='utf-8')
        status=g.json_read(status_path) if status_path.exists() else {'method':'deep_phaser','success':False,
            'status':'runtime_failure','message':completed.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        status={'method':'deep_phaser','success':False,'status':'runtime_timeout',
            'message':f'CPU inference exceeded {timeout}s budget; no result fabricated'}
    status.update(raw_sha256=metadata['raw_sha256'],source_hashes=source_hashes,
        adapter_sha256=sha256(v.ROOT/'scripts/nmr/deep_phaser_offline.cjs'),
        input_description='Identical unphased full complex FFT, reversed into descending-ppm order; author real/max normalization and all-true mask; no baseline/apodization change',
        low_field_domain_warning='NMReady ~60.5 MHz spectra differ from typical high-field solution-NMR training examples; human validation required')
    write_json(status_path,status)
    return status


def method_status(folder, method, context, production, a, *, deep=True):
    if method=='deep_phaser':
        return run_deep(folder,production,a) if deep else {'method':method,'success':False,'status':'not_requested'}
    output=Path(folder)/'analysis'/method;output.mkdir(parents=True,exist_ok=True)
    path=output/'optimizer_result.json'
    identity={'quality_source':sha256(Path(__file__).with_name('phase_quality.py')),
        'optimizer_source':sha256(Path(__file__).with_name('phase_optimizers.py')),
        'raw_sha256':a['raw_sha256'],'fft_sha256':context.metadata['fft_sha256'],
        'quality_config':QUALITY_CONFIG,'optimizer_config':OPTIMIZER_CONFIG,'combined_weights':COMBINED_WEIGHTS}
    cached=g.json_read(path) if path.exists() else {}
    if cached.get('cache_identity')==identity and cached.get('success'):
        return cached
    if method=='ernst_integral_p0':
        status=ernst_integral_p0(production['spectrum'].ppm_axis,production['unphased'])
    else:
        spec=production['spectrum'];sign=-1 if not production['args'].direct_phase else 1
        p0=(sign*spec.phase0_deg+180)%360-180
        status=optimize_phase(context,method,(p0,sign*spec.phase1_deg))
    status['cache_identity']=identity;write_json(path,status)
    return status


def failure_flags(row):
    flags=[]
    if row.get('optimizer_success') is False:flags.append('optimizer failure/non-convergence')
    if row.get('P0') is not None:
        sign=-1 if row.get('direction')=='inverse' else 1
        principal=(sign*row['P0']+180)%360-180
        if abs(principal)>=170:flags.append('canonical P0 near 180 degrees; inspect phase convention/polarity')
    if row.get('P1') is not None and abs(row['P1'])>=270:flags.append('large first-order phase (>=270 deg)')
    for key,limit,label in [('negative_area_fraction',.2,'negative area >20%'),
        ('even_imaginary_fraction',.3,'even imaginary leakage >30%'),('peak_asymmetry',.5,'real asymmetry >50%'),
        ('dispersive_metric',.5,'odd real energy ratio >50%'),('baseline_slope_normalized',.5,'broad baseline tilt >50% scale')]:
        if row.get(key) is not None and row[key]>limit:flags.append(label)
    if row.get('relative_area_change') is not None and abs(row['relative_area_change'])>.5:
        flags.append('target area differs >50% from production')
    if row.get('detection_changed'):flags.append('target detection changed')
    if row.get('completion_changed'):flags.append('retrospective prefix completion changed')
    if row.get('p1_constraint_warning'):flags.append('first-order phase weakly constrained by selected peaks')
    return flags


def metrics_row(a, method, result, production, context, decision=None, status=None):
    status=status or {'success':None if method in ('acme','peak_minima') else True,
        'status':'retained_optimizer_convergence_unknown' if method in ('acme','peak_minima') else 'retained_existing_result',
        'source':'Exact previously retained phase; optimizer not rerun'}
    row=g.result_row(a,method,result,production,decision)
    row.update(score_spectrum(spectrum_complex(result),context))
    target=result['target'];base=production['target']
    row.update(target_peak_height=target['height'],target_peak_area=target['area'],S_N=target['snr'],
        phase_convention='inverse' if not result['args'].direct_phase else 'direct',
        optimizer_source=status.get('source','Authors COLMARvista models/search' if method=='deep_phaser' else 'Custom objective'),
        optimizer_success=status['success'],optimizer_status=status['status'],
        objective_value=status.get('objective_value'),
        relative_area_change=(target['area']-base['area'])/abs(base['area']) if abs(base['area'])>1e-12 else None,
        detection_changed=target['detected']!=base['detected'],
        completion_changed=False,primary_cohort=bool(a['primary_cohort']))
    row['flags']='; '.join(failure_flags(row))
    return row


def draw_curve(ax,result,method,bounds=(0.,12.),component='real',label=True):
    spec=result['spectrum']
    ax.plot(spec.ppm_axis,getattr(spec,component),lw=.8,color=COLORS.get(method,'#222222'),
        label=LABELS.get(method,method) if label else None)
    ax.set(xlim=(bounds[1],bounds[0]),xlabel='Chemical shift (ppm)',ylabel='Intensity (a.u.)')
    ax.axhline(0,lw=.4,color='gray')


def panel_comparison(folder,a,results,methods,stem,description,manifest,*,bounds=None):
    methods=[m for m in methods if m in results]
    rows=int(np.ceil(len(methods)/2));fig,axes=plt.subplots(rows,2,figsize=(14,3*rows),layout='constrained',sharex=True,sharey=True)
    if bounds is None:
        axis=results['production']['spectrum'].ppm_axis;bounds=(float(axis.min()),float(axis.max()))
    for ax,method in zip(axes.flat,methods):
        draw_curve(ax,results[method],method,bounds,label=False)
        spec=results[method]['spectrum'];ax.set_title(f'{LABELS.get(method,method)} | P0={spec.phase0_deg:.3g}, P1={spec.phase1_deg:.3g}',fontsize=10)
    for ax in list(axes.flat)[len(methods):]:ax.axis('off')
    g.save_plot(fig,folder,stem,a,description,manifest)


def figures(folder,a,results,rows,context,manifest):
    methods=list(results)
    primary=[*g.METHODS,'combined_objective_v1','symmetry_objective']
    panel_comparison(folder,a,results,primary,'phase_comparison_primary','Primary Phase Comparison — same FFT, before baseline',manifest)
    panel_comparison(folder,a,results,methods,'phase_comparison_all_methods','All Implemented Phase Methods — before baseline',manifest)
    panel_comparison(folder,a,results,methods,'target_peak_all_phase_methods','Target Peak — same downstream analysis',manifest,bounds=g.TARGET_RANGE)
    # Replace target plot with quantitative panels showing the exact target
    # metrics and integration bounds used by the unchanged production path.
    n=int(np.ceil(len(methods)/2));fig,axes=plt.subplots(n,2,figsize=(14,3*n),layout='constrained',sharex=True,sharey=True)
    for ax,method in zip(axes.flat,methods):
        picked=results[method]['picked'];target=results[method]['target']
        ax.plot(picked.ppm_axis,picked.quantitative_corrected,color=COLORS.get(method),lw=.9)
        ax.set(xlim=(6.,5.6),xlabel='Chemical shift (ppm)',ylabel='Quantitative real intensity (a.u.)')
        ax.set_title(f'{LABELS.get(method,method)}\narea={target["area"]:.5g} | S/N={target["snr"]:.4g} | detected={target["detected"]}',fontsize=10)
        if target['peak_ppm'] is not None:
            ax.axvline(target['peak_ppm'],color='black',ls=':',lw=.7)
            best=min(results[method]['peaks'],key=lambda p:abs(p['interpolated_ppm']-target['peak_ppm']))
            lo,hi=best['integration_left_ppm'],best['integration_right_ppm']
            ax.axvspan(lo,hi,color=COLORS.get(method),alpha=.12)
            ax.text(.02,.93,f'peak={target["peak_ppm"]:.5f} ppm; bounds={lo:.5f}–{hi:.5f}',transform=ax.transAxes,fontsize=8,va='top')
        ax.axhline(0,color='gray',lw=.4)
    for ax in list(axes.flat)[len(methods):]:ax.axis('off')
    # Remove the preliminary manifest row: the final target plot has the
    # quantitative title/arrays, rather than claiming phase-only intensities.
    target_path=str((Path(folder)/'target_peak_all_phase_methods.png').resolve())
    manifest[:]=[r for r in manifest if r['path']!=target_path]
    g.save_plot(fig,folder,'target_peak_all_phase_methods',a,'Target Peak Metrics — same downstream boundaries and detection',manifest)
    fig,axes=plt.subplots(len(methods),2,figsize=(14,2.6*len(methods)),layout='constrained',sharex=True,sharey='col')
    for pair,method in zip(axes,methods):
        for ax,component in zip(pair,('real','imaginary')):
            draw_curve(ax,results[method],method,g.TARGET_RANGE,component,label=False)
            ax.set_title(f'{LABELS.get(method,method)} — {component}',fontsize=10)
    g.save_plot(fig,folder,'complex_all_phase_methods',a,'Real and Imaginary Target Diagnostics — before baseline',manifest)
    centers=context.centers[:4]
    if centers:
        fig,axes=plt.subplots(len(centers),1,figsize=(13,3*len(centers)),layout='constrained',squeeze=False)
        for ax,c in zip(axes.flat,centers):
            bounds=(float(context.ppm[c]-.18),float(context.ppm[c]+.18))
            for method in methods:draw_curve(ax,results[method],method,bounds,'imaginary')
            ax.set_title(f'Fixed magnitude-selected resonance at {context.ppm[c]:.4f} ppm')
            ax.legend(fontsize=7,ncol=4)
        g.save_plot(fig,folder,'major_resonance_imaginary_all_methods',a,'Major Resonance Imaginary Residuals — odd dispersion is expected',manifest)
    for method in NEW_METHODS:
        if method not in results:continue
        fig,axes=plt.subplots(1,2,figsize=(13,4),layout='constrained')
        draw_curve(axes[0],results[method],method,(float(results[method]['spectrum'].ppm_axis.min()),float(results[method]['spectrum'].ppm_axis.max())),label=False)
        draw_curve(axes[1],results[method],method,g.TARGET_RANGE,'imaginary',label=False)
        axes[0].set_title('Full real spectrum');axes[1].set_title('Target imaginary channel')
        g.save_plot(fig,folder,STEMS[method],a,LABELS[method]+' — real / imaginary',manifest)


def write_tables(folder,a,results,context,decisions=None,statuses=None):
    statuses=statuses or {};decisions=decisions or {};production=results['production']
    rows=[]
    for method,result in results.items():
        row=metrics_row(a,method,result,production,context,decisions.get(method),statuses.get(method))
        if decisions.get('production') and decisions.get(method):
            row['completion_changed']=decisions[method]['complete']!=decisions['production']['complete']
        row['flags']='; '.join(failure_flags(row));rows.append(row)
    # Existing genuine human checkpoints are not erased by an automatic build.
    previous=Path(folder)/'all_phase_methods_results.csv'
    old=v.read_rows(previous) if previous.exists() else []
    rows.extend(r for r in old if r.get('method','').startswith('manual_'))
    write_rows(Path(folder)/'all_phase_methods_results.csv',rows)
    write_rows(Path(folder)/'phase_quality_metrics.csv',rows)
    return rows


def cohort_tables(gallery,rows,statuses,records,manifest):
    write_rows(gallery/'cohort_phase_quality_metrics.csv',rows)
    summaries=[]
    keys=['negative_area_fraction','imaginary_residual','even_imaginary_fraction','peak_asymmetry',
        'dispersive_metric','difference_from_production_peak_area','difference_from_production_snr','relative_area_change']
    for cohort in ('all_27','primary_25','secondary_2'):
        selected=[r for r in rows if cohort=='all_27' or r['primary_cohort']==(cohort=='primary_25')]
        for method in dict.fromkeys(r['method'] for r in selected):
            values=[r for r in selected if r['method']==method];summary={'cohort':cohort,'method':method,'n':len(values),
                'changed_detections':sum(r['detection_changed'] for r in values),
                'changed_prefix_completion_results':sum(r['completion_changed'] for r in values),
                'pathological_or_review_flag_count':sum(bool(r['flags']) for r in values),
                'optimizer_convergence_not_recorded':sum(r['optimizer_success'] is None for r in values),
                'absolute_area_difference_median':float(np.median([abs(r['difference_from_production_peak_area']) for r in values])),
                'absolute_snr_difference_median':float(np.median([abs(r['difference_from_production_snr']) for r in values])),
                'optimizer_failures':sum(not s['success'] for (ident,m),s in statuses.items() if m==method and
                    any(a['acquisition_id']==ident and (cohort=='all_27' or a['primary_cohort']==(cohort=='primary_25')) for a in records))}
            for key in keys:
                data=[r[key] for r in values if r.get(key) is not None]
                summary[key+'_median']=float(np.median(data)) if data else None
                summary[key+'_q25']=float(np.quantile(data,.25)) if data else None
                summary[key+'_q75']=float(np.quantile(data,.75)) if data else None
            summaries.append(summary)
    write_rows(gallery/'cohort_phase_method_summary.csv',summaries)
    methods=list(dict.fromkeys(r['method'] for r in rows))
    fig,axes=plt.subplots(2,3,figsize=(17,11),layout='constrained')
    for ax,key in zip(axes.flat,keys[:4]+keys[4:5]+['difference_from_production_peak_area']):
        data=[[r[key] for r in rows if r['method']==method and r.get(key) is not None] for method in methods]
        ax.boxplot(data,tick_labels=[TICK_LABELS.get(m,m) for m in methods],showfliers=False)
        for i,values in enumerate(data):
            offsets=np.linspace(-.12,.12,len(values))
            ax.scatter(np.full(len(values),i+1)+offsets,values,s=12,alpha=.65,color=COLORS.get(methods[i]))
        ax.set_title(key);ax.tick_params(axis='x',labelsize=7);ax.set_ylabel('Area / a.u.·ppm' if key=='difference_from_production_peak_area' else 'Dimensionless')
    from .plot_titles import resolve_dataset_display_name
    identity=resolve_dataset_display_name(metadata={'dataset_name':' / '.join(dict.fromkeys(a['dataset_display_name'] for a in records))})
    save_figure(fig,gallery/'cohort_phase_quality_distributions',identity,'Phase Quality Distributions — all 27 real acquisitions',manifest,formats=('png',))
    fig,axes=plt.subplots(1,3,figsize=(16,5.5),layout='constrained')
    for ax,key in zip(axes[:2],('difference_from_production_peak_area','difference_from_production_snr')):
        for method in methods:
            vals=[r[key] for r in rows if r['method']==method]
            ax.plot(range(1,len(vals)+1),vals,'o-',ms=3,lw=.6,label=LABELS.get(method,method),color=COLORS.get(method))
        ax.axhline(0,color='gray',lw=.6);ax.set(title=key,xlabel='Acquisition index (metadata ordered)',ylabel='Difference from production')
    counts=[sum(r['detection_changed'] for r in rows if r['method']==m) for m in methods]
    axes[2].bar(range(len(methods)),counts,color=[COLORS.get(m) for m in methods]);axes[2].set_xticks(range(len(methods)),[TICK_LABELS.get(m,m) for m in methods],fontsize=7)
    axes[2].set(title='Detection disagreement counts',ylabel='Acquisitions');axes[0].legend(fontsize=6,ncol=2)
    save_figure(fig,gallery/'cohort_phase_measurement_changes',identity,'Area, S/N and Detection Changes — all 27 acquisitions',manifest,formats=('png',))
    return summaries


def june_figures(gallery,records,results,replays,manifest):
    group=[a for a in records if a['acquisition_date']=='2026-06-09'];folder=gallery/'june09_completion_review'
    methods=[m for m in results[group[0]['acquisition_id']] if ('2026-06-09',m) in replays]
    timing=[{'acquisition_id':a['acquisition_id'],'timestamp':a['timestamp'],'timestamp_source':a['timestamp_source'],'raw_sha256':a['raw_sha256']} for a in group]
    write_rows(folder/'all_methods_sequence_timing.csv',timing)
    trajectories=[];outcomes=[]
    for method in methods:
        replay=replays[('2026-06-09',method)];index=replay.completion.completion_index
        outcomes.append({'method':method,'complete':replay.completion.complete,
            'completion_timestamp':group[index]['timestamp'] if index is not None else 'NO COMPLETION',
            'completion_index':index,'classification':replay.completion.status,
            'warnings':'; '.join(replay.completion.quality_warnings),'physical_historical_stop':False})
        for i,a in enumerate(group):
            trace=replay.decision_trace[i];result=results[a['acquisition_id']][method]
            trajectories.append({**timing[i],'method':method,'measurement_index':i,
                'fixed_completion_area':replay.measurements[i]['area'],
                'target_peak_area':result['target']['area'],'target_snr':result['target']['snr'],
                **trace})
    write_rows(folder/'june09_all_phase_methods_results.csv',outcomes)
    write_rows(folder/'june09_all_phase_methods_trajectories.csv',trajectories)
    fig,axes=plt.subplots(3,1,figsize=(15,13),layout='constrained')
    labels=[a['timestamp'][11:19] for a in group]
    for method in methods:
        replay=replays[('2026-06-09',method)];color=COLORS[method]
        axes[0].plot(range(8),[m['area'] for m in replay.measurements],'o-',color=color,label=LABELS[method])
        axes[1].plot(range(8),[results[a['acquisition_id']][method]['target']['snr'] for a in group],'o-',color=color,label=LABELS[method])
        slopes=[float(t['recent_slope_per_hour']) if t.get('recent_slope_per_hour') is not None else np.nan for t in replay.decision_trace]
        axes[2].plot(range(8),slopes,'o-',color=color,label=LABELS[method])
    for ax in axes:ax.set_xticks(range(8),labels);ax.set_xlabel('JCAMP LONG DATE acquisition time');ax.legend(fontsize=8,ncol=4)
    axes[0].set_ylabel('Fixed completion-window area (a.u.·ppm)');axes[1].set_ylabel('Target S/N (unchanged picker)');axes[2].set_ylabel('Recorded slope / hour (latched after criterion)')
    threshold=replays[('2026-06-09','production')].config.completion.absolute_slope_threshold_per_hour
    axes[2].axhspan(-threshold,threshold,color='green',alpha=.1)
    note='\n'.join(LABELS[r['method']]+': '+r['completion_timestamp'] for r in outcomes)
    axes[0].text(1.02,.95,note,transform=axes[0].transAxes,va='top',fontsize=9)
    save_figure(fig,folder/'june09_all_phase_methods_vs_completion',group[0]['dataset_display_name'],
        'All Phase Methods vs Retrospective Completion — no physical stop established',manifest,formats=('png',))
    return outcomes


def verify_protected(root):
    log=root/'logs/additional_phase_methods'
    allowed={'PHASE_VALIDATION_INDEX.md','PHASE_VALIDATION_GALLERY.html','PHASE_VALIDATION_MANIFEST.json','OUTPUT_FILES.csv','OUTPUT_SUMMARY.json'}
    rows=[]
    for filename in ('protected_before.csv','raw_copies_before.csv','historical_processed_before.csv'):
        if not (log/filename).exists():continue
        for row in v.read_rows(log/filename):
            p=Path(row['path']);after=sha256(p)
            editable=p.parent==root/'phase_validation' and p.name in allowed
            rows.append({**row,'sha256_after':after,'unchanged':after==row['sha256_before'],'index_update_allowed':editable})
    write_rows(log/'protected_after.csv',rows)
    if not all(r['unchanged'] or r['index_update_allowed'] for r in rows):
        raise ValueError('Protected scientific evidence changed')
    return rows


def extend_gallery(root=v.DEFAULT_OUTPUT,*,deep=True):
    root=Path(root).resolve();gallery=root/'phase_validation';records=g.discover_acquisitions(root)
    if not (root/'logs/additional_phase_methods/protected_before.csv').is_file():
        raise ValueError('Capture pre-extension hash baseline before extending')
    all_results={};contexts={};statuses={};manifest=[];verification=[]
    for i,a in enumerate(records,1):
        folder=gallery/a['acquisition_id'];print(f'[{i}/27] Extending {a["acquisition_id"]}',flush=True)
        existing,checks=g.load_methods(root,a)
        results={m:existing[m] for m in g.METHODS};production=results['production']
        context=load_context(folder);contexts[a['acquisition_id']]=context
        write_json(folder/'phase_quality_context.json',context.metadata)
        for method in NEW_METHODS:
            status=method_status(folder,method,context,production,a,deep=deep);statuses[(a['acquisition_id'],method)]=status
            print(f'  {method}: {status["status"]}',flush=True)
            if not status['success']:continue
            result=v.analyze(production['spectrum'].source,production['args'],
                phase=(status['p0_deg'],status['p1_deg'],status.get('inverse_phase',False),method),unphased=production['unphased'])
            np.testing.assert_array_equal(result['unphased'],production['unphased'])
            np.testing.assert_array_equal(result['spectrum'].ppm_axis,production['spectrum'].ppm_axis)
            results[method]=result;g.export_arrays(folder,method,result,stem=STEMS[method])
        all_results[a['acquisition_id']]=results
        for method,result in results.items():
            verification.append({'acquisition_id':a['acquisition_id'],'method':method,'raw_sha256':a['raw_sha256'],
                'fft_sha256':context.metadata['fft_sha256'],'axis_sha256':context.metadata['axis_sha256'],
                'same_fft':np.array_equal(result['unphased'],production['unphased']),
                'same_axis':np.array_equal(result['spectrum'].ppm_axis,production['spectrum'].ppm_axis),
                'downstream_parameters_unchanged':{k:value for k,value in vars(result['args']).items() if k!='direct_phase'}==
                    {k:value for k,value in vars(production['args']).items() if k!='direct_phase'}})
        write_json(folder/'additional_phase_methods.json',{'implemented_spectra':list(results),
            'unavailable_methods':UNAVAILABLE,'new_method_status':{m:statuses[(a['acquisition_id'],m)] for m in NEW_METHODS},
            'quality_context_file':'phase_quality_context.json','raw_sha256':a['raw_sha256'],
            'manual_status':g.manual_status(folder)[0], 'provenance':provenance(),
            'additional_source_hashes':{p.name:sha256(p) for p in (Path(__file__),Path(__file__).with_name('phase_quality.py'),Path(__file__).with_name('phase_optimizers.py'))}})
    replays={};cohort_rows=[];date_outcomes=[]
    for date in dict.fromkeys(a['acquisition_date'] for a in records):
        group=[a for a in records if a['acquisition_date']==date]
        available=set.intersection(*(set(all_results[a['acquisition_id']]) for a in group))
        for method in dict.fromkeys(m for a in group for m in all_results[a['acquisition_id']]):
            if method not in available:continue  # never silently omit a failed acquisition from a sequence
            replay=v.replay_target_series([all_results[a['acquisition_id']][method] for a in group]);replays[(date,method)]=replay
            idx=replay.completion.completion_index
            date_outcomes.append({'date':date,'method':method,'complete':replay.completion.complete,
                'completion_timestamp':group[idx]['timestamp'] if idx is not None else 'NO COMPLETION',
                'classification':replay.completion.status,'warnings':'; '.join(replay.completion.quality_warnings),'physical_stop_verified':False})
        for index,a in enumerate(group):
            folder=gallery/a['acquisition_id'];results=all_results[a['acquisition_id']]
            decisions={m:report.decision_trace[index] for (d,m),report in replays.items() if d==date}
            local_status={m:statuses[(a['acquisition_id'],m)] for m in NEW_METHODS}
            rows=write_tables(folder,a,results,contexts[a['acquisition_id']],decisions,local_status)
            cohort_rows.extend(r for r in rows if not r['method'].startswith('manual_'))
            figures(folder,a,results,rows,contexts[a['acquisition_id']],manifest)
    write_rows(gallery/'all_dates_phase_completion_results.csv',date_outcomes)
    summaries=cohort_tables(gallery,cohort_rows,statuses,records,manifest)
    june=june_figures(gallery,records,all_results,replays,manifest)
    write_rows(gallery/'ADDITIONAL_FIGURE_MANIFEST.csv',manifest)
    write_rows(gallery/'ADDITIONAL_METHOD_VERIFICATION.csv',verification)
    failures=['# Phase method pairs requiring manual inspection','',
        'These diagnostic flags are review triggers, not changes to production QC and not proof that an algorithm is wrong. '
        'Thresholds: negative area >0.2; even imaginary >0.3; asymmetry/odd-real ratio >0.5; normalized broad tilt >0.5; '
        'absolute target-area change >50%; |P1| >=270°; detection/completion disagreement; underconstrained P1. '
        'Total imaginary dispersion is expected and is not itself a failure criterion.','',
        '| Acquisition | Method | Inspection reason |','|---|---|---|']
    for row in cohort_rows:
        if row['flags']:failures.append(f'| {row["acquisition_id"]} | {row["method"]} | {row["flags"]} |')
    for (ident,method),status in statuses.items():
        if not status['success']:failures.append(f'| {ident} | {method} | {status["status"]}: {status.get("message","")} |')
    (gallery/'PHASE_METHOD_FAILURES.md').write_text('\n'.join(failures)+'\n',encoding='utf-8')
    g.write_index(gallery,records)
    protected=verify_protected(root)
    summary={'acquisitions':len(records),'method_spectrum_counts':{m:sum(m in r for r in all_results.values()) for m in [*g.METHODS,*NEW_METHODS]},
        'unavailable_methods':UNAVAILABLE,'manual_checkpoints':sum(len(g.manual_status(gallery/a['acquisition_id'])[1]) for a in records),
        'protected_scientific_files_unchanged':sum(r['unchanged'] for r in protected),
        'same_FFT_checks':len(verification),'new_figures':len(manifest),'june09_outcomes':june}
    write_json(gallery/'ADDITIONAL_METHOD_SUMMARY.json',summary)
    print(json.dumps(summary,indent=2),flush=True)
    return summary


def refresh_manual_method_comparison(folder,output,manual):
    """Append genuine saved human result to expanded tables and overlays."""
    folder=Path(folder);output=Path(output)
    if not (folder/'all_phase_methods_results.csv').is_file():return
    checkpoint=g.json_read(output/'checkpoint.json')
    if checkpoint.get('analysis_role')!='manual_reference' or not checkpoint.get('explicit_gui_save'):return
    a=g.json_read(folder/'00_metadata.json');context=load_context(folder)
    meta=g.json_read(folder/'automated/processing_metadata.json')
    prod=v.analyze(meta['source_path'],v.production_args(meta['source_path'],meta['parameters']))
    method='manual_'+output.name;rows=v.read_rows(folder/'all_phase_methods_results.csv')
    completion=v.read_rows(output/'completion_comparison.csv')
    prefix=next(r for r in completion if r['scope']=='through_this_acquisition')
    decision={'complete':v.truth(prefix['manual_complete']),'status':prefix['manual_classification']}
    row=metrics_row(a,method,manual,prod,context,decision,{'success':True,'status':'explicit_gui_save','source':'Human accepted GUI checkpoint'})
    row['completion_changed']=v.truth(prefix['boolean_changed']);row['flags']='; '.join(failure_flags(row));row['checkpoint_path']=str(output)
    rows=[r for r in rows if r['method']!=method]+[row]
    write_rows(folder/'all_phase_methods_results.csv',rows);write_rows(folder/'phase_quality_metrics.csv',rows)
    cohort=folder.parent/'cohort_phase_quality_metrics.csv'
    if cohort.exists():
        cohort_rows=v.read_rows(cohort)
        cohort_rows=[r for r in cohort_rows if (r['acquisition_id'],r['method'])!=(a['acquisition_id'],method)]
        cohort_rows.append(row);write_rows(cohort,cohort_rows)
    comparisons=[];fig,axes=plt.subplots(1,2,figsize=(14,5),layout='constrained');manifest=[]
    for saved in rows:
        name=saved['method']
        if name.startswith('manual_'):continue
        result=v.analyze(meta['source_path'],prod['args'],phase=(float(saved['P0']),float(saved['P1']),saved['direction']=='inverse',name),unphased=prod['unphased'])
        for ax,component in zip(axes,('real','imaginary')):draw_curve(ax,result,name,g.TARGET_RANGE,component)
        comparisons.append({'automatic_method':name,'manual_checkpoint':output.name,
            'automatic_area':result['target']['area'],'manual_area':manual['target']['area'],
            'manual_minus_automatic_area':manual['target']['area']-result['target']['area'],
            'automatic_snr':result['target']['snr'],'manual_snr':manual['target']['snr'],
            'manual_minus_automatic_snr':manual['target']['snr']-result['target']['snr'],
            'automatic_detection':result['target']['detected'],'manual_detection':manual['target']['detected'],
            'automatic_prefix_completion':saved['completion_boolean'],'manual_prefix_completion':prefix['manual_complete']})
    for ax,component in zip(axes,('real','imaginary')):
        draw_curve(ax,manual,'manual',g.TARGET_RANGE,component);ax.legend(fontsize=7,ncol=2);ax.set_title(component.capitalize())
    g.save_plot(fig,output,'all_automatic_vs_manual_overlay',a,'All Automatic Methods vs Explicit Manual Checkpoint',manifest)
    write_rows(output/'all_automatic_vs_manual_metrics.csv',comparisons)
    write_rows(output/'all_methods_manual_figure_manifest.csv',manifest)
