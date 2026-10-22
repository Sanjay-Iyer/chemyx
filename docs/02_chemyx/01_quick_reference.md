> [Documentation home](../00_START_HERE.md) | [Chemyx pump](00_START_HERE.md)

# Chemyx quick reference (offline)

Copy-paste commands for the Chemyx Fusion 4000 standalone tools, plus where
every file is and what it does. Everything here works on the instrument laptop
with no internet. Longer explanations are in
[02_manual_control_and_home.md](02_manual_control_and_home.md).

**Jump to:**
[Start](#start) ·
[Where everything is](#where-everything-is) ·
[How it works](#how-it-works-in-one-minute) ·
[First test (0.1 mL)](#first-supervised-test-01-ml) ·
[Everyday commands](#everyday-commands) ·
[Before an experiment](#before-an-experiment) ·
[After an experiment](#after-an-experiment) ·
[UNKNOWN recovery](#if-a-channel-says-unknown) ·
[Change settings](#change-volume-rate-port-or-syringe) ·
[Refusal messages](#what-a-refusal-message-means) ·
[Practice](#practice-without-the-pump)

---

## Start

Open PowerShell and go to the repository. Every command below assumes you are
in this folder.

```powershell
cd C:\code\chemyx_pump
```

The commands use the offline Python, `.venv\Scripts\python.exe`. On a computer
without `.venv`, replace it with `python`.

Check that the pump is connected, and on which COM port:

```powershell
.venv\Scripts\python.exe -m serial.tools.list_ports -v
```

The tools expect **COM4** (set in `configs\machines\si6_real_COM4.yaml`). If the
pump shows up on COM6, see [Change settings](#change-volume-rate-port-or-syringe).

---

## Where everything is

| What | Path | Notes |
|---|---|---|
| Simple tool (no saved state) | `scripts\26_chemyxmanual.py` | status, stop, infuse, withdraw; use it around 20–25 runs ([guide](../03_fixed_time_experiment/26_chemyx_manual.md)) |
| Main tool | `scripts\chemyx_control.py` | status, verify, prepare, set-home, home, infuse, withdraw, stop |
| HOME shortcut | `scripts\chemyx_home.py` | same as `chemyx_control.py home` |
| Everyday settings | `configs\chemyx\manual_control.yaml` | used by default; 5 mL per move at 10 mL/min |
| First-test settings | `configs\chemyx\first_motion_test.yaml` | 0.1 mL per move at 0.5 mL/min; add `--config` |
| COM port (COM4) | `configs\machines\si6_real_COM4.yaml` | `chemyx.serial_port` |
| COM port (COM6) | `configs\machines\si6_real_COM6.yaml` | switch to it in the YAML if needed |
| Experiment recipe (syringe sizes) | `configs\experiments\si6_real_two_stage.yaml` | `pump.channels.1` and `pump.channels.2` |
| Saved HOME and positions | `runtime\chemyx_state\` | written by the software; never edit or copy |
| Per-command pump logs | `runtime\chemyx_state\manual_runs\` | one folder per moving command |
| Mock (practice) record | `runtime\chemyx_state_mock\` | separate from the real record |
| Reagent dose records | `runtime\si6_doses\` | read only; protects Channel 2 |
| Full guide | `docs\02_chemyx\02_manual_control_and_home.md` | |

**Syringes:** both channels use 20.0 mm inner diameter / 20.0 mL syringes.
Channel 1 = sampling, Channel 2 = reagent dosing.

---

## How it works in one minute

| Command | What it does | Moves the pump? |
|---|---|---|
| `status` | Shows saved HOME, position and the calculated return to HOME | No (and no pump contact without `--live`) |
| `verify` | Checks settings, HOME, dose records; with `--live` also communication and idle STOP | No |
| `prepare` | Sends units, syringe diameter and rate to the pump | No |
| `set-home` | Saves the plunger's current physical position as HOME = 0 mL | No |
| `withdraw` | Withdraws the configured volume | Yes |
| `infuse` | Infuses the configured volume | Yes |
| `home` | Calculates and runs the infusion/withdrawal back to HOME | Yes |
| `stop` | Sends STOP and marks the channel's position UNKNOWN | Stops it |

| Option | Meaning |
|---|---|
| `--channel 1` / `2` / `both` | Which channel (required for everything except status/verify) |
| `--live` | Real pump |
| `--mock` | Fake pump and separate practice record, for rehearsal |
| `--config <file>` | Use a different settings YAML (for example the first-test file) |
| `--allow-channel2-infusion` | Required for any Channel 2 infusion, for that one command only |
| `--volume <mL>` | `infuse`/`withdraw` only: volume for this one command (default: YAML `manual_transfer.volume_ml`) |
| `--rate <mL/min>` | `infuse`/`withdraw`/`home` only: rate for this one command (default: YAML `manual_transfer.rate_ml_min`) |
| `--dose-ledger-dir <folder>` | Different reagent dose records folder (rarely needed) |

Things to remember:

- **HOME is software-only.** The Chemyx has no HOME command. `set-home` saves
  where the plunger is now (no motion). `home` reverses the tracked moves with
  ordinary infuse/withdraw commands.
- **Sign:** withdraw counts as **+**, infuse as **−**. "+2 mL from HOME" means
  HOME is 2 mL of infusion away.
- **One `y` per moving command.** Each move shows the planned volume, rate and
  fluid path, then asks `[y/N]`. Anything other than `y` cancels.
- **Volume and rate:** the YAML `manual_transfer` values are the defaults. Add
  `--volume 2.5` and/or `--rate 5` to use other values for one command; the
  YAML is not changed (see [Change settings](#change-volume-rate-port-or-syringe)).
- **Limits:** a move is refused if it would take a syringe below empty or above
  20 mL. Channel 1's HOME is fully depressed (empty), so withdraw before infusing.
- **UNKNOWN means no automatic HOME.** After a crash, interrupted move, `stop`,
  or front-panel use, re-establish HOME by hand (see [UNKNOWN recovery](#if-a-channel-says-unknown)).
- **Front-panel movement is invisible** to the software. After using the
  Chemyx buttons, always run `set-home` again.
- **The experiment updates the same record.** Moves made by
  `02_si6_experiment.py` are tracked, so `home` works after a run.

---

## First supervised test (0.1 mL)

Before running anything:

1. Confirm by eye that **both** syringes are 20 mL syringes with 20 mm inner diameter.
2. Route both lines into a waste vial.
3. Push both plungers fully in (empty). That is HOME for this test.
4. Pump powered on; COM port confirmed (see [Start](#start)).

**1. Check communication, idle and settings** (no motion):

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py verify --channel both --live --config configs\chemyx\first_motion_test.yaml
```

**2. Set HOME on both channels** (no motion; answer `y`):

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py set-home --channel both --live --config configs\chemyx\first_motion_test.yaml
```

**3. Channel 1: withdraw 0.1 mL**

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py withdraw --channel 1 --live --config configs\chemyx\first_motion_test.yaml
```

**4. Channel 1: return to HOME** (infuses 0.1 mL)

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py home --channel 1 --live --config configs\chemyx\first_motion_test.yaml
```

**5. Channel 2: withdraw 0.1 mL**

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py withdraw --channel 2 --live --config configs\chemyx\first_motion_test.yaml
```

**6. Channel 2: return to HOME** (infuses 0.1 mL, so the flag is required)

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py home --channel 2 --live --allow-channel2-infusion --config configs\chemyx\first_motion_test.yaml
```

**7. Confirm both channels are at HOME**

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py status --channel both --config configs\chemyx\first_motion_test.yaml
```

Expected: each withdraw visibly draws about 0.1 mL in, each HOME pushes it back
out, and `status` shows both channels `Position: KNOWN, 0 mL from HOME`. If a
withdraw infuses instead (or the volume is clearly wrong), stop and do not
continue.

Using the everyday file afterwards: Channel 1's HOME carries over. Channel 2's
does not, because its HOME is defined differently in `manual_control.yaml`;
run `set-home --channel 2` again before using that file.

---

## Everyday commands

These use `configs\chemyx\manual_control.yaml`: **1 mL per command at
10 mL/min** on both channels. Channel 2's HOME there is "loaded with 5.0 mL
reagent", the experiment's starting state, so a Channel 2 infuse can push out
at most those 5 mL and a withdraw can fill the syringe to at most 20 mL.

### Look and check (no motion)

Saved positions only, no pump contact:

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py status --channel both
```

Saved positions plus a pump communication check:

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py status --channel both --live
```

Full readiness check (settings, HOME, dose records, communication, idle):

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py verify --channel both --live
```

Send units, diameter and rate to the pump (no motion):

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py prepare --channel both --live
```

### Set HOME (no motion)

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py set-home --channel 1 --live
```

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py set-home --channel 2 --live
```

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py set-home --channel both --live
```

### Channel 1 (sampling)

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py withdraw --channel 1 --live
```

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py infuse --channel 1 --live
```

### Channel 2 (reagent dosing)

Withdraw. The line's inlet must be in the reagent/refill source, not a reaction:

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py withdraw --channel 2 --live
```

Infuse. Route the reagent line to waste first:

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py infuse --channel 2 --live --allow-channel2-infusion
```

### Both channels together (one after the other, each with its own settings)

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py withdraw --channel both --live
```

### Return to HOME

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py home --channel 1 --live
```

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py home --channel 2 --live
```

If Channel 2's return is an infusion (it was withdrawn), add the flag:

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py home --channel 2 --live --allow-channel2-infusion
```

Both channels with the shortcut script:

```powershell
.venv\Scripts\python.exe scripts\chemyx_home.py --channel both --live
```

### Stop

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py stop --channel both --live
```

If the tool cannot reach the pump (for example another program has the COM
port), use the Chemyx front-panel STOP or switch the pump off.

---

## Before an experiment

1. Mount the syringes and check `manual_control.yaml` matches them
   (20 mm / 20 mL), and that its `machine_config` is the same machine file you
   give the experiment.
2. Prepare the pump:

   ```powershell
   .venv\Scripts\python.exe scripts\chemyx_control.py prepare --channel both --live
   ```

3. Put each plunger at its HOME position by hand: Channel 1 fully depressed;
   Channel 2 loaded with 5.0 mL reagent (the recipe's
   `initial_retained_volume_ml`). The experiment then doses 1.86 mL once,
   leaving about 3.14 mL.
4. Save HOME:

   ```powershell
   .venv\Scripts\python.exe scripts\chemyx_control.py set-home --channel both --live
   ```

5. Check readiness:

   ```powershell
   .venv\Scripts\python.exe scripts\chemyx_control.py verify --channel both --live
   ```

6. Check the experiment config with the same machine file (no hardware is opened):

   ```powershell
   .venv\Scripts\python.exe scripts\02_si6_experiment.py --validate-config --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
   ```

7. Start the experiment as usual. Do not press the Chemyx front-panel buttons
   during or after the run.

---

## After an experiment

1. See where each syringe is and what return is planned:

   ```powershell
   .venv\Scripts\python.exe scripts\chemyx_control.py status --channel both
   ```

2. Route each line where that fluid should go. A Channel 2 return after the
   dose is a **withdrawal** through the reagent line: put its inlet in the
   reagent/refill source, not the reactor.
3. Return to HOME:

   ```powershell
   .venv\Scripts\python.exe scripts\chemyx_home.py --channel both --live
   ```

With `both`, each channel is checked before anything moves, then they move one
after another. If one fails, the other keeps its own true state; nothing is
reversed automatically.

---

## If a channel says UNKNOWN

`home` refuses to move a channel whose history has a gap: a crash or closed
window during a move, an unacknowledged STOP, a lost serial link, `stop`, a
changed syringe setting, or a damaged record. `status` shows the reason and the
last trusted position.

1. Stop the pump if anything might be moving:

   ```powershell
   .venv\Scripts\python.exe scripts\chemyx_control.py stop --channel both --live
   ```

2. Inspect the syringe, tubing and flask. Move the plunger to its HOME position
   by hand or with the front panel.
3. Re-establish HOME for that channel (example: Channel 1):

   ```powershell
   .venv\Scripts\python.exe scripts\chemyx_control.py set-home --channel 1 --live
   ```

---

## Change volume, rate, port or syringe

Edit the YAML in Notepad, save, then run the command again.

```powershell
notepad configs\chemyx\manual_control.yaml
```

| To change | File | Key |
|---|---|---|
| Volume per infuse/withdraw | `configs\chemyx\manual_control.yaml` | `channels.1.manual_transfer.volume_ml` (or `channels.2...`) |
| Rate (also used by `home`) | `configs\chemyx\manual_control.yaml` | `channels.N.manual_transfer.rate_ml_min` (0.0000001 to 170.5 mL/min) |
| Channel 2 syringe contents at HOME (enables manual Ch2 moves) | `configs\chemyx\manual_control.yaml` | `channels.2.home.syringe_volume_at_home_ml` (set **before** `set-home`) |
| HOME description | `configs\chemyx\manual_control.yaml` | `channels.N.home.reference_description` |
| Volume or rate for one command only | command line | `--volume <mL>` (infuse/withdraw), `--rate <mL/min>` (infuse/withdraw/home) |
| Syringe diameter / capacity | `configs\chemyx\manual_control.yaml`, plus the experiment YAML you run: `configs\experiments\si6_fixed_48h_6h.yaml` (fixed-time) or `configs\experiments\si6_real_two_stage.yaml` | `channels.N.syringe.diameter_mm` / `capacity_ml` and `pump.channels.N.syringe_diameter_mm` / `syringe_capacity_ml`; keep them identical (the fixed-time run refuses to load otherwise) |
| COM port | `configs\chemyx\manual_control.yaml` and `configs\chemyx\first_motion_test.yaml` | `machine_config:` point at `configs/machines/si6_real_COM6.yaml` (or edit `chemyx.serial_port` in the machine file); use the same machine file for the experiment |

- **Safe to change any time:** `volume_ml`, `rate_ml_min`.
- **Changing these makes a saved HOME refused until you run `set-home` again:**
  syringe diameter, capacity, `reference_description`, `syringe_volume_at_home_ml`, COM port.
  Nothing moves when this happens; it just refuses.

---

## What a refusal message means

| Message contains | Meaning | What to do |
|---|---|---|
| `no HOME reference exists` | HOME was never set for this channel | Place the plunger, run `set-home` |
| `position UNKNOWN` | The history has a gap | Follow [UNKNOWN recovery](#if-a-channel-says-unknown) |
| `no longer matches the saved HOME reference` | A syringe/HOME/port setting changed since `set-home` | Restore the old value, or run `set-home` again |
| `set home.syringe_volume_at_home_ml` | Manual moves need the syringe contents at HOME | Set it in the YAML, then `set-home` |
| `implies a syringe volume of ... outside 0 to 20 mL` | The move would empty or overfill the syringe | Use a smaller volume, or withdraw before infusing |
| `--allow-channel2-infusion` | Channel 2 infusion needs explicit authorization | Route the reagent line to waste, add the flag |
| `RESERVED without a confirmed dose` | An admitted experiment's dose is not confirmed | Channel 2 infusion is blocked; service the syringe by hand, then `set-home` |
| `are uncertain` (dose records) | A reagent dose has no confirmed completion | All Channel 2 motion is blocked; reconcile with the experiment journal first |
| `owned by another process` | The experiment or another window is using the pump | Finish or close that program first |
| `Access denied opening COM4` | Another program has the port open | Close the Chemyx GUI, serial monitors, other scripts |
| `Port COM4 not found` | Wrong port, cable or driver | Check `list_ports`; switch to the COM6 machine file if needed |
| `STOP was not positively acknowledged` | The pump did not confirm it is idle | Check cable and power, then retry |
| `requires --live or --mock` / `requires --channel` | An option is missing | Add the option |

---

## Practice without the pump

Replace `--live` with `--mock` to rehearse any command. Mock runs use the fake
pump and the separate `runtime\chemyx_state_mock\` record, never the real one.

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py set-home --channel both --mock --config configs\chemyx\first_motion_test.yaml
```

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py withdraw --channel both --mock --config configs\chemyx\first_motion_test.yaml
```

```powershell
.venv\Scripts\python.exe scripts\chemyx_control.py status --channel both --mock --config configs\chemyx\first_motion_test.yaml
```

```powershell
.venv\Scripts\python.exe scripts\chemyx_home.py --channel both --mock --allow-channel2-infusion --config configs\chemyx\first_motion_test.yaml
```
