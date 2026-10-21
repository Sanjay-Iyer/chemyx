# Project memory

## Deployment preference (clarified 2026-10-19)

The user pushes source changes to Git from HOME, pulls them on WORK, then
transfers from WORK to the second WORK laptop using an external hard drive.
HOME never makes or supplies offline packages for this workflow. Do not
proactively build release ZIPs, run prepare_si6_release.py, or recommend a HOME
package-export step. Existing generated archives are left alone. Any needed
machine-local dependencies/data are handled on the WORK side of the transfer.
The user handles Git push/pull; do not infer an instruction to commit or push
from a request to edit or validate code.

## Script 15 must analyse its own data (clarified 2026-10-21)

Collecting only raw `.dx` files was not what the user wanted from 15. After the
physical run ends, 15 automatically runs the existing 09 SciPy analysis
(results CSV, one spectrum PNG per `.dx`, area vs JCAMP time) in a separate
process. Instrument control, schedule and the no-lockout design stay as they
were; an analysis failure is only logged.

## Two-instrument 17 / 18 / 19 (clarified 2026-10-21)

17's setup (Chemyx + NMR, needle stationary in the flask, one startup
confirmation) is the base for 18 (quick check: CH1 1 mL out/back, 1-scan NMR,
CH2 STOP only) and 19 (48 h Stage 1 every 2 h -> one CH2 dose of 1.86 mL at
1 mL/min with no needle move -> 6 h Stage 2 every 30 min). The user chose live
review plots per stage (14's separate review worker). The user wants the infuse
volume at 5.2 mL and easy to change in the YAML; tests read volumes from the YAML.

## Arduino HOME ownership (clarified 2026-10-19)

The user establishes the official needle HOME with the Arduino-only script,
which logs the action. The fixed-time experiment must move to this saved HOME
at startup, then raise to UP; it must never set the current needle position as
a new zero. Preserve the saved HOME and pending/uncertain state rather than
silently overwriting it. Movement tracking may update the current position.
Old-dose reconciliation removal remains scoped separately from needle HOME.

## Fixed-time launch and pump HOME (clarified 2026-10-19)

The user prepares all instruments before launching. The fixed-time --live
command must start automatically without a startup y or later confirmations.
Preserve official HOME for BOTH Arduino and Chemyx. For Chemyx, log the user's
manual return to the existing HOME loading; only current-position bookkeeping
changes. Do not replace the original HOME receipt, geometry or identity, and
do not invent missing HOME. The needle physically returns to its saved HOME,
then UP. Neither phase nor their boundary dose asks for acknowledgment.

## Separate Stage 2 only workflow

Stage 2 only is a separate workflow: `scripts/06_si6_stage2only_run.py`,
`configs/experiments/si6_stage2only_6h.yaml` and documentation 03/06. It skips
Phase 1 and the Channel 2 dose, preserves original HOMEs, leaves Channel 2
loading alone, and samples at 0:00, 0:30, ..., 6:00 (13 slots). Starts without
prompts. The full two-stage runner and preset remain unchanged.

The user clarified that stage2only must also process the data after each
scheduled measurement, so they can inspect it DURING the run. This processing
is optional background review only. A separate process writes analysis_live
plots/tables and index.html; analysis errors, timeouts, crashes, missing
dependencies or bad results must never stop or shorten the instrument schedule.
The instrument runner never waits for or reads analysis decisions. Raw FIDs,
official HOMEs and physical control remain independent of review processing.

For a manual full Stage 2 reanalysis/recovery, use the separate
`scripts/07_analyze_stage2only.py`: no arguments opens a folder picker, or pass
a run/nmr_raw/copied raw directory. It reads Stage 2 raw files independently of
broken live-analysis JSON and writes a fresh analysis_stage2only folder. It
never controls instruments or changes the acquisition runner.

The user approved `find_peaks` minimum prominence 8 sigma with
`peak_widths(rel_height=0.95)` as the primary dynamic algorithm in the separate
08/09 workflows: `scripts/08_si6_stage2only_scipy_run.py` and
`scripts/09_analyze_stage2only_scipy.py`. They share one numerical processor and
`configs/nmr/stage2only_scipy.yaml`, while preserving 06/07 and their fixed-area
behavior. Primary tables, plots and rates use dynamic area; fixed 5.70–5.90 is
a comparison only, never a substitute for missing/invalid dynamic area. The
08 clock, recipe, HOME policy and isolation from numerical failures match 06.

## Scientific visualization preference

The user expects every experimental-data plot to visibly show the dataset or
experiment name in its title so figures can be identified during presentations
without relying on filenames or surrounding context. This is a project-wide
preference, not a rule specific to one NMR run.

NMR code implements the preference through
`chemyx_lab.analysis.plot_titles.format_dataset_plot_title`. Single-panel plots
prefix their descriptive title; multi-panel figures use a dataset-aware
suptitle. Reusable functions must receive or resolve the dataset from
configuration, metadata, or dataset/run paths and must not hard-code it.

The user also expects timing figures to distinguish nominal filename/schedule
time from actual recorded event time. For offline NMR, JCAMP metadata time is
authoritative; `sequence-HHMM` is only a nominal filename label. Plots should
preserve real irregular acquisition intervals and make the timing source clear.
