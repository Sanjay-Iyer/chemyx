"""Transparent disagreement-based review queue; consensus is never truth."""
from pathlib import Path
import json
import numpy as np
from scipy.stats import rankdata
from . import nmr_validation as v,phase_gallery as g,additional_phase_methods as a
from .synthetic_phase_benchmark import circular_degrees,direct_phase
from .phase_audit import write_rows,write_json

AUTOMATIC=('production','acme','peak_minima',*a.NEW_METHODS)


def consensus(values):
    values=[v.truth(z) for z in values];positive=sum(values);n=len(values)
    return {'true_count':positive,'false_count':n-positive,'agreement_count':max(positive,n-positive),
        'minority_count':min(positive,n-positive),'total':n,
        'majority':True if positive>n/2 else False if positive<n/2 else None}


def empirical_scale(values):
    values=np.asarray(values,float)
    if len(values)<2 or np.ptp(values)==0:return np.zeros(len(values))
    ranks=(rankdata(values,method='average')-1)/(len(values)-1)
    return np.where(values==0,0,ranks)


def acquisition_disagreement(rows,ppm,area_floor,snr_floor):
    rows=[r for r in rows if r['method'] in AUTOMATIC]
    if len(rows)!=7:raise ValueError('Review queue requires all seven actual automatic results')
    phases=[direct_phase(float(r['P0']),float(r['P1']),r['direction']=='inverse') for r in rows]
    n=len(ppm);indices=np.flatnonzero((ppm>=0)&(ppm<=12));step=max(1,int(np.ceil(len(indices)/4096)));indices=indices[::step]
    pairwise=[]
    for i in range(7):
        for j in range(i):
            differences=circular_degrees(phases[i][0]-phases[j][0]+(phases[i][1]-phases[j][1])*indices/n)
            pairwise.append((float(np.sqrt(np.mean(differences**2))),rows[j]['method'],rows[i]['method']))
    worst=max(pairwise)
    areas=[float(r['target_peak_area']) for r in rows];snrs=[float(r['S_N']) for r in rows]
    area_den=max(float(np.median(np.abs(areas))),area_floor);snr_den=max(float(np.median(np.abs(snrs))),snr_floor)
    quality_keys=('negative_area_fraction','even_imaginary_fraction','peak_asymmetry','dispersive_metric')
    spreads={key+'_spread':float(np.ptp([float(r[key]) for r in rows])) for key in quality_keys}
    detection=consensus([r['detected'] for r in rows]);completion=consensus([r['completion_boolean'] for r in rows])
    prod=next(r for r in rows if r['method']=='production')
    flags=[r['method']+': '+r['flags'] for r in rows if r.get('flags')]
    failures=[r['method'] for r in rows if r.get('optimizer_success') not in ('',None) and not v.truth(r['optimizer_success'])]
    out={'phase_ramp_disagreement_deg':worst[0],'largest_phase_disagreement_methods':worst[1]+' vs '+worst[2],
        'p0_circular_spread_deg':max(abs(float(circular_degrees(p[0]-q[0]))) for p in phases for q in phases),
        'p1_grid_equivalent_spread_deg':max(abs(float(circular_degrees(p[1]-q[1],360*n))) for p in phases for q in phases),
        'area_min':min(areas),'area_max':max(areas),'area_median':float(np.median(areas)),
        'area_spread_absolute':float(np.ptp(areas)),'area_normalization_denominator':area_den,
        'area_spread_percent':100*float(np.ptp(areas))/area_den,'area_floor_used':float(np.median(np.abs(areas)))<area_floor,
        'snr_min':min(snrs),'snr_max':max(snrs),'snr_median':float(np.median(snrs)),
        'snr_normalization_denominator':snr_den,'snr_spread_percent':100*float(np.ptp(snrs))/snr_den,
        'detection_disagreement_count':detection['minority_count'],'detection_true_count':detection['true_count'],
        'detection_agreement_count':detection['agreement_count'],'detection_consensus':f'{detection["agreement_count"]}/7 '+('detected' if detection['majority'] else 'not detected'),
        'detection_changes_vs_production':sum(v.truth(r['detected'])!=v.truth(prod['detected']) for r in rows),
        'completion_disagreement_count':completion['minority_count'],'completion_true_count':completion['true_count'],
        'completion_agreement_count':completion['agreement_count'],'completion_consensus':f'{completion["agreement_count"]}/7 '+('criterion met' if completion['majority'] else 'criterion not met'),
        'completion_changes_vs_production':sum(v.truth(r['completion_boolean'])!=v.truth(prod['completion_boolean']) for r in rows),
        'decision_disagreement_fraction':(detection['minority_count']+completion['minority_count'])/14,
        'quality_disagreement':float(np.mean(list(spreads.values()))),
        'flagged_method_failures':'; '.join(failures),'flagged_method_failure_count':len(failures),
        'flagged_method_review_count':len(flags),'flagged_method_review_details':' | '.join(flags),
        'manual_review_status':'MANUAL REVIEW PENDING',**spreads}
    return out


