"""Summarize saved synthetic evidence; never refit an automatic phase."""
import argparse
from pathlib import Path
import json
import hashlib
import warnings
import numpy as np
from scipy.stats import spearmanr
from scipy.signal import find_peaks,peak_widths
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import _bootstrap
from chemyx_lab.analysis import synthetic_phase_benchmark as b,nmr_validation as v,phase_gallery as g,additional_phase_methods as a
from chemyx_lab.analysis.phase_audit import write_json,write_rows,save_figure,sha256
from chemyx_lab.analysis.plot_titles import resolve_dataset_display_name

LABELS={**a.LABELS,'metadata_correct_oracle':'Correct metadata ORACLE','metadata_nominal_anchor':'Fixed nominal metadata'}
COLORS={**a.COLORS,'metadata_correct_oracle':'black','metadata_nominal_anchor':'#0072B2'}
METRICS=('negative_area_fraction','imaginary_residual','even_imaginary_fraction','peak_asymmetry','dispersive_metric','baseline_penalty','combined_quality_score')


def median(rows,key):
    values=[r[key] for r in rows if r.get(key) is not None and np.isfinite(r[key])]
    return float(np.median(values)) if values else None


def correlation(rows,x,y):
    pairs=[(r[x],r[y]) for r in rows if r.get(x) is not None and r.get(y) is not None and np.isfinite(r[x]) and np.isfinite(r[y])]
    if len(pairs)<3:return None,len(pairs)
    values=np.asarray(pairs)
    if np.ptp(values[:,0])==0 or np.ptp(values[:,1])==0:return None,len(pairs)
    return float(spearmanr(values[:,0],values[:,1]).statistic),len(pairs)


