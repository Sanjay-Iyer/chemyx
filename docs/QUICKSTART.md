# Quickstart

Run all Python commands through the Conda environment named `ai`.

## Home Laptop

Validate config without hardware:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --validate-only
```

Print the workflow plan without hardware:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --dry-run
```

Run offline tests:

```powershell
conda run -n ai python -m pytest
```

## Work Laptop

Create local machine config:

```powershell
copy configs\machines\00_machine.example.yaml configs\machines\00_machine.local.yaml
```

Edit `configs\machines\00_machine.local.yaml` for the Chemyx serial port and
NMR host.

Dry-run:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --dry-run --machine-config configs\machines\00_machine.local.yaml
```

Real run:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --machine-config configs\machines\00_machine.local.yaml
```
