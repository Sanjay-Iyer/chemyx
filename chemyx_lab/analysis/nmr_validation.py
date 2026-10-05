"""Offline, non-destructive validation against the existing production processor.

Scientific calculations are delegated to production helpers. Historical evidence,
current-config results, alternative phases, and manual checkpoints remain distinct.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import nmrglue as ng
import numpy as np

from .nmr import (_build_complex_spectrum, analyze_dx_peak, build_phased_spectrum,
                  NmrProcessingError, read_jcamp_fid)
from .phase_audit import provenance, retain_phase_audit, save_figure, sha256, write_json, write_rows
from .plot_titles import resolve_dataset_display_name
from .statistics_report import PeakObservation, SpectrumStat
from .target_peak_report import build_target_peak_analysis

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "results/NMR_validation_100226"
DEFAULT_INVENTORY = ROOT / "results/nmr_instrument_data_inventory_20261002.json"


def pipeline():
    script_dir = str(ROOT / "scripts/nmr")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import process_fid
    return process_fid


def read_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def finite(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def truth(value):
    return value is True or str(value).lower() == "true"


def acquisition_time(metadata):
    """Metadata only. Keep the recorded timezone; fail closed rather than infer."""
    value = metadata.get("LONG DATE", "").strip()
    for fmt in ("%Y/%m/%d %H:%M:%S%z", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt), "LONG DATE header"
        except ValueError:
            pass
    if metadata.get("$DATE"):
        from datetime import timezone
        return datetime.fromtimestamp(float(metadata["$DATE"]), timezone.utc), "$DATE epoch header"
    raise ValueError("No authoritative acquisition timestamp: " + repr(value))


def phase_field_reconciliation(metadata, unphased):
    """Verify centered-direct versus index-zero-inverse metadata algebra."""
    v=metadata.get("$PHASECORRECTION","").split(",")
    p0=finite(metadata.get("$PHC0"));p1=finite(metadata.get("$PHC1"))
    row={"data_type":metadata.get("DATA TYPE"),"ntuples":metadata.get("NTUPLES"),
         "var_name":metadata.get("VAR_NAME"),"phasecorrection_raw":metadata.get("$PHASECORRECTION"),
         "vendor_pivot":metadata.get("$PIVOT"),"ph_mod":metadata.get("$PH_mod",metadata.get("$PH_MOD")),
         "production_phase_fields":"$PHC0,$PHC1 only", "phc0":p0,"phc1":p1,
         "phase_field_missing":p0 is None or p1 is None,
         "vendor_semantics_status":"algebra verified from file values; exporter/pivot semantic definition not documented in repository"}
    if len(v)!=2 or p0 is None or p1 is None:
        row["equivalent_centered_direct_convention"]=None;return row
    v0,v1=map(float,v)
    expected0=-v0+v1/2;expected1=-v1
    row.update(phasecorrection_p0=v0,phasecorrection_p1=v1,expected_phc0=expected0,
               expected_phc1=expected1,phc0_residual=p0-expected0,phc1_residual=p1-expected1)
    k=np.arange(len(unphased))/len(unphased)
    centered=np.asarray(unphased)*np.exp(1j*np.pi/180*(v0+v1*(k-.5)))
    production=ng.proc_base.ps(unphased,p0=p0,p1=p1,inv=True)
    relative_error=float(np.max(np.abs(centered-production))/max(np.max(np.abs(production)),1e-30))
    row["centered_direct_vs_production_max_relative_error"]=relative_error
    row["equivalent_centered_direct_convention"]=relative_error<1e-10
    return row


def load_cohort(inventory=DEFAULT_INVENTORY):
    entries = json.loads(Path(inventory).read_text(encoding="utf-8-sig"))
    import yaml
    config=yaml.safe_load((ROOT/'configs/nmr/analysis.yaml').read_text()) or {}
    configured=(config.get('statistics',{}).get('dataset_display_name') or
                config.get('target_peak',{}).get('dataset_display_name'))
    unique = {}
    for entry in entries:
        source = ROOT / entry["SourcePath"]
        digest = sha256(source)
        if digest.lower() != entry["Sha256"].lower():
            raise ValueError(f"Inventory/source hash mismatch: {source}")
        if digest in unique:
            unique[digest]["duplicate_inventory_rows"] += 1
            continue
        fid = read_jcamp_fid(source)
        stamp, field = acquisition_time(fid.metadata)
        metadata_name=stamp.strftime('%m-%d-%y')
        # This config names one dataset. Apply it to that acquisition date;
        # other dates retain their authoritative metadata identity.
        dataset_name=resolve_dataset_display_name(
            configured if str(configured).strip()==metadata_name else None,
            metadata={'dataset_name':metadata_name},input_paths=source)
        ident = stamp.strftime("%Y%m%d_%H%M%S") + "_" + digest[:8]
        unique[digest] = {"acquisition_id": ident, "timestamp": stamp.isoformat(),
            "timestamp_source": field, "acquisition_date": stamp.date().isoformat(),
            "source_path": str(source.resolve()), "canonical_file_path": str(source.resolve()),
            "raw_sha256": digest, "previous_processing_exists": bool(entry["ProcessingEvidence"]),
            "primary_cohort": bool(entry["ProcessingEvidence"]),
            "historical_result_paths": [str((ROOT / p).resolve()) for p in entry["ProcessingEvidence"]],
            "duplicate_copy_count": entry["CopyCount"], "duplicate_inventory_rows": 0,
            "copy_paths": [str((ROOT / p).resolve()) for p in entry["CopyPaths"]],
            "instrument": fid.metadata.get("SPECTROMETER/DATA SYSTEM"),
            "dataset_display_name": dataset_name,
            "notes": "primary historical comparison" if entry["ProcessingEvidence"] else "secondary exploratory; no confirmed historical processing"}
    return sorted(unique.values(), key=lambda e: e["timestamp"])


def production_args(source, overrides=None):
    mod = pipeline()
    args = mod._parser(mod._config_defaults([])).parse_args([str(source)])
    if overrides:
        for key, value in overrides.items():
            if hasattr(args, key) and key not in {"paths", "output_dir", "run_name", "config"}:
                setattr(args, key, value)
    if args.solvent != "none" or args.reference_model != "metadata" or args.reference_method != "metadata":
        raise ValueError("Validation supports this cohort's metadata/no-shift reference path only; use full process_fid for other references")
    if error := mod._error(args):
        raise ValueError(error)
    return args


def analyze(source, args=None, *, phase=None, unphased=None):
    """One production-equivalent evaluation; phase is the only comparator override."""
    args = args or production_args(source)
    spectrum = build_phased_spectrum(source, line_broadening_hz=args.line_broadening_hz,
        zero_fill_points=args.zero_fill_points, phase0_deg=args.phase0, phase1_deg=args.phase1,
        inverse_phase=not args.direct_phase, phase_method=args.phase_method,
        truncation_window=args.truncation_window)
    if unphased is None:
        _, _, unphased, _, _ = _build_complex_spectrum(source,
            line_broadening_hz=spectrum.line_broadening_hz, zero_fill_points=spectrum.processed_points,
            truncation_window=spectrum.truncation_window)
    if phase is not None:
        p0, p1, inverse, method = phase
        phased = ng.proc_base.ps(unphased, p0=p0, p1=p1, inv=inverse)
        spectrum = replace(spectrum, real=phased.real.copy(), imaginary=phased.imag.copy(),
                           magnitude=np.abs(phased), phase0_deg=float(p0), phase1_deg=float(p1),
                           phase_method=method)
        args = SimpleNamespace(**vars(args))
        args.direct_phase = not inverse
    quantitative, magnitude, picked = pipeline().process_spectrum_for_peaks(spectrum, args)
    rows = []
    for index, peak in enumerate(picked.peaks):
        width_hz = peak.width_ppm * spectrum.observe_frequency_mhz
        passed, reasons, gates = pipeline()._peak_qc(peak, width_hz, args)
        row = {**asdict(peak), "peak_number": index + 1, "height": peak.peak_height,
            "width_hz": width_hz, "qc_pass": passed, "qc_failure_reasons": reasons,
            "integration_left_ppm": peak.interpolated_ppm - max(peak.width_ppm, args.min_peak_width_ppm),
            "integration_right_ppm": peak.interpolated_ppm + max(peak.width_ppm, args.min_peak_width_ppm),
            "integration_units": "a.u. * ppm", "in_target_window":
                abs(peak.interpolated_ppm - args.simple_target_ppm) <= args.simple_window_ppm,
            "detection_min_prominence_snr": args.min_prominence_snr,
            "qc_min_snr": args.qc_min_snr, "qc_min_prominence_snr": args.qc_min_prominence_snr,
            "qc_min_width_hz": args.qc_min_width_hz, "qc_max_width_hz": args.qc_max_width_hz,
            **gates}
        rows.append(row)
    selected = pipeline()._select_simple_peak_rows(rows, args)
    best = max(selected, key=lambda r: r["snr"]) if selected else None
    target = {"detected": best is not None, "peak_ppm": best["interpolated_ppm"] if best else None,
        "height": best["height"] if best else 0.0, "area": best["positive_area"] if best else 0.0,
        "snr": best["snr"] if best else 0.0, "noise": best["noise"] if best else picked.noise,
        "baseline": best["baseline"] if best else None}
    return {"spectrum": spectrum, "args": args, "unphased": np.asarray(unphased).copy(),
            "quantitative": np.asarray(quantitative), "picked": picked, "peaks": rows, "target": target}


def spectrum_stat(result, index=0):
    spec, picked = result["spectrum"], result["picked"]
    stamp, field = acquisition_time(spec.metadata)
    peaks = [PeakObservation(i+1, "", p.peak_ppm, p.peak_height, p.width_ppm,
        p.width_ppm*spec.observe_frequency_mhz, p.signed_area, p.positive_area, p.snr,
        p.prominence_snr, p.classification, p.prominence) for i, p in enumerate(picked.peaks)]
    return SpectrumStat(spec.source.name, str(spec.source), index, stamp,
        spec.observe_frequency_mhz, picked.noise, 0.0, True, spec.phase0_deg, spec.phase1_deg,
        peaks, picked.ppm_axis, picked.quantitative_corrected, spec.ppm_axis,
        result["quantitative"], field)


def delta_table(automated, other):
    rows = []
    for metric in ("peak_ppm", "height", "area", "snr", "noise", "baseline"):
        a, b = automated["target"][metric], other["target"][metric]
        delta = None if a is None or b is None else b-a
        rows.append({"metric": metric, "automated": a, "comparison": b,
            "absolute_difference": delta,
            "percent_difference": None if a is None or abs(a) < 1e-12 or delta is None else 100*delta/abs(a)})
    for metric in ("phase0_deg", "phase1_deg"):
        a, b = getattr(automated["spectrum"], metric), getattr(other["spectrum"], metric)
        rows.append({"metric": metric, "automated": a, "comparison": b,
                     "absolute_difference": b-a, "percent_difference": None})
    rows.append({"metric": "detected", "automated": automated["target"]["detected"],
                 "comparison": other["target"]["detected"],
                 "absolute_difference": int(other["target"]["detected"])-int(automated["target"]["detected"])})
    return rows


def export_result(result, output, acquisition, provenance_data, *, csv_arrays=True, figures=True):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    spec, picked = result["spectrum"], result["picked"]
    dataset = acquisition["dataset_display_name"]
    meta = retain_phase_audit(spec, result["args"], spec.ppm_axis, result["quantitative"],
        picked, output, dataset=dataset, unphased=result["unphased"], provenance_data=provenance_data)
    meta.update({"acquisition_id": acquisition["acquisition_id"],
                 "actual_acquisition_timestamp": acquisition["timestamp"],
                 "primary_cohort": acquisition["primary_cohort"]})
    write_json(output / "processing_metadata.json", meta)
    write_rows(output / "peak_results.csv", result["peaks"], columns=list(result["peaks"][0]) if result["peaks"] else
        ["peak_number", "peak_ppm", "height", "positive_area", "snr", "qc_pass"])
    write_json(output / "target_result.json", result["target"])
    if csv_arrays:
        fid=read_jcamp_fid(spec.source)
        sweep=float(fid.metadata.get("$SWH",fid.metadata.get("$SWEEP WIDTH")))
        np.savetxt(output/"raw_complex_fid.csv",np.column_stack((np.arange(len(fid.real))/sweep,fid.real,fid.imag)),
            delimiter=",",fmt="%.17g",header="time_seconds,real,imaginary",comments="")
        np.savetxt(output/"pipeline_display_spectrum.csv",np.column_stack((spec.ppm_axis,result["quantitative"])),
            delimiter=",",fmt="%.17g",header="referenced_ppm,real",comments="")
        np.savetxt(output / "unphased_spectrum.csv", np.column_stack((spec.ppm_axis,
            result["unphased"].real, result["unphased"].imag)), delimiter=",", fmt="%.17g",
            header="ppm,real,imaginary", comments="")
        np.savetxt(output / "automatically_phased_spectrum.csv", np.column_stack((spec.ppm_axis,
            spec.real, spec.imaginary, spec.real-result["quantitative"], result["quantitative"])),
            delimiter=",", fmt="%.17g", header="ppm,real,imaginary,global_baseline,baseline_corrected_real", comments="")
        np.savetxt(output / "regional_peak_analysis.csv", np.column_stack((picked.ppm_axis,
            picked.magnitude, picked.baseline, picked.corrected, picked.smoothed,
            picked.quantitative_corrected)), delimiter=",", fmt="%.17g",
            header="ppm,detection_input,local_baseline,corrected,smoothed_detection,quantitative_corrected", comments="")
    manifests = read_rows(output / "plot_manifest.csv")
    if figures:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained")
        x = np.asarray(spec.ppm_axis)
        axes[0,0].plot(x, result["unphased"].real, lw=.7, label="Unphased")
        axes[0,0].plot(x, spec.real, lw=.7, label="Production phased")
        axes[0,0].legend(fontsize=8)
        axes[0,0].set(title="Full spectrum", xlim=(x.max(), x.min()))
        axes[0,1].plot(x, result["unphased"].real, lw=.8, label="Before")
        axes[0,1].plot(x, spec.real, lw=.8, label="After")
        axes[0,1].set(title="Phase effect near target", xlim=(6.5,5.0))
        mask=(x>=5)&(x<=6.5)
        values=np.concatenate((result['unphased'].real[mask],np.asarray(spec.real)[mask]))
        span=max(float(np.ptp(values)),1)
        axes[0,1].set_ylim(float(values.min())-.08*span,float(values.max())+.08*span)
        axes[0,1].legend(fontsize=8)
        rx = np.asarray(picked.ppm_axis)
        axes[1,0].plot(rx, picked.quantitative_corrected, label="Quantitative, local baseline removed")
        axes[1,0].plot(rx, picked.smoothed, lw=.7, label="Detection only (smoothed)")
        axes[1,0].axhline(result["args"].min_prominence_snr*picked.noise, ls=":", color="gray",
            label="Minimum prominence scale (not height gate)")
        for row in result["peaks"]:
            mask = (rx >= row["integration_left_ppm"]) & (rx <= row["integration_right_ppm"])
            axes[1,0].fill_between(rx[mask], 0, np.maximum(np.asarray(picked.quantitative_corrected)[mask],0),
                alpha=.2, color="green" if row["qc_pass"] else "red")
            axes[1,0].axvline(row["interpolated_ppm"], lw=.6, ls="--")
        axes[1,0].set(title="Peak detection and integration", xlim=(6.5,5.0))
        axes[1,0].legend(fontsize=7)
        text = (f"Acquisition: {acquisition['timestamp']}\nRaw SHA-256: {acquisition['raw_sha256'][:24]}…\n"
                f"File: {spec.source.name}\n\nMethod: {spec.phase_method}\nP0={spec.phase0_deg:.6g}°  P1={spec.phase1_deg:.6g}°\n"
                f"Pivot: index 0 ({x[0]:.6g} ppm)\nLB={spec.line_broadening_hz:g} Hz, FFT N={spec.processed_points}\n"
                f"Baseline: {result['args'].baseline_method}\n\nTarget detected: {result['target']['detected']}\n"
                f"ppm: {result['target']['peak_ppm']}\nHeight: {result['target']['height']:.6g} a.u.\n"
                f"Area: {result['target']['area']:.6g} a.u.·ppm\nS/N: {result['target']['snr']:.6g}\n"
                + ("Manual/reference phase displayed; see checkpoint role\n" if spec.phase_method=='manual' else
                 "Manual expert review: pending unless checkpoint present\n") +
                "Controller stop: requires historical run evidence")
        import textwrap
        text = "\n".join(textwrap.fill(line, 65) for line in text.splitlines())
        axes[1,1].axis("off")
        axes[1,1].text(0,1,text,ha="left",va="top",transform=axes[1,1].transAxes,fontsize=8)
        for ax in axes.flat:
            if ax.axison:
                ax.set(xlabel="Chemical shift (ppm)", ylabel="Intensity (a.u.)")
                ax.axhline(0, color="gray", lw=.5)
        save_figure(fig, output / "acquisition_audit_sheet", dataset,
            f"Acquisition Audit {acquisition['acquisition_id']}", manifests)
        fig, ax = plt.subplots(figsize=(10,4.5), layout="constrained")
        ax.plot(rx,picked.magnitude,label="Input after global baseline")
        ax.plot(rx,picked.baseline,label="Regional polynomial baseline",ls="--")
        for row in result["peaks"]:
            ax.axvline(row["integration_left_ppm"],ls=":",lw=.7)
            ax.axvline(row["integration_right_ppm"],ls=":",lw=.7)
            ax.plot(row["peak_ppm"],row["raw_peak_height"],"o",ms=4)
        ax.set(xlim=(6.5,5),xlabel="Chemical shift (ppm)",ylabel="Intensity (a.u.)")
        ax.legend(fontsize=8)
        save_figure(fig,output/"peak_validation",dataset,"Baseline and Peak Boundaries",manifests)
    write_rows(output / "plot_manifest.csv", manifests)
    return meta


def alternative_phases(result):
    """Use installed nmrglue objectives and scipy fmin; record convergence."""
    from scipy.optimize import fmin
    values = np.asarray(result["unphased"])
    spec = result["spectrum"]
    sign = -1 if not result["args"].direct_phase else 1
    initial = [sign*spec.phase0_deg, sign*spec.phase1_deg]
    peak_width = max(10, int(round(.10 / np.median(np.diff(spec.ppm_axis)))))
    settings = {"xtol": 1e-4, "ftol": 1e-4, "maxiter": 1000, "maxfun": 2000,
                "disp": False, "full_output": True}
    methods = []
    for name in ("acme", "peak_minima"):
        objective = getattr(ng.proc_autophase, "_ps_" + name + "_score")
        arguments = (values,) if name == "acme" else (values,peak_width)
        try:
            phases, score, iterations, evaluations, flag = fmin(objective, initial,
                args=arguments, **settings)
            if not np.all(np.isfinite(phases)) or not np.isfinite(score):
                raise ValueError("non-finite objective or phases")
            comparator = analyze(spec.source, result["args"],
                phase=(phases[0],phases[1],False,name),unphased=values)
            metadata = {"method": name, "objective": objective.__name__,
                "objective_score": float(score), "initial_direct_phases_deg": initial,
                "optimizer": "scipy.optimize.fmin (Nelder-Mead)", "optimizer_settings": settings,
                "peak_width_points": peak_width if name == "peak_minima" else None,
                "phase_bounds": None, "iterations": iterations, "function_evaluations": evaluations,
                "convergence_warnflag": flag, "converged": flag == 0,
                "analysis_role": "validation comparator; not production replacement"}
            methods.append((comparator,metadata))
        except Exception as exc:
            methods.append((None,{"method":name,"error":str(exc),"converged":False}))
    return methods


def quality_metrics(result):
    spec = result["spectrum"]
    mask=(spec.ppm_axis>=5)&(spec.ppm_axis<=6.5)
    real=np.asarray(spec.real)[mask]; imag=np.asarray(spec.imaginary)[mask]
    return {"imaginary_energy_fraction_region": float(np.sum(imag**2)/max(np.sum(real**2+imag**2),1e-30)),
        "negative_area_region": float(np.trapezoid(np.maximum(-real,0),spec.ppm_axis[mask])),
        "positive_area_region": float(np.trapezoid(np.maximum(real,0),spec.ppm_axis[mask]))}


def historical_candidates(acquisition):
    found=[]
    for p in acquisition["historical_result_paths"]:
        path=Path(p)
        if path.name.endswith("results.csv"):
            summaries=[p for p in path.parent.glob("*summary.json") if "target_peak" not in p.name and "statistics" not in p.name]
            for summary_path in summaries:
                summary=json.loads(summary_path.read_text(encoding="utf-8-sig"))
                matching=[r for r in summary.get("records",[]) if r.get("raw_sha256","").lower()==acquisition["raw_sha256"].lower()]
                if not matching:
                    matching=[r for r in summary.get("records",[]) if r.get("file")==Path(acquisition["source_path"]).name]
                if matching:
                    found.append((path,summary_path,summary,matching[0]))
    return found


def reproduce_historical(acquisition, current):
    source=Path(acquisition["source_path"])
    variants=historical_candidates(acquisition)
    records=[]
    for csv_path,summary_path,summary,old_record in variants:
        params=summary.get("parameters",{})
        row={"acquisition_id":acquisition["acquisition_id"],"historical_result_path":str(csv_path),
             "historical_summary_path":str(summary_path),"historical_git_commit":summary.get("git_commit"),
             "historical_phase0":old_record.get("phase0_deg"),"historical_phase1":old_record.get("phase1_deg"),
             "historical_qc_min_snr":params.get("qc_min_snr"),"current_qc_min_snr":current["args"].qc_min_snr}
        try:
            if abs(finite(old_record.get("applied_shift_ppm"),0))>1e-12:
                raise ValueError("Historical reference shift needs full reference replay")
            result=analyze(source,production_args(source,params))
            simple_files=list(csv_path.parent.glob("*peaks_simple.csv"))
            simple=[]
            for p in simple_files:
                simple += [r for r in read_rows(p) if r.get("file")==old_record.get("file")]
            if not simple:
                raise ValueError("Historical target table missing for acquisition")
            old=max(simple,key=lambda r:finite(r.get("snr"),0))
            old_vals={"peak_ppm":finite(old.get("peak_ppm")),"height":finite(old.get("intensity")),
                "area":finite(old.get("integrated_area")),"snr":finite(old.get("snr"))}
            old_detect=bool(finite(old.get("integrated_area"),0)>0 and finite(old.get("snr"),0)>0)
            row.update({"historical_detection":old_detect,"reproduced_detection":result["target"]["detected"],
                        "reproduced_phase0":result["spectrum"].phase0_deg,"reproduced_phase1":result["spectrum"].phase1_deg})
            agrees=old_detect==result["target"]["detected"]
            for metric,old_value in old_vals.items():
                new=result["target"][metric]
                row["historical_"+metric]=old_value; row["reproduced_"+metric]=new
                row["difference_"+metric]=None if old_value is None or new is None else new-old_value
                row["current_"+metric]=current["target"][metric]
                if metric=="peak_ppm" and not old_detect:
                    continue # placeholder center depends on the whole historical series
                tolerance=.00051 if metric=="peak_ppm" else .0051
                agrees=agrees and old_value is not None and new is not None and abs(new-old_value)<=tolerance
            row["reproducibility_status"]="agrees_with_saved_rounding" if agrees else "disagreement"
            row["notes"]="Target table rounding: ppm 0.001; height/area/SNR 0.01. Nondetection center is a cohort placeholder."
        except Exception as exc:
            row.update(reproducibility_status="unresolved",notes=str(exc))
        records.append(row)
    if not records and acquisition["primary_cohort"]:
        # Some original runs only retain the phase-insensitive monitoring result.
        for p in acquisition["historical_result_paths"]:
            path=Path(p)
            if path.name!="time_series.csv":
                continue
            config_path=path.parent/"config_snapshot.json"
            if not config_path.exists():
                continue
            cfg=json.loads(config_path.read_text())
            analysis=cfg["analysis"]
            for old in read_rows(path):
                if old.get("file")!=source.name:
                    continue
                row={"acquisition_id":acquisition["acquisition_id"],"historical_result_path":str(path),
                     "historical_processing_path":"phase-insensitive controller magnitude"}
                try:
                    r=analyze_dx_peak(source,target_ppm=float(old["target_ppm"]),
                        window_ppm=analysis["detection_window_ppm"],line_broadening_hz=analysis.get("line_broadening_hz",.3),
                        min_prominence_snr=analysis["min_prominence_snr"],integration_window_ppm=analysis["integration_window_ppm"])
                    agrees=True
                    for metric in ("peak_ppm","peak_height","peak_area","snr"):
                        value=getattr(r,metric); saved=finite(old.get(metric))
                        row["historical_"+metric]=saved; row["reproduced_"+metric]=value
                        agrees &= saved is not None and np.isclose(value,saved,rtol=1e-8,atol=1e-8)
                    row["reproducibility_status"]="agrees_full_precision" if agrees else "disagreement"
                except NmrProcessingError as exc:
                    row["reproducibility_status"]="reproduced_historical_nondetection" if old.get("error")==str(exc) else "disagreement"
                    row["notes"]=str(exc)
                records.append(row)
                break
            if records:
                break
    if not records:
        records=[{"acquisition_id":acquisition["acquisition_id"],"reproducibility_status":
                  "secondary_no_historical_comparison" if not acquisition["primary_cohort"] else "unresolved",
                  "notes":"No compatible saved numerical target result"}]
    return records


def protected_snapshot(cohort):
    """Hash raw, historical numerical evidence and run logs, excluding validation."""
    paths={Path(p) for a in cohort for p in a["copy_paths"]}
    roots=[ROOT/"results/processed",ROOT/"results/runs",ROOT/"results/nmr_phase_demo_exports",
           ROOT/"results/nmr_processing_inspection",ROOT/"nmr_template"]
    for root in roots:
        if root.exists():
            paths.update(p for p in root.rglob("*") if p.is_file())
    return {str(p):sha256(p) for p in sorted(paths) if p.is_file()}


def verify_snapshot(snapshot):
    rows=[]
    for p,digest in snapshot.items():
        after=sha256(p) if Path(p).is_file() else None
        rows.append({'path':p,'before_sha256':digest,'after_sha256':after,'unchanged':after==digest})
    return rows


def replay_target_series(results, config=None):
    config=config or pipeline()._target_peak_config([])
    return build_target_peak_analysis([spectrum_stat(r,i) for i,r in enumerate(results)],config)


def discover_stop_evidence(cohort, output):
    """Search stored run sequences, configs, journals, and completion outputs.

    Hash mapping distinguishes repeated reference inputs from a genuine sequence.
    Completion reports are retrospective decision support unless controller logs
    establish that they actually caused a stop.
    """
    from chemyx_lab.workflows.si6_automated_nmr import plateau_reached
    output=Path(output)
    hashes={a["raw_sha256"]:a for a in cohort}
    copy_map={str(Path(p).resolve()).casefold():a for a in cohort for p in a["copy_paths"]}
    evidence=[]; sequences=[]; journals=[]; scanned=[]
    candidates=sorted((ROOT/"results").rglob("time_series.csv"))
    for table in candidates:
        if "NMR_validation_100226" in table.parts or "pytest" in str(table) or "test_tmp" in str(table):
            continue
        run=table.parent; rows=read_rows(table)
        matched=[]
        for i,row in enumerate(rows):
            source=run/"raw_nmr"/row.get("file","")
            acq=copy_map.get(str(source.resolve()).casefold())
            if not acq and source.is_file():
                acq=hashes.get(sha256(source))
            if acq:
                matched.append((i,row,acq))
        if not matched:
            continue
        scanned.append(str(table))
        snapshot=run/"config_snapshot.json"
        cfg=json.loads(snapshot.read_text(encoding="utf-8-sig")) if snapshot.exists() else {}
        analysis=cfg.get("analysis")
        stage_rows={}
        unique_ids={a["acquisition_id"] for _,_,a in matched}
        first_true=None; saved_true=None
        for index,row,acq in matched:
            stage=row.get("stage","")
            normalized=dict(row,peak_clear=truth(row.get("peak_clear")))
            stage_rows.setdefault(stage,[]).append(normalized)
            replay=plateau_reached(stage_rows[stage],analysis) if analysis else None
            saved=truth(row.get("plateau"))
            if replay and first_true is None: first_true=index
            if saved and saved_true is None: saved_true=index
            sequences.append({"run":str(run),"historical_table":str(table),"row_index":index,
                "stage":stage,"acquisition_id":acq["acquisition_id"],
                "actual_metadata_timestamp":acq["timestamp"],"timestamp_source":acq["timestamp_source"],
                "historical_run_acquired_at":row.get("acquired_at"),"peak_area":row.get("peak_area"),
                "growth_percent":row.get("growth_percent"),"historical_plateau":saved,
                "replayed_plateau":replay,"agreement":saved==replay if replay is not None else None,
                "unique_acquisitions_in_run":len(unique_ids),"raw_sha256":acq["raw_sha256"]})
        journal=run/"operation_journal.jsonl"
        events=[]
        if journal.exists():
            scanned.append(str(journal))
            for line in journal.read_text(encoding="utf-8-sig").splitlines():
                try: event=json.loads(line)
                except json.JSONDecodeError:
                    journals.append({"path":str(journal),"event_type":"malformed_json_line","line":line[:200]})
                    continue
                if event.get("event_type") in {"monitoring_stage_started","plateau_detection","terminal","stage_completed"}:
                    events.append(event)
                    journals.append({"path":str(journal),**event})
        stop_events=[e for e in events if e.get("event_type")=="plateau_detection" and
            (e.get("result_classification")=="stage_completed" or truth(e.get("plateau_stopping_enabled")))]
        starts=[e for e in events if e.get("event_type")=="monitoring_stage_started"]
        stopping_enabled=any(truth(e.get("plateau_stopping_enabled")) for e in starts)
        mock_context=any("mock" in str(run).lower() for _ in [0]) or any(
            str(e.get("result_classification","")).startswith("mock") for e in events)
        repeated=len(unique_ids)<len(matched)
        evidence.append({"evidence_type":"controller_sequence","run":str(run),"table":str(table),
            "config_snapshot":str(snapshot) if snapshot.exists() else None,"sequence_rows":len(matched),
            "unique_acquisitions":len(unique_ids),"repeated_raw_input":repeated,"mock_context":mock_context,
            "plateau_stopping_enabled":stopping_enabled,"first_saved_plateau_row":saved_true,
            "first_replayed_plateau_row":first_true,"recorded_plateau_stop_events":len(stop_events),
            "classification":"reused_input_not_independent_acquisitions" if repeated else
                "explicit_mock_context" if mock_context else "recorded_plateau_stop" if stop_events else
                "stopping_disabled" if starts and not stopping_enabled else "no_confirmed_plateau_stop",
            "actual_stop_verified":bool(stop_events and not repeated and not mock_context)})
    # Recover every completion report with the same actual acquisition dates.
    dates={a["acquisition_date"] for a in cohort}
    completion_reports=[]
    for p in sorted((ROOT/"results").rglob("*target_peak_summary.json")):
        if "NMR_validation_100226" in p.parts or "pytest" in str(p): continue
        data=json.loads(p.read_text(encoding="utf-8-sig")); decision=data.get("completion",{})
        stamp=decision.get("completion_timestamp") or ""
        if stamp[:10] not in dates or not decision.get("complete"): continue
        row={"evidence_type":"retrospective_completion_report","path":str(p),
            "completion_timestamp":stamp,"completion_index":decision.get("completion_index"),
            "status":decision.get("status"),"evidence_level":decision.get("evidence_level"),
            "thresholds":decision.get("thresholds"),"warnings":decision.get("quality_warnings"),
            "actual_controller_stop_verified":False,
            "notes":"Saved completion analysis alone does not demonstrate physical early stop"}
        completion_reports.append(row); scanned.append(str(p))
    write_rows(output/"historical_controller_runs.csv",evidence)
    write_rows(output/"historical_sequence_replay.csv",sequences)
    write_json(output/"historical_journal_evidence.json",journals)
    write_json(output/"historical_completion_reports.json",completion_reports)
    write_json(output/"search_coverage.json",{"searched_root":str(ROOT/"results"),
        "matched_paths":scanned,"cohort_hash_count":len(hashes),
        "criteria":"time_series rows joined to raw_nmr bytes; plateau/terminal journals; all target_peak_summary completion events"})
    return {"controller_runs":evidence,"sequences":sequences,"completion_reports":completion_reports}


def save_manual_checkpoint(acquisition_dir, *, p0_deg, p1_deg, pivot_ppm=None,
                           reviewer=None, notes="", role="manual_reference", pivot_fraction=None,
                           checkpoint_root=None):
    """Append-only human review or explicitly labelled phase reference checkpoint."""
    directory=Path(acquisition_dir).resolve()
    automated_dir=directory/"automated"
    meta=json.loads((automated_dir/"processing_metadata.json").read_text())
    source=Path(meta["source_path"])
    if sha256(source)!=meta["raw_sha256"]:
        raise ValueError("Raw input changed since automated audit")
    if not all(math.isfinite(float(v)) for v in (p0_deg,p1_deg)):
        raise ValueError("Phase values must be finite")
    args=production_args(source,meta["parameters"])
    automated=analyze(source,args)
    axis=automated["spectrum"].ppm_axis
    pivot=float(axis[0]) if pivot_ppm is None else float(pivot_ppm)
    if not math.isfinite(pivot) or pivot<axis.min() or pivot>axis.max():
        raise ValueError("Manual pivot must lie on acquired ppm axis")
    # Canonical API uses exact nmrglue k/N. GUI supplies its existing k/(N-1)
    # fraction explicitly so saved values reproduce the actual displayed GUI.
    fraction=float(np.interp(pivot,axis,np.arange(len(axis))/len(axis))) if pivot_fraction is None else float(pivot_fraction)
    if not 0<=fraction<=1 or not math.isfinite(fraction):
        raise ValueError("Pivot fraction must be finite within [0,1]")
    effective=float(p0_deg)-float(p1_deg)*fraction
    manual=analyze(source,args,phase=(effective,float(p1_deg),True,"manual"),unphased=automated["unphased"])
    review_root=directory/"manual_reviews" if checkpoint_root is None else Path(checkpoint_root).resolve()
    if checkpoint_root is not None and not review_root.is_relative_to(directory):
        raise ValueError("Checkpoint output must stay inside the acquisition folder")
    if review_root.is_relative_to(automated_dir):
        raise ValueError("Checkpoint output must not lie inside automatic evidence")
    review_root.mkdir(parents=True,exist_ok=True)
    for number in range(1,100000):
        output=review_root/f"review_{number:03d}"
        try: output.mkdir()
        except FileExistsError: continue
        break
    else: raise RuntimeError("Manual review checkpoint limit exceeded")
    acquisition={"dataset_display_name":meta["dataset_display_name"],"acquisition_id":meta["acquisition_id"],
        "timestamp":meta["actual_acquisition_timestamp"],"raw_sha256":meta["raw_sha256"],"primary_cohort":meta["primary_cohort"]}
    export_result(manual,output,acquisition,provenance())
    (output/"automatically_phased_spectrum.csv").rename(output/"manually_phased_spectrum.csv")
    checkpoint={"schema":"chemyx.nmr-manual-checkpoint.v1","analysis_role":role,
        "reviewer":reviewer,"notes":notes,"created_at_utc":provenance()["created_at_utc"],
        "source_path":str(source),"raw_sha256":meta["raw_sha256"],"starting_automated_phase":meta["phase"],
        "manual_phase":{"p0_deg":float(p0_deg),"p1_deg":float(p1_deg),"pivot_ppm":pivot,
                        "pivot_fraction":fraction,"effective_index_zero_p0_deg":effective,"inverse":True},
        "phase_parameter_convention":"manual inverse p0 at pivot; effective p0=p0-p1*pivot_fraction",
        "downstream_parameters_unchanged":True,
        "early_stop_implication":"single spectrum does not establish a stop; rerun sequence comparisons"}
    write_json(output/"checkpoint.json",checkpoint)
    difference=delta_table(automated,manual)
    difference.append({"metric":"pivot_ppm","automated":meta["phase"]["pivot_ppm"],
        "comparison":pivot,"absolute_difference":pivot-meta["phase"]["pivot_ppm"]})
    write_rows(output/"automated_vs_manual.csv",difference)
    # The immutable automated subtree is never a checkpoint output destination.
    return output


def completion_figures(analysis, results, output, dataset, method, manifest):
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(15,4.5),layout="constrained")
    times=[r["elapsed_time_hours"] for r in analysis.measurements]
    areas=[r["area"] for r in analysis.measurements]
    for ax,x,label in ((axes[0],list(range(1,len(areas)+1)),"Acquisition number"),
                       (axes[1],times,"Elapsed hours from JCAMP LONG DATE")):
        ax.plot(x,areas,"o-",label=method)
        idx=analysis.completion.completion_index
        if idx is not None:
            ax.axvline(x[idx],color="red",ls="--",label="First completion criterion")
        ax.set(xlabel=label,ylabel="Fixed-window area (a.u.·ppm)")
        ax.legend(fontsize=8)
    trace=analysis.decision_trace
    slopes=[finite(r.get("recent_slope_per_hour"),np.nan) for r in trace]
    axes[2].plot(times,slopes,"o-",label="Recorded slope (latched after criterion)")
    threshold=analysis.config.completion.absolute_slope_threshold_per_hour
    axes[2].axhspan(-threshold,threshold,alpha=.15,color="green",label="Absolute slope band")
    axes[2].set(title="Completion decision trace",xlabel="Elapsed hours (metadata)",ylabel="Recorded area slope (a.u.·ppm/h)")
    axes[2].legend(fontsize=8)
    save_figure(fig,output/f"{method}_decision_metrics",dataset,
        f"Completion Replay ({method}); decision support",manifest)
    idx=analysis.completion.completion_index
    if idx is not None:
        fig,axes=plt.subplots(1,3,figsize=(13,4),layout="constrained",sharey=True)
        local_values=[]
        for ax,offset,label in zip(axes,(-1,0,1),("Before criterion","Criterion reached","Following acquisition")):
            i=idx+offset
            if not 0<=i<len(results):
                ax.text(.5,.5,f"{label}\nNo acquisition available",ha="center",va="center",transform=ax.transAxes)
                ax.axis('off')
                continue
            result=results[i]
            ax.plot(result["spectrum"].ppm_axis,result["quantitative"],lw=.8)
            mask=(result['spectrum'].ppm_axis>=5.65)&(result['spectrum'].ppm_axis<=5.95)
            local_values.extend(np.asarray(result['quantitative'])[mask])
            ax.set(title=f"{label}\n{analysis.measurements[i]['timestamp']}",xlim=(5.95,5.65),
                   xlabel="Chemical shift (ppm)",ylabel="Intensity (a.u.)")
        if local_values:
            low,high=min(local_values),max(local_values)
            padding=.08*max(high-low,1)
            axes[0].set_ylim(low-padding,high+padding)
        save_figure(fig,output/f"{method}_criterion_spectra",dataset,
                    "Spectra Surrounding Completion Criterion",manifest)


def method_row(acquisition,result,method,**extra):
    spec=result["spectrum"]
    return {"acquisition_id":acquisition["acquisition_id"],"timestamp":acquisition["timestamp"],
        "primary_cohort":acquisition["primary_cohort"],"method":method,
        "p0_deg":spec.phase0_deg,"p1_deg":spec.phase1_deg,
        "phase_direction":"direct" if method in {"acme","peak_minima"} else "inverse",
        "pivot_fraction":0,"pivot_ppm":float(spec.ppm_axis[0]),**result["target"],
        **quality_metrics(result),**extra}


def build_package(output=DEFAULT_OUTPUT, inventory=DEFAULT_INVENTORY, *, acquisition_id=None,
                  alternatives=True, sensitivity=True):
    output=Path(output).resolve()
    if output != DEFAULT_OUTPUT.resolve() and not output.is_relative_to(DEFAULT_OUTPUT.resolve()):
        raise ValueError("Validation output must stay inside results/NMR_validation_100226")
    for name in ("documentation","acquisitions","phase_method_comparison","early_stop_validation",
                 "summary_plots","tables","logs"):
        (output/name).mkdir(parents=True,exist_ok=True)
    cohort=load_cohort(inventory)
    if acquisition_id:
        cohort=[a for a in cohort if a["acquisition_id"]==acquisition_id]
        if not cohort: raise ValueError("Unknown acquisition ID")
    before_path=output/"logs/historical_hashes_before.json"
    snapshot=json.loads(before_path.read_text()) if before_path.exists() else protected_snapshot(cohort)
    write_json(output/"logs/historical_hashes_before.json",snapshot)
    prov=provenance()
    prov["validation_code_sha256"]=sha256(Path(__file__))
    prov["inventory_sha256"]=sha256(inventory)
    write_json(output/"logs/software_provenance.json",prov)
    # Preserve a copy of effective config without modifying production configuration.
    write_json(output/"logs/effective_production_arguments.json",vars(production_args(cohort[0]["source_path"])))
    for p in (ROOT/"configs/nmr/analysis.yaml",ROOT/"configs/nmr/analysis.local.yaml"):
        if p.exists():
            (output/"logs"/p.name).write_bytes(p.read_bytes())
    write_rows(output/"acquisition_manifest.csv",[{**a,"historical_result_paths":";".join(a["historical_result_paths"]),
        "copy_paths":";".join(a["copy_paths"])} for a in cohort])
    write_json(output/"acquisition_manifest.json",cohort)
    results={}; method_results={}; comparisons=[]; method_rows=[]; failed=[]; phase_fields=[]
    for index,a in enumerate(cohort,1):
        ident=a["acquisition_id"]; source=Path(a["source_path"])
        print(f"[{index}/{len(cohort)}] {ident}: production + historical + phase comparators",flush=True)
        try:
            result=analyze(source)
            results[ident]=result
            phase_fields.append({"acquisition_id":ident,"raw_sha256":a["raw_sha256"],
                **phase_field_reconciliation(result["spectrum"].metadata,result["unphased"])})
            export_result(result,output/"acquisitions"/ident/"automated",a,prov)
            comparisons.extend(reproduce_historical(a,result))
            candidates=historical_candidates(a)
            if candidates:
                _,historical_summary,saved,old_record=candidates[0]
                historical=analyze(source,production_args(source,saved['parameters']))
                hist_dir=output/'acquisitions'/ident/'historical_parameter_reconstruction'
                export_result(historical,hist_dir,a,prov,figures=False)
                write_json(hist_dir/'historical_context.json',{
                    'historical_summary_path':str(historical_summary),
                    'historical_summary_sha256':sha256(historical_summary),
                    'historical_code_commit':saved.get('git_commit'),
                    'reconstruction_code_commit':prov['git_commit'],
                    'role':'current code with saved historical parameters; not an independently stored historical frequency-domain spectrum',
                    'historical_export_csv_enabled':saved['parameters'].get('export_csv'),
                    'historical_record':old_record})
            method_rows.append(method_row(a,result,"production_stored"))
            method_results[ident]={"production_stored":result}
            for comparator,metadata in alternative_phases(result) if alternatives else []:
                name=metadata["method"]
                if comparator is None:
                    method_rows.append({"acquisition_id":ident,**metadata}); continue
                method_results[ident][name]=comparator
                compare_dir=output/"acquisitions"/ident/"alternative_methods"/name
                export_result(comparator,compare_dir,a,prov,csv_arrays=False,figures=False)
                write_json(compare_dir/"optimizer.json",metadata)
                write_rows(compare_dir/"production_comparison.csv",delta_table(result,comparator))
                method_rows.append(method_row(a,comparator,name,**{k:v for k,v in metadata.items() if k!="method"}))
            # Machine-readable starting phase state: not a completed human review.
            s=result["spectrum"]
            write_json(output/"acquisitions"/ident/"manual_review_start.json",{
                "status":"awaiting_manual_expert_review","source_path":str(source),"raw_sha256":a["raw_sha256"],
                "starting_p0_deg":s.phase0_deg,"starting_p1_deg":s.phase1_deg,
                "starting_pivot_ppm":float(s.ppm_axis[0]),"starting_pivot_fraction":0,
                "automated_directory":str(output/"acquisitions"/ident/"automated")})
        except Exception as exc:
            results.pop(ident,None)
            method_results.pop(ident,None)
            failed.append({"acquisition_id":ident,"error":str(exc)})
            print("ERROR:",repr(exc),flush=True)
    write_rows(output/"tables/historical_variant_comparison.csv",comparisons)
    primary_comparisons=[]
    for a in cohort:
        rows=[r for r in comparisons if r['acquisition_id']==a['acquisition_id']]
        if rows:
            best=sorted(rows,key=lambda r:('mock' in r.get('historical_result_path','').lower(),
                r['reproducibility_status'] in {'disagreement','unresolved'},
                r.get('historical_result_path','')))[0]
            primary_comparisons.append({**best,'historical_variant_count':len(rows)})
    write_rows(output/"tables/historical_reproduction.csv",primary_comparisons)
    write_rows(output/"tables/phase_field_reconciliation.csv",phase_fields)
    write_rows(output/"tables/historical_disagreements.csv",[r for r in comparisons if r["reproducibility_status"] in {"disagreement","unresolved"}],
        columns=list(dict.fromkeys(k for r in comparisons for k in r)))
    write_rows(output/"tables/processing_failures.csv",failed,columns=["acquisition_id","error"])
    write_rows(output/"phase_method_comparison/phase_method_comparison.csv",method_rows)
    write_rows(output/"tables/phase_method_disagreements.csv",[
        r for r in method_rows if r.get("method") in {"acme","peak_minima"} and r.get("acquisition_id") in results and
        (r.get("detected")!=results[r["acquisition_id"]]["target"]["detected"] or
         (results[r["acquisition_id"]]["target"]["area"]>0 and abs(r.get("area",0)/results[r["acquisition_id"]]["target"]["area"]-1)>.10))],
        columns=list(dict.fromkeys(k for r in method_rows for k in r)))
    stop_evidence=discover_stop_evidence(cohort,output/"early_stop_validation")
    completion_rows=[]; manifests=[]; group_analyses={}
    for date in sorted({a["acquisition_date"] for a in cohort}):
        group=[a for a in cohort if a["acquisition_date"]==date and a["acquisition_id"] in results]
        for name in ("production_stored","acme","peak_minima"):
            selected=[method_results[a["acquisition_id"]][name] for a in group if name in method_results[a["acquisition_id"]]]
            if not selected or len(selected)!=len(group): continue
            analysis=replay_target_series(selected)
            group_analyses[(date,name)]=analysis
            out=output/"early_stop_validation"/date/name
            for table,(columns,rows) in analysis.tables.items(): write_rows(out/table,rows,columns)
            write_json(out/"completion.json",analysis.completion.as_dict())
            write_json(out/"sequence_provenance.json",{
                "analysis_role":"current-config retrospective replay by acquisition day; not proof of controller stop",
                "acquisition_ids":[a["acquisition_id"] for a in group],"method":name,
                "timestamp_source":"JCAMP metadata only","parameters":asdict(analysis.config)})
            completion_rows.append({"dataset":date,"method":name,
                "complete":analysis.completion.complete,"first_completion_index":analysis.completion.completion_index,
                "first_completion_timestamp":analysis.completion.completion_timestamp,
                "status":analysis.completion.status,"reason":analysis.completion.reason,
                "evidence_level":analysis.completion.evidence_level,
                "warnings":"; ".join(analysis.completion.quality_warnings),"controller_stop_verified":False})
            completion_figures(analysis,selected,out,date,name,manifests)
    write_rows(output/"early_stop_validation/phase_method_completion_comparison.csv",completion_rows)
    # Import existing saved manual/exploratory settings without pretending a new human review took place.
    imported=[]
    for path in sorted((ROOT/"results/nmr_phase_demo_exports").glob("*settings*.json")):
        payload=json.loads(path.read_text())
        source=Path(payload.get("source_file",""))
        if not source.is_file(): continue
        digest=sha256(source)
        a=next((a for a in cohort if a["raw_sha256"]==digest),None)
        if not a or a["acquisition_id"] not in results: continue
        marker=output/"logs"/(path.stem+"_imported.json")
        if marker.exists():
            imported.append(json.loads(marker.read_text())); continue
        phase=payload["phase"]
        pivot=float(phase["pivot_ppm"])
        axis=results[a["acquisition_id"]]["spectrum"].ppm_axis
        pivot=float(np.clip(pivot,axis.min(),axis.max()))
        # Phase2 effective_p0 uses the same linspace k/(N-1) mapping as Phase4.
        fraction=float(np.interp(pivot,axis,np.linspace(0,1,len(axis))))
        checkpoint=save_manual_checkpoint(output/"acquisitions"/a["acquisition_id"],
            p0_deg=phase["p0_deg"],p1_deg=phase["p1_deg"],pivot_ppm=pivot,pivot_fraction=fraction,
            role="historical_exploratory_phase_reference",reviewer=None,
            notes=f"Imported existing exploratory-only settings: {path}; original reviewer not recorded; retained downstream current production parameters")
        row={"acquisition_id":a["acquisition_id"],"checkpoint":str(checkpoint),
             "source_settings":str(path),"source_settings_sha256":sha256(path),
             "role":"historical_exploratory_phase_reference","reviewer":None}
        write_json(marker,row); imported.append(row)
    manual_rows=[]
    for a in cohort:
        review_root=output/"acquisitions"/a["acquisition_id"] /"manual_reviews"
        if not review_root.exists(): continue
        for checkpoint in sorted(review_root.glob("review_*/checkpoint.json")):
            meta=json.loads(checkpoint.read_text()); target=json.loads((checkpoint.parent/"target_result.json").read_text())
            manual_rows.append({"acquisition_id":a["acquisition_id"],"checkpoint":str(checkpoint.parent),
                "analysis_role":meta["analysis_role"],"reviewer":meta["reviewer"],**target})
            # Replay the whole day's same production decision-support pipeline with this acquisition replaced.
            date=a["acquisition_date"]; group=[x for x in cohort if x["acquisition_date"]==date and x["acquisition_id"] in results]
            mp=meta["manual_phase"]
            manual=analyze(a["source_path"],results[a["acquisition_id"]]["args"],
                phase=(mp["effective_index_zero_p0_deg"],mp["p1_deg"],True,"manual"))
            seq=[manual if x["acquisition_id"]==a["acquisition_id"] else results[x["acquisition_id"]] for x in group]
            decision=replay_target_series(seq).completion
            baseline=group_analyses[(date,"production_stored")].completion
            write_json(checkpoint.parent/"sequence_decision_comparison.json",{
                "dataset":date,"automated":baseline.as_dict(),"manual_reference":decision.as_dict(),
                "boolean_changed":baseline.complete!=decision.complete,
                "first_completion_index_changed":baseline.completion_index!=decision.completion_index,
                "actual_controller_stop_verified":False})
    write_rows(output/"tables/automated_vs_manual_references.csv",manual_rows)
    sensitivity_rows=[]; sensitivity_decisions=[]
    if sensitivity:
        # Full June9 sequence; other dates get one representative, not fictitious endpoints.
        representatives=[next(a for a in cohort if a["acquisition_date"]==date and a["acquisition_id"] in results)
            for date in sorted({a["acquisition_date"] for a in cohort}) if any(a["acquisition_date"]==date and a["acquisition_id"] in results for a in cohort)]
        selected={a["acquisition_id"]:a for a in representatives}
        for a in cohort:
            if a["acquisition_date"]=="2026-06-09" and a["acquisition_id"] in results: selected[a["acquisition_id"]]=a
        june9=[a for a in cohort if a["acquisition_date"]=="2026-06-09" and a["acquisition_id"] in results]
        for dp0 in (-10,-5,-2,0,2,5,10):
            for dp1 in (-20,-10,0,10,20):
                print(f"Sensitivity: P0 at target {dp0:+g} deg; P1 {dp1:+g} deg",flush=True)
                changed={}
                for ident,a in selected.items():
                    result=results[ident]; s=result["spectrum"]; pivot=result["args"].simple_target_ppm
                    fraction=float(np.interp(pivot,s.ppm_axis,np.arange(s.processed_points)/s.processed_points))
                    p0=s.phase0_deg+dp0-dp1*fraction; p1=s.phase1_deg+dp1
                    altered=analyze(s.source,result["args"],phase=(p0,p1,True,"manual"),unphased=result["unphased"])
                    changed[ident]=altered
                    sensitivity_rows.append(method_row(a,altered,"phase_perturbation",
                        delta_p0_at_target_deg=dp0,delta_p1_deg=dp1,pivot_ppm=pivot,
                        target_phase_delta_deg=dp0,phase_span_across_target_window_deg=dp1*.2/(float(s.ppm_axis[-1]-s.ppm_axis[0])),
                        detection_changed=altered["target"]["detected"]!=result["target"]["detected"]))
                if june9:
                    systematic=replay_target_series([changed[a["acquisition_id"]] for a in june9]).completion
                    original=group_analyses[("2026-06-09","production_stored")].completion
                    sensitivity_decisions.append({"dataset":"2026-06-09","scope":"same perturbation on entire sequence",
                        "delta_p0_at_target_deg":dp0,"delta_p1_deg":dp1,"complete":systematic.complete,
                        "first_completion_index":systematic.completion_index,"status":systematic.status,
                        "boolean_changed":systematic.complete!=original.complete,
                        "first_index_changed":systematic.completion_index!=original.completion_index})
                    if original.completion_index is not None:
                        ident=june9[original.completion_index]["acquisition_id"]
                        one=replay_target_series([changed[a["acquisition_id"]] if a["acquisition_id"]==ident else results[a["acquisition_id"]] for a in june9]).completion
                        sensitivity_decisions.append({"dataset":"2026-06-09","scope":"triggering acquisition only",
                            "delta_p0_at_target_deg":dp0,"delta_p1_deg":dp1,"complete":one.complete,
                            "first_completion_index":one.completion_index,"status":one.status,
                            "boolean_changed":one.complete!=original.complete,
                            "first_index_changed":one.completion_index!=original.completion_index})
        write_rows(output/"tables/phase_sensitivity.csv",sensitivity_rows)
        write_rows(output/"early_stop_validation/phase_sensitivity_decisions.csv",sensitivity_decisions)
        write_json(output/"tables/sensitivity_design.json",{
            "p0_deg":[-10,-5,-2,0,2,5,10],"p1_deg":[-20,-10,0,10,20],
            "pivot":"target 5.8 ppm; exact k/N fraction", "rationale":
            "P1 is a phase ramp across the approximately 20 ppm sweep. +/-20 degrees changes phase only approximately +/-0.1 degree over a 0.2 ppm target window about the target pivot. P0 varies local phase by +/-2,5,10 degrees.",
            "scope":"June9 full sequence, plus one representative for each remaining date; systematic and single-trigger errors distinguished",
            "scientific_limit":"explored grid is a sensitivity experiment, not a calibrated uncertainty distribution"})
    render_cohort_summary(cohort,results,method_rows,sensitivity_rows,sensitivity_decisions,output,manifests)
    write_rows(output/"tables/figure_manifest.csv",manifests)
    integrity=verify_snapshot(snapshot)
    write_rows(output/"logs/historical_integrity.csv",integrity)
    if not all(r["unchanged"] for r in integrity): raise RuntimeError("Historical integrity check failed")
    stats={"cohort_total":len(cohort),"primary_total":sum(a["primary_cohort"] for a in cohort),
        "secondary_total":sum(not a["primary_cohort"] for a in cohort),"processed_successfully":len(results),
        "historical_comparison_status_counts":{s:sum(r["reproducibility_status"]==s for r in primary_comparisons) for s in sorted({r["reproducibility_status"] for r in primary_comparisons})},
        "historical_variant_record_count":len(comparisons),
        "phase_methods":sorted({r["method"] for r in method_rows}),"manual_reference_checkpoints":len(manual_rows),
        "human_expert_checkpoints":sum(bool(r["reviewer"]) and r["analysis_role"]=="manual_reference" for r in manual_rows),
        "controller_runs_searched":len(stop_evidence["controller_runs"]),
        "actual_unique_acquisition_stop_events_verified":sum(r["actual_stop_verified"] for r in stop_evidence["controller_runs"]),
        "retrospective_completion_reports":len(stop_evidence["completion_reports"]),
        "method_detection_disagreements":{name:sum(r.get("detected")!=results[r["acquisition_id"]]["target"]["detected"] for r in method_rows if r.get("method")==name and r.get("acquisition_id") in results) for name in ("acme","peak_minima")},
        "sensitivity_evaluations":len(sensitivity_rows),"sensitivity_decision_replays":len(sensitivity_decisions),
        "historical_files_verified_unchanged":len(integrity),"processing_failures":failed}
    write_json(output/"tables/cohort_summary.json",stats)
    write_summary(output,stats,primary_comparisons,method_rows,completion_rows,sensitivity_decisions)
    return stats


def render_cohort_summary(cohort,results,method_rows,sensitivity_rows,sensitivity_decisions,output,manifest):
    import matplotlib.pyplot as plt
    ids=[a["acquisition_id"] for a in cohort if a["acquisition_id"] in results]
    fig,axes=plt.subplots(2,1,figsize=(14,8),layout="constrained",sharex=True)
    for name in ("production_stored","acme","peak_minima"):
        rows={r["acquisition_id"]:r for r in method_rows if r["method"]==name}
        axes[0].plot(range(len(ids)),[rows.get(i,{}).get("area",np.nan) for i in ids],"o-",ms=3,label=name)
        axes[1].plot(range(len(ids)),[rows.get(i,{}).get("snr",np.nan) for i in ids],"o-",ms=3,label=name)
    axes[0].set(ylabel="Target peak positive area (a.u.·ppm)")
    axes[1].set(ylabel="Height S/N",xlabel="Distinct acquisition (chronological; labels use JCAMP timestamps)")
    axes[1].set_xticks(range(len(ids)),[i[:15] for i in ids],rotation=70,fontsize=7)
    for ax in axes: ax.legend(fontsize=8); ax.grid(alpha=.2)
    dataset="; ".join(sorted({a["acquisition_date"] for a in cohort}))
    save_figure(fig,output/"summary_plots/phase_method_peak_comparison",dataset,"Distinct-Acquisition Phase Comparison",manifest)
    if sensitivity_decisions:
        fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout="constrained")
        p0=[-10,-5,-2,0,2,5,10];p1=[-20,-10,0,10,20]
        for ax,scope in zip(axes,("same perturbation on entire sequence","triggering acquisition only")):
            rows=[r for r in sensitivity_decisions if r["scope"]==scope]
            matrix=np.full((len(p1),len(p0)),np.nan)
            for r in rows: matrix[p1.index(r["delta_p1_deg"]),p0.index(r["delta_p0_at_target_deg"])]=float(r["complete"])
            im=ax.imshow(matrix,origin="lower",aspect="auto",vmin=0,vmax=1,cmap="RdYlGn")
            ax.set_xticks(range(len(p0)),p0); ax.set_yticks(range(len(p1)),p1)
            ax.set(title=scope,xlabel="P0 change at 5.8 ppm (degrees)",ylabel="P1 ramp change (degrees)")
        fig.colorbar(im,ax=axes,label="Completion criterion: 0 false / 1 true",ticks=[0,1])
        save_figure(fig,output/"summary_plots/completion_phase_sensitivity","2026-06-09",
            "Retrospective Completion Sensitivity",manifest)


def write_summary(output,stats,historical,methods,completions,sensitivity):
    primary_ok=sum(r["reproducibility_status"].startswith("agrees") or r["reproducibility_status"]=="reproduced_historical_nondetection" for r in historical)
    lines=["# NMR validation summary", "", "## 1. Purpose", "",
        "All 27 JCAMP-DX exports contain raw complex time-domain FID tables, not already processed frequency-domain spectra. Phase fields accompany the raw data. This package traces decoding, FFT, metadata phase, baseline, peak measurements and decisions. Historical data and scientific algorithms were preserved.",
        "", "## 2. Dataset", "",f"{stats['cohort_total']} unique SHA-256 acquisitions; {stats['primary_total']} primary historical comparisons and {stats['secondary_total']} secondary exploratory spectra. See acquisition_manifest.csv.",
        "", "## 3. How phase correction works", "",
        "A complex rotation mixes real and imaginary components to change absorption/dispersive appearance. The magnitude is invariant. Before/after real and imaginary arrays and plots are saved for each acquisition.",
        "", "## 4. Current automated phase algorithm", "",
        "Current configured method is stored: P0/P1 are read from $PHC0/$PHC1, not optimized. nmrglue inverse rotation uses exp(-i*pi/180*(P0+P1*k/N)). Pivot is index zero; the vendor $PIVOT field is not separately applied. Missing stored phases default to zero. See PHASE_CORRECTION_GUIDE.md.",
        "All 27 files satisfy PHC0=-PHASECORRECTION_P0+PHASECORRECTION_P1/2 and PHC1=-PHASECORRECTION_P1. For (-20,+79), inverse (59.5,-79) applies -59.5+79*k/N=-20+79*(k/N-1/2) degrees. Production reads the exported PHC fields directly; the vendor semantic origin, PIVOT and PH_mod meanings remain unverified. See documentation/RAW_FID_PHASE_FIELDS.md.",
        "", "## 5. Before/after evidence", "",
        "Each automated folder contains 17-digit numerical CSVs, compressed lossless complex arrays, metadata, phase parameters, baseline/peak tables, and acquisition audit sheets. Phasing changes the real signal; baseline correction is a separate subsequent operation.",
        "", "## 6. Manual validation", "",
        f"{stats['manual_reference_checkpoints']} saved exploratory reference checkpoints; {stats['human_expert_checkpoints']} identified human expert reviews. Existing September phase2 settings were imported with their original settings path/hash and an explicit exploratory label. They are not ground truth. A scientist must still review the cohort. GUI and CLI support append-only review_001, review_002, etc., starting from exact production phases and replaying the same downstream processor.",
        "", "## 7. Alternative phase algorithms", "",
        f"Installed nmrglue 0.11 ACME and peak_minima objectives with scipy Nelder-Mead were evaluated. Target detection disagreements with current production: {stats['method_detection_disagreements']}. Full individual results, convergence flags, objective values and area differences are retained. No method has been promoted to production. Material-difference screening uses >10% target area change or any detection disagreement; it is a review screen, not a scientific acceptance tolerance.",
        "", "## 8. Peak-analysis validation", "",
        f"Successfully processed {stats['processed_successfully']}/{stats['cohort_total']}. Historical reproduction status counts: {stats['historical_comparison_status_counts']}. {primary_ok} historical comparison records agreed under saved parameters or reproduced saved nondetection. Current-config and historical-config analyses remain separate: historical June QC minimum S/N can differ from current configuration. See historical_reproduction.csv and historical_disagreements.csv.",
        "", "## 9. Early-stop validation", "",
        f"Examined {stats['controller_runs_searched']} controller run tables joined by raw hash plus their logs/configs. Verified actual stop events with independent cohort acquisitions: {stats['actual_unique_acquisition_stop_events_verified']}. Repeated reference spectra in later run folders are explicitly rejected as independent timepoints. {stats['retrospective_completion_reports']} saved retrospective completion reports were recovered. A retrospective criterion is not proof that an instrument stopped.",
        "", "Current-config completion replays:", "",
        "| Dataset | Method | Complete | First index (zero-based) | Status |", "|---|---|---|---|---|"]
    lines += [f"| {r['dataset']} | {r['method']} | {r['complete']} | {r['first_completion_index']} | {r['status']} |" for r in completions]
    lines += ["", "## 10. Robustness", "",f"{stats['sensitivity_evaluations']} phase perturbation evaluations and {stats['sensitivity_decision_replays']} decision sequence replays. Perturbations are centered at the target: P0 ±2/5/10 degrees, P1 ±10/20 degrees across the full sweep. These grids are sensitivity experiments, not measured phase uncertainty."]
    for scope in sorted({r["scope"] for r in sensitivity}):
        rows=[r for r in sensitivity if r["scope"]==scope]
        lines.append(f"{scope}: {sum(r['boolean_changed'] for r in rows)}/{len(rows)} Boolean changes, {sum(r['first_index_changed'] for r in rows)}/{len(rows)} first-criterion-index changes.")
    lines += ["", "## 11. Disagreements or limitations", "",
        "Human expert phasing is pending. Unidentified exploratory references must not be treated as expert consensus. The available stored phases do not reveal how the instrument selected them. Some historical acquisitions retained a failed 6.1-ppm magnitude monitor rather than a successful regional result; that historical failure is reproducible evidence, not a processing omission. Phase method scores and imaginary/negative-area diagnostics are not proof of correct phase. Fixed-window and variable-width peak integrals are different metrics. June9 completion is decision support with moderate evidence and post-completion departures; read its quality warnings. Full phase uncertainty and chemical endpoint validation require experiment-specific expert assessment.",
        "", "## 12. Future workflow", "",
        "process_fid automatically retains compressed unphased/phased arrays, baseline/detection arrays, phase parameters, raw hash, code hashes, software versions and a titled before/after plot under phase_audit. Scientific calculations and thresholds were not changed. Run controller journals remain essential for proving a physical stop.",
        "", "## 13. Conclusions supported by the data", "",
        "Stored inverse-phase processing is inspectable and reproducible using preserved metadata and parameter snapshots. Alternate phasing can materially affect peak quantification/detection; it requires review rather than silent algorithm replacement. No claim of universal phase correctness or chemically justified instrument stopping is made without identified expert review and controller evidence.",
        "", "## Required question answers", "",
        "1. Current correction: stored inverse nmrglue linear phase.",
        "2. P0/P1: read from JCAMP $PHC0/$PHC1; no Python optimization in configured production.",
        "3. Pivot: k=0 with ramp k/N; GUI has a documented k/(N-1) pivot conversion preserved for compatibility.",
        "4. Appearance: before/after complex CSVs and figures show real/imaginary changes and unchanged magnitude.",
        f"5. Historical reproduction: see the explicit status counts above and full comparison table; {primary_ok} agreeing/reproduced comparison records.",
        "6. Manual agreement: only historical exploratory reference corrections are available; identified expert-cohort agreement cannot yet be calculated.",
        "7. Alternative effects: inspect phase_method_comparison.csv and phase_method_disagreements.csv; detection counts above.",
        "8. Manual detection: numerical per-checkpoint differences are explicit in automated_vs_manual.csv; human expert answer remains pending.",
        "9. Manual historical stop: no actual independent-acquisition controller event was established; retrospective/manual sequence comparisons are saved separately.",
        "10. Plausible-error sensitivity: grid outcomes above; no calibrated probability or guarantee beyond that explored grid.",
        "11. Questionable phase spectra: all alternative disagreements and nonconverged optimization records are retained for scientist review.",
        "12. Questionable peak results: every detection includes baseline/noise/width/integration boundaries and per-gate failures; zero-filled target rows are explicit nondetections.",
        "13. Traceability: every newly reported peak links to raw SHA-256, numerical arrays and immutable effective parameters.",
        "14. Future artifacts: phase_audit arrays, phase parameters, provenance, before/after figure plus existing peak/QC/decision tables and journals.",
        "15. Next issues: identified expert reviews, resolve historical disagreements, review post-completion departures, then independently approve any algorithm change."]
    (output/"VALIDATION_SUMMARY.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument("--inventory",type=Path,default=DEFAULT_INVENTORY)
    parser.add_argument("--acquisition-id")
    parser.add_argument("--no-alternatives",action="store_true")
    parser.add_argument("--no-sensitivity",action="store_true")
    parser.add_argument("--checkpoint",type=Path,help="Acquisition folder for an append-only manual checkpoint")
    parser.add_argument("--p0",type=float)
    parser.add_argument("--p1",type=float)
    parser.add_argument("--pivot-ppm",type=float)
    parser.add_argument("--reviewer")
    parser.add_argument("--notes",default="")
    args=parser.parse_args(argv)
    if args.checkpoint:
        if args.p0 is None or args.p1 is None: parser.error("--checkpoint requires --p0 and --p1")
        print(save_manual_checkpoint(args.checkpoint,p0_deg=args.p0,p1_deg=args.p1,
              pivot_ppm=args.pivot_ppm,reviewer=args.reviewer,notes=args.notes))
    else:
        print(json.dumps(build_package(args.output,args.inventory,acquisition_id=args.acquisition_id,
            alternatives=not args.no_alternatives,sensitivity=not args.no_sensitivity),indent=2))
    return 0
