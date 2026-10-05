"""Known-truth comparator benchmark, isolated from the real acquisition gallery.

Physical damped-resonance raw FIDs go through the production preprocessing.
The exact linear phase operator is then injected on that FFT grid, equivalently
on its full processed time-domain trace. This avoids claiming that truncation
commutes with fractional delay. Both time-domain arrays are retained.
"""
from pathlib import Path
import hashlib
import json
import numpy as np
import nmrglue as ng
from . import nmr_validation as v,phase_gallery as g,additional_phase_methods as a
from . import phase_quality as q,phase_optimizers as opt
from .nmr import read_jcamp_fid,_build_complex_spectrum,fourier_transform_fid
from .phase_audit import write_json,write_rows,sha256

BENCH=v.DEFAULT_OUTPUT/'phase_validation/synthetic_ground_truth'
P0_VALUES=(-120.,-60.,-30.,-10.,0.,10.,30.,60.,120.)
P1_VALUES=(-160.,-79.,-20.,0.,20.,79.,160.)
CONDITIONS=('clean_high_snr','moderate_noise','low_snr','overlap','unequal_amplitudes',
    'baseline_distortion','broad_peaks','edge_truncation','mixed_difficulty','dense_reference')
ESTIMATORS=('acme','peak_minima','combined_objective_v1','symmetry_objective','ernst_integral_p0','deep_phaser')
INITIAL_DIRECT=(-59.5,79.)  # fixed real-cohort prior; never the injected truth


def circular_degrees(values,period=360.):
    return (np.asarray(values)+period/2)%period-period/2


def direct_phase(p0,p1,inverse=False,pivot_fraction=0.):
    sign=-1 if inverse else 1
    return sign*(float(p0)-float(p1)*pivot_fraction),sign*float(p1)


def phase_errors(recovered,true_corrective,points,peak_indices=(),magnitude=None):
    r0,r1=recovered;t0,t1=true_corrective
    delta=circular_degrees((r0-t0)+(r1-t1)*np.arange(points)/points)
    out={'p0_error_deg':float(abs(circular_degrees(r0-t0))),
        # P1+360 is NOT equivalent on a k/N grid. Its alias period is 360*N.
        'p1_error_deg':float(abs(circular_degrees(r1-t1,360.*points))),
        'phase_ramp_rms_deg':float(np.sqrt(np.mean(delta**2))),
        'phase_ramp_max_deg':float(np.max(np.abs(delta))),
        'peak_phase_rms_deg':float(np.sqrt(np.mean(delta[list(peak_indices)]**2))) if len(peak_indices) else None,
        'peak_phase_errors_deg':delta[list(peak_indices)].tolist()}
    if magnitude is not None:
        weights=np.abs(magnitude)**2
        out['signal_weighted_phase_rms_deg']=float(np.sqrt(np.sum(weights*delta**2)/weights.sum()))
    return out


