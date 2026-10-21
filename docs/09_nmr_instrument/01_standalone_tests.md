> [Documentation home](../00_START_HERE.md) | [NMR instrument](00_START_HERE.md)

# NMR standalone tests: connection, 1 scan, 4 scans

Five scripts test the NMR on its own, the same way the Arduino and Chemyx have
their own scripts. Each one prints every NMR request, explains what the reply
means, and, if something fails, says **why** and **what to do**. Everything is
also saved to a debug report.

| Script | What it does | Completion method | Changes anything on the NMR? |
|---|---|---|---|
| `01_test_connection.py` | Network, remote (RPC) service, acquisition status and settings templates | (none) | **No.** Read-only; never starts an acquisition |
| `02_run_1_scan.py` | One 1-scan acquisition, saves the raw FID | Newer, strict (receipt + scan count + `Service/Acquire`) | Sends the settings and **one START** |
| `03_run_4_scans.py` | One 4-scan acquisition, saves the raw FID | Newer, strict | Sends the settings and **one START** |
| `04_run_1_scan_simple.py` | One 1-scan acquisition, saves the raw FID | Simple legacy-style (`ResultCode` 0) | Sends the settings and **one START** |
| `05_run_4_scans_simple.py` | One 4-scan acquisition, saves the raw FID | Simple legacy-style | Sends the settings and **one START** |