def rank_disagreements(rows):
    for key in ('phase_ramp_disagreement_deg','area_spread_percent','snr_spread_percent','quality_disagreement','decision_disagreement_fraction'):
        values=empirical_scale([r[key] for r in rows])
        for r,value in zip(rows,values):r[key+'_normalized']=float(value)
    for r in rows:
        r['phase_disagreement_score']=r['phase_ramp_disagreement_deg_normalized']
        r['quantification_disagreement_score']=(r['area_spread_percent_normalized']+r['snr_spread_percent_normalized'])/2
        r['quality_disagreement_score']=r['quality_disagreement_normalized']
        r['decision_disagreement_score']=r['decision_disagreement_fraction_normalized']
        r['manual_review_priority_score']=25*(r['phase_disagreement_score']+r['quantification_disagreement_score']+r['quality_disagreement_score']+r['decision_disagreement_score'])
    scores=[r['manual_review_priority_score'] for r in rows];q50,q75=np.quantile(scores,[.5,.75])
    for r in rows:
        r['priority_tier']='A' if r['detection_disagreement_count'] or r['completion_disagreement_count'] or r['manual_review_priority_score']>=q75 else 'B' if r['manual_review_priority_score']>=q50 else 'C'
        reasons=[]
        if r['completion_disagreement_count']:reasons.append(f'completion {r["completion_consensus"]}')
        if r['detection_disagreement_count']:reasons.append(f'detection {r["detection_consensus"]}')
        reasons.extend([f'phase spread {r["phase_ramp_disagreement_deg"]:.1f}° ({r["largest_phase_disagreement_methods"]})',
            f'area spread {r["area_spread_percent"]:.1f}%',f'S/N spread {r["snr_spread_percent"]:.1f}%'])
        r['reason_for_priority']='; '.join(reasons)
    ordered=sorted(rows,key=lambda r:(-r['manual_review_priority_score'],r['timestamp'],r['acquisition_id']))
    for rank,row in enumerate(ordered,1):row['rank']=rank
    return ordered,{'tier_A_score_threshold_observed_q75':float(q75),'tier_B_score_threshold_observed_q50':float(q50),
        'tier_A_override':'Any detection or retrospective prefix-completion disagreement',
        'weights':{'phase':.25,'quantification':.25,'decision':.25,'quality':.25}}


