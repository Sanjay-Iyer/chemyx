# NMR RPC Reference

The retained vendor/API reference is under `html/`. The active client is
`chemyx_lab/instruments/nmr.py`.

Configure the NMR endpoint in
`configs/machines/00_machine.local.yaml`. Acquisition settings for workflow 01
remain in `configs/experiments/01_first_real_chemyx_nmr.yaml`.

Offline payload preview:

```powershell
conda run -n ai python scripts\diagnostics\04_run_nmr_1d_acquisition.py --dry-run
```

The dry-run uses documented local example settings and performs no RPC request.

Work-laptop checks:

```powershell
conda run -n ai python scripts\diagnostics\03_check_nmr_connection.py --machine-config configs\machines\00_machine.local.yaml
conda run -n ai python scripts\diagnostics\04_run_nmr_1d_acquisition.py --machine-config configs\machines\00_machine.local.yaml --save-dx results\raw\nmr\generated\diagnostic_1d.dx
```

Workflow 01 uses the iFlow settings, run, and status routes documented in the
reference and preserves their existing request order.
