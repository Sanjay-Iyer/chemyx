"""Finalize added method tables/report from saved evidence; never refit phases."""
import argparse
from pathlib import Path
import json
import importlib.metadata
import xml.etree.ElementTree as ET
import _bootstrap
import numpy as np
from chemyx_lab.analysis import additional_phase_methods as a
from chemyx_lab.analysis import phase_gallery as g
from chemyx_lab.analysis import nmr_validation as v
from chemyx_lab.analysis.phase_audit import sha256,write_json,write_rows


def typed(row):
    result={}
    for key,value in row.items():
        if key in ('acquisition_id','raw_sha256','timestamp','method','phase_quality_version'):
            result[key]=value
            continue
        if value=='':value=None
        elif value in ('True','False'):value=value=='True'
        else:
            try:value=float(value)
            except (ValueError,TypeError):pass
        result[key]=value
    return result


def main(root=v.DEFAULT_OUTPUT):
    gallery=Path(root)/'phase_validation';records=g.discover_acquisitions(root)
    rows=[];statuses={}
    for record in records:
        folder=gallery/record['acquisition_id'];availability=g.json_read(folder/'additional_phase_methods.json')
        for method,status in availability['new_method_status'].items():statuses[(record['acquisition_id'],method)]=status
        values=[typed(r) for r in v.read_rows(folder/'all_phase_methods_results.csv')]
        for row in values:
            if row['method'] in ('acme','peak_minima'):
                row['optimizer_success']=None;row['optimizer_status']='retained_optimizer_convergence_unknown'
            row['flags']='; '.join(a.failure_flags(row))
            row['phase_quality_version']='phase_quality_v1'
        write_rows(folder/'all_phase_methods_results.csv',values);write_rows(folder/'phase_quality_metrics.csv',values)
        rows.extend(r for r in values if not r['method'].startswith('manual_'))
        parameters=[g.json_read(folder/'analysis'/m/'phase_parameters.json') for m in a.NEW_METHODS if m in availability['implemented_spectra']]
        write_rows(folder/'additional_phase_parameters.csv',parameters)
    manifest=[r for r in v.read_rows(gallery/'ADDITIONAL_FIGURE_MANIFEST.csv') if not Path(r['path']).name.startswith('cohort_')]
    summaries=a.cohort_tables(gallery,rows,statuses,records,manifest)
    write_rows(gallery/'ADDITIONAL_FIGURE_MANIFEST.csv',manifest)
    all27=[r for r in summaries if r['cohort']=='all_27']
    lines=['# Quantitative phase-method comparison','',
        'All values use fixed phase-invariant diagnostic windows and the same downstream target analysis. '
        'Total imaginary dispersion is expected; even imaginary leakage is the more specific phase diagnostic. '
        'These are component comparisons, not an absolute ranking or proof of chemically correct phase.','',
        '| Method | n | Negative fraction median | Total imaginary fraction median | Even imaginary median | Asymmetry median | Median absolute area change | Median absolute S/N change | Detection changes | Prefix completion changes | Optimizer failures | Convergence not recorded |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in all27:
        lines.append(f'| {row["method"]} | {row["n"]} | {row["negative_area_fraction_median"]:.5g} | {row["imaginary_residual_median"]:.5g} | {row["even_imaginary_fraction_median"]:.5g} | {row["peak_asymmetry_median"]:.5g} | {row["absolute_area_difference_median"]:.5g} | {row["absolute_snr_difference_median"]:.5g} | {row["changed_detections"]} | {row["changed_prefix_completion_results"]} | {row["optimizer_failures"]} | {row["optimizer_convergence_not_recorded"]} |')
    candidates=[r for r in all27 if r['method'] not in ('unphased','production')]
    lines+=['','## Component agreement and disagreement','']
    for metric,label in [('negative_area_fraction_median','Lowest median negative fraction'),('imaginary_residual_median','Lowest descriptive total imaginary fraction'),
        ('even_imaginary_fraction_median','Lowest median even imaginary leakage'),('peak_asymmetry_median','Lowest median real asymmetry'),
        ('absolute_area_difference_median','Closest median target-area agreement with production'),('absolute_snr_difference_median','Closest median S/N agreement with production')]:
        best=min(candidates,key=lambda r:r[metric]);lines.append(f'- {label}, among alternative methods: **{best["method"]}** ({best[metric]:.6g}).')
    lines+=['','The production anchor itself has zero measurement differences by definition. Small differences mean agreement, not independent stability or correctness. '
        'Primary-25 and exploratory-2 strata, quartiles and individual points remain available in the summary/distribution files. '
        'Original ACME/peak-minima convergence was not recorded; the retained spectra are valid comparison inputs, but no convergence claim is invented.','',
        '## Primary historical comparison cohort (25)','',
        '| Method | Median absolute area change | Median absolute S/N change | Detection changes | Prefix completion changes | Review-flagged pairs |',
        '|---|---:|---:|---:|---:|---:|']
    primary=[r for r in summaries if r['cohort']=='primary_25']
    for row in primary:
        lines.append(f'| {row["method"]} | {row["absolute_area_difference_median"]:.6g} | {row["absolute_snr_difference_median"]:.6g} | {row["changed_detections"]} | {row["changed_prefix_completion_results"]} | {row["pathological_or_review_flag_count"]} |')
    primary_alternatives=[r for r in primary if r['method'] not in ('unphased','production')]
    area=min(primary_alternatives,key=lambda r:r['absolute_area_difference_median'])
    snr=min(primary_alternatives,key=lambda r:r['absolute_snr_difference_median'])
    lines+=['',f'In the primary-25 cohort, **{area["method"]}** is closest to production by median absolute target-area difference ({area["absolute_area_difference_median"]:.6g}); '
        f'**{snr["method"]}** is closest by median absolute S/N difference ({snr["absolute_snr_difference_median"]:.6g}). '
        'The two exploratory June 29 spectra shift the closest-area comparator to Ernst P0 in the pooled 27. This agreement criterion is not proof of accurate quantitation.','',
        '## June 9 retrospective completion','',
        '| Method | Completion outcome | Classification | Warnings |','|---|---|---|---|']
    for row in v.read_rows(gallery/'june09_completion_review/june09_all_phase_methods_results.csv'):
        lines.append(f'| {row["method"]} | {row["completion_timestamp"]} | {row["classification"]} | {row["warnings"]} |')
    lines+=['','These are retrospective criteria, not verified physical historical controller stops. '
        'Target metrics use variable boundaries; completion trajectories use the separate original fixed window.','',
        '## Inspection flags','',
        'Flags request review rather than change production QC. Every flagged acquisition/method is listed in [PHASE_METHOD_FAILURES.md](PHASE_METHOD_FAILURES.md). '
        'Equal objective weights and diagnostic assumptions are explicit in [the algorithm guide](documentation/PHASE_METHODS_GUIDE.md).']
    (gallery/'QUANTITATIVE_PHASE_COMPARISON.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    failures=['# Phase method pairs requiring manual inspection','',
        'Diagnostic review triggers, not production QC changes: negative fraction >0.2, even imaginary >0.3, asymmetry/odd-real ratio >0.5, '
        'normalized tilt >0.5, |target-area difference| >50%, |P1| >=270°, canonical P0 within 10° of inversion, '
        'detection/completion disagreements or weak P1 constraints. Total odd imaginary dispersion is expected. '
        'Original ACME/peak-minima convergence is unknown; this is separately recorded, not called failure.','',
        '| Acquisition | Method | Review trigger |','|---|---|---|']
    for row in rows:
        if row['flags']:failures.append(f'| {row["acquisition_id"]} | {row["method"]} | {row["flags"]} |')
    for (ident,method),status in statuses.items():
        if not status['success']:failures.append(f'| {ident} | {method} | {status["status"]}: {status.get("message","")} |')
    (gallery/'PHASE_METHOD_FAILURES.md').write_text('\n'.join(failures)+'\n',encoding='utf-8')
    g.write_index(gallery,records)
    protected=a.verify_protected(Path(root))
    versions=g.json_read(Path(root)/'logs/additional_phase_methods/environment_audit.json')['versions']
    assert all(importlib.metadata.version(name)==version for name,version in versions.items())
    checks=v.read_rows(gallery/'ADDITIONAL_METHOD_VERIFICATION.csv')
    assert all(v.truth(r['same_fft']) and v.truth(r['same_axis']) and v.truth(r['downstream_parameters_unchanged']) for r in checks)
    assert sum(len(g.manual_status(gallery/r['acquisition_id'])[1]) for r in records)==0
    summary=g.json_read(gallery/'ADDITIONAL_METHOD_SUMMARY.json')
    summary['protected_scientific_files_unchanged']=sum(r['unchanged'] for r in protected)
    summary['original_ai_environment_versions_unchanged']=True
    summary['full_date_method_outcomes']=v.read_rows(gallery/'all_dates_phase_completion_results.csv')
    write_json(gallery/'ADDITIONAL_METHOD_SUMMARY.json',summary)
    files=sorted(p for p in gallery.rglob('*') if p.is_file() and p.name!='OUTPUT_FILES.csv')
    write_rows(gallery/'OUTPUT_FILES.csv',[{'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':sha256(p)} for p in files])
    print(json.dumps({'all_27_summaries':all27,'protected_files':len(protected),'unchanged':sum(r['unchanged'] for r in protected)},indent=2))


if __name__=='__main__':main()
