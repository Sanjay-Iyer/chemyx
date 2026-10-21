# 14 — Full Stage 1 and Stage 2 with simplified NMR acquisition

[Experiment index](00_START_HERE.md) | [14 script](../../scripts/14_full_stage1_stage2.py) | [14 configuration](../../configs/experiments/14_full_stage1_stage2.yaml) | [12 / 13 guide](12_13_legacy_nmr_alternatives.md)

`14` runs the full two-stage experiment using the same simplified legacy NMR
acquisition function and Arduino/Chemyx adapter as `12`. It is a separate runner;
`04`, `10`, `12` and `13` keep their existing experiment behavior.

## WORK commands

Use WORK's verified Python from `C:\code\chemyx_pump`. Prepare the rig first:
Channel 1 empty, Channel 2 loaded to 5 mL, and the needle's existing official
HOME reference established using the standalone Arduino script. The experiment
loads that reference and moves HOME then UP; it never establishes a new HOME.
The pump uses the manually prepared starting volumes and never sets a new HOME.

For a short check of the shared hardware and NMR acquisition method:

```powershell
python scripts\13_3InstrumentCheck.py --live
```

After the check finishes and the rig is ready, launch the full experiment:

```powershell
python scripts\14_full_stage1_stage2.py --live
```

Run one command at a time. Both start immediately without an interactive prompt.
`13` reads its base hardware settings from `12_stage2only.yaml`; `14` reads its
own YAML. Keep COM ports, NMR address, syringe geometry and needle reference
consistent if you edit either configuration. `13` checks CH1 and one NMR scan;
it does not exercise the CH2 dose or prove that a 54-hour run will succeed.

## Default schedule and physical sequence

| Step | Duration / timing | Action |
|---|---|---|
| Stage 1 | 48 hours; every 120 minutes | 24 scheduled measurements at 0, 2, …, 46 hours; wait until the full 48-hour window ends |
| Boundary dose | After Stage 1 and its physical cleanup | Needle to existing HOME; CH2 infuses 1.86 mL at 1 mL/min once; needle returns UP |
| Stage 2 | Six hours starting after dose completion; every 30 minutes | 13 scheduled measurements at 0, 0.5, …, 6 hours |

Every measurement uses eight NMR scans. Its physical cycle is the same as `12`:
withdraw 8 mL with the needle UP, move to HOME, withdraw 5 mL, settle 10 seconds,
acquire NMR, raise UP, return 13 mL, then wash by withdrawing and returning 5 mL.
Channel 1 finishes each successful cycle empty. The dose is at the stage
boundary, with no additional Time-0 dose or 180-second delay.

The `stages` entries in
[14_full_stage1_stage2.yaml](../../configs/experiments/14_full_stage1_stage2.yaml)
control each stage's duration, interval and endpoint measurement. The inherited
`experiment.duration_minutes` / `interval_minutes` fields are descriptive base
settings, not the two-stage schedule. Hardware, NMR and analysis settings are
in the same YAML. CH2 is required for this full-run preset.

These are fixed stage windows. NMR results never finish a stage early. Missed
measurement slots are logged and skipped rather than acquired in a burst.
The Stage 2 endpoint measurement and its cleanup finish after its six-hour mark.
Total wall time therefore includes 54 hours of stage windows, startup, the dose,
and final measurement/cleanup; it is longer than exactly 54 hours.

## NMR, analysis and restarts

Both stages use the exact `LegacyAcquisition` / `legacy_acquire` path used by
`12` and `13`: baseline raw-FID read, settings, one START, the existing driver's
completion polling, then fresh raw-FID retrieval. There is no pre-START idle
approval, prompt, historical dose acknowledgment or saved NMR permission file.
The [12 / 13 guide](12_13_legacy_nmr_alternatives.md) explains that method's
completion-parser limitations.

One acquisition session spans the boundary. An uncertain START remains pending
in memory; later NMR opportunities monitor it before another START. That can
skip a spectrum while both stage clocks and physical cleanup continue. NMR
exceptions, missing raw data and timeouts are reported without ending the run.

Live review uses separate instrument-free processes, one per stage, with `12`'s
ACME/ALS processing and primary 8σ prominence / width-0.95 dynamic area. Missing
dependencies, invalid analysis settings, bad data, worker crashes and analysis
errors cannot control stage timing, dosing or completion. The runner never
imports numerical analysis or waits for those processes. Each figure carries
the run/dataset identity and its stage; plot times use JCAMP acquisition metadata.

Physical instrument failures or uncertain movement still stop further movement.
There is no pause/resume. After manual preparation, a fresh launch starts Stage 1
again and includes another dose. Old results or RESERVED JSON files are not read
as admission gates. Existing output directories are preserved: use the default
fresh directory or choose a new `--output-dir`. Use `12` when Stage 1 is already
complete and only Stage 2 is wanted.

## Results and analysis recovery

Default results are under `results/experiments/full_stage1_stage2_<timestamp_id>/`:

| Location | Contents |
|---|---|
| `experiment.log`, `summary.json`, `config_used.yaml` | Combined experiment progress, dose outcome, stage outcomes and configuration |
| `stage_1/nmr_raw/stage_1_*.dx` | Stage 1 raw acquisitions |
| `stage_2/nmr_raw/stage_2_*.dx` | Stage 2 raw acquisitions |
| Each stage's `summary.json` and `config_used.yaml` | Stage schedule, acquisition outcomes and review configuration |
| Each stage's `analysis/` and `analysis_live.log` | Separate review CSVs, figures, manifest, cache, status and errors |

A completed clock with no saved spectra is reported as `COMPLETE_NO_SPECTRA`.
Analysis may finish after the instrument runner. To repeat the same review on
either stage, replace `<run>` with the results directory and run:

```powershell
python -m chemyx_lab.analysis.stage2_demo_analysis --run-dir "<run>\stage_1" --config "<run>\stage_1\config_used.yaml"
```

```powershell
python -m chemyx_lab.analysis.stage2_demo_analysis --run-dir "<run>\stage_2" --config "<run>\stage_2\config_used.yaml"
```

## HOME validation and deployment

No hardware is contacted by mock mode. The entire default schedule advances in
simulated time, with mock motion and mock NMR requests:

```powershell
conda activate ai
python scripts\14_full_stage1_stage2.py --mock
```

Deploy through HOME Git push → WORK Git pull → external-drive transfer from WORK
to WORK2. No offline package is required.
