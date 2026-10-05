# How to create a new experiment

Use the existing environment on the instrument laptop. Run commands from the
repository root. If `python` is ambiguous, use `.venv\Scripts\python.exe` for
the documented offline installation; no package installation is needed here.

## Copy, edit, validate, run

1. Choose the closest recipe in `config_templates/experiments/`:
   standard Si6, 30 min, 60 min, mixed intervals, short attended, or long monitoring.
2. Copy and rename it (keep an existing recipe if that name already exists):

   ```powershell
   Copy-Item config_templates\experiments\reaction_30min.yaml configs\experiments\my_experiment.yaml
   ```

3. Edit only the required values in the copy, for example:

   ```yaml
   interval_minutes: 30 # minutes between cycle starts, for this stage
   max_hours: 8         # stage ceiling; already-started cycles finish cleanup
   ```

4. Validate without hardware:

   ```powershell
   python scripts\02_si6_experiment.py --workflow-config configs\experiments\my_experiment.yaml
   ```

5. Optionally exercise the mock path (no instruments; accelerated fixture data):

   ```powershell
   python scripts\02_si6_experiment.py --workflow-config configs\experiments\my_experiment.yaml --mock
   ```

   Mock mode defaults to four cycles per stage, independently of live duration
   limits. For a one-cycle short mock, add `--mock-cycles-per-stage 1`.

6. On the instrument laptop, after verifying its existing physical setup and
   local configurations, start the attended run:

   ```powershell
   python scripts\02_si6_experiment.py --workflow-config configs\experiments\my_experiment.yaml --machine-config configs\machines\00_machine.local.yaml --arduino-config arduino\configs\arduino.local.yaml --live
   ```

   The runner asks for live confirmation and reagent confirmations. Stage
   limits without plateau ask CONTINUE, ADVANCE, or ABORT. The default standard
   recipe enables initial early plateau stopping through `three_instrument`.

## Where to edit

| Change | Setting/file |
| --- | --- |
| Sampling frequency / duration | Each stage `interval_minutes` / `max_hours` in your recipe |
| Immediate first cycle | Stage `measure_immediately` |
| Later additions/repeat rounds | `first_addition_stage`, `repeating_stages`, `repeat_addition_rounds` |
| Pump volume | Existing `workflow.cycle` `volume_ml` entries (mL) |
| Pump rate | `pump.rate_ml_min`, keeping `units: mL/min` |
| Syringe diameter / capacity | `pump.syringe_diameter_mm` / `syringe_capacity_ml` |
| Pre-NMR settling | Existing pause entry `seconds` |
| NMR scans / gain | Global `nmr.scans` / `receiver_gain` |
| Tracked peak / tolerance | `nmr.target_ppm` / `analysis.detection_window_ppm` (half-width) |
| Plateau limits | `analysis.plateau_max_growth_percent`, `plateau_max_decline_percent`, `plateau_consecutive_intervals` |
| Initial early plateau stop | `three_instrument.initial_plateau_stopping_enabled` (new runner) |
| Arduino UP/DOWN / calibration / speed / acceleration | `arduino/configs/arduino.local.yaml` `needle.*` / `motion.*` |
| Pump COM / NMR address / acquisition timeout | `configs/machines/00_machine.local.yaml` |
| Detailed NMR processing | `configs/nmr/analysis.yaml` plus optional `analysis.local.yaml` |

Keep iFlow/FID, auto-gain false, current sampling action order, and reviewed
Arduino settings. Changing volumes requires a balanced cycle and capacity
reserve. Example: reducing sample withdrawal from 5 to 3 mL requires reducing
its return from 13 to 11 mL if the first withdrawal remains 8 mL. Cleanup
withdraw/infuse must also balance. Validation checks the retained-volume model.

