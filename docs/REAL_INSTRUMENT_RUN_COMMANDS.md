# Real instrument commands — standalone Windows laptop

Assume you are already in the repository root and the correct Python/Conda environment is active. Every command below starts with `python`.

**Primary:** Arduino COM3, Chemyx COM4. **Alternate:** Arduino COM3, Chemyx COM6. NMR settings are identical: 169.254.30.54:5000. Choose one port command per step. Close other applications using the instrument ports; the laptop needs its NMR network connection, but no internet.

Recommended order: communication → confirm HOME → Channel 1 short test → Channel 2 short test → optional toluene soak → real chemistry.

## Step 1 — three-instrument communication check

**Purpose:** Open all three interfaces; Arduino PING/STATUS, Chemyx HELP, NMR PING. **Configs:** `si6_real_hardware_test.yaml`, selected machine YAML, `arduino_real_COM3.yaml`.

**COM4:**

```powershell
python scripts\01_three_instrument_system_test.py --live --communications-only --workflow-config configs\experiments\si6_real_hardware_test.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Alternate COM6:**

```powershell
python scripts\01_three_instrument_system_test.py --live --communications-only --workflow-config configs\experiments\si6_real_hardware_test.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Expected:** Enter `y`; Arduino, Chemyx and NMR communication OK, then PASS. Startup sends pump STOP/configuration commands; no pump START, needle movement or NMR acquisition.

## Step 2 — confirm Arduino HOME

**Purpose:** Establish the existing physical reference before motion. **Config:** `arduino/configs/arduino_real_COM3.yaml`.

Physically place and inspect the stationary needle at its existing HOME reference, then register it:

```powershell
python arduino\scripts\needle_control.py confirm-home --live --config arduino\configs\arduino_real_COM3.yaml
```

**Expected:** Enter `y` after inspection; logical position 0, valid true. This registers the current position; it does not search for a home switch. Existing positions remain HOME=0, UP=1, DOWN=-1, 200 steps/unit.

## Step 3 — Channel 1 short integrated test

**Purpose:** Check all three connections, then explicitly exercise Channel 1. **Configs:** `si6_real_channel1_test.yaml`, selected machine YAML, `arduino_real_COM3.yaml`.

**COM4:**

```powershell
python scripts\01_three_instrument_system_test.py --live --integrated-channel-test --workflow-config configs\experiments\si6_real_channel1_test.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Alternate COM6:**

```powershell
python scripts\01_three_instrument_system_test.py --live --integrated-channel-test --workflow-config configs\experiments\si6_real_channel1_test.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Expected:** Enter `y`; all connections pass; needle returns HOME then UP; Channel 1 withdraws 0.5 mL and infuses 0.5 mL at 5 mL/min, with STOP after each move. Needle remains UP. Supervise the intended liquid path. No NMR acquisition or reaction cycle runs.

## Step 4 — Channel 2 short integrated test

**Purpose:** Check all three connections, then explicitly exercise Channel 2. **Configs:** `si6_real_channel2_test.yaml`, selected machine YAML, `arduino_real_COM3.yaml`.

**COM4:**

```powershell
python scripts\01_three_instrument_system_test.py --live --integrated-channel-test --workflow-config configs\experiments\si6_real_channel2_test.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Alternate COM6:**

```powershell
python scripts\01_three_instrument_system_test.py --live --integrated-channel-test --workflow-config configs\experiments\si6_real_channel2_test.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Expected:** Enter `y`; all connections pass; needle returns HOME then UP; Channel 2 withdraws 0.5 mL and infuses 0.5 mL at 1 mL/min, with STOP after each move. Needle remains UP. This does not deliver the real 1.8 mL reaction dose. Proceed only after PASS.

## Step 5 — toluene full-system soak test

**Purpose:** Exercise Arduino + Channel 1 + NMR + processing repeatedly, with one small Channel 2 infusion. Optional, strongly useful before committing real sample. Load **toluene only** in the system and Channel 2 test syringe. **Configs:** `configs/experiments/si6_real_toluene_soak_test.yaml`, selected machine YAML, `arduino/configs/arduino_real_COM3.yaml`.

**Defaults:** 10 iterations; 60-minute start-to-start interval; immediate first iteration; maximum 12 hours; 8 NMR scans; Channel 2 once after iteration 5, 0.5 mL at 1 mL/min. Initial retained contents remain Channel 1=0 mL and Channel 2=2 mL.

**COM4:**

```powershell
python scripts\03_si6_real_soak_test.py --live --workflow-config configs\experiments\si6_real_toluene_soak_test.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Alternate COM6:**

```powershell
python scripts\03_si6_real_soak_test.py --live --workflow-config configs\experiments\si6_real_toluene_soak_test.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Expected:** Enter `y` once. Each iteration runs the full existing Channel 1 sampling → NMR acquisition → processing/peak analysis → needle UP → sample return → UP cleanup sequence. After iteration 5 cleanup, needle moves DOWN → Channel 2 infuses 0.5 mL → needle returns UP → sampling continues. Iteration 10 ends with cleanup, reports and `completed`.

No 5.8 ppm reaction peak is expected with toluene. Missing peaks record `peak_found=false`, `peak_area=0`, blank ppm/SNR/width as appropriate; analysis is still saved and the next iteration continues. No near-zero, growth, plateau or QC rule controls this test.

The first count or duration limit ends normally. A cycle already underway finishes its existing safe return/cleanup; no new cycle or Channel 2 action starts after the duration ceiling. If the ceiling occurs before the trigger, Channel 2 is not executed. Hardware, processing, state, STOP and interruption failures retain stop/review handling and do not report successful completion.

