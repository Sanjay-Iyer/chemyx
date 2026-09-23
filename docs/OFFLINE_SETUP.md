# Offline Laptop Setup

How to run the Chemyx pump, the NMR, and the needle from a laptop with no
internet, and how the working scripts talk to each instrument. Everything here
comes from the scripts that ran on the real hardware:

- `scripts/02_si6_automated_nmr.py`: pump + NMR, ran 2026-08-10 (run
  `20260810_171441_si6`).
- `arduino/dm542s_hello_world/04_needle_up.py` and `05_needle_down.py`: needle,
  ran 2026-08-10 and 2026-08-11 (logs in `calibration_results/`).
- `scripts/nmr/process_fid.py`: NMR data processing.

## 1. What to bring

| Item | Needed for | Where it comes from |
|---|---|---|
| This repository folder, with `offline\wheelhouse\` and `offline\installers\` filled | Code, configs, data, every Python package | Section 2 |
| Python 3.11, 64-bit | Running everything | `offline\installers\python-3.11.9-amd64.exe` |
| `offline\arduino\` (portable arduino-cli 1.5.1, UNO R4 core 1.6.0, compiler, dfu-util) | Recompiling or re-uploading the needle firmware offline | Section 2 |
| Pump USB-serial driver | The pump's COM port | `offline\drivers\`, exported in section 2. Not needed if Windows' own driver runs the pump |
| Pump USB cable, plus the USB-to-RS-232 adapter if your setup uses one | Pump | Existing rig |
| USB-C **data** cable | Arduino UNO R4 Minima | A charge-only cable shows no COM port |
| Ethernet cable | NMR | Direct to the NMR or through the lab switch |
| 24 V DC adapter with its inline switch | DM542S needle driver | Existing rig |

The Arduino keeps its sketch through power cycles, so uploading firmware 1.2.0
before going offline is simplest. `offline\arduino\` lets the offline laptop
compile and re-upload anyway: the sketch needs only the Arduino core (no
third-party libraries). The UNO R4 Minima uploads with dfu-util to USB
2341:0369 (its bootloader). Windows 10/11 runs the board's normal COM port with
the built-in USB serial driver; if an upload cannot find the DFU device, run
`offline\arduino\data\packages\arduino\hardware\renesas_uno\1.6.0\post_install.bat`
once as Administrator to install the bundled bootloader driver.

## 2. Build the bundle (on a computer with internet)

Use any 64-bit Windows computer with internet and Python 3.11. From the
repository root:

```powershell
powershell -ExecutionPolicy Bypass -File offline\build_offline_bundle.ps1 -Python C:\path\to\python.exe
```

This script does four things:

- Fills `offline\wheelhouse\` with the 27 packages pinned in
  `offline\requirements-lock.txt`, the exact versions the test suites run on.
- Downloads `offline\installers\python-3.11.9-amd64.exe` from python.org and
  checks its signature.
- Proves the wheelhouse installs with the network switched off.
- Writes `offline\BUNDLE_MANIFEST.txt` with the SHA-256 of every file.

Then build the portable Arduino toolchain. It downloads arduino-cli (SHA-256
checked) and the UNO R4 core into `offline\arduino\`, skips driver
installation on this computer, compiles the firmware with the network blocked,
and writes `offline\arduino\ARDUINO_MANIFEST.txt`:

```powershell
powershell -ExecutionPolicy Bypass -File offline\arduino_toolchain.ps1 -Build
```

Next, on the laptop where the pump and needle already work, plug both in. Open
PowerShell **as Administrator** in the repository folder:

```powershell
powershell -ExecutionPolicy Bypass -File offline\serial_drivers.ps1 -Export
```

It lists every COM port with its driver. It copies any third-party driver
(`oemNN.inf`) into `offline\drivers\` and skips drivers built into Windows,
because the offline laptop already has those. Without `-Export`, it only lists
the ports.

Finally, copy the repository folder to a USB drive. With both bundles it is
about 2 GB: roughly 340 MB of wheels and the Python installer, 860 MB of
Arduino toolchain, and 580 MB of existing results. Use `robocopy` instead of
Explorer. It skips caches and the locked leftover pytest folders
(`test_tmp_*`, `arduino\.test-tmp-*`, and `results\archive\pytest_*`), which
would otherwise stop the copy. Use your USB drive's letter in place of `E:`:

```powershell
robocopy C:\code\chemyx_pump E:\chemyx_pump /E /XD .test-tmp* test_tmp_* pytest_* __pycache__ .pytest_cache .ruff_cache .venv .codex_tmp_deps /R:0 /W:0
```

Robocopy exit codes 0–7 mean success.

## 3. Install on the offline laptop

1. Copy the folder to `C:\code\chemyx_pump`, or another path of at most 50
   characters. The bundled Arduino compiler cannot open files beyond the Windows
   path limit, so a deep folder breaks the firmware compile. The commands below
   assume `C:\code\chemyx_pump`.
2. Run `offline\installers\python-3.11.9-amd64.exe`. Tick **Add python.exe to
   PATH**, then choose **Install Now**. Offline, Windows may say it cannot
   verify the app: choose **More info**, then **Run anyway**.
3. Open a **new** PowerShell window in `C:\code\chemyx_pump` and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File offline\install_offline.ps1 -RunTests
   ```

   This installs everything into `.venv\` from the wheelhouse, never touching
   the network. It checks imports and the required files, creates
   `configs\machines\00_machine.local.yaml`, validates the Si6 workflows, and
   compiles the needle firmware when `offline\arduino\` was copied. With
   `-RunTests` it then runs both test suites and the Level 1 and Level 2 mock
   workflows (about 8 minutes). Nothing contacts hardware.
4. If `offline\drivers\` contains anything, install it from PowerShell **as
   Administrator**, then unplug and replug the instruments:

   ```powershell
   powershell -ExecutionPolicy Bypass -File offline\serial_drivers.ps1 -Install
   ```

5. Set the date, time, and time zone by hand, because there is no internet time
   sync. The run journal uses the laptop clock, while NMR analysis uses the time
   the NMR stamps into each file (`LONG DATE`). On 2026-08-10 the NMR's clock
   was about 8–9 minutes ahead of the laptop's.

From now on, run every command from `C:\code\chemyx_pump` with
`.venv\Scripts\python.exe`. No activation step is needed.

## 4. Connect and configure each instrument

### Chemyx pump

1. Plug in the pump's USB cable. A new COM port appears in Device Manager under
   **Ports (COM & LPT)**. Running `offline\serial_drivers.ps1` shows the same
   list with drivers.
2. Put that port in `configs\machines\00_machine.local.yaml` as
   `chemyx.serial_port`. It was `COM6` on the working laptop; the number will
   differ. Never use an "Intel AMT" or "Communications Port" entry.
3. The baud rate set on the pump must match `chemyx.baud_rate`, which was 115200
   on the working setup.
4. Close the Chemyx software and any serial terminal. Only one program can open
   a COM port at a time.

### NMR

1. Connect the Ethernet cable. The NMR answers at `169.254.30.54`, port 5000.
   That is a link-local address, so no router or internet is involved.
2. Give the laptop's wired adapter an address on the same subnet. Go to
   **Settings > Network & internet > Ethernet > IP assignment > Edit >
   Manual**, turn on IPv4, and enter:
   - IP address `169.254.30.10`
   - Subnet mask `255.255.0.0`
   - Leave the gateway and DNS empty.

   Leaving it on automatic also works once Windows gives itself a 169.254.x.x
   address, which takes about a minute.
3. On the NMR touchscreen, remote control must be on: **Setup > System > Remote
   > Enable**. The NMR's `config/pygui.cfg` must also contain
   `RPC_API_ENABLED = True`. Without these, every settings or run request fails
   with `403 Forbidden`.
4. Test the link:

   ```powershell
   Test-NetConnection 169.254.30.54 -Port 5000
   ```

   It should report `TcpTestSucceeded : True`. A browser pointed at
   `http://169.254.30.54:5000/interfaces/iStatus/PingSpectrometer` should show
   `{"connected": true}`.

