"""Explicit offline controller fixtures, never selected by a live workflow."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config


class TrendSimulation:
    def __init__(self, settings: dict, source: Path):
        path = config.resolve_repo_path(settings["fixture_file"])
        self.fixture = json.loads(path.read_text(encoding="utf-8"))
        if self.fixture.get("simulation_only") is not True:
            raise ValueError("Trend fixture must be labeled simulation_only")
        self.source = source
        self.seconds = 0.0
        self.origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.acquisition_seconds = float(settings["acquisition_seconds"])
        self.indices = {}
        self.last = None

    def monotonic(self):
        return self.seconds

    def now(self):
        return self.origin + timedelta(seconds=self.seconds)

    def sleep(self, label, seconds):
        self.seconds += seconds

    def acquire(self, nmr_cfg, save_dir, *, label):
        stage = label.rsplit("_", 1)[0]
        index = self.indices.get(stage, 0)
        values = self.fixture["stages"][stage]
        if index >= len(values):
            raise ValueError(f"Simulation fixture exhausted in {stage}")
        self.indices[stage] = index + 1
        self.sleep("synthetic acquisition", self.acquisition_seconds)
        value = values[index]
        self.last = {"area": float(value), "scans": nmr_cfg.scans, "simulation_only": True}
        text = self.source.read_text(encoding="latin-1")
        stamp = self.now().strftime("%Y/%m/%d %H:%M:%S%z")
        text, count = re.subn(r"(?m)^##LONG DATE=.*$", "##LONG DATE=" + stamp, text)
        if count != 1:
            raise ValueError("Simulation source needs one LONG DATE header")
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"{label}.dx"
        path.write_text(text, encoding="latin-1")
        return path

    def process(self, path, paths, dataset):
        output = paths.run_dir / "processed_nmr" / f"{path.stem}_simulated_controller_fixture"
        output.mkdir(parents=True, exist_ok=False)
        (output / "SIMULATION_ONLY.json").write_text(json.dumps(self.last), encoding="utf-8")
        return output

    def analyze(self, path, processed, paths, analysis, metadata):
        # Controller fixtures test trend decisions and instrument ordering.
        # Production process_fid/QC is independently regression-tested; fixture
        # values are never presented as a physically processed spectrum.
        value = json.loads((processed / "SIMULATION_ONLY.json").read_text())
        row = dict(metadata, file=path.name, peak_area=value["area"],
                   peak_ppm=metadata["target_ppm"], peak_clear=True, measurement_valid=True,
                   area_uncertainty=0.01, peak_height=value["area"], snr=100.0, prominence_snr=100.0,
                   metric_source="SIMULATION ONLY: deterministic controller trend fixture", error="")
        return row, []