### Soak settings to edit

Edit only the needed values near the top of `configs/experiments/si6_real_toluene_soak_test.yaml`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `soak_test.iterations` | 10 | Maximum number of full sampling cycles. |
| `soak_test.interval_minutes` | 60 | Start-to-start interval; longer cycles cause the next start to run late. |
| `soak_test.max_hours` | 12 | Independent runtime ceiling. |
| `soak_test.channel2.enabled` | true | Enable/disable the single test infusion. |
| `soak_test.channel2.trigger_iteration` | 5 | Infuse after this completed cycle's cleanup. |
| `soak_test.channel2.volume_ml` | 0.5 | Test infusion volume; does not change the chemistry config. |
| `soak_test.channel2.rate_ml_min` | 1.0 | Test infusion rate. |
| `nmr.scans` | 8 | Scans per acquisition. |
| `workflow.cycle` → `action: pause` → `seconds` | 10 | Pre-NMR settling delay; set 0 for immediate acquisition. |
| `workflow.experiment_id` | SI6_TOLUENE_SOAK_001 | Unique ID for one physical soak; change for a separate test after reconciling the rig. |

Examples require YAML edits only:

| Scenario | Iterations | Interval (min) | Max hours | Channel 2 trigger | Scans |
| --- | --- | --- | --- | --- | --- |
| Short | 3 | 10 | 2 | 2 | 8 |
| Normal | 10 | 60 | 12 | 5 | 8 |
| Overnight | 20 | 60 | 24 | 10 | 8 |

The pre-NMR pause is 10 seconds, reduced from the earlier configured 300-second settling hold. It is a workflow setting, not a required NMR delay. The full pump cycle remains active in the short scenario. With immediate first acquisition scheduling, 10 hourly cycles take about 9 hours plus the final cycle; 20 take about 19 hours plus the final cycle.

**Outputs:** `results/runs/si6_real_soak/<timestamp>_soak_live/` contains raw acquisitions, production `processed_nmr/` outputs, usual `time_series.csv` with timestamp/source, peak metrics, `peak_found`, `channel2_action` and separate runtime elapsed hours, journal, `soak_summary.json` and `final_qc/` reports/plots. Peak area/ppm/SNR/width plots use JCAMP LONG DATE metadata and visible dataset titles. Missing metadata omits time plots; filename or modification time is never substituted. Absent peaks still produce valid reports.

## Step 6 — real two-stage chemistry experiment

**Purpose:** Run the existing reaction automatically after preparing the real sample. **Configs:** `si6_real_two_stage.yaml`, selected machine YAML, `arduino_real_COM3.yaml`. Check actual syringe contents against Channel 1=0 mL and Channel 2=2 mL, and reconcile the toluene test setup before chemistry.

**COM4:**

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM4.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Alternate COM6:**

```powershell
python scripts\02_si6_experiment.py --live --workflow-config configs\experiments\si6_real_two_stage.yaml --machine-config configs\machines\si6_real_COM6.yaml --arduino-config arduino\configs\arduino_real_COM3.yaml
```

**Expected:** One initial `y` → Stage 1 → area endpoint → Channel 2 **1.8 mL at 1 mL/min** → automatic Stage 2 → endpoint → final cleanup → completed. There is no Stage 2 prompt. The soak test does not alter this reaction config.

Both workflows reuse Channel 1: 8 mL withdraw UP → DOWN → 5 mL withdraw → 10-second wait → NMR → UP → 13 mL return → 5 mL withdraw/5 mL infuse cleanup, at 5 mL/min. NMR remains 8 scans, gain 12, auto-gain false, center 5 ppm, sweep width 20 ppm, target 5.8 ppm.

Chemistry endpoints remain Stage 1: every 120 min, 20 iterations/48 hours, ≤2.5% initial area for 3 observations. Stage 2: every 30 min, 20 iterations/12 hours, ≥25% growth then 4 stable observations (3 adjacent changes ≤2%). Exhausted limits stop rather than authorize chemistry progression. QC is retrospective. Chemistry output is under `results/runs/si6_two_stage/`.

## Configuration and restart notes

Machine ports/NMR settings are in `configs/machines/si6_real_COM4.yaml` and `si6_real_COM6.yaml`; Arduino positions/calibration are in `arduino/configs/arduino_real_COM3.yaml`. Workflow pump channels, sampling, acquisition, peak finding and thresholds live in their experiment YAML. Shared phase/baseline defaults are in `configs/nmr/analysis.yaml`.

The current sampling cycle raises the needle UP before the 13 mL return, including controlled cleanup after an NMR failure. The shared runner must also be updated on the instrument laptop; copying only the reordered YAML onto an older runner is insufficient.

Config files are loaded at startup: changing YAML does not alter a run already in progress. Apply the new pause to the matching YAML on the instrument laptop before the next run.

Each soak/reaction config identifies one physical run. The existing durable ledger under `runtime/si6_doses/` refuses automatic restart, including changing COM ports; never delete dose/state evidence to bypass a block. For a separate physical run reconcile the rig and choose a new `workflow.experiment_id`. Keep dose/state history when transferring an already-used rig. Use the physical disconnect in an emergency; Ctrl+C also requests software stop.

All source/config files above are Git-trackable and require no ignored local config. They must be committed/pushed before another laptop receives them through Git. Run data and physical-state history remain local.

Optional quick configuration load, without opening hardware: use the same soak command above with `--live` omitted.