`nmr.host` and `nmr.port` in the machine config already hold these values.

### Needle (Arduino UNO R4 Minima + DM542S)

The `dm542s_hello_world` scripts below are historical bring-up notes using
the older bridge serial protocol. The active 1.2.0 controller deliberately
preserves the same validated **D3 STEP / D4 DIR** wiring but uses a different
serial protocol and Python persistent software-position tracker. Do not mix
old scripts with new firmware. There is no ENABLE connection or upper/lower
limit switch requirement. See `arduino/README.md` for the current commands.
The following numbered legacy steps are not the new commissioning procedure.

1. Plug in the USB-C data cable. The Arduino appears as a COM port with USB
   vendor ID 2341. Windows 10/11 normally runs it with its built-in USB serial
   driver.
2. Put that port in `serial.port` of every needle config you use:
   `arduino\dm542s_hello_world\configs\04_needle_up.yaml` and
   `05_needle_down.yaml` (also `01_needle_move.yaml` and
   `99_needle_calibration.yaml` if used). It was `COM3` on the working laptop.
3. Follow this power order: plug in the Arduino USB first, then switch on 24 V
   to the DM542S. Switch off 24 V before unplugging USB. The rig has no limit
   switches, no homing, and no emergency stop input.

### Files to edit

| File | Setting | Value on the working setup |
|---|---|---|
| `configs\machines\00_machine.local.yaml` | `chemyx.serial_port` | `COM6` (will differ) |
| same file | `nmr.host`, `nmr.port` | `169.254.30.54`, `5000` |
| `arduino\configs\arduino.local.yaml` (copy of `arduino.example.yaml`) | Arduino port, D3/D4 review, software limits, movement calibration, UP/DOWN logical positions | Commission per the checklist, section A |
| `arduino\dm542s_hello_world\configs\04_needle_up.yaml` and `05_needle_down.yaml` (legacy bridge only) | `serial.port` | `COM3` (will differ) |
| `configs\experiments\02_si6_automated_nmr.yaml` | `pump.syringe_diameter_mm`, `pump.syringe_capacity_ml`, `nmr.target_ppm` | 20.0 mm and 20 mL (verify against the installed syringe); 5.8 ppm |
| `configs\nmr\analysis.local.yaml` (optional) | `input.paths`, `output.directory` | Only needed to run `process_fid.py` with no arguments |

