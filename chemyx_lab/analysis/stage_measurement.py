"""Fixed target-window metrics from existing production phase audit arrays."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np


def fixed_window_measurement(source: Path, processed: Path, row: dict, analysis: dict, prior: list[dict], reference: dict | None = None) -> dict:
    evidence = list(processed.glob("phase_audit/*/*spectral_evidence.npz"))
    if len(evidence) != 1:
        raise ValueError("Exactly one retained production spectral evidence file required")
    metadata_name = evidence[0].name.removesuffix("spectral_evidence.npz") + "processing_metadata.json"
    audit = json.loads(evidence[0].with_name(metadata_name).read_text(encoding="utf-8"))
    if audit["raw_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("Retained spectral evidence does not match raw acquisition")
    if audit["parameters"].get("normalization") == "max":
        raise ValueError("Per-spectrum max normalization prevents comparable quantitative areas")
    if audit["phase"]["method"] == "none":
        raise ValueError("Unphased spectrum is not accepted as quantitative evidence")
    if not all(math.isfinite(float(audit["phase"][key])) for key in ("p0_deg", "p1_deg")):
        raise ValueError("Finite retained phase parameters required")
    with np.load(evidence[0], allow_pickle=False) as arrays:
        x = arrays["region_ppm"]
        y = arrays["regional_quantitative"]
        target = float(row["target_ppm"])
        half = float(analysis["integration_window_ppm"])
        mask = abs(x - target) <= half
        side = (abs(x - target) > half) & (abs(x - target) <= float(analysis["plot_window_ppm"]))
        if len(x) != len(y) or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or mask.sum() < 5 or side.sum() < 10 or np.any(np.diff(x) <= 0):
            raise ValueError("Finite ordered corrected trace and target/noise windows required")
        noise = float(np.median(abs(y[side] - np.median(y[side]))) / 0.67448975)
        height = float(max(abs(y[mask])))
        if height <= 0 or noise <= 0:
            raise ValueError("Zero trace or unmeasurable noise is not a valid low signal")
        integrate = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
        signed = float(integrate(y[mask], x[mask]))
        negative_area = float(integrate(np.maximum(-y[mask], 0), x[mask]))
        uncertainty = float(analysis["measurement_qc"]["noise_multiplier"]) * noise * (2 * half)
    reference = reference or (prior[0] if prior else row)
    if not reference.get("peak_clear") or float(reference["peak_area"]) <= 0:
        raise ValueError("A QC-passing initial detected peak is required")
    reference_height = float(reference.get("reference_height", height))
    reference_area = float(reference.get("qc_reference_area", reference.get("completion_area", max(signed, 0))))
    qc = analysis["measurement_qc"]
    if not math.isfinite(reference_height) or reference_height <= 0 or not math.isfinite(reference_area):
        raise ValueError("Finite positive initial QC reference required")
    if reference_area <= 0 or noise / reference_height > qc["max_noise_fraction"]:
        raise ValueError("Spectrum noise exceeds initial-signal QC bound")
    if uncertainty / reference_area > qc["max_area_uncertainty_fraction"]:
        raise ValueError("Fixed-window area uncertainty exceeds initial-signal QC bound")
    if signed < -uncertainty:
        raise ValueError("Negative target integral exceeds uncertainty; phase/baseline review required")
    if negative_area > uncertainty:
        raise ValueError("Negative target lobes exceed noise bound; phase/baseline review required")
    # An undetected peak is accepted only with independent, bounded corrected
    # integral evidence. Rejected detected peaks cannot silently become absence.
    if not row["peak_clear"]:
        if row.get("rejected_candidate_count", 0):
            raise ValueError("Rejected peak candidate requires spectrum/phase review, not an absence decision")
        if reference is row or max(signed, 0) + uncertainty > reference_area * qc["undetected_max_fraction"]:
            raise ValueError("Peak detection failed without independently bounded low-signal evidence")
        if "no QC-passing peak" not in row.get("qc_failure_reasons", ""):
            raise ValueError("Detected peak failed configured QC; not a valid absence")
    row.update(completion_area=max(signed, 0), area_uncertainty=uncertainty, measurement_valid=True,
               noise=noise, reference_height=reference_height, qc_reference_area=reference_area,
               signal_classification="detected" if row["peak_clear"] else "bounded_low_signal",
               metric_source="production process_fid regional_quantitative fixed target integral",
               phase_evidence_path=str(evidence[0]), raw_sha256=audit["raw_sha256"])
    return row
