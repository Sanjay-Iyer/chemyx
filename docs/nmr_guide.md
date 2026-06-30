# NMR Guide

The first iteration supports three NMR paths:

1. Ingest exported NMReady/Nanalysis `.dx` files.
2. Check and run the documented NMReady/Nanalysis RPC API.
3. Leave room for custom Experiment Designer/Lua scripts later.

## Exported DX Files

Example data is in:

```text
NMR/06-08-26/*.dx
```

Run:

```powershell
python scripts\analyze_nmr_dx.py NMR\06-08-26 --target 6.1
```

Each processing pass creates a clean analysis folder:

```text
runs/nmr_analysis/<timestamp>/
  results.csv
  manifest.json
  plots/
    <sample>_target_6p100ppm.png
```

The script parses the JCAMP-DX FID, Fourier transforms it with `numpy`, builds a
ppm axis from the file metadata, then uses `scipy.signal.find_peaks` inside the
target ppm window. Candidate peaks are scored by prominence so the reported
peak is a real local maximum, not just the tallest sampled point in the window.

Those plots show a wider `6.1 +/- 0.5 ppm` region, shade the narrower detection
window, and mark both the target ppm and the detected peak.

Output columns:

```text
file, source_path, target_ppm, peak_ppm, snr, prominence_snr, prominence, width_ppm, peaks_considered, peak_height, baseline, noise, points_in_window, plot_file, error
```

Use this as a screening metric at first. The exact integration window and
phase/baseline treatment should be refined after comparing against the NMR
software display.

Useful peak-finder tuning options:

```powershell
python scripts\analyze_nmr_dx.py runs\nmr --target 6.1 --run-name first_real_test
python scripts\analyze_nmr_dx.py runs\nmr --target 6.1 --window 0.20 --min-prominence-snr 2
python scripts\analyze_nmr_dx.py runs\nmr --target 6.1 --min-distance-ppm 0.02
python scripts\analyze_nmr_dx.py runs\nmr --target 6.1 --plot-window 0.75
python scripts\analyze_nmr_dx.py runs\nmr --target 6.1 --no-plots
```

## Signal Of Interest

Per the SOP note, watch the small signal near:

```text
6.1 ppm
```

Do not integrate the older broad region down to 5.8 ppm unless the chemistry
workflow changes back to needing it.

## RPC Instrument Control

The included Nanalysis RPC notes in `api/rpc-api.md` indicate a default RPC port
of `5000`. The archived working code connected to the NMR at:

```text
169.254.30.54:5000
```

Typical setup is:

```text
Setup -> System -> Remote -> Enable
```

The guide in the API notes also mentions enabling RPC in the instrument GUI
configuration. Confirm this on the instrument laptop before acquisition.

Check status:

```powershell
python scripts\nmr_rpc_status.py
```

The scripts use committed defaults if no local file exists. For the work
laptop, make a local editable NMR config:

```powershell
copy configs\nmr.local.example.json configs\nmr.local.json
```

Edit `configs\nmr.local.json` when the IP address, scans, receiver gain,
solvent, or timeout changes. Missing local config will not break the scripts.

Run a 1D acquisition and save the returned JCAMP-DX:

```powershell
python scripts\nmr_run_1d.py --save-dx runs\nmr\si6_test.dx
```

Override values from the terminal when you want a one-off test:

```powershell
python scripts\nmr_run_1d.py --scans 8 --receiver-gain 14 --save-dx runs\nmr\si6_test.dx
```

More detail: `docs/nmr_rpc_api.md`.

The planned future flow is:

```text
pump sample movement
pause
start 1H NMR acquisition
wait for experiment completion
export or retrieve DX/result data
analyze 6.1 ppm region
decide continue/stop/add reagent
```

For now, the SOP workflow script can ingest the newest `.dx` file in a data
folder:

```powershell
python scripts\sop_mock_workflow.py --cycles 1 --data-dir NMR\06-08-26
```

Or call RPC directly:

```powershell
python scripts\sop_mock_workflow.py --cycles 1 --nmr-rpc --nmr-save-dir runs\nmr
```
