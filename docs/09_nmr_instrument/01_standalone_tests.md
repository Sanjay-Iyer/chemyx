> [Documentation home](../00_START_HERE.md) | [NMR instrument](00_START_HERE.md)

# NMR standalone tests: connection, 1 scan, 4 scans

Three scripts test the NMR on its own, the same way the Arduino and Chemyx
have their own scripts. Run them in order. Each one prints every NMR request,
explains what the reply means, and, if something fails, says **why** and
**what to do**. Everything is also saved to a debug report.

| Step | Script | What it does | Changes anything on the NMR? |
|---|---|---|---|
| 1 | `scripts\nmr_standalone\01_test_connection.py` | Network, remote (RPC) service, acquisition status and settings templates | **No.** Read-only; never starts an acquisition |
| 2 | `scripts\nmr_standalone\02_run_1_scan.py` | One 1-scan acquisition with the simplified method, saves the raw FID | Sends the settings and **one START** |
| 3 | `scripts\nmr_standalone\03_run_4_scans.py` | One 4-scan acquisition with the simplified method, saves the raw FID | Sends the settings and **one START** |

What they never do: open the Chemyx pump or the Arduino needle, touch any HOME,
position or dose record, process a spectrum, or send START twice.

### How 02 and 03 acquire

By default they use the **simplified method** shared with `10_stage2only` and
`11_3InstrumentCheck` (`acquire_nmr` in `chemyx_lab\workflows\stage2_demo_nmr.py`):

1. No status check before START. You confirm on the touchscreen that the NMR is
   idle, then type `y`.
2. Read the two settings templates, send the settings (gain 12, auto gain off).
3. Send START once.
4. Every 2 s read the status until it shows **this** run (its receipt or export
   file) with the requested number of scans, and `Service/Acquire` reports the
   acquisition done. Limit 300 s.
5. Save the raw FID.

It never uses the NMR's `ResultCode`, so it does not stop at the
`NMR idle not confirmed; START skipped to avoid overlap` check that blocked
06/08 and `04 --nmr-check`.

Add `--experiment-worker` to use the **06/08 method** instead (strict idle check
before START, then completion judged by `ResultCode`). Comparing the two tells
you where a problem is:

| Default (simplified) | `--experiment-worker` | Meaning |
|---|---|---|
| PASS | PASS | The NMR works with both; 06/08 should work too |
| PASS | FAIL at the idle check | The NMR works; only 06/08's strict idle check blocks it. Bring both debug reports to HOME |
| FAIL | FAIL | A problem with the NMR, network or settings; follow the first FAIL's advice |

Both methods use the NMR address in `configs\machines\si6_real_COM4.yaml` and
the acquisition settings in `configs\experiments\si6_stage2only_6h.yaml` (the
same values as the `nmr:` section of `configs\experiments\10_stage2only.yaml`;
a test checks they stay equal). Only the scan count differs (1 or 4).

## Before you start

1. NMR switched on, NMReady software open, and idle on the touchscreen.
2. Ethernet cable connected between the laptop (or its USB-Ethernet adapter) and the NMR.
3. For steps 2 and 3: a sample or solvent tube in the NMR. The scan runs on whatever is in it.
4. No experiment running (06, 08, `04 --nmr-check`), and no other window running an NMR test.

## Commands

Copy **one block at a time** into PowerShell and press Enter.

Go to the repository folder:

```powershell
cd C:\code\chemyx_pump
```

If `python` is not recognized, use `.venv\Scripts\python.exe` in its place in
every command below.

### Step 1: connection test (read-only)

```powershell
python scripts\nmr_standalone\01_test_connection.py
```

Expected: `RESULT: PASS`. Fix any FAIL before step 2.
`RESULT: PASS (with warnings)` with `06/08 do not recognise this status reply as idle`
means the connection works and 02/03 can still run, but 06/08 would refuse to
start with that reply. Keep that debug report.

### Step 2: one 1-scan acquisition

