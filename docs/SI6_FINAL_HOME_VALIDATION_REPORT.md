> Historical audit of the implementation before the tracked-area refactor.

> Historical evidence before the area-only update. Current live rules and QC separation are in [SI6_AREA_ONLY_IMPLEMENTATION.md](SI6_AREA_ONLY_IMPLEMENTATION.md) and [SI6_CONFIG_USER_GUIDE.md](SI6_CONFIG_USER_GUIDE.md).
> Its integral descriptions and source line links do not describe current Stage 1/2 behavior.
> See [current implementation](SI6_TRACKED_PEAK_IMPLEMENTATION.md).

# Independent final HOME synthetic-analysis validation

Reviewer: independent agent, HOME laptop, 2026-10-06. This review uses static
inspection, deterministic spectra, fake transports and automated tests only.
No physical Chemyx, Arduino or NMR operation is permitted or claimed.

## Final independent assessment

**PASS WITH LIMITATIONS.** Genuine generated processed spectra drive the real
production measurement helpers and unchanged completion logic. The shared mock
runner completes Stage 1, confirms exactly one configured Ch2 infusion, resumes
Ch1 sampling and completes Stage 2 conservatively. Every required false-stop
trajectory is checked. The guides reference real scripts, parser flags and
template values, with reviewed corrections documented below.

This is software validation only. The model is already phased and uses tracked
shared processing defaults, not optional ignored `analysis.local.yaml`
overrides. Its test-only 9-hour Stage-2 deadline differs from nominal 6 hours.
Endpoint/QC calibration, physical instrument operation and actual chemistry
remain uncommissioned on HOME.

## Initial architecture audit, before implementation

The retained `si6_two_stage_trends.json` fixture is explicitly marked
`simulation_only`. `TrendSimulation.acquire` copies the existing real JCAMP
fixture, substitutes synthetic LONG DATE metadata and records a fixture area.
Its `process` writes a labeled `SIMULATION_ONLY.json`; its `analyze` injects the
fixture area directly as both peak and completion area with synthetic QC.
This is a useful controller/orchestration test, but changing its fixture area
does not change its copied spectrum. It therefore does not independently
validate spectrum-to-integral or QC behavior.

Production measurements use `Services.nmr_measurement`: acquisition, production
`process_fid`, authoritative JCAMP LONG DATE, `analyze_tracked_resonance`, then
`fixed_window_measurement`, then `completion_evidence`. The fixed-window adapter
is skipped in the existing simulation path. Production peak detection and
peak integration occur in `process_fid.process_spectrum_for_peaks`; peak QC is
performed by `process_fid._peak_qc` and checked again by the workflow adapter.
The completion metric is independently integrated from retained production
`regional_quantitative` arrays, with raw hash, phase, noise, uncertainty and
negative-lobe checks. Missing peaks alone cannot establish disappearance.

Directional stage rules are in `analysis/stage_completion.py`. Stage 1 uses
the initially detected signal as normalization; Stage 2 uses its greatest
observed valid signal and requires demonstrated growth and proximity to that
maximum. Both require elapsed time, observation count, rolling range/slope
limits and consecutive passing windows. The runner receives completion through
the measurement row's `plateau` flag, finishes sampling cleanup, and only then
executes Stage 1's boundary actions. Genuine sustained Stage-1 completion is
checked again before dose dispatch. Stage 2 starts only after completed Ch2
motion, accepted addressed STOP, boundary cleanup and durable confirmation.

## Initial independent finding

The user's conceptual success lists cannot complete at their last listed point
under unchanged nominal criteria: Stage 1 has only two passing windows at
iteration 13; Stage 2 has only two at iteration 15. Both require three. Continue
their stable tails rather than relax criteria. The longer Stage-2 trajectory
also exceeds the nominal six-hour maximum, so any validation-only extended
limit must be visibly identified. Nominal operating limits and chemistry
calibration must remain distinct from deterministic software validation.

