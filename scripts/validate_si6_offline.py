"""Deterministic repository and copied-directory validation; hardware is never opened live."""
from __future__ import annotations

import argparse
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
)


def validate_resources():
    missing = [p for p in RESOURCES if not (config.REPO_ROOT / p).is_file()]
    if missing:
        raise ValueError("Incomplete portable package: " + ", ".join(missing))
    for name in ("nominal", "fast_sim", "stage1_development", "stage2_development"):
        si6.prepare(config.REPO_ROOT / f"config_templates/experiments/si6_two_stage_{name}.yaml",
                    config.REPO_ROOT / "config_templates/machines/si6_instrument_settings.example.yaml",
                    config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True)
    versions = {name: importlib.metadata.version(name) for name in
                ("pyserial", "PyYAML", "numpy", "scipy", "matplotlib", "nmrglue", "pytest")}
    print(json.dumps({"repository_root": str(config.REPO_ROOT), "resources": "PASS", "versions": versions}, indent=2))


def simulate():
    settings = list(si6.prepare(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml",
                               config.REPO_ROOT / "config_templates/machines/si6_instrument_settings.example.yaml",
                               config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True))
    settings[0]["output"]["run_root_dir"] = "test_tmp_offline_validation/runs"
    with si6.open_services(*settings, identity=si6.RunIdentity("si6", True)) as services:
        outcome = si6.run_experiment(services)
        print(f"SIMULATION: {outcome.status.value}; {services.paths.run_dir}")
        if outcome.exit_code:
            raise RuntimeError(outcome.message)


def copied_directory_test():
    # Copy portable source/config/resources, excluding historical outputs and
    # machine virtual environments. Actual deployment ALSO preserves rig history
    # and dose reservations; this test starts a new mock experiment only.
    with tempfile.TemporaryDirectory(prefix="si6_new_computer_") as temporary:
        destination = Path(temporary) / "portable_experiment"
        destination.mkdir()
        ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "test_tmp_*", ".test-tmp*", ".codex_pytest_temp_*", "node_modules",
                                        "wheelhouse", "installers", "drivers", ".git", ".venv*", "*.pyc", "NanalysisData", "NMR_data", "NMRresults", ".ipynb_checkpoints")
        for name in ("chemyx_lab", "scripts", "arduino", "configs", "config_templates", "docs", "tests", "offline", "nmr_template"):
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
        runs = list((destination / "test_tmp_offline_validation/runs_mock").glob("*/final/experiment_summary.json"))
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
        result = subprocess.run([sys.executable, "-B", "-m", "pytest", "tests/test_si6_two_stage.py",
                                 "tests/test_si6_validator_gates.py", "tests/test_si6_pump_channels.py",
                                 "tests/test_three_instrument_si6.py", "tests/test_si6_monitoring.py",
                                 "tests/test_runtime_journal.py", "-q", "-p", "no:cacheprovider",
                                 "--basetemp", "test_tmp_offline_tests"], cwd=config.REPO_ROOT)
        if result.returncode:
            return result.returncode
    if args.copy_test:
        copied_directory_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
