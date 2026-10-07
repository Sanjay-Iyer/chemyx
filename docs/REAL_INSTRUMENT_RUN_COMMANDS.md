# Real instrument commands — standalone Windows laptop

Current expected ports: **Arduino COM3; Chemyx COM4**. Alternate: **Arduino COM3; Chemyx COM6**. Both use the existing NMR RPC address **169.254.30.54:5000**.

## Step 0 — repository and existing environment

Use the prepared standalone `.venv`. Activation is optional because every command below names its Python directly:

```powershell
cd C:\code\chemyx_pump
.\.venv\Scripts\Activate.ps1
$si6MachineCfg = 'configs\machines\si6_real_COM4.yaml'
```

For the alternate pump, replace only the last command with:

```powershell
$si6MachineCfg = 'configs\machines\si6_real_COM6.yaml'
```

Run subsequent commands in this same PowerShell window. If your checkout is elsewhere, change the `cd` path. Close other programs using the instrument ports. Internet is unnecessary; the laptop must still reach the NMR over its instrument network.

## Step 1 — optional quick configuration load

**Purpose:** catch configuration errors without opening hardware. **Configs:** `configs/experiments/si6_real_two_stage.yaml`, selected machine YAML, `arduino/configs/arduino_real_COM3.yaml`.

```powershell
.\.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config $si6MachineCfg --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Expected:** `Configuration valid. No hardware opened` and two stages with 20 measurements each. This checks settings; it does not certify hardware readiness.

## Step 2 — real three-instrument communication check

**Purpose:** open the integrated interfaces and check Arduino PING/STATUS, Chemyx HELP and NMR PING. **Configs:** `configs/experiments/si6_real_hardware_test.yaml`, selected machine YAML, `arduino/configs/arduino_real_COM3.yaml`.

```powershell
.\.venv\Scripts\python.exe -B scripts\01_three_instrument_system_test.py --live --communications-only --workflow-config configs\experiments\si6_real_hardware_test.yaml --machine-config $si6MachineCfg --arduino-config arduino\configs\arduino_real_COM3.yaml
```

Enter `y` at the live-instrument confirmation. **Expected:** `Arduino communication OK`, `Chemyx communication OK`, `NMR communication OK`, then `PASS`. Startup sends pump STOP/configuration commands; there is no pump START, needle movement or NMR acquisition. This checks NMR connectivity; acquisition and processing occur in the experiment.

### Establish HOME before motion

Physically inspect and place the needle at the existing HOME reference (0). With the axis stationary, register that reference:

```powershell
.\.venv\Scripts\python.exe -B arduino\scripts\needle_control.py confirm-home --live --config arduino\configs\arduino_real_COM3.yaml
```

Enter `y` after inspection. **Expected:** logical position 0, valid true. This registers the current position; it does not search for a home switch. Positions remain HOME=0, UP=1, DOWN=-1, 200 steps/unit.

## Step 3 — real integrated Channel 1 test

**Purpose:** check all three connections, then run the existing small pump diagnostic on Channel 1. **Configs:** `configs/experiments/si6_real_channel1_test.yaml`, selected machine YAML, `arduino/configs/arduino_real_COM3.yaml`.

```powershell
.\.venv\Scripts\python.exe -B scripts\01_three_instrument_system_test.py --live --integrated-channel-test --workflow-config configs\experiments\si6_real_channel1_test.yaml --machine-config $si6MachineCfg --arduino-config arduino\configs\arduino_real_COM3.yaml
```

Enter `y`. **Expected:** all three communication checks pass; needle returns to HOME then UP; **Channel 1 withdraws 0.5 mL and infuses 0.5 mL at 5 mL/min**, with STOP after each move. Needle remains UP. No NMR acquisition or reaction cycle runs. Supervise the test with the intended liquid path ready.

## Step 4 — real integrated Channel 2 test

**Purpose:** check all three connections and address Channel 2 explicitly. **Configs:** `configs/experiments/si6_real_channel2_test.yaml`, selected machine YAML, `arduino/configs/arduino_real_COM3.yaml`.

```powershell
.\.venv\Scripts\python.exe -B scripts\01_three_instrument_system_test.py --live --integrated-channel-test --workflow-config configs\experiments\si6_real_channel2_test.yaml --machine-config $si6MachineCfg --arduino-config arduino\configs\arduino_real_COM3.yaml
```

Enter `y`. **Expected:** all three communication checks pass; needle returns to HOME then UP; **Channel 2 withdraws 0.5 mL and infuses 0.5 mL at 1 mL/min**, with STOP after each move. Needle remains UP. This uses the existing UP-position diagnostic and does not deliver the 1.8 mL reaction dose. Supervise both channel tests and proceed only after PASS.

## Step 5 — actual two-stage experiment

**Purpose:** run the configured chemistry automatically. **Configs:** `configs/experiments/si6_real_two_stage.yaml`, the COM4 or COM6 machine YAML, `arduino/configs/arduino_real_COM3.yaml`. Check loaded syringes match the configured starting contents: Channel 1 retained volume 0 mL; Channel 2 retained volume 2 mL.

**COM4 command:**

```powershell
.\.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**COM6 command (choose one port variant):**

