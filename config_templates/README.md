# YAML templates for a new laptop

Run these commands from the repository root. Files in this directory are
**tracked, ready-to-edit templates**, but scripts do not read them here. Copy
each needed file to the exact runtime path below, then edit the copy. The
runtime `*.local.yaml` files are now **tracked in Git** as the shared starting
point, so a fresh clone or `git pull` already has them. Commit a local change
only when it should apply on every laptop. The Arduino files use `port: null`
and find the board by USB VID:PID, so the same file works on COM3 or COM9.
Never overwrite a local file that already contains reviewed settings. An
offline installer may already have created `00_machine.local.yaml` from the
older example; inspect it before using any instrument.

After flashing Arduino firmware 1.2.1, set both `arduino.expected_version`
and `firmware.version` to `1.2.1` in the Arduino local YAML.
Do not replace a reviewed local file with the template just to update version.

## Copy once

PowerShell commands below create a runtime file only if it is missing:

```powershell
if (-not (Test-Path arduino\configs\arduino.local.yaml)) { Copy-Item config_templates\arduino.local.template.yaml arduino\configs\arduino.local.yaml }
if (-not (Test-Path arduino\configs\integrated_hello_world.local.yaml)) { Copy-Item config_templates\integrated_hello_world.local.template.yaml arduino\configs\integrated_hello_world.local.yaml }
if (-not (Test-Path configs\machines\00_machine.local.yaml)) { Copy-Item config_templates\00_machine.local.template.yaml configs\machines\00_machine.local.yaml }
if (-not (Test-Path configs\nmr\analysis.local.yaml)) { Copy-Item config_templates\analysis.local.template.yaml configs\nmr\analysis.local.yaml }
```

For one file, the unguarded form is simply `Copy-Item
config_templates\arduino.local.template.yaml
arduino\configs\arduino.local.yaml`; use it only when the destination does not
already exist.

## Destinations and edits

| Tracked template | Exact runtime destination (directory) | Required? | Scripts that use it | Edit or physically verify |
| --- | --- | --- | --- | --- |
| `arduino.local.template.yaml` | `arduino/configs/arduino.local.yaml` (`arduino/configs/`) | Required for live Arduino-only tests and the main three-instrument workflow when passed with `--arduino-config`; not needed for mocks | `arduino/scripts/test_01_arduino_connection.py`, `test_02_unloaded_motor.py`, `test_03_needle_axis.py`, `needle_control.py` via `--config`; `scripts/01_three_instrument_system_test.py` and `scripts/02_si6_experiment.py` via `--arduino-config` | COM3 and USB VID/PID `2341:0069` were observed on the home laptop; verify/update for each connected board. Before motion, physically verify D3/D4 wiring, `needle.steps_per_unit`, `needle.up_step_sign`, logical bounds and UP/DOWN positions, speed, and safety acknowledgements. Never guess these. |
| `integrated_hello_world.local.template.yaml` | `arduino/configs/integrated_hello_world.local.yaml` (`arduino/configs/`) | Required only for live staged Test 4; optional for its mock/validation mode | `arduino/scripts/test_04_integrated_system.py` via `--config` | Set Arduino COM port and copy **verified** motion/needle values from `arduino.local.yaml`. Review `integrated.machine_config_path`, `integrated.experiment_config_path`, pump cycle indices, NMR diagnostic, artifact suffix, settle times, and safety acknowledgements. This is a separate full config, not an overlay. |
| `00_machine.local.template.yaml` | `configs/machines/00_machine.local.yaml` (`configs/machines/`) | Required for live pump/NMR diagnostics and live workflows; valve section only when a valve is used | `scripts/01_three_instrument_system_test.py`, `scripts/02_si6_experiment.py`, `scripts/02_si6_automated_nmr.py`, archived workflow 01, and diagnostics `02_verify_chemyx_movement.py`, `03_check_nmr_connection.py`, `04_run_nmr_1d_acquisition.py`, `06_check_mx_valve.py` via `--machine-config` or their default; staged Test 4 via `integrated.machine_config_path` | COM6 and `169.254.30.54` are documented prior-rig examples, **not** verified on a new laptop. Confirm the pump COM port, NMR address/port, and baud rates before live use. Set the optional valve port only if installed. |
| `analysis.local.template.yaml` | `configs/nmr/analysis.local.yaml` (`configs/nmr/`) | Optional; needed for no-argument per-computer `process_fid.py` input/output paths | `scripts/nmr/process_fid.py` automatically merges it over `configs/nmr/analysis.yaml` when no explicit `--config` is used | `input.paths` points to the existing `06-09-26` example dataset; update it for this laptop's dataset and review `output.directory`. No hardware calibration is involved. |

## Find this laptop's values

