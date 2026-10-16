> **Historical document — archived 2026-10-16.** Preserved for provenance; its commands/settings may be superseded. Use [the current simple operating guide](../SI6_SIMPLE_RUN_GUIDE.md) and [errors/fixes](../SI6_ERRORS_AND_FIXES.md) at the instruments.

# Si6 three-instrument quick commands — offline WORK laptop

**Arduino needle + Chemyx Channels 1/2 + NMR.** Keep this file on WORK at
`C:\code\chemyx_pump\docs\archive\SI6_THREE_INSTRUMENT_QUICK_COMMANDS.md`.
All commands below are Windows **PowerShell** commands. Copy one command block
at a time; choose the COM4 **or** COM6 variant, not both. Internet is unnecessary
once the current source, offline dependencies and instrument software are installed.
The NMR still needs its local Ethernet connection.

## Quick Start — checkout first, then production

**WORK only.** Deploy the current code/dependencies, fill the six checkout
settings listed in [section 3](#3-fill-the-short-checkout-settings), and verify
fluid routing, syringe loading and existing HOME references. Choose one run at
a time; the commands here use Chemyx **COM4** and Arduino **COM3**.

Open PowerShell in the repository. Use the PATH line if using the offline `.venv`:

```powershell
Set-Location C:\code\chemyx_pump
$env:Path = "C:\code\chemyx_pump\.venv\Scripts;" + $env:Path
```

**1. Validate checkout settings — no instrument contact:**

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**2. Run the LIVE checkout — read the plan and enter `y` once:**

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**3. Inspect results:** the command prints the exact `Results:` folder under
`results\experiments`. In that folder inspect `final_qc\checkout_report.md`,
both `step_1` / `step_2` `nmr_raw` and `nmr_processed` directories,
`channel_2_dose\dose_record.json`, and `logs\experiment_journal.jsonl`.

```powershell
explorer.exe C:\code\chemyx_pump\results\experiments
```

**4. After a successful physical checkout, prepare production fluid/loading and
routing, review its recipe/identity, then launch production:**

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

For Chemyx COM6 use `configs\machines\si6_real_COM6.yaml` instead; full variants
are below. The checkout's five-minute deadlines allow active cycles to finish:
the complete mock took **16:12**. Production runs **48 hours → one protected
dose → Stage 2 minimum 2 / maximum 6 hours**. Both automatic workflows need only
their single startup `y`. Preserve saved HOME references and dose history.

## Jump to what you need

- [Open the terminal and select Python](#1-open-the-terminal-and-select-python)
- [Find scripts and configuration files](#2-find-scripts-and-configuration-files)
- [Fill the short-checkout settings](#3-fill-the-short-checkout-settings)
- [Short checkout: validate and run](#4-short-checkout-validate-and-run)
- [Main experiment: validate and run](#5-main-experiment-validate-and-run)
- [Expected physical sequence and timing](#6-expected-physical-sequence-and-timing)
- [Optional offline mock and connection checks](#7-optional-offline-mock-and-connection-checks)
- [Separate manual needle commands](#8-separate-manual-needle-commands)
- [Standalone Chemyx Channel 1/2 commands](#8a-standalone-chemyx-commands)
- [Find results and read status](#9-find-results-and-read-status)
- [Offline installation, backups and help](#10-offline-installation-backups-and-help)
- [Before pressing y](#11-before-pressing-y)

## 1. Open the terminal and select Python

Open PowerShell on the **instrument-connected WORK laptop**:

```powershell
Set-Location C:\code\chemyx_pump
```

If installed with `offline\install_offline.ps1`, select that Python for this
terminal. This changes only this terminal's PATH; no environment activation is
required:

```powershell
$env:Path = "C:\code\chemyx_pump\.venv\Scripts;" + $env:Path
python -c "import sys; print(sys.executable); print(sys.version)"
```

The executable should be `C:\code\chemyx_pump\.venv\Scripts\python.exe`.
If using a different already-verified instrument environment, keep its Python
instead. The supplied offline wheel bundle targets **Python 3.11, Windows x64**.
Repeat the PATH command in each new terminal when using the offline `.venv`.

Open this guide or the results folder quickly:

```powershell
notepad docs\archive\SI6_THREE_INSTRUMENT_QUICK_COMMANDS.md
```

```powershell
explorer.exe results\experiments
```

## 2. Find scripts and configuration files

Paths below are relative to `C:\code\chemyx_pump`. For example,
`scripts\02_si6_experiment.py` means
`C:\code\chemyx_pump\scripts\02_si6_experiment.py`. Links work from this file
without internet.

| File | Purpose |
| --- | --- |
| [scripts/02_si6_experiment.py](../../scripts/02_si6_experiment.py) | **Main runner for BOTH the production experiment and short checkout.** |
| [configs/experiments/si6_real_two_stage.yaml](../../configs/experiments/si6_real_two_stage.yaml) | Production timing, reagent dose, syringes, NMR acquisition and analysis settings. |
| [configs/experiments/si6_real_10min_checkout.yaml](../../configs/experiments/si6_real_10min_checkout.yaml) | Separate short-checkout overrides; reads the production recipe as its base without writing to it. |
| [configs/machines/si6_real_COM4.yaml](../../configs/machines/si6_real_COM4.yaml) | Chemyx **COM4** and NMR network settings. |
| [configs/machines/si6_real_COM6.yaml](../../configs/machines/si6_real_COM6.yaml) | Chemyx **COM6** and NMR network settings. |
| [arduino/configs/arduino_real_COM3.yaml](../../arduino/configs/arduino_real_COM3.yaml) | Arduino **COM3**, needle calibration, positions and saved-state path. |
| [configs/nmr/analysis.yaml](../../configs/nmr/analysis.yaml) | Shared NMR processing defaults. The experiment recipe explicitly selects its primary phase method. |
| [configs/nmr/analysis.local.yaml](../../configs/nmr/analysis.local.yaml) | Existing local processing defaults; review alongside the shared settings when diagnosing processing. |
| [scripts/01_three_instrument_system_test.py](../../scripts/01_three_instrument_system_test.py) | Separate instrument diagnostics; not the two-stage checkout. |
| [arduino/scripts/needle_control.py](../../arduino/scripts/needle_control.py) | Separate manual needle HOME, UP, DOWN, status and STOP. |
| [scripts/chemyx_control.py](../../scripts/chemyx_control.py) | Separate manual Chemyx control and saved syringe HOME bookkeeping. |
| [configs/chemyx/manual_control.yaml](../../configs/chemyx/manual_control.yaml) | Manual Chemyx settings; does not replace the automated experiment recipe. |
| [scripts/verify_si6_environment.py](../../scripts/verify_si6_environment.py) | Offline imports, source integrity, resources and configuration checks; no instrument contact. |
| [scripts/nmr/summarize_run.py](../../scripts/nmr/summarize_run.py) | Summarize an existing run offline without taking another acquisition. |
| [scripts/nmr/process_fid.py](../../scripts/nmr/process_fid.py) | Production FID processing, called automatically during live runs. |
| [scripts/rig_state_backup.py](../../scripts/rig_state_backup.py) | Back up/verify/compare physical-state records without contacting instruments. |

Open the checkout YAML:

```powershell
notepad configs\experiments\si6_real_10min_checkout.yaml
```

Inspect the main recipe without editing it:

```powershell
Get-Content configs\experiments\si6_real_two_stage.yaml
```

**Command meanings:** `--workflow-config` chooses the experiment recipe;
`--machine-config` chooses pump port/NMR connection; `--arduino-config` chooses
needle settings. `--validate-config` checks configuration without connecting;
`--mock` uses simulated instruments; `--live` contacts physical instruments.
Use exactly one of those three modes. Keep all three config arguments explicit.

## 3. Fill the short-checkout settings

Before the checkout, fill **all six** fields in
`configs\experiments\si6_real_10min_checkout.yaml`:

| YAML field | What to enter |
| --- | --- |
| `checkout.test_fluid` | Actual approved test fluid. |
| `checkout.routing` | Verified reservoir/flask/NMR/return/waste connections. |
| `workflow.experiment_id` | Fresh identity beginning `SI6_CHECKOUT_`, such as `SI6_CHECKOUT_20261016_001` for a new checkout. |
| `workflow.initial_stage.after_monitoring` → Channel 2 `infuse.volume_ml` | Approved test-infusion volume in mL. |
| Same infusion → `rate_ml_min` | Approved test-infusion rate in mL/min. |
| `pump.channels.'2'.initial_retained_volume_ml` | Actual loaded Channel 2 test-fluid volume in mL. |

The shipped null values and placeholder ID intentionally block running. No
approved test volume/rate is supplied. The production **1.8 mL reagent dose is
not inherited**. Do not use values from mock tests as live recommendations.
YAML indentation matters; keep the existing structure and replace only the
intended values.

The checkout reuses the main recipe's Channel 1 cycle, syringe geometry, NMR
and analysis settings. Current settings are:

| Parameter | Current value |
| --- | --- |
| Channel 1 syringe | 20 mm internal diameter / 20 mL capacity; verify physically. |
| Channel 1 initial retained volume / margin | 0 mL / 1 mL. |
| Channel 1 cycle | Withdraw 8 mL, withdraw 5 mL, return 13 mL, wash withdraw 5 mL, wash infuse 5 mL; transfers at 5 mL/min. |
| Channel 2 syringe | 20 mm internal diameter / 20 mL capacity; verify physically. |
| Channel 2 checkout volume/rate/loading | Your explicit checkout fields above. |
| NMR connection | `169.254.30.54:5000`, local instrument Ethernet. |
| NMR acquisition | `iflow`, FID, 8 scans, receiver gain 12, auto-gain off. |
| NMR center / sweep width / target | 5 ppm / 20 ppm / 5.8 ppm; target search ±0.2 ppm. |
| Primary phase method | ACME, selected by `nmr.processing.phase_method` in the base recipe. |

**Physically prepare the fluid routing.** Writing its description into YAML
does not change tubing. Verify the test fluid, sample amount and destination of
the Channel 2 infusion before starting.

For checkout timing, edit only its `initial_stage` / `first_addition_stage`
`max_hours` and `interval_minutes` if needed. Defaults are five minutes
(`max_hours: 0.08333333333333333`) and a requested one-minute interval per stage.
Both stages remain `fixed_duration`; chemical peaks cannot end them early.
Do not edit the production recipe merely to shorten the checkout.

## 4. Short checkout: validate and run

Choose one port variant. Arduino remains COM3 in all commands.

### Chemyx COM4 — validate, no instrument contact

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

### Chemyx COM4 — LIVE checkout

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

### Chemyx COM6 — validate, no instrument contact

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

### Chemyx COM6 — LIVE checkout

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

Read the printed experiment ID, ports, syringes, transfer values and CHECKOUT
label. Enter **`y` once** at startup. The two stages, test dose and cleanup then
run without additional prompts. There are no acknowledgment flags to add.

## 5. Main experiment: validate and run

After a successful physical checkout, prepare the actual production fluid,
syringe loading and routing. A checkout consumes test fluid and updates the
existing position ledger; changing recipes does not physically reload a syringe.
The main recipe's ID must identify the intended new physical reaction.
An existing reservation blocks replay: do not delete it or change ID to retry
a possibly delivered dose. There is no automatic partial-run resume.

### Chemyx COM4 — validate, no instrument contact

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

### Chemyx COM4 — LIVE production experiment

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

### Chemyx COM6 — validate, no instrument contact

```powershell
python scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

### Chemyx COM6 — LIVE production experiment

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

The main experiment also needs only **one startup `y`**. Current schedule:

| Phase | Timing and transition |
| --- | --- |
| Stage 1 | Fixed 48 hours; requested NMR interval 120 minutes; chemical evidence is advisory. |
| Addition | One protected Channel 2 infusion, currently 1.8 mL at 1 mL/min. |
| Stage 2 | Minimum 2 hours, maximum 6 hours; requested NMR interval 30 minutes; four qualifying post-minimum observations can establish chemical completion. |

Missing solvent/unavailable scientific evidence does not establish chemical
completion; the configured time endpoint governs. Instrument, uncertain-motion,
STOP, transport and storage failures remain failures. Do not change the
production timing or dose while setting up the separate checkout.

## 6. Expected physical sequence and timing

1. Startup returns the needle to its saved HOME reference, then raises it.
2. Channel 1 withdraws 8 mL; needle moves HOME; Channel 1 withdraws 5 mL and settles.
3. NMR acquires and saves the Stage 1 raw data.
4. Python performs FFT, ACME phasing, baseline correction, peak integration and QC.
5. Needle rises; Channel 1 returns 13 mL, then performs the existing 5 mL wash
   withdrawal and 5 mL wash infusion.
6. At the Stage 1 time endpoint, finish any active cycle safely, move needle
   HOME, perform exactly one protected Channel 2 infusion, verify completion,
   raise the needle and start Stage 2 automatically.
7. Stage 2 repeats the sampling, NMR acquisition, processing and cleanup cycle.
8. Finish the final cleanup and close the session; reports are saved afterward.

**HOME=DOWN=0 means needle in the flask; UP=1 means out of the flask.**
Needle HOME and Chemyx syringe HOME are different saved references.

The short checkout has five-minute stage deadlines, not a guaranteed ten-minute
total. Sampling/cleanup alone takes **452 seconds (7 min 32 s) per cycle**, plus
NMR, processing and needle overhead. Cycles do not overlap and active operations
are not interrupted to hit five minutes. An NMR acquisition that has not begun
when the stage deadline is reached is skipped, followed by cleanup.

The complete mock example took **16 min 12 s**: Stage 1 finished at 08:02,
its mock test transfer was confirmed at 08:10, and Stage 2 finished at 16:12.
Actual live time depends on acquisition, processing and your test transfer.
Expect approximately one acquisition per stage with the supplied cycle.

## 7. Optional offline mock and connection checks

These are optional tools, not extra required startup steps or a request to run
another extensive test campaign.

### Filled checkout recipe, simulated instruments only

```powershell
python scripts\02_si6_experiment.py --mock --workflow-config configs\experiments\si6_real_10min_checkout.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

The checkout fields still need filling. This uses virtual time and saves
**simulated** acquisition data; it does not prove physical delivery or real NMR
performance. Use COM6 in the machine filename if that is your selected variant.

### Environment and source verification, no instrument contact

```powershell
python scripts\verify_si6_environment.py --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

This defaults to checking the production recipe. It does not start an experiment
or take an acquisition. Exported releases also get source/payload hash checks.

### Communication check — WORK only, contacts all three instruments

Run only when no experiment or other program owns the ports. This is read-only
communication/status, with no pump START, needle movement or NMR acquisition:

```powershell
python scripts\01_three_instrument_system_test.py --live --communications-only --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

For COM6 substitute `configs\machines\si6_real_COM6.yaml`. This separate
diagnostic has its own startup confirmation; it is not part of the checkout.

## 8. Separate manual needle commands

**WORK only; do not run these concurrently with an experiment using COM3.**
Manual movement/reference commands have their own single `y`. They are not
additional prompts inside an automatic experiment. A valid HOME reference is
retained between experiments; establishing it again is not a routine prerequisite.

Read status (contacts Arduino; no commanded movement):

```powershell
python arduino\scripts\needle_control.py status --live --config arduino\configs\arduino_real_COM3.yaml
```

Return to HOME=0, or recover UNKNOWN position after physical placement and `y`:

```powershell
python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
```

For known tracking, HOME commands the return to zero and verifies completion.
For UNKNOWN tracking, it commands no movement: physically place and verify the
stopped needle at HOME, then confirm to establish the reference. Software does
not independently measure physical zero.

Raise to UP=1:

```powershell
python arduino\scripts\needle_control.py up --live --config arduino\configs\arduino_real_COM3.yaml
```

Lower to DOWN=HOME=0:

```powershell
python arduino\scripts\needle_control.py down --live --config arduino\configs\arduino_real_COM3.yaml
```

Explicitly establish the current operator-verified physical position as HOME
(no movement; use only when intentionally defining the reference):

```powershell
python arduino\scripts\needle_control.py set-home --live --config arduino\configs\arduino_real_COM3.yaml
```

Standalone Arduino STOP, when the port is available:

```powershell
python arduino\scripts\needle_control.py stop --live --config arduino\configs\arduino_real_COM3.yaml
```

During an active automatic run, use **Ctrl+C in that run's terminal** to request
abort. Keep the existing physical stop/power-disconnect procedure available.
Do not immediately rerun a stopped experiment; inspect motion and dose records.

Read the saved Chemyx syringe positions (ledger-only status, no pump connection):

```powershell
python scripts\chemyx_control.py status --channel both --config configs\chemyx\manual_control.yaml
```

For manual syringe movements and HOME use [CHEMYX_MANUAL_CONTROL.md](../CHEMYX_MANUAL_CONTROL.md).
Its `machine_config` must match the experiment's chosen COM port. Do not use
`configs\chemyx\first_motion_test.yaml` as a substitute experiment recipe; it
defines a separate supervised manual test and its own HOME convention.

## 8A. Standalone Chemyx commands

These commands use [scripts/chemyx_control.py](../../scripts/chemyx_control.py) and
[configs/chemyx/manual_control.yaml](../../configs/chemyx/manual_control.yaml),
independently of the automated three-instrument runner. Run individual commands
as needed, **not this whole section as a sequence**. Do not run them while an
experiment owns the pump port. `set-home` and moving commands have their own
single `y`; `status`, `prepare` and `stop` do not ask for one. These are not extra
prompts inside an automated experiment.

### Check the manual settings first

```powershell
notepad configs\chemyx\manual_control.yaml
```

| Setting | Meaning / current value |
| --- | --- |
| `machine_config` | Currently `configs/machines/si6_real_COM4.yaml`. For COM6 select `configs/machines/si6_real_COM6.yaml` here, matching the experiment. The manual tool has no `--machine-config` flag. |
| `channels.1.manual_transfer.volume_ml` / `rate_ml_min` | Currently **5 mL at 10 mL/min** per manual infuse/withdraw command. Review before any move; these are not the short-checkout transfer settings. |
| `channels.2.manual_transfer.volume_ml` / `rate_ml_min` | Also currently **5 mL at 10 mL/min**; choose values appropriate to the actual setup before using them. |
| `channels.1.home.syringe_volume_at_home_ml` | Currently 0 mL: Channel 1 HOME is the fully depressed plunger. Withdraw before infusing from that starting position. |
| `channels.2.home.syringe_volume_at_home_ml` | Currently **null**, deliberately. Set the verified volume at the chosen HOME to enable manual Channel 2 infuse/withdraw, following the HOME procedure below. HOME return can work without this value. |

Volume/rate come from YAML; there are **no `--volume` or `--rate` options**.
HOME return uses the tracked displacement rather than `manual_transfer.volume_ml`,
at the configured manual rate. Capacity and reference checks can refuse a move.
Changing HOME-dependent settings can invalidate the match to an existing
reference; reconcile the physical setup before intentionally re-establishing it.
Do not redefine a valid HOME before every experiment.

`set-home` means **save the current operator-verified plunger position without
movement**. `home` means **move back to the saved reference** using normal
infusion/withdrawal. Chemyx HOME is separate from Arduino needle HOME.

### Channel 1 — sampling syringe

**Status — saved positions only, no pump connection:**

```powershell
python scripts\chemyx_control.py status --channel 1 --config configs\chemyx\manual_control.yaml
```

**Prepare — applies units, diameter and rate; verifies idle using STOP; no motion:**

```powershell
python scripts\chemyx_control.py prepare --channel 1 --live --config configs\chemyx\manual_control.yaml
```

**Set HOME — physically place/verify the stopped plunger at its configured HOME,
then confirm `y`; no motion:**

```powershell
python scripts\chemyx_control.py set-home --channel 1 --live --config configs\chemyx\manual_control.yaml
```

**Return to saved HOME — requires known tracking:**

```powershell
python scripts\chemyx_control.py home --channel 1 --live --config configs\chemyx\manual_control.yaml
```

**Withdraw — draws the configured volume from the connected source:**

```powershell
python scripts\chemyx_control.py withdraw --channel 1 --live --config configs\chemyx\manual_control.yaml
```

**Infuse — pushes the configured volume toward the connected destination:**

```powershell
python scripts\chemyx_control.py infuse --channel 1 --live --config configs\chemyx\manual_control.yaml
```

**Stop Channel 1 — no confirmation; can leave saved position UNKNOWN:**

```powershell
python scripts\chemyx_control.py stop --channel 1 --live --config configs\chemyx\manual_control.yaml
```

### Channel 2 — reagent/test-infusion syringe

For standalone Channel 2 **infusion**, disconnect the line from any reactor and
route it to waste. The existing `--allow-channel2-infusion` flag is required for
that one manual infusion, including a HOME return that infuses. It does not
bypass dose reservations/uncertainties. This flag is **not** needed for the
automated experiment's protected dose or the automatic checkout command.
For withdrawal, put the inlet in the intended reagent/refill source, not a
reaction mixture.

**Status — saved positions and dose records, no pump connection:**

```powershell
python scripts\chemyx_control.py status --channel 2 --config configs\chemyx\manual_control.yaml
```

**Prepare — applies Channel 2 settings; verifies idle using STOP; no motion:**

```powershell
python scripts\chemyx_control.py prepare --channel 2 --live --config configs\chemyx\manual_control.yaml
```

**Set HOME — confirm the actual stopped plunger position and configured HOME
meaning before `y`; does not move the pump or clear reagent-dose records:**

```powershell
python scripts\chemyx_control.py set-home --channel 2 --live --config configs\chemyx\manual_control.yaml
```

**Return to saved HOME — typically withdrawal after an experiment's infusion:**

```powershell
python scripts\chemyx_control.py home --channel 2 --live --config configs\chemyx\manual_control.yaml
```

If status shows the HOME return requires **infusion** instead, route to waste
and use this alternative:

```powershell
python scripts\chemyx_control.py home --channel 2 --live --allow-channel2-infusion --config configs\chemyx\manual_control.yaml
```

**Withdraw — requires a verified volume-at-HOME setting and sufficient capacity:**

```powershell
python scripts\chemyx_control.py withdraw --channel 2 --live --config configs\chemyx\manual_control.yaml
```

**Infuse — requires verified loading/travel limits and the waste routing above:**

```powershell
python scripts\chemyx_control.py infuse --channel 2 --live --allow-channel2-infusion --config configs\chemyx\manual_control.yaml
```

**Stop Channel 2 — no confirmation; can leave saved position UNKNOWN:**

```powershell
python scripts\chemyx_control.py stop --channel 2 --live --config configs\chemyx\manual_control.yaml
```

For either saved-status command, add `--live` to also check pump communication:

```powershell
python scripts\chemyx_control.py status --channel 1 --live --config configs\chemyx\manual_control.yaml
```

```powershell
python scripts\chemyx_control.py status --channel 2 --live --config configs\chemyx\manual_control.yaml
```

Standalone STOP for **both** channels, when the serial port is available:

```powershell
python scripts\chemyx_control.py stop --channel both --live --config configs\chemyx\manual_control.yaml
```

If the port cannot be opened and the pump may be moving, use the Chemyx
front-panel STOP / existing physical intervention procedure. STOP does not
establish HOME and may invalidate tracked position. A standalone Chemyx `home`
refuses UNKNOWN tracking; inspect, physically reconcile and intentionally
`set-home` only when appropriate. Unlike the Arduino HOME recovery command,
Chemyx `home` does not establish an UNKNOWN reference.

An uncertain dose record blocks Channel 2 motion; a RESERVED dose blocks manual
Channel 2 infusion. Neither `set-home` nor `--allow-channel2-infusion` clears
these records. See [CHEMYX_QUICK_REFERENCE.md](../CHEMYX_QUICK_REFERENCE.md) and
[CHEMYX_MANUAL_CONTROL.md](../CHEMYX_MANUAL_CONTROL.md) for the full standalone guide.

## 9. Find results and read status

The runner prints `Results: <exact run folder>`. Both workflows save under:

```text
C:\code\chemyx_pump\results\experiments\YYYYMMDD_experimentID[_n]\
  metadata\                 effective settings, source hashes and outcomes
  logs\                     experiment journal, live status and commands
  step_1\
    nmr_raw\                Stage 1 acquired JCAMP files
    nmr_processed\          processing outputs
    analysis\               measurements and completion/solvent evidence
    figures\
    logs\
  channel_2_dose\            dose_record.json and command evidence
  step_2\                   same structure as step_1
  final_qc\                 final QC; checkout_report.json/.md for checkout
```

List recent run directories (folder ordering is for navigation, not NMR timing):

```powershell
Get-ChildItem results\experiments -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name
```

Set this variable to the **actual folder printed by your run** before copying
the following inspection commands. Replace `YOUR_ACTUAL_RUN_FOLDER`:

```powershell
$Si6Run = 'C:\code\chemyx_pump\results\experiments\YOUR_ACTUAL_RUN_FOLDER'
```

```powershell
Get-Content "$Si6Run\logs\live_status.json"
```

```powershell
Get-Content "$Si6Run\logs\experiment_journal.jsonl" -Tail 30
```

```powershell
Get-ChildItem "$Si6Run\step_1\nmr_raw"
Get-ChildItem "$Si6Run\step_2\nmr_raw"
```

After a short checkout, read its report:

```powershell
Get-Content "$Si6Run\final_qc\checkout_report.md"
```

A live report of `live_command_evidence_complete` requires separate acquisition
and processing/ documented numerical-unavailability evidence in **both stages**,
one confirmed Channel 2 transfer and completed cleanup. Inspect the actual
spectra and physical observations too. `checkout_evidence_incomplete` means the
full checkout was not demonstrated, even if timed progression completed.
Mock success is labelled `mock_validation_passed`.

Regenerate an existing run's NMR summary offline, without another experiment:

```powershell
python scripts\nmr\summarize_run.py "$Si6Run"
```

Scientific NMR timestamps come from acquisition metadata, preferentially JCAMP
`LONG DATE`; filename schedule labels and file modification times are not
substitutes. Missing sample/solvent or numerical unavailability must not be
interpreted as successful chemistry.

## 10. Offline installation, backups and help

The current source **and** ignored offline wheels/resources must be transferred
to WORK. Copying an old repository or only a commit identifier is insufficient.
See [OFFLINE_DEPLOYMENT.md](../OFFLINE_DEPLOYMENT.md) and
[SI6_DEPLOYMENT_MANIFEST.md](../SI6_DEPLOYMENT_MANIFEST.md) for transfer checks.
Vendor drivers/software stay on WORK; this guide does not require reflashing
the commissioned Arduino firmware.

If dependencies need installing on WORK, use the transferred Python 3.11 x64
installer if necessary, then run from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File offline\install_offline.ps1
```

It installs from the local wheelhouse with `--no-index` and performs existing
configuration checks. Omit `-RunTests` for routine installation. Afterward use
the PATH command in section 1, then verify the transferred offline assets:

```powershell
python offline\verify_bundle.py
```

Before a software update, stop experiments and back up state. This explicitly
includes the Chemyx HOME ledger, in addition to the backup tool's default
needle/dose/journal locations:

```powershell
$Si6BackupJson = python scripts\rig_state_backup.py backup --state-path runtime\chemyx_state
if ($LASTEXITCODE -ne 0) { throw 'Rig backup failed' }
$Si6Backup = ($Si6BackupJson | ConvertFrom-Json).backup
python scripts\rig_state_backup.py verify --backup "$Si6Backup"
```

If `CHEMYX_POSITION_STATE_DIR` is set, also include its actual `chemyx_state`
subdirectory with another `--state-path`. Preserve that environment setting on
WORK. Keep backups separate from source releases; see [RIG_STATE_BACKUP.md](../RIG_STATE_BACKUP.md).
Never replace WORK's state with HOME mock records, reset position tracking to
hide a discrepancy, or clear dose history to enable a rerun.

To include this newly added guide in a **new** offline working-tree package,
run on HOME only (requires the already prepared offline payload; does not commit):

```powershell
python scripts\prepare_si6_release.py --working-tree --with-offline-payload --output-dir dist\si6_operator_handoff
```

Copy the generated ZIP, checksum and manifest to the drive using the established
deployment procedure. Previously built ZIPs do not automatically gain this
guide; alternatively copy this Markdown file into WORK's `docs` directory.

Built-in help is local and opens no instruments:

```powershell
python scripts\02_si6_experiment.py --help
python scripts\01_three_instrument_system_test.py --help
python arduino\scripts\needle_control.py --help
python scripts\chemyx_control.py --help
```

| Problem | What to check |
| --- | --- |
| `python` missing or import fails | Correct Python/PATH; install the transferred offline dependencies. |
| Checkout says to fill a field | Complete all six required checkout values; save YAML; validate again. |
| COM port busy/unavailable | Correct machine config; close other experiment processes, vendor serial tools and serial monitors. |
| NMR cannot connect | Local Ethernet, configured address and enabled instrument RPC; internet access is unrelated. |
| UNKNOWN needle position | Use the separate needle HOME procedure after physical inspection. Do not guess/reset it through YAML. |
| Experiment already reserved | Inspect the existing dose/journal records; do not erase records or change ID to replay an uncertain dose. |
| Missing solvent or unusable spectra | Check fluid level, sample delivery and routing; timed completion does not establish good NMR data. |
| Checkout exceeds ten minutes | Expected with the unchanged production sampling/cleanup cycle; inspect stage outcomes and safe completion. |

## 11. Before pressing y

- [ ] Correct WORK directory, deployed source and Python environment.
- [ ] Correct COM4/COM6 choice; Arduino COM3; NMR Ethernet connected.
- [ ] Correct checkout or production recipe explicitly selected.
- [ ] Checkout placeholders filled, appropriate unique ID, approved transfer values.
- [ ] Installed syringe geometry, actual loaded fluid, tubing and destinations verified.
- [ ] Existing HOME references agree with the physical setup; state and dose records preserved.
- [ ] Configuration-only validation passed; no competing program owns the ports.
- [ ] Printed plan reviewed; enter `y` once and observe the automatic sequence.
- [ ] After checkout, inspect both stages' raw/processed data, test dose and cleanup
      before preparing and launching the production experiment.

Detailed references: [short checkout](SI6_10MIN_LIVE_CHECKOUT.md),
[current operating index](../SI6_START_HERE.md),
[configuration guide](../SI6_CONFIG_USER_GUIDE.md),
[NMR phase methods](../SI6_NMR_PHASE_METHODS.md),
[Arduino HOME recovery](../../arduino/docs/HOME_RECOVERY.md).
