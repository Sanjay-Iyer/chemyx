"""Synthetic complex-FFT validation through primary phase and real controller.

DX files are explicitly metadata carriers, not synthetic raw FIDs. The full
complex Lorentzian spectrum is built independently and receives selected phase
correction before unchanged baseline, moving area, endpoints and mock hardware.
Archived-FID benchmarks separately exercise decoding and FFT construction.
"""
import copy
import json
from pathlib import Path
import numpy as np
from .si6_synthetic_analysis import SpectralWorkflowSimulation
from ..analysis.nmr import read_jcamp_fid
from ..analysis.nmr_validation import production_args
from ..analysis.phase_comparison import evaluate_shared
from ..analysis.solvent_presence import evaluate_solvent_spectrum
from ..workflows import three_instrument_si6 as si6
from .. import config


def complex_spectrum(area, *, seed=4100, drift=0., noise=1., solvent=True, overlap=False):
    x = np.linspace(-5, 15, 16384)
    rng = np.random.default_rng(seed)
    z = rng.normal(0, noise, len(x)) + 1j*rng.normal(0, noise, len(x))
    def line(center, integrated_area, gamma=.015):
        return integrated_area / np.pi / (gamma + 1j*(x-center))
    if solvent:
        z += line(2.08, 6000.) + line(6.99, 6000.)
    z += line(5.8+drift, area)
    if overlap:
        z += line(5.85, area*.5)
    # Exact stored inverse recovers this synthetic convention; automatic
    # methods must estimate from the same phase-distorted FFT themselves.
    z *= np.exp(1j*np.deg2rad(23. - 47.*np.arange(len(x))/len(x)))
    return x, z


class PrimaryPhaseEngine:
    def __init__(self, method):
        self.method = method

    def measure(self, source, output, metadata, analysis, prior, *, area, seed, drift_ppm=0.):
        x, fft = complex_spectrum(area, seed=seed, drift=drift_ppm)
        fid = read_jcamp_fid(source)
        headers = dict(fid.metadata, **{"$PHC0": "23", "$PHC1": "-47"})
        args = production_args(source)
        row, _ = evaluate_shared(source, x, fft, headers, args, analysis, self.method, output, prior, role="primary")
        row.update(metadata)
        row.update(phase_method=self.method, generated_area=area, simulation_only=True,
                   metric_source="SIMULATION ONLY: complex synthetic FFT -> selected primary phase -> production baseline/candidates/moving area")
        return row, []


def run_primary_workflow(output, method="acme", *, missing_solvent=False, fail_primary=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    settings = list(si6.prepare(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml",
                               config.REPO_ROOT / "configs/machines/si6_real_COM4.yaml",
                               config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True))
    raw = settings[0]
    raw["nmr"]["processing"] = {"phase_method": method}
    if missing_solvent:
        # Match the updated real recipe's 48h / 4h fallback deadlines.
        raw["workflow"]["first_addition_stage"]["max_hours"] = 4
    raw["output"]["run_root_dir"] = str(output / "experiments")
    with si6.open_services(*settings, identity=si6.RunIdentity("si6", True)) as services:
        engine = PrimaryPhaseEngine(method)
        sim = SpectralWorkflowSimulation(raw["simulation"], si6.MOCK_NMR_FIXTURE, engine)
        # Sufficient explicitly simulated slots for the real 48h timed fallback.
        sim.fixture["stages"] = {k: list(v) + [v[-1]]*100 for k, v in sim.fixture["stages"].items()}
        services.simulation = sim
        services.acquire, services.process, services.analyze, services.sleep = sim.acquire, sim.process, sim.analyze, sim.sleep
        clocks = []
        original_record = services.recorder.record
        def observed_record(name, **fields):
            result = original_record(name, **fields)
            if name in {"monitoring_stage_started", "stage_transition_decision"}:
                clocks.append({"event_type": name, "stage": fields.get("workflow_phase"),
                               "virtual_time": sim.now().isoformat()})
            return result
        services.recorder.record = observed_record
        if fail_primary:
            def broken(*args, **kwargs):
                raise RuntimeError("TEST selected primary phase failed; no spectrum or area available")
            services.process = broken
        # Missing solvent is injected at the independent sample-presence layer;
        # selected phase still preflights, but blank samples bypass target areas.
        original_check = si6.solvent_presence
        if missing_solvent:
            def absent(source, settings):
                return {"present": False, "reason": "TEST missing full-spectrum solvent pair", "windows": []}
            si6.solvent_presence = absent
        try:
            outcome = si6.run_experiment(services)
        finally:
            si6.solvent_presence = original_check
        (output / "virtual_stage_clock.json").write_text(json.dumps({
            "simulation_only": True, "timing_source": "test virtual operational clock; not measured acquisition time",
            "events": clocks}, indent=2))
        return {"outcome": outcome, "run_dir": services.paths.run_dir, "rows": copy.deepcopy(sim.rows),
                "journal": services.paths.journal_jsonl, "virtual_stage_clock": clocks}
