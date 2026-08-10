# Operator Guide — Needle Motion Rig

This guide assumes you have never used Python, YAML, or Arduino before. Follow
it top to bottom. Every command is written out in full.

**Read the safety section at the end before you switch on the 24 V supply.**

---

## 1. Where everything lives

The project directory — every command in this guide is run from here:

```text
C:\code\chemyx_pump\arduino\dm542s_hello_world
```

To get there, open **Windows PowerShell** (press Start, type `PowerShell`, press
Enter) and paste this, then press Enter:

```bash
cd C:\code\chemyx_pump\arduino\dm542s_hello_world
```

You should see the prompt change to end with `dm542s_hello_world>`. If you get
"cannot find path", the project is somewhere else on this machine — search for
the folder name `dm542s_hello_world`.

### Every file, and what it is for

| File | What it is |
| ---- | ---------- |
| `arduino_dm542s_bridge\arduino_dm542s_bridge.ino` | **The Arduino firmware.** Upload this to the board. |
| `01_serial_hello.py` | Diagnostic: is the USB connection working? (24 V off) |
| `01b_led_blink_test.py` | Diagnostic: blink the board's LED ten times. (24 V off) |
| `02_slow_forward_test.py` | Diagnostic: one fixed 100-pulse move. |
| `03_forward_reverse_test.py` | Diagnostic: one fixed forward/reverse cycle. |
| `01_needle_move.py` | **Main script.** Runs a sequence of moves you define. |
| `99_needle_calibration.py` | **Calibration.** Works out how many millimetres one motor step moves the needle. |
| `configs\01_needle_move.yaml` | Settings for `01_needle_move.py`. |
| `configs\99_needle_calibration.yaml` | Settings for `99_needle_calibration.py`. |
| `configs\needle_calibration.yaml` | The calibration *result*. Written by script 99. **Do not hand-edit.** |
| `calibration_results\` | Where logs and calibration results are saved automatically. |
| `tests\` | Automated checks. You never need to run these to operate the rig. |
| `README.md` | Technical reference: wiring, protocol, theory. |
| `CONFIRMED_SETUP.md` | The exact hardware setup that was verified working. |
| `OPERATOR_GUIDE.md` | This file. |

**Which config controls which script** — the number at the front matches:

- `01_needle_move.py`  ->  `configs\01_needle_move.yaml`
- `99_needle_calibration.py`  ->  `configs\99_needle_calibration.yaml`

`configs\needle_calibration.yaml` is not a settings file. It is the *output* of
calibration, and the input that `01_needle_move.py` reads when working in
millimetres.

---

## 2. One-time setup

### 2a. Install the Python libraries

Run this once:

```bash
py -m pip install -r C:\code\chemyx_pump\arduino\dm542s_hello_world\requirements.txt
```

### 2b. Upload the firmware to the Arduino

The Arduino cannot do anything until the firmware is on it.

1. Open the **Arduino IDE**.
2. Menu: **File -> Open**, then navigate to and select this exact file:
   ```text
   C:\code\chemyx_pump\arduino\dm542s_hello_world\arduino_dm542s_bridge\arduino_dm542s_bridge.ino
   ```
3. Menu: **Tools -> Board -> Arduino Renesas UNO R4 Boards -> Arduino UNO R4 Minima**
4. Menu: **Tools -> Port -> COM3** (or whichever port your board shows).
5. Click the **Upload** button (the right-arrow, second from the left).
6. Wait for "Done uploading".
7. **Close the Arduino Serial Monitor** if it is open. Only one program at a
   time can use a COM port, and the Python scripts need it.

> **You must repeat this upload whenever the `.ino` file changes.** Python and
> the firmware have to agree on the exact commands they exchange. If they do
> not, you will see `ERROR unknown command` and the script will stop safely.

---

## 3. Which port is my Arduino on? (changing the serial port)

The scripts default to **COM3**. To find your actual port: in the Arduino IDE,
look at **Tools -> Port**. The one that disappears when you unplug the Arduino
is the right one.

If it is not COM3, you must edit **both** config files. Open each in Notepad:

```bash
notepad C:\code\chemyx_pump\arduino\dm542s_hello_world\configs\01_needle_move.yaml
```

```bash
notepad C:\code\chemyx_pump\arduino\dm542s_hello_world\configs\99_needle_calibration.yaml
```

Near the top of each file you will see:

```yaml
serial:
  port: COM3
  baud: 115200
  reset_wait_seconds: 2.0