def generate_fid(condition,seed,settings):
    """Only physical resonance parameters and random receiver noise generate FID."""
    rng=np.random.default_rng(seed);n=settings['acquired_points'];sw=settings['spectral_width_hz'];sf=settings['observe_frequency_mhz']
    t=np.arange(n)/sw;dominant=settings['dominant_height'];target=settings['target_height']
    centers=[2.08,3.17,4.38,5.80,6.99,8.23]
    heights=[dominant,.002*dominant,.001*dominant,target,.85*dominant,.0015*dominant]
    widths=[4.5,1.9,1.9,1.9,4.5,2.2]
    nominal_snr={'clean_high_snr':100.,'moderate_noise':28.,'low_snr':4.,'overlap':28.,
        'unequal_amplitudes':28.,'baseline_distortion':28.,'broad_peaks':28.,'edge_truncation':28.,
        'mixed_difficulty':12.,'dense_reference':28.}[condition]
    if condition in ('overlap','mixed_difficulty'):
        centers += [5.825,6.965];heights += [.7*target,.35*dominant];widths += [2.,3.8]
    if condition in ('unequal_amplitudes','mixed_difficulty'):
        heights[0]*=1.35;heights[4]*=1.35
    if condition=='broad_peaks':widths=[w*2.0 for w in widths]
    if condition=='edge_truncation':
        centers += [settings['center_ppm']+.495*sw/sf];heights += [.4*dominant];widths += [8.]
        widths[1]=.12  # long lived weak line has a measurable finite-FID tail
    if condition=='dense_reference':
        for center in np.linspace(.5,10.5,15):
            if min(abs(center-c) for c in centers)<.18:continue
            centers.append(float(center));heights.append(.015*dominant);widths.append(2.2)
    # Small reproducible sample-to-sample variation; every phase case sharing a
    # condition/seed has the same raw FID, so phase comparisons are paired.
    centers=np.asarray(centers)+rng.uniform(-.002,.002,len(centers))
    heights=np.asarray(heights)*rng.uniform(.9,1.1,len(heights))
    clean=np.zeros(n,complex);resonances=[]
    for center,height,width in zip(centers,heights,widths):
        amplitude=float(height*np.pi*(width+settings['line_broadening_hz'])/sw)
        clean+=amplitude*np.exp(-np.pi*width*t)*np.exp(2j*np.pi*(center-settings['center_ppm'])*sf*t)
        resonances.append({'ppm':float(center),'nominal_fft_height':float(height),'linewidth_hz':float(width),'fid_amplitude':amplitude})
    # Half-weight the initial causal sample: removes the rectangular-DFT
    # constant pedestal without rotating the known absorptive resonance phase.
    clean[0]*=.5
    em=np.exp(-np.pi*settings['line_broadening_hz']*t)
    sigma_td=float(target/nominal_snr/np.sqrt(np.dot(em,em)))
    noise=sigma_td*(rng.standard_normal(n)+1j*rng.standard_normal(n))
    artifact=np.zeros(n,complex)
    if condition in ('baseline_distortion','mixed_difficulty'):
        # Receiver DC/early-sample transient yields flat/curved complex baseline.
        artifact[:3]=dominant*np.array([.025,.012+.006j,-.005+.004j])
    observed=clean+noise+artifact
    metadata={'seed':seed,'condition':condition,'resonances':resonances,'nominal_target_snr':nominal_snr,
        'target_nominal_height':target,'noise_td_per_channel_sigma':sigma_td,
        'noise_model':'independent Gaussian real/imaginary receiver noise before apodization',
        'receiver_artifact_first_three_points':[[float(z.real),float(z.imag)] for z in artifact[:3]],
        'baseline_condition':'receiver DC plus early-sample transient' if np.any(artifact) else 'none',
        'overlap_condition':condition in ('overlap','mixed_difficulty'),
        'physical_acquisition_duration_s':(n-1)/sw,'settings':settings}
    return clean,observed,metadata


def write_jcamp(path,data,settings):
    """Simple uncompressed real/imaginary NTUPLES; synthetic origin explicit."""
    path=Path(path);factor=1e-9;n=len(data)
    headers=['##TITLE=SYNTHETIC GROUND TRUTH - NOT INSTRUMENT DATA','##JCAMP-DX=5.01',
        '##DATA TYPE=NMR FID','##DATA CLASS=NTUPLES','##ORIGIN=Deterministic analytic synthetic resonances',
        '##.OBSERVE NUCLEUS=^1H',f'##.OBSERVE FREQUENCY={settings["observe_frequency_mhz"]:.17g}',
        f'##$SWH={settings["spectral_width_hz"]:.17g}',f'##$SF={settings["observe_frequency_mhz"]:.17g}',
        f'##$O1P={settings["center_ppm"]:.17g}','##$PHC0=59.5','##$PHC1=-79',
        '##NTUPLES=NMR FID','##VAR_NAME=TIME,FID/REAL,FID/IMAG,PAGE NUMBER','##SYMBOL=X,R,I,N',
        '##VAR_TYPE=INDEPENDENT,DEPENDENT,DEPENDENT,PAGE','##VAR_FORM=AFFN,AFFN,AFFN,AFFN',
        f'##VAR_DIM={n},{n},{n},2','##UNITS=SECONDS,ARBITRARY UNITS,ARBITRARY UNITS,',
        '##FIRST=0,0,0,1',f'##LAST={(n-1)/settings["spectral_width_hz"]:.17g},0,0,2',
        f'##FACTOR={1/settings["spectral_width_hz"]:.17g},{factor:.17g},{factor:.17g},1']
    for page,component,symbol in ((1,data.real,'R'),(2,data.imag,'I')):
        headers.extend([f'##PAGE=N={page}',f'##DATA TABLE=(X++({symbol}..{symbol})), XYDATA'])
        integers=np.rint(component/factor).astype(np.int64)
        for i in range(0,n,4):headers.append(str(i)+' '+' '.join(str(int(z)) for z in integers[i:i+4]))
    headers+=['##END NTUPLES=NMR FID','##END=']
    path.write_text('\n'.join(headers)+'\n',encoding='ascii')


