"""Browseable phase comparisons over preserved production validation evidence.

No optimizer, baseline, peak gate or completion algorithm is implemented here.
Stored alternative parameters are replayed through nmr_validation's shared path.
"""
from __future__ import annotations
import json
import shutil
from pathlib import Path
from dataclasses import asdict
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from . import nmr_validation as v
from .phase_audit import sha256,write_json,write_rows,save_figure,provenance
from .nmr import read_jcamp_fid

METHODS=('unphased','production','acme','peak_minima')
LABELS={'unphased':'Unphased FFT [before phase correction]','production':'DX metadata phase [PHC0/PHC1 from .dx]',
        'acme':'ACME PHASE','peak_minima':'PEAK-MINIMA PHASE','manual':'MANUAL CHECKPOINT'}
COLORS={'unphased':'#777777','production':'#0072B2','acme':'#D55E00','peak_minima':'#009E73','manual':'#CC79A7'}
STEMS={'unphased':'02_unphased_fft','production':'03_production_phase',
       'acme':'04_acme_phase','peak_minima':'05_peak_minima_phase'}
TARGET_RANGE=(5.6,6.0)
CRITICAL_TIMES=('10:53:46','11:08:23','11:24:34','11:36:54')


def json_read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def audit_existing(root):
    """Capture the pre-reorganization inventory before creating gallery files."""
    root=Path(root)
    snapshot=root/'logs/phase_output_inventory_before.json'
    if snapshot.exists():return json_read(snapshot)
    files=[p for p in root.rglob('*') if p.is_file() and 'logs' not in p.relative_to(root).parts]
    categories={
        'Phase-related plots':[str(p.resolve()) for p in files if p.suffix.lower() in {'.png','.svg','.pdf'}],
        'Numerical and comparison CSVs':[str(p.resolve()) for p in files if p.suffix.lower()=='.csv'],
        'Phase parameter JSONs':[str(p.resolve()) for p in files if p.name in {'phase_parameters.json','manual_review_start.json','processing_metadata.json','checkpoint.json'}],
        'ACME results':[str(p.resolve()) for p in files if 'acme' in p.relative_to(root).parts],
        'Peak-minima results':[str(p.resolve()) for p in files if 'peak_minima' in p.relative_to(root).parts],
        'Manual checkpoints':[str(p.resolve()) for p in files if p.name=='checkpoint.json'],
        'GUI-loadable inputs':[str(p.resolve()) for p in files if p.name in {'spectral_evidence.npz','processing_metadata.json'} and p.parent.name=='automated'],
    }
    rows=[]
    for a in v.load_cohort():
        folder=root/'acquisitions'/a['acquisition_id']
        rows.append({'acquisition_id':a['acquisition_id'],
            'unphased_production_plot':(folder/'automated/phase_before_after.png').is_file(),
            'acme_audit_plot':(folder/'alternative_methods/acme/phase_before_after.png').is_file(),
            'peak_minima_audit_plot':(folder/'alternative_methods/peak_minima/phase_before_after.png').is_file(),
            'production_full_csv':(folder/'automated/automatically_phased_spectrum.csv').is_file(),
            'acme_full_csv':(folder/'alternative_methods/acme/automatically_phased_spectrum.csv').is_file(),
            'peak_minima_full_csv':(folder/'alternative_methods/peak_minima/automatically_phased_spectrum.csv').is_file(),
            'gui_input':all((folder/'automated'/f).is_file() for f in ('spectral_evidence.npz','processing_metadata.json')),
            'manual_directory':(folder/'manual_reviews').is_dir(),
            'obvious_four_method_comparison':(root/'phase_validation'/a['acquisition_id']/'06_phase_comparison_full.png').is_file()})
    data={'captured_at_utc':provenance()['created_at_utc'],'scope':'All scientific files under validation root; logs, caches and test fixtures excluded.',
          'categories':categories,'acquisitions':rows}
    write_json(snapshot,data)
    return data


def load_methods(root,acquisition):
    """Recreate analysis and verify exact equality with the retained alternatives."""
    root=Path(root)
    old=root/'acquisitions'/acquisition['acquisition_id']
    meta=json_read(old/'automated/processing_metadata.json')
    source=Path(acquisition['source_path'])
    if sha256(source)!=acquisition['raw_sha256']:raise ValueError('Raw hash mismatch')
    args=v.production_args(source,meta['parameters'])
    prod=v.analyze(source,args)
    results={'production':prod}
    for method in ('acme','peak_minima'):
        saved=json_read(old/'alternative_methods'/method/'phase_parameters.json')
        results[method]=v.analyze(source,args,phase=(saved['p0_deg'],saved['p1_deg'],saved['inverse_phase'],method),unphased=prod['unphased'])
    results['unphased']=v.analyze(source,args,phase=(0.,0.,False,'none'),unphased=prod['unphased'])
    checks=[]
    for method,result in results.items():
        spec=result['spectrum']
        np.testing.assert_array_equal(spec.ppm_axis,prod['spectrum'].ppm_axis)
        np.testing.assert_array_equal(result['unphased'],prod['unphased'])
        if method!='unphased':
            folder=old/'automated' if method=='production' else old/'alternative_methods'/method
            with np.load(folder/'spectral_evidence.npz') as saved:
                np.testing.assert_array_equal(saved['ppm'],spec.ppm_axis)
                np.testing.assert_array_equal(saved['phased'],spec.real+1j*spec.imaginary)
                np.testing.assert_array_equal(saved['quantitative_real'],result['quantitative'])
            assert result['target']==json_read(folder/'target_result.json')
        checks.append({'acquisition_id':acquisition['acquisition_id'],'method':method,
            'raw_sha256':acquisition['raw_sha256'],'same_fft_and_axis':True,
            'saved_arrays_and_metrics_match':'NOT APPLICABLE' if method=='unphased' else True,
            'notes':'Unphased downstream analysis newly exposed, using identical production parameters' if method=='unphased' else 'Exact retained-array and target-metric equality'})
    assert prod['spectrum'].phase0_deg==meta['phase']['p0_deg']
    assert prod['spectrum'].phase1_deg==meta['phase']['p1_deg']
    return results,checks


def target_limits(results,bounds=TARGET_RANGE,component='real'):
    values=[]
    for result in results.values():
        spec=result['spectrum'];mask=(spec.ppm_axis>=bounds[0])&(spec.ppm_axis<=bounds[1])
        values.extend(np.asarray(getattr(spec,component))[mask])
    low,high=min(values),max(values);margin=.08*max(high-low,1)
    return low-margin,high+margin


