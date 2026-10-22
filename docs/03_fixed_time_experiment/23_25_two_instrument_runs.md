# 23 / 24 / 25 — Two instruments (Chemyx + NMR, needle stationary)

[Experiment index](00_START_HERE.md) | [23 script](../../scripts/23_stage1stage2.py) | [24 script](../../scripts/24_stage2only.py) | [25 script](../../scripts/25_2instrumentquickcheck.py) | [Three-instrument 20 / 21 / 22](20_22_three_instrument_runs.md)

| Script | What it runs | Settings |
|---|---|---|
| `23_stage1stage2.py` (was 19) | 48 h Stage 1 → one Channel 2 dose → 6 h Stage 2, live review plots | [23_stage1stage2.yaml](../../configs/experiments/23_stage1stage2.yaml) |
| `24_stage2only.py` (was 17) | 6 h Stage 2 only (25 samples every 15 min), live spectrum/peak plots | [24_stage2only.yaml](../../configs/experiments/24_stage2only.yaml) |
| `25_2instrumentquickcheck.py` (was 18) | Quick check of both instruments, automatic spectrum/peak plots | [25_2instrumentquickcheck.yaml](../../configs/experiments/25_2instrumentquickcheck.yaml) (check values) on top of 23's file |

The needle stays secured in the flask: no Arduino imports, commands, HOME checks
or position files (the Arduino can be disconnected). Every sample is the same
cycle: **CH1 withdraw → NMR → CH1 infuse**, each step finishing before the next.
All three ask one `Confirm rig is ready [y/N]:` question after printing the
volumes and schedule.

## Change the volumes

Each script's YAML has an `EDIT HERE` block at the top:

```yaml
withdraw_volume_ml: 5.0
infuse_volume_ml: 5.2
initial_volume_ml: ...   # actual liquid loaded in Channel 1
```

Change either volume on its own (e.g. infuse `5.1`, `5.5`, `6.0`). Each sample
delivers `infuse - withdraw` extra (0.2 mL), so set `initial_volume_ml` to what
you actually load:

| Script | Samples | Extra delivered | Default loading | Left at the end |
|---|---|---|---|---|
| 24 | 25 | 5.0 mL | 6.0 mL | 1.0 mL |
| 23 | 37 | 7.4 mL | 9.0 mL | 1.6 mL |

23 walks every planned sample at startup and refuses to start (exit code 2,
nothing opened) if Channel 1 would run out or a withdrawal would overfill the
20 mL syringe; the message gives the mL needed. Example: infuse 5.5 needs
18.5 mL, which cannot fit with a 5 mL withdrawal. 24 only prints the plan and
stops at the first transfer that would cross a syringe limit, so check its
loading yourself. 23 and 24 have separate YAML files: change both if you want
the same volumes in each. 25 reads 23's file (ports, syringes, NMR, CH2) and
overrides only its check values.

## Commands on WORK

```powershell
cd C:\code\chemyx_pump
```

Quick check first (about 2 minutes; leaves the Channel 1 loading unchanged):

```powershell
python scripts\25_2instrumentquickcheck.py --live
```

Then either the full run (about 54 hours):

```powershell
python scripts\23_stage1stage2.py --live
```

or the six-hour Stage 2 only run:

```powershell
python scripts\24_stage2only.py --live
```

Rehearse on HOME with fake instruments and simulated time (23's review plots
follow a few minutes after it finishes):

```powershell
python scripts\25_2instrumentquickcheck.py --mock
```

```powershell
python scripts\23_stage1stage2.py --mock
```

```powershell
python scripts\24_stage2only.py --mock
```

## 25: what it checks

| Part | PASS when |
|---|---|
| Chemyx connect + STOP (CH1 and CH2) | Port opens and STOP is sent to both channels (CH2 never moves) |
| NMR reachable | NMReady answers |
| CH1 withdraw 1 mL | Transfer completed |
| NMR 1-scan spectrum saved | `check_0001.dx` saved |
| CH1 infuse 1 mL (sample returned) | Transfer completed (also after an NMR failure) |
| Chemyx STOP + port closed | Port closed after cleanup |

Exit code 0 only when every part passes (`CHECK PASSED: the rig is ready for 23 or 24`).
Results: `results/experiments/25_2instrumentquickcheck_<timestamp>/log.txt` and `check_0001.dx`.
The command also starts a separate review child before the pump opens. It
processes the saved check file into `analysis\observation_0001.png` (full
spectrum), `analysis\observation_0001_target.png` (target peak region), and
`analysis\peak_areas.csv`. The final plots finish shortly after the physical
check exits. Processing errors go to `review.log` and never change the
instrument PASS/FAIL result.

## 23: Stage 1 → dose → Stage 2 (same schedule as 14 and 20)