## 5. First bring-up, in order

For the **new three-instrument Si6 workflow**, follow
[LIVE_COMMISSIONING_CHECKLIST.md](LIVE_COMMISSIONING_CHECKLIST.md): first
complete the staged live Arduino commissioning and verify the Chemyx and NMR
separately. Then run the new scripts from the repository root in this order:

```powershell
.venv\Scripts\python.exe scripts\01_three_instrument_system_test.py --mock --all
.venv\Scripts\python.exe scripts\02_si6_experiment.py --mock
```

These contact no hardware. Only after the active firmware, software position
reference, reviewed UP/DOWN positions, ports, and fluid path are commissioned should an operator
consider `--live` on Level 1, followed by one attended short Level 2 cycle.
Both scripts default to validation-only when no mode is given. Level 2's fast
mock skips repeated full-spectrum `process_fid` calls; Level 1's mock runs one
full production processing pass. The legacy commands below are retained only
for historical bridge documentation, not for the new integrated rig.

Each step talks to one instrument. Stop at the first failure and check
section 8.

1. **Ports.** List what the laptop sees:

   ```powershell
   .venv\Scripts\python.exe scripts\diagnostics\01_list_serial_ports.py
   ```

2. **NMR reachable.** This check is read-only. It should report
   `PingSpectrometer` as connected and `RpcEnabled` as true:

   ```powershell
   .venv\Scripts\python.exe scripts\diagnostics\03_check_nmr_connection.py
   ```

3. **NMR acquisition.** Put a sample in the NMR first. This runs a 2-scan 1D
   acquisition with the workflow's settings (iFlow route, gain 12, auto-gain
   off, 5 ppm center, 20 ppm width) and saves the file:

   ```powershell
   .venv\Scripts\python.exe scripts\diagnostics\04_run_nmr_1d_acquisition.py --save-dx results\raw\nmr\generated\offline_check.dx
   ```

4. **Pump.** This infuses for 10 s, then withdraws for 10 s. Put the tubing in a
   safe container first. Pass the real inner diameter of the syringe on the
   pump; the script's default of 4.5 mm is wrong for this rig. It asks you to
   type `yes` before moving.

   ```powershell
   .venv\Scripts\python.exe scripts\diagnostics\02_verify_chemyx_movement.py --channel 1 --diameter 20.0 --rate 1 --volume 0.5
   ```

Steps 5 and 6 apply only to the archived bridge rig. Do not run them against
firmware 1.2.0; use section A of the commissioning checklist instead.

5. **Needle serial (legacy bridge only).** Keep the 24 V supply off for this check. Expect
   `PASS: Arduino serial communication is working`.

   ```powershell
   .venv\Scripts\python.exe arduino\dm542s_hello_world\01_serial_hello.py --port COM3
   ```

6. **Needle motion (legacy bridge only).** Switch on 24 V first. Each script moves 90° (200 steps) and
   asks you to type `RUN`:

   ```powershell
   .venv\Scripts\python.exe arduino\dm542s_hello_world\05_needle_down.py
   ```

   ```powershell
   .venv\Scripts\python.exe arduino\dm542s_hello_world\04_needle_up.py
   ```

