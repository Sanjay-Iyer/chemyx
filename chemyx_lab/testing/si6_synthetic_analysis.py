"""HOME-only processed-spectrum validation through unchanged production code.

The copied DX is a metadata carrier, NOT a synthetic raw FID. Quantitative
arrays are generated independently and explicitly identified as synthetic.
This module is imported only by validation tooling, never by a live runner.
"""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

from chemyx_lab import config
from chemyx_lab.analysis.nmr import SpectrumData, read_jcamp_fid
from chemyx_lab.analysis.phase_audit import sha256, write_json, write_rows, save_figure
from chemyx_lab.analysis.stage_completion import completion_evidence
from chemyx_lab.analysis.stage_measurement import tracked_peak_measurement
from chemyx_lab.analysis.nmr_validation import pipeline
from chemyx_lab.runtime_state import replay_journal
from chemyx_lab.workflows import three_instrument_si6 as si6
from chemyx_lab.workflows.si6_simulation import TrendSimulation

DATASET = "SI6-SYNTHETIC-VALIDATION"
SUCCESS = {
    "stage_1": [100, 82, 64, 45, 28, 15, 7, 3, 1.8, 1.4, 1.3, 1.2, 1.2, 1.2],
    "stage_2": [1, 8, 20, 38, 56, 71, 82, 90, 95, 98, 99, 99.4, 99.2, 99.5, 99.4, 99.4],
}
CASES = {
    "stage1_success": ("stage_1", SUCCESS["stage_1"], 14),
    "stage1_false_low": ("stage_1", [100, 75, 52, 4, 35, 25, 18, 16, 14], None),
    "stage1_below_threshold_spike": ("stage_1", [100, 75, 52, 1, 35, 25, 18, 16, 14], None),
    "stage1_low_changing": ("stage_1", [100, 55, 25, 10, 6, 4.2, 3, 2.1], None),
    "stage1_high_flat": ("stage_1", [100, 70, 50, 40, 39.5, 39.3, 39.4, 39.2] + [39.2] * 8, None),
    "stage2_success": ("stage_2", SUCCESS["stage_2"], 16),
    "stage2_still_growing": ("stage_2", [1, 10, 25, 42, 58, 70, 80, 88, 94], None),
    "stage2_temporary_plateau": ("stage_2", [1, 15, 35, 55, 70, 72, 72.2, 72.1, 82, 90, 96], None),
    "stage2_no_growth": ("stage_2", [2, 2.1, 2, 2.1, 2, 2.1] + [2.1] * 10, None),
    "stage2_decline": ("stage_2", [1, 20, 50, 80, 95, 70, 60, 59, 59] + [59] * 8, None),
}


def nominal():
    return yaml.safe_load((config.REPO_ROOT / "config_templates/experiments/si6_two_stage_nominal.yaml").read_text())


def historical_nominal():
    """Offline-only statistical/QC counterexamples, not live admission rules."""
    return yaml.safe_load((config.REPO_ROOT / "chemyx_lab/testing/fixtures/si6_historical_statistical.yaml").read_text())


def stage_config(raw, stage):
    return raw["workflow"]["initial_stage" if stage == "stage_1" else "first_addition_stage"]


def production_processor():
    return pipeline()


def metadata_carrier(path, stamp):
    text = si6.MOCK_NMR_FIXTURE.read_text(encoding="latin-1")
    text, count = re.subn(r"(?m)^##LONG DATE=.*$", "##LONG DATE=" + stamp.strftime("%Y/%m/%d %H:%M:%S%z"), text)
    if count != 1:
        raise ValueError("Expected one LONG DATE in metadata carrier")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="latin-1")