## Independent numeric and existing safety gates

Run from repository root in the prepared `ai` environment:

```powershell
python -B -m pytest tests/test_si6_validator_gates.py -q -p no:cacheprovider --basetemp test_tmp_final_validator_baseline
python -B -m pytest tests/test_si6_final_validator.py -q -p no:cacheprovider --basetemp test_tmp_final_validator_numbers
```

Results: **26 existing gates passed in 1.26 seconds** and **10 independent
final numeric tests passed in 0.55 seconds**. Numeric tests inspect every prefix
of the requested successful trajectories; Stage 1 first completes at 14 and
Stage 2 at 16 after extending stable tails. Every requested false-stop numeric
trajectory remains incomplete, including extended high-flat, no-growth and
decline-flat histories. These results are Level 1, not spectral validation.

A long apparently stable plateau can meet the configured criteria before
hypothetical later growth. The requested short temporary plateau fails, but
future kinetics cannot be inferred from observations that do not exist yet.
Endpoint rules and minimum duration therefore still require real-data review;
software must not claim that all conceivable temporary plateaus are excluded.

## Spectral implementation review and correction

The final adapter generates a seeded processed spectrum and calls unchanged
production `process_spectrum_for_peaks`, `_peak_qc`, table writers,
`analyze_tracked_resonance` and `fixed_window_measurement`. It never copies the
requested area into the measured completion value. Raw DX is explicitly a
metadata carrier. Absorption-real input is already phased; vendor decoding,
FFT and phase optimization are outside this Level-2 check. The production
controller and processing/driver modules remain unchanged.

An initial 4097-point Gaussian fixture failed the existing negative-lobe gate:
production baseline correction created a negative integral exceeding the
configured noise allowance. Independent tests exposed this before acceptance.
The model was corrected to 1025 points and 0.014 ppm Gaussian sigma; all existing
QC and endpoint thresholds were preserved. Provenance records grid, width,
baseline, seed, drift and noise. This demonstrates production baseline
sensitivity to input sampling/shape, not chemistry calibration. The failing
fixture was not accepted by weakening production QC.

The reviewer also found an incorrect journal filename in the first validation
wrapper and required the actual `services.paths.journal_jsonl`, matched completed
pump moves, sequence ordering and valid replay instead of accepting semantic
receipt count alone. These validation-tool corrections are included.

## Independent current integration and figure checks — PASS

```powershell
python -B -m pytest tests/test_si6_final_validator.py -q -p no:cacheprovider --basetemp test_tmp_final_validator_complete
```

Result: **16 passed in 105.99 seconds**, 26 existing nmrglue/NumPy deprecation
warnings. This independent suite includes the numeric counterexamples, genuine
spectrum amplitude sensitivity, deterministic seeds, measured ppm drift,
production peak-QC rejection, high-noise rejection, a fresh shared full spectral
mock run and saved figure title/format checks. The full run first completes
Stage 1 at 14 and Stage 2 at 16; all earlier observations remain incomplete.
The dose is exactly one Ch2 infusion, volume 1.8 mL and rate 1.0 mL/min, with
matching durable pump-completion sequence. Stage-1 analysis precedes durable
intent/receipt; Stage-2 analysis follows it.

All four synthetic figures are saved as PNG/SVG/PDF. The reviewer verifies
12 manifest entries, matching format titles, an actual visible figure-level
dataset identity and the title in saved SVGs. Independent visual inspection of
the primary combined PNG confirms readable stage completion and dose markers.

## Reviewed primary one-command evidence — PASS

The reviewer independently read `test_tmp_final_synthetic_03/SUMMARY.json`,
`REPORT.md`, per-stage trace/evidence, the completed operation journal and figure
manifest. Full replay is valid. Stage 1 has 14 observations and first completes
at 14; Stage 2 has 16 and first completes at 16. In this recorded run,
intent sequence 818 precedes completed Ch2 infusion 832 and confirmation 839.
Every completed transfer after confirmation uses channel 1. Both stages end
with three passing windows. Stage-2 final checks end at 6.5, 7.0 and 7.5 hours;
the validation-only 9-hour ceiling admits them. Nominal six-hour behavior is
unchanged: this particular slower trajectory would hit its configured deadline
without an endpoint under that nominal maximum.