All are in `scripts\nmr_standalone\`. What they never do: open the Chemyx pump
or the Arduino needle, touch any HOME, position or dose record, process a
spectrum, or send START twice.

## How the acquisition scripts differ

All four acquisition scripts start the same way:
- Read the NMR address from `configs\machines\si6_real_COM4.yaml`.
- Read the acquisition settings from `configs\experiments\si6_stage2only_6h.yaml`:
  gain 12, auto gain off, centre 5 ppm, width 20 ppm, and 1 or 4 scans.
- Read the two settings templates and wait for your `y`. There is no status
  check before START: you confirm on the touchscreen that the NMR is idle.
- Send the settings, then **one** `PUT /interfaces/iFlow/RunExperiment`.
  START is never repeated, even when its outcome is uncertain.

They differ in how they decide the acquisition has **finished**.

### 02 / 03: newer, strict completion

The method shared with `10_stage2only` and `11_3InstrumentCheck` (`acquire_nmr`
in `chemyx_lab\workflows\stage2_demo_nmr.py`). Every 2 s it reads
`ExperimentStatus` until **all** of these hold:

- the reply shows **this** run (its receipt matches START's, or its export file is this run's file);
- `NumberOfScansRun` has reached the requested scans;
- `GET /interfaces/Service/Acquire` reports `InProgress: false` and `LastAcquireSuccessful: true`.

Limit 300 s for everything. It never uses `ResultCode`. Add `--experiment-worker`
to run the 06/08 method instead (strict idle check before START).

### 04 / 05: simple legacy-style completion

The completion rule of the older three-instrument test (`run_nmr_acquisition`),
which saved a real 8-scan FID on 2026-08-10. It uses the NMR driver's own
`wait_for_idle`:

1. Right after START, read `ExperimentStatus`, then again every **2 s**.
2. **Still running** (keep waiting): any nonzero `ResultCode`, a running flag set
   to true, or `PercentComplete` below 100.
3. **Finished:** `ResultCode` 0 (with no running flag or percentage saying otherwise).
4. Take the raw FID (`JDX_FileContents_TD`) from that same reply and save it.
5. If it has not finished after **300 s**, stop, print the last status reply and
   save the debug report. START is not sent again.

No other endpoint, receipt or scan count is required. Two small additions to
the old code, both about the saved data, not about deciding completion:

- It saves the raw FID field. The old code saved the first text field it found,
  which could have been the processed spectrum (`JDX_FileContents_FD`). On
  2026-08-10 that field was empty, so the old code saved the raw FID too.
- It refuses to save a raw FID identical to the one the NMR held before START,
  so an old acquisition can never be reported as new. The old code had no such
  check.

### Comparing the results on WORK

| 02 (strict) | 04 (simple) | What it means |
|---|---|---|
| PASS | PASS | Both completion methods work with this NMR |
| FAIL (timeout) | PASS | The NMR does not report the receipt, scan count or `Service/Acquire` fields as expected; the simple method works. Bring the 02 debug report to HOME |
| PASS | FAIL (timeout) | The NMR keeps a nonzero `ResultCode` after a run; the strict method works. Bring the 04 debug report to HOME |
| FAIL | FAIL | A problem with the NMR, network or settings; follow the first FAIL's advice and run 01 |

## Before you start

1. NMR switched on, NMReady software open, and idle on the touchscreen.
2. Ethernet cable connected between the laptop (or its USB-Ethernet adapter) and the NMR.
3. For the acquisition scripts: a sample or solvent tube in the NMR. The scan runs on whatever is in it.
4. No experiment running (06, 08, 10, `04 --nmr-check`), and no other window running an NMR test.

## Commands for WORK

Copy **one block at a time** into PowerShell and press Enter. Run the
acquisition scripts one at a time, and only when the touchscreen shows the NMR idle.

Go to the repository folder:

```powershell
cd C:\code\chemyx_pump
```

If `python` is not recognized, use `.venv\Scripts\python.exe` in its place in
every command below.

**Connection test:**

```powershell
python scripts\nmr_standalone\01_test_connection.py
```

Expected: `RESULT: PASS`. Fix any FAIL before acquiring.
`RESULT: PASS (with warnings)` with `06/08 do not recognise this status reply as idle`
means the connection works and scripts 02 to 05 can still run, but 06/08 would
refuse to start with that reply. Keep that debug report.

**New completion method — 1 scan:**

```powershell
python scripts\nmr_standalone\02_run_1_scan.py
```

**New completion method — 4 scans:**

```powershell
python scripts\nmr_standalone\03_run_4_scans.py
```

**Simple legacy-style completion — 1 scan:**

```powershell
python scripts\nmr_standalone\04_run_1_scan_simple.py
```

**Simple legacy-style completion — 4 scans:**

```powershell
python scripts\nmr_standalone\05_run_4_scans_simple.py
```

Each acquisition script asks one question before START. Check the touchscreen
shows the NMR idle, type `y` and press Enter. Scripts 04/05 ask exactly:
`Confirm NMR touchscreen shows IDLE and ready to acquire [y/N]:`.
Expected result: `RESULT: PASS  (START was sent once)` and
`Averages  : 1 (requested scans: 1)` (or 4).

### Optional variations

Compare with the 06/08 method (1 scan):

```powershell
python scripts\nmr_standalone\02_run_1_scan.py --experiment-worker
```

Compare with the 06/08 method (4 scans):

```powershell
python scripts\nmr_standalone\03_run_4_scans.py --experiment-worker
```

Skip the `y` question (1 scan, simple method):

```powershell
python scripts\nmr_standalone\04_run_1_scan_simple.py --yes
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
python scripts\nmr_standalone\04_run_1_scan_simple.py --help
```

### Network checks without Python

Use these if the connection test reports a network problem. Run each one separately.

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

## When START succeeds but completion detection fails

The script says `START was sent once` and the result is a timeout, or
"no raw FID", or "same raw FID as before START".

**An uncertain completion result is not permission to send another START.** Do
not rerun a test until you have checked the instrument:

1. Look at the NMR touchscreen. Is it still acquiring, finished, or showing an error?
2. If it is still acquiring, wait for it to finish (or cancel it on the touchscreen).
3. When the touchscreen shows the NMR idle, run the connection test (01) once.
4. Keep the debug report of the failed run. It has every status reply received,
   including the last one, and shows exactly which condition was never met.
5. Then you may run the other completion method for comparison (02 after 04, or 04 after 02).

## What the output means

**01** prints five steps:

| Step | Shows | PASS means |
|---|---|---|
| `[1/5] Configuration` | Which preset and machine YAML were used, the NMR address, timing, and any `NMR_*` environment variable overriding the YAML | The files exist and are valid |
| `[2/5] Network path` | The laptop address Windows uses toward the NMR, any web proxy, and whether TCP port 5000 accepts a connection | The cable, IP address and port work |
| `[3/5] NMReady remote (RPC) service` | `PingSpectrometer`, `RpcEnabled`, `RpcActive` replies | NMReady answers and remote control is enabled |
| `[4/5] Spectrometer state` | `SpectrometerStatus`, `OperationalMessages` (information only) | No standby warning |
| `[5/5] Acquisition readiness` | `iFlow/ExperimentStatus` (the reply 06/08 read before every START) and both settings templates | Both templates are JSON objects. `NMR IDLE` passes; an unrecognised reply is a warning; `NMR BUSY` fails |

**04/05** print one plain line per stage (times are illustrative):

```text
  NMR SETTINGS: LOADED
  NMR CONNECTION: OK (status ResultCode=0; information only, START does not depend on it)
  NMR SETTINGS TEMPLATES: READ
  OPERATOR CONFIRMATION: YES
  NMR CONFIGURATION: SENT
  NMR START: SENT
  [   0.1 s] NMR STATUS: WAITING (ResultCode=1, NumberOfScansRun=0)
  [   2.1 s] NMR STATUS: WAITING (ResultCode=1, NumberOfScansRun=0)
  [  14.2 s] NMR STATUS: COMPLETE (ResultCode=0, NumberOfScansRun=1)
  RAW FID: RECEIVED
  DX FILE: SAVED results\nmr_standalone\..._1scan_simple\nmr_raw\nmr_1scan_simple.dx