| Fields | What they identify; when to change | How to find them |
| --- | --- | --- |
| `arduino.port`, `arduino.fingerprint.vid/pid` in both Arduino templates | Windows COM assignment can change by laptop/USB socket; VID/PID identify the connected board. Home-laptop example: `COM3`, `0x2341`, `0x0069`. | With motor power off, compare port lists before/after plugging in Arduino USB. Use the commands below or Device Manager. The firmware identity/version check is still required. |
| `arduino.fingerprint.serial_number/manufacturer` | Optional stronger identifiers, often device-specific rather than laptop-specific. Both are intentionally `null`; no value was guessed. | Inspect pyserial output below and populate only an actually observed value. Do not put a made-up value in the YAML: a nonempty but wrong fingerprint blocks the port. |
| `chemyx.serial_port`, `chemyx.baud_rate`, optional `valve.serial_port` in `00_machine.local.template.yaml` | Pump and valve serial endpoints; COM assignments vary by laptop. `COM6` is an older work-laptop pump example, **not** the Arduino's COM3. | Attach one device at a time, compare port lists, identify the pump/valve in Device Manager, and verify baud rate against its setup. Do not infer the pump port from the Arduino port. |
| `nmr.host`, `nmr.port` in `00_machine.local.template.yaml` | NMR RPC endpoint; `169.254.30.54:5000` is documented from the earlier rig, not proven on a new laptop. | Read the spectrometer's network/RPC settings or the instrument network documentation. `ipconfig` describes the laptop, not the NMR. After verifying the address, `Test-NetConnection 169.254.30.54 -Port 5000` checks network reachability; it does not validate an NMR acquisition. |
| `input.paths`, `output.directory` in `analysis.local.template.yaml` | Existing `.dx` input tree and where processed results go; these often change with laptop storage. Example input: `results/raw/nmr/06-09-26`. | From the repo root, use `Test-Path results\raw\nmr\06-09-26` and `Resolve-Path results\raw\nmr\06-09-26`. For an output path that does not yet exist, verify its intended parent and available storage instead. |
| `needle.steps_per_unit`, `needle.up_step_sign`, `needle.min_position/max_position`, UP/DOWN positions, driver settings, and safety flags in the Arduino templates | Physical travel/direction and operator-reviewed safeguards; these do **not** change merely because the laptop changes, but can change with hardware or wiring. The example software bounds are not measured limits. | **Must be physically measured/verified on the instrument. Do not copy blindly from another laptop.** There is no encoder or HOME/limit switch to discover these automatically. Keep motion disarmed until verified; see `arduino/docs/TEST_02_GUIDE.md` and `TEST_03_GUIDE.md`. |
| `integrated.*` in `integrated_hello_world.local.template.yaml` | Paths to the machine/experiment configs plus reviewed Test 4 pump actions, NMR diagnostic and settle times. | The two paths already point to tracked/default locations. Inspect the actual cycle in `configs/experiments/02_si6_automated_nmr.yaml`; choose pump actions and timings only after reviewing the liquid path and the 120-second Test 4 budget. Unchosen actions remain `null` and block live Test 4B. |

Read-only Windows port discovery (does not open a serial connection):

```powershell
Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Name,PNPDeviceID
python arduino\scripts\test_01_arduino_connection.py --config arduino\configs\arduino.local.yaml --list-ports
python -c "from serial.tools import list_ports; [print(p.device, hex(p.vid) if p.vid is not None else None, hex(p.pid) if p.pid is not None else None, p.serial_number, p.manufacturer, p.description) for p in list_ports.comports()]"
```

Example: `COM3 0x2341 0x69 ... USB Serial Device`; YAML `0x0069` is the
same numeric PID as `0x69`. Windows GUI route: **Device Manager → Ports (COM &
LPT) → device → Properties → Details → Hardware Ids**. If the port list looks
ambiguous, unplug/replug one device and compare the lists; never guess.

`fingerprint.vid/pid` use YAML hexadecimal integers because the parser
compares them numerically. `serial_number` and `manufacturer` can stay null
with a verified COM port and VID/PID. Null motion/calibration/safety values
are deliberate **fail-closed** fields, not missing software dependencies.

The needle's durable position is **not YAML**: `needle.state_path` in the
Arduino config defaults to `runs/arduino/needle_state.json`. This generated
software estimate must never be copied as a template to a new laptop. After
physically inspecting the needle at the chosen HOME reference, use explicit
`confirm-home`; the rig has no physical HOME, encoder, or limit switches.
An interrupted/failed move or independent physical movement makes the saved
estimate untrustworthy until inspected and re-confirmed.

## Which command uses which file

These examples do not command physical movement unless explicitly noted.

