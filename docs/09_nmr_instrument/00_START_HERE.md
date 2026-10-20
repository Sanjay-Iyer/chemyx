# 09 — NMR instrument

[All documentation](../00_START_HERE.md)

Read [01 — Standalone tests](01_standalone_tests.md) for the commands. Run
them in order: connection test, then one 1-scan run, then one 4-scan run. Each
explains any failure and saves a debug report.

| Item | File |
|---|---|
| 1. Connection test (read-only) | [scripts/nmr_standalone/01_test_connection.py](../../scripts/nmr_standalone/01_test_connection.py) |
| 2. One 1-scan acquisition | [scripts/nmr_standalone/02_run_1_scan.py](../../scripts/nmr_standalone/02_run_1_scan.py) |
| 3. One 4-scan acquisition | [scripts/nmr_standalone/03_run_4_scans.py](../../scripts/nmr_standalone/03_run_4_scans.py) |
| Shared implementation | [chemyx_lab/workflows/nmr_standalone.py](../../chemyx_lab/workflows/nmr_standalone.py) |
| NMR address (IP, port, timeouts) | [configs/machines/si6_real_COM4.yaml](../../configs/machines/si6_real_COM4.yaml), `nmr:` section |
| Acquisition settings (gain, centre, width) | [configs/experiments/si6_stage2only_6h.yaml](../../configs/experiments/si6_stage2only_6h.yaml), `nmr:` section, shared with the 06/08 runs |
| Debug reports and raw FIDs | `results/nmr_standalone/` |

First command, from the repository folder:

```powershell
python scripts\nmr_standalone\01_test_connection.py
```

Tests 02/03 use the simplified acquisition of `10_stage2only` and
`11_3InstrumentCheck` by default, which skips the status check that stops the
[06](../03_fixed_time_experiment/06_stage2only_run_guide.md) and
[08](../03_fixed_time_experiment/08_stage2only_scipy_run_guide.md) runs with
`NMR idle not confirmed`. Add `--experiment-worker` to run the 06/08 method
instead and compare. Saved-data processing is in
[05 — NMR analysis](../05_nmr_analysis/00_START_HERE.md); the RPC API manual
is in the [vendor reference](../08_reference/vendor/nmr_rpc_api/README.md).
