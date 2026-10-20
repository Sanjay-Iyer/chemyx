> [Documentation home](../00_START_HERE.md) | [Chemyx pump](00_START_HERE.md)

# Chemyx manual control and software HOME

These are standalone manual-tool rules. The fixed-time experiment refreshes
pump loading references from manual setup and does not require prior-dose
reconciliation. Its needle uses the saved Arduino-only HOME and returns to it;
the experiment never establishes a new needle HOME. See
[fixed-time restart behavior](../03_fixed_time_experiment/05_restart_behavior.md).

Copy-paste commands and file locations: [01_quick_reference.md](01_quick_reference.md).

Current defaults: 1.0 mL at 10.0 mL/min on either explicitly selected channel.
Failed-run and dose reconciliation commands are in
[fixed-time run guide, section 8](../03_fixed_time_experiment/01_run_guide.md#8--errors-and-recovery).
`--machine-config` optionally selects the existing COM4/COM6 file without editing
the manual YAML; it does not migrate saved physical HOME references.

`scripts/chemyx_control.py` is the standalone controller for the Chemyx Fusion
4000 (Channel 1 = sampling, Channel 2 = reagent dosing). It works like the
Arduino `needle_control.py` HOME: a software reference that the operator
establishes, plus tracked movement from that reference.

## What HOME means

**Set HOME** means: "The syringe is physically in the correct starting position.
Save this position as the software reference without moving the pump."

**HOME** means: "Use the saved reference and verified movement history to
calculate the infusion or withdrawal needed to return the syringe to that
position."

**HOME does not mean:** "Tell the Chemyx device to execute a native homing
command." The Chemyx has no such command, and this software never sends one.
HOME return uses the same `set volume` / `start` / timed wait / `stop` sequence
as every other transfer in the experiment.

A saved position is a software estimate from tracked commands. It is not a
measurement of the plunger. Nothing here can see the plunger.

### Sign convention

HOME is always 0 mL. Withdrawal is positive and infusion is negative, matching
the driver (`set volume +V` infuses, `set volume -V` withdraws).

| Operation | Before | After |
|---|---:|---:|
| Set HOME | any or unknown | 0 mL |
| Withdraw 5 mL | 0 | +5 mL |
| Infuse 2 mL | +5 | +3 mL |
| Withdraw 1 mL | +3 | +4 mL |
| Return HOME (infuse 4 mL) | +4 | 0 mL |

The return movement is `HOME - current`: positive withdraws, negative infuses,
zero does nothing. Volumes are stored as whole nanolitres, so rounding never
accumulates.

## Commands (PowerShell, repository root)

Use `--live` for the real pump and `--mock` for the mock pump. Mock commands
use a separate mock record and never touch the rig's record. On the instrument
laptop use `.venv\Scripts\python.exe` in place of `python`.

| Command | What it does | Moves the pump? |
|---|---|---|
| `status` | Shows the saved HOME, displacement, uncertainty and the calculated return | No |
| `verify` | Checks settings, references, dose records and (with `--live`) communication/idle | No |
| `prepare` | Sends units, syringe diameter and rate to the channel | No |
| `set-home` | Saves the confirmed physical plunger position as HOME = 0 | No |
| `home` | Calculates and performs the return to HOME | Yes |
| `infuse` | Infuses `manual_transfer.volume_ml`, or `--volume <mL>` | Yes |
| `withdraw` | Withdraws `manual_transfer.volume_ml`, or `--volume <mL>` | Yes |
| `stop` | Sends STOP and marks the stopped channel(s) UNKNOWN | No (stops) |

`--volume <mL>` (infuse, withdraw) and `--rate <mL/min>` (infuse, withdraw,
home) replace the YAML `manual_transfer` values for that one command only. The
YAML and the saved HOME are unchanged, and a volume that would take the syringe
below empty or above capacity is still refused before any pump command:

```powershell
python scripts/chemyx_control.py withdraw --channel 1 --volume 2.5 --rate 5 --live
```

```powershell
python scripts/chemyx_control.py status --channel both
```

```powershell
python scripts/chemyx_control.py verify --channel both --live
```

```powershell
python scripts/chemyx_control.py prepare --channel both --live
```

```powershell
python scripts/chemyx_control.py set-home --channel 1 --live
```

```powershell
python scripts/chemyx_control.py set-home --channel 2 --live
```

```powershell
python scripts/chemyx_control.py home --channel 1 --live
```

```powershell
python scripts/chemyx_control.py home --channel both --live
```

```powershell
python scripts/chemyx_control.py infuse --channel 1 --live
```

```powershell
python scripts/chemyx_control.py withdraw --channel 2 --live
```

```powershell
python scripts/chemyx_control.py stop --channel both --live
```

The convenience wrapper calls exactly the same HOME implementation:

```powershell
python scripts/chemyx_home.py --channel both --live
```

`status` without `--live` reads the saved record only and does not contact the
pump. Every moving command shows the planned direction, volume and fluid path,
then asks one `y` confirmation. `set-home` asks one `y` confirmation that the
plungers are physically in place. Answering anything else changes nothing.

## Configuration: `configs/chemyx/manual_control.yaml`

The YAML defines the HOME convention and manual settings. The actual HOME and
current displacement live only in the runtime record (below).

| To change | Edit |
|---|---|
| COM port | `chemyx.serial_port` in the file named by `machine_config` (or point `machine_config` at `configs/machines/si6_real_COM6.yaml`). Use the same machine file for the experiment. |
| Syringe diameter | `channels.N.syringe.diameter_mm` (null = value from `syringe_config`, the experiment recipe). The fixed-time run also needs the same value in `pump.channels.'N'.syringe_diameter_mm` of `configs/experiments/si6_fixed_48h_6h.yaml`, and refuses to load if they differ. |
| Syringe capacity | `channels.N.syringe.capacity_ml` (null = from `syringe_config`); likewise `syringe_capacity_ml` in the fixed-time YAML |
| Transfer volume | `channels.N.manual_transfer.volume_ml` (default; `--volume` overrides one command) |
| Transfer and HOME rate | `channels.N.manual_transfer.rate_ml_min` (mL/min; `--rate` overrides one command) |
| HOME description | `channels.N.home.reference_description` |
| Syringe contents at HOME | `channels.N.home.syringe_volume_at_home_ml` (0.0 = plunger fully depressed; null disables manual infuse/withdraw, HOME return still works) |
| Channel | the `1:` / `2:` keys; `--channel 1`, `2` or `both` on the command line |

Editing the YAML never silently resets a reference. If the pump port, syringe
diameter, capacity, HOME description or syringe volume at HOME no longer
matches what was saved at set-home, HOME and manual moves are refused until
you restore the old value or physically re-establish HOME with `set-home`.

## Where the record lives

```text
runtime/chemyx_state/            live rig record (Git-ignored)
  channel_1_state.json           Channel 1 HOME, displacement, uncertainty
  channel_2_state.json           Channel 2 HOME, displacement, uncertainty
  operations.jsonl               append-only, fsynced journal of every change
  manual_runs/                   per-command pump journals (STOP evidence)
runtime/chemyx_state_mock/       the same, for --mock runs only
```

Each change is journaled first, then the channel file is atomically replaced
and read back. Keep this folder with the rig's physical-state history (like
`runtime/si6_doses`); never copy it between rigs or restore an old copy. A
restored or edited file disagrees with the journal and is treated as UNKNOWN.