RESULT: PASS  (START was sent once)
```

Every other request also appears on its own line, with its HTTP result and time.

**02/03** print `[1/5]` configuration, `[2/5]` network, `[3/5]` a read-only
status pre-check, `[4/5]` the acquisition (each request plus `NMR:` progress
messages such as `1/1 scans reported` and the `Service/Acquire` reply), then
`[5/5]` the saved-file check. If the pre-check status is one 06/08 would refuse,
`[3/5]` says so; the default method continues after your `y`, and
`--experiment-worker` stops there.

The final file check of every acquisition script reads the saved file's header:
`##DATA TYPE` must be an FID, `LONG DATE` is the NMR's own acquisition time, and
`##.AVERAGES` should equal the requested scans.

## Where results go

Each run makes a new folder; nothing is overwritten.

| File | Contents |
|---|---|
| `results\nmr_standalone\<date>_<time>_connection\nmr_debug_report.json` | 01: settings and their sources, network facts, every request and reply, findings |
| `results\nmr_standalone\<date>_<time>_1scan\nmr_debug_report.json` | 02: the same, plus the acquisition result and saved-file check |
| `results\nmr_standalone\<date>_<time>_1scan\nmr_raw\nmr_1scan_0001.dx` | 02: the raw FID (JCAMP-DX) |
| `results\nmr_standalone\<date>_<time>_4scan\...` | 03: same layout, `nmr_4scan_0001.dx` |
| `results\nmr_standalone\<date>_<time>_1scan_simple\nmr_debug_report.json` | 04: the same report, including every status poll |
| `results\nmr_standalone\<date>_<time>_1scan_simple\nmr_raw\nmr_1scan_simple.dx` | 04: the raw FID |
| `results\nmr_standalone\<date>_<time>_4scan_simple\...` | 05: same layout, `nmr_4scan_simple.dx` |

Large spectral text inside replies is shortened in the report (length and first
characters only); the saved `.dx` keeps the complete raw data. If something
fails that this page does not explain, bring that folder back to HOME.

## Troubleshooting: message → meaning → what to do

The scripts print these explanations themselves. This table collects them.