def settings_from_real():
    calibration=g.json_read(BENCH/'real_calibration_summary.json')
    return {'acquired_points':8192,'processed_points':65536,
        'spectral_width_hz':calibration['spectral_width_hz']['median'],
        'observe_frequency_mhz':calibration['observe_frequency_mhz']['median'],'center_ppm':5.,
        'line_broadening_hz':.03,'dominant_height':calibration['dominant_magnitude']['median'],
        'target_height':calibration['target_height']['median'],'truncation_window':'none'}


def production_arguments(source):
    meta=g.json_read(v.DEFAULT_OUTPUT/'phase_validation/20260609_110823_d14e2a0c/automated/processing_metadata.json')
    return v.production_args(source,meta['parameters'])


def make_cases():
    settings=settings_from_real();rows=[]
    designs=[('clean_high_snr',20261002,p0,p1,'full_clean_grid') for p0 in P0_VALUES for p1 in P1_VALUES]
    for index,condition in enumerate(CONDITIONS[1:],1):
        for j,p0 in enumerate(P0_VALUES):
            p1=0. if p0==0 else P1_VALUES[(j+index)%len(P1_VALUES)]
            designs.append((condition,20261002+index*100,p0,p1,'balanced_stress_screen'))
    for index,condition in enumerate(CONDITIONS):
        for replicate in range(1,4):
            for p0,p1 in ((-60.,-79.),(0.,0.),(60.,79.)):
                designs.append((condition,20261002+index*100+replicate,p0,p1,'independent_seed_replication'))
    bases={}
    for i,(condition,seed,p0,p1,stratum) in enumerate(designs):
        ident=f'SYN_{i+1:04d}_{condition}';folder=BENCH/'cases'/ident;folder.mkdir(parents=True,exist_ok=True)
        key=(condition,seed)
        if key not in bases:
            clean,raw,metadata=generate_fid(condition,seed,settings)
            basefolder=BENCH/'physical_fids'/f'{condition}_{seed}';basefolder.mkdir(parents=True,exist_ok=True)
            source=basefolder/'synthetic_raw_fid.dx';ideal_source=basefolder/'synthetic_noisefree_fid.dx'
            write_jcamp(source,raw,settings);write_jcamp(ideal_source,clean,settings)
            args=production_arguments(source)
            gold=v.analyze(source,args,phase=(0.,0.,False,'known_zero_phase'))
            ideal=v.analyze(ideal_source,args,phase=(0.,0.,False,'known_zero_phase'))
            actual=np.array(read_jcamp_fid(source).complex_points)
            np.testing.assert_allclose(actual,raw,atol=7.1e-10,rtol=0)
            metadata.update(raw_fid_sha256=sha256(source),ideal_raw_fid_sha256=sha256(ideal_source),
                realized_correct_phase_target=gold['target'],noisefree_target=ideal['target'],
                target_width_hz_realized=next((float(p['width_ppm'])*settings['observe_frequency_mhz'] for p in gold['peaks'] if p['in_target_window']),None))
            write_json(basefolder/'metadata.json',metadata)
            bases[key]=(source,metadata,gold,ideal,actual)
        source,metadata,gold,ideal,raw=bases[key]
        observed=ng.proc_base.ps(gold['unphased'],p0=p0,p1=p1)
        processed_fid=np.fft.ifft(np.fft.ifftshift(observed))
        roundtrip=fourier_transform_fid(processed_fid)
        error=float(np.max(np.abs(roundtrip-observed)))
        np.testing.assert_allclose(roundtrip,observed,rtol=1e-12,atol=1e-8)
        np.savez_compressed(folder/'synthetic_arrays.npz',raw_complex_fid=raw,
            processed_phase_distorted_fid=processed_fid,ppm=gold['spectrum'].ppm_axis,
            known_correct_phase_fft=gold['unphased'],observed_fft=observed)
        case={'case_id':ident,'dataset_display_name':ident,'source_path':str(source.resolve()),'seed':seed,
            'condition':condition,'design_stratum':stratum,'imposed_p0_deg':p0,'imposed_p1_deg':p1,
            'true_corrective_direct_p0_deg':-p0,'true_corrective_direct_p1_deg':-p1,
            'phase_equation':'direct exp(+i*pi/180*(P0+P1*k/N)), pivot index zero, N=65536',
            'nominal_target_snr':metadata['nominal_target_snr'],'realized_oracle_snr':gold['target']['snr'],
            'realized_oracle_detected':gold['target']['detected'],'oracle_target':gold['target'],
            'noisefree_target':ideal['target'],'raw_sha256':metadata['raw_fid_sha256'],
            'observed_fft_sha256':hashlib.sha256(observed.tobytes()).hexdigest(),
            'raw_fid_points':len(raw),'processed_fid_points':len(processed_fid),'fft_roundtrip_max_error':error,
            'physical_metadata':metadata,'completion_classification':'NOT APPLICABLE: independent cases, no kinetic sequence'}
        write_json(folder/'metadata.json',case)
        rows.append({k:value for k,value in case.items() if k not in ('physical_metadata','oracle_target','noisefree_target')})
    write_rows(BENCH/'synthetic_cases.csv',rows)
    print(f'Created {len(rows)} phase-distorted cases from {len(bases)} independent physical FIDs',flush=True)
    return rows