class SyntheticSpectra:
    """Generate input traces; measure exclusively through production helpers."""

    def __init__(self):
        self.processor = production_processor()
        argv = ["--config", str(config.REPO_ROOT / "configs/nmr/analysis.yaml")]
        self.args = self.processor._parser(self.processor._config_defaults(argv)).parse_args(argv)
        if self.args.normalization == "max":
            raise ValueError("Synthetic validation requires the production comparable-area configuration")

    def measure(self, source, output, metadata, analysis, prior, *, area, seed=4100, drift_ppm=0.0, noise_sd=0.002):
        output.mkdir(parents=True, exist_ok=False)
        x = np.linspace(5.0, 6.5, 1025)
        sigma = 0.014
        center = float(metadata["target_ppm"]) + drift_ppm
        peak = area / (sigma * np.sqrt(2 * np.pi)) * np.exp(-0.5 * ((x - center) / sigma) ** 2)
        baseline = 0.02 + 0.01 * (x - 5.8)
        y = peak + baseline + np.random.default_rng(seed).normal(0, noise_sd, x.size)
        fid = read_jcamp_fid(source)
        frequency = float(fid.metadata["$SF"])
        spectrum = SpectrumData(source=source, metadata=fid.metadata, ppm_axis=x,
                                magnitude=np.abs(y), real=y, imaginary=np.zeros_like(y),
                                processed_points=len(x), observe_frequency_mhz=frequency,
                                phase_method="synthetic_already_phased", phase0_deg=0, phase1_deg=0)
        args = copy.copy(self.args)
        from chemyx_lab.analysis.peak_tracking import configure_candidates, candidate_records
        configure_candidates(args, analysis.get("peak_finding", analysis.get("peak_tracking")))
        quantitative, magnitude, picked = self.processor.process_spectrum_for_peaks(spectrum, args)
        peak_rows = []
        for p in picked.peaks:
            width = p.width_ppm * frequency
            passed, reason, flags = self.processor._peak_qc(p, width, args)
            peak_rows.append(dict(file=source.name, timestamp=metadata["acquired_at"],
                                  interpolated_ppm=p.interpolated_ppm, positive_area=p.positive_area,
                                  height=p.peak_height, intensity=p.peak_height, snr=p.snr,
                                  prominence_snr=p.prominence_snr, width_hz=width,
                                  qc_pass=passed, qc_failure_reasons=reason, **flags))
        self.processor._write_simple_peaks([dict(file=source.name, timestamp=metadata["acquired_at"])],
                                           peak_rows, output / "synthetic_peaks_simple.csv", args)
        self.processor._write_peak_qc_log(peak_rows, output / "synthetic_peak_qc_log_window.csv", args, window_only=True)
        audit = output / "phase_audit/synthetic_processed"
        audit.mkdir(parents=True)
        np.savez_compressed(audit / "spectral_evidence.npz", region_ppm=picked.ppm_axis,
                            regional_quantitative=picked.quantitative_corrected,
                            ppm=x, generated_real=y, baseline=baseline,
                            quantitative_real=quantitative, diagnostic_magnitude=magnitude)
        write_json(audit / "processing_metadata.json", {
            "peak_candidates": candidate_records(picked),
            "simulation_only": True, "validation_level": 2,
            "input_kind": "synthetic processed spectrum; copied DX metadata carrier, not synthetic raw/FID",
            "raw_sha256": sha256(source), "spectral_evidence_sha256": sha256(audit / "spectral_evidence.npz"),
            "phase": {"method": "synthetic_already_phased", "p0_deg": 0, "p1_deg": 0},
            "parameters": vars(args), "dataset_display_name": metadata["dataset_display_name"],
            "generated_area": area, "seed": seed, "drift_ppm": drift_ppm, "noise_sd": noise_sd,
            "model": {"grid_points": len(x), "ppm_min": 5.0, "ppm_max": 6.5,
                      "gaussian_sigma_ppm": sigma, "baseline_intercept": 0.02,
                      "baseline_slope_per_ppm": 0.01},
            "phase_scope": "input constructed as absorption real; no raw decoding, FFT or phase optimization validated",
            "processing_function": "scripts/nmr/process_fid.py:process_spectrum_for_peaks",
        })
        row = dict(metadata)
        if "peak_finding" in analysis:
            from ..analysis.stage_measurement import tracked_peak_area_observation
            row = tracked_peak_area_observation(source, output, row, analysis, prior)
        else:
            row = tracked_peak_measurement(source, output, row, analysis, prior, reference=prior[0] if prior else None)
        # The adapter's normal provenance refers to raw processing. Override only
        # the provenance label, never the measured value or QC decision.
        row.update(metric_source="SIMULATION ONLY: synthetic processed trace through production tracked moving peak area",
                   generated_area=area, simulation_only=True)
        spectrum_rows = [dict(iteration=metadata["iteration"], stage=metadata["stage"],
                              elapsed_hours=metadata["elapsed_hours"], ppm=float(ppm), magnitude=float(value))
                         for ppm, value in zip(x, np.abs(y))]
        return row, spectrum_rows