```powershell
python scripts\nmr_standalone\02_run_1_scan.py
```

It runs a read-only pre-check first, then asks one question. Check the
touchscreen shows the NMR idle, type `y` and press Enter. Expected:
`RESULT: PASS  (START was sent once)` and `Averages  : 1 (requested scans: 1)`.

### Step 3: one 4-scan acquisition

```powershell
python scripts\nmr_standalone\03_run_4_scans.py
```

Type `y`. Expected: `RESULT: PASS` and `Averages  : 4 (requested scans: 4)`.

### Optional variations

Skip the `y` question (1 scan):

```powershell
python scripts\nmr_standalone\02_run_1_scan.py --yes
```

Skip the `y` question (4 scans):

```powershell
python scripts\nmr_standalone\03_run_4_scans.py --yes
```

Compare with the 06/08 method (1 scan):

```powershell
python scripts\nmr_standalone\02_run_1_scan.py --experiment-worker
```

Compare with the 06/08 method (4 scans):

```powershell
python scripts\nmr_standalone\03_run_4_scans.py --experiment-worker
```

Try a different NMR IP address for one run only (the YAML is not changed):

```powershell
python scripts\nmr_standalone\01_test_connection.py --host 169.254.30.54
```

Use the COM6 machine file (the NMR address in it is the same as COM4):

```powershell
python scripts\nmr_standalone\01_test_connection.py --machine-config configs\machines\si6_real_COM6.yaml
```

Show every option:

```powershell
python scripts\nmr_standalone\01_test_connection.py --help
```

### Network checks without Python

Use these if step 1 reports a network problem. Run each one separately.

Show the laptop's network adapters (the one cabled to the NMR should have a
`169.254.x.x` address, not `Media disconnected`):

```powershell
ipconfig
```

Check that the NMR answers on the cable:

```powershell
ping 169.254.30.54
```

Check that the NMR's remote-control port is open (`TcpTestSucceeded : True`):

```powershell
Test-NetConnection 169.254.30.54 -Port 5000
```

### Open the saved debug reports

```powershell
explorer results\nmr_standalone
```

## What each step of the output means

Script 01 prints five numbered steps:

| Step | Shows | PASS means |
|---|---|---|
| `[1/5] Configuration` | Which preset and machine YAML were used, the NMR address, timing, and any `NMR_*` environment variable overriding the YAML | The files exist and are valid |
| `[2/5] Network path` | The laptop address Windows uses toward the NMR, any web proxy, and whether TCP port 5000 accepts a connection | The cable, IP address and port work |
| `[3/5] NMReady remote (RPC) service` | `PingSpectrometer`, `RpcEnabled`, `RpcActive` replies | NMReady answers and remote control is enabled |
| `[4/5] Spectrometer state` | `SpectrometerStatus`, `OperationalMessages` (information only) | No standby warning |
| `[5/5] Acquisition readiness` | `iFlow/ExperimentStatus` (the reply 06/08 read before every START) and both settings templates | Both templates are JSON objects. `NMR IDLE` passes; an unrecognised reply is a warning; `NMR BUSY` fails |

Scripts 02 and 03 print `[1/5]` configuration, `[2/5]` network, `[3/5]` a
read-only status pre-check, `[4/5]` the acquisition, then `[5/5]` a check of
the saved file. During `[4/5]` each NMR request appears on its own line with
the time since the test began, plus `NMR:` progress messages. With the default
method it looks like this (times are illustrative):

```text
  NMR: Sending START (once)
  [   0.1 s] PUT iFlow/RunExperiment -> HTTP 200 in 0.00 s | START sent
             reply: {"ExperimentNumber": 2, "ResultCode": 1, "TimeStamp": "..."}
  [   2.3 s] GET iFlow/ExperimentStatus -> HTTP 200 in 0.02 s | ResultCode=1, NumberOfScansRun=0
  [  14.6 s] GET iFlow/ExperimentStatus -> HTTP 200 in 0.03 s | ResultCode=0, NumberOfScansRun=1, receipt 2/...
  NMR: 1/1 scans reported for this acquisition
  [  14.6 s] GET Service/Acquire -> HTTP 200 in 0.01 s
             reply: {"Status": {"InProgress": false, "LastAcquireSuccessful": true}}
  NMR: Acquisition complete; retrieving raw FID
```

