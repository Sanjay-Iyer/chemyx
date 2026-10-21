# 12 / 13 — Stage 2 and instrument check with legacy NMR completion

[Experiment index](00_START_HERE.md) | [12 script](../../scripts/12_stage2only.py) | [13 script](../../scripts/13_3InstrumentCheck.py)

These are separate alternatives to `10` and `11`. The original scripts and
configurations remain unchanged. `12` injects the legacy NMR callable into `10`'s
existing experiment loop; `13` injects the same callable into `11`'s check. Both
reuse the same Arduino/Chemyx drivers, saved needle reference and motion functions.

| Comparison | Difference |
|---|---|
| `10` → `12` | Same physical recipe, six-hour clock, 15-minute interval, 25 scheduled slots, eight scans, analysis and optional CH2 dose; different NMR completion/raw retrieval |
| `11` → `13` | Same UP/DOWN/UP and CH1 1 mL out/back check; legacy NMR completion, exactly one scan |
| `12` versus `13` | Exact same `stage2_legacy_nmr.legacy_acquire()` function; different scan count and physical orchestration |
| Standalone `04`/`05` versus `12`/`13` | Same driver's `wait_for_idle()` and baseline-FID comparison; no standalone prompts, network preflight or NMR ownership lock |

## Commands and configuration

HOME, with no hardware connections:

```powershell
cd C:\code\chemyx_pump
conda activate ai
python scripts\12_stage2only.py --mock
python scripts\13_3InstrumentCheck.py --mock
```

WORK, with the syringes and established Arduino reference prepared:

```powershell
python scripts\13_3InstrumentCheck.py --live
python scripts\12_stage2only.py --live
```

Each `--live` command starts automatically without another prompt. These are
alternative commands, not instructions to run the two scripts concurrently.

`12` defaults to [12_stage2only.yaml](../../configs/experiments/12_stage2only.yaml).
Its functional settings copy `10`: COM3 Arduino, COM4 Chemyx, 20 mm / 20 mL
syringes, gain 12, auto gain off, center 5 ppm, width 20 ppm and eight scans.
Change duration, interval, ports and other settings in this new YAML. Optional
CH2 remains disabled. If enabled, one 1.86 mL dose follows Time 0 cleanup plus
180 seconds. NMR/analysis failure does not prevent that dose when physical
cleanup completed safely.

`13` defaults to [13_3InstrumentCheck.yaml](../../configs/experiments/13_3InstrumentCheck.yaml),
which reads its base settings from the new `12` YAML and selects one scan,
CH1 1 mL withdrawal/return at 5 mL/min, and no CH2. Analysis is optional through
`three_instrument_check.analyze_spectrum`. No edits to `10`/`11` presets are needed.
Both commands accept `--config` and an optional fresh `--output-dir`.

## Exact legacy NMR sequence

Both use [legacy_acquire](../../chemyx_lab/workflows/stage2_legacy_nmr.py):

```text
GET /interfaces/iFlow/ExperimentStatus       # Baseline raw FID; not idle approval
GET /interfaces/iFlow/Settings/1D
GET /interfaces/iFlow/ExperimentSettings
PUT /interfaces/iFlow/Settings/1D
PUT /interfaces/iFlow/RunExperiment          # START once; 8 scans or 1 scan
GET /interfaces/iFlow/ExperimentStatus      # Actual driver's wait_for_idle loop
Read raw TD from the returned status
Compare with baseline, identify FID structure, save .dx unchanged
```

There is no `Service/Acquire` call, receipt/timestamp matching, completed
scan-count requirement, pre-START idle approval, NMR lock, historical
acknowledgment file or additional acquisition worker. The informational baseline
read can fail as a normal HTTP request, but its busy/idle value never approves START.

The unchanged driver waits while its older `status_indicates_running()` returns
true: boolean running flags take priority, followed by incomplete percentage and
nonzero integer `ResultCode`. A normal code-only completion is `ResultCode: 0`.
These are the same rules used by standalone `04`/`05`, not a new completion method.

Freshness is raw text differing from the pre-START FID, when a baseline exists.
Raw identification requires an FID type, nonempty data table and exact END record.
This is structural checking, **not a numerical JCAMP integrity validation**.
No peak, phasing or analysis result controls acquisition success.

