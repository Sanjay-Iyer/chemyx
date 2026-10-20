> [Documentation home](../00_START_HERE.md) | [Other and older experiment workflows](00_START_HERE.md)

> Other/older workflow. For the full 48-hour / 6-hour run use the [fixed-time experiment](../03_fixed_time_experiment/00_START_HERE.md). Older Stage 2 endpoint rules can finish before 6 hours.

# Si6: standalone bench checkout → real experiment

For the new acquisition-only 48 h / 6 h experiment, use
[the fixed-time guide](../03_fixed_time_experiment/01_run_guide.md). The legacy production
experiment below retains its chemical endpoint behavior.

## Set Configuration Variables

These variables are shortcuts for existing file paths, **not new configurations**.
Set them once in **each new PowerShell terminal**, then run the short commands below.
Run from the repository root so the relative paths resolve correctly.

```powershell
cd C:\code\chemyx_pump
```

Arduino configuration:

```powershell
$Si6Needle = "arduino\configs\arduino_real_COM3.yaml"
```

Chemyx machine configuration (COM4):

```powershell
$Si6Machine = "configs\machines\si6_real_COM4.yaml"
```

Production experiment:

```powershell
$Si6Production = "configs\experiments\si6_real_two_stage.yaml"
```

Standalone manual Chemyx settings, including its HOME convention:

```powershell
$Si6Manual = "configs\chemyx\manual_control.yaml"
```

Standalone instrument checkout settings:

```powershell
$Si6Checkout = "configs\checkouts\si6_simple_instrument_check.yaml"
```

For Chemyx on COM6, change only the machine shortcut for commands accepting
`--machine-config`:

```powershell
$Si6Machine = "configs\machines\si6_real_COM6.yaml"
```

| Variable | Script argument it belongs to |
|---|---|
| `$Si6Needle` | `needle_control.py --config`; system test/production `--arduino-config` |
| `$Si6Manual` | `chemyx_control.py --config` and `chemyx_home.py --config` |
| `$Si6Checkout` | `03_simple_instrument_checkout.py --config` |
| `$Si6Machine` | System test, standalone checkout and production `--machine-config` |
| `$Si6Production` | System test and production `--workflow-config` |

Manual Chemyx tools read the pump endpoint from **`machine_config` inside
`$Si6Manual`**. That field must select the same COM4/COM6 machine file as the rig.
Changing `$Si6Machine` does not rewrite the manual YAML. Never pass `$Si6Machine`
to a manual tool's `--config`: it expects the manual-control YAML. Preserve the
existing saved HOME identity; changing a port does not silently migrate a reference.
Arduino remains COM3.

Use this guide top to bottom on **WORK**, with current code/dependencies already
installed. The recommended quick test is the [standalone bench checkout](../04_instrument_checkout/01_bench_checkout.md):
`python scripts\03_simple_instrument_checkout.py --live`, then one **y**. It checks
Arduino, each pump channel independently and one 2-scan NMR acquisition, returning
to the original known tracked positions. No liquid enters the NMR; blank/background
spectra are accepted. No chemistry stages or dosing occur. The manual sections
below are optional when existing references/configuration are valid.

Run individual manual commands as needed; STOP and set-home are not
routine actions to repeat after each successful move. Errors: [07_legacy_errors_and_fixes.md](07_legacy_errors_and_fixes.md).

## STEP 1 — Open Python

Open PowerShell. Select the installed offline Python without activation:

```powershell
cd C:\code\chemyx_pump
```

```powershell
$env:Path = "$PWD\.venv\Scripts;" + $env:Path
```

```powershell
python -c "import sys; print(sys.executable); print(sys.version)"
```

If using a different verified Python, keep that interpreter instead. Do not
reinstall packages for each experiment. The variables above select the file paths.

## STEP 2 — Manual Arduino needle test

Automated **HOME = DOWN = 0** (in flask); **UP = 1** (out of flask).
The standalone manual script now uses relative UP (+1) and DOWN (−1) counts,
including before HOME calibration, with the same persistent state and bounds.
Saved software position is not independently measured physical position.

Status — contacts Arduino, no movement:

```powershell
python arduino\scripts\needle_control.py status --live --config $Si6Needle
```

If UNKNOWN, physically place the stopped needle at the correct sampling HOME,
then run `home` below and confirm `y` to establish zero without motor movement.
Manual UP/DOWN use the calibrated increment and inspect-clear-travel confirmation:

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
- Both manual defaults are **1 mL at 10 mL/min**; the standalone checkout
  separately presets **1 mL at 10 mL/min**. Select approved small manual test
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
experiment owns the port. More detail: [01_quick_reference.md](../02_chemyx/01_quick_reference.md).

## STEP 4 — Confirm all three communicate

This contacts the instruments but commands **no needle movement, pump START or
NMR acquisition**. Use the selected `$Si6Machine` (COM4 or COM6):

```powershell
python scripts\01_three_instrument_system_test.py --live --communications-only --workflow-config $Si6Production --machine-config $Si6Machine --arduino-config $Si6Needle
```

Look for `[PASS] Arduino communication OK`, `[PASS] Chemyx communication OK`,
`[PASS] NMR communication OK`, and `THREE-INSTRUMENT SYSTEM TEST: PASS`.
If it fails, use [errors and fixes](07_legacy_errors_and_fixes.md) before continuing.

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
python scripts\03_simple_instrument_checkout.py --validate-config --config $Si6Checkout --machine-config $Si6Machine
```

```powershell
python scripts\03_simple_instrument_checkout.py --live --config $Si6Checkout --machine-config $Si6Machine
```

Validation contacts no hardware. Live asks one **y**, then Arduino UP/DOWN/HOME
and starting-position restoration → Channel 1 pair → Channel 2 pair → NMR/raw
saving/FFT-ACME processing. Normal use needs no manual commands between checks.
See [the bench guide](../04_instrument_checkout/01_bench_checkout.md) for the default one-command
launch, explicit config, settings, failure handling and exact offline transfer.

The older `configs/experiments/si6_real_10min_checkout.yaml` remains a separate,
optional full two-stage integration test. It requires its own configured test
addition and identity; it is not the simple bench check. See
[the historical integration guide](../99_archive/SI6_10MIN_LIVE_CHECKOUT.md).

## STEP 6 — Check the standalone result

Use the exact printed `Results:` path, or inspect the latest bench check:

```powershell
explorer.exe C:\code\chemyx_pump\results\instrument_checks
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
```

```powershell
python scripts\02_si6_experiment.py --live --workflow-config $Si6Production --machine-config $Si6Machine --arduino-config $Si6Needle
```

One startup **y**; no intermediate prompts. Keep WORK awake/powered with disk
space and stable connections. Ctrl+C requests abort; inspect physical state and
dose records before retrying. Production results remain in `results/experiments`,
separate from `results/instrument_checks`.