If the pre-check status is one 06/08 would refuse, `[3/5]` says
`Note: 06/08 and 04 --nmr-check would refuse START with this status reply.`
The default method continues after your `y`; `--experiment-worker` stops there.

`[5/5]` reads the saved file's header: `##DATA TYPE` must be an FID, `LONG
DATE` is the NMR's own acquisition time, and `##.AVERAGES` should equal the
requested scans.

## Where results go

Each run makes a new folder; nothing is overwritten.

| File | Contents |
|---|---|
| `results\nmr_standalone\<date>_<time>_connection\nmr_debug_report.json` | Script 01: settings and their sources, network facts, every request and reply, findings |
| `results\nmr_standalone\<date>_<time>_1scan\nmr_debug_report.json` | Script 02: the same, plus the worker result and saved-file check |
| `results\nmr_standalone\<date>_<time>_1scan\nmr_raw\nmr_1scan_0001.dx` | Script 02: the raw FID (JCAMP-DX) |
| `results\nmr_standalone\<date>_<time>_4scan\...` | Script 03: same layout, `nmr_4scan_0001.dx` |

Large spectral text inside replies is shortened in the report (length and first
characters only); the saved `.dx` keeps the complete raw data. If something
fails that this page does not explain, bring that folder back to HOME.

## Troubleshooting: message → meaning → what to do

The script prints these explanations itself. This table collects them.