def estimate_classical(case,observed,context):
    from scipy.optimize import fmin
    settings={'xtol':1e-4,'ftol':1e-4,'maxiter':1000,'maxfun':2000,'disp':False,'full_output':True}
    width=max(10,int(round(.10*context.metadata['scoring_stride']/np.median(np.diff(context.ppm)))))
    results={}
    for method in ('acme','peak_minima'):
        objective=getattr(ng.proc_autophase,'_ps_'+method+'_score')
        args=(observed,) if method=='acme' else (observed,width)
        try:
            phases,value,nit,nfev,flag=fmin(objective,INITIAL_DIRECT,args=args,**settings)
        except Exception as exc:
            # Keep the unchanged objective's failure visible, and allow the
            # other comparators and oracle controls to finish this same case.
            results[method]={'method':method,'success':False,'status':'optimizer_exception',
                'p0_deg':None,'p1_deg':None,'message':type(exc).__name__+': '+str(exc),
                'configuration':settings,'initial_direct_deg':INITIAL_DIRECT,
                'peak_width_points':width if method=='peak_minima' else None}
            continue
        results[method]={'method':method,'success':bool(flag==0 and np.isfinite(value) and np.all(np.isfinite(phases))),
            'status':'converged' if flag==0 else 'nonconverged','p0_deg':float(phases[0]),'p1_deg':float(phases[1]),
            'inverse_phase':False,'objective_value':float(value),'iterations':int(nit),'evaluations':int(nfev),
            'configuration':settings,'initial_direct_deg':INITIAL_DIRECT,'peak_width_points':width if method=='peak_minima' else None}
    for method in ('combined_objective_v1','symmetry_objective'):
        results[method]=opt.optimize_phase(context,method,INITIAL_DIRECT)
    results['ernst_integral_p0']=opt.ernst_integral_p0(np.load(BENCH/'cases'/case['case_id']/'synthetic_arrays.npz')['ppm'],observed)
    return results


