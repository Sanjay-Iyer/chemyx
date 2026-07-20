# Workflow 01: First Real Chemyx + NMR Test

Workflow 01 preserves the proven bench sequence identified in Phase 1:

```text
Withdraw sample -> acquire NMR -> infuse sample back
```

User-facing script:

```text
scripts/01_first_real_chemyx_nmr.py
```

Matching experiment config:

```text
configs/experiments/01_first_real_chemyx_nmr.yaml
```

## Home Laptop Commands

Validate configuration without contacting instruments:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --validate-only
```

Print the dry-run plan without contacting instruments:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --dry-run
```

Show CLI options:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --help
```

## Work Laptop Commands

Create local machine config:

```powershell
copy configs\machines\00_machine.example.yaml configs\machines\00_machine.local.yaml
```

Edit `configs\machines\00_machine.local.yaml` for the work-laptop Chemyx serial
port and NMR host.

Dry-run with the work-laptop local machine config:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --dry-run --machine-config configs\machines\00_machine.local.yaml
```

Real hardware run, only after the dry-run plan matches the physical setup:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --machine-config configs\machines\00_machine.local.yaml
```

The real run prints a confirmation prompt before opening the pump unless
`--yes` is supplied.
