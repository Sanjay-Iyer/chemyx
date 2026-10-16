# Si6: manual tests → short checkout → real experiment

Use this guide top to bottom on **WORK**, with current code/dependencies already
installed. Run individual manual commands as needed; STOP and set-home are not
routine actions to repeat after each successful move. Errors: [SI6_ERRORS_AND_FIXES.md](SI6_ERRORS_AND_FIXES.md).

## STEP 1 — Open Python

Open PowerShell. Select the installed offline Python without activation:

```powershell
cd C:\code\chemyx_pump
$env:Path = "C:\code\chemyx_pump\.venv\Scripts;" + $env:Path
python -c "import sys; print(sys.executable); print(sys.version)"
```

If using a different verified Python, keep that interpreter instead. Do not
reinstall packages for each experiment. Set the file choices once for this terminal:

```powershell
$Si6Machine = 'configs\machines\si6_real_COM4.yaml'
$Si6Needle = 'arduino\configs\arduino_real_COM3.yaml'
$Si6Manual = 'configs\chemyx\manual_control.yaml'
$Si6Checkout = 'configs\experiments\si6_real_10min_checkout.yaml'
$Si6Production = 'configs\experiments\si6_real_two_stage.yaml'
```

**COM6 instead of COM4:** run this alternative before the steps below:

```powershell
$Si6Machine = 'configs\machines\si6_real_COM6.yaml'
```

In the manual YAML, `machine_config` must select the **same** COM4/COM6 file.
Arduino remains COM3; the NMR endpoint comes from the machine YAML.

## STEP 2 — Manual Arduino needle test

**HOME = DOWN = 0** (in flask); **UP = 1** (out of flask).
Saved software position is not independently measured physical position.

Status — contacts Arduino, no movement:

```powershell
python arduino\scripts\needle_control.py status --live --config $Si6Needle
```

If UNKNOWN, physically place the stopped needle at the correct sampling HOME,
then run `home` below and confirm `y` to establish zero without motor movement.
If tracking is valid, skip re-establishing HOME and test UP/DOWN:

```powershell
python arduino\scripts\needle_control.py up --live --config $Si6Needle
```

```powershell
python arduino\scripts\needle_control.py down --live --config $Si6Needle
```

Repeat UP/DOWN individually if needed. Each manual movement asks one `y`.

Set HOME — only when intentionally recording the current physically verified,
stopped sampling position as zero; no motor movement:

```powershell
python arduino\scripts\needle_control.py set-home --live --config $Si6Needle
```

Return HOME — moves to saved zero when known; UNKNOWN uses the manual confirmation above:

```powershell
python arduino\scripts\needle_control.py home --live --config $Si6Needle
```

STOP — only if needed, not a routine final step; may leave tracking UNKNOWN:

```powershell
python arduino\scripts\needle_control.py stop --live --config $Si6Needle
```

## STEP 3 — Manual Chemyx pump test

Open the manual settings, not the production recipe:

```powershell
notepad $Si6Manual
```

- `machine_config`: match `$Si6Machine` above.
- `channels.1.manual_transfer.volume_ml` and `rate_ml_min`: manual Channel 1 move.
- `channels.2.manual_transfer.volume_ml` and `rate_ml_min`: manual Channel 2 move.
- Both currently default to **5 mL at 10 mL/min**; select approved small test
  values before moving. No CLI `--volume` or `--rate` exists.
- Channel 1 HOME is fully depressed, with `syringe_volume_at_home_ml: 0.0`.
- Channel 2 `home.syringe_volume_at_home_ml` is currently null. To enable manual
  infusion/withdrawal, specify the verified fluid volume at the chosen HOME and
  establish that physical reference with `set-home`. Do not invent loading.

`set-home` saves a stopped, operator-confirmed plunger position as **0 mL**, with
no motion. Withdraw adds displacement; infuse subtracts it. `home` calculates
the return from trustworthy tracking; it is not a built-in Chemyx command.
Keep an existing compatible reference; do not overwrite it just to hide uncertainty.

### Channel 1

Saved status (no pump contact):

```powershell
python scripts\chemyx_control.py status --channel 1 --config $Si6Manual
```

Prepare settings (idle STOP check, units/diameter/rate; no motion):

```powershell
python scripts\chemyx_control.py prepare --channel 1 --live --config $Si6Manual
```

Set HOME only after physically placing/verifying the stopped plunger:

```powershell
python scripts\chemyx_control.py set-home --channel 1 --live --config $Si6Manual
```

Withdraw, then infuse the same configured volume; check fluid source/destination:

```powershell
python scripts\chemyx_control.py withdraw --channel 1 --live --config $Si6Manual
```

```powershell
python scripts\chemyx_control.py infuse --channel 1 --live --config $Si6Manual
```

Return HOME, when tracking is known:

```powershell
python scripts\chemyx_control.py home --channel 1 --live --config $Si6Manual
```

STOP only if needed; may invalidate the saved position:

```powershell
python scripts\chemyx_control.py stop --channel 1 --live --config $Si6Manual
```

### Channel 2

Standalone infusion requires the reagent line **disconnected from any reactor
and routed to waste**. Withdrawal needs its inlet in the intended refill source.
The existing authorization flag below never bypasses reserved/uncertain dose records.

```powershell
python scripts\chemyx_control.py status --channel 2 --config $Si6Manual
```

```powershell
python scripts\chemyx_control.py prepare --channel 2 --live --config $Si6Manual
```

