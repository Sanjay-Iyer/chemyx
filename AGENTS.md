# Repository agent guidance

## Deployment workflow

The user's deployment route is HOME Git push -> WORK laptop Git pull ->
external-hard-drive transfer from WORK to the second WORK laptop. HOME does
not prepare or transfer an offline package for either WORK laptop.

Do not generate release ZIPs, offline handoff packages or export bundles unless
the user explicitly changes this instruction. Do not run
`scripts/prepare_si6_release.py` as a routine completion or verification step.
Existing packages may remain; do not delete them just because they are unused.
Keep current operating documentation aligned with the Git/WORK/drive workflow.

## Simple 15/16 scripts: deliberate exception

`arduino/scripts/needle_control.py`, `arduino/python/simple_needle.py`,
`scripts/15_stage2_simple.py`, `scripts/16_3_instrument_check_simple.py` and
`chemyx_lab/workflows/simple_rig.py` are the user's requested no-lockout path.
The needle position is two plain-text files with one integer each
(`runs/arduino/simple_needle_position.txt` counter, `simple_needle_steps.txt`
motor steps from HOME), written only after DONE. They never read or write
needle_state.json, journals, pump ledgers, dose records or port locks, accept
any Chemyx STOP reply, and use the standalone 04/05 NMR `legacy_acquire`. Do
not add validity flags, fault files, HOME-reference, journal or lockout rules
to them.

## Official Arduino needle HOME (older workflows 04–14)

These rules apply to the older workflows that still use needle_state.json;
needle_control.py no longer writes that file. The original standalone Arduino
script owned establishment of the official needle HOME
and logs that explicit operator action. The three-instrument experiment must
load the same saved reference and move to HOME=0, then UP=1. Never redefine the
needle's current location as HOME when an experiment starts, silently replace
an unknown reference, or delete its pending recovery marker. Normal movement
updates the tracked current position without changing the HOME definition.

## Automatic fixed-time run and official Chemyx HOME

The fixed-time --live command starts without interactive prompts. The user
prepares the rig first, including returning Channel 1 to its established 0 mL
HOME loading and Channel 2 to its established 5 mL HOME loading. Record that
manual return in current-position bookkeeping, preserving each original HOME
receipt, identity and geometry. Never call set_home on live experiment startup
or invent a missing/incompatible pump HOME. Old dose records do not block a
new manually prepared run; keep the single-dose guard local to its results.

The separate stage2only runner (`06_si6_stage2only_run.py`) starts directly at
Stage 2 and never invokes Phase 1, dosing or dose reservations. Its six-hour
schedule includes time 0 and the six-hour endpoint (13 slots every 30 minutes).
Only Channel 1 is manually restored to its existing empty HOME. Never reset
Channel 2 loading/displacement or require its HOME/dose reconciliation for this
sampling-only runner. Preserve the original full-run script and preset.

Stage2only also processes each saved measurement for review during the run.
Keep analysis in a separate process with no instrument handles or control
authority. The instrument runner must never import the review worker or execute
numerical processing, wait/join for it, or use an analysis result/status/error as an admission or
completion gate. Missing dependencies, worker crashes, bad metadata, processing
exceptions and timeouts must not stop or shorten the six-hour run. Keep review
outputs separate from raw data and required motion/state evidence.

The separate 08/09 stage2only SciPy workflows use the user-approved primary
algorithm: production ACME/ALS, find_peaks prominence >=8 sigma, then
peak_widths(rel_height=0.95), interpolated endpoint-line baseline and positive
trapezoidal area. Their primary area/time/rate/kinetic outputs must use dynamic
area; fixed 5.70–5.90 ppm remains a labelled comparison, never a fallback for
missing or invalid dynamic area. Preserve 06/07 behavior. Keep the same
six-hour/13-slot instrument schedule and existing HOME/no-dose policies, and
keep all numerical processing/profile validation out of the instrument process.

## Dataset-aware plot titles

Every saved experimental-data figure must visibly identify its source dataset.
For a single-panel plot, use `<dataset display name> <descriptive title>`. For a
multi-panel figure, put the dataset identity in the figure-level suptitle;
panel titles may then remain concise. A dataset identifier in the filename
alone is insufficient.

For NMR processing, use the existing `statistics.dataset_display_name` or
`target_peak.dataset_display_name` value in `configs/nmr/analysis.yaml` when it
is available. Otherwise resolve authoritative metadata, the input dataset
directory, or the output run directory using
`chemyx_lab.analysis.plot_titles`; never hard-code a run name in reusable plot
code. Use `format_dataset_plot_title` so whitespace is normalized and an
existing prefix is not duplicated. All formats of one figure (PNG, SVG, and
PDF) must carry the same visible title, and manifests must record the same
dataset identity and visible title.

Correct examples:

```text
06-09-26 Focused Spectral Evolution
06-09-26 Area vs Time
TEST-RUN-001 Completion Detection
```

Incorrect examples:

```text
Focused Spectral Evolution
Area vs Time
06-09-26 06-09-26 Area vs Time
```

Any new experimental plotting path requires a test of this title convention.

## Authoritative NMR timing

Treat JCAMP acquisition metadata as the authoritative NMR time source. For
NMReady data, `LONG DATE` is preferred by the existing timestamp hierarchy.
A filename token such as `sequence-1015` is a nominal `HHMM` schedule label,
not an actual acquisition timestamp. Never silently substitute filename time
or file modification time in a plot presented as metadata timing. Timing
comparison plots must expose their source fields, use one shared CSV table, and
fail closed when no metadata timestamp is available.