def calibration():
    rows=[]
    for folder in sorted((b.BENCH/'physical_fids').iterdir()):
        meta=g.json_read(folder/'metadata.json');source=folder/'synthetic_raw_fid.dx'
        result=v.analyze(source,b.production_arguments(source),phase=(0.,0.,False,'oracle'))
        x=result['spectrum'].ppm_axis;z=result['unphased'];ctx=a.prepare_context(x,z)
        mag=np.abs(z);dx=float(np.median(np.diff(x)))
        indices,_=find_peaks(mag,prominence=max(8*ctx.noise,.025*mag.max()),distance=max(1,int(.03/dx)))
        widths=peak_widths(mag,indices)[0]*dx if len(indices) else []
        separations=np.diff(np.sort(x[indices]))
        baseline=result['spectrum'].real-result['quantitative']
        rows.append({'physical_fid_id':folder.name,'condition':meta['condition'],'seed':meta['seed'],
            'target_snr':result['target']['snr'],'target_detected':result['target']['detected'],
            'target_height':result['target']['height'],'target_width_hz':meta['target_width_hz_realized'],
            'dominant_magnitude':float(mag.max()),'diagnostic_noise':ctx.noise,
            'baseline_rms_fraction':float(np.sqrt(np.mean(baseline**2))/mag.max()),
            'selected_peak_count':len(indices),'width_ppm_median':float(np.median(widths)) if len(widths) else None,
            'minimum_peak_separation_ppm':float(np.min(separations)) if len(separations) else None,
            'median_peak_separation_ppm':float(np.median(separations)) if len(separations) else None,
            'raw_sha256':sha256(source),'acquired_points':8192,'processed_points':65536})
    write_rows(b.BENCH/'synthetic_calibration.csv',rows)
    real=v.read_rows(b.BENCH/'real_cohort_calibration.csv');comparisons=[]
    for key in ('target_snr','target_height','target_width_hz','dominant_magnitude','baseline_rms_fraction','selected_peak_count','width_ppm_median','minimum_peak_separation_ppm','median_peak_separation_ppm'):
        for cohort,selected in [('real_27',real),('synthetic_40',rows),('synthetic_typical',[r for r in rows if r['condition'] not in ('broad_peaks','edge_truncation','dense_reference')])]:
            values=[float(r[key]) for r in selected if r.get(key) not in ('',None) and (key!='target_snr' or v.truth(r['target_detected']))]
            comparisons.append({'cohort':cohort,'property':key,'n':len(values),'min':float(np.min(values)),
                'q25':float(np.quantile(values,.25)),'median':float(np.median(values)),'q75':float(np.quantile(values,.75)),'max':float(np.max(values))})
    write_rows(b.BENCH/'calibration_distributions.csv',comparisons)
    lines=['# Calibration against the 27 real acquisitions','',
        'All comparisons use the same definitions: production target S/N/height/QC widths, phase-invariant magnitude peak widths/separations at the same prominence threshold, and RMS global baseline divided by maximum magnitude. '
        'Synthetic baseline is measured at known correct phase; real baseline uses retained production phase, whose correctness is unknown.','',
        '| Property | Real median (Q25–Q75) | Synthetic median (Q25–Q75) |','|---|---|---|']
    for key in dict.fromkeys(r['property'] for r in comparisons):
        r=next(z for z in comparisons if z['property']==key and z['cohort']=='real_27')
        s=next(z for z in comparisons if z['property']==key and z['cohort']=='synthetic_40')
        lines.append(f'| {key} | {r["median"]:.5g} ({r["q25"]:.5g}–{r["q75"]:.5g}) | {s["median"]:.5g} ({s["q25"]:.5g}–{s["q75"]:.5g}) |')
    lines+=['','The generator matches physical acquisition count/duration, spectral width/frequency, zero fill, apodization, weak-target/strong-solvent amplitude scale and target-width scale. '
        'Synthetic major Lorentzian resonances emphasize the broader part of the real magnitude-width distribution. Broad/edge families intentionally stress widths beyond typical acquisitions; the dense family probes a separate model-domain assumption. '
        'The observed calibration table exposes these differences rather than asserting all distributions match. Sparse six-resonance families and unequal dominant/target amplitudes resemble this experiment more than generic dense textbook spectra. '
        'The target’s realized S/N differs from its nominal Gaussian noise design because production baseline, smoothing and QC affect the measured value. Noise failures/no detections are retained. '
        'These finite analytic line/noise/receiver-artifact families cannot reproduce unknown shimming, multiplet structure, proprietary digital filtering or every baseline artifact. Conclusions are restricted to the declared families.']
    (b.BENCH/'CALIBRATION.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return rows,real


def collect():
    rows=[];cases=v.read_rows(b.BENCH/'synthetic_cases.csv')
    for case in cases:
        folder=b.BENCH/'cases'/case['case_id'];metadata=g.json_read(folder/'metadata.json')
        for mode in ('classical','deep'):
            path=folder/(mode+'_results.json')
            if not path.exists():raise RuntimeError('Unfinished case: '+str(path))
            values=g.json_read(path)
            for row in values:
                if row['observed_fft_sha256']!=metadata['observed_fft_sha256']:raise ValueError('Stale synthetic cache')
                row['physical_target_ppm']=metadata['physical_metadata']['resonances'][3]['ppm']
                row['physical_target_ppm_absolute_error']=abs(row['target_peak_ppm']-row['physical_target_ppm']) if row.get('target_peak_ppm') is not None else None
                if row['success']:
                    ideal=metadata['noisefree_target']
                    row['noisefree_target_detected']=ideal['detected']
                    row['detection_discrepancy_vs_noisefree']=row['target_detected']!=ideal['detected']
                    row['known_physical_target_present']=True
                    row['known_target_not_detected']=not row['target_detected']
                    for metric in ('height','area','snr'):
                        value=ideal[metric]
                        row[metric+'_percent_error_vs_noisefree']=100*row[metric+'_error_vs_noisefree']/abs(value) if abs(value)>1e-12 else None
                row['peak_phase_errors_deg']=json.dumps(row.get('peak_phase_errors_deg',[]))
                rows.append(row)
    assert len(rows)==len(cases)*8
    write_rows(b.BENCH/'synthetic_results.csv',rows)
    return cases,rows


def summaries(rows):
    keys=('p0_error_deg','p1_error_deg','phase_ramp_rms_deg','phase_ramp_max_deg','signal_weighted_phase_rms_deg',
        'peak_phase_rms_deg','peak_ppm_absolute_error','height_percent_error','area_percent_error','snr_percent_error',
        'area_absolute_error','snr_absolute_error','physical_target_ppm_absolute_error',
        'height_percent_error_vs_noisefree','area_percent_error_vs_noisefree','snr_percent_error_vs_noisefree')
    summaries=[];rng=np.random.default_rng(20261002)
    for condition in ('all_cases','replicated_only',*b.CONDITIONS):
        for method in (*b.ESTIMATORS,'metadata_correct_oracle','metadata_nominal_anchor'):
            selected=[r for r in rows if r['method']==method and (condition=='all_cases' or
                condition=='replicated_only' and r['design_stratum']=='independent_seed_replication' or r['condition']==condition)]
            valid=[r for r in selected if r['success']]
            record={'condition':condition,'method':method,'is_estimator':method in b.ESTIMATORS,'n_cases':len(selected),
                'successful_outputs':len(valid),'failure_count':len(selected)-len(valid),
                'recovered_within_5deg_count':sum(r['phase_recovered_within_5deg'] for r in valid),
                'detection_error_count':sum(r['detection_error'] for r in valid),
                'detection_discrepancy_vs_noisefree_count':sum(r['detection_discrepancy_vs_noisefree'] for r in valid),
                'known_target_not_detected_count':sum(r['known_target_not_detected'] for r in valid),
                'area_percent_error_n':sum(r.get('area_percent_error') is not None for r in valid),
                'oracle_no_detection_count':sum(not r['oracle_detected'] for r in valid)}
            for key in keys:
                record[key+'_median']=median(valid,key)
                values=[r[key] for r in valid if r.get(key) is not None]
                record[key+'_q25']=float(np.quantile(values,.25)) if values else None
                record[key+'_q75']=float(np.quantile(values,.75)) if values else None
            if condition in ('all_cases','replicated_only') and valid:
                blocks={key:[r['phase_ramp_rms_deg'] for r in valid if (r['condition'],r['seed'])==key] for key in {(r['condition'],r['seed']) for r in valid}}
                values=list(blocks.values());replicates=[]
                for _ in range(1000):
                    draw=rng.integers(0,len(values),len(values));replicates.append(float(np.median([z for i in draw for z in values[i]])))
                record['phase_ramp_median_cluster_bootstrap_ci_low'],record['phase_ramp_median_cluster_bootstrap_ci_high']=map(float,np.quantile(replicates,[.025,.975]))
            summaries.append(record)
    write_rows(b.BENCH/'method_summary.csv',summaries)
    correlations=[];valid=[r for r in rows if r['is_estimator'] and r['success']]
    for condition in ('all_cases','replicated_only',*b.CONDITIONS):
        selected=[r for r in valid if condition=='all_cases' or
            condition=='replicated_only' and r['design_stratum']=='independent_seed_replication' or r['condition']==condition]
        for metric in METRICS:
            phase,n=correlation(selected,metric,'phase_ramp_rms_deg');area,n_area=correlation(selected,metric,'area_percent_error')
            correlations.append({'condition':condition,'metric':metric,'spearman_phase_rms':phase,'n_phase':n,
                'spearman_area_percent_error':area,'n_area':n_area,'interpretation':'descriptive paired-case association; no independent-case p-value'})
    write_rows(b.BENCH/'quality_correlations.csv',correlations)
    selections=[];false_friends=[]
    for case in dict.fromkeys(r['case_id'] for r in valid):
        candidates=[r for r in valid if r['case_id']==case]
        best=min(candidates,key=lambda r:r['phase_ramp_rms_deg'])
        for metric in METRICS:
            chosen=min(candidates,key=lambda r:r[metric])
            tied=[r for r in candidates if np.isclose(r[metric],chosen[metric],rtol=1e-12,atol=1e-12)]
            record={'case_id':case,'condition':chosen['condition'],'design_stratum':chosen['design_stratum'],
                'metric':metric,'selected_method':chosen['method'],
                'metric_value':chosen[metric],'selected_true_phase_rms_deg':chosen['phase_ramp_rms_deg'],
                'selected_area_percent_error':chosen.get('area_percent_error'),'lowest_phase_error_method':best['method'],
                'lowest_phase_error_deg':best['phase_ramp_rms_deg'],
                'within_1deg_of_lowest_phase_error':chosen['phase_ramp_rms_deg']<=best['phase_ramp_rms_deg']+1.,
                'selected_phase_wrong_by_5deg_screen':chosen['phase_ramp_rms_deg']>5,
                'minimum_metric_tie_count':len(tied),'minimum_metric_tied_methods':';'.join(r['method'] for r in tied),
                'minimum_metric_tied_best_phase_error_deg':min(r['phase_ramp_rms_deg'] for r in tied),
                'minimum_metric_tied_worst_phase_error_deg':max(r['phase_ramp_rms_deg'] for r in tied),
                'tie_policy':'Stable configured estimator order; tied phase-error range recorded separately'}
            selections.append(record)
            if record['selected_phase_wrong_by_5deg_screen']:false_friends.append(record)
    write_rows(b.BENCH/'best_metric_selection.csv',selections);write_rows(b.BENCH/'metric_false_friends.csv',false_friends)
    objective_comparisons=[]
    for case in dict.fromkeys(r['case_id'] for r in rows):
        local=[r for r in rows if r['case_id']==case]
        oracle=next(r for r in local if r['method']=='metadata_correct_oracle')
        for method in ('combined_objective_v1','symmetry_objective'):
            chosen=next(r for r in local if r['method']==method)
            if not chosen['success']:continue
            score=lambda r:r['combined_quality_score'] if method=='combined_objective_v1' else r['peak_asymmetry']+.1*r['negative_area_fraction']
            difference=score(chosen)-score(oracle)
            bounds=b.opt.OPTIMIZER_CONFIG['bounds_deg']
            feasible=all(lo<=oracle[key]<=hi for (lo,hi),key in zip(bounds,('p0_direct_deg','p1_direct_deg')))
            objective_comparisons.append({'case_id':case,'condition':chosen['condition'],'method':method,
                'chosen_score':score(chosen),'correct_phase_score':score(oracle),
                'chosen_minus_correct_phase_score':difference,'known_phase_inside_optimizer_bounds':feasible,
                'phase_ramp_rms_deg':chosen['phase_ramp_rms_deg'],
                'phase_wrong_by_5deg_screen':chosen['phase_ramp_rms_deg']>5,
                'comparison':('objective favors chosen estimate over known phase' if difference < -1e-8 else
                    'chosen score worse than feasible known phase' if difference > 1e-8 and feasible else 'numerically similar scores')})
    write_rows(b.BENCH/'objective_truth_disagreement.csv',objective_comparisons)
    return summaries,correlations,selections


def plots(cases,rows,summary,correlations,calibrated,real):
    output=b.BENCH/'plots';output.mkdir(exist_ok=True);manifest=[]
    identity=resolve_dataset_display_name(metadata={'dataset_name':'SYNTHETIC GROUND TRUTH'},input_paths=b.BENCH)
    valid=[r for r in rows if r['is_estimator'] and r['success']]
    def save(fig,name,description,dataset=identity):save_figure(fig,output/name,dataset,description,manifest,formats=('png','svg'))
    fig,axes=plt.subplots(1,2,figsize=(13,5),layout='constrained')
    for method in b.ESTIMATORS:
        selected=[r for r in valid if r['method']==method]
        axes[0].scatter([r['true_corrective_direct_p0_deg'] for r in selected],b.circular_degrees([r['p0_direct_deg'] for r in selected]),s=12,alpha=.4,label=LABELS[method],color=COLORS[method])
        axes[1].scatter([r['true_corrective_direct_p1_deg'] for r in selected],[r['p1_direct_deg'] for r in selected],s=12,alpha=.4,color=COLORS[method])
    for ax,key in zip(axes,('P0','P1')):
        ax.plot([-180,180],[-180,180],'k--',lw=.7);ax.set(xlabel='True corrective '+key+' (deg)',ylabel='Recovered direct '+key+' (deg)',title=key+' — compare phase functions at wrap boundaries')
    axes[1].set_xscale('symlog',linthresh=180);axes[1].set_yscale('symlog',linthresh=180)
    axes[0].legend(fontsize=7);save(fig,'true_vs_recovered_phase','True vs Recovered Phase Parameters')
    keys=('phase_ramp_rms_deg','signal_weighted_phase_rms_deg','area_percent_error','snr_percent_error')
    fig,axes=plt.subplots(2,2,figsize=(14,10),layout='constrained')
    for ax,key in zip(axes.flat,keys):
        data=[[r[key] for r in valid if r['method']==m and r.get(key) is not None] for m in b.ESTIMATORS]
        ax.boxplot(data,tick_labels=[a.TICK_LABELS[m] for m in b.ESTIMATORS],showfliers=False)
        for i,values in enumerate(data):ax.scatter(i+1+np.linspace(-.15,.15,len(values)),values,s=7,alpha=.3,color=COLORS[b.ESTIMATORS[i]])
        ax.set(title=key,ylabel='Degrees' if 'phase' in key else 'Absolute percent error');ax.set_yscale('symlog',linthresh=1.)
    save(fig,'errors_by_method','Phase and Measurement Errors — all cases, failures separately tabulated')
    fig,axes=plt.subplots(2,1,figsize=(14,9),layout='constrained')
    for ax,key in zip(axes,('phase_ramp_rms_deg_median','area_percent_error_median')):
        data=np.array([[next(r for r in summary if r['condition']==c and r['method']==m)[key] for c in b.CONDITIONS] for m in b.ESTIMATORS],float)
        im=ax.imshow(np.log1p(data),aspect='auto',cmap='magma');fig.colorbar(im,ax=ax,label='log(1 + median error)')
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):ax.text(j,i,f'{data[i,j]:.1f}' if np.isfinite(data[i,j]) else 'N/A',ha='center',va='center',fontsize=8,color='white')
        ax.set_xticks(range(len(b.CONDITIONS)),[c.replace('_','\n') for c in b.CONDITIONS],fontsize=8)
        ax.set_yticks(range(6),[a.TICK_LABELS[m].replace('\n',' ') for m in b.ESTIMATORS]);ax.set_title(key)
    save(fig,'errors_by_difficulty','Median Errors by Difficulty — numbers retain original units')
    fig,axes=plt.subplots(2,3,figsize=(15,9),layout='constrained')
    for ax,method in zip(axes.flat,b.ESTIMATORS):
        selected=[r for r in valid if r['method']==method]
        ax.scatter([r['oracle_snr'] for r in selected],[r['phase_ramp_rms_deg'] for r in selected],s=12,alpha=.5,color=COLORS[method]);ax.set(title=LABELS[method],xlabel='Realized oracle target S/N',ylabel='Phase-ramp RMS error (deg)');ax.set_yscale('symlog',linthresh=1)
    save(fig,'phase_error_vs_snr','Phase Error vs Realized Target S/N — zero includes failed target QC')
    selected_metrics=('negative_area_fraction','imaginary_residual','even_imaginary_fraction','peak_asymmetry','baseline_penalty','combined_quality_score')
    for name,ylabel,ykey in [('quality_vs_true_phase','True phase-ramp RMS error (deg)','phase_ramp_rms_deg'),('quality_vs_area_error','Absolute target-area error (%)','area_percent_error')]:
        fig,axes=plt.subplots(2,3,figsize=(15,9),layout='constrained')
        for ax,metric in zip(axes.flat,selected_metrics):
            for method in b.ESTIMATORS:
                selected=[r for r in valid if r['method']==method and r.get(ykey) is not None]
                ax.scatter([r[metric] for r in selected],[r[ykey] for r in selected],s=8,alpha=.3,color=COLORS[method],label=LABELS[method])
            rho,n=correlation(valid,metric,ykey);ax.set(title=f'{metric}\nrho={rho:.3f}, n={n}' if rho is not None else metric,xlabel='Dimensionless diagnostic',ylabel=ylabel);ax.set_yscale('symlog',linthresh=1)
        axes[0,0].legend(fontsize=6);save(fig,name,'Phase Quality vs '+('Known Phase Accuracy' if 'true_phase' in name else 'Known Target-Area Error'))
    fig,axes=plt.subplots(2,3,figsize=(15,9),layout='constrained')
    for ax,key in zip(axes.flat,('target_snr','target_width_hz','target_height','dominant_magnitude','baseline_rms_fraction','minimum_peak_separation_ppm')):
        rr=[float(r[key]) for r in real if r.get(key) not in ('',None) and (key!='target_snr' or v.truth(r['target_detected']))]
        ss=[float(r[key]) for r in calibrated if r.get(key) is not None and (key!='target_snr' or r['target_detected'])]
        ax.boxplot([rr,ss],tick_labels=['Real 27','Synthetic 40'],showfliers=False)
        for i,values in enumerate((rr,ss)):ax.scatter(i+1+np.linspace(-.12,.12,len(values)),values,s=12,alpha=.6)
        ax.set(title=key,ylabel='Measured value; same definition')
        if key in ('target_height','dominant_magnitude','minimum_peak_separation_ppm'):ax.set_yscale('symlog',linthresh=.01)
    real_identity=resolve_dataset_display_name(metadata={'dataset_name':' / '.join(dict.fromkeys(r['timestamp'][:10] for r in real))})
    save(fig,'real_vs_synthetic_calibration','Real-Cohort vs Synthetic Calibration',real_identity+' / '+identity)
    for condition in b.CONDITIONS:
        case=next(r for r in cases if r['condition']==condition and r['design_stratum']=='independent_seed_replication' and float(r['imposed_p0_deg'])==-60.)
        folder=b.BENCH/'cases'/case['case_id'];meta=g.json_read(folder/'metadata.json')
        with np.load(folder/'synthetic_arrays.npz') as arrays:x=arrays['ppm'];oracle=arrays['known_correct_phase_fft'];observed=arrays['observed_fft'];raw=arrays['raw_complex_fid']
        ideal_source=Path(meta['source_path']).with_name('synthetic_noisefree_fid.dx')
        ideal=v.analyze(ideal_source,b.production_arguments(ideal_source),phase=(0.,0.,False,'oracle'))['unphased']
        recovered={}
        for mode in ('classical','deep'):
            with np.load(folder/(mode+'_recovered_spectra.npz')) as arrays:
                for name in b.ESTIMATORS:
                    if name in arrays:recovered[name]=arrays[name]
        curves=[('Ideal noisefree',ideal),('Oracle: same noise/baseline',oracle),('Distorted input',observed),*[(LABELS[m],recovered.get(m)) for m in b.ESTIMATORS]]
        for region,bounds in [('target',(5.6,6.0)),('full',(0.,12.))]:
            fig,axes=plt.subplots(3,3,figsize=(16,11),layout='constrained',sharex=True,sharey=True)
            for ax,(name,z) in zip(axes.flat,curves):
                if z is None:ax.text(.5,.5,'FAILED — no spectrum fabricated',ha='center',transform=ax.transAxes)
                else:ax.plot(x,z.real,lw=.8)
                ax.set(title=name,xlim=(bounds[1],bounds[0]),xlabel='Chemical shift (ppm)',ylabel='Real intensity (a.u.)');ax.axhline(0,lw=.4,color='gray')
            if region=='target':
                mask=(x>=bounds[0])&(x<=bounds[1]);yy=np.concatenate([z.real[mask] for _,z in curves if z is not None]);lo,hi=yy.min(),yy.max();axes[0,0].set_ylim(lo-.05*max(hi-lo,1),hi+.05*max(hi-lo,1))
            save(fig,'representative_'+case['case_id']+'_'+region,'Known Truth and All Phase Recoveries — '+region,case['case_id'])
    write_rows(b.BENCH/'FIGURE_MANIFEST.csv',manifest)
    links=['# Benchmark figures','',*['- ['+Path(r['path']).stem+']('+Path(r['path']).name+')' for r in manifest if Path(r['path']).suffix=='.png']]
    (output/'README.md').write_text('\n'.join(links)+'\n',encoding='utf-8')
    return manifest