def curve(ax,result,method,*,bounds=None,component='real',label=None):
    spec=result['spectrum'];values=np.asarray(getattr(spec,component))
    ax.plot(spec.ppm_axis,values,lw=.8,color=COLORS[method],label=label or LABELS[method])
    ax.axhline(0,color='#999999',lw=.5)
    low,high=bounds or (float(spec.ppm_axis.min()),float(spec.ppm_axis.max()))
    ax.set(xlim=(high,low),xlabel='Chemical shift (ppm)',ylabel='Intensity (a.u.)')


def save_plot(fig,folder,stem,a,description,manifest):
    return save_figure(fig,Path(folder)/stem,a['dataset_display_name'],
        f"{description} | {a['acquisition_id']} | {a['timestamp']}",manifest,formats=('png',))


def make_comparisons(folder,a,results,manifest):
    """Every panel uses the actual numerical arrays exported in this folder."""
    folder=Path(folder)
    for method in METHODS:
        result=results[method];spec=result['spectrum']
        fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
        curve(axes[0],result,method);axes[0].set_title('Full real spectrum; before baseline')
        curve(axes[1],result,method,bounds=TARGET_RANGE);axes[1].set_ylim(*target_limits(results))
        axes[1].set_title('Target region; shared vertical limits')
        phase=f"P0={spec.phase0_deg:.6g}°, P1={spec.phase1_deg:.6g}°"
        save_plot(fig,folder,STEMS[method],a,f"{LABELS[method]} — {phase}",manifest)
    fig,axes=plt.subplots(2,2,figsize=(12,8),layout='constrained',sharex=True,sharey=True)
    for letter,ax,method in zip('ABCD',axes.flat,METHODS):
        curve(ax,results[method],method);ax.set_title(f'{letter}. {LABELS[method]}')
    save_plot(fig,folder,'06_phase_comparison_full',a,'Full Phase Comparison — before baseline',manifest)
    fig,ax=plt.subplots(figsize=(12,4.5),layout='constrained')
    for method in METHODS[1:]:curve(ax,results[method],method)
    ax.legend(fontsize=9)
    save_plot(fig,folder,'07_phase_comparison_overlay',a,'Real Phase Overlay — before baseline',manifest)
    fig,axes=plt.subplots(1,2,figsize=(13,4.7),layout='constrained')
    for method in METHODS:
        curve(axes[0],results[method],method,bounds=TARGET_RANGE)
        spec=results[method]['spectrum'];pick=results[method]['picked']
        axes[1].plot(pick.ppm_axis,pick.quantitative_corrected,lw=.9,color=COLORS[method],label=LABELS[method])
    axes[0].set_ylim(*target_limits(results));axes[0].set_title('Phased real, before baseline')
    axes[1].set(xlim=(TARGET_RANGE[1],TARGET_RANGE[0]),title='Same downstream: regional quantitative trace',
        xlabel='Chemical shift (ppm)',ylabel='Intensity (a.u.)')
    visible=[]
    for result in results.values():
        picked=result['picked'];mask=(picked.ppm_axis>=TARGET_RANGE[0])&(picked.ppm_axis<=TARGET_RANGE[1])
        visible.extend(np.asarray(picked.quantitative_corrected)[mask])
    low,high=min(visible),max(visible);pad=.08*max(high-low,1);axes[1].set_ylim(low-pad,high+pad)
    for ax in axes:ax.axvspan(5.7,5.9,color='gray',alpha=.06);ax.legend(fontsize=7)
    save_plot(fig,folder,'08_target_peak_phase_comparison',a,'Target Phase and Quantitative Comparison',manifest)
    fig,axes=plt.subplots(2,4,figsize=(16,7),layout='constrained',sharex=True)
    for col,method in enumerate(METHODS):
        for row,component in enumerate(('real','imaginary')):
            ax=axes[row,col];curve(ax,results[method],method,bounds=TARGET_RANGE,component=component)
            ax.set_ylim(*target_limits(results,component=component))
            ax.set_title(f'{LABELS[method]}\n{component.capitalize()}')
    save_plot(fig,folder,'09_complex_phase_diagnostic',a,'Complex Target-Region Phase Diagnostic',manifest)


def export_arrays(folder,method,result,*,stem=None):
    folder=Path(folder);spec=result['spectrum'];picked=result['picked']
    values=np.column_stack((spec.ppm_axis,spec.real,spec.imaginary,spec.magnitude,
        spec.real-result['quantitative'],result['quantitative']))
    np.savetxt(folder/((stem or STEMS[method])+'.csv'),values,delimiter=',',fmt='%.17g',comments='',
        header='ppm,real,imaginary,magnitude,baseline,baseline_corrected_real')
    analysis_dir=folder/'analysis'/method;analysis_dir.mkdir(parents=True,exist_ok=True)
    np.savetxt(analysis_dir/'regional_analysis.csv',np.column_stack((picked.ppm_axis,picked.magnitude,picked.baseline,
        picked.smoothed,picked.quantitative_corrected)),delimiter=',',fmt='%.17g',comments='',
        header='ppm,input_after_global_baseline,regional_baseline,detection_smoothed,regional_quantitative_real')
    write_rows(analysis_dir/'peak_results.csv',result['peaks'],columns=list(result['peaks'][0]) if result['peaks'] else ['peak_ppm','height','positive_area','snr','qc_pass'])
    write_json(analysis_dir/'target_result.json',result['target'])
    np.savez_compressed(analysis_dir/'spectral_evidence.npz',ppm=spec.ppm_axis,unphased=result['unphased'],
        phased=spec.real+1j*spec.imaginary,quantitative_real=result['quantitative'],
        baseline=spec.real-result['quantitative'],region_ppm=picked.ppm_axis,
        regional_baseline=picked.baseline,detection_smoothed=picked.smoothed,regional_quantitative=picked.quantitative_corrected)
    phase={'method':method,'p0_deg':spec.phase0_deg,'p1_deg':spec.phase1_deg,
        'inverse_phase':not result['args'].direct_phase,'pivot_fraction':0.,'pivot_ppm':float(spec.ppm_axis[0]),
        'convention':'S[k]*exp(sign*i*pi/180*(P0+P1*k/N)), array-index-zero pivot',
        'phase_denominator':spec.processed_points}
    write_json(analysis_dir/'phase_parameters.json',phase)
    return phase


