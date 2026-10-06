"""Offline, rebuildable reports for distinct stage evidence and the dose boundary."""
from __future__ import annotations

from .phase_audit import save_figure, write_rows
from .stage_completion import completion_evidence
from ..runtime_state import write_json_atomic


def write_stage_reports(paths, rows, stages, *, dataset, outcome=None, transition=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figures = []
    summaries = []
    for stage in stages:
        selected = [r for r in rows if r["stage"] == stage.name]
        directory = paths.run_dir / "stages" / stage.name
        directory.mkdir(parents=True, exist_ok=True)
        write_rows(directory / "time_series.csv", selected)
        evidence = completion_evidence(selected, stage.completion)
        summary = {"stage": stage.name, "trend": stage.completion["trend"], "measurements": len(selected),
                   "interval_minutes": stage.interval_minutes, "max_hours": stage.max_hours,
                   "dataset_display_name": dataset, "completion": evidence}
        write_json_atomic(directory / "summary.json", summary)
        summaries.append(summary)
        if selected:
            fig, ax = plt.subplots(figsize=(8, 4), layout="constrained")
            x = [float(r["elapsed_hours"]) - float(selected[0]["elapsed_hours"]) for r in selected]
            y = [float(r["completion_area"]) for r in selected]
            ax.errorbar(x, y, yerr=[float(r.get("area_uncertainty", 0)) for r in selected], fmt="o-")
            ax.set(xlabel="Hours from first stage acquisition (JCAMP LONG DATE)", ylabel="Fixed target integral (a.u.)")
            title = save_figure(fig, directory / "time_series", dataset, f"{stage.name} {stage.completion['trend']} Time Series", figures)
            summary["visible_title"] = title
            write_json_atomic(directory / "summary.json", summary)
    final = paths.run_dir / "final"
    final.mkdir(exist_ok=True)
    write_rows(final / "full_time_series.csv", rows)
    if rows:
        fig, ax = plt.subplots(figsize=(9, 4), layout="constrained")
        for stage in stages:
            selected = [r for r in rows if r["stage"] == stage.name]
            ax.plot([r["elapsed_hours"] for r in selected], [r["completion_area"] for r in selected], "o-", label=stage.name)
        if transition and transition.get("elapsed_hours") is not None:
            ax.axvline(transition["elapsed_hours"], color="black", ls="--", label="Channel-2 addition (journal boundary)")
        ax.set(xlabel="Hours from first acquisition (JCAMP LONG DATE)", ylabel="Fixed target integral (a.u.)")
        ax.legend()
        save_figure(fig, final / "full_time_series", dataset, "Two-stage Reaction Time Series", figures)
    summary = {"schema": "chemyx.si6-two-stage-summary.v1", "dataset_display_name": dataset,
               "status": outcome.status.value if outcome else "running", "stages": summaries,
               "transition": transition, "timing_source": "JCAMP LONG DATE; dose boundary is journal time",
               "simulation_only": any("SIMULATION ONLY" in r.get("metric_source", "") for r in rows)}
    write_json_atomic(final / "experiment_summary.json", summary)
    write_json_atomic(final / "plot_manifest.json", {"dataset_display_name": dataset, "figures": figures})
    return figures
