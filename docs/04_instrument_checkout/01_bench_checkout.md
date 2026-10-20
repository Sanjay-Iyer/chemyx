> [Documentation home](../00_START_HERE.md) | [Standalone instrument checkout](00_START_HERE.md)

# Standalone three-instrument bench checkout

On WORK, from `C:\code\chemyx_pump`, using the installed offline Python.
For shorter variable-based commands and their one-time definitions, see
[Set Configuration Variables](../07_other_experiments/02_legacy_run_guide.md#set-configuration-variables).

```powershell
cd C:\code\chemyx_pump
python scripts\03_simple_instrument_checkout.py --validate-config
python scripts\03_simple_instrument_checkout.py --live
```

Type **y once**. There are no further prompts. An explicit config is also supported:

```powershell
python scripts\03_simple_instrument_checkout.py --live --config C:\code\chemyx_pump\configs\checkouts\si6_simple_instrument_check.yaml
```

For Chemyx on COM6, use the existing alternate machine file:

```powershell
python scripts\03_simple_instrument_checkout.py --validate-config --machine-config C:\code\chemyx_pump\configs\machines\si6_real_COM6.yaml
python scripts\03_simple_instrument_checkout.py --live --machine-config C:\code\chemyx_pump\configs\machines\si6_real_COM6.yaml
```

## Before typing y

- Arduino COM3 must have its already established, valid software HOME and known
  position. HOME=DOWN=0; UP=1. Keep adequate needle clearance.
- Both Chemyx channels must have valid compatible saved HOME references. Use the
  same COM4/COM6 identity used when establishing them. Both syringes are currently
  20 mm diameter / 20 mL capacity. At the configured HOME, Channel 1 contains
  0 mL and Channel 2 contains 5 mL; saved nonzero displacement is preserved too.
- Set up the pump test-fluid source and waste destination. **Isolate Channel 2
  from every chemical reaction and route its infusion to waste.** Reserved or
  uncertain production doses still block the test; no dose record is cleared.
- **No liquid flows into the NMR during this checkout. Leave its flow cell in
  its existing state.** Blank/background spectra are acceptable. This does not
  test sample delivery, chemistry, solvent detection, integration or endpoints.
- Close serial monitors, other instrument tools and any experiment using the rig.

If a reference is UNKNOWN, use the existing manual HOME procedure first. The
checkout never guesses or re-establishes live HOME and never asks an extra recovery
question midway through its sequence. See [needle recovery](../01_arduino/02_home_recovery.md)
and [Chemyx manual control](../02_chemyx/02_manual_control_and_home.md). Skip reference establishment
when existing tracking is valid.

## What happens

1. Arduino: read starting position, UP=1, DOWN=0, existing software HOME=0;
   restore the original starting position when different from HOME.
2. Channel 1: withdraw 1 mL, then infuse 1 mL, both at 10 mL/min. Wait for the
   existing timed move, positive STOP response and durable ledger completion.
3. Channel 2: the same independent pair. At HOME its tracked volume goes
   **5 → 6 → 5 mL**. Both channels end stopped, with zero net checkout displacement.
4. NMR: ping the existing Ethernet endpoint, acquire one **iflow FID / 2 scans**,
   wait and save raw DX; run existing full-spectrum FFT/ACME processing.

The pump pairs take 32 seconds of timed waits in total (four 6-second transfers,
each with the existing 2-second completion margin), plus communication overhead.
Needle movement, NMR acquisition and processing add time. This has no chemistry
stages, reagent addition or experiment duration.

Equal confirmed transfers establish return to the **software-tracked starting
displacement**, not independently measured physical position or delivered volume.
An uncertain command stops the sequence; it does not attempt a guessed reverse
move. Check the report's instrument attention message before retrying.

## Files and settings

| Purpose | File |
|---|---|
| Entry script | `scripts\03_simple_instrument_checkout.py` |
| Checkout transfers | `C:\code\chemyx_pump\configs\checkouts\si6_simple_instrument_check.yaml` |
| Needle COM3, steps, speed, HOME state path | `C:\code\chemyx_pump\arduino\configs\arduino_real_COM3.yaml` |
| Pump/NMR connection | `C:\code\chemyx_pump\configs\machines\si6_real_COM4.yaml` or `C:\code\chemyx_pump\configs\machines\si6_real_COM6.yaml` |
| Syringe geometry and existing HOME convention | `C:\code\chemyx_pump\configs\chemyx\manual_control.yaml` |
| Read-only source of NMR gain/center/width | `C:\code\chemyx_pump\configs\experiments\si6_real_two_stage.yaml` |

All established values are preset; no fluid/routing metadata or stage settings
are required. Edit `chemyx.channel_1` / `channel_2` in the **checkout YAML** to
change `withdraw_ml`, `infuse_ml` and `rate_ml_min`; equal positive transfers are
required. Changing these does not change manual defaults or the production dose.
`nmr.scans` is fixed at 2 for this test. `nmr.process_spectrum: false` can explicitly
skip optional processing; otherwise FFT/ACME is attempted. Production acquisition
settings currently inherited are gain 12, auto-gain false, center 5 ppm, width
20 ppm; endpoint `http://169.254.30.54:5000` comes from the selected machine file.
Existing environment endpoint overrides remain effective; review the printed
summary before confirming. Do not use a COM override to conceal an incompatible HOME.

## Results

Use the exact printed `Results:` directory:

```text
results/instrument_checks/YYYYMMDD_HHMMSS_live_<unique>/
  checkout_summary.json
  checkout_summary.txt
  instrument_commands.log
  operation_journal.jsonl
  run_state.json
  nmr_raw/<timestamp>_instrument_check_2scan_gain12.dx
  nmr_processed/<dataset>_<acquisition>_full_spectrum/
```

Open the printed result folder in Explorer and read `checkout_summary.txt`.
Browse all checkouts with this command and select the correct LIVE result:

```powershell
explorer.exe C:\code\chemyx_pump\results\instrument_checks
```

**PASS** means completed instrument operations, saved nonempty raw data and
confirmed restoration of tracked positions. A blank spectrum does not fail the
checkout. Processing is separately **PASS** or **UNAVAILABLE** for numerical
limitations/no meaningful phase; neither a peak nor successful integration is
required. Acquisition/transport/storage failures are FAILED. A processing launch
or execution failure is also explicitly reported; it is not mislabeled a blank
spectrum. **INCOMPLETE** means startup declined; **NOT RUN** marks later checks
that were not attempted. Always inspect `attention` if present. Historical HOME
and dose records remain preserved. The live checkout reads dose history but
creates no production dose reservations.

HOME development validation only, without contacting hardware:

```powershell
python scripts\03_simple_instrument_checkout.py --mock
```

Mocks use the existing Arduino/Chemyx emulators and real saved FID fixture, with
simulated references in that unique result's `mock_state/`. Their fixture spectra
are historical data, not newly measured two-scan spectra or hardware proof.

HOME validation (2026-10-16): **18 focused tests passed**; one complete CLI mock
passed in **45.047 seconds wall time**, including real full-spectrum processing.
A separate zero-signal-FID test passed the checkout with acquisition/raw saving
PASS and numerical processing UNAVAILABLE. Physical instruments were not contacted.
The production recipe, existing drivers and physical-state files matched their
pre-edit hashes. The full regression suite was not run.

## Separate production experiment

Restore production fluid routing/loading after the waste-line bench test. Check
the actual physical setup and intended reaction identity. Do not reset HOME or
clear dose records. This checkout's blank-spectrum acceptance is **not** a
production chemistry/QC rule. Production remains 48 h / 120-minute interval →
protected **1.86 mL at 1 mL/min** dose from 5 mL loading → Stage 2 minimum 2 h /
maximum 6 h, 30-minute interval and existing four-point completion rule.

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config C:\code\chemyx_pump\configs\experiments\si6_real_two_stage.yaml --machine-config C:\code\chemyx_pump\configs\machines\si6_real_COM4.yaml --arduino-config C:\code\chemyx_pump\arduino\configs\arduino_real_COM3.yaml
python scripts\02_si6_experiment.py --live --workflow-config C:\code\chemyx_pump\configs\experiments\si6_real_two_stage.yaml --machine-config C:\code\chemyx_pump\configs\machines\si6_real_COM4.yaml --arduino-config C:\code\chemyx_pump\arduino\configs\arduino_real_COM3.yaml
```

Substitute `C:\code\chemyx_pump\configs\machines\si6_real_COM6.yaml` for COM6. The older
`configs/experiments/si6_real_10min_checkout.yaml` remains an optional two-stage
integration test, documented in [the archived integration guide](../99_archive/SI6_10MIN_LIVE_CHECKOUT.md).
It is a different workflow requiring its own test-dose configuration.

## Complete offline WORK deployment

The complete working-tree package includes this script, preset YAML, documentation
and offline dependencies. Follow the exact copy, checksum, state-backup,
settings-preserving installation and verification commands in
[02_offline_deployment.md](02_offline_deployment.md).
Do not overwrite WORK HOME references, installed instrument settings or dose history.
