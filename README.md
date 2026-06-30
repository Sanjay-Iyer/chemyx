# Chemyx Pump + NMR Workflow

Portable Python control scripts for a Chemyx Fusion 4000X syringe pump, plus a
first-pass NMR intake workflow for monitoring the small signal near 6.1 ppm.

The repo is organized so laptop-specific settings stay out of git. The work
laptop can keep its own `config_local.py`, `configs/nmr.local.json`, or
environment variables, while the same committed scripts run from any clone.

## Current Structure

```text
chemyx_lab/              Shared Python package
  config.py              Laptop-agnostic settings and env var handling
  pump.py                Chemyx serial wrapper
  mock_serial.py         Fake pump for dry runs and tests
  nmr.py                 JCAMP-DX parser and 6.1 ppm peak check
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
copy config_local.example.py config_local.py
```

Edit `config_local.py` for that laptop. Your successful work-laptop test used:

```python
PORT = "COM4"
BAUD_RATE = 115200
CHANNEL = 1
```

Then run the hardware smoke test:

```powershell
python scripts\pump_infuse_withdraw.py
```

For a noninteractive checked run:

```powershell
python scripts\pump_infuse_withdraw.py --yes
```

## NMR Intake

Analyze the example `.dx` files near 6.1 ppm:

```powershell
python scripts\analyze_nmr_dx.py NMR\06-08-26 --target 6.1
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
- [NMR guide](docs/nmr_guide.md)
- [NMR RPC API notes](docs/nmr_rpc_api.md)
- [SOP workflow plan](docs/sop_workflow.md)

## Tests

```powershell
pytest
```

The test suite uses mocks and does not require the pump or NMR instrument.
