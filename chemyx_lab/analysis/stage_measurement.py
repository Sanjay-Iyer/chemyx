"""Continuity tracking over lossless production peak candidates; no fixed integral."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from .peak_tracking import tracking_config, identification_config
from .phase_audit import write_json


class TrackingFailure(ValueError):
    def __init__(self, status, message, evidence):
        super().__init__(f"{status}: {message}")
        self.status, self.evidence = status, evidence


class PeakEvidenceError(RuntimeError):
    """Missing/corrupt processing artifacts: no area signal exists to record."""


def credible_candidate(p, cfg):
    return (p["peak_height"] > 0
            and (not cfg["snr"]["enabled"] or p["snr"] >= cfg["snr"]["minimum"])
            and (not cfg["prominence"]["enabled"] or p["prominence_snr"] >= cfg["prominence"]["minimum_snr"]))


def select_candidate(candidates, cfg, target, previous=None, reference=None):
    audit = []
    for candidate in candidates:
        p = dict(candidate)
        center = float(p["interpolated_ppm"])
        tolerance = max(1.e-12, .5*float(p.get("ppm_resolution", 0)))
        tests = {
            "search": abs(center-target) <= cfg["search"]["half_width_ppm"]+1.e-12,
            "previous_shift": previous is None or abs(center-previous) <= cfg["previous_shift"]["maximum_ppm"]+tolerance,
            "reference_shift": reference is None or abs(center-reference) <= cfg["reference_shift"]["maximum_ppm"]+tolerance,
            "snr": p["snr"] >= cfg["snr"]["minimum"],
            "prominence": p["prominence_snr"] >= cfg["prominence"]["minimum_snr"],
            "absolute_prominence": p["prominence"] >= cfg["absolute_prominence"]["minimum"],
            "width": cfg["width"]["minimum_ppm"] <= p["width_ppm"] <= cfg["width"]["maximum_ppm"],
            "height": p["peak_height"] >= cfg["height"]["minimum"],
            "area": p["positive_area"] >= cfg["area"]["minimum"],
            "shoulder": p["asymmetry"] <= cfg["shoulder"]["maximum_asymmetry"],
            "slope": p["flank_fraction"] >= cfg["slope"]["minimum_flank_fraction"],
            "shape": p["interpolation_quality"] >= cfg["shape"]["minimum_interpolation_quality"],
            "neighbor": all(abs(center-q["interpolated_ppm"]) >= cfg["neighbor"]["minimum_distance_widths"]*max(p["width_ppm"],q["width_ppm"]) for q in candidates if q is not candidate and credible_candidate(q, cfg)),
        }
        p.update(rejection_reasons=[name for name, passed in tests.items() if cfg[name]["enabled"] and not passed], selected=False)
        audit.append(p)
    passing = [p for p in audit if not p["rejection_reasons"]]
    identity = previous if previous is not None else target
    passing.sort(key=lambda p: (abs(p["interpolated_ppm"]-identity), -p["snr"]))
    credible = [p for p in audit if credible_candidate(p, cfg) and "search" not in p["rejection_reasons"]]
    if cfg["continuity"]["enabled"] and passing and previous is not None and credible:
        nearest = min(credible, key=lambda p: abs(p["interpolated_ppm"]-previous))
        if nearest is not passing[0]:
            passing = []
    if passing:
        passing[0]["selected"] = True
        return passing[0], audit
    return None, audit


def _tracked_peak_measurement(source: Path, processed: Path, row: dict, analysis: dict, prior: list[dict], reference: dict | None = None) -> dict:
    cfg = tracking_config(analysis.get("peak_tracking"))
    result_path = processed / "tracking_evidence.json"
    details = {"acquired_at": row.get("acquired_at"), "timestamp_source": row.get("timestamp_source"), "filters": cfg, "candidates": []}
    def fail(status, message):
        details.update(tracking_status=status, measurement_valid=False, reason=message)
        write_json(result_path, details)
        raise TrackingFailure(status, message, details)
    files = list(processed.glob("phase_audit/*/*spectral_evidence.npz"))
    if len(files) != 1:
        fail("PROCESSING_FAILED", "Exactly one retained production spectral evidence file required")
    metadata_name = files[0].name.removesuffix("spectral_evidence.npz") + "processing_metadata.json"
    audit = json.loads(files[0].with_name(metadata_name).read_text(encoding="utf-8"))
    if audit["raw_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
        fail("PROCESSING_FAILED", "Retained spectral evidence does not match raw acquisition")
    if audit["parameters"].get("normalization") == "max":
        fail("MEASUREMENT_QC_FAILED", "Per-spectrum max normalization prevents comparable quantitative areas")
    if audit["phase"]["method"] == "none" or not all(math.isfinite(float(audit["phase"][k])) for k in ("p0_deg", "p1_deg")):
        fail("MEASUREMENT_QC_FAILED", "Finite retained phase correction required")
    if "peak_candidates" not in audit:
        fail("PROCESSING_FAILED", "Lossless production peak candidates missing; rerun current processor")
    with np.load(files[0], allow_pickle=False) as arrays:
        x, y = arrays["region_ppm"], arrays["regional_quantitative"]
    if len(x) != len(y) or len(x)<10 or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or np.any(np.diff(x)<=0):
        fail("MEASUREMENT_QC_FAILED", "Finite ordered corrected trace required")
    previous = prior[-1] if prior else reference
    previous_center = float(previous["peak_ppm"]) if previous and previous.get("peak_ppm") is not None else None
    reference_center = float(reference["peak_ppm"]) if reference and reference.get("peak_ppm") is not None else previous_center
    details.update(previous_peak_ppm=previous_center, reference_peak_ppm=reference_center,
                   target_ppm=row["target_ppm"], source_path=str(source), phase_evidence_path=str(files[0]))
    candidates_input = audit["peak_candidates"]
    selected, candidates = select_candidate(candidates_input, cfg, float(row["target_ppm"]), previous_center, reference_center)
    if cfg["continuity"]["enabled"] and selected and previous and previous.get("candidate_audit"):
        old_candidates = json.loads(previous["candidate_audit"])
        old_neighbors = [p for p in old_candidates if not p["selected"] and not p["rejection_reasons"]]
        if any(abs(selected["interpolated_ppm"]-p["interpolated_ppm"]) < abs(selected["interpolated_ppm"]-previous_center) for p in old_neighbors):
            selected["selected"] = False
            selected["rejection_reasons"].append("previous_neighbor_identity")
            selected = None
    details["candidates"] = candidates
    side = np.ones(len(x), dtype=bool)
    for p in candidates:
        if credible_candidate(p, cfg):
            side &= abs(x-p["interpolated_ppm"]) > 2*p["width_ppm"]
    if side.sum()<10:
        fail("MEASUREMENT_QC_FAILED", "Insufficient independent noise points")
    noise = float(np.median(abs(y[side]-np.median(y[side])))/.67448975)
    qc = analysis["measurement_qc"]
    reference = reference or (prior[0] if prior else None)
    if selected:
        center, area, height = selected["interpolated_ppm"], selected["positive_area"], selected["peak_height"]
        left, right = selected["integration_left_ppm"], selected["integration_right_ppm"]
        if right <= left:
            fail("MEASUREMENT_QC_FAILED", "Tracked peak is undersampled; positive integration width required")
        uncertainty = qc["noise_multiplier"]*noise*(right-left)
        if selected["negative_area"] > uncertainty or selected["signed_area"] < -uncertainty:
            fail("MEASUREMENT_QC_FAILED", "Negative tracked-peak lobes exceed uncertainty; phase/baseline review required")
        classification, status = "detected", "TRACKED_PEAK_FOUND"
    else:
        credible = [p for p in candidates if credible_candidate(p, cfg) and "search" not in p["rejection_reasons"]]
        if credible or previous is None or reference is None:
            fail("TRACKED_PEAK_LOST", "No eligible continuous peak; inspect candidate rejection reasons")
        center = previous_center
        half = (previous["integration_right_ppm"]-previous["integration_left_ppm"])/2
        left, right = center-half, center+half
        expected = (x>=left) & (x<=right)
        if expected.sum()<2 or np.min(y[expected]) < -qc["noise_multiplier"]*noise:
            fail("MEASUREMENT_QC_FAILED", "Negative expected peak trace; phase/baseline review required")
        detection_height = max(cfg["snr"]["minimum"] if cfg["snr"]["enabled"] else 0, cfg["prominence"]["minimum_snr"] if cfg["prominence"]["enabled"] else 0, qc["noise_multiplier"])*noise
        upper = (detection_height+qc["noise_multiplier"]*noise)*(right-left)
        if upper > reference["qc_reference_area"]*qc["undetected_max_fraction"]:
            fail("MEASUREMENT_QC_FAILED", "Nondetection upper area bound exceeds reference limit")
        area = uncertainty = upper/2
        height = detection_height
        classification, status = "bounded_nondetection", "TRACKED_PEAK_BELOW_DETECTION"
    if not all(math.isfinite(float(v)) for v in (center, area, height, left, right, uncertainty)):
        fail("MEASUREMENT_QC_FAILED", "Finite tracked peak values required")
    reference_height = float(reference.get("reference_height", reference.get("peak_height", height))) if reference else height
    reference_area = float(reference.get("qc_reference_area", reference["peak_area"])) if reference else area
    if not all(math.isfinite(v) and v>0 for v in (noise, reference_height, reference_area)):
        fail("MEASUREMENT_QC_FAILED", "Finite positive initial QC reference and measurable noise required")
    if noise/reference_height > qc["max_noise_fraction"]:
        fail("MEASUREMENT_QC_FAILED", "Spectrum noise exceeds initial-signal QC bound")
    if uncertainty/reference_area > qc["max_area_uncertainty_fraction"]:
        fail("MEASUREMENT_QC_FAILED", "Tracked area uncertainty exceeds initial-signal QC bound")
    row.update(file=source.name, peak_area=float(area), peak_ppm=float(center), peak_height=float(height), peak_clear=selected is not None,
               peak_position_source="identified interpolated center" if selected else "last known center; current peak undetected",
               snr=selected["snr"] if selected else 0, prominence_snr=selected["prominence_snr"] if selected else 0,
               prominence=selected["prominence"] if selected else 0, baseline=selected["baseline"] if selected else None,
               width_ppm=selected["width_ppm"] if selected else previous.get("width_ppm"),
               integration_left_ppm=left, integration_right_ppm=right, area_uncertainty=uncertainty,
               previous_peak_ppm=previous_center, shift_previous_ppm=center-previous_center if previous_center is not None else 0,
               shift_reference_ppm=center-reference_center if reference_center is not None else 0,
               measurement_valid=True, tracking_status=status, signal_classification=classification, noise=noise,
               reference_height=reference_height, qc_reference_area=reference_area, candidate_count=len(candidates),
               candidate_audit=json.dumps(candidates), qc_failure_reasons="", tracking_evidence_path=str(result_path),
               metric_source="production peak finder positive_area; continuity-tracked moving bounds" if selected else "noise-bounded censored tracked peak area",
               phase_evidence_path=str(files[0]), raw_sha256=audit["raw_sha256"])
    details.update({k:v for k,v in row.items() if k != "candidate_audit"})
    write_json(result_path, details)
    return row


def tracked_peak_measurement(source: Path, processed: Path, row: dict, analysis: dict, prior: list[dict], reference: dict | None = None) -> dict:
    """Persist processing failures as well as quantitative/identity failures."""
    try:
        return _tracked_peak_measurement(source, processed, row, analysis, prior, reference)
    except TrackingFailure:
        raise
    except (OSError, KeyError, TypeError, IndexError, ValueError, OverflowError) as exc:
        details = dict(tracking_status="PROCESSING_FAILED", measurement_valid=False,
                       acquired_at=row.get("acquired_at"), timestamp_source=row.get("timestamp_source"),
                       source_path=str(source), reason=str(exc), candidates=[])
        write_json(processed / "tracking_evidence.json", details)
        raise TrackingFailure("PROCESSING_FAILED", str(exc), details) from exc


def tracked_peak_area_observation(source: Path, processed: Path, row: dict, analysis: dict, prior: list[dict]) -> dict:
    """Live moving area, reusing select_candidate and production positive_area.

    No diagnostic threshold is read here. Missing/lost peaks have
    zero area. Keep the last detected identity across nondetections and stages.
    The separate historical tracked_peak_measurement remains an offline audit.
    """
    cfg = identification_config(analysis["peak_finding"])
    detected = [r for r in prior if r.get("peak_ppm") is not None and r.get("signal_classification") == "detected"]
    previous = detected[-1] if detected else None
    previous_center = float(previous["peak_ppm"]) if previous else None
    reference_center = float(detected[0]["peak_ppm"]) if detected else None
    evidence_path = processed / "tracking_evidence.json"
    row.update(file=source.name, peak_area=0.0, peak_ppm=None, peak_height=None,
               peak_clear=False, signal_classification="peak_not_detected", tracking_status="PEAK_NOT_DETECTED",
               peak_position_source="not detected; last identity retained for next search",
               integration_left_ppm=None, integration_right_ppm=None, snr=None, width_ppm=None,
               previous_peak_ppm=previous_center, observation_recorded=True,
               metric_source="zero for nondetection; otherwise production moving trapezoidal positive_area",
               tracking_evidence_path=str(evidence_path), diagnostic_notes="", candidate_audit="[]")
    candidates = []
    try:
        files = list(processed.glob("phase_audit/*/*spectral_evidence.npz"))
        if len(files) != 1:
            raise ValueError("Exactly one retained production spectral evidence file required")
        audit = json.loads(files[0].with_name(files[0].name.removesuffix("spectral_evidence.npz") + "processing_metadata.json").read_text(encoding="utf-8"))
        if audit["raw_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError("Retained spectral evidence does not match raw acquisition")
        with np.load(files[0], allow_pickle=False) as arrays:
            x, y = arrays["region_ppm"], arrays["regional_quantitative"]
        if len(x) != len(y) or len(x) < 2 or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or np.any(np.diff(x) <= 0):
            raise ValueError("Retained processed trace is corrupt or has no numerical area signal")
        selected, candidates = select_candidate(audit["peak_candidates"], cfg, float(row["target_ppm"]), previous_center, reference_center)
        if cfg["continuity"]["enabled"] and selected and previous:
            old_neighbors = [p for p in json.loads(previous.get("candidate_audit", "[]")) if not p["selected"] and not p["rejection_reasons"]]
            if any(abs(selected["interpolated_ppm"]-p["interpolated_ppm"]) < abs(selected["interpolated_ppm"]-previous_center) for p in old_neighbors):
                selected["selected"] = False
                selected["rejection_reasons"].append("previous_neighbor_identity")
                selected = None
        row.update(phase_evidence_path=str(files[0]), raw_sha256=audit["raw_sha256"],
                   phase_method=audit["phase"]["method"], phase_p0_deg=audit["phase"].get("p0_deg"),
                   phase_p1_deg=audit["phase"].get("p1_deg"))
        for key in ("phase_p0_deg", "phase_p1_deg"):
            if row[key] is not None and not math.isfinite(float(row[key])):
                row[key] = None
                row["diagnostic_notes"] = "nonfinite phase metadata; retrospective review required"
        if selected:
            area = float(selected["positive_area"])
            if not math.isfinite(area) or area < 0:
                raise ValueError("Production peak area is not finite and nonnegative")
            center = float(selected["interpolated_ppm"])
            row.update(peak_area=area, peak_ppm=center, peak_height=selected["peak_height"],
                       peak_clear=True, signal_classification="detected", tracking_status="TRACKED_PEAK_FOUND",
                       peak_position_source="identified interpolated center",
                       integration_left_ppm=selected["integration_left_ppm"], integration_right_ppm=selected["integration_right_ppm"],
                       snr=selected["snr"], prominence=selected["prominence"], prominence_snr=selected["prominence_snr"],
                       width_ppm=selected["width_ppm"], baseline=selected["baseline"],
                       asymmetry=selected["asymmetry"], negative_area=selected["negative_area"],
                       shift_previous_ppm=center-previous_center if previous_center is not None else 0.0,
                       shift_reference_ppm=center-reference_center if reference_center is not None else 0.0)
    except (ValueError, KeyError, TypeError, IndexError, OverflowError, OSError) as exc:
        # A failed processor/corrupt artifact supplies no area signal. It is an
        # operational failure, distinct from nondetection in a processed trace.
        write_json(evidence_path, {"tracking_status": "PROCESSING_EVIDENCE_UNAVAILABLE", "reason": str(exc)})
        raise PeakEvidenceError(str(exc)) from exc
    row.update(candidate_count=len(candidates), candidate_audit=json.dumps(candidates))
    write_json(evidence_path, dict(row, candidates=candidates, identity_parameters=cfg))
    return row
