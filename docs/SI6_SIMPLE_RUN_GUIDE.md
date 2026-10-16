# Si6: standalone bench checkout → real experiment

Use this guide top to bottom on **WORK**, with current code/dependencies already
installed. The recommended quick test is the [standalone bench checkout](SI6_SIMPLE_INSTRUMENT_CHECK.md):
`python scripts\03_simple_instrument_checkout.py --live`, then one **y**. It checks
Arduino, each pump channel independently and one 2-scan NMR acquisition, returning
to the original known tracked positions. No liquid enters the NMR; blank/background
spectra are accepted. No chemistry stages or dosing occur. The manual sections
below are optional when existing references/configuration are valid.

Run individual manual commands as needed; STOP and set-home are not
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
$Si6Checkout = 'configs\checkouts\si6_simple_instrument_check.yaml'
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
- Channel 2 HOME convention is currently 5.0 mL loaded, matching production.
  Preserve an existing compatible reference. Re-establish it only after physically
  verifying the correct stopped reference; never invent loading.

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

## STEP 5 — Recommended standalone instrument checkout

Known settings are already preset in `$Si6Checkout`: each channel withdraws 1 mL
then infuses 1 mL at 10 mL/min; NMR uses exactly 2 scans. Both syringes are 20 mm /
20 mL. Channel 1 HOME loading is 0 mL; Channel 2 HOME loading is 5 mL. The saved
starting displacement/fill is restored without redefining HOME.

Isolate both pump test paths from the reaction, with Channel 2 infusion to waste.
No pump operation routes liquid into the NMR; leave its existing flow cell as-is.
Blank/background NMR data are acceptable. No chemical peak or integration is
required. Restore production routing/loading after the bench test.

```powershell
python scripts\03_simple_instrument_checkout.py --validate-config --machine-config $Si6Machine
python scripts\03_simple_instrument_checkout.py --live --machine-config $Si6Machine
```

Validation contacts no hardware. Live asks one **y**, then Arduino UP/DOWN/HOME
and starting-position restoration → Channel 1 pair → Channel 2 pair → NMR/raw
saving/FFT-ACME processing. Normal use needs no manual commands between checks.
See [the bench guide](SI6_SIMPLE_INSTRUMENT_CHECK.md) for the default one-command
launch, explicit config, settings, failure handling and exact offline transfer.

The older `configs/experiments/si6_real_10min_checkout.yaml` remains a separate,
optional full two-stage integration test. It requires its own configured test
addition and identity; it is not the simple bench check. See
[the historical integration guide](archive/SI6_10MIN_LIVE_CHECKOUT.md).

## STEP 6 — Check the standalone result

Use the exact printed `Results:` path, or inspect the latest bench check:

```powershell
$Si6Check = (Get-ChildItem results\instrument_checks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
Get-Content "$Si6Check\checkout_summary.txt"
Get-ChildItem "$Si6Check\nmr_raw"
explorer.exe "$Si6Check"
```

Confirm LIVE versus MOCK and **Overall: PASS**. Acquisition/raw saving and
processing have separate results. Numerical processing limitations are UNAVAILABLE
and do not invalidate successful acquisition; blank spectra do not fail the test.
Transport/storage/execution failures identify the instrument needing attention.
Confirmed software position restoration is not independent physical metrology.

## STEP 7 — Separate full experiment

Prepare actual production fluid, loading and routing, and the intended physical
reaction identity. Preserve existing HOME/position records and dose history.
Do not use a fresh ID to retry an uncertain dose. The unchanged recipe uses
**48 h / 120-minute interval → protected 1.86 mL at 1 mL/min dose from 5 mL loading →
Stage 2 minimum 2 h / maximum 6 h / 30-minute interval**. Four qualifying
post-minimum observations can finish Stage 2; otherwise it reaches six hours.
Production retains its existing scientific analysis/solvent protections.

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config $Si6Production --machine-config $Si6Machine --arduino-config $Si6Needle
python scripts\02_si6_experiment.py --live --workflow-config $Si6Production --machine-config $Si6Machine --arduino-config $Si6Needle
```

One startup **y**; no intermediate prompts. Keep WORK awake/powered with disk
space and stable connections. Ctrl+C requests abort; inspect physical state and
dose records before retrying. Production results remain in `results/experiments`,
separate from `results/instrument_checks`.