Physically place/verify the stopped plunger at the configured reference before set-home:

```powershell
python scripts\chemyx_control.py set-home --channel 2 --live --config $Si6Manual
```

```powershell
python scripts\chemyx_control.py withdraw --channel 2 --live --config $Si6Manual
```

```powershell
python scripts\chemyx_control.py infuse --channel 2 --live --allow-channel2-infusion --config $Si6Manual
```

Return HOME (normally withdrawal after a dose):

```powershell
python scripts\chemyx_control.py home --channel 2 --live --config $Si6Manual
```

If the planned HOME return is **infusion**, route to waste and use this alternative:

```powershell
python scripts\chemyx_control.py home --channel 2 --live --allow-channel2-infusion --config $Si6Manual
```

STOP only if needed:

```powershell
python scripts\chemyx_control.py stop --channel 2 --live --config $Si6Manual
```

### Both channels

```powershell
python scripts\chemyx_control.py status --channel both --config $Si6Manual
```

```powershell
python scripts\chemyx_control.py prepare --channel both --live --config $Si6Manual
```

Known references and appropriate fluid paths required; Channel 2 infusion return
still needs its explicit waste-routing authorization:

```powershell
python scripts\chemyx_home.py --channel both --live --config $Si6Manual
```

Manual commands are independent tools. Do not run them while the automatic
experiment owns the port. More detail: [CHEMYX_QUICK_REFERENCE.md](CHEMYX_QUICK_REFERENCE.md).

## STEP 4 — Confirm all three communicate

This contacts the instruments but commands **no needle movement, pump START or
NMR acquisition**. Select COM4/COM6 through `$Si6Machine` from STEP 1:

```powershell
python scripts\01_three_instrument_system_test.py --live --communications-only --workflow-config $Si6Production --machine-config $Si6Machine --arduino-config $Si6Needle
```

Look for `[PASS] Arduino communication OK`, `[PASS] Chemyx communication OK`,
`[PASS] NMR communication OK`, and `THREE-INSTRUMENT SYSTEM TEST: PASS`.
If it fails, use [errors and fixes](SI6_ERRORS_AND_FIXES.md) before continuing.

## STEP 5 — Set checkout transfer values

```powershell
notepad $Si6Checkout
```

Required: a fresh `workflow.experiment_id` beginning `SI6_CHECKOUT_`, Channel 2
`infuse.volume_ml` and `rate_ml_min` in `initial_stage.after_monitoring`, and
`pump.channels.'2'.initial_retained_volume_ml` matching actual loading.
No production 1.8 mL dose is inherited. Geometry comes from the unchanged base recipe.

`checkout.test_fluid`, `routing` and `purpose` are optional descriptions. Missing
fluid/routing are recorded as **unspecified**; this does not verify connections.
Check them physically before the single startup `y`.

Five minutes per stage is nominal. Each stage attempts its first NMR acquisition
and processing even if preparation crosses five minutes. The configured
`checkout.overall_timeout_minutes: 30` is checked at safe boundaries; active
operations/cleanup finish. Empty raw data or failure to attempt processing makes
the checkout incomplete. Existing instrument timeouts still apply.

## STEP 6 — Short live checkout

Choose **one** port pair. Validation opens no instruments. Live asks one `y`,
then runs Stage 1 → one protected test dose → Stage 2 → cleanup without more prompts.

COM4 validation:

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

COM4 live:

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

COM6 validation:

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

COM6 live:

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

Observe needle HOME/UP, Channel 1 sampling, NMR/data processing, return/wash,
one confirmed Channel 2 test infusion, then the second full cycle and cleanup.
Current sampling/cleanup takes 7 min 32 s per stage plus NMR/processing; the
normal mock took **16:12 total**, not exactly ten minutes.

## STEP 7 — Check results

Use the exact printed `Results:` folder. For convenience, open the newest main
experiment/checkout directory below; verify its ID before interpreting it:

```powershell
$Si6Run = (Get-ChildItem results\experiments -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
explorer.exe "$Si6Run"
Get-ChildItem "$Si6Run\step_1\nmr_raw"
Get-ChildItem "$Si6Run\step_2\nmr_raw"
Get-Content "$Si6Run\channel_2_dose\dose_record.json"
Get-Content "$Si6Run\final_qc\checkout_report.md"
Select-String -Path "$Si6Run\logs\experiment_journal.jsonl" -Pattern 'CLEANUP_COMPLETE'
```

Require raw NMR files and processing-attempt evidence in **both stages**, one
confirmed dose and completed cleanup. `live_command_evidence_complete` supports
the software record; observe actual delivery/routing and inspect spectra too.
Numerical unavailability is documented, not good spectral quality. A mock pass
does not verify real instruments. Acquisition timing comes from JCAMP metadata.

## STEP 8 — Full experiment

Prepare actual production fluid, loading and routing; use the intended new
physical reaction ID. Preserve existing HOME/position records and dose history.
Do not use a fresh ID to retry an uncertain dose. The unchanged recipe runs
**48 h / 120-minute NMR interval → original protected 1.8 mL at 1 mL/min dose →
Stage 2 minimum 2 h / maximum 6 h / 30-minute interval**. Four qualifying
post-minimum observations can finish Stage 2; otherwise it reaches six hours.

COM4 production:

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

COM6 production:

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

One startup `y`; no intermediate prompts. Keep WORK awake/powered with disk space
and instrument connections stable. Ctrl+C in the running terminal requests
abort; inspect physical state and dose records before any subsequent run.