def trace_row(case, iteration, row, evidence):
    checks = evidence.get("checks", [])
    last = checks[-1] if checks else {}
    return dict(case=case, iteration=iteration, generated_metric=row.get("generated_area", row["peak_area"]),
                measured_metric=row["peak_area"], area_uncertainty=row.get("area_uncertainty", ""),
                acquired_at=row["acquired_at"], qc=row.get("measurement_valid", "retrospective only"),
                low_region=last.get("low_region", "insufficient points"),
                near_observed_maximum=last.get("near_observed_maximum", "insufficient points"),
                slope_fraction_per_hour=last.get("slope_fraction_per_hour", ""),
                range_fraction=last.get("range_fraction", ""),
                progress_fraction=last.get("progress_fraction", ""),
                passing_windows=sum(c["passed"] for c in checks), completed=evidence["complete"],
                reason=evidence["reason"])


def validate_case(case, level, output, engine=None):
    raw = historical_nominal()
    stage, values, expected = CASES[case]
    settings = stage_config(raw, stage)
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows, traces, evaluations = [], [], []
    reference = []
    if level == 2 and stage == "stage_2":
        # Production uses the first Stage1 observation as the global QC
        # reference, even when Stage2 begins close to zero.
        source = output / case / "initial_stage1_qc_reference.dx"
        metadata_carrier(source, origin - timedelta(hours=2))
        meta = dict(iteration=0, stage="stage_1", elapsed_hours=-2,
                    acquired_at=(origin - timedelta(hours=2)).isoformat(),
                    target_ppm=raw["nmr"]["target_ppm"], dataset_display_name=DATASET)
        ref, _ = engine.measure(source, output / case / "qc_reference", meta, raw["analysis"], [], area=100)
        reference = [ref]
    for index, area in enumerate(values):
        stamp = origin + timedelta(minutes=index * settings["interval_minutes"])
        metadata = dict(iteration=index + 1, stage=stage, elapsed_hours=index * settings["interval_minutes"] / 60,
                        acquired_at=stamp.isoformat(), acquired_at_source="synthetic LONG DATE metadata",
                        target_ppm=raw["nmr"]["target_ppm"], dataset_display_name=DATASET)
        if level == 1:
            row = dict(metadata, peak_area=area, generated_area=area,
                       peak_clear=True, measurement_valid=True, area_uncertainty=0.01)
        else:
            source = output / case / f"iteration_{index + 1}.dx"
            metadata_carrier(source, stamp)
            row, _ = engine.measure(source, output / case / f"processed_{index + 1}", metadata,
                                    raw["analysis"], reference + rows, area=area, seed=4100 + index,
                                    drift_ppm=0.001 * np.sin(index))
        rows.append(row)
        evidence = completion_evidence(rows, settings["completion"])
        evaluations.append(evidence)
        traces.append(trace_row(case, index + 1, row, evidence))
    completed = [t["iteration"] for t in traces if t["completed"]]
    actual = completed[0] if completed else None
    if actual != expected or not all(t["qc"] for t in traces):
        raise AssertionError(f"Level{level} {case}: expected completion {expected}, got {actual}")
    write_rows(output / f"{case}_trace.csv", traces)
    write_json(output / f"{case}_evidence.json", evaluations)
    print(f"PASS Level{level} {case}: completion={actual}")
    return dict(case=case, stage=stage, completion_iteration=actual, rows=rows, trace=traces, evidence=evaluations)