def report(cases,rows,summary,correlations,selections):
    def number(value):
        return f'{value:.4g}' if value is not None and np.isfinite(value) else 'N/A'
    pooled=[r for r in summary if r['condition']=='all_cases' and r['is_estimator']]
    lines=['# Synthetic ground-truth scientific summary','',
        'The real .dx exports contain raw complex time-domain FIDs with accompanying phase metadata; their numerical tables are not already phased spectra. '
        'After FFT processing, the real spectra contain no independent record of exactly correct P0/P1. Different automatic methods can agree and still be wrong. '
        'Here, analytic complex time-domain resonance FIDs have known phase, and the exact imposed linear phase is recorded on the production FFT grid. '
        'This permits objective error measurement while preserving the same downstream analysis.','',
        f'**{len(cases)} phase-distorted cases from 40 independently seeded physical FIDs**, covering ten conditions. '
        'P0 spans -120 to +120 degrees; P1 includes -160,-79,-20,0,20,79,160 degrees in the full k/N convention. '
        'The truth boundary, paired design and processed-FID representation are explicit in [BENCHMARK_DESIGN.md](synthetic_ground_truth/BENCHMARK_DESIGN.md).','',
        '| Estimator | Median P0 error (deg) | Median P1 error (deg) | Median phase RMS (deg) | Median signal-weighted RMS (deg) | Median area error (%) | Median S/N error (%) | Detection errors | Runtime/optimizer failures | ≤5° recovery count |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in pooled:lines.append(f'| {r["method"]} | {r["p0_error_deg_median"]:.4g} | {r["p1_error_deg_median"]:.4g} | {r["phase_ramp_rms_deg_median"]:.4g} | {r["signal_weighted_phase_rms_deg_median"]:.4g} | {r["area_percent_error_median"]:.4g} | {r["snr_percent_error_median"]:.4g} | {r["detection_error_count"]} | {r["failure_count"]} | {r["recovered_within_5deg_count"]}/{r["n_cases"]} |')
    lines+=['','Production-style correct metadata is an oracle application, not an optimizer. Fixed nominal metadata is a separate anchor. '
        'Undefined percent errors for zero oracle measurements are excluded with explicit sample counts; detection errors retain those cases. '
        'Primary detection errors mean disagreement with the correctly phased noisy control, isolating phase choice. '
        'A real physical target is present in every synthetic FID; separate noisefree-reference detection discrepancies and known-target non-detections '
        'expose total losses from noise, artifacts and phase rather than labelling noisy-control agreement as physical truth. '
        'Noisefree-relative measurement errors are also retained. '
        'Phase RMS includes empty spectral regions, so signal-weighted and resonance-specific phase errors provide complementary interpretation.','',
        'The pooled 234-case table weights the larger clean phase grid more heavily. A separate `replicated_only` summary '
        'uses the balanced 90-case stratum (three independent seeds × three phases per family). '
        'Use condition-specific and balanced summaries alongside the pooled table; repeated injections are not independent samples.','',
        '## Performance depends on the declared condition','',
        '| Condition | Lowest median phase-RMS estimator | Median phase RMS (deg) | Lowest median area-error estimator | Area error (%) |','|---|---|---:|---|---:|']
    winners=[]
    for condition in b.CONDITIONS:
        selected=[r for r in summary if r['condition']==condition and r['is_estimator']]
        phase=min(selected,key=lambda r:r['phase_ramp_rms_deg_median']);area=min(selected,key=lambda r:r['area_percent_error_median'] if r['area_percent_error_median'] is not None else np.inf)
        winners.append(phase['method']);lines.append(f'| {condition} | {phase["method"]} | {number(phase["phase_ramp_rms_deg_median"])} | {area["method"]} | {number(area["area_percent_error_median"])} |')
    lines+=['',('The same estimator minimizes phase RMS in every tested family, but that is evidence for this finite generator/domain, not universal superiority.' if len(set(winners))==1 else
        'No universal winner is established: the method minimizing median phase RMS changes across conditions. Phase and target-area winners can also differ.'),
        'Condition-specific tables include errors, failures, detection disagreements and quartiles. Block-bootstrap intervals resample independent physical FIDs, not repeated phase injections.','',
        '## Which quality metrics predicted truth?','',
        '| Diagnostic | Spearman rho with phase RMS | Spearman rho with area error | Best-metric selection within 1° of lowest phase error |','|---|---:|---:|---:|']
    for metric in METRICS:
        r=next(z for z in correlations if z['condition']=='all_cases' and z['metric']==metric)
        chosen=[z for z in selections if z['metric']==metric]
        phase_text=f'{r["spearman_phase_rms"]:.4f}' if r['spearman_phase_rms'] is not None else 'undefined'
        area_text=f'{r["spearman_area_percent_error"]:.4f}' if r['spearman_area_percent_error'] is not None else 'undefined'
        lines.append(f'| {metric} | {phase_text} | {area_text} | {sum(z["within_1deg_of_lowest_phase_error"] for z in chosen)}/{len(chosen)} |')
    lines+=['','These are descriptive associations across paired cases, not independent-sample p-values or proof of causality. '
        'Condition-specific correlations can differ. Expected odd imaginary dispersion prevents total imaginary energy from serving as a universal phase target. '
        'A low quality score can coexist with wrong phase; concrete examples are in [metric_false_friends.csv](synthetic_ground_truth/metric_false_friends.csv). '
        'Metric ties use stable configured estimator order; the selection CSV separately exposes tied-method count and best/worst true errors, so a tie is not treated as evidence for the selected method.','',
        '## Demonstrated limitations','',
        '| Estimator | Largest condition-median phase RMS | Degrees | Failures in that family | Detection disagreements |',
        '|---|---|---:|---:|---:|']
    for method in b.ESTIMATORS:
        selected=[r for r in summary if r['method']==method and r['condition'] in b.CONDITIONS]
        worst=max(selected,key=lambda r:r['phase_ramp_rms_deg_median'])
        lines.append(f'| {method} | {worst["condition"]} | {worst["phase_ramp_rms_deg_median"]:.4g} | {worst["failure_count"]} | {worst["detection_error_count"]} |')
    lines+=['','These measured limitations do not establish mechanistic causes. Ernst fixes P1=0 by design, so it cannot fully recover an imposed nonzero P1.']
    comparisons=v.read_rows(b.BENCH/'objective_truth_disagreement.csv')
    for method in ('combined_objective_v1','symmetry_objective'):
        wrong=[r for r in comparisons if r['method']==method and v.truth(r['phase_wrong_by_5deg_screen'])]
        favorable=sum(r['comparison']=='objective favors chosen estimate over known phase' for r in wrong)
        worse=sum(r['comparison']=='chosen score worse than feasible known phase' for r in wrong)
        lines+=['',f'**{method}:** {favorable} estimates with phase RMS >5° scored better than known correct phase under their own objective; '
            f'{worse} scored worse than that feasible reference. The first group demonstrates objective/truth mismatch; '
            'the second demonstrates a search miss relative to an available reference, without proving a specific local-minimum mechanism. '
            'See [objective comparison](synthetic_ground_truth/objective_truth_disagreement.csv).']
    failed=[r for r in rows if r['is_estimator'] and not r['success']]
    if failed:
        lines+=['','Execution/convergence failures are distinct from completed-but-inaccurate estimates:','',
            '| Case | Method | Recorded status |','|---|---|---|']
        for r in failed:lines.append(f'| [{r["case_id"]}](synthetic_ground_truth/cases/{r["case_id"]}/optimizer/{r["method"]}.json) | {r["method"]} | {r["status"]} |')
    lines+=['','DEEP has a separate dense-reference family and a [low-field applicability audit](documentation/DEEP_PHASER_LOW_FIELD_APPLICABILITY.md). '
        'Sparse, dominant-solvent low-field reaction data should not inherit validation merely because the neural network runs.','',
        '## Implications for the 27 real acquisitions','',
        'Synthetic success establishes recovery only where truth is known under these assumptions. It does not select the correct phase of a specific real spectrum. '
        'Real method disagreement supplies a transparent [manual-review queue](MANUAL_REVIEW_PRIORITY.md), with June 9 completion and August detection cases highlighted. '
        'Consensus is agreement, not ground truth. All real acquisitions remain **MANUAL REVIEW PENDING**. '
        'The next human task is to adjust/accept phase in the GUI and save independent checkpoints for the informative cases.']
    (b.BENCH.parent/'SYNTHETIC_GROUND_TRUTH_SUMMARY.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return pooled


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--calibration-only',action='store_true');args=parser.parse_args()
    calibrated,real=calibration()
    if args.calibration_only:return
    cases,rows=collect();summary,correlations,selections=summaries(rows)
    manifest=plots(cases,rows,summary,correlations,calibrated,real);pooled=report(cases,rows,summary,correlations,selections)
    write_json(b.BENCH/'SUMMARY.json',{'phase_cases':len(cases),'independent_physical_fids':len(calibrated),
        'method_results':len(rows),'figure_files':len(manifest),'pooled_estimator_results':pooled,
        'source_hashes':{str(p):sha256(p) for p in (Path(__file__),v.ROOT/'chemyx_lab/analysis/synthetic_phase_benchmark.py',v.ROOT/'chemyx_lab/analysis/phase_review_priority.py')}})
    print(json.dumps(pooled,indent=2))

if __name__=='__main__':main()
