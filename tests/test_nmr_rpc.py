import json

from chemyx_lab.nmr_rpc import (
    build_1d_experiment_settings,
    build_iflow_1d_settings,
    build_iflow_experiment_settings,
    example_1d_experiment_settings,
    example_iflow_1d_settings,
    example_iflow_experiment_settings,
    extract_text_payload,
    status_indicates_running,
)


def test_build_1d_experiment_settings_patches_nested_values():
    template = example_1d_experiment_settings()
    settings = build_1d_experiment_settings(
        template,
        scans=8,
        solvent="Toluene",
        spectral_center=5.0,
        sweep_width=20.0,
        receiver_gain=12,
        export_filename="runs/nmr/example.dx",
    )

    instrument = settings["setup"]["instrument"]
    params = settings["setup"]["params"]
    assert instrument["numScans"] == 8
    assert instrument["activeSolvent"] == "Toluene"
    assert instrument["spectralCenter"] == 5.0
    assert instrument["sweepWidth"] == 20.0
    assert params["receiverGain"] == 12
    assert settings["metadata"]["resultName"] == "example"

    assert template["setup"]["instrument"]["numScans"] != 8


def test_extract_text_payload_prefers_jdx_fields():
    assert extract_text_payload("##TITLE=x") == "##TITLE=x"
    assert extract_text_payload({"Value": "abc"}) == "abc"
    assert extract_text_payload({"JDX_FileContents_FD": "##JCAMP-DX=5.01"}) == "##JCAMP-DX=5.01"


def test_build_iflow_1d_settings_can_disable_autogain_and_set_gain():
    settings = build_iflow_1d_settings(
        example_iflow_1d_settings(),
        receiver_gain=12,
        auto_gain=False,
    )
    assert settings["AutoGain"] is False
    assert settings["ReceiverGain"] == 12.0


def test_build_iflow_experiment_settings_sets_scans_and_receiver_gain():
    settings = build_iflow_experiment_settings(
        example_iflow_experiment_settings(),
        scans=2,
        receiver_gain=12,
        spectral_center=5.0,
        sweep_width=20.0,
    )
    assert settings["NumberOfScans"] == 2
    assert settings["ReceiverGain"] == 12.0
    assert settings["SpectralCentreInPpm"] == 5.0
    assert settings["SpectralWidthInPpm"] == 20.0


def test_extract_text_payload_falls_back_to_json():
    payload = {"unexpected": {"value": 1}}
    assert json.loads(extract_text_payload(payload)) == payload


def test_status_indicates_running_handles_common_shapes():
    assert status_indicates_running({"Status": {"InProgress": True}}) is True
    assert status_indicates_running({"status": {"Running": False}}) is False
    assert status_indicates_running({"PercentComplete": 50}) is True
    assert status_indicates_running({"PercentComplete": 100}) is False
    assert status_indicates_running({"ResultCode": 1, "NumberOfScansRun": 1}) is True
    assert status_indicates_running({"ResultCode": 0, "NumberOfScansRun": 2}) is False
