"""Freeze real evidence and measure calibration without changing real results."""
from pathlib import Path
import json
import numpy as np
from scipy.signal import find_peaks,peak_widths
import _bootstrap
from chemyx_lab.analysis import nmr_validation as v,phase_gallery as g,additional_phase_methods as a
from chemyx_lab.analysis.phase_audit import sha256,write_rows,write_json
root=v.DEFAULT_OUTPUT;gallery=root/'phase_validation';bench=gallery/'synthetic_ground_truth'
bench.mkdir(exist_ok=True);(bench/'logs').mkdir(exist_ok=True)
freeze=bench/'logs/real_outputs_before.csv'
if not freeze.exists():
    paths=[p for p in gallery.rglob('*') if p.is_file() and bench not in p.parents]
    prior=v.read_rows(root/'logs/additional_phase_methods/protected_after.csv')
    paths+= [Path(r['path']) for r in prior]
    sources=[v.ROOT/p for p in ('chemyx_lab/analysis/nmr.py','chemyx_lab/analysis/nmr_validation.py',
        'chemyx_lab/analysis/phase_quality.py','chemyx_lab/analysis/phase_optimizers.py',
        'scripts/nmr/process_fid.py','scripts/nmr/deep_phaser_offline.cjs','scripts/nmr/deep_phaser_native.py','configs/nmr/analysis.yaml')]
    paths+=sources
    write_rows(freeze,[{'path':str(p.resolve()),'sha256':sha256(p),'bytes':p.stat().st_size,
        'index_update_allowed':p in (gallery/'PHASE_VALIDATION_INDEX.md',gallery/'PHASE_VALIDATION_GALLERY.html')} for p in sorted(set(paths))])
    print('Frozen existing evidence',flush=True)
checks=a.verify_protected(root)
assert all(r['unchanged'] or r['index_update_allowed'] for r in checks)
rows=[]
for record in g.discover_acquisitions(root):
    folder=gallery/record['acquisition_id'];meta=g.json_read(folder/'automated/processing_metadata.json')
    target=g.json_read(folder/'analysis/production/target_result.json')
    with np.load(folder/'analysis/production/spectral_evidence.npz') as z:
        x=z['ppm'];fft=z['unphased'];phased=z['phased'];baseline=z['baseline']
    ctx=a.prepare_context(x,fft);noise=ctx.noise;dx=float(np.median(np.diff(x)))
    mag=np.abs(fft);indices,_=find_peaks(mag,prominence=max(8*noise,.025*mag.max()),distance=max(1,int(.03/dx)))
    widths=peak_widths(mag,indices)[0]*dx if len(indices) else np.array([])
    separations=np.diff(np.sort(x[indices]))
    peaks=v.read_rows(folder/'analysis/production/peak_results.csv')
    widths_target=[float(p['width_ppm'])*float(meta['jcamp_metadata']['.OBSERVE FREQUENCY']) for p in peaks if v.truth(p.get('in_target_window',False))]
    rows.append({'acquisition_id':record['acquisition_id'],'timestamp':record['timestamp'],'raw_sha256':record['raw_sha256'],
        'target_snr':target['snr'],'target_detected':target['detected'],'target_height':target['height'],
        'target_width_hz':float(np.median(widths_target)) if widths_target else None,
        'dominant_magnitude':float(mag.max()),'diagnostic_noise':noise,
        'baseline_rms_fraction':float(np.sqrt(np.mean(baseline**2))/max(mag.max(),1e-12)),
        'selected_peak_count':len(indices),'width_ppm_median':float(np.median(widths)) if len(widths) else None,
        'minimum_peak_separation_ppm':float(separations.min()) if len(separations) else None,
        'median_peak_separation_ppm':float(np.median(separations)) if len(separations) else None,
        'acquired_points':len(g.read_jcamp_fid(folder/'raw_source.dx').real),
        'spectral_width_hz':float(meta['jcamp_metadata']['$SWH']),
        'observe_frequency_mhz':float(meta['jcamp_metadata']['.OBSERVE FREQUENCY']),
        'line_broadening_hz':meta['parameters']['line_broadening_hz']})
write_rows(bench/'real_cohort_calibration.csv',rows)
summary={}
for key in ('target_snr','target_height','target_width_hz','dominant_magnitude','baseline_rms_fraction','selected_peak_count','width_ppm_median','minimum_peak_separation_ppm','median_peak_separation_ppm','spectral_width_hz','observe_frequency_mhz'):
    values=[r[key] for r in rows if r.get(key) is not None and (key!='target_snr' or r['target_detected'])]
    summary[key]={'min':float(np.min(values)),'q25':float(np.quantile(values,.25)),'median':float(np.median(values)),'q75':float(np.quantile(values,.75)),'max':float(np.max(values)),'n':len(values)}
write_json(bench/'real_calibration_summary.json',summary)
print(json.dumps(summary,indent=2))
