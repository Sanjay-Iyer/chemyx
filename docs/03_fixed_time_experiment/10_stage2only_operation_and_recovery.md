# 10 — Stage 2 (`06`) operation, troubleshooting and recovery

[Experiment index](00_START_HERE.md) | [All documentation](../00_START_HERE.md) | [Stage 2 run guide](06_stage2only_run_guide.md)

This is the offline operating manual for the **six-hour Stage 2 only** experiment,
[`scripts\06_si6_stage2only_run.py`](../../scripts/06_si6_stage2only_run.py). It covers
preparing the Arduino needle and Chemyx Channel 1, launching the run, looking up an
error, recovering safely, and deciding whether to start a new run.

Recommended reading order:

| Order | Document | Use it for |
|---|---|---|
| 1 | [Arduino manual commands](../01_arduino/01_manual_commands.md), then [Arduino HOME recovery](../01_arduino/02_home_recovery.md) | Moving the needle, checking its saved position, establishing and recovering HOME |
| 2 | [Chemyx quick reference](../02_chemyx/01_quick_reference.md), then [Chemyx manual control and HOME](../02_chemyx/02_manual_control_and_home.md) | Channel 1 HOME, saved displacement, manual infuse/withdraw, pump recovery |
| 3 | **This document** | Preparing both instruments and the NMR, launching `06`, errors, restart |

This file is numbered `10_` because `09_stage2only_scipy_analysis.md` already
exists in this folder. Neither is related to the `09_analyze_stage2only_scipy.py`
script. This manual is only for the original `06` run and the `07` re-analysis.
Scripts `08` and `09` are a separate SciPy workflow with their own
[run guide](08_stage2only_scipy_run_guide.md).

Every command below was checked against the program's argument parser on
2026-10-19. All commands run in **Windows PowerShell** from `C:\code\chemyx_pump`
and need no internet access.

---

## Part G: One-page quick reference

Open PowerShell and run these two lines first in every new window:

```powershell
cd C:\code\chemyx_pump
```

Then use the WORK Python in this window:

```powershell
$env:Path = "$PWD\.venv\Scripts;" + $env:Path
```

**Arduino needle (always COM3, always pass this config)**

| Task | Command |
|---|---|
| Verify connection and see saved position | `python arduino\scripts\needle_control.py status --live --config arduino\configs\arduino_real_COM3.yaml` |
| Establish official HOME at the current spot (no motion, one `y`) | `python arduino\scripts\needle_control.py set-home --live --config arduino\configs\arduino_real_COM3.yaml` |
| Move UP one increment (200 steps, no prompt) | `python arduino\scripts\needle_control.py up --live --config arduino\configs\arduino_real_COM3.yaml` |
| Move DOWN one increment (200 steps, no prompt) | `python arduino\scripts\needle_control.py down --live --config arduino\configs\arduino_real_COM3.yaml` |
| Small adjustment (example: 50 steps; clears HOME) | `python arduino\scripts\needle_control.py down --steps 50 --live --config arduino\configs\arduino_real_COM3.yaml` |
| Stop | `python arduino\scripts\needle_control.py stop --live --config arduino\configs\arduino_real_COM3.yaml` |
| Return to known HOME, or recover an unknown position (one `y`) | `python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml` |

**Chemyx Channel 1 (COM4 shown; for COM6 replace `si6_real_COM4.yaml` with `si6_real_COM6.yaml`)**

