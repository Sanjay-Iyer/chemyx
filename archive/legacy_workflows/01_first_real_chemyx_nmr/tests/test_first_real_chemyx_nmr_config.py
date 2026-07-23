import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from chemyx_lab.workflows import first_real_chemyx_nmr as first_real_test  # noqa: E402


def _write_experiment(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "workflow:",
                "  name: precedence",
                "  sequence:",
                "    - event: W",
                "      volume_ml: 5",
                "    - event: N",
                "    - event: I",
                "      volume_ml: 5",
                "  pump_extra_seconds: 2",
                "  settle_before_nmr_seconds: 0",
                "pump:",
                "  channel: 1",
                "  syringe_diameter_mm: 28.6",
                "  units: mL/min",
                "  rate_ml_min: 5",
                "  default_volume_ml: 5",
                "nmr:",
                "  route: iflow",
                "  scans: 2",
                "  receiver_gain: 12",
                "  auto_gain: false",
                "  spectral_center: 5",
                "  sweep_width: 20",
                "  target_ppm: 6.1",
                "output:",
                "  nmr_save_dir: results/raw/nmr/generated",
            ]
        ),
        encoding="utf-8",
    )


def _write_machine(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "chemyx:",
                "  serial_port: MACHINE_PUMP_PORT",
                "  baud_rate: 9600",
                "  timeout_seconds: 3",
                "  response_delay_seconds: 0.1",
                "nmr:",
                "  host: machine-host",
                "  port: 5001",
                "  timeout_seconds: 11",
            ]
        ),
        encoding="utf-8",
    )


def test_first_real_script_precedence_env_over_machine_and_experiment(tmp_path, monkeypatch):
    experiment = tmp_path / "experiment.yaml"
    machine = tmp_path / "machine.yaml"
    _write_experiment(experiment)
    _write_machine(machine)
    monkeypatch.setenv("CHEMYX_RATE", "7")
    monkeypatch.setenv("NMR_HOST", "env-host")

    args = first_real_test.parse_args(
        [
            "--workflow-config",
            str(experiment),
            "--machine-config",
            str(machine),
            "--pump-config",
            "missing-chemyx-local.json",
            "--nmr-config",
            "missing-nmr-local.json",
            "--dry-run",
        ]
    )
    pump = first_real_test.load_pump_settings(args)
    nmr = first_real_test.load_nmr_settings(args)

    assert pump.port == "MACHINE_PUMP_PORT"
    assert pump.baud_rate == 9600
    assert pump.timeout == 3.0
    assert pump.response_delay == 0.1
    assert pump.rate == 7.0
    assert nmr.host == "env-host"
    assert nmr.port == 5001


def test_first_real_script_precedence_cli_over_env(tmp_path, monkeypatch):
    experiment = tmp_path / "experiment.yaml"
    machine = tmp_path / "machine.yaml"
    _write_experiment(experiment)
    _write_machine(machine)
    monkeypatch.setenv("CHEMYX_RATE", "7")
    monkeypatch.setenv("NMR_HOST", "env-host")

    args = first_real_test.parse_args(
        [
            "--workflow-config",
            str(experiment),
            "--machine-config",
            str(machine),
            "--pump-config",
            "missing-chemyx-local.json",
            "--nmr-config",
            "missing-nmr-local.json",
            "--port",
            "CLI_PUMP_PORT",
            "--rate",
            "9",
            "--nmr-host",
            "cli-host",
            "--dry-run",
        ]
    )
    pump = first_real_test.load_pump_settings(args)
    nmr = first_real_test.load_nmr_settings(args)

    assert pump.port == "CLI_PUMP_PORT"
    assert pump.rate == 9.0
    assert nmr.host == "cli-host"