## How positions are updated

Every tracked move records its intent durably *before* the pump command is
sent. The displacement changes only after the existing completion evidence:
the timed move finished, STOP was positively acknowledged, and the move's own
run journal recorded completion. A command acknowledgement alone never
advances the position.

These all update the same record:

- `scripts/chemyx_control.py` / `chemyx_home.py` (manual moves and HOME);
- `scripts/02_si6_experiment.py` (every Channel 1 sampling transfer and the
  Channel 2 dose);
- `scripts/01_three_instrument_system_test.py` and `scripts/03_si6_real_soak_test.py`.

So a syringe set to HOME before the experiment can be returned to HOME after
it, from a separate command window, even after a restart. The automated
experiment adds no prompts and does not need HOME; if a channel has no HOME its
moves are still journaled but no return is offered.

The legacy runner (`scripts/02_si6_automated_nmr.py`), the Arduino integrated
test and `scripts/diagnostics/02_verify_chemyx_movement.py` do not track
displacement. In live mode they mark the saved position UNKNOWN before moving.

## Preparing both channels before a new experiment

1. Load and mount each syringe. Check `manual_control.yaml` matches the
   syringes and that `machine_config` is the machine file you will give the
   experiment.
2. `python scripts/chemyx_control.py prepare --channel both --live`
3. Physically put each plunger at its HOME position (for example Channel 1
   fully depressed; Channel 2 loaded to the volume written in the YAML).