def result_row(a,method,result,prod,decision=None):
    spec=result['spectrum'];target=result['target']
    return {'method':method,'P0':spec.phase0_deg,'P1':spec.phase1_deg,
        'direction':'inverse' if not result['args'].direct_phase else 'direct',
        'pivot_ppm':float(spec.ppm_axis[0]),'convention':'index-zero, k/N',
        'target_peak_ppm':target['peak_ppm'],'peak_height':target['height'],'peak_area':target['area'],
        'snr':target['snr'],'detected':target['detected'],
        'completion_boolean':decision['complete'] if decision else None,
        'completion_classification':decision['status'] if decision else 'not evaluated without date sequence',
        'completion_first_index':decision.get('completion_index') if decision else None,
        'difference_from_production_peak_area':target['area']-prod['target']['area'],
        'difference_from_production_snr':target['snr']-prod['target']['snr'],
        'raw_sha256':a['raw_sha256'],'acquisition_id':a['acquisition_id'],'timestamp':a['timestamp'],
        'notes':'Variable-boundary target peak area; completion is a separate fixed-window date-prefix replay, not a physical stop.'}


def write_acquisition(folder,a,results,old_root,manifest):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    source=Path(a['source_path']);local_source=folder/'raw_source.dx'
    if not local_source.exists():shutil.copyfile(source,local_source)
    if sha256(local_source)!=a['raw_sha256']:raise ValueError('Local raw copy changed')
    fid=read_jcamp_fid(source);sweep=float(fid.metadata.get('$SWH',fid.metadata.get('$SWEEP WIDTH')))
    np.savetxt(folder/'01_raw_fid.csv',np.column_stack((np.arange(len(fid.real))/sweep,fid.real,fid.imag)),
        delimiter=',',fmt='%.17g',comments='',header='time_seconds,real,imaginary')
    parameters=[]
    for method in METHODS:
        p=export_arrays(folder,method,results[method]);parameters.append(p)
    write_rows(folder/'phase_parameters.csv',parameters)
    meta={**a,'raw_data_type':'complex time-domain NMR FID','local_raw_source':str(local_source.resolve()),
        'methods':list(METHODS),'shared_downstream_parameters':vars(results['production']['args']),
        'target_view_ppm':TARGET_RANGE,'csv_baseline_description':'Full-spectrum baseline is global production baseline; regional baseline and unsmoothed quantitative trace are in analysis/<method>/regional_analysis.csv.',
        'production_phase':parameters[1],'manual_status':'MANUAL REVIEW PENDING',
        'june09_critical':a['acquisition_date']=='2026-06-09' and a['timestamp'][11:19] in CRITICAL_TIMES,
        'manual_checkpoints_path':str((folder/'manual_review/checkpoints').resolve()),
        'provenance':provenance()}
    write_json(folder/'00_metadata.json',meta)
    old_auto=Path(old_root)/'acquisitions'/a['acquisition_id']/'automated'
    auto=folder/'automated';auto.mkdir(exist_ok=True)
    shutil.copyfile(old_auto/'spectral_evidence.npz',auto/'spectral_evidence.npz')
    gui_meta=json_read(old_auto/'processing_metadata.json')
    gui_meta.update(source_path=str(local_source.resolve()),original_source_path=str(source.resolve()))
    write_json(auto/'processing_metadata.json',gui_meta)
    review=folder/'manual_review';(review/'checkpoints').mkdir(parents=True,exist_ok=True)
    write_json(review/'starting_phase.json',parameters[1])
    write_json(review/'source_reference.json',{'raw_sha256':a['raw_sha256'],'local_raw_fid':str(local_source.resolve()),
        'original_raw_fid':str(source.resolve()),'timestamp':a['timestamp'],'timestamp_source':a['timestamp_source'],
        'gui_acquisition_folder':str(folder.resolve()),'manual_status':'MANUAL REVIEW PENDING'})
    (review/'README.md').write_text(
        '# Manual review — MANUAL REVIEW PENDING\n\nStart from the exact DX metadata phase using the acquisition selector. '
        'Saving or accepting in the GUI creates an append-only `checkpoints/review_001/` folder and reruns the same downstream analysis. '
        'No manual result is created by preparing this folder. Historical unidentified settings remain exploratory in the older acquisitions tree.\n\n'
        f'Acquisition: `{a["acquisition_id"]}` at `{a["timestamp"]}`.\n\n'
        'See [the manual review guide](../../MANUAL_PHASE_REVIEW_GUIDE.md).\n',encoding='utf-8')
    (folder/'README.md').write_text(
        f'# {a["dataset_display_name"]} — {a["timestamp"]}\n\nRaw instrument FID, SHA-256 `'+a['raw_sha256']+'`.\n\n'
        'Open **06_phase_comparison_full.png**, **08_target_peak_phase_comparison.png**, then **09_complex_phase_diagnostic.png**. '
        'Numbered phase CSVs contain real, imaginary, magnitude, global baseline and globally corrected signal. '
        'Raw FID samples are in 01_raw_fid.csv and the identical JCAMP export is raw_source.dx.\n\n'
        'All phase panels precede baseline correction. The right-hand target panel and analysis/<method>/ files show the same downstream baseline/peak path. '
        'The four methods use one identical FFT/ppm axis. Stored alternative phase parameters are replayed; optimizers are not changed or rerun.\n\n'
        '`phase_method_results.csv` compares variable-boundary peak metrics and date-prefix completion status. '
        'Completion uses a separate fixed window and does not prove physical stopping.\n\n'
        '**MANUAL REVIEW PENDING.** Select this ID in the GUI; exact DX metadata phase loads automatically. '
        'New checkpoints go in manual_review/checkpoints. Automatic results are preserved.\n',encoding='utf-8')
    make_comparisons(folder,a,results,manifest)
    write_rows(folder/'figure_manifest.csv',[x for x in manifest if str(folder.resolve()) in x['path']])
    return meta


def manual_status(folder):
    valid=[]
    for p in sorted((Path(folder)/'manual_review/checkpoints').glob('review_*/checkpoint.json')):
        row=json_read(p)
        if row.get('analysis_role')=='manual_reference' and row.get('explicit_gui_save'):
            valid.append(p)
    return (json_read(valid[-1]).get('review_status','CHECKPOINT SAVED') if valid else 'NOT REVIEWED'),valid