7. **Full workflow.** Run a dry run first. It opens no hardware but writes a
   journal-backed run folder:

   ```powershell
   .venv\Scripts\python.exe -B scripts\02_si6_automated_nmr.py --dry-run
   ```

   A real run drops `--dry-run` and asks you to type `RUN SI6`. Add
   `--workflow-config configs\experiments\03_081626_phsi4.yaml` to repeat the
   single-measurement Aug 10 run instead of the full Si6 schedule. Use a normal
   PowerShell window: the workflow refuses to start without an interactive
   terminal.

## 6. How the scripts talk to each instrument

### 6.1 Chemyx Fusion 4000X pump: text commands over serial

**Code:** `chemyx_lab\instruments\chemyx.py` (class `Pump`), called from
`chemyx_lab\workflows\si6_automated_nmr.py`.

| Setting | Value |
|---|---|
| Port | From `chemyx.serial_port` (`COM6` on the working laptop) |
| Baud | 115200, matching the pump's own setting |
| Framing | 8 data bits, no parity, 1 stop bit, no flow control |
| Read timeout | 2.0 s |
| Line ending | Carriage return `\r` |
| Channel prefix | `1 ` (from `pump.channel: 1` in the experiment YAML) |

For each command, the code does four things:

1. Clears the serial buffers.
2. Writes `1 <command>\r`.
3. Waits 0.2 s.
4. Reads the pump's reply.

The last read waits out the 2 s timeout, so every command takes about 2.2 s.
The Aug 10 journal shows exactly that.

This is the exact exchange from the Aug 10 run
(`configs\experiments\03_081626_phsi4.yaml`):

```text
1 set units 0          reply names the units      (0 = mL/min)
1 set diameter 20.0    reply "diameter = 20.0"    (syringe inner diameter, mm)
1 set rate 5.0         reply "rate = 5.0"
-- then for every transfer --
1 set volume -8.0      reply "volume = -8.0"      (negative withdraws, positive infuses)
1 start 0              pump starts moving
                       laptop waits volume/rate + 2 s = 8/5 min + 2 s = 98 s
1 stop
```

After each `set` command, the code finds `<name> = <number>` in the reply. If
the number differs, it stops with `EchoMismatchError`, because the pump quietly
ignores out-of-range values. The pump never reports that a move has finished:
the laptop computes the time, waits, then sends `stop`. The workflow also sends
`stop` on any error, operator abort, or Ctrl+C.

The Aug 10 cycle, timed from `operation_journal.jsonl` (laptop clock, EDT):

| Step | Started | Took |
|---|---|---|
| Withdraw 8 mL at 5 mL/min | 17:15:03 | 1 min 45 s |
| Withdraw 5 mL | 17:16:48 | 1 min 9 s |
| Settle | 17:17:57 | 10 s |
| NMR, 8 scans | 17:18:07 | 1 min 21 s |
| Infuse 13 mL | 17:19:28 | 2 min 45 s |
| Withdraw 5 mL | 17:22:13 | 1 min 9 s |
| Infuse 5 mL | 17:23:22 | 1 min 9 s |

The whole cycle took 9 min 27 s, and the analysis then took 21 s.

**Try it by hand** (no motion). Close every other program using the port, then
paste this into `.venv\Scripts\python.exe`:

```python
import serial, time
pump = serial.Serial("COM6", 115200, bytesize=8, parity="N", stopbits=1, timeout=2)
for command in ["1 set units 0", "1 set diameter 20.0", "1 set rate 5.0"]:
    pump.reset_input_buffer()
    pump.write((command + "\r").encode("ascii"))
    time.sleep(0.2)
    print(command, "->", pump.read(256).decode("ascii", "replace").strip())
pump.close()
```

### 6.2 NMR: JSON over HTTP (NMReady iFlow remote API)

**Code:** `chemyx_lab\instruments\nmr.py` (class `NmrRpcClient`) and
`run_nmr_acquisition` in `chemyx_lab\workflows\instrument_operations.py`. It
uses only the Python standard library.

| Setting | Value |
|---|---|
| Base URL | `http://169.254.30.54:5000` |
| Request timeout | 10 s |
| Status polling | Every 2 s, giving up after 300 s |

An acquisition goes through these requests:

