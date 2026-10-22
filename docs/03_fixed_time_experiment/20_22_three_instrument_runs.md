# 20 / 21 / 22 — Three instruments (Arduino needle + Chemyx + NMR), simple path

[Experiment index](00_START_HERE.md) | [20 script](../../scripts/20_stage1stage2.py) | [21 script](../../scripts/21_stage2only.py) | [22 script](../../scripts/22_3instrumentquickcheck.py) | [Shared helpers](../../chemyx_lab/workflows/simple_rig.py) | [Two-instrument 23 / 24 / 25](23_25_two_instrument_runs.md)

| Script | What it runs | Settings |
|---|---|---|
| `20_stage1stage2.py` | 48 h Stage 1 → one Channel 2 dose → 6 h Stage 2, live review plots | [20_stage1stage2.yaml](../../configs/experiments/20_stage1stage2.yaml) (dose, stages, plots) on top of 21's file |
| `21_stage2only.py` (was 15) | 6 h Stage 2 only, live spectrum/peak plots, then automatic 09 report | [21_stage2only.yaml](../../configs/experiments/21_stage2only.yaml) (ports, recipe, NMR, schedule, review) |
| `22_3instrumentquickcheck.py` (was 16) | Quick check of all three instruments, automatic spectrum/peak plots | [22_3instrumentquickcheck.yaml](../../configs/experiments/22_3instrumentquickcheck.yaml) (check values) on top of 21's file |

Simpler replacements for `12`, `13` and `14`. They use the same Arduino, Chemyx
and NMR drivers, the same physical recipe and the same 04/05 legacy NMR
completion, but **no** `needle_state.json`, HOME journals, pump ledgers, dose
records or port lock files. Nothing from an earlier run can block the next one.
Ports, travel range, recipe and NMR settings live once, in 21's file; 20 and 22
read it, so the three always match.

| | 12 / 13 / 14 | 20 / 21 / 22 |
|---|---|---|
| Needle position | `needle_state.json`, must be a valid HOME reference | The two one-integer files `needle_control.py` uses (`runs/arduino/simple_needle_*.txt`) |
| After a failure | Can leave a flag that blocks manual and automated commands | Files keep their last completed values; nothing blocks; check the needle by eye |
| Chemyx STOP reply | Must be exactly `pump stop` | Any reply (logged); only `error`/`invalid` in a set/start reply fails |
| NMR failure | 12 keeps an unresolved START pending in memory | Each slot independent; failed slot skipped |
| End of run | Needle UP | Needle returned to HOME (0) |

## Before every run

1. HOME set once with `needle_control.py set-home` at the sampling depth. The
   runs move to that saved HOME first and end there (saved position 0).
2. Channel 1 syringe empty (plunger fully in).
3. For 20 only: Channel 2 loaded with `channel_2.initial_volume_ml` (default 5 mL).
4. NMR on, NMReady open, idle on the touchscreen.
5. No other program on COM3/COM4 (Arduino Serial Monitor, Chemyx GUI, another script).

All three `--live` commands start immediately with no prompt.

## Commands on WORK

```powershell
cd C:\code\chemyx_pump
```

Quick check first (needle UP/DOWN, 1 mL out/back, one scan):

```powershell
python scripts\22_3instrumentquickcheck.py --live
```

Then, with the needle back at HOME and Channel 1 empty, either the full run:

```powershell
python scripts\20_stage1stage2.py --live
```

or the six-hour Stage 2 only run:

```powershell
python scripts\21_stage2only.py --live
```