def write_index(gallery,cohort):
    gallery=Path(gallery)
    lines=['# Phase validation — start here','',
        '**27 distinct real acquisitions.** Each folder contains '+('eight phase comparisons (one unphased reference and seven phased results)' if (gallery/'cohort_phase_quality_metrics.csv').exists() else 'all four original phase spectra')+', same-downstream numerical analysis, and an empty or explicitly saved manual-review workflow.',
        '', '[Thumbnail gallery](PHASE_VALIDATION_GALLERY.html) · [Manual review guide](MANUAL_PHASE_REVIEW_GUIDE.md) · [June 9 completion review](june09_completion_review/README.md)',
        '', 'Launch the GUI selector with `--validation-dir`; the guide supplies the exact PowerShell command. Historical unidentified settings are not counted as your review.']
    records=[]
    if (gallery/'MANUAL_PHASE_VALIDATION_STEP_BY_STEP.md').exists():
        lines += ['', '[QUICK START manual workflow](MANUAL_PHASE_VALIDATION_STEP_BY_STEP.md) · [GUI method audit](GUI_PHASE_METHOD_AUDIT.md)']
    if (gallery/'MANUAL_REVIEW_PRIORITY.csv').exists():
        lines += ['', '[Known-truth benchmark summary](SYNTHETIC_GROUND_TRUTH_SUMMARY.md) · '
            '[Synthetic cases and figures](synthetic_ground_truth/README.md) · '
            '[Manual-review priority](MANUAL_REVIEW_PRIORITY.md) · [June 9 first group](MANUAL_REVIEW_JUNE09.md) · '
            '[August disagreements](MANUAL_REVIEW_AUGUST.md)', '',
            'Priority measures review value. Method consensus is agreement, not ground truth.']
    if (gallery/'cohort_phase_quality_metrics.csv').exists():
        lines += ['', '**Expanded comparison:** [quantitative findings](QUANTITATIVE_PHASE_COMPARISON.md) · [cohort metrics](cohort_phase_quality_metrics.csv) · [method summaries](cohort_phase_method_summary.csv) · '
            '[algorithm guide](documentation/PHASE_METHODS_GUIDE.md) · [failure/review list](PHASE_METHOD_FAILURES.md) · '
            '[June 9 all-method completion](june09_completion_review/june09_all_phase_methods_vs_completion.png).']
    for date in sorted({a['acquisition_date'] for a in cohort}):
        lines += ['',f'## {date}','',f'[Date comparison montage](date_montages/phase_validation_{date.replace("-","")}.png)','',
            '| Acquisition metadata time | ID | DX metadata PHC0 / PHC1 | Comparison | Target | Phase CSVs | Manual review | Status |',
            '|---|---|---|---|---|---|---|---|']
        for a in (x for x in cohort if x['acquisition_date']==date):
            ident=a['acquisition_id'];folder=gallery/ident;meta=json_read(folder/'00_metadata.json')
            status,paths=manual_status(folder);phase=meta['production_phase']
            display_status=status
            if (folder/'method_consensus.json').exists():
                consensus=json_read(folder/'method_consensus.json')
                display_status=(status if paths else 'MANUAL REVIEW PENDING')+f'<br>Priority {consensus["priority_tier"]}, rank {consensus["rank"]}<br>Detection {consensus["detection_consensus"]}; completion {consensus["completion_consensus"]}'
                if paths and status!='MANUAL REVIEW — AMBIGUOUS':display_status='MANUAL REVIEW COMPLETE<br>'+display_status
            extended=folder/'additional_phase_methods.json'
            extra=''
            if extended.exists():
                available=json_read(extended)['implemented_spectra']
                extra=f'<br>[primary]({ident}/phase_comparison_primary.png) · [all methods]({ident}/phase_comparison_all_methods.png) · [metrics]({ident}/all_phase_methods_results.csv)<br>Ran: '+', '.join(available)
            lines.append(f'| {a["timestamp"]} | {ident} | {phase["p0_deg"]:g} / {phase["p1_deg"]:g} (inverse) | '
                f'[full]({ident}/06_phase_comparison_full.png) · [overlay]({ident}/07_phase_comparison_overlay.png){extra} | '
                f'[target]({ident}/08_target_peak_phase_comparison.png)'+(f' · [all target methods]({ident}/target_peak_all_phase_methods.png) · [complex]({ident}/complex_all_phase_methods.png)' if extended.exists() else '')+f' | [DX metadata phase CSV]({ident}/03_production_phase.csv) · [parameters]({ident}/phase_parameters.csv) · '
                f'[results]({ident}/phase_method_results.csv) | [open folder]({ident}/manual_review/README.md) | {display_status} |')
            records.append({**a,'gallery_folder':str(folder.resolve()),'manual_status':status,
                'manual_checkpoint_count':len(paths),'june09_critical':meta['june09_critical']})
    (gallery/'PHASE_VALIDATION_INDEX.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write_json(gallery/'PHASE_VALIDATION_MANIFEST.json',records)
    write_html_gallery(gallery,records)
    return records


def write_html_gallery(gallery,records):
    """An offline thumbnail view beside the requested Markdown index."""
    from html import escape
    expanded=(Path(gallery)/'cohort_phase_quality_metrics.csv').exists()
    method_description='one unphased reference and seven phased results' if expanded else 'four phase methods'
    june_plot='june09_all_phase_methods_vs_completion.png' if expanded else 'june09_phase_methods_vs_completion.png'
    chunks=['<!doctype html><html><head><meta charset="utf-8"><title>NMR Phase Validation Gallery</title>',
        '<style>body{font:16px Arial,sans-serif;background:#f4f6f8;color:#17212b;margin:0}header{background:#12334d;color:white;padding:26px 5%;}main{max-width:1450px;margin:auto;padding:20px}a{color:#075e9d}header a{color:#bce5ff}section{margin-bottom:35px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(450px,1fr));gap:18px}.card{background:white;border:1px solid #ccd6dd;border-radius:8px;padding:18px;overflow:hidden}.card img{width:100%;height:auto}.status{font-weight:bold;color:#935500}code{font-size:12px;overflow-wrap:anywhere}h2{padding-top:10px}nav{line-height:2}button{padding:6px}</style></head><body>',
        f'<header><h1>27 real NMR acquisitions — phase review</h1><p>Same raw FID, {method_description}, numerical analysis and your manual checkpoints.</p>',
        '<nav><a href="PHASE_VALIDATION_INDEX.md">Markdown index</a> · <a href="MANUAL_PHASE_REVIEW_GUIDE.md">Manual GUI guide</a> · '
        f'<a href="june09_completion_review/{june_plot}">June 9 completion comparison</a></nav></header><main>',
        '<p>Phase previews precede baseline correction. Target comparison images also show the quantitative downstream traces. '
        'Historical unidentified settings do not count as your manual review.</p>']
    if expanded:
        chunks.append('<nav><a href="QUANTITATIVE_PHASE_COMPARISON.md">Quantitative findings</a> · '
            '<a href="cohort_phase_method_summary.csv">Cohort summaries</a> · '
            '<a href="cohort_phase_quality_distributions.png">Distributions</a> · '
            '<a href="documentation/PHASE_METHODS_GUIDE.md">Algorithm guide</a> · '
            '<a href="PHASE_METHOD_FAILURES.md">Manual inspection flags</a></nav>')
    if (Path(gallery)/'MANUAL_REVIEW_PRIORITY.csv').exists():
        chunks.append('<nav><a href="SYNTHETIC_GROUND_TRUTH_SUMMARY.md">Known-truth method summary</a> · '
            '<a href="synthetic_ground_truth/README.md">Synthetic benchmark and figures</a> · '
            '<a href="MANUAL_REVIEW_PRIORITY.md">Manual-review priority</a> · '
            '<a href="MANUAL_REVIEW_JUNE09.md">June 9 first group</a> · '
            '<a href="MANUAL_REVIEW_AUGUST.md">August disagreements</a></nav>'
            '<p>Method consensus is agreement, not ground truth. Priority measures the likely information gained from review.</p>')
    for date in sorted({a['acquisition_date'] for a in records}):
        if date==sorted({a['acquisition_date'] for a in records})[0] and (Path(gallery)/'MANUAL_PHASE_VALIDATION_STEP_BY_STEP.md').exists():
            chunks.append('<p><a href="MANUAL_PHASE_VALIDATION_STEP_BY_STEP.md">QUICK START manual workflow</a> · <a href="GUI_PHASE_METHOD_AUDIT.md">GUI method audit</a></p>')
        chunks.append(f'<section><h2>{escape(date)}</h2><p><a href="date_montages/phase_validation_{date.replace("-","")}.png">Date montage — all four methods</a></p><div class="cards">')
        for a in (x for x in records if x['acquisition_date']==date):
            ident=a['acquisition_id'];safe=escape(ident);meta=json_read(Path(gallery)/ident/'00_metadata.json');p=meta['production_phase']
            additional=Path(gallery)/ident/'additional_phase_methods.json'
            extended_links=''
            consensus_line=''
            display_manual_status=a['manual_status']
            consensus_file=Path(gallery)/ident/'method_consensus.json'
            if consensus_file.exists():
                consensus=json_read(consensus_file)
                review_status=a['manual_status'] if a.get('manual_checkpoint_count') else 'MANUAL REVIEW PENDING'
                if a.get('manual_checkpoint_count') and review_status!='MANUAL REVIEW — AMBIGUOUS':review_status='MANUAL REVIEW COMPLETE · '+review_status
                display_manual_status=review_status
                consensus_line=f'<p class="status">{review_status} · Priority {escape(consensus["priority_tier"])} · rank {consensus["rank"]}</p><p>Method consensus: detection {escape(consensus["detection_consensus"])}; completion {escape(consensus["completion_consensus"])}.</p>'
            thumbnail='08_target_peak_phase_comparison.png'
            if additional.exists():
                available=json_read(additional)['implemented_spectra']
                extended_links='<p><strong>Methods that ran:</strong> '+escape(', '.join(available))+f'<br><a href="{safe}/phase_comparison_primary.png">Primary comparison</a> · <a href="{safe}/phase_comparison_all_methods.png">All methods</a> · <a href="{safe}/target_peak_all_phase_methods.png">Target metrics</a> · <a href="{safe}/complex_all_phase_methods.png">Complex diagnostic</a> · <a href="{safe}/all_phase_methods_results.csv">All-method numerical results</a></p>'
                thumbnail='target_peak_all_phase_methods.png'
            chunks.append(f'<article class="card"><h3>{escape(a["timestamp"])}'+(' · June 9 critical' if a.get('june09_critical') else '')+'</h3>'
                f'<p><code>{safe}</code><br>DX metadata phase: PHC0={p["p0_deg"]:g}°, PHC1={p["p1_deg"]:g}°, inverse. '
                f'<span class="status">{escape(display_manual_status)}</span></p>'
                +consensus_line+extended_links+f'<a href="{safe}/{thumbnail}"><img loading="lazy" src="{safe}/{thumbnail}" alt="Same-acquisition target comparison"></a>'
                f'<p><a href="{safe}/06_phase_comparison_full.png">Four full spectra</a> · '
                f'<a href="{safe}/07_phase_comparison_overlay.png">Overlay</a> · '
                f'<a href="{safe}/09_complex_phase_diagnostic.png">Real / imaginary</a></p>'
                f'<p><a href="{safe}/phase_method_results.csv">Peak and completion metrics</a> · <a href="{safe}/phase_parameters.csv">Phase parameters</a> · '
                f'<a href="{safe}/manual_review/README.md">Manual checkpoint folder</a></p>'
                f'<p>Numerical spectra: <a href="{safe}/02_unphased_fft.csv">unphased</a> · '
                f'<a href="{safe}/03_production_phase.csv">DX metadata phase</a> · <a href="{safe}/04_acme_phase.csv">ACME</a> · '
                f'<a href="{safe}/05_peak_minima_phase.csv">peak minima</a></p></article>')
        chunks.append('</div></section>')
    chunks.append('</main></body></html>')
    (Path(gallery)/'PHASE_VALIDATION_GALLERY.html').write_text('\n'.join(chunks),encoding='utf-8')


def date_montages(root,cohort,all_results,manifest):
    gallery=Path(root)/'phase_validation'
    for date in sorted({a['acquisition_date'] for a in cohort}):
        group=[a for a in cohort if a['acquisition_date']==date]
        fig,axes=plt.subplots(len(group),4,figsize=(16,2.2*len(group)+.5),layout='constrained',squeeze=False)
        for row,a in enumerate(group):
            results=all_results[a['acquisition_id']];limits=target_limits(results)
            for col,method in enumerate(METHODS):
                ax=axes[row,col];curve(ax,results[method],method,bounds=TARGET_RANGE)
                ax.set_ylim(*limits);ax.set_title(LABELS[method] if row==0 else '')
                ax.set_ylabel(a['timestamp'][11:19]+'\nIntensity (a.u.)')
                if row<len(group)-1:ax.set_xlabel('')
        save_figure(fig,Path(root)/'summary_plots'/f'phase_validation_{date.replace("-","")}',group[0]['dataset_display_name'],
            'Phase Validation Contact Sheet — Target Region, Before Baseline',manifest,formats=('png',))
        destination=gallery/'date_montages'/f'phase_validation_{date.replace("-","")}.png'
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(Path(root)/'summary_plots'/destination.name,destination)
        manifest.append({'path':str(destination.resolve()),'dataset_display_name':group[0]['dataset_display_name'],
                         'visible_title':manifest[-1]['visible_title']})


def june_review(gallery,cohort,all_results,replays,manifest):
    gallery=Path(gallery);folder=gallery/'june09_completion_review';folder.mkdir(exist_ok=True)
    group=[a for a in cohort if a['acquisition_date']=='2026-06-09']
    critical=[(i,a) for i,a in enumerate(group) if a['timestamp'][11:19] in CRITICAL_TIMES]
    rows=[]
    fig,axes=plt.subplots(len(critical)+1,4,figsize=(16,3*len(critical)+3),layout='constrained')
    for row,(index,a) in enumerate(critical):
        results=all_results[a['acquisition_id']];limits=target_limits(results)
        for col,method in enumerate(METHODS):
            result=results[method];decision=replays[(a['acquisition_date'],method)].decision_trace[index]
            data=result_row(a,method,result,results['production'],decision);rows.append(data)
            ax=axes[row,col];curve(ax,result,method,bounds=TARGET_RANGE);ax.set_ylim(*limits)
            ax.set_title(f'{a["timestamp"][11:19]} {LABELS[method]}\nArea={data["peak_area"]:.3f}, S/N={data["snr"]:.2f}\n'
                f'Completion={data["completion_boolean"]} ({data["completion_classification"]})',fontsize=9)
    for col,method in enumerate(METHODS):
        report=replays[('2026-06-09',method)];ax=axes[-1,col]
        hours=[m['elapsed_time_hours'] for m in report.measurements]
        areas=[m['area'] for m in report.measurements]
        ax.plot(hours,areas,'o-',color=COLORS[method]);idx=report.completion.completion_index
        if idx is not None:ax.axvline(hours[idx],color='red',ls='--',label='First completion');ax.legend(fontsize=7)
        ax.set(title=f'{LABELS[method]} sequence\n{report.completion.status}',xlabel='Hours from JCAMP LONG DATE',ylabel='Fixed-window area (a.u.·ppm)')
    save_figure(fig,folder/'june09_phase_methods_vs_completion',group[0]['dataset_display_name'],
        'Phase Methods vs Retrospective Completion — same FIDs; no physical stop established',manifest,formats=('png',))
    write_rows(folder/'critical_acquisition_metrics.csv',rows)
    write_rows(folder/'sequence_timing.csv',[{'acquisition_id':a['acquisition_id'],'timestamp':a['timestamp'],
        'timestamp_source':a['timestamp_source'],'raw_sha256':a['raw_sha256']} for a in group])
    for method in METHODS:
        report=replays[('2026-06-09',method)]
        write_rows(folder/f'{method}_completion_trace.csv',report.decision_trace)
        write_json(folder/f'{method}_completion.json',asdict(report.completion))
    links=['# June 9 completion review','',
        '[Main comparison](june09_phase_methods_vs_completion.png) · [Numerical metrics](critical_acquisition_metrics.csv)','',
        'Stored phase first meets the retrospective criterion at 11:08:23; ACME has no completion; peak minima first meets it at 11:36:54. '
        'Peak numbers in panels are variable-boundary target metrics; the bottom sequence panels use the separate fixed completion window. '
        'No physical plateau stop was established. The production report retains wide-CI and subsequent-departure warnings.','',
        'Critical acquisitions are marked in the GUI selector. Manual review remains pending until you explicitly save.','']
    for _,a in critical:
        ident=a['acquisition_id']
        links.append(f'- {a["timestamp"]}: [four-method comparison](../{ident}/06_phase_comparison_full.png), '
            f'[target zoom](../{ident}/08_target_peak_phase_comparison.png), [manual folder](../{ident}/manual_review/README.md)')
    (folder/'README.md').write_text('\n'.join(links)+'\n',encoding='utf-8')


def write_inventory(root,before,cohort):
    root=Path(root);gallery=root/'phase_validation'
    lines=['# Phase output inventory','',f'Root: `{root.resolve()}`','',
        '## Before this organization task','',before['scope'],'',
        '| Requested item | Existing coverage before this task |','|---|---|']
    for key,label in [('unphased_production_plot','Unphased and production audit plots'),('acme_audit_plot','Separate ACME audit plots'),
        ('peak_minima_audit_plot','Separate peak-minima audit plots'),('production_full_csv','Production full-spectrum CSV'),
        ('acme_full_csv','ACME full-spectrum CSV'),('peak_minima_full_csv','Peak-minima full-spectrum CSV'),
        ('gui_input','Old GUI-loadable metadata/array inputs'),('manual_directory','Old manual-checkpoint directories'),
        ('obvious_four_method_comparison','Obvious standardized four-method comparison')]:
        lines.append(f'| {label} | {sum(x[key] for x in before["acquisitions"])}/27 |')
    lines += ['', 'The old GUI required an acquisition-folder argument and lacked a 27-acquisition selector. '
        'Old ACME/peak-minima folders had lossless arrays and separate before/after plots, but lacked full-spectrum numerical CSVs and the requested shared comparison layout.',
        '', '## What now exists','',
        '[Start at the gallery index](phase_validation/PHASE_VALIDATION_INDEX.md). All 27 folders have the numbered plots/CSVs, phase parameters, same-downstream results, raw JCAMP copy, and manual-review/checkpoint inputs. '
        'Each manual checkpoint directory is prepared, but empty until you explicitly save. Old unidentified references are exploratory.','',
        '## Exact existing file paths captured before generation','']
    for category,paths in before['categories'].items():
        lines += [f'### {category} ({len(paths)} files)','']
        lines.extend(f'- [{Path(p).name}](<{Path(p).as_posix()}>)' for p in sorted(paths))
        lines.append('')
    lines += ['## Exact generated acquisition folders','']
    for a in cohort:lines.append(f'- `{(gallery/a["acquisition_id"]).resolve()}`')
    lines += ['', 'Generated exact file paths and their hashes are in [phase_validation/OUTPUT_FILES.csv](phase_validation/OUTPUT_FILES.csv).',
        'Verification results are in [phase_validation/VERIFICATION.csv](phase_validation/VERIFICATION.csv).']
    (root/'PHASE_OUTPUT_INVENTORY.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def build_gallery(root=v.DEFAULT_OUTPUT):
    root=Path(root).resolve();before=audit_existing(root);cohort=v.load_cohort()
    gallery=root/'phase_validation';gallery.mkdir(exist_ok=True)
    protected={str(p):sha256(p) for p in (root/'acquisitions').rglob('*') if p.is_file()}
    results={};checks=[];manifest=[]
    for i,a in enumerate(cohort,1):
        print(f'[{i}/27] Exposing all phase methods: {a["acquisition_id"]}',flush=True)
        methods,verified=load_methods(root,a);results[a['acquisition_id']]=methods;checks.extend(verified)
        write_acquisition(gallery/a['acquisition_id'],a,methods,root,manifest)
    replays={}
    for date in sorted({a['acquisition_date'] for a in cohort}):
        group=[a for a in cohort if a['acquisition_date']==date]
        for method in METHODS:
            replays[(date,method)]=v.replay_target_series([results[a['acquisition_id']][method] for a in group])
        for index,a in enumerate(group):
            ident=a['acquisition_id'];folder=gallery/ident
            rows=[result_row(a,method,results[ident][method],results[ident]['production'],
                replays[(date,method)].decision_trace[index]) for method in METHODS]
            previous=folder/'phase_method_results.csv'
            if previous.exists():
                rows.extend(x for x in v.read_rows(previous) if x.get('method','').startswith('manual_'))
            write_rows(folder/'phase_method_results.csv',rows)
            write_json(folder/'completion_context.json',{'scope':'Full date sequence; per-acquisition prefix decision. Retrospective support, not actual controller stop.',
                'ordered_acquisition_ids':[x['acquisition_id'] for x in group],
                'measurement_index':index,'timestamp_source':'LONG DATE header',
                'completion_config':asdict(replays[(date,'production')].config.completion)})
    date_montages(root,cohort,results,manifest)
    june_review(gallery,cohort,results,replays,manifest)
    write_index(gallery,cohort);write_inventory(root,before,cohort)
    write_rows(gallery/'VERIFICATION.csv',checks)
    write_rows(gallery/'FIGURE_MANIFEST.csv',manifest)
    integrity=[{'path':p,'sha256_before':h,'sha256_after':sha256(p),'unchanged':sha256(p)==h} for p,h in protected.items()]
    write_rows(gallery/'PRESERVED_AUTOMATIC_OUTPUTS.csv',integrity)
    if not all(x['unchanged'] for x in integrity):raise ValueError('Existing validation outputs changed')
    summary={'acquisitions':len(cohort),'unphased_png':27,'production_png':27,'acme_png':27,'peak_minima_png':27,
        'four_method_comparison':27,'overlays':27,'target_comparisons':27,'complex_diagnostics':27,
        'phase_method_csvs':108,'raw_fid_csvs':27,'manual_review_folders':27,'manual_checkpoints_prepared_not_fabricated':27,
        'date_montages':5,'old_automatic_files_preserved':len(integrity),'same_raw_fft_axis_checks':len(checks)}
    write_json(gallery/'OUTPUT_SUMMARY.json',summary)
    paths=sorted(p for p in gallery.rglob('*') if p.is_file() and p.name!='OUTPUT_FILES.csv')
    write_rows(gallery/'OUTPUT_FILES.csv',[{'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':sha256(p)} for p in paths])
    print(json.dumps(summary,indent=2),flush=True)
    return summary


def discover_acquisitions(validation_dir=v.DEFAULT_OUTPUT):
    root=Path(validation_dir).resolve()
    if root.name=='phase_validation':root=root.parent
    manifest=root/'phase_validation/PHASE_VALIDATION_MANIFEST.json'
    if manifest.is_file():return json_read(manifest)
    cohort=json_read(root/'acquisition_manifest.json')
    records=[]
    for a in cohort:
        gallery=root/'phase_validation'/a['acquisition_id']
        folder=gallery if (gallery/'00_metadata.json').is_file() else root/'acquisitions'/a['acquisition_id']
        records.append({**a,'gallery_folder':str(folder),
            'june09_critical':a['acquisition_date']=='2026-06-09' and a['timestamp'][11:19] in CRITICAL_TIMES})
    return records


def save_gallery_checkpoint(folder,*,p0_deg,p1_deg,pivot_ppm,pivot_fraction,
                            reviewer=None,notes='',role='manual_reference',
                            starting_method=None,starting_phase=None,ambiguous=False,
                            baseline_review='not_reviewed',preserve_frozen_tables=True):
    """Only invoked by an explicit GUI save (or a clearly labeled software test)."""
    folder=Path(folder).resolve()
    output=v.save_manual_checkpoint(folder,p0_deg=p0_deg,p1_deg=p1_deg,pivot_ppm=pivot_ppm,
        pivot_fraction=pivot_fraction,reviewer=reviewer,notes=notes,role=role,
        checkpoint_root=folder/'manual_review/checkpoints')
    auto_meta=json_read(folder/'automated/processing_metadata.json')
    source=Path(auto_meta['source_path']);args=v.production_args(source,auto_meta['parameters'])
    prod=v.analyze(source,args)
    checkpoint=json_read(output/'checkpoint.json');phase=checkpoint['manual_phase']
    manual=v.analyze(source,args,phase=(phase['effective_index_zero_p0_deg'],phase['p1_deg'],True,'manual'),
        unphased=prod['unphased'])
    with np.load(output/'spectral_evidence.npz') as saved:
        np.testing.assert_array_equal(saved['phased'],manual['spectrum'].real+1j*manual['spectrum'].imaginary)
    a=json_read(folder/'00_metadata.json');manifest=[]
    shutil.copyfile(output/'manually_phased_spectrum.csv',output/'manual_phase.csv')
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),layout='constrained')
    curve(axes[0],manual,'manual');curve(axes[1],manual,'manual',bounds=TARGET_RANGE)
    axes[1].set_ylim(*target_limits({'production':prod,'manual':manual}))
    save_plot(fig,output,'manual_phase',a,'Explicit Manual Checkpoint — before baseline',manifest)
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),layout='constrained')
    for method,result in (('production',prod),('manual',manual)):
        curve(axes[0],result,method,bounds=TARGET_RANGE)
        picked=result['picked'];axes[1].plot(picked.ppm_axis,picked.quantitative_corrected,
            color=COLORS[method],lw=.9,label=LABELS[method])
    axes[0].set_ylim(*target_limits({'production':prod,'manual':manual}))
    axes[0].set_title('Phase preview, before baseline')
    axes[1].set(xlim=(TARGET_RANGE[1],TARGET_RANGE[0]),title='Same downstream quantitative signal',
        xlabel='Chemical shift (ppm)',ylabel='Intensity (a.u.)')
    for ax in axes:ax.legend(fontsize=8)
    save_plot(fig,output,'production_vs_manual_overlay',a,'DX metadata phase vs Explicit Manual Checkpoint',manifest)
    comparison=v.read_rows(output/'automated_vs_manual.csv')
    gallery=folder.parent
    records=json_read(gallery/'PHASE_VALIDATION_MANIFEST.json')
    group=[x for x in records if x['acquisition_date']==a['acquisition_date']]
    production_sequence=[];manual_sequence=[];index=None;contributors=[]
    for i,record in enumerate(group):
        directory=gallery/record['acquisition_id'];meta=json_read(directory/'automated/processing_metadata.json')
        current=v.analyze(meta['source_path'],v.production_args(meta['source_path'],meta['parameters']))
        production_sequence.append(current)
        if record['acquisition_id']==a['acquisition_id']:
            modified=manual;index=i;reference=output/'checkpoint.json'
        else:
            _,saved=manual_status(directory)
            reference=saved[-1] if saved else None
            if reference:
                p=json_read(reference)['manual_phase']
                modified=v.analyze(meta['source_path'],current['args'],
                    phase=(p['effective_index_zero_p0_deg'],p['p1_deg'],True,'manual'),unphased=current['unphased'])
            else:modified=current
        manual_sequence.append(modified)
        contributors.append({'acquisition_id':record['acquisition_id'],'raw_sha256':record['raw_sha256'],
            'timestamp':record['timestamp'],'phase_source':str(reference) if reference else 'unchanged production'})
    if index is None:raise ValueError('Acquisition absent from review manifest')
    baseline=v.replay_target_series(production_sequence);replayed=v.replay_target_series(manual_sequence)
    completion_rows=[]
    for scope,old,new in [('through_this_acquisition',baseline.decision_trace[index],replayed.decision_trace[index]),
        ('full_available_date_sequence',asdict(baseline.completion),asdict(replayed.completion))]:
        completion_rows.append({'scope':scope,'production_complete':old['complete'],'manual_complete':new['complete'],
            'production_classification':old['status'],'manual_classification':new['status'],
            'production_first_index':old.get('completion_index'),'manual_first_index':new.get('completion_index'),
            'boolean_changed':old['complete']!=new['complete'],'classification_changed':old['status']!=new['status'],
            'physical_stop_verified':False})
    write_rows(output/'completion_comparison.csv',completion_rows)
    write_rows(output/'manual_sequence_completion_trace.csv',replayed.decision_trace)
    write_rows(output/'sequence_contributors.csv',contributors)
    write_json(output/'manual_sequence_completion.json',asdict(replayed.completion))
    for row in completion_rows:
        comparison.extend([{'metric':row['scope']+'_completion_boolean','automated':row['production_complete'],
            'comparison':row['manual_complete'],'absolute_difference':int(row['manual_complete'])-int(row['production_complete'])},
            {'metric':row['scope']+'_completion_classification','automated':row['production_classification'],
             'comparison':row['manual_classification'],'absolute_difference':None}])
    write_rows(output/'production_vs_manual_peak_metrics.csv',comparison)
    row=result_row(a,'manual_'+output.name,manual,prod,replayed.decision_trace[index])
    row.update(manual_p0_at_pivot=p0_deg,manual_p1=p1_deg,manual_pivot_ppm=pivot_ppm,
        checkpoint_path=str(output),notes='Explicit GUI save. Completion replay uses latest accepted manual checkpoints on this date, production elsewhere; contributors recorded.')
    rows=v.read_rows(folder/'phase_method_results.csv');rows.append(row)
    write_rows(output/'phase_method_results_with_manual.csv' if preserve_frozen_tables else folder/'phase_method_results.csv',rows)
    checkpoint.update(explicit_gui_save=role=='manual_reference',manual_review_status='CHECKPOINT SAVED' if role=='manual_reference' else 'SOFTWARE TEST',
        completion_replayed=True,completion_scope='Latest accepted manual checkpoints on the date; unchanged production elsewhere',
        frozen_automatic_tables_preserved=preserve_frozen_tables)
    write_json(output/'checkpoint.json',checkpoint)
    if (folder/'all_phase_methods_results.csv').exists() and not preserve_frozen_tables:
        from .additional_phase_methods import refresh_manual_method_comparison
        refresh_manual_method_comparison(folder,output,manual)
    if starting_method is not None:
        from .manual_phase_workflow import finalize_review
        finalize_review(folder,output,manual,starting_method=starting_method,
                        starting_phase=starting_phase,ambiguous=ambiguous)
        from .nmr_spectrum_view import export_final_spectrum
        export_final_spectrum(output,a,baseline_review)
        if preserve_frozen_tables:
            from .manual_phase_workflow import export_preserved_comparisons
            export_preserved_comparisons(folder,output,manual)
    write_rows(output/'manual_comparison_figure_manifest.csv',manifest)
    (output/'README.md').write_text(
        '# Saved manual checkpoint\n\nOpen manual_phase.png, production_vs_manual_overlay.png, '
        'production_vs_manual_peak_metrics.csv and completion_comparison.csv. '
        'manual_phase.csv retains full real/imaginary arrays; checkpoint.json records control and effective phase values. '
        'The completion replay uses the contributors in sequence_contributors.csv and does not establish a physical stop.\n',encoding='utf-8')
    write_index(gallery,records)
    return output