1. `GET /interfaces/iFlow/Settings/1D` returns the current 1D settings.
2. The code sets `ReceiverGain` to 12.0, `AutoGain` to false, and
   `ExportFilename`, then sends the result back with
   `PUT /interfaces/iFlow/Settings/1D`.
3. `GET /interfaces/iFlow/ExperimentSettings`. The code sets `NumberOfScans` to
   8, `ReceiverGain` to 12.0, `SpectralCentreInPpm` to 5.0,
   `SpectralWidthInPpm` to 20.0, and `ExportFilename`.
4. `PUT /interfaces/iFlow/RunExperiment` with those settings starts the
   acquisition.
5. `GET /interfaces/iFlow/ExperimentStatus` repeats every 2 s until the status
   no longer shows the run in progress.
6. The final status JSON carries the JCAMP-DX text. The laptop writes it to
   `raw_nmr\<date>_<time>_<label>_<scans>scan_gain<gain>.dx`, taking
   `JDX_FileContents_FD` if it is filled and otherwise `JDX_FileContents_TD`.
   On Aug 10 this produced the FID: `##DATA TYPE=NMR FID`, NMReady v2.2.5.1,
   8 scans, toluene, 62.7 s acquisition.

The code changes only those fields and sends every other field back untouched,
so solvent, pulse width, and point count stay as set on the NMR.

**Try it by hand** (read-only):

```powershell
Invoke-RestMethod http://169.254.30.54:5000/interfaces/iStatus/PingSpectrometer
Invoke-RestMethod http://169.254.30.54:5000/interfaces/iStatus/RpcEnabled
Invoke-RestMethod http://169.254.30.54:5000/interfaces/iFlow/ExperimentSettings
```

Do **not** send a GET to `/interfaces/iFlow/RunExperiment`. Per the vendor API
reference (`docs\reference\nmr_rpc_api\html\rpc-api.md`), a GET there starts an
acquisition with the last settings.

### 6.3 Needle: text lines over serial to the Arduino bridge sketch

**Code:** `arduino\dm542s_hello_world\single_move_utils.py`, `motion_utils.py`,
and `serial_test_utils.py`. The firmware is
archived at
`archive\legacy_arduino_firmware\proven_dm542s_bridge\arduino_dm542s_bridge.ino`.

The signal path is: laptop, then USB serial, then the Arduino, which pulses D3
(STEP) and D4 (DIR), then the DM542S driver, then the NEMA 17 motor. The wiring,
DIP switches, and power are recorded in
`arduino\dm542s_hello_world\CONFIRMED_SETUP.md`.

| Setting | Value |
|---|---|
| Port | From `serial.port` in the needle YAML (`COM3` on the working laptop) |
| Baud | 115200 |
| Line ending | Newline `\n` (the sketch ignores `\r`); 23 characters maximum |
| Read timeout | 0.25 s per line |
| Start-up | The script opens the port, waits 2 s, and discards what the board printed (`READY UNO_R4_DM542S`) |

| Send | Reply | Notes |
|---|---|---|
| `PING` | `PONG Arduino serial and LED test passed` | Blinks the LED three times |
| `MOVE -200` | `START MOVE -200`, then `DONE MOVE -200` when finished | Negative is down, positive is up; 1–5000 steps |
| `STATUS` | `STATUS IDLE` or `STATUS MOVING <done> OF <n>` | Answered even during a move |
| `STOP` | `STOPPED MOVE <done> OF <n>` or `STOPPED IDLE` | Software stop only; the driver stays energized |
| Anything invalid | `ERROR ...` | For example busy, zero steps, or more than 5000 |

The sketch fixes the speed at 5 ms high plus 5 ms low per step, which is 100
steps per second. With the DM542S at 800 steps per revolution, 200 steps is 90°
and takes 2 s.

**What `05_needle_down.py` does** (as logged on 2026-08-11 at 15:03):

1. Reads `movement.distance: 90` in degrees and converts it: 90/360 × 800 = 200
   steps.
2. Checks the limits (at most 5000 steps, within ±1000 of the start).
3. Prints the plan and waits for you to type `RUN`.
4. Opens the port, waits 2 s, and sends `MOVE -200`.
5. Waits for `DONE MOVE -200`. The timeout is 1.25 × the expected time + 2 s,
   with a minimum of 5 s.
6. Writes `calibration_results\needle_down_<timestamp>.yaml`.

Ctrl+C sends `STOP`. With no encoder or homing, the only position record is the
steps commanded, and 04 and 05 do not know about each other. Use the same
distance in both directions.

