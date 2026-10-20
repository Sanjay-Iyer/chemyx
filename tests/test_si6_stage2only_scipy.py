"""08 timing/HOME invariants and complete separation from optional analysis."""
import ast
import csv
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from chemyx_lab import config
from chemyx_lab.workflows import si6_fixed_time as fixed, si6_stage2only as original
from chemyx_lab.workflows import si6_stage2only_scipy as stage2
from chemyx_lab.workflows import si6_stage2only_scipy_analysis as review
from test_si6_stage2only import live
from test_si6_stage2only_live_analysis import add_observation

PROTECTED = ["scripts/06_si6_stage2only_run.py", "scripts/07_analyze_stage2only.py",
             "chemyx_lab/workflows/si6_stage2only.py", "chemyx_lab/workflows/si6_stage2only_analysis.py",
             "chemyx_lab/analysis/stage2only_reanalysis.py", "configs/nmr/analysis.yaml"]
BEFORE = {p: hashlib.sha256((config.REPO_ROOT / p).read_bytes()).hexdigest() for p in PROTECTED}


@pytest.fixture
def scipy_live(live, monkeypatch):
    # Every transport remains the old fixture's explicit mock, even with mock=False.
    monkeypatch.setattr(stage2, "controller_session", original.controller_session)
    monkeypatch.setattr(stage2, "PortProcessLock", original.PortProcessLock)
    monkeypatch.setattr(stage2, "start_review_worker", original.start_review_worker)
    live.run_scipy = lambda **kwargs: stage2.run(live.raw, live.cfg, live.settings, live.nmr, mock=False,
        output=live.tmp / "scipy_run", clock=fixed.SimulatedClock(),
        acquisition=kwargs.get("acquisition", lambda *_: {"status": "NMR_MISSING"}))
    return live


def test_same_recipe_scheduler_and_home_validation_as_06_with_separate_output():
    raw, _, _, nmr = stage2.load_settings()
    old, *_ = original.load_settings()
    assert {k: v for k, v in raw.items() if k != "output"} == {k: v for k, v in old.items() if k != "output"}
    assert raw["output"]["run_root_dir"] == "results/stage2only_scipy"
    assert nmr.scans == 8
    for name in ("load_settings", "run_stage2only"):
        assert ast.dump(ast.parse(inspect.getsource(getattr(stage2, name)))) == ast.dump(ast.parse(inspect.getsource(getattr(original, name))))
    assert stage2.new_identity().startswith("SI6_STAGE2ONLY_SCIPY_")


@pytest.mark.parametrize("fault", ["cannot_start", "crashed", "hung", "malformed_json", "early_completion"])
def test_analysis_cannot_shorten_or_block_the_full_6_hour_schedule(scipy_live, monkeypatch, fault):
    def sample(self, _):
        self.clock.sleep(60)
        return {"status": "NMR_FAILED", "error": "NMR unavailable; keep time"}
    monkeypatch.setattr(stage2.Stage2OnlyRig, "sample", sample)
    def launch(output):
        if fault == "cannot_start":
            raise ImportError("SciPy unavailable")
        folder = Path(output) / "analysis_live"
        folder.mkdir()
        (folder / "status.json").write_text("{bad JSON" if fault == "malformed_json" else json.dumps({"status": fault, "stop_run": True, "complete": True}))
        return {"status": "STARTED", "index": str(folder / "index.html"), "controls_run": False}
    monkeypatch.setattr(stage2, "start_review_worker", launch)
    result = scipy_live.run_scipy()
    assert result["status"] == "COMPLETE" and result["physical_cleanup"]
    assert [r["scheduled_seconds"] for r in result["observations"]] == list(range(0, 21601, 1800))
    assert result["stages"][0]["elapsed_seconds"] >= 21600
    assert not result["analysis_controls_run"] and not result["dose_performed"]
    assert result["primary_area_method"] == "scipy_width95_prominence8"


def test_real_sampling_primitives_preserve_homes_and_channel2(scipy_live):
    before1 = json.loads((scipy_live.root / "channel_1_state.json").read_text())
    before2 = (scipy_live.root / "channel_2_state.json").read_bytes()
    result = scipy_live.run_scipy()
    assert result["status"] == "COMPLETE" and len(result["observations"]) == 13
    after1 = json.loads((scipy_live.root / "channel_1_state.json").read_text())
    assert before1["home"] == after1["home"] and after1["current_displacement_nl"] == 0
    assert (scipy_live.root / "channel_2_state.json").read_bytes() == before2
    journal = [json.loads(line) for line in (Path(result["results"]) / "operation_journal.jsonl").read_text().splitlines()]
    transfers = [r for r in journal if r.get("operation_type") in {"infuse", "withdraw"}]
    assert transfers and all(r["channel"] == 1 for r in transfers)
    assert not [r for r in journal if r["event_type"].startswith("dose_")]