```

Change `COM3` to your port, for example `COM7`. Save with **Ctrl+S** and close.

Leave `baud` at `115200`. It must match the firmware.

---

## 4. How to edit a YAML config file (read this first)

YAML files are plain text, but the **spaces at the start of each line matter**.

Rules that will save you:

1. **Never use the Tab key.** Only spaces. Notepad inserts a real tab character
   and YAML will reject the file.
2. **Keep the existing indentation.** If a line starts with two spaces, keep two
   spaces. If you copy a block, copy its leading spaces too.
3. **A list item starts with `- ` (a dash and a space).**
4. **Anything after `#` is a comment** and is ignored. The configs are full of
   comments explaining each setting; you can leave them alone.
5. **Save the file before running the script.**

If you get the indentation wrong, the script tells you the file name and the
problem, and **it stops before touching the motor**. Nothing moves. Fix the file
and run it again.

If you misspell a setting name, the script now catches that too and suggests
the correct spelling. It does not silently ignore it.

---

## 5. Running the diagnostics first

Do these in order the first time, and any time something stops working.

**Step 1 — USB only. Keep the DM542S 24 V supply switched OFF.**

```bash
python .\01_serial_hello.py --port COM3
```

Expected: the board's LED flashes three times and you see
`PONG Arduino serial and LED test passed`.

**Step 2 — still with 24 V OFF, check the LED:**

```bash
python .\01b_led_blink_test.py --port COM3
```

**Step 3 — now switch the 24 V supply ON, and run one small move:**

```bash
python .\02_slow_forward_test.py --port COM3
```

It prints a safety checklist and waits. Type `RUN` exactly (capital letters) and
press Enter. The motor turns slowly for about one second.

**Step 4 — one forward-and-back cycle:**

```bash
python .\03_forward_reverse_test.py --port COM3
```

If all four work, the rig is healthy.

---

## 6. Calibration — teaching the software about millimetres

The software counts *motor steps*. It has no idea how far the needle physically
moves until you measure it. That is what calibration does.

**You need:** a ruler, dial indicator, or depth gauge, and a way to mark the
needle's starting position.

**Command:**

```bash
python .\99_needle_calibration.py --config .\configs\99_needle_calibration.yaml
```

**What happens:**

1. It prints the whole plan and checks it. If anything is wrong it stops here
   and **never opens the COM port**.
2. It prints safety warnings and waits. Type `RUN` exactly, press Enter.
3. For each trial it:
   - moves the needle forward,
   - pauses so you can measure,
   - asks: *"measured needle displacement ... in mm"* — type how far it moved,
     as a positive number, e.g. `2.5`, press Enter,
   - moves back by exactly the same number of steps,
   - asks: *"how far is it from its starting mark"* — type the offset. Type `0`
     if it came back exactly. Type a small negative number if it went past the
     mark. Type `skip` if you cannot measure it.
4. After all trials it prints the result and saves it into `calibration_results\`.
5. It asks whether to make the result official. Type `UPDATE` exactly to write
   `configs\needle_calibration.yaml`. Type anything else to skip — your raw
   measurements are kept either way.

### Changing the calibration trials

Open:

```bash
notepad C:\code\chemyx_pump\arduino\dm542s_hello_world\configs\99_needle_calibration.yaml
```

| To change... | Edit this |
| --- | --- |
| Which angles are tested | the `trial_degrees:` list |
| How many times to repeat the whole list | `repetitions:` |
| How long it waits before asking you to measure | `pause_before_measurement_seconds:` |
| How long it waits after returning | `pause_after_return_seconds:` |
| The largest single trial allowed | `maximum_absolute_degrees:` |
| Whether it asks about the return position | `measure_return_error:` (`true` or `false`) |
| Whether it offers to save the official calibration | `update_authoritative_calibration_file:` |

To test different angles, edit the list. Each line needs `  - ` (two spaces,
a dash, a space):

```yaml
calibration:
  trial_degrees:
    - 45
    - 90
    - 180
```

Running it twice to check repeatability:

```yaml
  repetitions: 2
