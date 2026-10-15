"""Deterministic repository and copied-directory validation; hardware is never opened live."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.workflows import three_instrument_si6 as si6


RESOURCES = (
    "configs/experiments/si6_real_two_stage.yaml", "configs/experiments/si6_real_toluene_soak_test.yaml",
    "configs/machines/si6_real_COM4.yaml", "configs/machines/si6_real_COM6.yaml", "arduino/configs/arduino_real_COM3.yaml",
    "arduino/scripts/needle_control.py", "chemyx_lab/analysis/solvent_presence.py", "chemyx_lab/run_metadata.py",
    "scripts/02_si6_experiment.py", "scripts/03_si6_real_soak_test.py", "scripts/verify_si6_environment.py", "scripts/prepare_si6_release.py",
    "docs/SI6_FINAL_REAL_RUN_CHECKLIST.md", "docs/SI6_DEPLOYMENT_MANIFEST.md", "docs/SI6_PROMPT_AUDIT.md", "offline/verify_bundle.py",
    "config_templates/experiments/si6_two_stage_nominal.yaml",
    "config_templates/experiments/si6_two_stage_fast_sim.yaml",
    "config_templates/experiments/si6_two_stage_stage1_development.yaml",
    "config_templates/experiments/si6_two_stage_stage2_development.yaml",
    "config_templates/machines/si6_instrument_settings.example.yaml",
    "configs/nmr/analysis.yaml", "arduino/configs/arduino.example.yaml",
    "chemyx_lab/testing/fixtures/si6_two_stage_trends.json",
    "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx",
    "offline/requirements-lock.txt", "docs/OFFLINE_DEPLOYMENT.md", "docs/OFFLINE_REQUIREMENTS.md",
    "docs/SI6_TWO_STAGE_OPERATOR_GUIDE.md", "docs/SI6_WORKFLOW_VALIDATOR.md",
    "docs/SI6_TWO_STAGE_VALIDATION_REPORT.md", "docs/SI6_TWO_STAGE_CONFIGURATION.md",
    "docs/SI6_START_HERE.md", "docs/SI6_SCRIPT_USER_GUIDE.md", "docs/SI6_CONFIG_USER_GUIDE.md",
    "docs/SI6_SYNTHETIC_ANALYSIS_AUDIT.md", "docs/SI6_FINAL_HOME_VALIDATION_REPORT.md",
    "scripts/validate_si6_synthetic_analysis.py", "chemyx_lab/testing/si6_synthetic_analysis.py",
)


def validate_resources():
    missing = [p for p in RESOURCES if not (config.REPO_ROOT / p).is_file()]
    if missing:
        raise ValueError("Incomplete portable package: " + ", ".join(missing))
    for name in ("nominal", "fast_sim", "stage1_development", "stage2_development"):
        si6.prepare(config.REPO_ROOT / f"config_templates/experiments/si6_two_stage_{name}.yaml",
                    config.REPO_ROOT / "config_templates/machines/si6_instrument_settings.example.yaml",
                    config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True)
    for port in ("COM4", "COM6"):
        si6.prepare(config.REPO_ROOT / "configs/experiments/si6_real_two_stage.yaml",
                    config.REPO_ROOT / f"configs/machines/si6_real_{port}.yaml",
                    config.REPO_ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=True)
    versions = {name: importlib.metadata.version(name) for name in
                ("pyserial", "PyYAML", "numpy", "scipy", "matplotlib", "nmrglue", "pytest")}
    print(json.dumps({"repository_root": str(config.REPO_ROOT), "resources": "PASS", "versions": versions}, indent=2))


def simulate():
    settings = list(si6.prepare(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml",
                               config.REPO_ROOT / "config_templates/machines/si6_instrument_settings.example.yaml",
                               config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True))
    # Each validator invocation models a separately prepared mock rig. Within
    # its output root the physical-ID reservation still blocks repeated dosing.
    scratch = config.REPO_ROOT / "test_tmp_offline_validation"
    scratch.mkdir(parents=True, exist_ok=True)
    session = Path(tempfile.mkdtemp(prefix="mock_session_", dir=scratch))
    settings[0]["output"]["run_root_dir"] = str(session / "runs")
    with si6.open_services(*settings, identity=si6.RunIdentity("si6", True)) as services:
        outcome = si6.run_experiment(services)
        print(f"SIMULATION: {outcome.status.value}; {services.paths.run_dir}")
        if outcome.exit_code:
            raise RuntimeError(outcome.message)


def copied_directory_test():
    # Copy portable source/config/resources, excluding historical outputs and
    # machine virtual environments. Actual deployment ALSO preserves rig history
    # and dose reservations; this test starts a new mock experiment only.
    scratch = config.REPO_ROOT / "test_tmp_offline_validation"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="si6_new_computer_", dir=scratch) as temporary:
        if not Path(temporary).resolve().is_relative_to(scratch.resolve()):
            raise ValueError("Unsafe temporary copy path")
        destination = Path(temporary) / "portable_experiment"
        destination.mkdir()
        ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "test_tmp_*", ".test-tmp*", ".codex_pytest_temp_*", "node_modules",
                                        "wheelhouse", "installers", "drivers", ".git", ".venv*", "*.pyc", "NanalysisData", "NMR_data", "NMRresults", ".ipynb_checkpoints")
        for name in ("chemyx_lab", "scripts", "arduino", "configs", "config_templates", "docs", "tests", "offline", "nmr_template", "smoke_test"):
            directory_ignore = shutil.ignore_patterns("arduino", "wheelhouse", "installers", "drivers") if name == "offline" else ignore
            shutil.copytree(config.REPO_ROOT / name, destination / name, ignore=directory_ignore)
        for name in ("requirements.txt", "pyproject.toml", "pytest.ini"):
            source = config.REPO_ROOT / name
            if source.is_file():
                shutil.copyfile(source, destination / name)
        log = destination / "copied_mock.log"
        with log.open("w", encoding="utf-8") as handle:
            result = subprocess.run([sys.executable, "-B", "scripts/validate_si6_offline.py", "--simulate"],
                                    cwd=destination, stdout=handle, stderr=subprocess.STDOUT)
        # Preserve a compact evidence report outside the disposable copy.
        report = config.REPO_ROOT / "test_tmp_offline_validation/copied_directory_report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        runs = list((destination / "test_tmp_offline_validation").rglob("final_qc/experiment_summary.json"))
        summary = json.loads(runs[0].read_text()) if len(runs) == 1 else None
        report.write_text(json.dumps({"copied_root": str(destination), "exit_code": result.returncode,
                                     "summary": summary, "log_tail": log.read_text()[-2000:]}, indent=2), encoding="utf-8")
        if result.returncode or not summary or summary["status"] != "completed":
            raise RuntimeError(f"Copied-directory mock failed; inspect {report}")
        print(f"COPIED DIRECTORY: PASS; evidence {report}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--copy-test", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()
    validate_resources()
    if args.simulate:
        simulate()
    if args.run_tests:
        # Never remove/reuse a previous laptop's test artifacts (ACLs may also
        # differ after copying). Every validation receives a new scratch path.
        test_temp = config.REPO_ROOT / "test_tmp_offline_validation" / datetime.now(timezone.utc).strftime("tests_%Y%m%d_%H%M%S_%f")
        result = subprocess.run([sys.executable, "-B", "-m", "pytest", "tests/test_si6_two_stage.py",
                                 "tests/test_si6_validator_gates.py", "tests/test_si6_pump_channels.py",
                                 "tests/test_three_instrument_si6.py", "tests/test_si6_monitoring.py",
                                 "tests/test_runtime_journal.py", "tests/test_si6_synthetic_analysis.py",
                                 "tests/test_si6_final_validator.py", "tests/test_si6_profile.py",
                                 "tests/test_si6_solvent_fallback.py", "tests/test_si6_predeployment.py",
                                 "tests/test_experiment_output_paths.py", "arduino/tests/test_home_recovery.py",
                                 "-q", "-p", "no:cacheprovider",
                                 "--basetemp", str(test_temp)], cwd=config.REPO_ROOT)
        if result.returncode:
            return result.returncode
    if args.copy_test:
        copied_directory_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
