"""Configuration-only checkout overlay and retrospective evidence; no instrument commands."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import json
from pathlib import Path

from .. import config


def validate_checkout(raw):
    section = raw.get("checkout")
    if section is None:
        return
    fields = {"enabled", "purpose", "base_recipe", "base_recipe_sha256", "test_fluid", "routing", "overall_timeout_minutes"}
    if not isinstance(section, dict) or set(section) - fields:
        raise ValueError("checkout needs only enabled, purpose, base_recipe, test_fluid and routing")
    if section.get("enabled") is not True:
        raise ValueError("checkout.enabled must be true")
    if not isinstance(section.get("base_recipe"), str) or not section["base_recipe"].strip():
        raise ValueError("Fill checkout.base_recipe to select the instrument settings")
    # Descriptions do not select instruments or authorize transfers.
    for key, default in (("purpose", "physical_instrument_checkout"),
                         ("test_fluid", "unspecified"), ("routing", "unspecified")):
        value = section.get(key)
        section[key] = value.strip() if isinstance(value, str) and value.strip() else default
    timeout = section.get("overall_timeout_minutes", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("checkout.overall_timeout_minutes must be a positive finite number")
    section["overall_timeout_minutes"] = timeout
    identity = raw.get("workflow", {}).get("experiment_id")
    if not isinstance(identity, str) or not identity.startswith("SI6_CHECKOUT_"):
        raise ValueError("Set a unique workflow.experiment_id beginning SI6_CHECKOUT_ (never the production ID)")
    if any(raw["workflow"].get(key, {}).get("control_mode") != "fixed_duration"
           for key in ("initial_stage", "first_addition_stage")):
        raise ValueError("Both checkout stages must select fixed_duration")


def resolve_checkout(raw, path):
    """Reuse one validated production setup; only checkout-owned overrides merge."""
    if "checkout" not in raw:
        return raw
    validate_checkout(raw)
    if {"nmr", "analysis", "output"} <= set(raw):
        return raw  # Already-resolved effective snapshot, still validated below.
    if set(raw) - {"checkout", "workflow", "pump"}:
        raise ValueError("Checkout overlay supports checkout, workflow and pump only")
    source = Path(raw["checkout"]["base_recipe"])
    if not source.is_absolute():
        source = Path(path).resolve().parent / source
    original = config.read_mapping_config(source, "checkout base recipe")
    if "checkout" in original or "simulation" in original:
        raise ValueError("Checkout base_recipe must be a production recipe, not another checkout or simulation")
    if raw["workflow"]["experiment_id"] == original["workflow"]["experiment_id"]:
        raise ValueError("Checkout identity must differ from production")
    result = deepcopy(original)
    workflow = raw["workflow"]
    if set(workflow) - {"experiment_id", "initial_stage", "first_addition_stage"}:
        raise ValueError("Checkout cannot override the production sampling sequence")
    result["workflow"]["experiment_id"] = workflow["experiment_id"]
    for key in ("initial_stage", "first_addition_stage"):
        overrides = workflow.get(key, {})
        allowed = {"control_mode", "max_hours", "interval_minutes", "advance_at_duration"}
        if key == "initial_stage":
            allowed.add("after_monitoring")
        if not isinstance(overrides, dict) or set(overrides) - allowed:
            raise ValueError(f"Unsupported checkout {key} override")
        stage = result["workflow"][key]
        stage.update(deepcopy(overrides))
        # Both checkout stages use the existing fixed-duration scheduler.
        stage.pop("min_completion_hours", None)
        stage.pop("max_measurements", None)
        stage["completion"].pop("max_iterations", None)
    actions = workflow.get("initial_stage", {}).get("after_monitoring")
    if not isinstance(actions, list):
        raise ValueError("Checkout requires its own explicit initial_stage.after_monitoring test transfer")
    doses = [e for e in actions if e.get("action") == "infuse"]
    if len(doses) != 1 or doses[0].get("channel") != 2:
        raise ValueError("Checkout needs exactly one explicit Channel 2 test infusion")
    for key in ("volume_ml", "rate_ml_min"):
        value = doses[0].get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Fill checkout workflow.initial_stage.after_monitoring Channel 2 {key}; no production reagent dose is inherited")
    pump = raw.get("pump", {})
    if (not isinstance(pump, dict) or set(pump) != {"channels"}
            or set(pump["channels"]) != {"2"}
            or set(pump["channels"]["2"]) != {"initial_retained_volume_ml"}):
        raise ValueError("Checkout pump override is only channels.'2'.initial_retained_volume_ml; geometry comes from base_recipe")
    loaded = pump["channels"]["2"]["initial_retained_volume_ml"]
    if isinstance(loaded, bool) or not isinstance(loaded, (int, float)) or not math.isfinite(loaded) or loaded < 0:
        raise ValueError("Fill pump.channels.'2'.initial_retained_volume_ml with the actual loaded test-fluid volume")
    channel = result["pump"]["channels"]["2"]
    channel.update(initial_retained_volume_ml=loaded, rate_ml_min=doses[0]["rate_ml_min"],
                   default_volume_ml=doses[0]["volume_ml"])
    result["checkout"] = dict(raw["checkout"], base_recipe=str(source.resolve()),
                              base_recipe_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    return result


def cycle_lower_bound_seconds(raw):
    """Existing transfer/settle duration, excluding NMR, processing and needle motion."""
    total = 0.
    for event in raw["workflow"]["cycle"]:
        if event["action"] in {"withdraw", "infuse"}:
            total += 60*event["volume_ml"]/event["rate_ml_min"] + raw["workflow"]["pump_extra_seconds"]
        elif event["action"] == "pause":
            total += event["seconds"]
    return total


def write_checkout_report(paths, metadata, events):
    """Evidence only: cannot authorize motion, dosing or change terminal outcome."""
    from ..runtime_state import write_json_atomic
    completed = [e for e in events if e.get("lifecycle_state") == "completed"
                 and e.get("result_classification") == "positively_confirmed"]
    raw = [e for e in events if e["event_type"] == "nmr_retrieved"]
    valid_raw = [e for e in raw if (paths.run_dir/e["result_path"]).is_file()
                 and (paths.run_dir/e["result_path"]).stat().st_size]
    files = [e["result_path"] for e in valid_raw]
    stages = [e for e in events if e["event_type"] == "stage_transition_decision"]
    cleanup = [e for e in events if e["event_type"] == "cycle_status" and e.get("status") == "CLEANUP_COMPLETE"]
    doses = [e for e in events if e["event_type"] == "dose_confirmed"]
    needles = [e for e in events if e["event_type"] == "needle_transition" and e.get("result_classification") == "completed"]
    analysis = [e for e in events if e["event_type"] == "analysis_unavailable" or
                e["event_type"] == "analysis_result" and e.get("result_classification") == "completed"]
    by_stage = {}
    for stage in metadata["stage_names"]:
        by_stage[stage] = dict(
            raw_nmr_files=[e["result_path"] for e in valid_raw if e.get("workflow_phase") == stage],
            analysis_completed_or_documented_unavailable=sum(e.get("workflow_phase") == stage for e in analysis),
            cleanup_completed_cycles=sum(e.get("workflow_phase") == stage for e in cleanup),
            channel1_completed_transfers=sum(e.get("workflow_phase") == stage and e.get("channel") == 1
                and e.get("operation_type") in {"infuse", "withdraw"} for e in completed))
    observations = dict(arduino_completed_commands=len(needles),
        channel1_completed_transfers=sum(e.get("channel") == 1 and e.get("operation_type") in {"infuse","withdraw"} for e in completed),
        raw_nmr_files=files,analysis_completed_or_documented_unavailable=len(analysis),
        stage_outcomes=[{k:e[k] for k in ("workflow_phase","result_classification","stage_elapsed_hours") if k in e} for e in stages],
        confirmed_channel2_test_transfers=len(doses),cleanup_completed_cycles=len(cleanup),by_stage=by_stage)
    enough = (metadata["status"] == "completed" and len(stages) == 2
              and all(e.get("result_classification") == "time_limit_completed" for e in stages)
              and len(files) >= 2 and len(analysis) >= 2 and len(doses) == 1 and len(cleanup) >= 2
              and len(needles) >= 4 and observations["channel1_completed_transfers"] >= 10
              and len(by_stage) == 2 and all(stage["raw_nmr_files"]
                  and stage["analysis_completed_or_documented_unavailable"] >= 1
                  and stage["cleanup_completed_cycles"] >= 1
                  and stage["channel1_completed_transfers"] >= 5 for stage in by_stage.values()))
    mock = metadata["mode"].lower() == "mock"
    payload = dict(experiment_id=metadata["experiment_id"],mode=metadata["mode"],
        purpose="physical_instrument_checkout",mock_only=mock,physical_instruments_verified=False,
        result="mock_validation_passed" if enough and mock else "live_command_evidence_complete" if enough else "checkout_evidence_incomplete",
        operator_observation_required=True,checkout=metadata["checkout"],evidence=observations,
        note="Mock data cannot verify physical instruments. Live command/record evidence does not independently measure needle position, delivered volume or chemical identity.")
    directory = paths.layout.final_qc if paths.layout else paths.run_dir/"final_qc"
    write_json_atomic(directory/"checkout_report.json",payload)
    text = [f"# {metadata['experiment_id']} instrument checkout", "",f"Mode: {metadata['mode']}. Result: {payload['result']}.", "",payload["note"],"",
            "Evidence: the existing instrument journal and raw acquisition files; no inferred successful chemical reaction.","",
            "```json",json.dumps(observations,indent=2),"```", "",
            "Inspect logs/experiment_journal.jsonl, step_1/ and step_2/ NMR data, and channel_2_dose/dose_record.json. Numerical unavailability is distinct from physical-command failure."]
    (directory/"checkout_report.md").write_text("\n".join(text)+"\n",encoding="utf-8")