## Guide review and corrections

The reviewer checks the start-here, script and configuration guides against
actual paths, CLI parsers and config values. A diagnostic example initially
lacked a mode flag and would only validate, rather than process its input. It
now explicitly uses `--mock --process-only`; no hardware is contacted. The
guide also now explains that the shared runner supplies a fixed 5.0–6.5 ppm
search region and that experiment `analysis.line_broadening_hz` governs its
diagnostic magnitude view, while production quantitative broadening comes from
the processing YAML. Editing regional YAML does not override the runner's
explicit search-region flags.

Additional independent parser/config checks:

```powershell
python -B -m pytest tests/test_si6_final_validator.py -q -p no:cacheprovider --basetemp test_tmp_final_validator_guides_corrected -k "guides_cli or nominal_guide"
```

Result: **2 passed in 5.33 seconds**, 16 deselected. All Python commands in
PowerShell guide blocks reference existing scripts and valid parser options;
validation uses each script's `--help` only, never any documented live action.
Every displayed nominal/machine YAML block matches its actual template values.
Partial stage snippets are checked as subsets because the dose block is shown
separately. A test initially expected a whole stage mapping from a partial
snippet; this validator-test expectation was corrected without changing the
documented YAML or implementation.

The final guide correctly describes environment precedence and the real
`instrument_settings_snapshot.json` filename. It identifies seven requested
counterexamples plus an additional isolated measurement genuinely below the
2.5% Stage-1 bound. Shared nominal/development templates and raw/phase/control
modules are preserved. The independent suite contains **18 checks**, validated
as the 16-test integration run plus the separate two-test guide run above.

No physical instruments were tested. Nominal retained-real-fixture QC rejection
and required WORK endpoint commissioning remain unchanged. Synthetic validation
proves software behavior, not chemical correctness.

Latest independent current-code quick verification includes all primary
spectral trajectories, the new genuinely low isolated spike, provenance,
overwrite refusal, and independent numeric/QC/guide tests:

```powershell
python -B -m pytest tests/test_si6_synthetic_analysis.py tests/test_si6_final_validator.py -q -p no:cacheprovider --basetemp test_tmp_final_validator_current_quick -k "not actual_spectral_workflow and not saved_plot_dataset"
```

Result: **28 passed, 2 deselected in 94.91 seconds**, 26 existing warnings.
The two deliberately deselected full-workflow/plot tests already passed in the
independent integration run above. No physical command is part of these checks.

## Final one-command run review

The reviewer independently re-read `test_tmp_final_synthetic_04/SUMMARY.json`,
its operation journal, `shared_workflow.log` and the new
`workflow_completion_trace.csv`. All 10 cases pass at both numeric and spectral
levels, including the genuinely below-threshold isolated spike. The shared
trace has 30 rows: Stage 1 first completes at observation 14 and Stage 2 at 16.
The final Stage-2 rows show 1, 2, then 3 passing confirmations at observations
14, 15 and 16, explaining why it does not stop earlier. Journal replay is valid;
exactly one intent, completed Ch2 infusion and receipt occur, with requested
volume 1.8 mL and rate 1.0 mL/min. The dose sequence ordering remains
818 → 832 → 839. No physical instrument is involved.

The latest guide correctly distinguishes shipped nominal/shared processing
defaults from arbitrary edited run YAML and local processing overrides. Its
earliest possible completion observation 10 is correct: each of three
confirmation windows must itself end at or after the eighth observation. With
the configured cadences, this is 18 hours for Stage 1 and 4.5 hours for Stage 2.

## Genuine profile-admission defect — corrected, PASS

