"""Shared pump timing, configuration, and NMR acquisition operations."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config
from ..analysis.nmr import NmrProcessingError, analyze_dx_peak
from ..instruments.chemyx import Pump
from ..instruments.nmr import (
    NmrRpcClient,
    NmrRpcConfig,
    build_1d_experiment_settings,
    build_iflow_1d_settings,
    build_iflow_experiment_settings,
    extract_text_payload,
)


def move_seconds(volume_ml: float, rate: float, units: int) -> float:
    """Estimate pump travel time for a volume expressed in mL."""
    if rate <= 0:
        raise ValueError("Pump rate must be positive")
    if units == 0:
        return abs(volume_ml) / rate * 60.0
    if units == 1:
        return abs(volume_ml) / rate * 3600.0
    if units == 2:
        return abs(volume_ml) * 1000.0 / rate * 60.0
    if units == 3:
        return abs(volume_ml) * 1000.0 / rate * 3600.0
    raise ValueError(f"Unsupported Chemyx units code: {units}")


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, remainder = divmod(seconds, 60)
    if minutes:
        return f"{minutes} min {remainder} s"
    return f"{remainder} s"


def sleep_with_progress(label: str, seconds: float, mock: bool = False) -> None:
    if mock or seconds <= 0:
        return
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        print(f"     {label}: {format_seconds(remaining)} remaining")
        time.sleep(min(30.0, remaining))


def configure_pump(pump: Pump, pump_cfg: config.PumpConfig) -> None:
    print("\n[1] Configure Chemyx pump")
    print("units    ->", repr(pump.set_units(pump_cfg.units)))
    print("diameter ->", repr(pump.set_diameter(pump_cfg.diameter)))
    print("rate     ->", repr(pump.set_rate(pump_cfg.rate)))


def make_dx_path(
    save_dir: Path, label: str, nmr_settings: config.NmrSettings
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    gain = (
        "auto"
        if nmr_settings.receiver_gain is None
        else f"gain{nmr_settings.receiver_gain:g}"
    )
    gain = gain.replace(".", "p")
    return save_dir / f"{stamp}_{label}_{nmr_settings.scans}scan_{gain}.dx"


def summarize_nmr_status(status: Any) -> None:
    if not isinstance(status, dict):
        print(f"     final status -> {status!r}")
        return
    receipt = status.get("OriginalReceipt") or {}
    settings = receipt.get("Settings") or {}
    print(
        "     final status -> "
        f"ResultCode={status.get('ResultCode')}, "
        f"NumberOfScansRun={status.get('NumberOfScansRun')}, "
        f"TimeStamp={receipt.get('TimeStamp')}"
    )
    print(
        "     actual settings -> "
        f"NumberOfScans={settings.get('NumberOfScans')}, "
        f"ReceiverGain={settings.get('ReceiverGain')}, "
        f"SpectralCentreInPpm={settings.get('SpectralCentreInPpm')}, "
        f"SpectralWidthInPpm={settings.get('SpectralWidthInPpm')}"
    )


def run_nmr_acquisition(
    nmr_settings: config.NmrSettings,
    save_dir: Path,
    label: str = "nmr_measurement",
) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    dx_path = make_dx_path(save_dir, label, nmr_settings)
    client = NmrRpcClient(
        NmrRpcConfig(
            host=nmr_settings.host,
            port=nmr_settings.port,
            scheme=nmr_settings.scheme,
            timeout=nmr_settings.timeout,
            poll_seconds=nmr_settings.poll_seconds,
            max_wait_seconds=nmr_settings.max_wait_seconds,
        )
    )

    print("\n[3] Run NMR")
    print(
        "     settings -> "
        f"{client.base_url}, route={nmr_settings.route}, "
        f"scans={nmr_settings.scans}, "
        f"receiver_gain={nmr_settings.receiver_gain}, "
        f"auto_gain={nmr_settings.auto_gain}"
    )
    if nmr_settings.route == "iflow":
        one_d_settings = build_iflow_1d_settings(
            client.iflow_1d_settings(),
            receiver_gain=nmr_settings.receiver_gain,
            auto_gain=nmr_settings.auto_gain,
            export_filename=str(dx_path),
        )
        run_settings = build_iflow_experiment_settings(
            client.iflow_experiment_settings(),
            scans=nmr_settings.scans,
            receiver_gain=nmr_settings.receiver_gain,
            spectral_center=nmr_settings.spectral_center,
            sweep_width=nmr_settings.sweep_width,
            export_filename=str(dx_path),
        )
        print("     set iFlow 1D parameters")
        client.set_iflow_1d_settings(one_d_settings)
        print("     start iFlow 1D experiment")
        client.run_iflow_experiment(run_settings)
        final_status = client.wait_for_idle(
            status_getter=client.iflow_experiment_status
        )
        payload = final_status
    else:
        template = client.experiment_settings(nmr_settings.experiment)
        run_settings = build_1d_experiment_settings(
            template,
            scans=nmr_settings.scans,
            solvent=nmr_settings.solvent,
            spectral_center=nmr_settings.spectral_center,
            sweep_width=nmr_settings.sweep_width,
            receiver_gain=nmr_settings.receiver_gain,
            export_filename=str(dx_path),
        )
        print(f"     start {nmr_settings.experiment} experiment")
        client.start_experiment(run_settings, nmr_settings.experiment)
        final_status = client.wait_for_idle()
        results = client.experiment_results()
        result_name = nmr_settings.result_name
        if isinstance(results, list) and results and result_name not in results:
            result_name = results[0]
        payload = client.experiment_result(
            result_name,
            fmt="jdx",
            data_type=nmr_settings.result_type,
        )

    summarize_nmr_status(final_status)
    text = extract_text_payload(payload)
    dx_path.write_text(text, encoding="utf-8")
    print(f"     saved -> {dx_path}")

    if nmr_settings.result_type == "fid":
        try:
            peak = analyze_dx_peak(
                dx_path, target_ppm=nmr_settings.target_ppm
            )
        except NmrProcessingError as exc:
            print(f"     analysis warning -> {exc}")
        else:
            print(
                f"     peak near {nmr_settings.target_ppm:g} ppm -> "
                f"{peak.peak_ppm:.4f} ppm, SNR {peak.snr:.2f}"
            )
    return dx_path