## Failures and limitations

The legacy parser can mistake unknown replies for completion, or prefer a false
running flag over a conflicting status code. The new wrapper rejects an unknown
return lacking recognized state fields, rejects non-finite progress values as
sole evidence, and never claims saved data for missing, malformed or unchanged FID.
It does not strengthen recognized legacy status precedence or prove acquisition
identity. Fresh text alone is weaker evidence than the `10`/`11` receipt/service
checks; with no baseline, freshness comparison is unavailable. Compatibility and
completion semantics still require physical NMR verification on WORK.

A timeout, lost START reply, unknown completion, missing raw FID or unchanged FID
leaves the attempt pending **only in this run's memory**. There is no automatic
START retry. `12` continues physical cleanup and its fixed clock. Later NMR steps
monitor the pending attempt using the same legacy wait and baseline comparison;
only fresh valid raw and recognized legacy completion clear it. Recovered data
keeps the original observation filename. If unresolved, later STARTs remain
skipped. `13` reports failure and ends its one-shot check.
The pending condition is not persisted: a newly launched process cannot remember
an unresolved START from a previous process.

As in standalone `04`/`05`, a legacy return with missing raw data reports that
failure immediately; it does not add a separate late-data polling phase. `12`
can recover that data at a later scheduled NMR step. A save error after verified
legacy completion/freshness does not automatically repeat the acquisition.
One configurable `acquisition_timeout_seconds` covers each call's setup, waiting
and retrieval. HTTP calls are capped to remaining time; DNS, trickling responses
and filesystem operations are not hard wall-clock bounded.

Physical movement uncertainty stops further movement using the existing drivers.
Neither pump nor needle HOME is redefined. The only required saved physical
reference is the established Arduino needle reference, as in `10`/`11`.

## Results and analysis

Fresh folders are created under the configured `results/experiments` root:

- `stage2only_legacy_demo_<timestamp_id>/` for `12`;
- `3instrument_legacy_check_<timestamp_id>/` for `13`.

They retain `config_used.yaml`, `experiment.log`, `summary.json`, `nmr_raw/` and
the existing analysis layout. `12` uses `stage_2_0001.dx`, etc.; `13` uses
`instrument_check_0001.dx`. Existing data is never overwritten. If a server
export already occupies a path, the helper preserves it and saves RPC raw beside
it. For `13`, the check publishes the verified raw at its canonical filename and
preserves the displaced server export as `.server_export.dx`; this small output
adaptation is necessary because `11`'s check validates the canonical path.
Missing legacy raw is an acquisition FAIL in `13`, rather than inheriting `11`'s
assumption that `NMR_MISSING` already proves completion.

`12` retains the independent background ACME/ALS and dynamic 8σ / width-0.95
review process. It is never joined and cannot control timing or dosing. `13`
optionally processes the saved raw after serial handles close, as `11` does.
Plot titles identify the actual run. `12` uses the run-folder name alone as its
automatic dataset label to avoid a repeated prefix clipping longer titles;
explicit `analysis.dataset_display_name` still takes precedence. JCAMP metadata, preferring LONG DATE,
remains the time source. Mock data uses clearly labelled fixture copies with
unique titles for freshness tests; original metadata times are retained, so its
area/time plot does not pretend to span the simulated six hours.

## HOME validation

Validation on 2026-10-20: **249 tests passed**, including 37 focused new tests and
212 existing regression checks. These cover the actual legacy driver and HTTP
sequence, paired physical command streams, six-hour/25-slot mock, dose timing,
raw preservation, pending uncertainty and blocked physical network/serial access.
Normal HTTP sequence and saved bytes are compared directly with standalone
`04`/`05`. The standalone tests use fake servers restricted to loopback.

Both default mock CLI commands passed. `12` saved 25 spectra and its independent
child completed 25 analyses with 51 plots; `13` passed Arduino, CH1, NMR, raw
saving and analysis. Visible dataset titles were inspected. All 368 pre-existing
source/config/firmware files remained byte-identical. Independent review found
two issues (colliding server exports and non-finite progress values); both were
fixed, regression-tested and reviewed again with no unresolved findings.
These results verify offline behavior, not physical NMR compatibility.

Deployment remains HOME Git push → WORK Git pull → external drive → WORK2.
No offline package is produced.
