"""Final release invariants; all transports are fake or explicitly prohibited."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.analysis.solvent_presence import DEFAULTS, evaluate_solvent_spectrum, record_sample_presence
from chemyx_lab.analysis.stage_completion import completion_evidence
from chemyx_lab.runtime_journal import discover_git_commit
from chemyx_lab.workflows import three_instrument_si6 as si6, si6_automated_nmr as base

ROOT = config.REPO_ROOT


def load_script(name, monkeypatch):
    path = ROOT / name
    monkeypatch.syspath_prepend(str(path.parent))
    if name.startswith("arduino/scripts/"):
        spec = importlib.util.spec_from_file_location("_common", path.parent / "_common.py")
        common = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "_common", common)
        spec.loader.exec_module(common)
    spec = importlib.util.spec_from_file_location("si6_test_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("shift", [-.08, -.04, 0, .04, .08])
def test_widened_windows_detect_consistently_shifted_pair(shift):
    x = np.linspace(-5, 15, 65536)
    y = abs(np.random.default_rng(54).normal(0, 20, len(x)))
    for center in (2.08+shift, 6.99+shift):
        y += 60000*np.exp(-.5*((x-center)/.02)**2)
    evidence = evaluate_solvent_spectrum(x, y, DEFAULTS)
    assert evidence["present"]
    assert evidence["pair_shift_difference_ppm"] < .001


@pytest.mark.parametrize("centers", [(2.08,), (6.99,), (7.12,), (2.08, 7.12), (2.08, 7.08), (2.00, 7.04), (2.12, 7.16), ()])
def test_neighbor_or_incomplete_pair_never_confirms_sample(centers):
    x = np.linspace(-5, 15, 65536)
    y = abs(np.random.default_rng(54).normal(0, 20, len(x)))
    for center in centers:
        y += 90000*np.exp(-.5*((x-center)/.015)**2)
    cfg = deepcopy(DEFAULTS)
    # Even an accidentally expanded search window cannot waive identity checks.
    cfg["windows_ppm"][1] = [6.8, 7.3]
    assert not evaluate_solvent_spectrum(x, y, cfg)["present"]


def test_high_noise_and_flat_elevated_background_fail():
    x = np.linspace(-5, 15, 65536)
    for y in (abs(np.random.default_rng(21).normal(0, 1.e5, len(x))), np.full_like(x, 1.e6), np.zeros_like(x)):
        assert not evaluate_solvent_spectrum(x, y, DEFAULTS)["present"]


def test_solvent_confirmed_target_absence_remains_eligible():
    raw = base.load_si6_config(ROOT / "configs/experiments/si6_real_two_stage.yaml")
    rules = base.build_stages(raw["workflow"])[0].completion
    rows = []
    for area in [100, 0, 0, 0, 0]:
        rows.append(record_sample_presence({"peak_area": area}, rows, DEFAULTS, {"present": True}))
    assert rows[-1]["endpoint_eligible"]
    assert completion_evidence(rows, rules)["complete"]


@pytest.mark.parametrize("name,hours", [("initial_stage", 7.5), ("first_addition_stage", 2.75)])
def test_independent_yaml_durations_load_and_change_only_selected_stage(tmp_path, name, hours):
    raw = yaml.safe_load((ROOT / "configs/experiments/si6_real_two_stage.yaml").read_text())
    raw["workflow"][name]["max_hours"] = hours
    path = tmp_path / "reaction.yaml"
    path.write_text(yaml.safe_dump(raw))
    prepared = si6.prepare(path, ROOT / "configs/machines/si6_real_COM4.yaml", ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=True)
    stages = base.build_stages(prepared[0]["workflow"])
    assert [s.max_hours for s in stages] == ([hours, 4] if name == "initial_stage" else [48, hours])


@pytest.mark.parametrize("name", ["initial_stage", "first_addition_stage"])
@pytest.mark.parametrize("hours", [0, -1, float("inf"), float("nan"), True, None, "wrong", 1.e100])
def test_invalid_duration_rejected_before_transport(tmp_path, monkeypatch, name, hours):
    raw = yaml.safe_load((ROOT / "configs/experiments/si6_real_two_stage.yaml").read_text())
    raw["workflow"][name]["max_hours"] = hours
    path = tmp_path / "reaction.yaml"
    path.write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(si6, "SerialTransport", lambda *a, **k: pytest.fail("opened serial"))
    with pytest.raises((ValueError, TypeError)):
        si6.prepare(path, ROOT / "configs/machines/si6_real_COM4.yaml", ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=False)


@pytest.mark.parametrize("answer,opens,expected", [("n", 0, 1), ("y", 1, 0)])
def test_main_has_one_confirmation_and_decline_never_opens(tmp_path, monkeypatch, answer, opens, expected):
    module = load_script("scripts/02_si6_experiment.py", monkeypatch)
    prepared = si6.prepare(ROOT / "configs/experiments/si6_real_two_stage.yaml", ROOT / "configs/machines/si6_real_COM4.yaml", ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=True)
    monkeypatch.setattr(module.si6, "prepare", lambda *a, **k: prepared)
    prompts, opened = [], []
    monkeypatch.setattr("builtins.input", lambda text: prompts.append(text) or answer)
    @contextmanager
    def session(*a, **k):
        opened.append(True)
        yield SimpleNamespace(paths=SimpleNamespace(run_dir=tmp_path))
    monkeypatch.setattr(module.si6, "open_services", session)
    monkeypatch.setattr(module.si6, "run_experiment", lambda *a, **k: base.RunOutcome(base.TerminalStatus.COMPLETED, "test completed"))
    monkeypatch.setattr(module, "summarize_run", lambda path: path)
    assert module.main(["--live"]) == expected
    assert len(prompts) == 1 and len(opened) == opens


def test_explicit_validate_config_is_mutually_exclusive_with_live(monkeypatch):
    module = load_script("scripts/02_si6_experiment.py", monkeypatch)
    monkeypatch.setattr(module.si6, "open_services", lambda *a, **k: pytest.fail("opened services"))
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("unexpected prompt"))
    assert module.main(["--validate-config", "--machine-config", str(ROOT / "configs/machines/si6_real_COM6.yaml"), "--arduino-config", str(ROOT / "arduino/configs/arduino_real_COM3.yaml")]) == 0
    with pytest.raises(SystemExit) as exc:
        module.main(["--validate-config", "--live"])
    assert exc.value.code == 2


@pytest.mark.parametrize("action,target", [("set-home", 0), ("home", 0), ("up", 1), ("down", 0)])
@pytest.mark.parametrize("answer", ["y", "n"])
def test_manual_live_actions_have_one_prompt_before_transport(monkeypatch, tmp_path, action, target, answer):
    module = load_script("arduino/scripts/needle_control.py", monkeypatch)
    prompts, opened, moves = [], [], []
    monkeypatch.setattr(module, "confirm_live", lambda text: prompts.append(text) if answer == "y" else (_ for _ in ()).throw(RuntimeError("declined")))
    @contextmanager
    def session(*a, **k):
        opened.append(True)
        yield SimpleNamespace()
    class Needle:
        def __init__(self, *a, **k): pass
        def confirm_home(self, **k): moves.append(("reference", 0))
        def move_to(self, position): moves.append(("move", position))
        def return_to_home(self): moves.append(("move", 0))
        def status(self): return dict(logical_position=str(target), position_valid="true", position_source="software_estimate", moving="false", fault="NONE")
    monkeypatch.setattr(module, "controller_session", session)
    monkeypatch.setattr(module, "TrackedNeedle", Needle)
    cfg = yaml.safe_load((ROOT / "arduino/configs/arduino_real_COM3.yaml").read_text())
    cfg["needle"]["state_path"] = str(tmp_path / "state.json")
    monkeypatch.setattr(module, "load_arduino_config", lambda *a: cfg)
    if answer == "n":
        with pytest.raises(RuntimeError, match="declined"):
            module.main([action, "--live"])
        assert not opened and not moves
    else:
        assert module.main([action, "--live"]) == 0
        assert len(prompts) == 1 and len(opened) == 1
        assert moves == [("reference" if action == "set-home" else "move", target)]


def test_stop_with_corrupt_state_still_sends_stop_and_preserves_bytes(tmp_path, monkeypatch):
    module = load_script("arduino/scripts/needle_control.py", monkeypatch)
    cfg = yaml.safe_load((ROOT / "arduino/configs/arduino_real_COM3.yaml").read_text())
    path = tmp_path / "state.json"
    path.write_text("{corrupt")
    cfg["needle"]["state_path"] = str(path)
    stopped = []
    @contextmanager
    def session(*a, **k):
        yield SimpleNamespace(status=lambda: {"moving": "true"}, stop=lambda: stopped.append(True))
    monkeypatch.setattr(module, "load_arduino_config", lambda *a: cfg)
    monkeypatch.setattr(module, "controller_session", session)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("STOP prompted"))
    assert module.main(["stop", "--live"]) == 0
    assert stopped == [True] and path.read_text() == "{corrupt"


def test_explicit_home_reset_preserves_corrupt_record_and_sends_no_motion(tmp_path, monkeypatch):
    module = load_script("arduino/scripts/needle_control.py", monkeypatch)
    cfg = yaml.safe_load((ROOT / "arduino/configs/arduino_real_COM3.yaml").read_text())
    path = tmp_path / "state.json"
    path.write_text("{corrupt")
    cfg["needle"]["state_path"] = str(path)
    @contextmanager
    def session(*a, **k):
        assert not k["allow_motion"]
        yield SimpleNamespace(status=lambda: {"moving": "false", "fault": "NONE"}, jog=lambda *a: pytest.fail("set-home moved motor"))
    monkeypatch.setattr(module, "load_arduino_config", lambda *a: cfg)
    monkeypatch.setattr(module, "controller_session", session)
    prompts = []
    monkeypatch.setattr(module, "confirm_live", lambda text: prompts.append(text))
    assert module.main(["set-home", "--live"]) == 0
    assert len(prompts) == 1
    backup, = tmp_path.glob("state.json.corrupt-*.bak")
    assert backup.read_text() == "{corrupt"
    assert json.loads(path.read_text())["reason"] == "operator_confirmed_home"


@pytest.mark.parametrize("packed", [False, True])
def test_git_version_discovery_handles_loose_and_packed_refs(tmp_path, packed):
    sha = "a"*40
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    if packed:
        (gitdir / "packed-refs").write_text("# packed refs\n" + sha + " refs/heads/main\n")
    else:
        (gitdir / "refs/heads").mkdir(parents=True)
        (gitdir / "refs/heads/main").write_text(sha)
    assert discover_git_commit(tmp_path) == sha


def test_release_integrity_distinguishes_config_edits_from_source_tampering(tmp_path, monkeypatch):
    module = load_script("scripts/verify_si6_environment.py", monkeypatch)
    code = tmp_path / "scripts/control.py"
    code.parent.mkdir()
    code.write_text("code")
    settings = tmp_path / "configs/run.yaml"
    settings.parent.mkdir()
    settings.write_text("hours: 48")
    manifest = {"commit": "b"*40, "source_files": {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (code, settings)}}
    (tmp_path / "RELEASE_VERSION.json").write_text(json.dumps(manifest))
    assert discover_git_commit(tmp_path) == "b"*40
    assert module.verify_release(tmp_path)["changed_operator_configs"] == []
    settings.write_text("hours: 24")
    assert module.verify_release(tmp_path)["changed_operator_configs"] == ["configs/run.yaml"]
    code.write_text("changed code")
    with pytest.raises(ValueError, match="hash mismatch"):
        module.verify_release(tmp_path)


def test_run_metadata_and_status_expose_timed_completion_without_secrets(tmp_path, monkeypatch):
    from test_si6_solvent_fallback import run_mock
    monkeypatch.setenv("SI6_UNRELATED_PASSWORD", "TEST_SECRET_MUST_NOT_BE_LOGGED")
    outcome, services, events, rows = run_mock(tmp_path, monkeypatch, missing_stages={"stage_1", "stage_2"}, empty=True,
                                              duration1=3, interval1=60, duration2=2, interval2=30)
    assert outcome.status is base.TerminalStatus.COMPLETED
    metadata_path = services.paths.run_dir / "metadata/run_metadata.json"
    metadata = json.loads(metadata_path.read_text())
    assert metadata["experiment_id"] == "SIMULATION_ONLY"
    assert metadata["git_commit"] == events[0].get("software_version")
    assert metadata["source_files_sha256"]["chemyx_lab/analysis/solvent_presence.py"]
    assert metadata["status"] == "completed" and metadata["dose_confirmed"]
    assert metadata["timed_fallback_stages"] == ["stage_1", "stage_2"]
    assert len(metadata["stages"]) == 2
    assert datetime.fromisoformat(metadata["ended_at_utc"]) >= datetime.fromisoformat(metadata["started_at_utc"])
    status_path = services.paths.run_dir / "operator_status.json"
    status = json.loads(status_path.read_text())
    assert status["target_area"] is None and status["timed_fallback"]
    assert status["terminal_classification"] == "timed_fallback_completed"
    for name in (metadata_path, status_path, services.paths.journal_jsonl, services.paths.run_dir / "config_snapshot.json"):
        assert "TEST_SECRET_MUST_NOT_BE_LOGGED" not in name.read_text()


def test_pre_transport_failure_is_logged_and_metadata_closed(tmp_path, monkeypatch):
    raw = base.load_si6_config(ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml")
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    path = tmp_path / "reaction.yaml"
    path.write_text(yaml.safe_dump(raw))
    prepared = si6.prepare(path, ROOT / "configs/machines/si6_real_COM4.yaml", ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=True)
    def failed_reservation(self):
        raise RuntimeError("reservation failed before transport")
    monkeypatch.setattr(si6.DoseGuard, "reserve", failed_reservation)
    monkeypatch.setattr(si6, "SerialTransport", lambda *a, **k: pytest.fail("opened transport"))
    with pytest.raises(RuntimeError, match="reservation failed"):
        with si6.open_services(*prepared, identity=si6.RunIdentity("si6", True)):
            pytest.fail("entered services")
    metadata_path, = tmp_path.rglob("metadata/run_metadata.json")
    metadata = json.loads(metadata_path.read_text())
    assert metadata["status"] == "failed" and metadata["ended_at_utc"]


def test_acquisition_span_cannot_exclude_required_solvent_windows(tmp_path):
    raw = yaml.safe_load((ROOT / "configs/experiments/si6_real_two_stage.yaml").read_text())
    raw["nmr"]["sweep_width"] = 2
    path = tmp_path / "narrow.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="acquisition span"):
        si6.prepare(path, ROOT / "configs/machines/si6_real_COM4.yaml", ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=True)


@pytest.mark.parametrize("dry_run", [False, True])
def test_nmr_diagnostic_decline_and_dry_run_never_request_rpc(monkeypatch, dry_run):
    module = load_script("scripts/diagnostics/04_run_nmr_1d_acquisition.py", monkeypatch)
    class Client:
        base_url = "http://offline-test:5000"
        def __init__(self, *a, **k): pass
        def iflow_1d_settings(self): pytest.fail("contacted NMR before confirmation")
        def iflow_experiment_settings(self): pytest.fail("contacted NMR before confirmation")
    monkeypatch.setattr(module, "NmrRpcClient", Client)
    args = [str(ROOT / "scripts/diagnostics/04_run_nmr_1d_acquisition.py"), "--machine-config", str(ROOT / "configs/machines/si6_real_COM4.yaml")]
    if dry_run:
        args.append("--dry-run")
    monkeypatch.setattr(sys, "argv", args)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda text: prompts.append(text) or "n")
    assert module.main() == (0 if dry_run else 3)
    assert len(prompts) == (0 if dry_run else 1)


def test_failed_stop_acknowledgment_invalidates_saved_position(tmp_path, monkeypatch):
    from arduino.python.needle_state import NeedleStateStore, NeedleState
    module = load_script("arduino/scripts/needle_control.py", monkeypatch)
    cfg = yaml.safe_load((ROOT / "arduino/configs/arduino_real_COM3.yaml").read_text())
    path = tmp_path / "state.json"
    cfg["needle"]["state_path"] = str(path)
    NeedleStateStore(path).save(NeedleState(logical_position=1, position_valid=True, reason="motion_completed",
                                         last_updated=datetime.now(timezone.utc).isoformat(), steps_per_unit=200, up_step_sign=1))
    def failure(): raise RuntimeError("STOP not acknowledged")
    @contextmanager
    def session(*a, **k):
        yield SimpleNamespace(status=lambda: {"moving": "true"}, stop=failure)
    monkeypatch.setattr(module, "load_arduino_config", lambda *a: cfg)
    monkeypatch.setattr(module, "controller_session", session)
    with pytest.raises(RuntimeError, match="not acknowledged"):
        module.main(["stop", "--live"])
    assert not NeedleStateStore(path).load().position_valid


def test_live_main_refuses_a_manual_legacy_profile_before_prompt_or_transport(monkeypatch):
    module = load_script("scripts/02_si6_experiment.py", monkeypatch)
    prepared = si6.prepare(base.DEFAULT_CONFIG, ROOT / "configs/machines/si6_real_COM4.yaml", ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=True)
    monkeypatch.setattr(module.si6, "prepare", lambda *a, **k: prepared)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("prompted for unsupported manual profile"))
    monkeypatch.setattr(module.si6, "open_services", lambda *a, **k: pytest.fail("opened for unsupported manual profile"))
    assert module.main(["--live", "--workflow-config", str(base.DEFAULT_CONFIG)]) == 1


def test_communications_check_never_assumes_home_and_disables_motion(tmp_path):
    raw = base.load_si6_config(ROOT / "configs/experiments/si6_real_hardware_test.yaml")
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    path = tmp_path / "communication.yaml"
    path.write_text(yaml.safe_dump(raw))
    prepared = si6.prepare(path, ROOT / "configs/machines/si6_real_COM4.yaml", ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=True)
    with si6.open_services(*prepared, identity=si6.RunIdentity("diagnostic", True, "communications")) as services:
        si6.preflight(services, check_nmr=False)
        assert "logical_position" not in services.needle.status()
        assert not (services.paths.run_dir / "mock_needle_state.json").exists()
        with pytest.raises(Exception):
            services.needle.jog(1, 100)
        assert not any(" JOG " in command for command in services.needle.transport.tx_log)
