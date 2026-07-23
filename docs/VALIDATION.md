# Validation

## Home-laptop offline validation

Validated on 2026-07-22 with Python 3.11.15 from the Conda environment `ai`.
These checks did not enumerate serial ports, open instrument connections, move
the pump, contact the NMR RPC service, or send commands to the valve.

The active test suite was run with an isolated temporary directory:

```powershell
conda run -n ai python -B -m pytest -p no:cacheprovider --basetemp C:\code\chemyx_pump\test_tmp_monitoring_cleanup
```

Result: 194 tests passed in 21.07 seconds. This includes monitoring-mode,
fixed-schedule, journal replay, failure-policy, configuration-boundary, and
legacy-workflow isolation coverage. Tests under the Git-tracked legacy archive
are intentionally outside the active `testpaths` configuration.

The canonical Workflow 02 entry point and offline diagnostics were also checked:

```powershell
conda run -n ai python -B scripts\02_si6_automated_nmr.py --validate-only
conda run -n ai python -B scripts\02_si6_automated_nmr.py --dry-run
conda run -n ai python -B scripts\diagnostics\04_run_nmr_1d_acquisition.py --dry-run
conda run -n ai python -B scripts\diagnostics\06_check_mx_valve.py --mock
```

`--validate-only` loads and validates configuration without initializing
hardware. Workflow `--dry-run` writes an isolated timestamped run record while
using simulated operations. The NMR diagnostic prints the request without an
RPC fetch, and the valve diagnostic uses its in-memory mock.

Repository-boundary tests verify that Workflow 01 is absent from active entry
points, active modules do not import its archive, and the retired W/N/I event
schema is rejected by the active Si6 configuration loader. The archived source
is preserved under `archive/legacy_workflows/01_first_real_chemyx_nmr/` for
history only and must not be used for active execution.

## Work-laptop hardware validation

Hardware validation was not performed during this implementation phase. Passing
home-laptop checks does not establish instrument compatibility or physical
safety.

On the instrument-connected work laptop, the intended staged qualification is:

1. Pull and review the two implementation commits.
2. Rerun the full offline suite and the four safe commands above.
3. Review the local machine configuration, tubing, syringe limits, valve state,
   sample placement, endpoint values, direction, rate, volume, and output path.
4. Only with explicit authorization, qualify each instrument independently with
   cautious diagnostic actions.
5. Review the generated journal and artifacts before separately authorizing a
   short, attended integrated trial.
6. Run the complete Si6 workflow only after the staged checks are accepted.

Real hardware initialization remains restricted to an explicit real-run or
explicitly authorized hardware-diagnostic path. Import, inspection, validation,
dry-run, and test commands must remain hardware-free.