```

---

## 7. Running a movement sequence

**Command:**

```bash
python .\01_needle_move.py --config .\configs\01_needle_move.yaml
```

It prints a full table of what it is about to do, then waits for you to type
`RUN` exactly. Nothing moves before that.

### 7a. Choosing the units

Open the config:

```bash
notepad C:\code\chemyx_pump\arduino\dm542s_hello_world\configs\01_needle_move.yaml
```

Find this line:

```yaml
  movement_mode: degrees
```

| Set it to | Meaning | Needs calibration? |
| --- | --- | --- |
| `degrees` | Distances are motor rotation in degrees | No |
| `mm` | Distances are millimetres of needle travel | **Yes** — run script 99 first |
| `steps` | Distances are raw motor steps | No |

The rig ships as `degrees` because it has not been calibrated yet. After you run
calibration successfully, change it to `mm`.

### 7b. Changing the movements

Scroll to the bottom of the file, to `moves:`. **The list is the sequence.** Its
length is the number of movements — there is no separate count to keep in step.

```yaml
moves:
  - name: lower_5mm
    direction: forward
    degrees: 90.0
    mm: 5.0
    pause_after_seconds: 2.0
```

Each block is one movement:

| Line | What it does | How to change it |
| --- | --- | --- |
| `- name:` | A label you choose, shown in the table and the log | Any short name. **Must be different for every move.** |
| `direction:` | Which way | Exactly `forward` or `backward`, lower case |
| `degrees:` | Distance when mode is `degrees` | Any positive number |
| `mm:` | Distance when mode is `mm` | Any positive number |
| `steps:` | Distance when mode is `steps` | Any positive whole number |
| `pause_after_seconds:` | Wait after this move | Any number, `0` for none. Leave the line out to use the default. |

**To add a movement:** copy an existing block, including its indentation, paste
it where you want it in the order, and change the name and numbers.

**To remove a movement:** delete its whole block (all its lines).

**To reorder:** cut and paste whole blocks. The order in the file is the order
they run.

Only the line matching your `movement_mode` has to be correct. If the mode is
`degrees`, the `mm:` line is ignored — but keeping both means you can switch
modes later without rewriting everything.

### 7c. Zero-net checking

By default the script **refuses to run unless the movements cancel out exactly**
and the needle ends where it started. This is a safety feature on a rig with no
limit switches.

```yaml
  require_zero_net_steps: true
```

If your sequence does not balance, the script prints exactly how far out it is
and suggests a fix, then stops without opening the port. Nothing moves.

Set it to `false` only if you deliberately want the needle to end somewhere else.

The check happens in whole motor steps, *after* rounding. A sequence can look
balanced in millimetres and still be one step out. That is not a bug — it is the
script telling you the truth about what the motor would actually do.

### 7d. Travel limits

```yaml
software_limits:
  enabled: true
  minimum_steps: -1000
  maximum_steps: 1000
```

This stops a sequence that balances out overall but wanders a long way in the
middle. Widen the numbers if you need more travel; set `enabled: false` to turn
the check off.

**These are measured from wherever the needle is when you start the script.**
The rig has no home switch, so the software cannot know where the needle
actually is. These limits bound the *plan*, not the machine.

---

## 8. Worked example: forward, forward, backward, backward, forward

This is the shipped example. Five movements, every one a different distance,
ending exactly where it started.

```yaml
execution:
  movement_mode: degrees

moves:
  - name: lower_5mm
    direction: forward
    degrees: 90.0
    pause_after_seconds: 2.0

  - name: lower_3mm
    direction: forward
    degrees: 45.0
    pause_after_seconds: 1.0

  - name: raise_8mm
    direction: backward
    degrees: 135.0
    pause_after_seconds: 2.0

  - name: raise_2mm
    direction: backward
    degrees: 45.0
    pause_after_seconds: 1.0

  - name: lower_2mm
    direction: forward
    degrees: 45.0
    pause_after_seconds: 0.0