The short template has **one immediate full SOP cycle and no later stages**;
its initial plateau stopping is disabled. It still takes the actual SOP's pump,
settling, acquisition, and processing time. Long monitoring uses the existing
attended workflow. Intervals are cycle start-to-start, not exact NMR timestamps;
the default cycle needs at least 12 min 22 s before needle/NMR/processing overhead.
No YAML reload occurs during a run. Global NMR and pump settings apply to all
stages. Reordering actions, per-stage scans/rates, or a prompt before every action
would require a targeted Python change; these templates do not add them.

## Configuration precedence

* Experiment: the complete file passed with `--workflow-config`.
* Machine communication: `--machine-config` (default machine local YAML).
* Arduino geometry/motion: `--arduino-config` (default Arduino local YAML).
* Detailed processing: shared NMR `analysis.yaml`, recursively overlaid by
  `analysis.local.yaml`, then processing CLI arguments. An explicit processing
  `--config` selects that file instead of merging the default pair.
* Existing process `CHEMYX_*` / `NMR_*` environment variables can override
  assembled pump/NMR YAML values (e.g. `CHEMYX_RATE`, `NMR_DEFAULT_SCANS`,
  `NMR_RECEIVER_GAIN`, `NMR_RPC_MAX_WAIT_SECONDS`, `NMR_TARGET_PPM`).

If a YAML edit appears ineffective, inspect relevant overrides in the terminal
launching Python. To list only the instrument overrides:

```powershell
Get-ChildItem Env: | Where-Object Name -Match '^(CHEMYX_|NMR_)'
```

Templates are copy sources, not auto-loaded overlays. The current runtime local
YAML files are tracked: preserve the instrument laptop's verified copies during
USB/Git updates. A sampling-interval edit needs no firmware or dependency update.

## NMR analysis adjustments

There are multiple gates; changing one does not bypass the others:

| Layer | Settings | Effect |
| --- | --- | --- |
| Regional candidate detection | Processing YAML `regional_analysis`: ppm range, prominence, spacing, minimum width, `detection_trace` | Finds candidates on the current corrected real trace |
| Production peak QC | `peak_qc`: `use_manual_thresholds`, SNR, prominence, width in Hz, positive area | Determines which candidates enter the simple table; manual values apply only when enabled |
| Selected simple-table window | Experiment target/half-width passed to processing CLI | Keeps the tracked resonance, currently 5.70-5.90 ppm |
| Workflow measurement QC | Recipe `analysis.min_peak_snr`, `min_prominence_snr`, `min_peak_area`, `area_epsilon` | Can further reject the production QC-passing peak |
| Workflow plateau | Recipe growth/decline limits and interval count | Uses accepted per-stage areas; three intervals need four measurements |

For a new chemistry, review target/window and signal-quality requirements.
Width limits are in processing `peak_qc`. Advanced phase method/P0/P1,
baseline method, zero filling, and line broadening are processing settings;
change only with comparison to saved spectra. Accepted defaults are unchanged.
The integrated runner explicitly passes processing region **5.0-6.5 ppm**;
editing regional YAML alone does not override that CLI choice. A target outside
that region requires a small code change to the existing region constant.
Recipe integration/plot windows and line broadening remain legacy/magnitude
settings; they do not replace production real-spectrum processing settings.

Processing's optional statistics/target-peak completion analyses have their own
thresholds. They do not replace the experiment's stage plateau decision. Every
time plot uses acquisition metadata; filenames/mtime are not acquisition time.

## NMR GUI tools (local saved files; no internet or instrument)

For exact production phase matching, before/after review, separate manual
numerical re-analysis and the transfer checklist, see
[Phase 4 deployment review](PHASE4_DEPLOYMENT_REVIEW.md).

Recommended complete review tool:

```powershell
python scripts\nmr\phase4.py path\to\spectrum.dx --runs-root results\runs\si6
```

Phase 4 compares Original FFT, Automated production result, and Manual phase;
supports saved candidates and browsing processed runs. It reads production
exports/recorded phase provenance and can explicitly run the existing
`process_fid.py` on a chosen file. If no spectrum argument is supplied, it uses
an existing demo file or offers a file picker. It never acquires data.

Other retained tools (supply an actual local file rather than relying on demo paths):

