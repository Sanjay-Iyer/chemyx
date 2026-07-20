# Validation

## Home-Laptop Offline Validation

Validated on 2026-07-20 with Python 3.11.15 from Conda environment `ai`.
No serial port was opened, no pump moved, and no NMR RPC request was made.

```powershell
conda run -n ai python -B -m pytest -p no:cacheprovider --basetemp .test-tmp-final
```

Result: 80 tests passed in 20.56 seconds. The temporary test directory was
removed after the run.

The following entry-point checks all exited with status 0:

```powershell
conda run -n ai python -B scripts\01_first_real_chemyx_nmr.py --help
conda run -n ai python -B scripts\01_first_real_chemyx_nmr.py --validate-only
conda run -n ai python -B scripts\01_first_real_chemyx_nmr.py --dry-run
conda run -n ai python -B scripts\diagnostics\04_run_nmr_1d_acquisition.py --dry-run
conda run -n ai python -B scripts\diagnostics\06_check_mx_valve.py --mock
conda run -n ai python -B scripts\diagnostics\07_analyze_nmr_results.py --help
```

`--validate-only` reported that configuration loaded without opening hardware.
Workflow `--dry-run` reported that no movement or acquisition started. The NMR
diagnostic printed payloads from bundled example settings without an RPC fetch,
and the valve diagnostic used its in-memory mock.

Repository checks also passed:

```powershell
git diff --check
```

The active tree has no generated `__pycache__` directory. Concrete legacy COM
ports, IP addresses, and laptop paths occur only in historical disposition,
archive, reference, audit, or preserved-result material.

## Work-Laptop Hardware Qualification

Hardware validation remains intentionally pending. On the work laptop, create
`configs/machines/00_machine.local.yaml` from the committed example and enter
the actual Chemyx, NMR, and optional valve endpoints. Then run these commands in
order, stopping at the first unexpected response:

```powershell
conda run -n ai python -B scripts\01_first_real_chemyx_nmr.py --validate-only
conda run -n ai python -B scripts\01_first_real_chemyx_nmr.py --dry-run
conda run -n ai python -B scripts\diagnostics\01_list_serial_ports.py
conda run -n ai python -B scripts\diagnostics\02_verify_chemyx_movement.py --mock
conda run -n ai python -B scripts\diagnostics\03_check_nmr_connection.py
conda run -n ai python -B scripts\diagnostics\04_run_nmr_1d_acquisition.py --dry-run
conda run -n ai python -B scripts\diagnostics\06_check_mx_valve.py --mock
```

After checking tubing, syringe limits, valve state, sample placement, endpoint
values, printed direction, rate, volume, and result path, qualify each real
instrument separately using the corresponding diagnostic without its mock or
dry-run flag. Run the integrated workflow last:

```powershell
conda run -n ai python -B scripts\01_first_real_chemyx_nmr.py
```

The real run must retain its interactive confirmation. Confirm the resulting DX
file under `results/raw/nmr/generated/` before treating the restructuring as
hardware-qualified.