def test_launcher_never_imports_numerics_waits_or_passes_instrument_handles(tmp_path, monkeypatch):
    captured = []
    class Child:
        pid = 123
        def wait(self, *_, **__):
            pytest.fail("Instrument controller waited for analysis")
        poll = communicate = wait
    monkeypatch.setattr(stage2.subprocess, "Popen", lambda command, **kw: captured.append((command, kw)) or Child())
    result = stage2.start_review_worker(tmp_path)
    command, options = captured[0]
    assert "chemyx_lab.workflows.si6_stage2only_scipy_analysis" in command
    assert options["close_fds"] and options["stdin"] == subprocess.DEVNULL
    assert options["env"]["OPENBLAS_NUM_THREADS"] == "1"
    assert result["controls_run"] is False
    imports = [node.module or "" for node in ast.walk(ast.parse(inspect.getsource(stage2))) if isinstance(node, ast.ImportFrom)]
    assert not any("analysis" in name or "scipy_peak" in name for name in imports)


def test_worker_recovers_from_analysis_timeout_and_uses_same_scipy_processor(tmp_path):
    run = tmp_path / "run"
    add_observation(run, 1)
    add_observation(run, 2)
    calls = []
    def processor(*args):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("processor", 600)
        return args[1]
    worker = review.ReviewWorker(run, processor=processor)
    worker.tick()
    assert [j["status"] for j in worker.jobs] == ["FAILED", "COMPLETE"]
    assert len(worker.sources) == 2


def test_worker_command_explicit_inputs_profile_dataset_and_bounded_timeout(tmp_path):
    run = tmp_path / "run"
    raw = add_observation(run, 1)
    snapshot = review.review.fresh_snapshot(run / "analysis_live", 1)
    captured = []
    review.process_snapshot(run, snapshot, [review.review.metadata_time(raw)], "Actual Run Stage 2",
        runner=lambda command, **kw: captured.append((command, kw)) or SimpleNamespace(returncode=0))
    command, options = captured[0]
    assert Path(command[1]).name == "process_stage2only_scipy.py"
    assert command[2:command.index("--config")] == [str(raw)]
    assert command[command.index("--profile") + 1].endswith("stage2only_scipy.yaml")
    assert command[command.index("--dataset-display-name") + 1] == "Actual Run Stage 2"
    assert options["timeout"] == 600


def test_hung_numerical_process_is_killed_without_controller_involvement(tmp_path, monkeypatch):
    raw = add_observation(tmp_path, 1)
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(review, "PROCESSOR", sleeper)
    snapshot = review.review.fresh_snapshot(tmp_path / "analysis_live", 1)
    with pytest.raises(subprocess.TimeoutExpired):
        review.process_snapshot(tmp_path, snapshot, [review.review.metadata_time(raw)], "Test", timeout=.2)


def test_08_mock_cli_completes_all_slots_without_any_instrument(tmp_path):
    out = tmp_path / "mock_run"
    proc = subprocess.run([sys.executable, str(config.REPO_ROOT / "scripts/08_si6_stage2only_scipy_run.py"),
        "--mock", "--output-dir", str(out)], cwd=config.REPO_ROOT, text=True, capture_output=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((out / "report.json").read_text())
    assert report["workflow"] == "stage2only_scipy" and report["status"] == "COMPLETE"
    assert len(report["observations"]) == 13
    assert report["live_analysis"]["status"] == "MOCK_NOT_STARTED"


def test_06_07_and_existing_processors_remain_byte_identical():
    assert {p: hashlib.sha256((config.REPO_ROOT / p).read_bytes()).hexdigest() for p in BEFORE} == BEFORE


def test_instrument_runner_completes_without_scientific_analysis_dependencies(tmp_path):
    code = """import sys
class BlockScience:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'numpy', 'scipy', 'matplotlib', 'nmrglue'}:
            raise ModuleNotFoundError('Analysis dependency deliberately unavailable: ' + fullname)
sys.meta_path.insert(0, BlockScience())
from chemyx_lab.workflows.si6_stage2only_scipy import main
raise SystemExit(main(['--mock', '--output-dir', sys.argv[1]]))
"""
    out = tmp_path / "no_analysis_dependencies"
    result = subprocess.run([sys.executable, "-c", code, str(out)], cwd=config.REPO_ROOT,
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((out / "report.json").read_text())["status"] == "COMPLETE"