def evaluate_method(case,method,status,observed,context):
    row={'case_id':case['case_id'],'condition':case['condition'],'seed':case['seed'],'design_stratum':case['design_stratum'],
        'method':method,'is_estimator':method in ESTIMATORS,'success':bool(status['success']),'status':status['status'],
        'imposed_p0_deg':case['imposed_p0_deg'],'imposed_p1_deg':case['imposed_p1_deg'],
        'true_corrective_direct_p0_deg':case['true_corrective_direct_p0_deg'],'true_corrective_direct_p1_deg':case['true_corrective_direct_p1_deg'],
        'nominal_target_snr':case['nominal_target_snr'],'oracle_snr':case['oracle_target']['snr'],
        'raw_sha256':case['raw_sha256'],'observed_fft_sha256':case['observed_fft_sha256'],
        'completion_classification':'NOT APPLICABLE: independent synthetic cases'}
    if not status['success']:return row,None
    effective=direct_phase(status['p0_deg'],status['p1_deg'],status.get('inverse_phase',False))
    x=context.ppm;full_x=np.load(BENCH/'cases'/case['case_id']/'synthetic_arrays.npz')['ppm']
    peaks=[int(np.argmin(abs(full_x-r['ppm']))) for r in case['physical_metadata']['resonances']]
    row.update(p0_direct_deg=effective[0],p1_direct_deg=effective[1],
        **phase_errors(effective,(case['true_corrective_direct_p0_deg'],case['true_corrective_direct_p1_deg']),len(observed),peaks,observed))
    args=production_arguments(case['source_path'])
    result=v.analyze(case['source_path'],args,phase=(effective[0],effective[1],False,method),unphased=observed)
    phased=result['spectrum'].real+1j*result['spectrum'].imaginary
    row.update(q.score_spectrum(phased,context));row['combined_quality_score']=opt.objective(effective,context,'combined_objective_v1')
    target=result['target'];oracle=case['oracle_target'];ideal=case['noisefree_target']
    for metric in ('peak_ppm','height','area','snr'):
        actual=target[metric];truth=oracle[metric];ideal_value=ideal[metric]
        row['target_'+metric]=actual
        row[metric+'_absolute_error']=abs(actual-truth) if actual is not None and truth is not None else None
        row[metric+'_percent_error']=100*abs(actual-truth)/abs(truth) if actual is not None and truth is not None and abs(truth)>1e-12 else None
        row[metric+'_error_vs_noisefree']=abs(actual-ideal_value) if actual is not None and ideal_value is not None else None
    row.update(target_detected=target['detected'],oracle_detected=oracle['detected'],
        detection_error=target['detected']!=oracle['detected'],phase_recovered_within_5deg=row['phase_ramp_rms_deg']<=5,
        same_downstream_parameters=True)
    return row,phased


def run_case(case_row,mode='classical'):
    folder=BENCH/'cases'/case_row['case_id'];case=g.json_read(folder/'metadata.json')
    output=folder/(mode+'_results.json')
    if output.exists():return case['case_id']+' cached '+mode
    with np.load(folder/'synthetic_arrays.npz') as arrays:observed=arrays['observed_fft'];x=arrays['ppm']
    context=q.prepare_context(x,observed)
    if mode=='deep':statuses={'deep_phaser':a.run_deep(folder,{'unphased':observed},
        {'raw_sha256':case['observed_fft_sha256']})}
    else:
        statuses=estimate_classical(case,observed,context)
        statuses['metadata_correct_oracle']={'success':True,'status':'known metadata application, not estimation',
            'p0_deg':case['imposed_p0_deg'],'p1_deg':case['imposed_p1_deg'],'inverse_phase':True}
        statuses['metadata_nominal_anchor']={'success':True,'status':'fixed real-cohort metadata, not estimation',
            'p0_deg':59.5,'p1_deg':-79.,'inverse_phase':True}
    rows=[];spectra={}
    for method,status in statuses.items():
        write_json(folder/'optimizer'/f'{method}.json',status)
        row,phased=evaluate_method(case,method,status,observed,context);rows.append(row)
        if phased is not None:spectra[method]=phased
    np.savez_compressed(folder/(mode+'_recovered_spectra.npz'),**spectra)
    write_json(output,rows)
    return case['case_id']+' '+mode+' '+','.join(r['method']+':'+r['status'] for r in rows)
