# Laptop Setup Guide

Use this when pulling the repo onto a new laptop or returning to the work
laptop after pushing changes from home.

## 1. Get The Repo Ready

From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the no-hardware checks:

```powershell
python scripts\pump_infuse_withdraw.py --mock
python scripts\sop_mock_workflow.py --cycles 1
python scripts\nmr_run_1d.py --dry-run --mock-settings
pytest
```

## 2. Keep Local Settings Out Of Git

Do not edit committed code just to change ports. Use one of these.

Option A, one terminal session:

```powershell
$env:CHEMYX_PORT="COM4"
$env:CHEMYX_BAUD="115200"
$env:CHEMYX_CHANNEL="1"
```

Option B, persistent on that clone:

```powershell
copy configs\chemyx.local.example.json configs\chemyx.local.json
```

Then edit `configs\chemyx.local.json`. It is gitignored.

The successful work-laptop script used:

```json
{
  "port": "COM4",
  "baud_rate": 115200,
  "channel": 1
}
```

Also set the syringe diameter and default test rate for the physical setup:

```json
{
  "diameter": 28.6,
  "rate": 2.0,
  "volume": 1.5
}
```

For NMR, copy the local JSON template:

```powershell
copy configs\nmr.local.example.json configs\nmr.local.json
```

Then edit `configs\nmr.local.json`. It is also gitignored. The current
starting point from the archived working NMR code is:

```json
{
  "host": "169.254.30.54",
  "port": 5000,
  "route": "iflow",
  "scans": 2,
  "receiver_gain": 12.0,
  "auto_gain": false
}
```

## 3. Find The Chemyx Port

With the pump powered on and plugged in:

```powershell
python scripts\list_ports.py
```

Windows alternatives:

```powershell
python -m serial.tools.list_ports -v
Get-PnpDevice -Class Ports
```

Device Manager path:

```text
Device Manager -> Ports (COM & LPT)
```

Unplug/replug the pump or USB-to-serial adapter and watch which COM port
appears. That is the value for `CHEMYX_PORT` or `port` in
`configs\chemyx.local.json`.

## 4. Run Order On Hardware

Start with communication only:

```powershell
python scripts\list_ports.py
python scripts\pump_infuse_withdraw.py --mock
```

Then run the new smoke test:

```powershell
python scripts\pump_infuse_withdraw.py
```

Check NMR RPC before adding it to the pump workflow:

```powershell
python scripts\nmr_rpc_status.py
python scripts\nmr_run_1d.py --dry-run --mock-settings
python scripts\nmr_run_1d.py --save-dx runs\nmr\test_1d.dx
```

For the SOP-shaped bench test, start tiny:

```powershell
python scripts\sop_mock_workflow.py --real --volume-scale 0.05
```

Increase `--volume-scale` only after the tubing, waste path, needle position,
and reaction vessel geometry are physically verified.