```powershell
.\.venv\Scripts\python.exe -B scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Expected successful path:** enter `y` once → Stage 1 → automatic endpoint → Channel 2 **1.8 mL at 1 mL/min**, needle DOWN then UP → Stage 2 automatically → endpoint → final cleanup → `completed`. **There is no Stage 2 readiness prompt.** Preparation and diagnostic commands have their own confirmations.

Each sampling cycle preserves: Channel 1 8 mL withdraw UP → DOWN → 5 mL withdraw → 300-second wait → NMR → 13 mL return → UP → 5 mL withdraw/5 mL infuse cleanup, all at 5 mL/min. NMR remains 8 scans, gain 12, auto-gain false, center 5 ppm, sweep width 20 ppm, target 5.8 ppm.

Stage 1 samples every 120 minutes (immediate first measurement), up to 20 iterations/48 hours, and completes when area is ≤2.5% of its initial area for 3 consecutive measurements. Stage 2 samples every 30 minutes, up to 20 iterations/12 hours, and requires ≥25% growth over its initial area followed by 4 stable observations (3 adjacent changes ≤2%). Moving tracked-peak area uses trapezoidal integration. Analytical QC is retrospective and cannot change stage decisions.

A stage reaching its iteration limit stops with `STAGE_1_MAX_ITERATIONS_REACHED` or `STAGE_2_MAX_ITERATIONS_REACHED`; reaching the duration ceiling also stops. Stage 1 limit does not authorize the dose. Hardware/serial, motion, NMR acquisition/processing, timeout or unsafe-state failures retain safe-stop handling. For an emergency, use the rig's physical disconnect; Ctrl+C also requests software stop.

## Files and adjustment locations

| File | Settings |
| --- | --- |
| `configs/machines/si6_real_COM4.yaml` | Chemyx COM4; existing baud/timeouts and NMR host/port. |
| `configs/machines/si6_real_COM6.yaml` | Identical settings, Chemyx COM6 only. |
| `arduino/configs/arduino_real_COM3.yaml` | Arduino COM3, firmware checks, needle positions/calibration and motion limits. |
| `configs/experiments/si6_real_hardware_test.yaml` | Communication diagnostic settings. |
| `configs/experiments/si6_real_channel1_test.yaml` | `three_instrument.diagnostic_channel: 1`, 0.5 mL test movements. |
| `configs/experiments/si6_real_channel2_test.yaml` | `three_instrument.diagnostic_channel: 2`, 0.5 mL test movements. |
| `configs/experiments/si6_real_two_stage.yaml` | Actual reaction: `pump.channels`, `workflow.cycle`, stage timing/`completion`, dose in `initial_stage.after_monitoring`, `nmr`, `analysis.peak_finding`, `qc_reporting`. |
| `configs/nmr/analysis.yaml` | Shared production phase/baseline processing defaults used by `scripts/nmr/process_fid.py`. |

No YAML editing is needed for the first run. The real config already has `workflow.experiment_id: SI6_REAL_TWO_STAGE_001`, identifying **one physical reaction**. Its durable reservation in `runtime/si6_doses/` refuses automatic restart or another run with that ID, including changing COM ports. For a later, separate reaction use a new unique experiment ID after reconciling the rig; never delete or rename dose evidence to bypass a restart block. Keep existing dose/state history when moving an already-used rig to another laptop.

Diagnostics save under `results/runs/si6_real_diagnostics/`; experiments under `results/runs/si6_two_stage/`. The experiment run folder contains the journal, spectra/time-series CSVs, raw/processed data and `final_qc/` reports. These run outputs and physical-state records are intentionally local. All command/config source files listed above are eligible for Git tracking and use no required ignored local configuration. New source files must be committed and pushed before they can arrive through `git pull`.