| Command | Purpose / relationship to production |
| --- | --- |
| `python scripts\nmr\phase1.py path\to\spectrum.dx` | Simple interactive P0/P1/pivot phasing using audited FFT helpers; exploratory fixed preprocessing |
| `python scripts\nmr\desktop_nmr_phase_demo.py path\to\spectrum.dx` | Same simple demo as Phase 1; duplicate retained entry point |
| `python scripts\nmr\phase2.py path\to\spectrum.dx` | Exploratory phase/baseline/integration controls through audited helpers; separate demo settings, not a recipe editor |
| `python scripts\nmr\phase3.py path\to\spectrum.dx` | Phase candidates/comparison; explicit handoff to production process_fid |
| `python scripts\nmr\interactive_processing_explorer.py path\to\spectrum.dx` | Matplotlib exploratory phase/reference/baseline views using shared helpers and separate exploratory settings |

Qt tools need PySide6, pyqtgraph, NumPy, nmrglue, plus SciPy/matplotlib/PyYAML
for helpers/production analysis. These are already in the pinned 27-package
offline wheel bundle. The
Matplotlib explorer uses the same installed scientific stack. Its **optional
notebook interface** additionally needs IPython/ipywidgets/Jupyter, which are
not in that lock: use the standalone desktop command on the bundled setup.
`plot_integration_charts.py` is a static chart script, not a GUI. Its inputs are
`--workbook <file.xlsx>` and `--csv-dir <directory>`. The workbook path requires
a pandas Excel reader such as openpyxl, which is **not** in the offline lock;
do not assume that unrelated optional chart script is fully bundled.

The Matplotlib explorer imports an inspection helper that selects the noninteractive
Agg backend. Its plain command therefore may not display a window. Without
changing that existing GUI, explicitly select the already-bundled Qt backend
after importing it:

```powershell
python -c "import sys; sys.path.insert(0, 'scripts/nmr'); import interactive_processing_explorer as e; e.plt.switch_backend('QtAgg'); e.main()" path\to\spectrum.dx
```

Its CLI also expects the historical demo series to exist while constructing
defaults, and its export path is restricted to its existing exploratory root.
Use Phase 4 for a portable file-picker/browse interface. The older tools remain
available; none were deleted or redesigned.

## Final NMR summary

After the Level 2 instrument session closes normally (including returned
non-success outcomes), reporting writes `final_nmr_summary/` inside the run.
Its failure cannot change the experiment outcome or trigger instrument motion.
Unexpected exceptions still follow existing failure handling; rerun reporting
after inspection on whatever saved output exists:

```powershell
python scripts\nmr\summarize_run.py results\runs\si6\<your_run_folder>
```

Outputs: `nmr_iteration_overview.csv`, `nmr_run_summary.csv`,
`nmr_qc_overview.csv`, `peak_area_vs_time.png`,
`peak_area_percent_change.png`, `all_nmr_spectra.png`, and `manifest.json`.
Individual raw/processed iteration data and the original journal are preserved.
Tables link to those files using paths relative to the source run folder.

The report merges existing time-series values and journal failure/QC evidence.
It reuses corrected regional real-spectrum exports, or an existing exported
phased real spectrum; it never reprocesses missing FIDs. Fast mocks reuse the
saved trace for their byte-identical fixture copies. Missing spectra/timing are
explicit in the manifest and plots; an iteration without real-spectrum exports
cannot be reconstructed by this summary. Existing within-stage growth and
plateau decisions are copied, not recalculated across reagent stages. Overall
first-to-last area change is descriptive and not a new plateau criterion.

Attempt counts use confirmed saved NMR evidence. An unfinished cycle without
such evidence is marked unconfirmed instead of assuming acquisition occurred.
Actual acquisition time is LONG DATE (or an explicit LONG DATE journal record);
no timing source is invented for acquisition failures. Repeated mock fixture
copies retain the same original acquisition timestamp, so their elapsed time
is zero; that verifies reporting without simulating a real chemistry timeline.