| Message contains | Meaning | What to do |
|---|---|---|
| `Machine YAML not found` / `FAILED:` under the first step | A config file is missing or invalid. No NMR request was sent | Fix the named file, or pass the right `--machine-config` |
| `NMR_* environment ... overrides the YAML` | An environment variable (for example `NMR_HOST`) replaces the YAML value, for these tests and for 06/08 | Check the value is right, or remove the variable |
| `NOT a 169.254.x.x address` / `No network link to the NMR` | Windows has no Ethernet link to the NMR; traffic would go out another adapter (Wi-Fi) | Check the cable at both ends and that the NMR is on; run `ipconfig` and `ping 169.254.30.54` |
| `TIMEOUT` with a `169.254.x.x` laptop address | Laptop is on the NMR network but nothing answered | NMR off or still starting, wrong IP in `nmr.host`, or a firewall. Confirm the NMR's IP in its network settings |
| `REFUSED` / `nothing accepts connections on port 5000` | The NMR computer answered but NMReady's remote server is not listening | Open/restart NMReady on the touchscreen (power-cycle if frozen); check `nmr.port` is 5000 |
| `NMR CONNECTION: FAILED` (04/05) | The connection check or the first status read failed | Run 01; it explains the network failure in detail |
| `Web proxy for NMR requests` | Windows or environment proxy settings would route NMR requests to a proxy server. The experiment uses the same settings | Add `169.254.30.54` to the proxy exceptions, or ask IT |
| `not connected to the spectrometer` | NMReady runs but cannot talk to the NMR hardware | Restart NMReady or power-cycle the NMR; wait for start-up to finish |
| `Remote control (RPC API) is disabled` / `HTTP 403 Forbidden` | Status can be read, but settings changes and START are refused | Touchscreen: **Setup → System → Remote → Enable**. If that is missing, the vendor manual says `config/pygui.cfg` needs `RPC_API_ENABLED = True`, then restart NMReady |
| `The NMR is in standby mode` | It may not acquire until woken | Wake it on the touchscreen |
| `NMR BUSY` / `an acquisition is running` | Another acquisition is in progress (touchscreen, other program, earlier run). 01 fails; the acquisition scripts do not check, so look at the touchscreen yourself | Let it finish or cancel it on the touchscreen; run 01 again |
| `06/08 do not recognise this status reply as idle` (WARN), `NMR UNKNOWN: ResultCode=2` (or another number) | The status reply is not one 06/08 understand as idle. **This is exactly what makes 06/08 print `NMR idle not confirmed; START skipped to avoid overlap`.** The vendor manual's own status example also shows `ResultCode 2` without defining it | Scripts 02 to 05 still run. Do **not** edit 06/08 to accept it; bring the `connection` debug report to HOME |
| `NMR UNKNOWN: ... arrived as plain text` | The NMR sent valid JSON the driver could not read as an object (leading spaces or a non-JSON content type) | Bring the debug report to HOME |
| `settings template could not be read` / `NMR SETTINGS TEMPLATES: FAILED` (HTTP 404) | This NMReady version lacks the iFlow interface every method here uses | Bring the debug report to HOME |
| `the simplified method's code is missing` (02/03) | `chemyx_lab\workflows\stage2_demo_nmr.py` (shared with 10/11) is not on this laptop | Copy it with these scripts, use `--experiment-worker`, or use 04/05 |
| `Another program on this laptop is using the NMR` | A 06/08 run, `04 --nmr-check` or another test window holds the NMR lock | Let it finish or close it; never test during a live run |
| `Sending START failed` | The START request itself failed; it may or may not have reached the NMR | Check the touchscreen. Do not send START again until it shows the NMR idle |
| `NMR STATUS: TIMEOUT after 300 s` / `did not report completion` (04/05) | START was sent once, but the status kept saying running (nonzero `ResultCode`) for 300 s. The last status reply is printed | Follow [When START succeeds but completion detection fails](#when-start-succeeds-but-completion-detection-fails). If the touchscreen shows the run finished, this NMR keeps a nonzero code after a run: bring the 04 report to HOME |
| `Reading ExperimentStatus failed while waiting` (04/05) | A status read failed after START; the legacy method stops at the first failed read | Check the touchscreen, then run 01 |
| `RAW FID: NOT RETURNED` / `contained no raw FID` (04/05) | Completion was reported, but the reply had no raw FID. If it happened right after START, the NMR may not have started yet | Check the touchscreen; look for the file on the NMR (its `JDX_Filename` is printed). Do not resend START |
| `SAME AS BEFORE START` / `same raw FID as before START` (04/05) | The NMR returned the previous acquisition's data, so completion was read too early. It was not saved as new | Check the touchscreen; bring the report to HOME |
| `never reported this acquisition with the requested scans` (02/03) | After START, no status reply showed this run's receipt with the requested scans within 300 s | Check the touchscreen: did a 1D experiment run, and how many scans? Bring the report to HOME; try 04 for comparison once idle |
| `Service/Acquire never said the acquisition was done` (02/03) | The scans were reported, but `Service/Acquire` never showed `InProgress=false` and `LastAcquireSuccessful=true` | Bring the report to HOME (it holds the real `Service/Acquire` replies); try 04 for comparison once idle |
| `The NMR answered START with an error` (02/03) | The START reply contained an error | Read the message on the touchscreen; bring the report to HOME |
| `not confirmed complete` (02/03 with `--experiment-worker`) | START went out, but the status never proved the new acquisition finished within 300 s | Check the touchscreen; bring the report to HOME |
| `averaged N scan(s), not the requested M` (WARN) | The NMR may not have applied the scan setting | Bring the report to HOME |
| `Stopped with Ctrl+C` | Test interrupted. If START was sent, the NMR may still be acquiring | Let it finish or cancel it on the touchscreen; run 01 before trying again |

Exit codes (for reference): `0` pass, `1` fail, `2` configuration error,
`3` you answered something other than `y`, `130` stopped with Ctrl+C.

## How this relates to other NMR tools

- `10_stage2only` and `11_3InstrumentCheck`: use the same strict-completion
  `acquire_nmr` as 02/03. These tests do not change those scripts.
- `06`/`08` Stage 2 runs: use the worker that `--experiment-worker` runs, with
  the same settings. These tests do not change the experiment or its configuration.
- `scripts\01_three_instrument_system_test.py` (older): its acquisition
  (`run_nmr_acquisition`) is the source of the 04/05 completion rule.
- `scripts\04_si6_fixed_time_run.py --nmr-check`: one 8-scan acquisition
  through the 06/08 worker, via the full 48 h preset and without the
  per-request trace. Use these standalone tests for debugging.
- `scripts\diagnostics\03_check_nmr_connection.py` and
  `smoke_test\02_smoke_nmr.py`: older read-only checks that do **not** read
  `iFlow/ExperimentStatus`, so a pass there cannot rule out the
  `NMR idle not confirmed` problem. 01 does read it.

## Offline check on HOME

The scripts have no mock mode. Their automated tests run against a fake NMR on
`127.0.0.1` and never contact the real address:

```powershell
python -m pytest tests\test_nmr_standalone.py -q
```