Rehearse on HOME without hardware (simulated time; 20 finishes in seconds, its
review plots a few minutes later; 21's analysis takes about 6 minutes):

```powershell
python scripts\22_3instrumentquickcheck.py --mock
```

```powershell
python scripts\20_stage1stage2.py --mock
```

```powershell
python scripts\21_stage2only.py --mock
```

## The sampling recipe (20 and 21)

Every sample: withdraw 8 mL (needle UP), needle DOWN to HOME, withdraw 5 mL,
settle 10 s, 8-scan NMR, needle UP, return 13 mL, wash 5 mL out and back.
Channel 1 ends each sample empty. Volumes, rates and the settle time are in
21's file and apply to 20 too. A slot whose start time has already passed is
skipped (no catch-up). At the end the needle returns to HOME and the ports close.

## 22: what it checks

Connect Arduino and Chemyx, check the NMR answers (warning only), needle UP one
increment and DOWN to HOME, Channel 1 withdraw 1 mL and infuse 1 mL at 5 mL/min,
one NMR scan saved as `nmr_raw\check_0001.dx`, then a PASS/FAIL line per part.
The initial NMR reachability check is advisory; exit code 0 requires the motion,
saved scan and cleanup checks to pass. It does not move Channel 2. A separate
review child processes the saved check file into `analysis\observation_0001.png`
(full spectrum), `analysis\observation_0001_target.png` (target region), and
`analysis\peak_areas.csv`. These finish shortly after the physical check exits;
processing success is independent of the instrument PASS/FAIL result.

## 20: Stage 1 → dose → Stage 2

| Step | Timing | Action |
|---|---|---|
| Stage 1 | 48 h, every 2 h | 24 samples at 0, 2, … 46 h; then the window runs to 48 h |
| Channel 2 dose | Right after Stage 1 | Needle to HOME, CH2 infuses 1.86 mL at 1 mL/min once, needle back UP (as in 14) |
| Stage 2 | 6 h from the end of the dose, every 30 min | 13 samples at 0, 0.5, … 6 h |

Change the dose, durations and intervals in `20_stage1stage2.yaml`. At launch 20
starts one separate review process per stage (14's review worker: ACME/ALS with
the primary 8σ / width-0.95 dynamic area) before the ports open. After each saved
spectrum it refreshes `stage_N\analysis\observation_0001.png` …, `peak_area_vs_time.png`
and `peak_areas.csv`; every title names the run and stage. It only reads raw
files and never stops, delays or ends the run (see `stage_N\review.log`); its last
update finishes shortly after 20 exits. `review.enabled: false` = raw data only.

```text
results\experiments\20_stage1stage2_<timestamp>\
    log.txt
    stage_1\nmr_raw\stage_1_0001.dx ...   stage_1\analysis\   stage_1\review.log
    stage_2\nmr_raw\stage_2_0001.dx ...   stage_2\analysis\   stage_2\review.log
```

The last log line gives each stage's saved / NMR-failed / missed counts and the
dose outcome. A dose error or Ctrl+C during the dose sends CH2 STOP, stops the
run before Stage 2 (needle to HOME, ports closed) and reports the dose as
`UNCERTAIN`; it is never repeated automatically. There is no pause or resume: a
fresh 20 starts with Stage 1 and includes the dose again. Use 21 when only
Stage 2 is wanted.

## 21: live spectrum review and final report

Before the instrument ports open, 21 starts the same separate review worker as
20 and 23. After each saved `.dx` file it updates `analysis\observation_0001.png`
... (ACME/ALS full spectra), `analysis\observation_0001_target.png` ... (target
peak region), `analysis\peak_areas.csv` and `analysis\peak_area_vs_time.png`.
Open the PNGs while the run is active to inspect the peak near 5.79 ppm;
`detection_status` and `analysis_quality_pass` are recorded in the CSV. Missing
peaks retain a missing/flagged result; a missing plot is a processing error or
delay, not evidence that the peak is absent. Details go to `review.log`.
All titles name the dataset. `review.enabled: false` disables this optional
review only; the physical schedule is unchanged. No analysis output is read by
the instrument runner, and a missing dependency or worker failure cannot stop it.

When the physical run has finished (needle at HOME, ports closed) and at least
one spectrum was saved, 21 runs the existing `09_analyze_stage2only_scipy.py` on
`nmr_raw\` in a separate process: ACME/ALS processing, 8σ `find_peaks`,
`peak_widths(rel_height=0.95)` dynamic area. Expect a few minutes (about 6 min
for 25 spectra on HOME). Its summary lines go into `log.txt`; the last line
says COMPLETE, PARTIAL or FAILED and where the plots are:

```text
results\experiments\21_stage2only_<timestamp>\
    log.txt
    review.log, review_config.yaml
    analysis\observation_0001.png, observation_0001_target.png ...    live review
    analysis\peak_areas.csv, peak_area_vs_time.png
    nmr_raw\stage_2_0001.dx ...                       raw data, only read
    analysis_stage2only_scipy\<date_time_id>\
        ANALYSIS_SUMMARY.txt, processor.log
        stage_2\target_peak_measurements.csv          area per spectrum, JCAMP time
        stage_2\plots\area_vs_time.png                peak area vs acquisition time
        stage_2\plots\observations\obs_0001_full_spectrum.png ...   one per .dx
```

21's default schedule is 25 slots every 15 minutes (0:00 through 6:00); change
`experiment` in its file. Each spectrum plot is titled with the run and
observation number and names its source `.dx` file (observations are numbered
in acquisition order, so after a failed slot `obs_0003` can be `stage_2_0004.dx`).
Times come only from JCAMP `LONG DATE` metadata. Mock spectra are copies of one
fixture with the same `LONG DATE`, so a mock rehearsal's time axis is a single instant.

An analysis problem (missing package, crash, timeout, bad file) is logged as
`ANALYSIS FAILED` with the command to rerun it, and never changes the run's
status or exit code; the raw `.dx` files stay as saved. Ctrl+C during the
analysis skips it. After Ctrl+C during the run there is no final 09 report;
live review can still finish processing raw files already saved. To
(re)analyse any Stage 2 data later (21's run folder, or 20's `stage_2` folder),
which adds a new dated folder beside the old ones:

```powershell
python scripts\09_analyze_stage2only_scipy.py "<run folder>\nmr_raw"
```

## 21: optional Channel 2 dose after the time-0 sample

Off by default. To use it, set `enabled: true` in the `channel_2` section of
[21_stage2only.yaml](../../configs/experiments/21_stage2only.yaml) and load Channel 2:

```yaml
channel_2:
  enabled: true
  channel: 2
  volume_ml: 1.86
  rate_ml_min: 1.0
  delay_after_time_zero_seconds: 180   # 3 minutes
  initial_volume_ml: 5.0               # what you loaded in Channel 2
  syringe_diameter_mm: 20.0
  syringe_capacity_ml: 20.0
```

When the time-0 sample has finished (sample returned and washed), 21 waits
`delay_after_time_zero_seconds`, then: needle to HOME, Channel 2 infuses
`volume_ml` once at `rate_ml_min` (1.86 mL at 1 mL/min is about 2 minutes),
needle UP. Then it waits for the next sample as usual. The startup lines and
the log show the plan, the dose time and the result (`CH2 dose: COMPLETE`).

The sampling schedule never moves: if the dose is still running at the next
sample's time, that sample is skipped (as for any late slot). With the default
15-minute interval a time-0 sample takes about 9–10 minutes, so 3 minutes +
2 minutes of dose leaves about a minute. If the log shows `Slot 2 MISSED`,
shorten the delay or lengthen `experiment.interval_minutes`.

21 refuses to start (exit code 2) if an enabled dose is set up wrong: Channel 2
loaded with less than `volume_ml`, a rate outside the pump's range, or a
negative delay. Ctrl+C or a pump error during the dose sends CH2 STOP, stops the
run (needle to HOME, ports closed) and reports the dose `UNCERTAIN`; it is never
repeated automatically. 20 keeps its own dose between stages, and 22 never doses.

## Errors and Ctrl+C (20, 21, 22)

| Event | What happens |
|---|---|
| NMR failure or timeout | Logged; that slot's spectrum is skipped; the physical sample return and wash still run; the schedule continues |
| Ctrl+C or pump error | STOP to pump and Arduino; needle UP; Channel 1 infused back to empty; needle to HOME; ports closed; exit |
| Needle error | STOP to pump and Arduino; syringe **not** emptied (needle position uncertain); the log gives the mL left |
| Needle move without DONE | Not counted; the needle is **not** moved again; the log says to check it by eye and run `needle_control.py set-home` |
| Leftover Arduino firmware fault | Cleared at connect |

Emptying after Ctrl+C or a pump error uses the volumes of the transfers that
finished plus an estimate of the interrupted one from how long the pump ran.
The estimate is rounded down by about one second of flow (about 0.1 mL at
5 mL/min) so the plunger is never pushed past fully-in. Press Ctrl+C again to
skip emptying or the return to HOME. If emptying is skipped or fails, or after
a needle error, the log gives the mL left in the syringe: push Channel 1 back
to empty before the next run, because 20/21/22 start by assuming it is empty.
Use [26 — simple manual Chemyx](26_chemyx_manual.md) (it keeps no saved state,
so a stopped run never blocks it) or the Chemyx front panel:

```powershell
python scripts\26_chemyxmanual.py infuse --channel 1 --volume 3.9 --live
```

Nothing that 20, 21 or 22 writes blocks a later run. Saved spectra, logs, review
outputs and the two needle integers are retained after interruption; review
reports are never admission requirements.

## Limits

- Position is commanded steps only (no encoder or switch), exactly as before.
- `needle_control.py` shares the needle position with 20/21/22. `chemyx_control.py`
  keeps its own saved pump position and does not see these moves; a successful
  run ends with Channel 1 empty, which matches its HOME definition.
- Kept safety: firmware 120-second move timeout, configured travel range,
  syringe 0 to 20 mL bounds, STOP on every error.

Offline tests: `python -m pytest tests\test_simple_rig.py tests\test_21_post_run_analysis.py tests\test_20_stage1stage2.py -q`.