```powershell
# Arduino-only: syntax check, then optional connection/LED check (no motor motion).
python arduino\scripts\test_01_arduino_connection.py --config arduino\configs\arduino.local.yaml --validate-only
python arduino\scripts\test_01_arduino_connection.py --config arduino\configs\arduino.local.yaml --live

# Needle-axis commissioning preflight (no motion); live Test 3 additionally
# requires a successful unloaded-motor Test 2 record.
python arduino\scripts\test_03_needle_axis.py --config arduino\configs\arduino.local.yaml --preflight-only --live

# Staged Arduino/Chemyx/NMR Test 4: configuration syntax only.
python arduino\scripts\test_04_integrated_system.py --config arduino\configs\integrated_hello_world.local.yaml --validate-only

# Main three-instrument test and experiment: no --live or --mock means validation only.
python scripts\01_three_instrument_system_test.py --workflow-config configs\experiments\02_si6_automated_nmr.yaml --machine-config configs\machines\00_machine.local.yaml --arduino-config arduino\configs\arduino.local.yaml
python scripts\02_si6_experiment.py --workflow-config configs\experiments\02_si6_automated_nmr.yaml --machine-config configs\machines\00_machine.local.yaml --arduino-config arduino\configs\arduino.local.yaml

# Pump mock (no physical pump movement); NMR endpoint check does contact the NMR.
python scripts\diagnostics\02_verify_chemyx_movement.py --mock --machine-config configs\machines\00_machine.local.yaml
python scripts\diagnostics\03_check_nmr_connection.py --machine-config configs\machines\00_machine.local.yaml

# Offline NMR processing; requires a real .dx input at analysis.local.yaml input.paths.
python scripts\nmr\process_fid.py
```

For individual supervised Arduino moves, use
`needle_control.py <status|confirm-home|up|down|return-home|stop> --live --config
arduino\configs\arduino.local.yaml`. `confirm-home` records an inspected
software zero and does **not** physically home the axis. Do not try a live
move until the physical setup is commissioned. `test_02_unloaded_motor.py`
requires the motor to be mechanically **disconnected** from the needle axis;
never run it coupled just to unlock Test 3. `04_run_nmr_1d_acquisition.py` can
start a real scan; `06_check_mx_valve.py` can move a valve. Follow
`docs/LIVE_COMMISSIONING_CHECKLIST.md` before integrated live work.

## YAMLs already present at their runtime locations

These tracked files are not duplicated in this template directory:

| Tracked YAML/YML | Role and scripts |
| --- | --- |
| `configs/experiments/02_si6_automated_nmr.yaml` | Default complete Si6 recipe for the three-instrument and legacy two-instrument workflows. It includes pump geometry, volumes, NMR settings, and stage timing; verify physical values before live use. For a local run variant, copy it to `configs/experiments/<name>.local.yaml` and pass `--workflow-config` explicitly. |
| `configs/experiments/03_081626_phsi4.yaml` | Historical one-run Chemyx/NMR recipe; select only with explicit `--workflow-config`. |
| `configs/nmr/analysis.yaml` | Shared NMR analysis and plotting settings. `process_fid.py` overlays `analysis.local.yaml` by default; the other `scripts/nmr/` analysis commands use this tracked YAML unless given their own `--config`. |
| `arduino/configs/arduino.example.yaml`, `arduino/configs/integrated_hello_world.example.yaml`, `configs/machines/00_machine.example.yaml`, `configs/nmr/analysis.local.example.yaml` | Existing tracked examples at their original locations. The templates above provide one clearly mapped copy point; COM/IP values are examples that must be verified on each laptop. |
| `arduino/dm542s_hello_world/configs/{01_needle_move,04_needle_up,05_needle_down,99_needle_calibration,needle_calibration}.yaml` | Tracked **older bridge** configs already at their script-expected locations. They use a different firmware/protocol and are not configs for the current `needle_controller.ino`; their COM ports, geometry, and calibration still require review if that older tooling is deliberately used. |
| `nmr_template/InitValues.yml` | Tracked input for an older standalone NMR script, not the current workflow. `nmr_template/main_justNMR.py` contains a hard-coded external YAML path, so copying a template inside this repo alone cannot make that old script portable; it would need a separate code change. |
| `archive/legacy_workflows/01_first_real_chemyx_nmr/configs/01_first_real_chemyx_nmr.yaml` and `archive/legacy_arduino_firmware/commercial_variant/*.example.yaml` | Tracked archive/reference configs, not active laptop setup files. |

`configs/experiments/*.local.yaml` files are optional per-run variations, not
fixed runtime requirements; the complete tracked Si6 recipe above is their
source. Do not invent a new experiment recipe just to set COM ports or NMR data
paths. Neither the templates nor the runtime local destinations are ignored by
`.gitignore`.
