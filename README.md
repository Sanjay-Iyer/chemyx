# Chemyx Fusion 4000X — Python Test Repo

A small, self-contained set of Python scripts for driving a **Chemyx Fusion
4000X** syringe pump over a serial (RS232 / USB) connection with
[PySerial](https://pyserial.readthedocs.io/).

It is built so a beginner can verify the link **one step at a time**
(`hello_world/01 → 02 → 03`), then run a full **infuse → withdraw** demo
(`main.py`). Every script also runs in a **mock / dry-run mode** with no
hardware attached, and the whole thing is covered by an automated `pytest`
suite that needs no pump.

> The Fusion 4000X is a **dual-channel** pump (two independent drives). These
> scripts can address either channel — see [Channels](#handling-the-two-channels).

Command syntax follows Chemyx's official
[serial command reference](https://chemyx.com/support/knowledge-base/programming-and-computer-control/serial-commands/)
and is modeled on Chemyx's own
[Python program for the 4000X](https://chemyx.com/resources/knowledge-base/general-syringe-pump-info/computer-control-programs/python-program-for-chemyx-syringe-pumps-fusion-4000-x/).

---

## Contents

```
chemyx-4000x-tests/
├── README.md                  ← you are here
├── requirements.txt           ← pyserial, pytest
├── config.py                  ← EDIT THIS: port, baud, channel, syringe, limits
├── pump.py                    ← reusable Pump class (the serial wrapper)
├── mock_serial.py             ← fake pump for dry-run / tests (no hardware)
├── conftest.py                ← lets pytest import the modules
├── hello_world/
│   ├── 01_open_connection.py  ← open the port, confirm, close
│   ├── 02_send_help.py        ← send `help`, print the reply
│   └── 03_set_parameters.py   ← set units/diameter/rate, verify echo (no movement)
├── tests/
│   └── test_pump.py           ← automated validation against a MOCK serial port
└── main.py                    ← full demo: INFUSE then WITHDRAW (with safety prompt)
```

---

## 1. Hardware checklist & physical connection

You need:

- [ ] A Chemyx **Fusion 4000X** pump with its power supply.
- [ ] **One** of these cables:
  - **RS232 serial:** a **DB9 straight-through** cable (male-to-female, NOT a
    null-modem / crossover cable) from the pump's DB9 port to the PC's serial
    port. If your PC has no serial port, add a **USB-to-serial adapter** (FTDI
    or Prolific based adapters are the most reliable; install the adapter's
    driver so it shows up as a COM port).
  - **USB:** a **USB cable** from the pump's USB port to the PC. The pump
    presents itself as a virtual COM port (it uses a USB-to-serial bridge
    internally), so on the PC it still looks like a normal serial port.
- [ ] A syringe loaded in the drive you intend to use, plus tubing and a
      waste/collection vessel — **before** you ever send a movement command.

Steps:

1. Power the pump on and let it reach its main screen.
2. Connect the cable (DB9 straight-through **or** USB) between pump and PC.
3. If using a USB-to-serial adapter, confirm Windows/macOS installed its driver
   and assigned a COM port / `/dev/tty.*` device.
4. Note the **baud rate** shown in the pump's settings (you'll match it in
   `config.py`).

> **Straight-through vs null-modem:** Chemyx pumps use a straight-through
> pinout. A null-modem (crossover) cable will open the port fine but you'll get
> **no response** — a classic "the port opens but the pump is silent" symptom.

---

## 2. Software setup

### Install Python
Install Python 3.8+ from [python.org](https://www.python.org/downloads/) (tick
"Add Python to PATH" on Windows) or via your package manager.

### Create a virtual environment & install dependencies

**Windows (PowerShell):**
```powershell
cd path\to\chemyx-4000x-tests
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux:**
```bash
cd path/to/chemyx-4000x-tests
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Prefer conda?** It's optional — a plain `venv` (above) is the supported
> path. If you do use conda, create an env with any name you like and install
> the same requirements; the repo makes no assumption about conda or any env
> name:
> ```
> conda create -n chemyx python=3.11
> conda activate chemyx
> pip install -r requirements.txt   # pyserial + pytest
> ```

`requirements.txt` is just:
```
pyserial>=3.5
pytest>=7.0
```

---

## 3. Find your port and match the baud rate

### Find the port name

- **Windows:** open **Device Manager → Ports (COM & LPT)**. The pump (or its
  USB-to-serial adapter) appears as `USB Serial Port (COM3)` or similar. The
  `COMx` number is what goes in `config.py`. Tip: unplug/replug the cable and
  watch which entry disappears/reappears.
- **macOS / Linux:** list serial devices:
  ```bash
  ls /dev/tty.*      # macOS  -> e.g. /dev/tty.usbserial-A50285BI
  ls /dev/ttyUSB*    # Linux  -> e.g. /dev/ttyUSB0
  ```
  On Linux you may need to be in the `dialout` group:
  `sudo usermod -a -G dialout $USER` (then log out/in).

### Match the baud rate

On the pump, open its **System / Settings** screen and read the **baud rate**
(commonly **9600** or **38400**). Whatever it shows **must** equal `BAUD_RATE`
in `config.py`. A mismatch is the #1 cause of "opens but no/garbled response".

Framing is always **8 data bits, no parity, 1 stop bit** (8-N-1), and commands
are terminated with a carriage return `\r` — all handled for you in `pump.py`.

---

## 4. Handling the two channels

The 4000X has **two independent pump drives**. In this repo a channel is just a
number set in `config.py` (`CHANNEL`) or passed per call:

| `CHANNEL` | Meaning              | What goes on the wire        |
|-----------|----------------------|------------------------------|
| `0`       | default / both       | `set rate 5.0`               |
| `1`       | channel 1 only       | `1 set rate 5.0`             |
| `2`       | channel 2 only       | `2 set rate 5.0`             |

- **Single-channel pump (e.g. Fusion 200X):** leave `CHANNEL = 0`.
- **Target one drive of the 4000X:** set `CHANNEL = 1` or `2` in `config.py`,
  or override per command in code:
  ```python
  pump.set_rate(5.0, channel=2)   # just this command goes to channel 2
  pump.select_channel(1)          # change the default for subsequent commands
  ```

---

## 5. Set your port/baud/channel (per machine, NOT committed)

`config.py` is committed with **generic, safe defaults** (placeholder `COM3`,
tiny volumes). Your real port differs from machine to machine, so don't bake it
into `config.py`. Settings resolve in this order — **later wins**:

1. **Committed defaults** in `config.py` (generic).
2. **Environment variables** — quickest for a one-off:
   ```powershell
   # Windows PowerShell
   $env:CHEMYX_PORT="COM5"; $env:CHEMYX_BAUD="9600"; $env:CHEMYX_CHANNEL="0"
   ```
   ```bash
   # macOS / Linux
   export CHEMYX_PORT=/dev/ttyUSB0 CHEMYX_BAUD=9600 CHEMYX_CHANNEL=0
   ```
3. **`config_local.py`** — best for a permanent per-machine setup. Copy the
   template and edit it:
   ```bash
   cp config_local.example.py config_local.py   # Windows: copy ... config_local.py
   ```
   ```python
   # config_local.py
   PORT = "COM5"
   BAUD_RATE = 9600
   CHANNEL = 0
   ```
   `config_local.py` is in `.gitignore`, so it never gets committed and never
   travels between machines.

> A fresh clone needs **no edit to `config.py`** — set an env var or drop in a
> `config_local.py`. The **mock/dry-run path needs none of this**.

The recognised env vars: `CHEMYX_PORT`, `CHEMYX_BAUD`, `CHEMYX_CHANNEL`,
`CHEMYX_DIAMETER`, `CHEMYX_RATE`, `CHEMYX_VOLUME`. `config.py` also holds the
hardware limits the code validates against (diameter `0.103–40.000 mm`, flow
rate up to `170.5 mL/min`) and conservative safety caps — you rarely touch
those.

---

## 6. Run order

Always work up from the simplest test. Add `--mock` to any script to run it with
**no hardware** (uses the built-in pump emulator) — great for a first look or
when the pump isn't on your desk.

```powershell
# 1) Can we even open the port?  (no commands, nothing moves)
python hello_world/01_open_connection.py            # real hardware
python hello_world/01_open_connection.py --mock     # dry run

# 2) Does the pump talk back?  (sends `help`, prints reply — proves baud match)
python hello_world/02_send_help.py
python hello_world/02_send_help.py --mock

# 3) Set units/diameter/rate and verify the pump echoed them (still no movement)
python hello_world/03_set_parameters.py
python hello_world/03_set_parameters.py --mock

# 4) The real demo: INFUSE then WITHDRAW a small volume (asks to confirm!)
python main.py                                      # real hardware, prompts you
python main.py --mock --yes                         # dry run, no prompt
```

`main.py` will not move anything until you type `yes` at its safety prompt
(real-hardware mode). Use small volumes/rates while you're learning — the
defaults in `config.py` are deliberately tiny.

---

## 7. Run the automated tests

The test suite mocks the serial port, so it runs **without a pump** and proves
the command formatting, direction logic, range checks, echo verification and
error handling all behave:

```powershell
pytest                # from the repo root
pytest -v             # verbose: see each test name
```

Expected result: **30 passed**. What the tests cover:

- every command is correctly formatted and terminated with `\r`;
- `infuse()` sends a **positive** volume, `withdraw()` sends a **negative** one
  (then `start`);
- `set_diameter` / `set_rate` reject out-of-range values (and the limits move
  with the units);
- dual-channel addressing adds the right `N ` prefix;
- an **echo mismatch** (out-of-range value the pump didn't accept) is detected
  and raised as `EchoMismatchError`;
- connection failures (**port not found**, **access denied**) surface as a clear
  `PumpConnectionError`;
- the context manager always closes the port, even on error.

> **What genuinely needs real hardware?** Only the actual fluid movement and the
> pump's real timing/echo. Everything else is exercised here behind the mock. On
> real hardware, the same code path runs — the only swap is `serial.Serial`
> instead of the emulator (`Pump(mock=True)` vs `Pump()`).

---

## 8. Using the `Pump` class in your own code

```python
from pump import Pump

# Opens the port on entry, ALWAYS closes it on exit:
with Pump() as pump:                 # reads PORT/BAUD/CHANNEL from config.py
    pump.set_units("mL/min")
    pump.set_diameter(4.5)           # validated + echo-verified
    pump.set_rate(1.0)
    pump.infuse(0.5)                 # positive volume -> dispense, then start
    pump.stop()
    pump.pause()
    pump.withdraw(0.5)               # negative volume -> aspirate, then start
    pump.stop()
```

Key methods: `connect()`, `disconnect()`, `send_command()`, `set_units()`,
`set_diameter()`, `set_rate()`, `set_volume()`, `infuse()`, `withdraw()`,
`start()`, `stop()`, `pause()`, `select_channel()`, plus `__enter__`/`__exit__`.
Every `set_*` reads the pump's echo back and raises `EchoMismatchError` if the
returned value doesn't match what was sent (the firmware does this when a value
is out of range, so the command silently wouldn't have taken).

---

## 9. Troubleshooting

| Symptom | Likely cause & fix |
|---|---|
| **`PumpConnectionError: ... Access denied`** | The port is already open elsewhere. Close the **Chemyx GUI**, any serial terminal (PuTTY, Arduino Serial Monitor, etc.), or another script holding the port. Only one program can own a COM port at a time. |
| **`PumpConnectionError: ... not found`** | Wrong `PORT` in `config.py`, cable unplugged, or driver not installed. Re-check Device Manager / `ls /dev/tty.*`, and confirm the USB-to-serial driver is installed. |
| **Port opens but no/empty response** (`02_send_help` is blank) | **Baud mismatch** — set `BAUD_RATE` in `config.py` to exactly what the pump screen shows. Also check you're using a **straight-through** DB9 cable, not a null-modem one. |
| **`EchoMismatchError: ... value likely out of range`** | The pump echoed a different value than you sent, meaning it rejected/clamped it. Pick a value inside the limits (diameter `0.103–40 mm`; rate within range for the current units). |
| **`ValueError: ... out of range`** | The Python side rejected the value **before** sending (same limits as above). Adjust the number. |
| **Pump doesn't move on `start`** | Make sure you set a non-zero `volume` and `rate` first, the right **channel** is targeted, and (real hardware) the drive isn't paused or at a limit switch. |
| **Garbled characters in the reply** | Baud mismatch or a flaky USB-to-serial adapter — try a known-good FTDI adapter and re-check the baud rate. |
| **Works in `--mock` but not on hardware** | The mock proves your code/logic is fine; a hardware-only failure points at cable, port, baud, channel, or the pump being in a state that blocks the command. |

---

## Status

- ✅ All scripts created (`config.py`, `pump.py`, `mock_serial.py`, the three
  `hello_world/` scripts, `main.py`, `tests/test_pump.py`).
- ✅ Test suite passes — **30 passed** under `pytest`.
- ✅ `hello_world/01–03` and `main.py` validated in mock/dry-run mode.
- ✅ This guide covers hardware, setup, ports/baud, channels, config, run order,
  testing and troubleshooting.