```

Adds up to: +90 +45 −135 −45 +45 = **0 degrees**. The script confirms this
before it opens the port.

---

## 9. Stopping the machine

### The emergency stop is the 24 V power switch. Use it.

If anything looks, sounds, or smells wrong — switch off the 24 V supply. Do not
go looking for a keyboard shortcut first.

### Ctrl+C (software stop)

Pressing **Ctrl+C** in the PowerShell window while a move is running makes the
script send a stop command to the Arduino on the connection it already has open.
It then tells you whether the Arduino confirmed the stop.

What you will see:

- `STOP acknowledged by firmware` — the pulses stopped. **The motor is still
  powered and still holding.** Nothing was de-energised.
- `THE STOP WAS NOT CONFIRMED` — **switch off the 24 V supply now.**

Ctrl+C at other moments:

| When you press it | What happens |
| --- | --- |
| Before you type `RUN` | Nothing was opened, nothing moved. |
| While it waits for `RUN` | Nothing was opened, nothing moved. |
| During a move | Stop command sent, result reported, script exits. |
| During a pause between moves | No motor is running; the sequence is cancelled cleanly. |
| While typing a calibration measurement | No motor is running; you are told where the needle was left. |

**A software stop is not an emergency stop.** It cannot help if the Arduino
crashes, the USB cable falls out, or the script itself is killed. It does not cut
power to anything.

You do not need a second window to stop the machine — and you could not use one
anyway, because Windows only lets one program hold a COM port at a time.

---

## 10. Where your results are saved

Everything is written to:

```text
C:\code\chemyx_pump\arduino\dm542s_hello_world\calibration_results\
```

| File pattern | What it is |
| --- | --- |
| `needle_calibration_<date>-<time>.yaml` | Full calibration record, including your measurements |
| `needle_calibration_<date>-<time>.csv` | The same measurements, openable in Excel |
| `needle_move_<date>-<time>.yaml` | A record of every movement sequence that reached the hardware |

Timestamps are UTC, so they may not match your wall clock.

To open the folder in Windows Explorer:

```bash
explorer C:\code\chemyx_pump\arduino\dm542s_hello_world\calibration_results
```

The official calibration lives at
`configs\needle_calibration.yaml`, with the previous version kept beside it as
`needle_calibration.yaml.bak` whenever it is overwritten.

---

## 11. When something goes wrong

| Symptom | What to do |
| --- | --- |
| `Access is denied` on the COM port | Close the Arduino Serial Monitor and any other window using the port. Only one program at a time. |
| `Configuration file not found` | Check you ran `cd` into the project directory first, and that you typed the config path exactly. |
| `is not valid YAML` | You used a Tab, or the indentation is wrong. Re-check spacing. Nothing moved. |
| `unknown configuration key` | You misspelled a setting. The message suggests the correct spelling. |
| `already used by move 1` | Two movements share a name. Give each one a different name. |
| `Zero-net validation failed` | Your movements do not cancel out. The message says by how much and suggests a fix. |
| `calibration.calibrated: false` | You set `movement_mode: mm` but have not calibrated. Either run script 99, or use `movement_mode: degrees`. |
| `ERROR unknown command: MOVE ...` | The Arduino has old firmware. Re-upload the `.ino` (section 2b). |
| No response from the Arduino | Check the USB cable carries data, the port is right, and the firmware is uploaded. Run `01_serial_hello.py` first. |
| Motor hums but does not turn | Switch off 24 V. A motor coil pair is probably wired wrong. |
| Motor moves the wrong way | Swap `forward` and `backward` in your config, or swap one coil pair with the power off. Re-run calibration afterwards. |
| Needle is not where the software says | The motor missed steps or something slipped. The software cannot detect this. Return the needle to a known mark by hand and recalibrate. |

---

## 12. Safety — what this rig cannot do

Read this once, properly.

- **There are no limit switches.** Nothing physically stops the needle at the end
  of its travel. The software cannot tell when it has hit something.
- **There is no home switch and no homing.** The software has no idea where the
  needle actually is when you start. "Zero" means "where it was when the script
  started", nothing more.
- **There is no encoder.** Nothing checks that the motor actually turned. If it
  stalls, slips, or misses steps, the software will not notice and will keep
  reporting confident, wrong numbers.
- **There is no physical emergency-stop button wired in.** The 24 V supply switch
  is your emergency stop.
- **`DONE MOVE` means the Arduino finished sending pulses.** It does not mean the
  motor turned.
- **Ending at zero commanded steps does not prove the needle came back.** Only
  measuring it proves that.
- **Software STOP does not cut power.** The driver stays energised and holding.

Before running the needle anywhere near its physical travel limits, or leaving
it running unattended, **limit switches and a homing routine should be fitted.**

Always keep the 24 V supply switch within reach. Switch it off immediately for a
driver fault light, violent vibration, the Arduino disconnecting, parts heating
up, smoke, sparking, or a burning smell.