| Task | Command |
|---|---|
| Inspect saved HOME and displacement (no pump contact) | `python scripts\chemyx_control.py status --channel 1 --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml` |
| Verify connection, HOME and idle STOP (no motion) | `python scripts\chemyx_control.py verify --channel 1 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml` |
| Set HOME (plunger fully depressed, empty; one `y`) | `python scripts\chemyx_control.py set-home --channel 1 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml` |
| Withdraw (example: 2 mL at 5 mL/min; one `y`) | `python scripts\chemyx_control.py withdraw --channel 1 --volume 2 --rate 5 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml` |
| Infuse (example: 2 mL at 5 mL/min; one `y`) | `python scripts\chemyx_control.py infuse --channel 1 --volume 2 --rate 5 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml` |
| Return to saved HOME (one `y`) | `python scripts\chemyx_control.py home --channel 1 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml` |
| Stop both channels (marks a known position UNKNOWN) | `python scripts\chemyx_control.py stop --channel both --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml` |
| Recover an invalid state | Procedure B1 to B4 in [Part D](#part-d-errors-and-recovery) |

**Stage 2 experiment**

| Task | Command |
|---|---|
| Validate configuration (no hardware) | `python scripts\06_si6_stage2only_run.py --validate-config` |
| Accelerated mock (no hardware) | `python scripts\06_si6_stage2only_run.py --mock` |
| Live, Chemyx COM4 (starts immediately, no prompt) | `python scripts\06_si6_stage2only_run.py --live` |
| Live, Chemyx COM6 (starts immediately, no prompt) | `python scripts\06_si6_stage2only_run.py --live --machine-config configs\machines\si6_real_COM6.yaml` |
| Find the newest live run folder | `$Run = (Get-ChildItem results\stage2only\live -Directory \| Sort-Object Name \| Select-Object -Last 1).FullName; $Run` |
| Open live plots | `Start-Process "$Run\analysis_live\index.html"` |
| Final status and error | `Get-Content "$Run\report.json" -Raw \| ConvertFrom-Json \| Select-Object status, error, physical_cleanup` |
| Post-run analysis (folder picker) | `python scripts\07_analyze_stage2only.py` |
| Post-run analysis of a known folder | `python scripts\07_analyze_stage2only.py "$Run"` |

In the table cells above, `\|` is the Markdown escape for the pipe character `|`.
Type a plain `|` in PowerShell. The `$Run` commands are shown in full in Part F.

---

## Part 1: Files and the settings that matter

| File | What `06` uses from it | Current value |
|---|---|---|
| [`configs\experiments\si6_stage2only_6h.yaml`](../../configs/experiments/si6_stage2only_6h.yaml) | Schedule, sampling sequence, Channel 1 recipe, NMR settings, output folder | 6 h, every 30 min, endpoint included; 8 scans; `results/stage2only` |
| [`configs\machines\si6_real_COM4.yaml`](../../configs/machines/si6_real_COM4.yaml) | Chemyx serial port and NMR network address (default) | Chemyx `COM4`; NMR `169.254.30.54:5000`; NMR wait 300 s |
| [`configs\machines\si6_real_COM6.yaml`](../../configs/machines/si6_real_COM6.yaml) | Same, alternate pump port | Identical except Chemyx `COM6` |
| [`arduino\configs\arduino_real_COM3.yaml`](../../arduino/configs/arduino_real_COM3.yaml) | Needle port, calibration, limits and state file | `COM3`; 200 steps per increment; positions −3 to +5; HOME=DOWN=0, UP=1; `runs/arduino/needle_state.json` |
| [`configs\chemyx\manual_control.yaml`](../../configs/chemyx/manual_control.yaml) | Channel 1 HOME convention and syringe geometry | 20.0 mm / 20.0 mL; HOME "Plunger fully depressed", 0.0 mL at HOME |
| [`configs\nmr\analysis.yaml`](../../configs/nmr/analysis.yaml) | Processing settings for live review and `07` | Used only by analysis; cannot affect the run |

Saved state (read-only for the operator; never edit these by hand):

| Record | Path |
|---|---|
| Needle saved position | `runs\arduino\needle_state.json` (plus `needle_state.json.home_pending.json` while a HOME recovery is incomplete) |
| Needle HOME/recovery journals | `runs\arduino\<timestamp>_needle_home_<id>\operation_journal.jsonl` |
| Needle manual move journal | `runs\arduino\needle_state_manual_operations.jsonl` |
| Pump HOME and displacement | `runtime\chemyx_state\channel_1_state.json`, `channel_2_state.json`, `operations.jsonl` |
| Pump manual command journals | `runtime\chemyx_state\manual_runs\<timestamp>_<command>_live_<id>\` |

Rules verified in code:

- `06` checks that the Channel 1 diameter, capacity and HOME loading in
  `manual_control.yaml` equal the Stage 2 YAML: 20 mm, 20 mL, 0 mL. Otherwise
  it stops before contacting any instrument.
- A saved Chemyx HOME belongs to the port it was set on (`serial:COM4` or
  `serial:COM6`). Switching ports needs a new verified `set-home` on the new port.
- Environment variables such as `CHEMYX_PORT` or `NMR_HOST` override the YAML
  values if they are set in the PowerShell window. Do not set them for this run.
  `NMR_DEFAULT_SCANS` is ignored; the YAML's 8 scans always apply.

---

## Part A: Arduino needle manual operation

Script: [`arduino\scripts\needle_control.py`](../../arduino/scripts/needle_control.py).
Its default config is `arduino.local.yaml`, so **always pass**
`--config arduino\configs\arduino_real_COM3.yaml` on the real rig.

### A.1 Four different things that are easy to confuse

| Operation | Command | Moves the motor? | Changes the HOME definition? |
|---|---|---|---|
| **Establish official HOME** | `set-home` | No | **Yes**: the current stopped spot becomes HOME=0 |
| **Return to an existing known HOME** | `home` with a valid saved position | Yes, to absolute 0 | No |
| **Recover an unknown position** | `home` when the saved position is invalid | No | **Yes**: you place the needle at HOME and confirm it |
| **Normal movement bookkeeping** | `up`, `down`, and every experiment move | Yes | No; only the current position (and, for `--steps` not a multiple of 200, HOME is cleared) |

There is no encoder, HOME switch or limit switch (`limits_enabled: false`).
"valid" means the software record is consistent, **not** that anyone measured
where the needle is. If the needle may have been moved by hand, by a power loss
or by an interrupted command, its true position is unknown until you look.

Only this standalone script ever sets needle HOME. `06` loads the saved record,
moves to HOME=0, then UP=1, and never redefines HOME.

### A.2 Commands

**Connect and inspect** (opens COM3, reads firmware status, no motion, no prompt):

```powershell
python arduino\scripts\needle_control.py status --live --config arduino\configs\arduino_real_COM3.yaml
```

Ready for `06` looks like:

```text
Last known needle position: 1; valid=true; source=software_estimate; firmware moving=false; fault=NONE
```

`valid=true` with `fault=NONE` and `moving=false` is required by `06`. The
number is the tracked position: 0 is HOME in the flask, 1 is UP. Before a HOME
exists you see `unknown; valid=false` or a relative count with `valid=false`.
This command changes nothing.

**Move UP / DOWN one increment** (no prompt; moves immediately; 200 steps at
100 steps/s):

UP one increment:

```powershell
python arduino\scripts\needle_control.py up --live --config arduino\configs\arduino_real_COM3.yaml
```

DOWN one increment:

```powershell
python arduino\scripts\needle_control.py down --live --config arduino\configs\arduino_real_COM3.yaml
```

Success:

```text
Needle moved UP 200 steps.
Needle movement count: +1; calibrated HOME=True; source=software_estimate
```

- **Works before HOME exists.** It then keeps an *uncalibrated* relative count
  (`calibrated HOME=False`), which `06` will not accept.
- **With HOME set,** it updates the tracked position and keeps HOME.
- **Travel limits:** −3 to +5 increments are enforced on the count. A move past
  them is refused with `Arduino travel limit: ... Do not reset HOME to bypass a
  travel limit.`
- **Before every jog,** inspect the physical travel yourself.

**Smaller or larger moves** with `--steps N` (UP/DOWN only, 1 to 1600 steps):

```powershell
python arduino\scripts\needle_control.py down --steps 50 --live --config arduino\configs\arduino_real_COM3.yaml
```

- **A multiple of 200** (for example 400) counts as whole increments and keeps
  the count and HOME.
- **Any other size** prints `Needle movement count: reset (...); calibrated
  HOME=False. Run set-home at the sampling position before an experiment.` It
  clears a saved HOME, so `06` refuses to start until you run `set-home` again.

**Establish the official HOME** (one `y`, no motion):

```powershell
python arduino\scripts\needle_control.py set-home --live --config arduino\configs\arduino_real_COM3.yaml
```

1. Position the needle at the correct sampling depth **in the flask** using `up`,
   `down` or `--steps`.
2. Confirm by eye that it is stopped there.
3. Run the command and answer `y` to:
   `Physically place the stopped needle at HOME=0. Establish current position as HOME=0? [y/N]:`

Success ends with:

```text
ARDUINO HOME COMPLETE
Saved position: HOME = 0
Controller: READY
Position tracking: VALID
Active position fault: CLEARED
Reference: operator-confirmed HOME; no motor movement.
Physical position was not independently measured.
```

It writes `runs\arduino\needle_state.json` and a new HOME journal folder.
`confirm-home` is an alias. Declining changes nothing.

**Return to HOME** (one `y`):

```powershell
python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
```

- **Saved position valid:** it asks
  `Move needle to absolute HOME=0 using the saved reference? [y/N]:`, then moves
  to 0. Success shows `Reference: completed absolute HOME command; software
  position estimate.` HOME is unchanged.
- **Saved position invalid:** it does **not** move. It shows `Needle position is
  UNKNOWN. Physically place the needle at HOME=0. ... Establish current position
  as HOME=0? [y/N]:`. Answering `y` records the current spot as HOME, exactly
  like `set-home`. Place and inspect the needle first.
- `return-home` is an alias.

**Stop** (no prompt):

```powershell
python arduino\scripts\needle_control.py stop --live --config arduino\configs\arduino_real_COM3.yaml
```

Prints `STOP sent. No HOME reference was inferred.`

- **Needle was moving, or STOP could not be verified:** the saved position
  becomes invalid.
- **Needle was idle and STOP verified:** the saved position is unchanged.

This works even when the position record is corrupt.

**Verify a movement completed:** the command printed `Needle moved ...` without
an error, and `status --live` shows the expected position with `valid=true`,
`moving=false`, `fault=NONE`. Then look at the needle.

### A.3 Recovering the needle

| Situation | What the software says | Supported recovery |
|---|---|---|
| Unknown, invalid, interrupted, or after a 06 failure during motion | `valid=false`, or 06: `Needle HOME is unconfirmed ...` | Procedure A1 |
| A STOP failed or was unverified | `home` refuses: `Unresolved STOP verification failure; verify the standalone stop command before HOME.` | Procedure A2 |
| HOME recovery marker left behind | `HOME recovery verification is incomplete; use needle_control.py home to re-establish the reference` | Procedure A1 (run `home`; never delete the marker) |
| Needle moved by hand or knocked | Software still says `valid=true` | Procedure A1 using `set-home`: the record cannot know about hand movement |
| Corrupt state file | `Needle state file ... is corrupt or unreadable` | Procedure A1; the bad file is backed up as `.corrupt-*.bak` after your `y` |

---

## Part B: Chemyx Channel 1 manual operation

Script: [`scripts\chemyx_control.py`](../../scripts/chemyx_control.py) (HOME alias:
[`scripts\chemyx_home.py`](../../scripts/chemyx_home.py)). Defaults: `--config
configs\chemyx\manual_control.yaml`, whose machine file is COM4. Pass
`--machine-config configs\machines\si6_real_COM6.yaml` to use COM6.

### B.1 Syringe, HOME and displacement

- **Geometry:** both channels are configured as **20.0 mm diameter / 20.0 mL
  capacity**, in `manual_control.yaml` and, for Channel 1, in
  `si6_stage2only_6h.yaml`. Verify physically that the installed syringe matches.
- **Channel 1 HOME** is "Plunger fully depressed" with **0 mL** in the syringe.
  This is a physical stop, so it can be re-verified by eye.
- **Displacement** is software bookkeeping: + is withdrawn, − is infused, from
  HOME. It is updated only by tracked commands from this script or an experiment.
  **Front-panel or hand movement is invisible to it.**
- A manual move is refused if it would take the syringe below 0 mL or above
  20 mL, judged from the saved displacement.

### B.2 Commands

**Inspect** (reads the live record, does not contact the pump; safe while `06` runs):

```powershell
python scripts\chemyx_control.py status --channel 1 --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

It shows `Ledger owner:` (`none`, or `IN USE by ...`), `Position:` (`KNOWN`,
`UNKNOWN` or `NO_HOME`), the HOME description, displacement and the return to HOME.

**Connect and verify** (opens the port, sends HELP and one STOP to Channel 1; no motion):

```powershell
python scripts\chemyx_control.py verify --channel 1 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

Success ends with `[PASS] Channel 1 idle: STOP positively acknowledged` and
`VERIFY: READY. No motion was commanded.` A `[FAIL] ... HOME reference` line
means no usable HOME (Procedure B1). Do not run `--live` commands while `06` is
running: the pump port is in use.

**Set Channel 1 HOME** (contacts the pump, checks STOP, one `y`, no motion):

```powershell
python scripts\chemyx_control.py set-home --channel 1 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

Only run it when the plunger is **physically fully depressed (empty) and
stopped**, and the syringe is the 20 mm / 20 mL syringe. The prompt is:
`Each syringe listed above is installed with the stated diameter and capacity,
and its plunger is physically at its HOME position and stopped. Save that position
as HOME = 0 mL? This does not move the pump. [y/N]:`

Success: `Channel 1: HOME saved and verified (...); displacement 0 mL` and
`SET-HOME complete.` This changes the HOME definition.

**Withdraw / infuse a chosen volume** (one `y` fluid-path check each). Without
`--volume`/`--rate` the YAML default is 1.0 mL at 10.0 mL/min:

Withdraw 2 mL at 5 mL/min:

```powershell
python scripts\chemyx_control.py withdraw --channel 1 --volume 2 --rate 5 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

Infuse 2 mL at 5 mL/min:

```powershell
python scripts\chemyx_control.py infuse --channel 1 --volume 2 --rate 5 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

Success: `Channel 1: withdraw complete; displacement now +2 mL` (or `infuse
complete`). `--volume` and `--rate` apply to that one command only. They need a
known HOME; with an UNKNOWN or missing HOME they are refused before any pump command.

**Return to HOME** (moves the tracked displacement back to 0; one `y`):

```powershell
python scripts\chemyx_control.py home --channel 1 --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

Success: `Channel 1: infuse complete; displacement now 0 mL (HOME reached,
software estimate)`. `--rate <mL/min>` may be added. HOME is unchanged.
`python scripts\chemyx_home.py --channel 1 --live` runs the same thing.

**Stop** (no prompt):

```powershell
python scripts\chemyx_control.py stop --channel both --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

Success: `STOP acknowledged on every selected channel.` **This marks a known
position UNKNOWN**, because the Chemyx does not report whether it was moving.
Afterwards manual `home`/`infuse`/`withdraw` are refused until Procedure B2.
If the command cannot reach the pump, it says to use the Chemyx front-panel STOP
or switch the pump off.

**Stale ownership after a crash** (no motion, no HOME change; only if `status`
shows `IN USE` and the owning program is really gone):

```powershell
python scripts\chemyx_control.py recover-stale --channel both --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

### B.3 Which problems need a new verified HOME

| Saved state | Can `06` start? | Manual `home`/`infuse`/`withdraw`? | Needs a new `set-home`? |
|---|---|---|---|
| `KNOWN`, displacement 0, same settings | Yes | Yes | No |
| `KNOWN`, displacement ≠ 0 | Yes, **but only after you physically return the plunger to empty**: `06` records your manual return to 0 | Yes (`home` returns it) | No |
| `UNKNOWN` (after `stop`, interrupted move, crash, edited/corrupt file) | Yes, if `status` still shows the HOME, **after you physically return the plunger to empty**: the HOME definition is kept | Refused | Only for manual moves (Procedure B2) |
| `NO_HOME` | No: `Channel 1: no established Chemyx HOME to preserve.` | Refused | **Yes** (Procedure B1) |
| Syringe diameter/capacity/HOME description/port changed since `set-home` | No: `saved Chemyx HOME does not match this setup` | Refused | **Yes**, after verifying the installed syringe (Procedure B3) |

`06` trusts that you physically emptied Channel 1 before launch. It cannot see
the plunger. Its first transfer withdraws 8 mL then 5 mL. A syringe that already
holds liquid could be overfilled, so check the plunger by eye every time.

### B.4 Channel 2 in `06`

- **No dose.** `06` never infuses or withdraws on Channel 2. Its dose method
  raises `stage2only never delivers a reagent dose`. It never resets Channel 2
  loading or displacement, and it never reads dose records.
- **No Channel 2 HOME needed.** A missing Channel 2 HOME does not block the run
  (verified by test).
- **But it does send STOP to Channel 2.** At startup and before **every** pump
  transfer and needle move, `06` sends STOP to Channels 1 and 2 and requires a
  positive acknowledgment from both. If Channel 2 does not acknowledge, the run
  stops with `Channel 2 STOP was not positively acknowledged (...); the pump is
  not proven idle`.
- On a single Fusion 4000 this almost always means pump communication failed
  (Procedure B4).

---

## Part C: Running the Stage 2 experiment

### C.1 Pre-run checklist (in order)

**Computer (operator)**

1. Open PowerShell in the project with the WORK Python:

   ```powershell
   cd C:\code\chemyx_pump
   ```

   Use the WORK Python in this window:

   ```powershell
   $env:Path = "$PWD\.venv\Scripts;" + $env:Path
   ```

   Check which Python is used:

   ```powershell
   python -c "import sys; print(sys.executable)"
   ```

   Check the dependencies load:

   ```powershell
   python -c "import serial, yaml, numpy, scipy, matplotlib, nmrglue; print('dependencies OK')"
   ```

2. Check the ports are present. Expect COM3 (Arduino) and COM4 or COM6 (Chemyx):

   ```powershell
   python -m serial.tools.list_ports
   ```

3. Close the Chemyx GUI, the Arduino IDE serial monitor, PuTTY/TeraTerm and any
   other window running `needle_control.py`, `chemyx_control.py`, `04`, `06` or `08`.

4. Plug in the laptop. Disable sleep and hibernate while plugged in, for at
   least 7 hours. Also pause Windows Update and turn off USB selective suspend
   ([run guide](01_run_guide.md), section 6):

   Never sleep while plugged in:

   ```powershell
   powercfg /change standby-timeout-ac 0
   ```

   Never hibernate while plugged in:

   ```powershell
   powercfg /change hibernate-timeout-ac 0
   ```

5. Check free disk space; keep **at least 5 GB** free:

   ```powershell
   Get-PSDrive C
   ```

   Raw spectra are small (about 0.3 MB each). The live review saves a growing
   snapshot after each measurement, roughly 0.8 to 1 GB per run (estimated from
   a local test). A full disk would make required motion records fail.

**Arduino (operator first, then the program)**

6. Run `status` (Part A.2). You need `valid=true`, `moving=false`, `fault=NONE`.
7. Look at the needle. It must physically be where the record says: normally
   UP=1 after a previous run, or 0 if you just set HOME in the flask. If it was
   moved by hand, use Procedure A1 before continuing.

**Chemyx Channel 1 (operator first, then the program)**

8. Confirm the installed Channel 1 syringe is 20 mm / 20 mL. Confirm its tubing
   is the Stage 2 sampling path and the pump is not running.
9. Physically return the Channel 1 plunger to **fully depressed (0 mL)**. If the
   saved position is `KNOWN`, you can use `home` (Part B.2). Otherwise use your
   normal manual method, sending the liquid where it belongs.
10. Run `status` and `verify` (Part B.2). For `06` you need: HOME established
    for the same port and settings, `Ledger owner: none`, and Channel 1 STOP
    acknowledged. `UNKNOWN` is acceptable for `06` only after step 9.

**NMR (operator)**

11. The NMReady is on, in iFlow, idle, and no other program is acquiring.
    To check this from the laptop, run the read-only
    [NMR connection test](../09_nmr_instrument/01_standalone_tests.md) (step 5 must show `NMR IDLE`).
12. Check the network (offline Windows command):

    ```powershell
    Test-NetConnection 169.254.30.54 -Port 5000
    ```

    `TcpTestSucceeded : True` is required for any spectra.

13. Optional, about 2 minutes: one real 8-scan acquisition through the same NMR
    worker and the same NMR settings. It uses the full-run script, but opens no
    pump, needle or dose record, and asks one `y`:

    ```powershell
    python scripts\04_si6_fixed_time_run.py --nmr-check
    ```

    Continue only if it prints `NMR CHECK NMR_SAVED`. Its output goes to
    `results\fixed_time\nmr_check\<id>\`. It refuses while an experiment owns the NMR.

**Experiment (operator)**

14. Stage 1 is finished, any reagent addition is done, and the Stage 2 reaction
    is ready. `06` cannot check this and never doses.
15. Validate the configuration:

    ```powershell
    python scripts\06_si6_stage2only_run.py --validate-config
    ```

    Expected: `stage2only configuration valid; 13 scheduled measurements
    including initial/endpoint; 8 scans; no hardware contacted`. For COM6 add
    `--machine-config configs\machines\si6_real_COM6.yaml`.

**What the program checks automatically at launch, before any liquid moves:**

| Order | Check | Failure stops the run? |
|---|---|---|
| 1 | YAML valid; Channel 1 geometry equals `manual_control.yaml` (20 mm, 20 mL, 0 mL) | Yes, before any instrument |
| 2 | Live review process starts | No (warning) |
| 3 | No other program holds the pump position record | Yes |
| 4 | NMR not owned by another experiment program | No: warning, and **no NMR STARTs this run** |
| 5 | Arduino COM3 present, not locked, READY identity, firmware version 1.2.1, configuration accepted | Yes |
| 6 | Chemyx port opens and answers HELP | Yes |
| 7 | STOP acknowledged on Channel 1 **and** Channel 2 | Yes |
| 8 | Arduino not moving, no fault | Yes |
| 9 | Needle record valid, calibration matches, no pending HOME marker | Yes |
| 10 | Channel 1 HOME exists and matches port and settings; your manual return to 0 mL is recorded | Yes |
| 11 | Needle moves to saved HOME=0, then UP=1, each verified | Yes |

The program does **not** check: the actual needle depth, the actual plunger
position, tubing, the reaction, NMR readiness before the first sample, disk
space or power settings.

### C.2 Launch

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\06_si6_stage2only_run.py --live
```

Chemyx on COM6:

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\06_si6_stage2only_run.py --live --machine-config configs\machines\si6_real_COM6.yaml
```

- **No prompts at all:** `--live` starts immediately, with no `y/N`, no
  intermediate confirmation, acknowledgment or dose reconciliation.
- **Startup output:** `Results: <run folder>` and `Live analysis review:
  <run folder>\analysis_live\index.html`.
- **Each finished cycle prints:** `STAGE2ONLY | sample N | scheduled X.XX h |
  cleanup OK | NMR_SAVED` (or `NMR_MISSING` / `NMR_FAILED`).
- **The end prints:** `STAGE2ONLY COMPLETE | observations: 13 | final cleanup:
  True`. Exit code 0 means COMPLETE; anything else exits 1.
- **Arduino config:** the default is used. `--arduino-config` exists only for
  another Arduino file and is not needed.

**Validation vs mock vs live:**

| Mode | Command | Contacts hardware? | Writes |
|---|---|---|---|
| Validate | `python scripts\06_si6_stage2only_run.py --validate-config` | No | Nothing |
| Mock | `python scripts\06_si6_stage2only_run.py --mock` | No: simulated instruments and an accelerated clock (finishes in seconds) | `results\stage2only\mock\SI6_STAGE2ONLY_<id>\`, including its own mock state; the real HOME records are untouched; no live review |
| Live | `python scripts\06_si6_stage2only_run.py --live` | Yes | `results\stage2only\live\SI6_STAGE2ONLY_<id>\`, plus the real needle and pump records |

`--output-dir <new folder>` chooses a different, not-yet-existing results folder.

### C.3 Schedule and duration

13 measurements, each one cycle start:

```text
0:00  0:30  1:00  1:30  2:00  2:30  3:00  3:30  4:00  4:30  5:00  5:30  6:00
```

- **The cycle:** withdraw 8 mL (needle UP), needle to HOME, withdraw 5 mL, 10 s
  settle, NMR 8 scans, needle UP, infuse 13 mL, withdraw 5 mL, infuse 5 mL. All
  transfers are Channel 1 at 5 mL/min.
- **Duration:** one cycle takes about 7.5 minutes of pump and needle time plus
  the NMR acquisition. Expect about 9 to 10 minutes per cycle, and up to about
  15 minutes if the NMR uses its full 430 s limit.
- **The 6:00 measurement starts at six hours** and must finish NMR and cleanup,
  so the run ends about 6 h 10 min after time 0.
- **If a cycle overruns its next slot,** the missed slots are skipped and
  logged. You see `Warning: N sampling slot(s) missed while a previous cycle
  finished; no catch-up burst`. There are no overlapping or catch-up cycles,
  and the 6:00 endpoint is still taken.
- **Time labels:** these are schedule labels. Actual acquisition time comes from
  the JCAMP metadata (`LONG DATE`).

**Never stops the run:** any NMR problem, all live-analysis problems, and failed
optional files (report, status, logs).
**Stops the run:** pump or needle command failures, STOP not acknowledged, a
position that can no longer be trusted, and failure to write required motion records.

---

## Part D: Errors and recovery

### D.1 Where the error appears

- **Console:** `STAGE2ONLY FAILED: <ErrorType>: <message>` or `STAGE2ONLY
  INTERRUPTED: ...`, then `STAGE2ONLY FAILED | observations: N | final cleanup: False`.
- **Before startup:** `stage2only startup failed: <ErrorType>: <message>`
  (configuration or output folder).
- **`report.json`:** the `status` and `error` fields.
- **`instrument_commands.log`:** driver detail during startup and each cycle.
- **`operation_journal.jsonl`:** every physical step, in order (Part E.3).
- **On any failure after the instruments open,** `06` sends STOP to both pump
  channels and the needle. It does **not** reverse or finish any transfer.

### D.2 Troubleshooting table

"Stops" means the physical run ends. Recovery IDs refer to the procedures in D.3.

**Startup and configuration**

| Error or symptom (actual text) | Likely cause | Stops? | Recovery | Verification |
|---|---|---|---|---|
| `stage2only startup failed: ValueError: ...` (for example `stage2only requires ...`) | Edited or wrong YAML | Yes, nothing contacted | C1 | `--validate-config` passes |
| `... Channel 1 manual HOME geometry/loading in ... must match ...; HOME is never redefined` | `manual_control.yaml` Channel 1 ≠ 20 mm / 20 mL / 0 mL | Yes, nothing contacted | C1, then B3 if the syringe really changed | `--validate-config` passes |
| `stage2only startup failed: FileExistsError` | `--output-dir` folder already exists | Yes | Use a new folder name, or omit `--output-dir` | Run starts |
| `ModuleNotFoundError: No module named ...` | Wrong Python window (PATH line not run) | Yes | Run the two setup lines (Part G) | Dependency check prints `dependencies OK` |
| `Warning: live analysis could not start: ...; timed sampling will continue` | Review process could not launch | **No** | None needed; run `07` afterwards | Run continues |

**Pump and pump records**

| Error or symptom (actual text) | Likely cause | Stops? | Recovery | Verification |
|---|---|---|---|---|
| `The Chemyx position ledger ... is owned by another process (..., pid N). Only one program may control the pump; finish or stop it first.` | Another controller is running | Yes, before motion | C2 | `status` shows `Ledger owner: none` |
| `Access denied opening COM4. The port is probably open in another program. ...` | Chemyx GUI, serial monitor or another script holds the port | Yes | C3 | `verify --channel 1 --live` passes |
| `Port COM4 not found. Check the cable, driver, and CHEMYX_PORT/machine YAML setting.` | Cable, power, driver, or the pump is on COM6 | Yes | C3 | `list_ports` shows the port; `verify` passes |
| `Chemyx HELP returned no response; check cable, port and power` | Pump off, wrong device, cable | Yes | C3 | `verify` passes |
| `Channel 1 STOP was not positively acknowledged (...); the pump is not proven idle` | Pump communication lost or no reply | Yes | B4 | `verify` shows `STOP positively acknowledged` |
| `Channel 2 STOP was not positively acknowledged (...)` | Same pump communication problem (06 STOPs Channel 2 too) | Yes | B4 | `verify --channel both --live` passes |
| `Channel 1: no established Chemyx HOME to preserve. Establish it with the standalone pump tool during manual setup.` | Channel 1 HOME never set, or its record is unrecoverable | Yes, before motion | B1 | `status` shows a HOME; `verify` has no FAIL |
| `Channel 1: saved Chemyx HOME does not match this setup: ... The experiment will not redefine it.` | Port (COM4↔COM6), diameter, capacity or HOME description differs from when HOME was set | Yes, before motion | B3 | `verify` shows the HOME reference PASS |
| `Stop after withdraw 8 mL could not be positively confirmed` (any transfer) | Communication loss during a transfer | Yes, mid-cycle | B2 + E.3 | Plunger inspected; `verify` passes |
| `diameter: sent ... but pump reported ...` / `No '... = ...' echo found in pump response` | Pump did not accept a setting | Yes | B4 | `verify` passes; check the configured values |
| `Could not open COM4: ...` / a serial error during a cycle | USB unplugged, pump powered off | Yes | B2 + B4 | `verify` passes |
| `Channel 1: movement ledger completion is uncertain; ...` / `Sampling cleanup did not restore the Channel 1 displacement` | Transfer completion could not be proven | Yes | B2 + E.3 | Plunger inspected |
| `Channel 1 position record failed read-back verification` / `JournalError: ...` | Required record write failed (disk full, file lock, permissions) | Yes | C4, then B2 | `Get-PSDrive C`; a mock run completes |

**Arduino and needle**

| Error or symptom (actual text) | Likely cause | Stops? | Recovery | Verification |
|---|---|---|---|---|
| `Configured Arduino port 'COM3' is not present` | Unplugged, unpowered, driver, different COM number | Yes | C5 | `list_ports` shows COM3; `status --live` works |
| `Arduino port is already locked by another process: ...` | Another `needle_control.py` or experiment is open | Yes | C2 | `status --live` works |
| `Could not open Arduino port COM3: ...` (access denied) | Arduino IDE serial monitor or another program | Yes | C3 | `status --live` works |
| `Timed out waiting for Arduino READY identity` / identity or version mismatch / `Firmware runtime configuration did not match reviewed YAML` | Wrong device on COM3, firmware not 1.2.1, Arduino not ready | Yes | C5; inspect hardware if it persists | `status --live` works |
| `Arduino is moving or faulted at startup: {...}` | Fault latched (`COMMUNICATION_LOSS`, `MOTOR_TIMEOUT`, `BOTH_LIMITS_ACTIVE`) or still moving | Yes, before motion | A3 | `status --live` shows `moving=false; fault=NONE` |
| `Needle HOME is unconfirmed; physically inspect and use needle_control.py confirm-home ...` | Saved position invalid: never set, after STOP-in-motion, interrupted move, `--steps` partial move, corrupt file | Yes, before motion | A1 | `status --live` shows `valid=true` |
| `HOME recovery verification is incomplete; use needle_control.py home to re-establish the reference` | A HOME recovery did not finish (marker present) | Yes, before motion | A1 (`home`; do not delete the marker) | `status --live` shows `valid=true` |
| `Unresolved STOP verification failure; verify the standalone stop command before HOME.` (from `home`) | A STOP never verified | Recovery blocked | A2 | `home` proceeds |
| `Arduino motion/reference verification failed: {...}. Use the standalone Arduino script ...` | Firmware/record disagree, moving, fault, or file changed | Yes | A1 | `status --live` valid |
| `Motion did not complete; commanded position is uncertain: ...` / `Timed out waiting for DONE ...` / `JOG completion step count does not match requested movement` / `Needle firmware is moving or faulted` | Needle movement failed or was interrupted (USB loss, stall) | Yes, mid-cycle | A1 (and A3 if a fault is latched) + E.3 | Needle inspected; `status --live` valid |
| `Software needle limit exceeded` / `Arduino travel limit: ...` | Target outside −3..+5 | Yes / refused | Inspect; do not reset HOME to bypass | — |

**NMR (all nonfatal; the observation is recorded as missing/failed and the schedule continues)**

| Error or symptom (actual text in the observation's `error`) | Likely cause | Stops? | Recovery | Verification |
|---|---|---|---|---|
| `Warning: NMR ownership unavailable; no STARTs will be sent: ...` (at startup) | `04 --nmr-check`, `04`, `08` or another `06` holds the NMR | **No**, but this run collects **no spectra** | C6 | Next run shows `NMR_SAVED` |
| `NMR idle not confirmed; START skipped to avoid overlap` / `NMR became busy/unknown during setup; START skipped` | NMR busy (previous acquisition still running), status unreadable or not understood as idle, NMR UI in use | No | Check NMR UI and network when convenient; do not start manual acquisitions. Between runs, the [NMR connection test](../09_nmr_instrument/01_standalone_tests.md) shows the exact status reply and why it was refused | Later samples `NMR_SAVED` |
| `NMR acquisition not confirmed complete after 300s (...)` | Acquisition hung or status never showed completion | No | As above | Later samples saved |
| `NMR worker: TimeoutExpired: ...` | Whole acquisition exceeded the 430 s limit | No | As above | Later samples saved |
| `Raw TD JCAMP absent in iFlow response and local export ...` | NMR returned no raw FID | No | Check NMR export settings in its UI | Later samples saved |
| `Raw TD retrieval failed after acquisition completed: ...` / connection refused / timeout errors | Network dropped | No | `Test-NetConnection 169.254.30.54 -Port 5000` | `TcpTestSucceeded : True` |
| `NMR state/receipt changed during raw retrieval; unrelated data was not saved` | Someone used the NMR during retrieval | No | Leave the NMR alone during the run | — |

**Analysis (all nonfatal; never restart the physical experiment for these)**

| Symptom | Where | Stops? | Recovery |
|---|---|---|---|
| Job `FAILED` or `SKIPPED` on `index.html` | `analysis_live\index.html`, `analysis_live\stage_2_000N\processor.log`, `analysis_live\worker.log` | No | Run `07` after the run (Part F) |
| `No authoritative JCAMP acquisition timestamp in ...; no filename/mtime substitution` | That snapshot's log | No | The file stays unanalysed; filename/mtime timing is never used |
| Processing, ACME phasing, baseline, peak integration or plot failure | `processor.log` | No | `07`, optionally with a corrected `--config` |
| Review snapshot killed after 600 s | `index.html` shows FAILED | No | `07` (1-hour default limit) |
| Live page not updating | `analysis_live\status.json` | No | Measurements continue; run `07` afterwards |

**Computer and files**

| Symptom | Stops? | Recovery |
|---|---|---|
| `Warning: optional output ... failed` / `optional observation export failed` / `optional instrument log ... unavailable` / `run_state.json projection unavailable; verified journal sequence N retained` | No | Free disk space or close the program holding the file when convenient |
| Brief Windows file lock on a state file | No: required writes retry for about a second | — |
| Disk full / persistent `PermissionError` on a required record (`JournalError`, read-back failure) | Yes | C4 |
| Laptop slept, restarted or lost power | Yes, abruptly (no final report) | C7 + E |
| Ctrl+C | Yes: `STAGE2ONLY INTERRUPTED` | E |
| `Warning: Stage 2 completed; connection/lock close failed: ...` | No: all 13 were already done | None |

### D.3 Recovery procedures

Every procedure ends with a check. Relaunch only when every instrument is
stopped, its saved state is valid, and you have looked at the physical state.

#### A1. Needle position unknown, invalid, interrupted or moved by hand

**What happened:** `06` only accepts a valid saved needle position. It never
invents or replaces one. The needle may not be where any record says.

**Stops the experiment:** yes (startup or mid-cycle).

1. Make sure nothing is moving. If a STOP failed earlier, do A2 first.
2. Check communication and the saved state:

   ```powershell
   python arduino\scripts\needle_control.py status --live --config arduino\configs\arduino_real_COM3.yaml
   ```

   If `fault=` is not `NONE`, do A3.
3. Look at the needle. Use `up`, `down` or `--steps` to bring it to the correct
   sampling depth in the flask. These work while the position is unknown.
4. Re-establish tracking at that physically verified spot:

   ```powershell
   python arduino\scripts\needle_control.py home --live --config arduino\configs\arduino_real_COM3.yaml
   ```

   With an unknown position it does not move. It asks you to confirm the needle
   is at HOME=0. Answer `y` only if it really is at the sampling position. If
   the record still says `valid=true` but you know the needle was moved by hand,
   use `set-home` instead: `home` would trust the old record and move.
5. Confirm: the output says `ARDUINO HOME COMPLETE` and `Position tracking: VALID`.
6. Re-check with `status --live`: `0; valid=true; ... moving=false; fault=NONE`.
   Optionally run `up` once and confirm it rises.

**Cleared when** `status --live` shows `valid=true`, `moving=false`,
`fault=NONE`, and the needle is physically where the number says.

#### A2. A STOP was never verified

**Error:** `home` reports `Unresolved STOP verification failure; verify the
standalone stop command before HOME. No position fault cleared.`

1. `python arduino\scripts\needle_control.py stop --live --config arduino\configs\arduino_real_COM3.yaml`
2. It must print `STOP sent. No HOME reference was inferred.` without an error.
   If STOP cannot be verified (the command errors), check the USB cable and
   Arduino power. Use the 24 V driver power disconnect if motion may continue.
   Do not continue until STOP verifies.
3. Then do A1. The position stays unknown after STOP; STOP never restores it.

**Cleared when** `stop` completes without error and A1 is complete.

#### A3. Arduino reports a fault

**Error:** `Arduino is moving or faulted at startup: {... 'fault': '...'}` or
`status` shows `fault=` other than `NONE`.

- **`moving=true`:** wait, then run `stop` (A2).
- **`COMMUNICATION_LOSS`** (USB dropped during a move): reconnect, then run
  `home` (A1). After you physically place the needle and confirm, the supported
  HOME procedure clears this fault.
- **`MOTOR_TIMEOUT` or `BOTH_LIMITS_ACTIVE`:** `home` refuses (`HOME rejected:
  unrelated/unresolved controller fault`). There is **no software-only
  recovery** in this repository.
  - Inspect the mechanics, driver power and wiring, using
    [Arduino safety and failure modes](../01_arduino/06_safety_and_failure_modes.md).
  - The firmware keeps the fault in memory, so a fault normally clears when the
    board restarts. This is from reading the firmware source and was not tested.
  - After any restart the position is unknown, so do A1.

**Cleared when** `status --live` shows `fault=NONE` and A1 is complete.

#### B1. Channel 1 has no HOME

**Error:** `Channel 1: no established Chemyx HOME to preserve.` (06) or
`no HOME reference exists` (manual commands).

1. Confirm the 20 mm / 20 mL syringe is installed and the pump is stopped.
2. Physically bring the plunger to fully depressed (empty). The software cannot
   move a channel without HOME, so use the pump's own controls or your lab's
   manual method, sending any liquid to an appropriate place.
3. Run `set-home --channel 1 --live` (Part B.2) and answer `y`.
4. Run `verify --channel 1 --live`; `status` must show displacement 0 mL.

**Cleared when** `verify` ends `VERIFY: READY`.

#### B2. Channel 1 position UNKNOWN, partial or interrupted transfer, or moved by hand

**What happened:** a transfer did not reach confirmed completion, a STOP was
sent, a previous program ended during a transfer, the record was edited or
damaged, or the plunger was moved with the front panel or by hand. The saved
displacement can no longer be trusted. The HOME *definition* is still kept.

1. Stop the pump: `stop --channel both --live` (Part B.2). If that cannot
   reach the pump, use the front-panel STOP or switch it off.
2. Inspect the syringe and lines. Read the run's journal (Part E.3) to see
   which transfer was uncertain. The actual volume moved cannot be known from
   software. **Do not assume a transfer completed.**
3. Decide where the liquid in the syringe belongs, then physically return the
   plunger to fully depressed (empty).
4. Choose:
   - **To relaunch `06` only:** nothing else is needed. At startup `06` records
     your manual return to the existing HOME without redefining it.
     `verify` may still show the HOME reference as FAIL while UNKNOWN; that is expected.
   - **To use manual `home`/`infuse`/`withdraw`:** they refuse while UNKNOWN.
     Run `set-home --channel 1 --live` with the plunger verified empty. This is
     legitimate because empty is the physical HOME.
5. `status` shows the expected state; `verify --channel 1 --live` has no
   communication or STOP failure.

**Cleared when** the plunger is visibly empty, the pump is stopped, and `verify`
passes (or only the expected UNKNOWN HOME line fails, if you are relaunching `06`).

#### B3. Saved Channel 1 HOME does not match the setup

**Error:** `Channel 1: saved Chemyx HOME does not match this setup: <what
changed>. The experiment will not redefine it.`, or `the configuration no
longer matches the saved HOME reference (...)`.

1. Read what changed. It is usually `pump_identity: 'serial:COM4' -> 'serial:COM6'`
   (you switched ports), or a diameter, capacity or HOME-description edit.
2. If a YAML was edited by mistake, restore it. See what changed with
   `git diff -- configs\chemyx\manual_control.yaml configs\experiments\si6_stage2only_6h.yaml`.
3. If you really changed port or syringe, verify the installed syringe, return
   the plunger to empty, and run `set-home --channel 1 --live` with the
   `--machine-config` you will run on.
4. Run `verify --channel 1 --live` with that same machine file.

**Cleared when** `verify` shows the HOME reference PASS with the same machine
file you will launch with.

#### B4. Pump STOP not acknowledged or communication failure

**Errors:** `Channel 1 STOP was not positively acknowledged`, `Channel 2 STOP
was not positively acknowledged`, `Chemyx HELP returned no response`, serial or
echo errors.

1. Check pump power and the USB cable. Make sure nothing else holds the port (C3).
2. If the pump may still be running, use the front-panel STOP.
3. Run `python scripts\chemyx_control.py verify --channel both --live --config configs\chemyx\manual_control.yaml --machine-config configs\machines\si6_real_COM4.yaml`.
4. Both `Channel 1 idle` and `Channel 2 idle` must say `STOP positively
   acknowledged`. `06` needs both.
5. If the failure happened mid-run, also do B2 for Channel 1.

**Cleared when** `verify --channel both --live` shows both channels idle and no
communication FAIL.

#### C1. Configuration rejected

1. Read the message: it names the setting.
2. Compare with Git: `git diff -- configs\experiments\si6_stage2only_6h.yaml configs\chemyx\manual_control.yaml configs\machines\si6_real_COM4.yaml configs\machines\si6_real_COM6.yaml`.
3. Undo unintended edits. Never change the sampling sequence or volumes to get past an error.
4. Run `python scripts\06_si6_stage2only_run.py --validate-config`.

**Cleared when** validation prints `stage2only configuration valid`.

#### C2. Another program controls the pump or Arduino

1. Find Python programs:

   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, CommandLine
   ```

2. Let a running experiment finish, or stop it in its own window with Ctrl+C.
   Finish or close any manual command. Do not kill a process that may be moving
   an instrument, and never delete lock files.
3. Run `status --channel 1` (Part B.2). If it still says `IN USE` but that
   process no longer exists, run `recover-stale` (Part B.2). It does no motion
   and keeps unfinished moves UNKNOWN. A leftover owner file alone does not
   block `06`.
4. If a manual or experiment program was interrupted during motion, do A1 or B2.

**Cleared when** `status` shows `Ledger owner: none` and `needle_control.py status --live` connects.

#### C3. Serial port access denied or wrong port

1. Close the Chemyx GUI, Arduino IDE serial monitor, PuTTY/TeraTerm and other scripts.
2. `python -m serial.tools.list_ports` must list COM3 and the pump port.
   - If the pump now appears as COM6, launch with the COM6 machine file and
     expect B3 (HOME belongs to its port).
   - If the Arduino is no longer COM3, see C5.
3. Unplugging and re-plugging USB is fine, but treat any instrument that might
   have been moving as uncertain (A1, B2).

**Cleared when** `verify --channel 1 --live` and `needle_control.py status --live` both connect.

#### C4. A required record could not be written

**Errors:** `JournalError: ...`, `Channel 1 position record failed read-back
verification`, or a needle state write error. Disk full or a persistent
permission/lock problem.

1. `Get-PSDrive C`: free space if low. Do not delete raw data or state records;
   remove copies or old analysis outputs you no longer need.
2. Make sure `C:\code\chemyx_pump` is not opened by sync or backup software
   holding files, and no editor has the state files open.
3. Treat the step in progress as uncertain: B2 for the pump, A1 for the needle.
4. Run `python scripts\06_si6_stage2only_run.py --mock` to confirm records can
   be written. It must end with `STAGE2ONLY COMPLETE`.

#### C5. Arduino not found or not identified

1. Check the USB cable and Arduino power. `python -m serial.tools.list_ports`
   should list COM3.
2. The port is set by `arduino.port: COM3` in
   `arduino\configs\arduino_real_COM3.yaml`. If Windows now lists the Arduino
   under another number, first reconnect it to its usual USB socket. Changing
   that setting is a configuration change; verify the device with
   `status --live` before trusting it.
3. Run `needle_control.py status --live`. An identity or version error means a
   different device or firmware is on that port: hardware inspection is needed.
4. After reconnecting, do A1 if the needle may have moved.

#### C6. NMR owned by another program at startup

The run continues sampling **without any NMR acquisition**.

1. If you need spectra, stop with Ctrl+C as early as possible. The warning
   appears before the first liquid transfer. Then follow Part E.
2. Close the other program (`04 --nmr-check`, `04`, `08` or another `06`).
3. Relaunch after the restart checks in Part E.

#### C7. Windows restart, sleep or power loss

The controller ended without its final report: `report.json` may still show
`INCOMPLETE`.

1. Check power settings (Part C.1 step 4).
2. Run `needle_control.py status --live` and `chemyx_control.py status --channel 1`.
3. Inspect both instruments physically. Then do A1 if the needle is invalid or
   was mid-move, and B2 if Channel 1 is UNKNOWN or was mid-transfer.
4. Read the journal (Part E.3) to see what was in progress.

---

## Part E: Interrupted experiments and restarting

### E.1 Restarting is not resuming

`06` has **no pause or resume**. A new launch always:

- creates a **new** run folder;
- starts a **new** six-hour clock at time 0, with its own first measurement;
- does not reuse earlier measurements or recover the elapsed reaction time.

The earlier run folder and its raw spectra are preserved unchanged. Analyse it
separately with `07`. There is no supported procedure for joining two runs into
one time series.

Two separate decisions:

1. **Instrument recovery:** get the needle and Channel 1 into a verified, valid
   state. This manual covers that.
2. **Chemistry decision:** the reaction kept progressing during the
   interruption. Whether a new six-hour Stage 2 run is scientifically appropriate
   (same reaction, new reaction, or stop) is the operator's decision. No
   validated continuation procedure exists.

### E.2 What state is left behind

| When it stopped | Needle record | Channel 1 record | What to do |
|---|---|---|---|
| Ctrl+C while waiting between cycles (most of the time) | Valid, at UP=1 | Known, displacement 0 | Physical check, then relaunch is allowed |
| During a pump transfer (any cause) | Usually valid | **UNKNOWN**; the journal shows the transfer `uncertain` | B2 |
| During a needle move | **Invalid** (`motion_failed_or_interrupted`, `stopped_during_motion` or `stop_status_unknown`) | Known, unless a transfer was also running | A1 (A2 first if STOP is unresolved) |
| During NMR acquisition (needle in the flask, pump idle) | Valid, at HOME=0 | Known, about +13 mL (the cycle's two withdrawals are held in the syringe) | Inspect; decide where that liquid belongs, then empty Channel 1 (`home` shows its fluid path and asks `y`; B2 if UNKNOWN) |
| During cleanup | As above, depending on the step | Often non-zero or UNKNOWN | B2 and/or A1 |
| NMR failed but the run continued | Unaffected | Unaffected | Nothing; that observation is `NMR_FAILED`/`NMR_MISSING` |
| Windows restart or power loss | Valid if idle, invalid if mid-move | UNKNOWN if mid-transfer | C7 |
| Stopped after observation 1 or 6 | Depends on the moment, as above | Same | Observations 1 (or 1 to 6) remain valid data in the old folder; a new launch is a new run |

Before relaunching after any interruption, physically check both instruments.
Records can be valid while the physical state is not, for example after hand movement.

### E.3 Read what happened

Find the run folder (newest first):

List the five newest run folders:

```powershell
Get-ChildItem results\stage2only\live -Directory | Sort-Object Name | Select-Object -Last 5 Name
```

Store the newest folder in $Run; the commands below use it (run them in the same window):

```powershell
$Run = (Get-ChildItem results\stage2only\live -Directory | Sort-Object Name | Select-Object -Last 1).FullName
```

Show the stored folder:

```powershell
$Run
```

Final status:

```powershell
Get-Content "$Run\report.json" -Raw | ConvertFrom-Json | Select-Object status, error, physical_cleanup
```

Last completed measurement. Each line in `observations.jsonl` is written only
after that cycle's physical cleanup completed:

```powershell
Get-Content "$Run\observations.jsonl" | ForEach-Object { $o = $_ | ConvertFrom-Json; "{0}  {1}  scheduled {2} h  cleanup {3}" -f $o.observation, $o.status, ($o.scheduled_seconds / 3600), $o.physical_cleanup }
```

The last physical steps in the operation journal:

```powershell
Get-Content "$Run\operation_journal.jsonl" -Tail 12 | ForEach-Object { $_ | ConvertFrom-Json } | Select-Object sequence, event_type, operation_type, channel, lifecycle_state, target
```

How to read it:

- **Pump transfer:** `operation_type` `withdraw`/`infuse`. Lifecycle
  `planned` → `dispatch_started` → `completed` means it finished.
  `uncertain` means it may have moved partially. `failed` with
  `motion_not_started` means it never started.
- **Pump STOP:** `pump_stop` records should end `completed`.
- **Needle:** `needle_requested` followed by `needle_completed` (with the same
  `target`) means the move finished. A `needle_requested` without a matching
  `needle_completed` means it is uncertain.
- **Clean finish:** a cleanly finished run ends with `stage_completed` and
  `stage2only_complete`.

Find every uncertain step:

```powershell
Get-Content "$Run\operation_journal.jsonl" | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.lifecycle_state -eq "uncertain" } | Select-Object sequence, operation_type, channel, error_message
```

`instrument_commands.log` shows the exact driver commands and replies.

---

## Part F: Results and NMR analysis

### F.1 Run folder

```text
results\stage2only\live\SI6_STAGE2ONLY_<UTC time>_<id>\
  configuration.json          settings used
  observations.jsonl          one line per finished cycle (status, error, schedule, cleanup)
  operation_journal.jsonl     required record of every physical step
  instrument_commands.log     driver commands and replies
  run_state.json              derived controller state
  report.json                 final status, error, all observations
  nmr_raw\stage_2_0001.dx ... raw FIDs (only for saved acquisitions)
  nmr_raw\stage_2_000N.request.json / .result.json   NMR request and outcome
  analysis_live\index.html    live review page
  analysis_live\status.json   review worker status
  analysis_live\worker.log    review worker log
  analysis_live\stage_2_000N\ one snapshot per measurement (processor.log, processed\ plots and tables)
```

No dose files are created. Raw files are never modified by analysis.

### F.2 Live review

```powershell
Start-Process "$Run\analysis_live\index.html"
```

- **What the page shows:** it refreshes every 15 s. It shows
  `Analysis: WAITING`, `PROCESSING` or `FINISHED`. Each measurement row shows
  `COMPLETE`, `FAILED` (with its log) or `SKIPPED` (no saved spectrum).
- **Snapshots:** each one processes all valid spectra so far with the existing
  ACME processor, one at a time, at low priority. It has no access to the
  instruments and the run never waits for it.
- **Failures:** a failed review never stops or shortens the run. Re-run the
  analysis with `07`.

### F.3 Post-run analysis (`07`)

```powershell
python scripts\07_analyze_stage2only.py
```

- **Folder:** a picker opens. Choose the run folder or its `nmr_raw` folder.
  Without a desktop picker it asks for a path. Cancelling does nothing.
- **Known folder:** `python scripts\07_analyze_stage2only.py "$Run"`.
- **Separate output location** (for example, the data is on a read-only drive):
  `python scripts\07_analyze_stage2only.py "$Run" --output-dir "C:\data\stage2_analysis"`.
- **Output:** a new `analysis_stage2only\<timestamp>_<id>\` folder each time,
  containing `ANALYSIS_SUMMARY.txt`, `analysis_summary.json`,
  `observation_inventory.csv`, `acquisition_metadata.csv`, `processor.log` and
  `stage_2\` (plots, tables, statistics).
- **Data safety:** it never contacts instruments or changes raw data, and it is
  safe during a run (CPU-heavy).
- **Exit codes:** 0 complete, 1 partial/failed, 2 no usable spectra.

More options: [07 — Stage 2 analysis recovery](07_stage2only_analysis_recovery.md).
The SciPy scripts `08`/`09` are a different method; do not mix their outputs
with this workflow.

---

## Part H: How this document was verified

Checked on 2026-10-19 against the working tree, without contacting any instrument:

- **Argument parsers:** every command was checked with `--help`, for
  `needle_control.py`, `chemyx_control.py`, `chemyx_home.py`, `06` and `07`.
- **Quoted output:** the needle and Chemyx output above was captured from mock
  runs pointed at scratch copies of the state records.
- **Configuration:** `06 --validate-config` passed.
- **Full mock run:** a mock `06` run completed all 13 samples (0 to 6 h) with
  `COMPLETE`. `07` processed its 13 spectra (`COMPLETE`).
- **Tests:** the 48 stage2only, live-review and `07` tests passed.
- **Error messages:** messages are quoted from the code paths reachable from
  `06`. The Chemyx position record, needle state, pump driver, Arduino
  transport, NMR worker and live-review worker were all traced.

Not verified on hardware: real NMReady replies, real COM-port error wording from
Windows, Arduino fault clearing by a board restart (inferred from the firmware
source), and the cycle-time and disk-usage estimates.