| Step | Timing | Action |
|---|---|---|
| Stage 1 | 48 h, every 2 h | 24 samples at 0, 2, … 46 h; then the window runs to 48 h |
| Channel 2 dose | Right after Stage 1 | CH2 infuses 1.86 mL at 1 mL/min once (CH2 loaded with 5 mL); no needle movement |
| Stage 2 | 6 h from the end of the dose, every 30 min | 13 samples at 0, 0.5, … 6 h |

8 NMR scans per sample. Slots stay anchored to each stage's start; a slot whose
time has passed is skipped (no catch-up). Change durations, intervals, the dose
or the scans in the same YAML.

At launch 23 starts one separate review process per stage (14's review worker,
ACME/ALS with the primary 8σ / width-0.95 dynamic area), before the serial port
opens. After each saved spectrum it refreshes, in each stage folder:

- `analysis\observation_0001.png` … one spectrum plot per sample (plus `_target.png`, the peak integration)
- `analysis\peak_area_vs_time.png` peak area vs hours since the first acquisition (JCAMP time)
- `analysis\peak_areas.csv` the numbers behind the plot

Every title names the run and stage, e.g. `23_stage1stage2_<time> stage_1 Peak Area vs Time`.
The review process only reads raw files: missing packages, crashes or bad data
never stop, delay or end the run (see `review.log`). Its last update finishes
shortly after 23 exits. Set `review.enabled: false` for raw data only. For the
full 09 SciPy report of Stage 2 afterwards:

```powershell
python scripts\09_analyze_stage2only_scipy.py "<run folder>\stage_2\nmr_raw"
```

```text
results\experiments\23_stage1stage2_<timestamp>\
    log.txt
    stage_1\nmr_raw\stage_1_0001.dx ...   stage_1\analysis\   stage_1\review.log
    stage_2\nmr_raw\stage_2_0001.dx ...   stage_2\analysis\   stage_2\review.log
```

The last log line gives each stage's saved / NMR-failed / missed counts and the
dose outcome. Exit codes: 0 complete, 1 stopped or failed, 2 settings refused,
3 confirmation declined, 130 Ctrl+C.

## 24: Stage 2 only

Defaults: 25 samples at 0, 15, … 360 minutes; 8 scans each. Change
`experiment.observation_count`, `experiment.interval_minutes` or `nmr.scans` in
its YAML. Raw files `observation_001.dx` … and `log.txt` are saved directly under
`results/experiments/24_stage2only_<timestamp>/` (a suffix avoids collisions;
existing folders are never overwritten). Before the pump opens, 24 starts a
separate review child. After every saved observation it updates:

- `analysis\observation_0001.png` ... full ACME/ALS spectra
- `analysis\observation_0001_target.png` ... the target region near 5.79 ppm
- `analysis\peak_areas.csv` detection status, peak area and quality flags
- `analysis\peak_area_vs_time.png` using JCAMP acquisition metadata

Open the PNGs during the run to inspect the peak. Plots are saved in the
background, so allow processing time after each acquisition. Every title names
the dataset. A missing plot means processing has not finished or failed; it
does not mean the peak is absent. See `review.log` for processing messages.
Review uses the same ACME/ALS and primary 8-sigma / width-0.95 algorithm as
20/23. `review.enabled: false` disables review. Analysis never controls,
delays or changes the instrument run. For the full 09 report afterwards:

```powershell
python scripts\09_analyze_stage2only_scipy.py "<run folder>"
```

## Errors (23, 24, 25)

| Event | What happens |
|---|---|
| NMR failure or timeout | Logged; one NMR cancel is sent if START was sent (no waiting, the reply is never required); the sample is still returned; that spectrum is skipped and the schedule continues, as in 20/21. 25 marks its NMR part FAIL |
| Pump error or uncertain displacement | STOP; no blind infusion; the log asks for physical inspection |
| Channel 2 dose error or Ctrl+C during the dose (23) | CH2 STOP, run stops before Stage 2; the dose is reported `UNCERTAIN` and never repeated automatically |
| Ctrl+C | STOP to both channels; if a scan was running, one NMR cancel (no waiting); a completed, known withdrawal is returned at once |

A failed scan never stops the run or waits for the NMR to report idle. The next
sample is 15 minutes (24) or 30 minutes to 2 hours (23) later, so the NMR has long
finished; if it were still busy, that sample's scan would also be skipped, and the
NMR code never saves an old spectrum as new.

Connections close after cleanup; saved spectra remain. No fault files, journals,
HOME records or previous logs are read to permit another run: there is no pause
or resume. After a stop, prepare the rig again and start fresh (23 begins with
Stage 1 and includes the dose again). If the log reports a sample that was not
returned, restore the loading you set in `initial_volume_ml` first (for example
infuse the 5.0 mL withdrawal), with [26 — simple manual Chemyx](26_chemyx_manual.md)
or the front panel:

```powershell
python scripts\26_chemyxmanual.py infuse --channel 1 --volume 5.0 --live
```

Offline tests: `python -m pytest tests\test_twoinstrument.py tests\test_23_25_twoinstrument.py -q`.
