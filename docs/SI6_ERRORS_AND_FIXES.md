# Si6 errors and fixes

Use [SI6_SIMPLE_RUN_GUIDE.md](SI6_SIMPLE_RUN_GUIDE.md) for the operating order.
The recommended individual-instrument test is now the
[standalone bench checkout](SI6_SIMPLE_INSTRUMENT_CHECK.md):
`python scripts\03_simple_instrument_checkout.py --live`. Its report is in
`results/instrument_checks`, with no chemical peak or integration requirement.
A blank/background NMR spectrum is acceptable when acquisition and raw saving
succeed; numerical processing limitations are separately UNAVAILABLE.
UNKNOWN starting positions still require the existing manual HOME procedure.
The `$Si6Integration` commands below concern the older optional two-stage test.
Run from `C:\code\chemyx_pump` using the installed Python. Commands below are
PowerShell. Define these once; select the COM6 machine file instead if needed:

```powershell
cd C:\code\chemyx_pump
$env:Path = "C:\code\chemyx_pump\.venv\Scripts;" + $env:Path
$Si6Machine = 'configs\machines\si6_real_COM4.yaml'
$Si6Needle = 'arduino\configs\arduino_real_COM3.yaml'
$Si6Manual = 'configs\chemyx\manual_control.yaml'
$Si6Integration = 'configs\experiments\si6_real_10min_checkout.yaml'
$Si6Production = 'configs\experiments\si6_real_two_stage.yaml'
$Si6Run = 'C:\code\chemyx_pump\results\experiments\YOUR_ACTUAL_RUN_FOLDER'
```

Replace `YOUR_ACTUAL_RUN_FOLDER` with the printed `Results:` folder.
`--validate-config` and environment checks open no instruments. Live communication
and manual commands below contact hardware; use them only on WORK with ports free.