class SpectralWorkflowSimulation(TrendSimulation):
    """Same virtual clock and shared controller; production-derived measurements."""

    def __init__(self, settings, source, engine):
        super().__init__(settings, source)
        self.fixture = {"simulation_only": True, "stages": SUCCESS}
        self.engine = engine
        self.rows = []

    def process(self, path, paths, dataset):
        output = paths.run_dir / "processed_nmr" / f"{path.stem}_synthetic_spectrum"
        # Measurement needs acquisition metadata supplied by the shared runner.
        self.pending_area = self.last["area"]
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "SIMULATION_ONLY.json", {"input_kind": "synthetic processed spectrum",
                   "metadata_carrier_only": True, "generated_area": self.pending_area})
        return output

    process.last_kind = "SIMULATION ONLY synthetic processed spectrum"

    def analyze(self, path, processed, paths, analysis, metadata):
        index = len(self.rows)
        row, spectra = self.engine.measure(path, processed / "measurement", metadata, analysis, self.rows,
                                          area=self.pending_area, seed=4100 + index,
                                          drift_ppm=0.001 * np.sin(index))
        self.rows.append(row)
        return row, spectra


def validate_workflow(output, engine):
    with (output / "shared_workflow.log").open("w", encoding="utf-8") as log, redirect_stdout(log):
        return _validate_workflow(output, engine)


def _validate_workflow(output, engine):
    settings = list(si6.prepare(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml",
                               config.REPO_ROOT / "config_templates/machines/si6_instrument_settings.example.yaml",
                               config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True))
    raw = settings[0]
    # Only the test deadline/output change. Chemistry decision thresholds and
    # nominal 120/30 minute metadata cadence are exactly retained.
    raw["output"]["run_root_dir"] = str(output / "workflow")
    with si6.open_services(*settings, identity=si6.RunIdentity("si6", True)) as services:
        sim = SpectralWorkflowSimulation(raw["simulation"], si6.MOCK_NMR_FIXTURE, engine)
        services.simulation = sim
        services.acquire, services.process, services.analyze, services.sleep = sim.acquire, sim.process, sim.analyze, sim.sleep
        outcome = si6.run_experiment(services)
        run_dir = services.paths.run_dir
        journal_path = services.paths.journal_jsonl
        rows = copy.deepcopy(sim.rows)
        transition = copy.deepcopy(services.transition_evidence)
    if outcome.exit_code or outcome.status.value != "completed":
        raise AssertionError(f"Full synthetic workflow failed: {outcome}")
    events = [json.loads(line) for line in journal_path.read_text().splitlines()]
    # The receipt and shared journal independently establish a single dose.
    doses = [e for e in events if e.get("event_type") == "dose_confirmed"]
    if len(doses) != 1:
        raise AssertionError(f"Expected exactly one dose_confirmed, got {len(doses)}")
    moves = [e for e in events if e.get("operation_type") in {"withdraw", "infuse"}
             and e.get("lifecycle_state") == "completed"]
    ch2 = [e for e in moves if e.get("channel") == 2]
    assert len(ch2) == 1 and ch2[0]["operation_type"] == "infuse"
    assert ch2[0]["requested_parameters"]["volume"] == 1.8
    assert ch2[0]["requested_parameters"]["rate"] == 1
    assert doses[0]["channel"] == 2 and doses[0]["volume_ml"] == 1.8 and doses[0]["rate_ml_min"] == 1
    assert transition["status"] == "CONFIRMED" and transition["rate_ml_min"] == 1
    stage1 = [e for e in events if e.get("event_type") == "stage_completion_evidence" and e.get("workflow_phase") == "stage_1"][-1]
    stage2 = next(e for e in events if e.get("event_type") == "measurement_started" and e.get("workflow_phase") == "stage_2")
    intent = next(e for e in events if e.get("event_type") == "dose_dispatch_intent")
    assert stage1["evidence"]["complete"]
    assert stage1["sequence"] < intent["sequence"] < ch2[0]["sequence"] < doses[0]["sequence"] < stage2["sequence"]
    assert all(e["channel"] == 1 for e in moves if e["sequence"] > doses[0]["sequence"])
    assert {stage: sum(r["stage"] == stage for r in rows) for stage in SUCCESS} == {"stage_1": 11, "stage_2": 13}
    replay = replay_journal(journal_path)
    assert replay.valid and replay.state.pump_channels["2"]["cumulative_infused_ml"] == 1.8
    assert all(r["observation_recorded"] and r["simulation_only"] for r in rows)
    traces = []
    for stage in SUCCESS:
        selected = [r for r in rows if r["stage"] == stage]
        evidence = [e["evidence"] for e in events if e.get("event_type") == "stage_completion_evidence" and e.get("workflow_phase") == stage]
        assert len(evidence) == len(selected) and not any(e["complete"] for e in evidence[:-1]) and evidence[-1]["complete"]
        for index, (row, item) in enumerate(zip(selected, evidence)):
            traces.append(dict(stage=stage, **trace_row("full_workflow", index + 1, row, item)))
    write_rows(output / "workflow_completion_trace.csv", traces)
    write_json(output / "workflow_evidence.json", dict(outcome=outcome.status.value, run_dir=str(run_dir),
               rows=rows, transition=transition, dose_confirmed=doses, journal=str(journal_path),
               nominal_stage2_max_hours=12, completion_method="area_only"))
    print(f"PASS Level3 full shared workflow: {outcome.status.value}; one confirmed Ch2 addition")
    return dict(rows=rows, transition=transition, run_dir=str(run_dir), journal=str(journal_path), events=events, trace=traces)


