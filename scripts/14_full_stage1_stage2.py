"""Full fixed-time Stage 1 -> one CH2 dose -> Stage 2, using 12's NMR method.

WORK: prepare the established needle reference and both pump loadings first.
--live starts immediately. --mock uses simulated time and no real instruments.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import math
from pathlib import Path

import _bootstrap  # noqa: F401
import yaml

from chemyx_lab import config
from chemyx_lab.workflows.stage2_legacy_nmr import LegacyAcquisition

# Reuse 12's loader and its existing physical/logging/review helpers, without
# invoking its Stage-2-only loop or importing numerical processing here.
import importlib.util

_spec = importlib.util.spec_from_file_location("stage2_legacy_for_full_run", Path(__file__).with_name("12_stage2only.py"))
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)
demo = legacy.demo
DEFAULT_CONFIG = config.REPO_ROOT / "configs/experiments/14_full_stage1_stage2.yaml"


def load_config(path=DEFAULT_CONFIG):
    cfg = legacy.load_config(path)
    stages = cfg.get("stages")
    if not isinstance(stages, list) or len(stages) != 2:
        raise ValueError("14 requires Stage 1 and Stage 2")
    for index, stage in enumerate(stages, 1):
        if not isinstance(stage, dict) or stage.get("name") != f"stage_{index}":
            raise ValueError("Stages must be stage_1 followed by stage_2")
        duration = demo.positive(stage["duration_minutes"], "stage duration")
        interval = demo.positive(stage["interval_minutes"], "stage interval")
        if not math.isfinite((duration + interval) * 60) or duration / interval > 10000 or type(stage["include_final_observation"]) is not bool:
            raise ValueError("Invalid or excessive stage schedule")
    if not cfg["channel_2"]["enabled"] or cfg["channel_2"]["delay_after_time_zero_seconds"] != 0:
        raise ValueError("14 requires one CH2 boundary dose with no Time-0 delay")
    return cfg


def stage_config(cfg, stage):
    result = deepcopy(cfg)
    result["experiment"].update(stage)
    return result


def run_experiment(cfg, *, mock, output_dir=None, clock=None, client=None,
                   rig_factory=demo.setup_instruments, acquisition=None,
                   analysis_launcher=demo.start_analysis):
    clock = clock or (demo.SimulatedClock() if mock else demo.Clock())
    acquisition = acquisition if acquisition is not None else LegacyAcquisition(
        cfg["nmr"], mock=mock, clock=clock, fixture=demo.FIXTURE, client=client)
    run = Path(output_dir or legacy.fresh_output(cfg, "full_stage1_stage2_")).resolve()
    run.mkdir(parents=True, exist_ok=False)
    origin = [clock.now()]
    logger, emit = demo.make_logger(run, cfg, clock, origin)
    report = dict(status="RUNNING", mode="MOCK" if mock else "LIVE", results=str(run),
                  channel_2_status="NOT_ATTEMPTED", stages=[], observations=[])
    relays, rig = [], None

    def save_all():
        for stage in report["stages"]:
            for status, key in (("NMR_SAVED", "nmr_saved"), ("NMR_FAILED", "nmr_failed"), ("NMR_MISSING", "nmr_missing")):
                stage[key] = sum(row["status"] == status for row in stage["observations"])
            demo.save_summary(Path(stage["results"]), stage, emit)
        demo.save_summary(run, report, emit)

    try:
        emit("FULL STAGE 1 / STAGE 2 — LEGACY NMR METHOD (14)" + (" [MOCK: no hardware]" if mock else ""))
        emit(f"Results: {run}")
        try:
            (run / "config_used.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        except OSError as exc:
            emit(f"Config snapshot warning: {exc}; timed experiment continues")

        # Launch optional children before opening serial handles. They only read
        # raw data, and remain alive for late NMR recovery across the boundary.
        for stage in cfg["stages"]:
            local = stage_config(cfg, stage)
            directory = run / stage["name"]
            directory.mkdir()
            raw_dir = directory / cfg["nmr"]["raw_directory"]
            summary = dict(stage=stage["name"], status="RUNNING", results=str(directory),
                           duration_seconds=stage["duration_minutes"] * 60,
                           interval_seconds=stage["interval_minutes"] * 60,
                           scheduled_observations=len(demo.observation_times(local)),
                           attempted_observations=0, skipped_observations=0,
                           nmr_saved=0, nmr_failed=0, nmr_missing=0, observations=[])
            report["stages"].append(summary)
            demo.save_summary(directory, summary, emit)
            try:
                snapshot = directory / "config_used.yaml"
                local["analysis"]["dataset_display_name"] = (
                    str(cfg["analysis"].get("dataset_display_name") or run.name).strip()
                    + " " + stage["name"])
                snapshot.write_text(yaml.safe_dump(local, sort_keys=False), encoding="utf-8")
                if cfg["analysis"].get("enabled", True):
                    relays.append(analysis_launcher(directory, snapshot, raw_dir, emit))
            except Exception as exc:
                emit(f"Analysis WARNING ({stage['name']}): {exc}; timed experiment continues")
        save_all()

        with rig_factory(cfg, run, mock=mock, clock=clock, emit=emit) as rig:
            rig.prepare()  # Load official reference; move HOME -> UP once.
            origin[0] = clock.now()
            for stage, summary in zip(cfg["stages"], report["stages"]):
                local = stage_config(cfg, stage)
                start = clock.now()
                summary["started_elapsed_seconds"] = start - origin[0]
                emit(f"{stage['name'].upper()} STARTED: {stage['duration_minutes']:g} minutes; {summary['scheduled_observations']} scheduled measurements")
                raw_dir = Path(summary["results"]) / cfg["nmr"]["raw_directory"]
                for index, due in enumerate(demo.observation_times(local), 1):
                    elapsed = clock.now() - start
                    if index > 1 and due < elapsed - 1e-8:
                        summary["skipped_observations"] += 1
                        emit(f"{stage['name']} MISSED observation {index}: no catch-up acquisition")
                        continue
                    while clock.now() - start < due:
                        clock.sleep(due - (clock.now() - start))
                    summary["attempted_observations"] += 1
                    path = raw_dir / f"{stage['name']}_{index:04d}.dx"
                    emit(f"{stage['name']} OBSERVATION {index} / {summary['scheduled_observations']}")

                    def measure():
                        try:
                            result = acquisition(cfg["nmr"], path, clock=clock, emit=emit)
                            if not isinstance(result, dict) or result.get("status") not in {"NMR_SAVED", "NMR_FAILED", "NMR_MISSING"}:
                                raise ValueError("Invalid NMR acquisition outcome")
                        except Exception as exc:
                            result = dict(status="NMR_FAILED", path=None, reason=f"{type(exc).__name__}: {exc}")
                        recovered = result.get("recovered_previous")
                        if isinstance(recovered, dict) and recovered.get("original_path") and recovered.get("path"):
                            for prior in report["observations"]:
                                if prior["requested_path"] == recovered["original_path"] and prior["status"] != "NMR_SAVED":
                                    prior.update(status="NMR_SAVED", path=recovered["path"], unresolved_start=False,
                                                 reason="Original acquisition completed during later monitoring")
                                    break
                        row = dict(result, stage=stage["name"], observation=index,
                                   requested_path=str(path), scheduled_seconds=due,
                                   acquired_elapsed_seconds=clock.now() - start,
                                   physical_cleanup="PENDING")
                        summary["observations"].append(row)
                        report["observations"].append(row)
                        if result["status"] != "NMR_SAVED":
                            emit(f"NMR WARNING: {result.get('reason', 'No raw spectrum saved')}; timed experiment continues")
                        return result

                    rig.sample(measure)
                    summary["observations"][-1]["physical_cleanup"] = "COMPLETE"
                    save_all()
                while clock.now() - start < summary["duration_seconds"]:
                    clock.sleep(summary["duration_seconds"] - (clock.now() - start))
                summary.update(window_complete=True, elapsed_seconds=clock.now() - start,
                               completed_elapsed_seconds=clock.now() - origin[0])
                emit(f"{stage['name'].upper()} COMPLETE")
                save_all()
                if stage["name"] == "stage_1":
                    report["channel_2_status"] = "ATTEMPTED_UNCERTAIN"
                    report["dose_started_elapsed_seconds"] = clock.now() - origin[0]
                    save_all()
                    rig.dose()  # Only this boundary; never scheduled at Stage 2 Time 0.
                    report["channel_2_status"] = "COMPLETED"
                    report["dose_completed_elapsed_seconds"] = clock.now() - origin[0]
                    save_all()
            report["status"] = "COMPLETE" if any(row["status"] == "NMR_SAVED" for row in report["observations"]) else "COMPLETE_NO_SPECTRA"
    except BaseException as exc:
        report["status"] = "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        emit(f"PHYSICAL INSTRUMENT / EXECUTION ERROR: {report['error']}; no further movement")
        if rig is not None:
            rig.stop()
    finally:
        report["elapsed_seconds"] = clock.now() - origin[0]
        for summary in report["stages"]:
            summary["status"] = (("COMPLETE" if summary["nmr_saved"] else "COMPLETE_NO_SPECTRA")
                                 if summary.get("window_complete") else report["status"])
        save_all()
        emit(f"EXPERIMENT {report['status']}; Channel 2: {report['channel_2_status']}; results: {run}")
        for relay in relays:
            if relay is not None:
                try:
                    relay.set()
                except Exception:
                    pass  # Optional review relay cannot change experiment success.
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, help="Optional fresh output directory")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_experiment(load_config(args.config), mock=args.mock, output_dir=args.output_dir)
        return 0 if result["status"] == "COMPLETE" else 1
    except Exception as exc:
        print(f"Full experiment startup failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
