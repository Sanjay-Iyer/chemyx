"""Retrospective Si6 diagnostics. Never called by a live endpoint evaluator."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

from .peak_tracking import tracking_config
from .phase_audit import save_figure, write_rows, write_json
from .stage_completion import completion_evidence


def diagnostic_row(row, reporting, run_dir):
    result = dict(row)
    flags = []
    filters = tracking_config(reporting.get("peak_filters", {}))
    qc = dict(noise_multiplier=3.0, max_noise_fraction=.01, max_area_uncertainty_fraction=.005)
    qc.update(reporting.get("measurement_qc", {}))
    if not row.get("peak_clear"):
        flags.append("peak_not_detected")
    if row.get("diagnostic_notes"):
        flags.append(str(row["diagnostic_notes"]))
    if row.get("qc_pass") is False:
        flags.append("recorded QC flag failed")
    if row.get("low_snr_warning"):
        flags.append("low SNR (recorded)")
    if row.get("high_noise_warning"):
        flags.append("high noise (recorded)")
    tests = {
        "snr": ("low SNR", lambda r: r["snr"] >= filters["snr"]["minimum"]),
        "prominence": ("low prominence", lambda r: r["prominence_snr"] >= filters["prominence"]["minimum_snr"]),
        "width": ("peak width outside review range", lambda r: filters["width"]["minimum_ppm"] <= r["width_ppm"] <= filters["width"]["maximum_ppm"]),
        "height": ("low height", lambda r: r["peak_height"] >= filters["height"]["minimum"]),
        "area": ("low area", lambda r: r["peak_area"] >= filters["area"]["minimum"]),
        "shoulder": ("possible shoulder", lambda r: r["asymmetry"] <= filters["shoulder"]["maximum_asymmetry"]),
        "previous_shift": ("large ppm shift", lambda r: abs(r["shift_previous_ppm"]) <= filters["previous_shift"]["maximum_ppm"]),
        "reference_shift": ("large reference ppm shift", lambda r: abs(r["shift_reference_ppm"]) <= filters["reference_shift"]["maximum_ppm"]),
    }
    if row.get("peak_clear"):
        for name, (label, test) in tests.items():
            if filters[name]["enabled"]:
                try:
                    if not test(row):
                        flags.append(label)
                except (TypeError, KeyError):
                    flags.append(label + " metric unavailable")
    phase = row.get("phase_method")
    if phase == "none" or any(row.get(k) is not None and not math.isfinite(float(row[k])) for k in ("phase_p0_deg", "phase_p1_deg")):
        flags.append("phase review required")
    evidence = row.get("phase_evidence_path")
    if evidence:
        try:
            path = Path(evidence)
            if not path.is_absolute():
                path = run_dir / path
            with np.load(path, allow_pickle=False) as arrays:
                x, y = arrays["region_ppm"], arrays["regional_quantitative"]
            candidates = json.loads(row.get("candidate_audit", "[]"))
            side = np.ones(len(x), dtype=bool)
            for p in candidates:
                if p["prominence_snr"] >= 5:
                    side &= abs(x-p["interpolated_ppm"]) > 2*p["width_ppm"]
            if side.sum() < 10:
                flags.append("independent noise estimate unavailable")
            else:
                noise = float(np.median(abs(y[side]-np.median(y[side])))/.67448975)
                width = max(0.0, float(row.get("integration_right_ppm") or 0) - float(row.get("integration_left_ppm") or 0))
                result.update(noise=noise, area_uncertainty=qc["noise_multiplier"] * noise * width)
                if row.get("negative_area", 0) > result["area_uncertainty"]:
                    flags.append("negative lobes; phase/baseline review required")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            flags.append(f"noise diagnostic unavailable: {exc}")
    result.update(qc_warning_count=len(flags), qc_warnings="; ".join(dict.fromkeys(flags)),
                  qc_pass=not flags, affect_workflow=False)
    return result


def write_final_qc(paths, rows, stages, reporting, *, dataset, outcome, transition=None):
    if reporting.get("affect_workflow") is not False:
        raise ValueError("QC reporting must have affect_workflow=false")
    if not reporting["enabled"]:
        return []
    directory = paths.run_dir / "final_qc"
    directory.mkdir(parents=True, exist_ok=True)
    review = [diagnostic_row(r, reporting, paths.run_dir) for r in rows]
    qc = {"max_noise_fraction": .01, "max_area_uncertainty_fraction": .005}
    qc.update(reporting.get("measurement_qc", {}))
    reference = next((r for r in review if r.get("peak_height") and r.get("peak_area", 0) > 0), {})
    for row in review:
        extra = []
        if reference and row.get("noise") is not None and row["noise"] / reference["peak_height"] > qc["max_noise_fraction"]:
            extra.append("high noise relative to first detected signal")
        if reference and row.get("area_uncertainty") is not None and row["area_uncertainty"] / reference["peak_area"] > qc["max_area_uncertainty_fraction"]:
            extra.append("high area uncertainty relative to first detected signal")
        row["qc_warnings"] = "; ".join(v for v in (row["qc_warnings"], *extra) if v)
        row["qc_warning_count"] += len(extra)
        row["qc_pass"] = not row["qc_warning_count"]
    # All plots consume this exact shared table and only metadata acquisition time.
    times = []
    for row in review:
        try:
            if row.get("timestamp_source") != "LONG DATE header":
                raise ValueError("No authoritative LONG DATE")
            times.append(datetime.fromisoformat(row["acquired_at"]))
        except (ValueError, TypeError, KeyError):
            times = []
            break
    can_plot = bool(review) and len(times) == len(review)
    for i, row in enumerate(review):
        row["plot_elapsed_hours"] = (times[i] - times[0]).total_seconds()/3600 if can_plot else None
    write_rows(directory / "qc_summary.csv", review)
    stage_summaries = [{"stage": s.name, "endpoint": completion_evidence([r for r in rows if r["stage"] == s.name], s.completion) if s.completion else None} for s in stages]
    payload = {"dataset_display_name": dataset, "status": outcome.status.value,
               "affect_workflow": False, "timing_source": "JCAMP LONG DATE only",
               "plots_available": can_plot, "stages": stage_summaries,
               "transition": transition, "observations": review}
    write_json(directory / "qc_summary.json", payload)
    figures = []
    if can_plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for stem, field, label, title in (
            ("peak_position_vs_time", "peak_ppm", "Tracked peak center (ppm)", "Tracked Peak Position vs Time"),
            ("peak_area_vs_time", "peak_area", "Tracked trapezoidal area (a.u.)", "Tracked Peak Area vs Time"),
            ("snr_vs_time", "snr", "SNR", "SNR vs Time"),
            ("peak_width_vs_time", "width_ppm", "Peak width (ppm)", "Peak Width vs Time"),
        ):
            fig, ax = plt.subplots(figsize=(8, 4), layout="constrained")
            for stage in stages:
                selected = [r for r in review if r["stage"] == stage.name]
                ax.plot([r["plot_elapsed_hours"] for r in selected], [r.get(field) if r.get(field) is not None else float("nan") for r in selected], "o-", label=stage.name)
            ax.set(xlabel="Hours from first acquisition (JCAMP LONG DATE)", ylabel=label)
            ax.legend()
            save_figure(fig, directory / stem, dataset, title, figures)
    write_json(directory / "plot_manifest.json", {"dataset_display_name": dataset, "figures": figures})
    text = [f"# {dataset} Si6 retrospective QC", "", f"Recorded experiment outcome: `{outcome.status.value}`.",
            "", "These warnings are for post-run review. They did not reject observations, delay acquisitions, authorize dosing, or change either endpoint.", "",
            f"Observations: {len(review)}; observations with warnings: {sum(bool(r['qc_warning_count']) for r in review)}.", "",
            "Time source: JCAMP LONG DATE. CSV and JSON retain candidate decisions, raw/phase evidence paths, centers, moving bounds, areas and diagnostic metrics.", ""]
    if not can_plot:
        text += ["Time plots omitted: authoritative metadata is unavailable for one or more observations (or no observations recorded). No filename/modification-time substitution.", ""]
    for i, row in enumerate(review, 1):
        if row["qc_warnings"]:
            text.append(f"- Observation {i} ({row['stage']}): {row['qc_warnings']}")
    (directory / "qc_report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return figures
