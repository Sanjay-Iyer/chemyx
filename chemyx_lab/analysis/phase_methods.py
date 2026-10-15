"""Strict quantitative phase selection; no instrument or network interfaces.

ACME shares the historical validator's exact Nelder-Mead optimizer, DX-derived
direct starting phases, tolerances and 1000-iteration/2000-evaluation budget.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

METHODS = ("stored", "acme", "combined_objective", "ernst_p0", "deep_phaser")
ROOT = Path(__file__).resolve().parents[2]
DEEP_ROOT = ROOT / "offline/resources/deep_phaser"


class PhaseMethodError(ValueError):
    def __init__(self, message, audit):
        super().__init__(message)
        self.phase_audit = audit


def settings(nmr):
    value = nmr.get("processing", {})
    if not isinstance(value, dict) or set(value) - {"phase_method", "phase_comparison"}:
        raise ValueError("nmr.processing supports phase_method and phase_comparison only")
    method = value.get("phase_method", "stored")
    if method not in METHODS:
        raise ValueError(f"Unknown primary phase_method {method!r}; choose {METHODS}")
    comparison = value.get("phase_comparison", {})
    if not isinstance(comparison, dict) or set(comparison) - {"enabled", "methods", "include_unphased_reference"}:
        raise ValueError("Invalid phase_comparison settings")
    for key in ("enabled", "include_unphased_reference"):
        if key in comparison and type(comparison[key]) is not bool:
            raise ValueError(f"phase_comparison.{key} must be boolean")
    methods = comparison.get("methods", list(METHODS))
    if not isinstance(methods, list) or any(m not in METHODS for m in methods) or len(methods) != len(set(methods)):
        raise ValueError("phase_comparison.methods must be unique supported method names")
    return {"phase_method": method, "phase_comparison": {
        "enabled": comparison.get("enabled", False), "methods": methods,
        "include_unphased_reference": comparison.get("include_unphased_reference", True)}}


def deep_inventory(root=None):
    root = Path(root or DEEP_ROOT)
    manifest = root / "resource_manifest.json"
    if not manifest.is_file():
        raise ValueError(f"DEEP Phaser offline resource manifest missing: {manifest}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema") != "chemyx.deep-resources.v1":
        raise ValueError("Unsupported DEEP Phaser resource manifest")
    required = ["node.exe", "tfjs-4.22.0.cjs", "wasm/tf-backend-wasm.js",
                "wasm/tfjs-backend-wasm.wasm", "wasm/tfjs-backend-wasm-simd.wasm",
                "wasm/tfjs-backend-wasm-threaded-simd.wasm", "colmarvista/js/1d.js",
                *[f"colmarvista/saved_model_p{i}/{n}" for i in (0, 1)
                  for n in ("model.json", "group1-shard1of1.bin")]]
    if set(required) - set(data.get("files", {})):
        raise ValueError("DEEP Phaser manifest omits required model/runtime resources")
    for name in data["files"]:
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"DEEP Phaser missing offline resource: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != data.get("files", {}).get(name):
            raise ValueError(f"DEEP Phaser resource checksum mismatch: {name}")
    return data


def preflight(nmr, *, load_models=True):
    effective = settings(nmr)
    import numpy
    import scipy  # noqa: F401
    import nmrglue as ng
    if not callable(ng.proc_autophase._ps_acme_score):
        raise ValueError("Installed nmrglue has no validated ACME objective")
    if effective["phase_method"] in {"acme", "combined_objective", "ernst_p0"}:
        from .phase_optimizers import optimize_phase, ernst_integral_p0, minimize_acme  # noqa: F401
        from .phase_quality import prepare_context  # noqa: F401
    if effective["phase_method"] == "ernst_p0" and not callable(getattr(numpy, "trapezoid", None)):
        raise ValueError("Ernst P0 requires the pinned NumPy with numpy.trapezoid; install the offline lock")
    if effective["phase_method"] == "deep_phaser":
        deep_inventory()
        if load_models:
            deep_request({"preflight_only": True})
    return effective


def deep_request(request, *, resource_root=None, timeout=900):
    root = Path(resource_root or DEEP_ROOT)
    inventory = deep_inventory(root)
    with tempfile.TemporaryDirectory(prefix="si6_deep_") as temp:
        source, output = Path(temp) / "input.json", Path(temp) / "status.json"
        source.write_text(json.dumps(request), encoding="utf-8")
        env = dict(os.environ, DEEP_PHASER_RESOURCE_ROOT=str(root.resolve()), DEEP_PHASER_BACKEND="wasm")
        run = subprocess.run([str(root / "node.exe"), str(ROOT / "scripts/nmr/deep_phaser_offline.cjs"),
                              str(source), str(output)], capture_output=True, text=True,
                             timeout=timeout, env=env)
        status = json.loads(output.read_text()) if output.exists() else {}
        if run.returncode or not status.get("success"):
            raise ValueError(f"DEEP Phaser failed: {status or run.stderr[-2000:]}")
        status["resources"] = inventory
        return status


def apply_phase(ppm, fft, metadata, method, *, resource_root=None):
    """Apply one method to the shared, ascending-ppm, complex FFT, strictly."""
    import numpy as np
    import nmrglue as ng
    from .phase_optimizers import optimize_phase, ernst_integral_p0, minimize_acme, CLASSICAL_OPTIMIZER_SETTINGS
    from .phase_quality import prepare_context

    started = time.perf_counter()
    z = np.asarray(fft, dtype=complex)
    if z.ndim != 1 or not z.size or not np.isfinite(z).all():
        raise ValueError("Invalid complex FFT")
    if method not in METHODS and method != "unphased":
        raise ValueError(f"Unknown phase method: {method}")
    uses_seed = method in {"stored", "acme", "combined_objective"}
    status = {"success": True, "status": "metadata", "inverse_phase": True,
              "p0_deg": float(metadata.get("$PHC0", 0)) if uses_seed else 0.,
              "p1_deg": float(metadata.get("$PHC1", 0)) if uses_seed else 0.}
    if method == "acme":
        initial = [-status["p0_deg"], -status["p1_deg"]]
        phases, objective, iterations, evaluations, warning = minimize_acme(z, initial)
        status = {"success": warning == 0, "status": "converged" if warning == 0 else "nonconverged",
                  "p0_deg": float(phases[0]), "p1_deg": float(phases[1]), "inverse_phase": False,
                  "starting_values_deg": initial, "objective_value": float(objective),
                  "iterations": int(iterations), "function_evaluations": int(evaluations),
                  "optimizer": "nmrglue ACME score; scipy.optimize.fmin",
                  "configuration": dict(CLASSICAL_OPTIMIZER_SETTINGS)}
    elif method == "combined_objective":
        p0 = (-status["p0_deg"] + 180) % 360 - 180
        status = optimize_phase(prepare_context(ppm, z), "combined_objective_v1", (p0, -status["p1_deg"]))
    elif method == "ernst_p0":
        status = ernst_integral_p0(ppm, z)
    elif method == "deep_phaser":
        status = deep_request({"real": z.real[::-1].tolist(), "imaginary": z.imag[::-1].tolist(),
                               "maximum_predictions": 500}, resource_root=resource_root)
    elif method == "unphased":
        status.update(p0_deg=0., p1_deg=0., status="reference_only", inverse_phase=False)
    status.update(method=method, schema="chemyx.phase-method.v1", pivot_fraction=0., phase_denominator=z.size,
                  convention="S[k]*exp(sign*i*pi/180*(P0+P1*k/N)); ascending ppm; index-zero pivot",
                  packages={p: importlib.metadata.version(p) for p in ("nmrglue", "numpy", "scipy")})
    status["implementation"] = {"acme": "phase_optimizers.minimize_acme (historical validation)",
        "combined_objective": "phase_optimizers.combined_objective_v1",
        "ernst_p0": "phase_optimizers.ernst_integral_p0",
        "deep_phaser": "COLMARvista author models/search; deep_phaser_offline.cjs"}.get(method, method)
    status["phase_bounds"] = status.get("configuration", {}).get("bounds_deg")
    status["stored_source_fields"] = ["$PHC0", "$PHC1"] if uses_seed else []
    if not status.get("success"):
        raise PhaseMethodError(f"Selected phase method failed without fallback: {status}", status)
    if status.get("objective_value") is not None and not np.isfinite(status["objective_value"]):
        raise ValueError("Nonfinite optimizer objective; no quantitative result")
    p0, p1 = status["p0_deg"], status["p1_deg"]
    if not np.isfinite([p0, p1]).all():
        raise ValueError("Nonfinite phase parameters; no quantitative result")
    phased = ng.proc_base.ps(z, p0=p0, p1=p1, inv=status["inverse_phase"])
    if phased.shape != z.shape or not np.isfinite(phased).all():
        raise ValueError("Invalid phased spectrum; no quantitative result")
    status["elapsed_s"] = time.perf_counter() - started
    return phased, status