def build_queue(gallery=None):
    gallery=Path(gallery or v.DEFAULT_OUTPUT/'phase_validation');records=g.discover_acquisitions(gallery.parent)
    table=v.read_rows(gallery/'cohort_phase_quality_metrics.csv')
    automatic=[r for r in table if r['method'] in AUTOMATIC]
    area_floor=float(np.quantile([float(r['target_peak_area']) for r in automatic if float(r['target_peak_area'])>0],.25))
    snr_floor=float(np.quantile([float(r['S_N']) for r in automatic if float(r['S_N'])>0],.25))
    rows=[]
    for record in records:
        folder=gallery/record['acquisition_id']
        with np.load(folder/'analysis/production/spectral_evidence.npz') as arrays:ppm=arrays['ppm']
        # Join by immutable acquisition hash. One retained cohort identifier
        # was serialized as a float by the older report formatter; metadata
        # and hashes remain authoritative. Preserve the frozen table itself.
        local=[r for r in table if r['raw_sha256']==record['raw_sha256']]
        row={'acquisition_id':record['acquisition_id'],'timestamp':record['timestamp'],'timestamp_source':record['timestamp_source'],
            'date':record['acquisition_date'],'dataset_display_name':record['dataset_display_name'],'primary_historical_cohort':record['primary_cohort'],
            'raw_sha256':record['raw_sha256'],'special_review_group':'JUNE09_FIRST' if record['acquisition_date']=='2026-06-09' and record['timestamp'][11:19] in g.CRITICAL_TIMES else '',
            **acquisition_disagreement(local,ppm,area_floor,snr_floor)}
        rows.append(row)
    ordered,thresholds=rank_disagreements(rows)
    thresholds.update(area_spread_denominator_floor_observed_positive_q25=area_floor,
        snr_spread_denominator_floor_observed_positive_q25=snr_floor,
        normalization='Average empirical rank scaled to [0,1]; exact zero disagreement maps to zero; tied ranks averaged',
        interpretation='Expected information from review, not bad-spectrum classification; majority is method consensus, not truth')
    write_json(gallery/'MANUAL_REVIEW_PRIORITY_DESIGN.json',thresholds);write_rows(gallery/'MANUAL_REVIEW_PRIORITY.csv',ordered)
    for row in ordered:
        write_json(gallery/row['acquisition_id']/'method_consensus.json',row)
    lines=['# Manual-review priority — method disagreement','',
        'Seven automatic phased methods contribute. The unphased reference and pending manual review are excluded. '
        'This ranks how informative review is likely to be; consensus is not a correct result. All real acquisitions remain MANUAL REVIEW PENDING.','',
        'Review the [June 9 special group](MANUAL_REVIEW_JUNE09.md) first regardless of global rank. '
        'Then follow the ranked queue and [August guide](MANUAL_REVIEW_AUGUST.md).','',
        '| Rank | Tier | Metadata acquisition time | Acquisition | Score | Detection consensus | Completion consensus | Reason |','|---:|---|---|---|---:|---|---|---|']
    for r in ordered:lines.append(f'| {r["rank"]} | {r["priority_tier"]} | {r["timestamp"]} | [{r["acquisition_id"]}]({r["acquisition_id"]}/target_peak_all_phase_methods.png) | {r["manual_review_priority_score"]:.2f} | {r["detection_consensus"]} | {r["completion_consensus"]} | {r["reason_for_priority"]} |')
    lines+=['','## Transparent score and tiers','',
        'Score = 25 × (phase + quantification + decision + quality), each normalized to [0,1] using the observed cohort. '
        'Equal group weights keep measurement units and correlated shape diagnostics from dominating by scale. They are a declared review-budget policy, not weights learned from known correctness. '
        'Quantification averages separately normalized area and S/N spreads. Quality averages the raw spreads in negative fraction, even imaginary leakage, asymmetry and odd-real dispersion. '
        'Decision disagreement is the minority fraction averaged across detection and retrospective prefix-completion.','',
        'Phase disagreement is the largest pairwise circular RMS difference in applied phase over 0–12 ppm on the same full k/N grid. '
        'Inverse/direct and pivots are converted before comparison. P0 wraps at 360°; P1 wraps only at 360N° on this grid, not 360°.','',
        f'Area spread = range / max(median absolute area, observed positive-area Q25={area_floor:.6g}); S/N uses its corresponding Q25={snr_floor:.6g}. '
        'The CSV exposes denominators and floor use; undefined weak/no-signal relative percentages are not disguised as enormous finite certainty.','',
        f'Priority A: any decision disagreement OR score ≥ observed Q75 ({thresholds["tier_A_score_threshold_observed_q75"]:.3f}). '
        f'Priority B: remaining score ≥ observed median ({thresholds["tier_B_score_threshold_observed_q50"]:.3f}). Priority C: remaining cases. '
        'Quartile/median cutoffs allocate review effort using the actual distribution; they are not instrument QC gates. Every component and normalized value is in the CSV.']
    (gallery/'MANUAL_REVIEW_PRIORITY.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    june=[r for r in ordered if r['special_review_group']];june.sort(key=lambda r:r['timestamp'])
    lines=['# June 9 — first special manual-review group','',
        'The preserved retrospective result is phase-sensitive: production, combined, symmetry and Ernst P0 first meet the criterion at 11:08:23; '
        'peak minima meets it at 11:36:54; ACME and DEEP Phaser never meet it. The unphased reference also never meets it. '
        'These outcomes are not verified physical historical controller stops.','',
        '| Metadata time | Acquisition | Why inspect | Method consensus |','|---|---|---|---|']
    purposes={'10:53:46':'Immediately before the production/combined/symmetry/Ernst criterion; establish the pre-plateau reference.',
        '11:08:23':'First criterion crossing for four methods; compare those against ACME, peak minima and DEEP.',
        '11:24:34':'Available subsequent acquisition; inspect the departure warning and phase-dependent confirmation.',
        '11:36:54':'Peak-minima criterion crossing; other methods disagree, and later area declines matter.'}
    for r in june:lines.append(f'| {r["timestamp"]} | [{r["acquisition_id"]}]({r["acquisition_id"]}/phase_comparison_all_methods.png) | {purposes[r["timestamp"][11:19]]} | detection {r["detection_consensus"]}; completion {r["completion_consensus"]} |')
    lines+=['','Adjust from production in the existing GUI, inspect real/imaginary target and major resonances, and save an explicit independent checkpoint. '
        'Retain the post-criterion departure warnings. Saved manual phase will be compared with every automatic method and replayed through the same completion calculation.']
    (gallery/'MANUAL_REVIEW_JUNE09.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    august=[r for r in ordered if r['date'] in ('2026-08-10','2026-08-11')]
    lines=['# August — detection and quantitative disagreements','',
        'Sorted by global information-priority score. Detection differences are explicit; area and S/N spreads use documented weak-signal floors. '
        'High rank does not mean a spectrum is bad.','',
        '| Global rank | Tier | Metadata time | Acquisition | Decision changes vs production | Reason |','|---:|---|---|---|---:|---|']
    for r in august:lines.append(f'| {r["rank"]} | {r["priority_tier"]} | {r["timestamp"]} | [{r["acquisition_id"]}]({r["acquisition_id"]}/target_peak_all_phase_methods.png) | {r["detection_changes_vs_production"]} | {r["reason_for_priority"]} |')
    (gallery/'MANUAL_REVIEW_AUGUST.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'thresholds':thresholds,'top10':ordered[:10]},indent=2))
    return ordered