Verifying the earliest-observation explanation exposed a real configuration
validation defect. The old admission bound was
`max(minimum_points, window_points + confirmations - 1)`, which admitted
duration-driven schedules with only 8 or 9 observations under min8/confirmations3.
The completion engine correctly remained false, so the defect could not trigger
an unsafe dose; it allowed an impossible completion schedule to validate.

Independent duration-based counterexamples reproduced both admissions. The
correction changes only the profile feasibility bound to
`max(minimum_points, window_points) + confirmations - 1`. Endpoint evaluation,
thresholds, raw/phase processing, drivers and workflow sequencing are unchanged.
The nominal 24/12-slot stage schedules and the final synthetic run remain valid.

```powershell
python -B -m pytest tests/test_si6_profile.py -q -p no:cacheprovider --basetemp test_tmp_final_validator_profile
```

Result: **8 passed in 0.78 seconds**. Actual preparation rejects 8/9 slots for
both stages and accepts 10; when a seven-point window dominates a two-point
minimum, it rejects eight slots and accepts nine. The final one-command run and
earlier broad regression began before this small admission correction; this
focused current-code result validates the corrected boundary independently.

## Primary execution evidence

```powershell
python -B scripts/validate_si6_synthetic_analysis.py --output-dir test_tmp_final_synthetic_04
```

Exit0: all20 Level1/Level2 case checks and the full shared spectral workflow PASS.
Every requested false-stop case remains incomplete; the extra isolated1%-of-initial
glitch also remains incomplete. The full controller's measured values are:

| Stage | Generated first → last | Measured first → last | First completion | Prior iteration |
|---|---|---|---|---|
| Stage1 |100 →1.2 |99.457209 →1.193387 |14,26h from its first acquisition |13: only2/3 windows pass |
| Stage2 |1 →99.4 |0.994570 →98.860522 |16,7.5h from its first acquisition |15: only2/3 windows pass |

All observations and internal window metrics are in
`test_tmp_final_synthetic_04/workflow_completion_trace.csv`; the readable
`REPORT.md` also shows the generated/measured series, all counterexamples and
each complete set of pre-completion checks. `figures/combined_workflow.png`
marks Stage1 completion, the single confirmed Ch2 addition and Stage2 completion.
The primary agent visually inspected all four synthetic figures, with readable
dataset identity, completion markers and Ch2 dose label. PNG/SVG/PDF title
consistency is independently tested. These generated outputs are intentionally
local/ignored and regenerate with the one command after pull/copy.

The one-dose receipt reports channel2, volume1.8mL, rate1.0mL/min and statusCONFIRMED.
Journal replay proves the matched completed infusion and accepted STOP precede
Stage2. Subsequent completed transfers use Ch1. Raw decoding, physical phase,
instrument motion and chemical endpoint accuracy are not inferred from this proof.

## Final offline regression and portability — PASS

```powershell
python -B scripts/validate_si6_offline.py --copy-test --run-tests
```

Exit0. Resource/dependency checks and all four templates PASS. Selected broad
regression suite: **223 passed in899.65s**, 26 existing nmrglue/NumPy deprecation
warnings. The copied-directory mock experiment also PASSes with final status
`completed`; compact evidence is
`test_tmp_offline_validation/copied_directory_report.json`. This suite started
before the feasibility-bound correction/new profile test module was added;
the separately recorded8 current-code profile regressions validate that fix.
Future `--run-tests` runs include those profile tests automatically.

Primary spectral case/provenance/output tests: **12 passed in79.32s**:

```powershell
python -B -m pytest tests/test_si6_synthetic_analysis.py -q -p no:cacheprovider --basetemp test_tmp_final_synthetic_cases
```

Final resource/template validation after the admission fix PASSes. Ruff and
`git diff --check` PASS for the changed files. Exact primary command log is
retained locally at `test_tmp_final_validation_evidence/offline_validation.log`.
All tests here are software-only; no physical instrument contact or chemistry
calibration is claimed.
