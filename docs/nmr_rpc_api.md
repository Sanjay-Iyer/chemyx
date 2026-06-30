# NMReady/Nanalysis RPC API Notes

This repo now has a small Python client for the API documentation in `api/`.

The main implementation is:

```text
chemyx_lab/nmr_rpc.py
```

The first work-laptop commands are:

```powershell
copy configs\nmr.local.example.json configs\nmr.local.json
python scripts\nmr_rpc_status.py
python scripts\nmr_run_1d.py --dry-run --mock-settings
python scripts\nmr_run_1d.py --save-dx runs\nmr\test_1d.dx
```

## What The API Docs Say

From `api/rpc-api.md`:

- Default RPC port is `5000`.
- RPC must be enabled in the instrument software.
- `PingSpectrometer` checks the spectrometer connection.
- `RpcActive` checks whether RPC can actually be used.
- `Experiment/List` returns available experiments such as `1D`.
- `Experiment/1D/Settings` returns a settings template.
- `Experiment/1D/Start` starts an experiment with that settings structure.
- `Experiment/Status` reports the active/recent experiment state.
- `Experiment/Results` lists result names.
- `Experiment/Results/<resultName>?format=jdx&type=fid` can retrieve a DataSet1D
  as JCAMP-DX/JDX text.
- `Service/Acquire` and `Service/SaveResults` provide a lower-level acquisition
  and save route.
- `iFlow/Settings/1D` has an `ExportFilename` field; when set, the instrument
  software can save results under that name.

The archived working Python wrapper used the `iFlow` route, so the committed
starting default is `route = iflow`. The generic Experiment API and lower-level
Service route are still present in `NmrRpcClient` for software versions that
prefer those endpoints.

## Local NMR Config

Committed defaults are:

```text
host = 169.254.30.54
port = 5000
route = iflow
scans = 2
receiver_gain = 12.0
auto_gain = false
```

For a real work-laptop setup, copy and edit:

```powershell
copy configs\nmr.local.example.json configs\nmr.local.json
```

The scripts automatically read `configs\nmr.local.json` when it exists. Missing
local config is okay; command-line flags override both defaults and the local
file.

## Enabling RPC On The Instrument

The docs mention:

```text
RPC_API_ENABLED = True
Setup -> System -> Remote -> Enable
```

Confirm these settings on the instrument laptop before running Python control.
If `nmr_rpc_status.py` reports HTTP 403, RPC is visible but not enabled for the
requested protected endpoint.

## Status Check

```powershell
python scripts\nmr_rpc_status.py
```

Expected useful responses:

```text
PingSpectrometer: {"connected": true}
RpcActive: {"RpcActive": true}
Experiment/List: ["1D", ...]
```

If this fails, fix RPC setup before trying acquisition.

## 1D Acquisition

Offline command sanity check:

```powershell
python scripts\nmr_run_1d.py --dry-run --mock-settings
```

Real instrument:

```powershell
python scripts\nmr_run_1d.py --save-dx runs\nmr\si6_test.dx
```

One-off override without editing the local config:

```powershell
python scripts\nmr_run_1d.py --scans 8 --receiver-gain 14 --save-dx runs\nmr\si6_test.dx
```

After saving the `.dx`, the script immediately runs the local 6.1 ppm analysis
against that file.

## Setting Scans And Receiver Gain

If the NMR ignores `numscans` or `ReceiverGain` from an older Python class,
that usually means those values were only stored in Python or were sent through
the wrong API shape.

The docs show the most direct route as `iFlow`, using exact keys:

```text
PUT /interfaces/iFlow/Settings/1D
  AutoGain = false
  ReceiverGain = 12

PUT /interfaces/iFlow/RunExperiment
  NumberOfScans = 2
  ReceiverGain = 12
```

Dry-run the payloads:

```powershell
python scripts\nmr_run_1d.py --dry-run --mock-settings --route iflow --scans 2 --receiver-gain 12
```

Real instrument test:

```powershell
python scripts\nmr_run_1d.py --route iflow --scans 2 --receiver-gain 12 --save-dx runs\nmr\iflow_test.dx
```

Then open the saved `.dx` header and confirm it contains the intended scan
count and receiver gain fields.

## SOP Workflow With RPC

Once standalone NMR acquisition works:

```powershell
python scripts\sop_mock_workflow.py --cycles 1 --nmr-rpc --nmr-save-dir runs\nmr
```

With pump hardware too:

```powershell
python scripts\sop_mock_workflow.py --real --nmr-rpc --volume-scale 0.05 --nmr-save-dir runs\nmr
```

Start with a small pump `--volume-scale` until the fluid path and NMR sample
timing are verified.
