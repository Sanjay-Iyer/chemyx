# 18 / 19 — Two-instrument check and full Stage 1 + Stage 2 (needle stationary)

[Experiment index](00_START_HERE.md) | [18 script](../../scripts/18_twoinstrument_check.py) | [19 script](../../scripts/19_twoinstrument_stage1_stage2.py) | [19 settings](../../configs/experiments/19_twoinstrument_stage1_stage2.yaml) | [17 guide](17_twoinstrument_guide.md)

Both use 17's simplified setup: Chemyx + NMR only, the needle stays secured in
the flask, no Arduino imports, commands, HOME checks or position files. Every
sample is 17's cycle, unchanged: **CH1 withdraw → NMR → CH1 infuse**, each step
finishing before the next. Both ask one `Confirm rig is ready [y/N]:` question.

## Change the volumes

Edit the block at the top of
[19_twoinstrument_stage1_stage2.yaml](../../configs/experiments/19_twoinstrument_stage1_stage2.yaml):

```yaml
withdraw_volume_ml: 5.0
infuse_volume_ml: 5.2
initial_volume_ml: 9.0   # actual liquid loaded in Channel 1
```

Each sample delivers `infuse - withdraw` extra (0.2 mL). 37 samples need 7.4 mL,
so 9.0 mL leaves 1.6 mL. If you change the infuse volume, update
`initial_volume_ml` to what you actually load. 19 walks every planned sample at
startup and refuses to start (exit code 2, nothing opened) if Channel 1 would run
out or a withdrawal would overfill the 20 mL syringe; the message gives the mL
needed. Example: infuse 5.5 needs 18.5 mL, which cannot fit with a 5 mL withdrawal.

18 reads the same file (ports, syringes, NMR, CH2) and overrides only its check
values in [18_twoinstrument_check.yaml](../../configs/experiments/18_twoinstrument_check.yaml).

## Commands on WORK

```powershell
cd C:\code\chemyx_pump
```

Quick check first (about 2 minutes; leaves the Channel 1 loading unchanged):

```powershell
python scripts\18_twoinstrument_check.py --live
```

Then the full run (about 54 hours):

```powershell
python scripts\19_twoinstrument_stage1_stage2.py --live
```

Rehearse on HOME with fake instruments and simulated time (19 finishes in seconds;
its review plots follow a few minutes later):

```powershell
python scripts\18_twoinstrument_check.py --mock
```

```powershell
python scripts\19_twoinstrument_stage1_stage2.py --mock
```

## 18: what it checks

| Part | PASS when |
|---|---|
| Chemyx connect + STOP (CH1 and CH2) | Port opens and STOP is sent to both channels (CH2 never moves) |
| NMR reachable | NMReady answers |
| CH1 withdraw 1 mL | Transfer completed |
| NMR 1-scan spectrum saved | `check_0001.dx` saved |
| CH1 infuse 1 mL (sample returned) | Transfer completed (also after an NMR failure) |
| Chemyx STOP + port closed | Port closed after cleanup |

Exit code 0 only when every part passes (`CHECK PASSED: the rig is ready for 19`).
Results: `results/experiments/18_twoinstrument_check_<timestamp>/log.txt` and `check_0001.dx`.

## 19: schedule (same as 14)

| Step | Timing | Action |
|---|---|---|
| Stage 1 | 48 h, every 2 h | 24 samples at 0, 2, … 46 h; then the window runs to 48 h |
| Channel 2 dose | Right after Stage 1 | CH2 infuses 1.86 mL at 1 mL/min once (CH2 loaded with 5 mL); no needle movement |
| Stage 2 | 6 h from the end of the dose, every 30 min | 13 samples at 0, 0.5, … 6 h |

8 NMR scans per sample. Slots stay anchored to each stage's start; a slot whose
time has passed is skipped (no catch-up), as in 15 and 17. Change durations,
intervals, the dose or the scans in the same YAML.

## Review plots during the run

At launch 19 starts one separate review process per stage (14's review worker,
ACME/ALS with the primary 8σ / width-0.95 dynamic area), before the serial port
opens. After each saved spectrum it refreshes, in each stage folder:

- `analysis\observation_0001.png` … one spectrum plot per sample (plus `_target.png`, the peak integration)
- `analysis\peak_area_vs_time.png` peak area vs hours since the first acquisition (JCAMP time)
- `analysis\peak_areas.csv` the numbers behind the plot

Every title names the run and stage, e.g. `19_twoinstrument_stage1_stage2_<time> stage_1 Peak Area vs Time`.
The review process only reads raw files: missing packages, crashes or bad data
never stop, delay or end the run (see `review.log`). Its last update finishes
shortly after 19 exits. Set `review.enabled: false` for raw data only. For the
full 09 SciPy report of Stage 2 afterwards:

```powershell
python scripts\09_analyze_stage2only_scipy.py "<run folder>\stage_2\nmr_raw"
```

## Results

```text
results\experiments\19_twoinstrument_stage1_stage2_<timestamp>\
    log.txt
    stage_1\nmr_raw\stage_1_0001.dx ...   stage_1\analysis\   stage_1\review.log
    stage_2\nmr_raw\stage_2_0001.dx ...   stage_2\analysis\   stage_2\review.log
```

The last log line gives each stage's saved / NMR-failed / missed counts and the
dose outcome. Exit codes: 0 complete, 1 stopped or failed, 2 settings refused,
3 confirmation declined, 130 Ctrl+C.

## Errors

| Event | What happens |
|---|---|
| NMR failure | 17's handling: logged, iFlow cancel and idle wait, the sample is still returned, the schedule continues once idle is confirmed |
| NMR idle not confirmed | Sample returned, run stops |
| Pump error or uncertain displacement | STOP; no blind infusion; the log asks for physical inspection |
| Channel 2 dose error or Ctrl+C during the dose | CH2 STOP, run stops before Stage 2; the dose is reported `UNCERTAIN` and never repeated automatically |
| Ctrl+C | STOP to both channels; a completed, known withdrawal is returned (17's rule) |

There is no pause or resume: after a stop, prepare the rig again and start a
fresh 19 (it begins with Stage 1 and includes the dose again).

Offline tests: `python -m pytest tests\test_18_19_twoinstrument.py tests\test_twoinstrument.py -q`.