| Message contains | Meaning | What to do |
|---|---|---|
| `Machine YAML not found` / `FAILED:` under `[1/5]` | A config file is missing or invalid. No NMR request was sent | Fix the named file, or pass the right `--machine-config` |
| `NMR_* environment ... overrides the YAML` | An environment variable (for example `NMR_HOST`) replaces the YAML value, for this test and for 06/08 | Check the value is right, or remove the variable |
| `NOT a 169.254.x.x address` / `No network link to the NMR` | Windows has no Ethernet link to the NMR; traffic would go out another adapter (Wi-Fi) | Check the cable at both ends and that the NMR is on; run `ipconfig` and `ping 169.254.30.54` |
| `TIMEOUT` with a `169.254.x.x` laptop address | Laptop is on the NMR network but nothing answered | NMR off or still starting, wrong IP in `nmr.host`, or a firewall. Confirm the NMR's IP in its network settings |
| `REFUSED` / `nothing accepts connections on port 5000` | The NMR computer answered but NMReady's remote server is not listening | Open/restart NMReady on the touchscreen (power-cycle if frozen); check `nmr.port` is 5000 |
| `Web proxy for NMR requests` | Windows or environment proxy settings would route NMR requests to a proxy server. The experiment uses the same settings | Add `169.254.30.54` to the proxy exceptions, or ask IT |
| `not connected to the spectrometer` | NMReady runs but cannot talk to the NMR hardware | Restart NMReady or power-cycle the NMR; wait for start-up to finish |
| `Remote control (RPC API) is disabled` / `HTTP 403 Forbidden` | Status can be read, but settings changes and START are refused | Touchscreen: **Setup → System → Remote → Enable**. If that is missing, the vendor manual says `config/pygui.cfg` needs `RPC_API_ENABLED = True`, then restart NMReady |
| `The NMR is in standby mode` | It may not acquire until woken | Wake it on the touchscreen |
| `NMR BUSY` / `an acquisition is running` | Another acquisition is in progress (touchscreen, other program, earlier run). Test 01 fails; the default 02/03 method does not check, so look at the touchscreen yourself | Let it finish or cancel it on the touchscreen; run step 1 again |
| `06/08 do not recognise this status reply as idle` (WARN), `NMR UNKNOWN: ResultCode=2` (or another number) | The status reply is not one 06/08 understand as idle. **This is exactly what makes 06/08 print `NMR idle not confirmed; START skipped to avoid overlap`.** The vendor manual's own status example also shows `ResultCode 2` without defining it | 02/03 still run with the default method. Do **not** edit 06/08 to accept it; bring the `connection` debug report to HOME so the real reply can be reviewed |
| `NMR UNKNOWN: ... arrived as plain text` | The NMR sent valid JSON the 06/08 driver could not read as an object (leading spaces or a non-JSON content type). The default 02/03 method reads it | Bring the debug report to HOME |
| `settings template could not be read` (HTTP 404) | This NMReady version lacks the iFlow interface used by every method here | Bring the debug report to HOME |
| `the simplified method's code is missing` | `chemyx_lab\workflows\stage2_demo_nmr.py` (shared with 10/11) is not on this laptop | Copy it with these scripts, or add `--experiment-worker` |
| `Another program on this laptop is using the NMR` | A 06/08 run, `04 --nmr-check` or another test window holds the NMR lock | Let it finish or close it; never test during a live run |
| `never reported this acquisition with the requested scans` (START was sent once) | Default method: after START, no status reply showed this run's receipt with the requested scans within 300 s | Check the touchscreen: did a 1D experiment run, and how many scans? If nothing ran, the NMR ignored START. Bring the report to HOME |
| `Service/Acquire never said the acquisition was done` | Default method: the scans were reported, but `Service/Acquire` never showed `InProgress=false` and `LastAcquireSuccessful=true` | Bring the report to HOME (it holds the real `Service/Acquire` replies); try `--experiment-worker` for comparison |
| `The NMR answered START with an error` | The START reply contained an error | Read the message on the touchscreen; bring the report to HOME |
| `not confirmed complete` (START was sent once) | `--experiment-worker` only: START went out, but the status never proved the new acquisition finished within 300 s | Check the touchscreen: did a 1D experiment run? If status never showed `BUSY`, the NMR may have ignored or rejected START; read the START reply line. Bring the report to HOME |
| `no raw FID was returned or found` | The acquisition finished but the reply had no raw FID | The NMR saved its own copy (its `JDX_Filename` appears in the message or last status); copy it from the NMR if needed |
| `averaged N scan(s), not the requested M` (WARN) | The NMR may not have applied the scan setting | Bring the report to HOME |
| `Stopped with Ctrl+C` | Test interrupted. If START was sent, the NMR may still be acquiring | Let it finish or cancel it on the touchscreen; run step 1 before trying again |

Exit codes (for reference): `0` pass, `1` fail, `2` configuration error,
`3` you answered something other than `y`, `130` stopped with Ctrl+C.

## How this relates to other NMR tools

- `10_stage2only` and `11_3InstrumentCheck`: use the same simplified
  `acquire_nmr` as the default 02/03. These tests do not change those scripts.
- `06`/`08` Stage 2 runs: use the worker that `--experiment-worker` runs, with
  the same settings. These tests do not change the experiment or its configuration.
- `scripts\04_si6_fixed_time_run.py --nmr-check`: one 8-scan acquisition
  through the 06/08 worker, via the full 48 h preset and without the
  per-request trace. Use these standalone tests for debugging.
- `scripts\diagnostics\03_check_nmr_connection.py` and
  `smoke_test\02_smoke_nmr.py`: older read-only checks that do **not** read
  `iFlow/ExperimentStatus`, so a pass there cannot rule out the
  `NMR idle not confirmed` problem. Test 01 does read it.

## Offline check on HOME

The scripts have no mock mode. Their automated tests run against a fake NMR on
`127.0.0.1` and never contact the real address:

```powershell
python -m pytest tests\test_nmr_standalone.py -q
```