4. `python scripts/chemyx_control.py set-home --channel both --live`, confirm with `y`.
5. `python scripts/chemyx_control.py verify --channel both --live`
6. Start the experiment as usual. Do not press Chemyx front-panel buttons
   during or after the run.

## If an experiment ends with a syringe away from HOME

1. `python scripts/chemyx_control.py status --channel both`
2. If a channel is KNOWN, read its calculated return. Route its tubing where
   that fluid should go (see below).
3. `python scripts/chemyx_control.py home --channel both --live`, check the
   fluid-path lines, answer `y`.
4. If a channel is UNKNOWN, follow the recovery below instead.

For `--channel both` each channel is checked before anything moves, then the
channels move one after another, each verified before the next. If Channel 1
completes and Channel 2 fails, Channel 1 stays at HOME and Channel 2 keeps its
own true state; nothing is reversed automatically.

## Why an UNKNOWN position is never returned automatically

A channel becomes UNKNOWN when its history has a gap: a crash or closed window
during a move, a STOP that was not acknowledged, a lost serial connection, a
manual `stop`, a move with different syringe settings, an untracked program,
a corrupt, edited or restored record, or a missing HOME. In each case the
plunger may have moved by an amount nobody recorded. Driving it by the old
number could over-infuse, pull fluid back into a syringe, or hit the end of
travel. So the software refuses, explains what is missing, and shows the last
trusted displacement for your inspection.

To recover:

1. `python scripts/chemyx_control.py stop --channel 1 --live` (or 2/both) if
   anything might be moving.
2. Inspect the syringe, tubing and flask. Move the plunger to its HOME position
   by hand or with the Chemyx front panel.
3. `python scripts/chemyx_control.py set-home --channel 1 --live`

Front-panel or Chemyx-GUI movement cannot be detected. After any of it, always
re-establish HOME before trusting `home`.

## Fluid paths and reagent safety

Returning to HOME is a real transfer. Withdraw 5 mL then HOME means pushing
5 mL back out through whatever tubing is connected. Before confirming, check
every fluid-path line the command prints.

Channel 2 also respects the experiment's reagent dose records
(`runtime/si6_doses`, read only):

- any DISPATCH_INTENT or unreadable record: all Channel 2 motion is refused;
- any RESERVED record (an admitted experiment without a confirmed dose):
  Channel 2 infusion is refused;
- otherwise a Channel 2 infusion (manual or as part of HOME) additionally needs
  `--allow-channel2-infusion` on that one command, after the reagent line is
  disconnected from every reactor and routed to waste.

`set-home` never changes or clears a dose record, and no manual command does.

## First supervised physical test

`configs/chemyx/first_motion_test.yaml` moves only 0.1 mL at 0.5 mL/min per
command, with HOME = plunger fully depressed on both channels. Route both lines
into a waste vial, and confirm by eye that both syringes are 20 mL syringes
with 20 mm inner diameter.

```powershell
python -m serial.tools.list_ports -v
python scripts/chemyx_control.py verify --channel both --live --config configs/chemyx/first_motion_test.yaml
python scripts/chemyx_control.py set-home --channel both --live --config configs/chemyx/first_motion_test.yaml
python scripts/chemyx_control.py withdraw --channel 1 --live --config configs/chemyx/first_motion_test.yaml
python scripts/chemyx_control.py home --channel 1 --live --config configs/chemyx/first_motion_test.yaml
python scripts/chemyx_control.py withdraw --channel 2 --live --config configs/chemyx/first_motion_test.yaml
python scripts/chemyx_control.py home --channel 2 --live --allow-channel2-infusion --config configs/chemyx/first_motion_test.yaml
python scripts/chemyx_control.py status --channel both --config configs/chemyx/first_motion_test.yaml
```

Each withdraw should visibly draw about 0.1 mL in, and each HOME should push
it back out. Afterwards `status` must show both channels `KNOWN, 0 mL from HOME`.
The Channel 2 return is an infusion, so it is refused while any
`runtime/si6_doses` record is RESERVED or uncertain; `status` lists them.

## Limits and required instrument-side verification

Mock tests prove the bookkeeping, not the hardware. On the rig, before relying
on HOME return:

- Confirm with a small withdraw/infuse that `set volume -V` withdraws and
  `+V` infuses on each channel, and that the delivered volume matches the
  configured diameter.
- Confirm addressed `1 stop` / `2 stop` acknowledgements and that a timed move
  finishes within its wait (otherwise STOP truncates it while the software
  records the full volume).
- Verify both syringe diameters and capacities physically.
- Confirm the experiment and this tool use the same machine file (COM port).
