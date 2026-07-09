# Chemyx Pump + NMR Workflow

Portable Python control scripts for a Chemyx Fusion 4000X syringe pump, plus a
first-pass NMR intake workflow for monitoring the small signal near 6.1 ppm.

The repo is organized so laptop-specific settings stay out of git. The work
laptop can keep its own `configs/chemyx.local.json`,
`configs/nmr.local.json`, legacy `config_local.py`, or environment variables,
while the same committed scripts run from any clone.

## Current Structure

```text
chemyx_lab/              Shared Python package
  config.py              Laptop-agnostic settings and env var handling
  pump.py                Chemyx serial wrapper
  valve.py               IDEX MX Series II valve driver (2-position MXX777-601)
  mock_serial.py         Fake pump + fake MX II valve board for dry runs/tests
  nmr.py                 JCAMP-DX parser and 6.1 ppm peak check
  nmr_outputs.py         CSV/plot/manifest output helpers
  workflow.py            First-pass SOP workflow steps
scripts/                 Clean commands to run from the repo root
docs/                    Setup and instrument guides
deploy/infuse_withdraw.py
                         Original work-laptop script that proved the pump link
NMR/                     SOP notes and example NMR .dx files
tests/                   Automated tests, no hardware required
```

Legacy example folders were moved into the local ignored `archive/` folder.
New work should use the `chemyx_lab/` package and `scripts/` commands.

## Quick Start

From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Dry-run the pump path without hardware:

```powershell
python scripts\pump_infuse_withdraw.py --mock
python scripts\sop_mock_workflow.py --cycles 1
```

On the work laptop, find the pump port:

```powershell
python scripts\list_ports.py
```

Create an untracked local config:

```powershell
copy configs\chemyx.local.example.json configs\chemyx.local.json
```

Edit `configs\chemyx.local.json` for that laptop. Your successful work-laptop
test used:

```json
{
  "port": "COM4",
  "baud_rate": 115200,
  "channel": 1,
  "units": "mL/min",
  "diameter": 28.6,
  "rate": 2.0,
  "volume": 1.5,
  "timeout": 2.0,
  "response_delay": 0.2
}
```

Then run the hardware smoke test:

```powershell
python scripts\pump_infuse_withdraw.py
```

For a noninteractive checked run:

```powershell
python scripts\pump_infuse_withdraw.py --yes
```

## MX Series II Valve (MXX777-601)

The switching valve is an IDEX MX Series II with a 2-POSITION, 6-port
MXX777-601: only positions 1 and 2 exist, and the board silently ignores
commands to any other position, so the driver validates before sending.

Dry-run without hardware:

```powershell
python scripts\test_valve.py --mock
python scripts\test_valve.py --mock --mock-level-logic
```

On the laptop with the valve (auto-detects the FTDI COM port if unset):

```powershell
$env:MXVALVE_PORT="COM7"     # or copy configs\valve.local.example.json
python scripts\test_valve.py
```

If home works but position moves are ignored, the board's command mode is
probably not BCD: run `python scripts\test_valve.py --set-bcd`, power-cycle
the 24 V supply, and test again. Details: [MX II valve guide](docs/valve_mx2_guide.md).

## NMR Intake

Analyze the example `.dx` files near 6.1 ppm:

```powershell
python scripts\analyze_nmr_dx.py NMR\06-08-26 --target 6.1
```

Each analysis run saves:

```text
runs/nmr_analysis/<timestamp>/results.csv
runs/nmr_analysis/<timestamp>/manifest.json
runs/nmr_analysis/<timestamp>/plots/*.png
```

Run the SOP-shaped mock workflow and ingest the newest `.dx` file in a folder
at each NMR step:

```powershell
python scripts\sop_mock_workflow.py --cycles 1 --data-dir NMR\06-08-26
```

Check the NMReady/Nanalysis RPC API on the instrument laptop:

```powershell
copy configs\nmr.local.example.json configs\nmr.local.json
python scripts\nmr_rpc_status.py
python scripts\nmr_run_1d.py --dry-run --mock-settings
python scripts\nmr_run_1d.py --save-dx runs\nmr\test_1d.dx
```

The committed NMR starting point is the archived working direct-ethernet
address `169.254.30.54` on RPC port `5000`, with iFlow scans set to `2` and
receiver gain set to `12.0`. Edit `configs\nmr.local.json` on the instrument
laptop when the IP, scan count, receiver gain, solvent, or timing changes.

## Guides

- [Laptop setup](docs/setup_work_laptop.md)
- [Chemyx 4000X guide](docs/chemyx_4000x_guide.md)
- [MX II valve guide](docs/valve_mx2_guide.md)
- [NMR guide](docs/nmr_guide.md)
- [NMR RPC API notes](docs/nmr_rpc_api.md)
- [SOP workflow plan](docs/sop_workflow.md)

## Tests

```powershell
pytest
```

The test suite uses mocks and does not require the pump or NMR instrument.