def plots(output, cases, workflow):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    manifest = []
    for name in ("stage1_success", "stage2_success"):
        result = cases[name]
        stage = result["stage"]
        rows = result["rows"]
        times = [r["elapsed_hours"] for r in rows]
        areas = [r["peak_area"] for r in rows]
        stop = result["completion_iteration"]
        fig, ax = plt.subplots(figsize=(9, 5), layout="constrained")
        ax.plot(times, areas, "o-", label="Measured production tracked peak area")
        ax.plot(times, [r["generated_area"] for r in rows], "--", label="Generated input area", alpha=.6)
        if stage == "stage_1":
            ax.axhspan(0, areas[0] * .025, color="green", alpha=.15, label="Low region: <=2.5% initial")
        else:
            ax.axhspan(max(areas) * .98, max(areas), color="green", alpha=.15, label="Near maximum: >=98% observed max")
        ax.axvline(times[stop - 1], color="red", ls=":", label=f"Completion iteration {stop}")
        ax.annotate(f"{stop}: {areas[stop - 1]:.5f}", (times[stop - 1], areas[stop - 1]), xytext=(-90, 20), textcoords="offset points")
        ax.set(xlabel="Hours from synthetic LONG DATE", ylabel="Integral (arbitrary units)")
        ax.legend(fontsize=8)
        save_figure(fig, output / f"{stage}_completion", DATASET, f"{stage.replace('_', ' ').title()} Completion Detection", manifest)
    rows = workflow["rows"]
    fig, ax = plt.subplots(figsize=(11, 5), layout="constrained")
    for stage in SUCCESS:
        selected = [r for r in rows if r["stage"] == stage]
        ax.plot([r["elapsed_hours"] for r in selected], [r["peak_area"] for r in selected], "o-", label=stage.replace("_", " "))
        last = selected[-1]
        ax.annotate(f"{stage} complete ({len(selected)})", (last["elapsed_hours"], last["peak_area"]),
                    xytext=(-110, 18), textcoords="offset points", fontsize=8)
    dose_hour = workflow["transition"]["elapsed_hours"]
    ax.axvline(dose_hour, ls=":", color="red", label="Ch2 1.8 mL @ 1.0 mL/min")
    ax.set(xlabel="Hours from first synthetic JCAMP LONG DATE", ylabel="Measured tracked peak area")
    ax.legend(fontsize=8)
    save_figure(fig, output / "combined_workflow", DATASET, "Two Stage Workflow and Confirmed Addition", manifest)
    # Direct spectra evidence demonstrates the decreasing and increasing inputs.
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")
    for ax, name in zip(axes, ("stage1_success", "stage2_success")):
        result = cases[name]
        for i in (1, 5, len(result["rows"])):
            path = output.parent / "level2" / name / f"processed_{i}/phase_audit/synthetic_processed/spectral_evidence.npz"
            with np.load(path) as arrays:
                ax.plot(arrays["region_ppm"], arrays["regional_quantitative"], label=f"Iteration {i}")
        ax.set(title=result["stage"], xlim=(5.95, 5.65), xlabel="ppm", ylabel="Corrected real intensity")
        ax.axvspan(5.7, 5.9, color="gray", alpha=.1)
        ax.legend(fontsize=8)
    save_figure(fig, output / "synthetic_spectra", DATASET, "Processed Spectra Through Production Analysis", manifest)
    write_rows(output / "plot_manifest.csv", manifest)
    return manifest


