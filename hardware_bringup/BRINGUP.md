# Hardware bring-up — cold pump to confirmed control (WORK LAPTOP)

A step-by-step path to go from a **fresh `git pull` on the work laptop** to
**confirmed, verified control** of the real Chemyx Fusion 4000X — safely.

> This repo was developed/mock-tested on a *different* machine. **None of its
> machine-specific settings travel with it**: the COM port, baud, and any conda
> env are all set per-machine. Expect the port here to differ. That's normal —
> these steps find and set it.

Everything outside `hardware_bringup/` is mock-validated and never touches
hardware. The three scripts here are the only ones that talk to the real pump,
and they go from *safest* (`list_ports` → `test_connection`) to *first motion*
(`first_light`).

---

## 0. One-time: get the repo + a Python environment

```bash
# 1. Clone (or pull) the repo onto the work laptop
git clone <your-repo-url> chemyx-4000x-tests
cd chemyx-4000x-tests
#   (already cloned? just:  git pull)

# 2. Create an isolated virtual environment (NO conda required)
python -m venv .venv

# 3. Activate it
#    Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
#    macOS / Linux:
source .venv/bin/activate

# 4. Install dependencies (pyserial + pytest)
pip install -r requirements.txt
```

> Conda is optional. If you prefer it: `conda create -n chemyx python=3.11 && conda activate chemyx && pip install -r requirements.txt`. The repo assumes **no** specific env name.

### Sanity check with ZERO hardware (do this first, every time)

Prove the code itself is healthy before plugging anything in:

```bash
pytest                                         # expect: 30 passed
python hardware_bringup/list_ports.py --mock   # exits 0
python hardware_bringup/test_connection.py --mock
python hardware_bringup/first_light.py --mock  # full sequence, nothing moves
```

If all of those pass, the clone is good and any later problem is hardware/port,
not the code.

---

## 1. Physically connect the pump

1. Plug the **USB-B → USB-A** cable from the pump into the laptop (or use an
   RS232 **straight-through** DB9 cable, optionally via a USB-to-serial adapter).
2. Power the pump **on** and let it reach its main screen.
3. **Windows:** if no COM port appears in the next step, install the Chemyx
   USB driver (or the FTDI/Prolific driver for your adapter), then re-plug.

---

## 2. Find the pump's port (it WILL differ from other machines)

```bash
python hardware_bringup/list_ports.py
```

- Look for an **FTDI / "USB Serial"** entry — that's almost always the Chemyx.
- Note its port name: `COM5` (Windows) or `/dev/tty.usbserial-XXXX` (Mac) or
  `/dev/ttyUSB0` (Linux).
- **No ports listed?** Pump is off, cable unplugged, or the USB-to-serial driver
  isn't installed. Fix and re-run.

---

## 3. Set the port / baud / channel for THIS machine (not committed)

Pick **one** method (these override the generic defaults in `config.py`):

**A. Environment variables** (quick, per session):
```powershell
# Windows PowerShell
$env:CHEMYX_PORT="COM5"; $env:CHEMYX_BAUD="9600"; $env:CHEMYX_CHANNEL="0"
```
```bash
# macOS / Linux
export CHEMYX_PORT=/dev/ttyUSB0 CHEMYX_BAUD=9600 CHEMYX_CHANNEL=0
```

**B. A local config file** (persists; recommended for a fixed rig):
```bash
cp config_local.example.py config_local.py     # Windows: copy config_local.example.py config_local.py
# then edit config_local.py: PORT / BAUD_RATE / CHANNEL
```

`config_local.py` is **gitignored** — it stays on this laptop and is never
committed or pushed. A fresh clone never carries another machine's port.

> **Channel (dual-channel 4000X):** `CHANNEL=0` = default/both, `1` = drive 1,
> `2` = drive 2. Set it to whichever drive your syringe is in, or `0`.

---

## 4. Match the baud rate

