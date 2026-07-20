# MX II Valve — Step-by-Step Bring-Up Checklist

Run Part A on the home laptop before pushing (pure software, no hardware).
Run Part B on the work laptop with the real valve after pulling.

All commands are PowerShell, run **from the repo root**. After each step,
`echo $LASTEXITCODE` should print `0` unless the step says otherwise.

Python environment: any env with `pyserial` (and `pytest` for the test
steps) works — the repo assumes nothing. Baseline setup:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

(Existing conda envs are fine too: `ai` on the home laptop, `llm` on the
work laptop.) Sanity-check the interpreter first:

```powershell
python -c "import sys, serial; print(sys.version.split()[0], 'pyserial', serial.__version__)"
```

---

## Part A — Software checkout (home laptop, no hardware)

### A1. Valve unit tests

```powershell
pytest tests\test_valve.py -q
```

Expected: `24 passed`.

### A2. Whole test suite

```powershell
pytest -q
```

Expected: `1 failed, 68 passed`. The single failure is the **pre-existing,
valve-unrelated** `tests/test_nmr_rpc.py::test_build_1d_experiment_settings_patches_nested_values`
(NMR receiver-gain shape, tracked separately). Any OTHER failure is new —
stop and investigate.

### A3. Mock happy path

```powershell
python scripts\test_valve.py --mock
```

Expected: exit 0, every step `PASS`, final line:

```text
All steps passed: the valve toggled 1 -> 2 -> 1 -> 2 with confirmed readbacks.
```

### A4. Failure rehearsal (what a wrong command mode looks like)

```powershell
python scripts\test_valve.py --mock --mock-level-logic --motion-timeout 2
```

Expected: **exit 1** (that is the point). Step 2 warns the mode is
`0x01 (level logic)`; home passes; moves to position 2 fail. Memorize the
wire signature so you recognize it on real hardware:

```text
TX raw=b'P02\r' ...
RX raw=b'' hex=(empty) (no response)      <- board silently ignored the move
```

### A5. Recovery rehearsal (--set-bcd flow)

```powershell
python scripts\test_valve.py --mock --mock-level-logic --set-bcd
```

Expected: exit 0. It stores BCD (`TX raw=b'F03\r'`), simulates the power
cycle, and then all toggles pass. On real hardware the power cycle is
manual (step B7).

### A6. Position-validation guards

```powershell
python scripts\serial_hello_test.py --position 5
python scripts\titan_valve_control.py --command P05 --dry-run
```

Expected: both **refuse with exit 2** and say `valve has only 2 positions,
got 5` — nothing is sent, no port is opened.

```powershell
python scripts\titan_valve_control.py --command P02 --dry-run
```

Expected: exit 0, prints the exact frame `50 30 32 0d` and
`Dry run: serial port was not opened and no bytes were sent.`

### A7. Fresh-clone rehearsal (optional, catches "works only on my machine")

```powershell
git clone C:\code\chemyx_pump $env:TEMP\valve_clone_test
cd $env:TEMP\valve_clone_test
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --quiet pyserial pytest
.\.venv\Scripts\python.exe -m pytest tests\test_valve.py -q
.\.venv\Scripts\python.exe scripts\test_valve.py --mock
cd C:\code\chemyx_pump
Remove-Item -Recurse -Force $env:TEMP\valve_clone_test
```

Expected: `24 passed` and the A3 output — from a clone with only pyserial +
pytest installed. This is exactly what the work laptop will experience.

### A8. Push

```powershell
git status        # should be clean
git push
```

---

## Part B — Real hardware (work laptop)

### B1. Pull and check the environment

```powershell
git pull
python -c "import sys, serial; print(sys.version.split()[0], 'pyserial', serial.__version__)"
```

### B2. Prove the software on THIS machine before touching hardware

```powershell
pytest tests\test_valve.py -q          # expect: 24 passed
python scripts\test_valve.py --mock    # expect: All steps passed ...
```

If these fail here, the problem is the pull/environment, not the valve.

### B3. Free the COM port

Close IDEX/Rheodyne software, PuTTY, TeraTerm, serial monitors, and any
stuck Python. Only one program can hold the port.

### B4. Find the valve's COM port

Power the board (24 V — never more), connect USB-B, then:

```powershell
python scripts\test_valve.py --list-only
```

The valve is the line with `vid:pid=0403:6001` (FTDI FT232R). If the pump
is also FTDI, unplug the valve USB, list, replug, list again — the port
that appears is the valve.

### B5. Configure the port for this machine (never in committed code)

```powershell
$env:MXVALVE_PORT="COM7"     # this terminal only
```

or persistently for this clone:

```powershell
copy configs\valve.local.example.json configs\valve.local.json
# then edit the "port" value; the file is gitignored
```

(With exactly one FTDI device plugged in you can skip this — the script
auto-detects.)

### B6. Read-only identity check first (nothing moves)

```powershell
python scripts\serial_hello_test.py --port COM7 --identify
```

Expected: `Controller responded at 19200 baud.`, a status line, firmware,
valve profile, **command mode**, and last error. Note the command mode:

```text
Response: b'03\r' - command mode 0x03 (BCD logic)      <- good, go to B8
Response: b'01\r' - command mode 0x01 (level logic)    <- do B7 first
```

If nothing responds at any baud: check cable, 24 V power, FTDI driver, B3.

### B7. Only if the mode was not BCD: set it, then power-cycle

```powershell
python scripts\test_valve.py --set-bcd
```

It stores BCD (`F03`) and stops. Then **unplug the 24 V barrel jack, wait
a few seconds, plug it back in** (USB alone does not reset the board).
Re-run B6 and confirm `command mode 0x03 (BCD logic)`.

### B8. The full motion test

```powershell
python scripts\test_valve.py
echo $LASTEXITCODE
```

Watch/listen to the valve: home moves, then four position moves. Expected:
exit 0, every step `PASS`, ending with

```text
All steps passed: the valve toggled 1 -> 2 -> 1 -> 2 with confirmed readbacks.
```

Every command/response is printed raw + hex + decoded, so if a step fails
the offending exchange is right there in the transcript.

### B9. If something still fails

| Symptom in the transcript | Likely cause / next command |
|---|---|
| `RX raw=b''` after `TX raw=b'P02\r'`, move times out | Mode still not BCD (redo B7, confirm the power cycle) or wrong valve profile |
| Status returns `4D` | Configuration/command-mode error: redo B7 |
| `Access denied opening COMx` | Port held by another program (B3) |
| Board answers only `*` forever | Motor stuck/jammed; power-cycle; check `--read last-error` |
| Home also stops working | Wiring/power regression — home worked before, recheck 24 V and cable |

Deeper read-only queries:

```powershell
python scripts\serial_hello_test.py --port COM7 --read command-mode
python scripts\serial_hello_test.py --port COM7 --read valve-profile
python scripts\serial_hello_test.py --port COM7 --read last-error
```

Protocol reference and error-code table: `docs/valve_mx2_guide.md`.

### B10. Done

Exit 0 from B8 means: transport proven, positions validated, both physical
positions reached and confirmed by readback. The valve is ready to be used
from Python:

```python
from chemyx_lab.valve import MX_valve, find_address

with MX_valve(find_address()) as valve:   # ports=2 is the default
    valve.change_port(2)
    valve.change_port(1)
```