def report(output, levels, workflow, manifest):
    lines = [f"# {DATASET} validation", "", "SOFTWARE ONLY. Hardware NOT tested. Chemistry thresholds NOT commissioned.", "",
             "Levels 1/2 retain HISTORICAL OFFLINE statistical/QC counterexamples, using si6_historical_statistical.yaml. Their window/QC assertions are not live workflow gates.",
             "Level3 uses CURRENT area-only completion and measured moving spectra in the shared three-instrument mock controller: one confirmed Ch2 dose, automatic Stage2 entry, retrospective QC only.", "",
             "Copied DX files carry synthetic LONG DATE timestamps only; their FIDs are unchanged real-fixture carriers. Generated arrays do not test vendor decoding, FFT, physical acquisition or phase optimization. All evidence labels identify this limitation.", "",
             "This spectrum validator explicitly selects shared configs/nmr/analysis.yaml, excluding optional per-rig analysis.local.yaml processing overrides. Such overrides require their own WORK/real-spectrum review.", "",
             "Current full workflow: Stage1 stops at11 and Stage2 at13 measured observations, with nominal120/30min cadence and48/12h runtime ceilings. Each stage has20 iterations maximum. Wall time uses a virtual clock.", "",
             "The model uses1025 ascending points from5.0–6.5ppm, Gaussian sigma0.014ppm, baseline0.02+0.01*(ppm-5.8), seeded Gaussian noise SD0.002 and drift0.001*sin(iteration index)ppm. Production ALS is sensitive to trace resolution/shape. The initial4097-point input failed negative-lobe QC; the final geometry passes without changing any production gate. This clean model is not an instrument noise/lineshape calibration.", "",
             "## Decisions and counterexamples", "", "| Case | Level1 stop | Level2 stop | QC |", "|---|---:|---:|---|"]
    for name in CASES:
        a, b = levels[1][name], levels[2][name]
        lines.append(f"| {name} | {a['completion_iteration'] or 'no completion'} | {b['completion_iteration'] or 'no completion'} | PASS |")
    for name in ("stage1_success", "stage2_success"):
        result = levels[2][name]
        n = result["completion_iteration"]
        row = result["rows"][n - 1]
        previous = result["evidence"][n - 2]
        lines += ["", f"## {name}", "", f"Completion at iteration{n}, measured area{row['peak_area']:.8f}, uncertainty{row['area_uncertainty']:.8f}, stage metadata time{row['elapsed_hours']:g}h.",
                  f"One iteration earlier: only{sum(c['passed'] for c in previous['checks'])}/3 windows pass. Full checks:", "", "```json", json.dumps(previous, indent=2), "```", "",
                  "| Iteration | Generated | Measured | QC | Low | Near max | Slope/h | Range | Passing windows | Complete |",
                  "|---:|---:|---:|---|---|---|---:|---:|---:|---|"]
        for t in result["trace"]:
            lines.append(f"| {t['iteration']} | {t['generated_metric']:g} | {t['measured_metric']:.6f} | {t['qc']} | {t['low_region']} | {t['near_observed_maximum']} | {t['slope_fraction_per_hour']} | {t['range_fraction']} | {t['passing_windows']} | {t['completed']} |")
    lines += ["", "## Conservatism", "",
              "Stage1 requires8 valid points,12h,4-point windows and3 consecutive overlapping confirmations; >=95% progress, upper signal including uncertainty <=2.5% initial, uncertainty-inclusive range <=1%, |slope| <=0.001 initial fraction/h. A single low point, a low but changing window, and a stable high signal fail.",
              "Stage2 requires8 valid points,3h,5-point windows and3 consecutive overlapping confirmations; progress >=25% of observed maximum, every lower bound >=98% of the maximum seen over the entire stage, range <=2%, |slope| <=0.012 maximum fraction/h. No growth fails progress; decline from a historical maximum fails near-maximum. The supplied short temporary plateau fails persistence/range/slope. A sufficiently long stable plateau can pass before hypothetical later growth: no algorithm observes future chemistry. expected_duration_hours3 is a hint, not a completion override.",
              "Nominal measurement QC remains noise <=1% of initial height, uncertainty <=0.5% of initial area, multiplier3; undetected signal needs independently bounded <=2.5% initial evidence. Synthetic noise is deliberately small and does not establish real-instrument suitability. Real spectra require separate tracked-area precision validation; do not loosen gates based on synthetic success.", "",
              "## Current area-only full shared workflow", "", f"Run: `{workflow['run_dir']}`. Journal: `{workflow['journal']}`.",
              "Full workflow completed. Stage1 analysis precedes a single confirmed Ch2 infusion1.8mL at1.0mL/min; the receipt precedes Stage2 and Channel1 sampling resumes. Shared dose guard/replay logic is unchanged.", "",
              "Each level has per-iteration CSV and full window JSON. Level2 retains generated traces, seed/noise/drift, production tables and hashed spectral evidence for every counterexample. Figure manifest records the same visible dataset title for PNG/SVG/PDF.", "",
              "## Figures", ""]
    position = lines.index("## Figures")
    full_trace = ["## Shared runner completion trace", "", "| Stage | Iteration | Generated | Measured | QC | Passing windows | Complete |", "|---|---:|---:|---:|---|---:|---|"]
    full_trace += [f"| {t['stage']} | {t['iteration']} | {t['generated_metric']:g} | {t['measured_metric']:.8f} | {t['qc']} | {t['passing_windows']} | {t['completed']} |" for t in workflow["trace"]]
    lines[position:position] = full_trace + [""]
    lines += [f"![{m['visible_title']}]({Path(m['path']).relative_to(output).as_posix()})" for m in manifest if m["path"].endswith(".png")]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_all(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    engine = SyntheticSpectra()
    levels = {1: {}, 2: {}}
    for level in levels:
        for case in CASES:
            levels[level][case] = validate_case(case, level, output / f"level{level}", engine)
    workflow = validate_workflow(output, engine)
    print("PASS Level3 full analysis-driven shared workflow: completed")
    print("PASS transition: exactly one Channel2 infusion, 1.8 mL @ 1.0 mL/min; confirmed before Stage2; sampling resumes Channel1")
    manifest = plots(output / "figures", levels[2], workflow)
    report(output, levels, workflow, manifest)
    write_json(output / "SUMMARY.json", dict(simulation_only=True, checks="PASS", dataset_display_name=DATASET,
               completion_iterations={n: r["completion_iteration"] for n, r in levels[2].items()},
               historical_counterexamples=True, workflow_completion_method="area_only",
               workflow_run=workflow["run_dir"], nominal_stage2_max_hours=12,
               hardware_tested=False, chemistry_thresholds_commissioned=False))
    print(f"PASS all required synthetic analysis checks; report: {output / 'REPORT.md'}")
    print("SOFTWARE ONLY | HARDWARE NOT TESTED | THRESHOLDS NOT COMMISSIONED")
    return output