| Error message or symptom | Likely cause | Exact fix | Command to verify |
| --- | --- | --- | --- |
| `Fill checkout.test_fluid before running the checkout` | Old Python checkout resolver is still installed. | Transfer current `si6_checkout.py`, controller, runner and checkout YAML (prefer a fresh complete offline source package). Fluid description is now optional and records `unspecified`; no HOME reset required. | `python scripts\02_si6_experiment.py --validate-config --workflow-config $Si6Integration --machine-config $Si6Machine --arduino-config $Si6Needle` |
| `checkout.routing` missing/invalid | Older descriptive-field validation. | Update the same files. Routing description is optional; physically check tubing yourself. The startup plan does not claim verification. | Same checkout validation command above. |
| `Fill ... Channel 2 volume_ml/rate_ml_min` | Actual physical transfer is unset, nonpositive or nonfinite. | Fill the single Channel 2 `initial_stage.after_monitoring` infusion with approved volume/rate. Do not substitute production dose or invent quantities. | Same checkout validation command above. |
| Invalid YAML / unknown field / needs a mapping | Indentation, wrong key or schema mismatch. | Correct the named file/key, preserving the existing schema. Open with Notepad. | `notepad $Si6Integration`, then checkout validation above. |
| Wrong Python interpreter | Terminal resolves a different installation. | Prefix PATH with the already installed `.venv\Scripts` as above; no activation/reinstallation each run. | `python -c "import sys; print(sys.executable); print(sys.version)"` |
| `ModuleNotFoundError` | Selected interpreter lacks the installed dependencies. | Select the correct interpreter first. If actually missing, use the transferred wheelhouse: `powershell -NoProfile -ExecutionPolicy Bypass -File offline\install_offline.ps1` (no `-RunTests`). | `python scripts\verify_si6_environment.py --machine-config $Si6Machine --arduino-config $Si6Needle` |
| Chemyx COM4/COM6 fails to open / access denied / no HELP response | Wrong port, disconnected adapter, competing serial tool or baud mismatch. | Match the actual port; manual YAML `machine_config` must match automation. Close competing tools and check the configured 115200 baud against the pump. | `python -m serial.tools.list_ports -v`; then `python scripts\chemyx_control.py status --channel both --live --config $Si6Manual` |
| Arduino COM3 fails to open / wrong device/version / no READY | Port/cable/ownership issue or mismatched commissioned firmware. | Check the data cable and actual port; close serial monitors/other runs. Retain the commissioned firmware; inspect the reported mismatch rather than guessing calibration. | `python -m serial.tools.list_ports -v`; then `python arduino\scripts\needle_control.py status --live --config $Si6Needle` |
| NMR PING unavailable / timeout / RPC failure | Instrument Ethernet, address or remote-control/RPC problem. | Check the configured `169.254.30.54:5000`, local Ethernet and enabled remote RPC on the instrument. Internet is not needed. | `Test-NetConnection 169.254.30.54 -Port 5000`; then communication command below. |
| Arduino HOME unknown / unconfirmed / pending recovery | Interrupted/untracked movement, invalid saved state or changed calibration. | Physically place the stopped needle at sampling HOME=0; run `home` and confirm `y`. UNKNOWN recovery establishes the operator-confirmed reference without movement. It does not clear unrelated faults. | `python arduino\scripts\needle_control.py home --live --config $Si6Needle`; then `python arduino\scripts\needle_control.py status --live --config $Si6Needle` |
| Chemyx HOME unknown / reference settings differ | Untracked move, STOP, interrupted transfer or changed HOME identity. | Inspect the saved state and actual plunger/fluid. If needed, physically establish the intended reference and intentionally `set-home`; do not pretend an unknown displacement is zero. Chemyx `home` cannot recover UNKNOWN automatically. | `python scripts\chemyx_control.py status --channel both --config $Si6Manual`; when physically verified, `python scripts\chemyx_control.py set-home --channel 1 --live --config $Si6Manual` (select channel 2 if appropriate). |
| Capacity exceeded / infuses more than syringe contains / loaded volume missing | Actual loading or transfer/geometry settings are inconsistent. | Verify installed syringes and loading. Set checkout Channel 2 `initial_retained_volume_ml` to the actual amount; check requested test dose and capacity/margin. Manual C2 moves need verified `home.syringe_volume_at_home_ml`. | Checkout validation above; `python scripts\chemyx_control.py verify --channel both --config $Si6Manual` for manual settings. |
| Pump STOP not positively confirmed | Lost connection, rejected response or uncertain active motion. | Use physical STOP/intervention if necessary; inspect the pump and run journal. Do not command cleanup or restart merely because software timed out. | Once the port is free: `python scripts\chemyx_control.py stop --channel both --live --config $Si6Manual`; inspect status afterward (STOP may mark position UNKNOWN). |
| `Experiment already reserved` / existing dose record | This physical ID already has a RESERVED, dispatched or confirmed record; even a failed startup can reserve it. | Inspect its run, receipt and reactor. No automatic resume. Keep reservations and uncertainties; never delete/edit receipts or change ID to repeat a possibly delivered dose. A genuinely new prepared reaction/checkout needs its own ID after reconciliation. | `Get-Content "$Si6Run\channel_2_dose\dose_record.json"`; `python scripts\chemyx_control.py status --channel 2 --config $Si6Manual` |
| Manual Channel 2 infusion authorization error | Standalone infusion requires explicit maintenance authorization and waste routing; dose records may also block it. | Disconnect from reactor and route to waste, then use the supported flag. RESERVED doses still block infusion; uncertain doses block movement. Automatic workflow needs no extra flag. | `python scripts\chemyx_control.py infuse --channel 2 --live --allow-channel2-infusion --config $Si6Manual` — only after permitted physical setup. |
| NMR acquisition fails / timeout | Failed physical acquisition or transport; machine `nmr.max_wait_seconds` bounds waiting. | Inspect NMR status and raw output; fix actual communication/acquisition cause. No fabricated spectrum. Checkout is incomplete; reconcile any dose state before a subsequent run. | `Get-Content "$Si6Run\logs\experiment_journal.jsonl" -Tail 40`; communication command below. |
| ACME processing unavailable | Recognized numerical failure, unusable input, or a different unexpected processor/IO error. | Retain raw file and diagnostics. Typed recoverable numerical failure continues after verified cleanup; unexpected crashes/storage failures remain failures. Inspect numerical-unavailability/processing-failure records. | `Get-ChildItem "$Si6Run\step_1\nmr_processed" -Recurse -Filter '*failure*.json'`; `Get-Content "$Si6Run\logs\experiment_journal.jsonl" -Tail 40` |
| Target peak not detected / ordinary QC warning | No relevant chemistry peak, weak signal or untrusted solvent evidence. | Timed Stage 1 and checkout do not stop for missing target/ordinary QC. Stage 2 cannot use untrusted evidence for early completion. Check sample delivery/solvent and inspect spectra; do not turn missing data into zero-area completion. | `Get-Content "$Si6Run\step_1\analysis\time_series.csv"`; inspect Stage 2 table similarly. |
| No Stage 1 raw NMR file | Old deadline behavior, overall timeout, empty retrieval or real acquisition failure. | Current checkout permits its first acquisition past the nominal five-minute deadline within the overall timeout. No raw/processing evidence now makes the checkout fail and prevents the test dose. Fix the logged cause. | `Get-ChildItem "$Si6Run\step_1\nmr_raw"`; `Get-Content "$Si6Run\final_qc\checkout_report.md"` |
| No Stage 2 raw NMR file | Stage 1 failed, C2 completion uncertain, overall timeout or Stage 2 acquisition failure. | Read dose and journal first. Stage 2 never overrides an unconfirmed dose. Do not redose to obtain a second spectrum. | `Get-Content "$Si6Run\channel_2_dose\dose_record.json"`; `Get-ChildItem "$Si6Run\step_2\nmr_raw"` |
| Checkout longer than ten minutes / overall timeout reached | Existing cycle takes 452 s plus NMR/processing per stage; test dose adds time. | More than ten minutes is expected (normal mock 16:12). Default overall timeout is 30 minutes at safe boundaries. If genuinely too short for approved parameters, review `checkout.overall_timeout_minutes`; do not hide a failed/uncertain command by raising it. | `Get-Content "$Si6Run\final_qc\checkout_report.md"`; `Get-Content "$Si6Run\logs\live_status.json"` |
| Unexpected termination / Ctrl+C / power loss | Interrupted software or physical action. | Inspect actual needle/plungers/reactor, journal and dose intent. Active action may be uncertain. Reconcile first; no automatic partial-run resume, copied HOME trust or replay. | `Get-Content "$Si6Run\logs\experiment_journal.jsonl" -Tail 50`; Arduino status and saved Chemyx status above. |
| Output folder not found | Wrong working directory, wrong assumed run name or failure before output allocation. | Use printed `Results:` path; folders are dated and may have numeric suffixes. Failure before opening services may create no run. | `Get-ChildItem results\experiments -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name` |
| Release hash / missing resource / offline asset mismatch | Old/incomplete source transfer, incompatible wheels or source edits since the archived package. | Transfer a fresh complete source/offline package; preserve WORK runtime records. Python 3.11 Windows x64 is the wheel target. Intentional operator YAML differences are reported separately; source hash failures need the correct package. | `python offline\verify_bundle.py`; `python scripts\verify_si6_environment.py --machine-config $Si6Machine --arduino-config $Si6Needle` |

Communication-only verification, no commanded motion/acquisition (WORK only):

```powershell
python scripts\01_three_instrument_system_test.py --live --communications-only --workflow-config $Si6Production --machine-config $Si6Machine --arduino-config $Si6Needle
```

Successful recovery means the reported cause is resolved, saved references match
the actual rig, required STOP/communication evidence succeeds, and existing dose
records remain intact. Software acknowledgments do not independently measure
physical delivery. State preservation: [RIG_STATE_BACKUP.md](RIG_STATE_BACKUP.md).