On the pump's **System / Settings** screen, read the **baud rate** (commonly
9600 or 38400). It must equal `CHEMYX_BAUD` / `BAUD_RATE`. A mismatch makes the
port open but return nothing or garbage.

---

## 5. Free the port

Make sure **no other program is holding the COM port** — close the **Chemyx
GUI**, PuTTY, TeraTerm, Arduino Serial Monitor, or any other serial tool. Only
one program can own a serial port at a time.

---

## 6. Test the link (no movement)

```bash
python hardware_bringup/test_connection.py
```

Expect a `help` reply listing the pump's commands. That confirms the port, baud,
cable, and driver are all correct.

- **`not found`** → wrong port or missing driver. Re-run `list_ports.py`, fix
  `CHEMYX_PORT`.
- **`access denied`** → another program holds the port (see step 5).
- **opens but blank/garbled** → baud mismatch (step 4) or a null-modem cable.

**Do not proceed to first light until this gives a clean `help` reply.**

---

## 7. First light — first real movement (SYRINGE REMOVED)

> **Remove the syringe. Run the pump EMPTY and DRY, with nothing connected
> downstream.** First light only confirms the motor turns the right way.

```bash
python hardware_bringup/first_light.py
```

- It prints a loud safety header and asks you to **type the word `MOVE`**
  (exactly) to authorise motion — pressing Enter alone won't do it.
- It then runs a tiny **infuse → stop → pause → withdraw → stop** using the
  small `FIRST_LIGHT_VOLUME` / `FIRST_LIGHT_RATE` from `config.py`, **verifying
  the pump's echo at every step** and **aborting on any mismatch**.
- On success you have **confirmed, verified control**. After that, `main.py`
  (repo root) runs the same infuse/withdraw demo, and you can build from there.

---

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| **`list_ports.py` shows no ports** | Pump off / cable unplugged / USB-to-serial driver not installed. Power on, re-plug, install the Chemyx (or FTDI/Prolific) driver, try another USB port/cable. |
| **`test_connection.py`: "port not found"** | `CHEMYX_PORT` is wrong for this machine, or the driver isn't installed. Re-run `list_ports.py` and set the right port. |
| **"access denied" / "port in use"** | Another program owns the port — close the **Chemyx GUI**, PuTTY, TeraTerm, any serial monitor, then retry. |
| **Port opens but NO reply** | Baud mismatch — set `CHEMYX_BAUD` to exactly the pump-screen value. Confirm a **straight-through** (not null-modem) cable if using DB9. |
| **Garbled / random characters** | Baud mismatch or a flaky USB-to-serial adapter. Re-check baud; try a known-good FTDI adapter. |
| **`first_light.py` aborts: "echo mismatch"** | The pump echoed a different value than sent → that value was out of range and didn't take. Check `DIAMETER` (0.103–40 mm) and rate are valid for the units; re-run. |
| **"Works on my personal laptop but not the work laptop"** | This is expected to differ in exactly three places, none of which are committed: **(1) the COM port** (set `CHEMYX_PORT` / `config_local.py` — it differs per machine), **(2) the Python env** (create a fresh `.venv` here; don't assume conda or an env name), **(3) the baud** (match this pump's screen). The code paths are identical; only these per-machine settings change. Run the `--mock` sanity checks (step 0) — if those pass, it's a port/baud/driver issue, not the code. |
| **`pytest` fails on a fresh clone** | You're likely not in the venv or `pip install -r requirements.txt` didn't run. Activate `.venv` and reinstall. Tests need **no hardware**. |

---

## Why this is portable

- **No absolute paths in code** — scripts locate the repo via `pathlib` +
  `__file__`, so they run from any clone location.
- **No conda assumption** — a plain `python -m venv` is the supported path.
- **No committed machine port** — `config.py` ships generic, safe defaults;
  the real port lives in an env var or the gitignored `config_local.py`.
- **Mock path needs zero setup** — every script runs with `--mock` to validate
  the whole flow before any hardware is involved.