**Try it by hand** (no motion). Paste into `.venv\Scripts\python.exe`:

```python
import serial, time
arduino = serial.Serial("COM3", 115200, timeout=0.25, write_timeout=2)
time.sleep(2.0)                  # same start-up wait as the scripts
arduino.reset_input_buffer()     # drop the READY banner
arduino.write(b"PING\n")
deadline = time.time() + 5
while time.time() < deadline:
    line = arduino.readline().decode("utf-8", "replace").strip()
    if line:
        print(line)              # PONG Arduino serial and LED test passed
        break
arduino.close()
```

### 6.4 How a run fits together

Workflow 02 drives the pump and the NMR. You move the needle yourself from a
**second** PowerShell window when prompted. The two can run at once because they
use different COM ports.

With `02_si6_automated_nmr.yaml`, each measurement runs this cycle:

1. Withdraw 8 mL.
2. Prompt: "Lower the needle". Run `05_needle_down.py` in window 2, then type
   `yes` in window 1.
3. Withdraw 5 mL, then wait 300 s.
4. Take the NMR spectrum.
5. Infuse 13 mL.
6. Prompt: "Raise the needle". Run `04_needle_up.py`, then type `yes`.
7. Withdraw 5 mL, then infuse 5 mL.

Results go to `results\runs\si6\<timestamp>_si6\`. `operation_journal.jsonl` is
the authoritative record; `raw_nmr\` holds the `.dx` files, and
`processed_nmr\` holds the automatic `process_fid.py` output.

`arduino\scripts\test_04_integrated_system.py` is the only script that
addresses all three instruments. It needs different Arduino firmware
(`arduino\firmware\needle_controller`), and its full sequence is not approved
for live use.

## 7. Processing NMR data offline

Process every `.dx` file in a folder, finding all peaks from 5.0 to 6.5 ppm:

```powershell
.venv\Scripts\python.exe scripts\nmr\process_fid.py results\raw\nmr\06-09-26 --run-name 06-09-26_region-peaks
```

Output goes to `results\processed\nmr\<run-name>\`. `peaks_simple.csv` is the
one-row-per-spectrum table for the ~5.8 ppm peak. Settings live in
`configs\nmr\analysis.yaml`. Reusing a run name deletes and rewrites that
folder.

To phase a spectrum interactively and send the chosen phase to the same
pipeline, open the GUI (the argument is optional):

```powershell
.venv\Scripts\python.exe scripts\nmr\phase3.py results\raw\nmr\generated\offline_check.dx
```

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Access denied opening COMx` | Another program holds the port | Close the Chemyx software, the Arduino IDE Serial Monitor, PuTTY, or the other script |
| `Port COMx not found` | Wrong port or missing driver | Run `offline\serial_drivers.ps1`; install `offline\drivers\` |
| Pump never replies | Baud mismatch | Match the pump's baud to `chemyx.baud_rate` (115200) |
| `EchoMismatchError` | Pump rejected a value | Check diameter (0.103–40 mm), rate for the units, and `pump.channel` |
| NMR `timed out` or `URLError` | Network | Check the cable and a 169.254.x.x/16 address; run `Test-NetConnection` |
| NMR `HTTP 403` | Remote control off | On the NMR: Setup > System > Remote > Enable |
| NMR works in a browser but not in Python | A Windows proxy setting | Run `$env:NO_PROXY = "169.254.30.54"` in that window first |
| No `PONG` | Wrong port, charge-only cable, or Serial Monitor open | Check the port and cable; close the Arduino IDE |
| `ERROR controller busy` | A needle move is still running | Wait for `DONE`, or send `STOP` |
| Si6 run stops with `analysis_inconclusive` (exit 7) | The spectrum had no QC-passing peak in 5.70-5.90 ppm, or acquisition, processing, or `LONG DATE` failed | See below |
| Live run refuses to start: "Previous live run review" | The last live run stopped outside its rest state or needs review | Reconcile it, then add `--acknowledge-review <run id>` |

**Measurement failures.** The three-instrument workflow tracks the resonance
at 5.8 +/- 0.10 ppm with production `process_fid` (the 2026-08-10 run stopped
because the old setting watched 6.1 ppm). When a measurement fails after the
sample was withdrawn and the pump and needle states are proven, the workflow
still returns the sample and completes the cleanup, then stops (exit 7) with
the run marked for operator review. A spectrum recorded before any product
forms also stops the run this way. Inspect the run with
`scripts\02_si6_automated_nmr.py --inspect-run <run folder>` before starting
another.
