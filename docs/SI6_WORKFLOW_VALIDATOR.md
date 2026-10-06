# Independent Si6 workflow validator

This offline review specification needs no cloud model, API key, internet, or
agent service. A human or separate LLM can apply it; deterministic Python tests
provide the runnable validation framework. Read `AGENTS.md`, the operator and
offline deployment guides, and `SI6_TWO_STAGE_VALIDATION_REPORT.md`. Inspect
implementation and evidence directly rather than repeating developer claims.

HOME is software/simulation only. Never run `--live` here or represent fake
motion as delivered volume, physical needle position, calibration, serial
responses, or real NMR operation. WORK hardware commissioning is separate.

For each major section, inspect architecture, review the smallest change, run
targeted tests, report concrete defects, and review corrections. Record **PASS**,
**PASS WITH LIMITATIONS**, or **FAIL**, with commands, results, and unresolved
physical limitations in the validation report. Review incrementally. Preserve
drivers and phase/raw processing unless a genuine defect is established.

## Sampling and configuration

- Reuse existing three-instrument services, metered moves, Arduino controller,
  NMR RPC, production FID processor, and journal/replay infrastructure.
- Verify exact order: UP → Ch1 withdraw 8 mL → DOWN → withdraw 5 mL → configured
  pause → configured NMR scans → infuse 13 mL while DOWN → UP → withdraw 5 mL →
  infuse 5 mL. Completion never skips cleanup.
- Check editable channel/volume/rate, pause, scan count, cadence, needle
  coordinates, and initial UP validation. Scan count and acquisition duration
  are separate from start-to-start reaction monitoring cadence.
- Check every channel capacity prefix and cycle volume balance before
  transports open. Invalid/missing channels fail before motion. Preserve
  existing channel-1 workflow and manual boundary recipe compatibility.

## Completion and quantitative evidence

- Stage 1 decreases, nominally every 120 minutes with a 48-hour maximum. Require
  a positive initially detected reference, material decrease, low-signal bound,
  minimum observations/time, rolling stability, and consecutive confirmations.
- Stage 2 increases, nominally every 30 minutes with about 3-hour expected
  duration and a separate maximum. Support valid near-zero initial signal,
  require subsequent material growth, and sustained conservative plateau near
  the greatest observed QC-qualified signal. A large decline followed by a flat
  lower signal is not completion.
- One anomalous low point, one flat interval, still-growing data, flat baseline,
  invalid QC, or repeated/backwards metadata cannot establish completion.
- Check retained production corrected trace, raw hash, finite arrays/phase/
  metrics, noise/uncertainty bounds, candidate QC, and negative-lobe cancellation.
  A zero-filled missing-peak table row alone is not proof of signal absence.
- Exercise the actual processor's prefixed retained-array/metadata filenames,
  not only synthetic bare-name fixtures. Record nominal fixture rejection and
  any diagnostic-only relaxed thresholds separately from commissioning bounds.
- Preserve validated processing. Explicit controller trend fixtures are
  simulation only and must never be selected during live execution.
- Fixed acquisition gain is required for quantitative comparisons. Reject
  automatic gain in YAML and resolved environment settings before transports.

## One-time transition and recovery

- Genuine Stage-1 completion precedes exactly one configured Ch2 infusion,
  nominally 1.8 mL at 1.0 mL/min. Stage 2 follows durable confirmation.
- Exclusive durable experiment reservation precedes transports. Journal/ledger
  contain stable experiment/dose/configuration identity. Dispatch intent is
  durable before physical actions; confirmation follows metered movement,
  accepted addressed STOP, durable pump completion, and boundary cleanup.
- Journal replay must reject semantic confirmation without its matching
  completed channel-2 pump operation, addressed STOP, exact volume/rate,
  completion sequence, and stable experiment/configuration identity.
- Test concurrent admission and crash before intent, during dispatch, after
  physical dose before receipt, and during Stage 2. Inspect actual sequences.
- RESERVED, DISPATCH_INTENT, CONFIRMED, and corrupt/torn prior reservation all
  refuse fresh replay. Changing output root/config cannot bypass the same ID.
  No automatic resume is currently supported. Require operator reconciliation.
- Never describe a new ID as a way to resume an interrupted reaction. Copy
  local ledgers and prior-run evidence with the physical experiment.

## Outputs, timing, and offline portability

- Preserve individual acquisition raw/processed references, phase evidence,
  metrics, QC/detection status, stage/iteration, scans, and timestamps.
- Refresh separate stage CSVs/plots/summaries while monitoring; preserve full
  experiment series and explicitly mark the dose boundary.
- JCAMP acquisition metadata is authoritative; LONG DATE is preferred. No
  filename/mtime fallback. Expose timestamp source and axis time origins.
- Dataset identity must be visible in every saved figure through the existing
  title helper. PNG/SVG/PDF and manifest titles must match.
- Resolve normal resources from the repository root without HOME paths,
  network/cloud requests, runtime package fetching, or this conversation.
- Distinguish machine COM/IP/calibration settings from portable resources.
  Check tracked code/config/fixtures/docs and separately staged ignored
  wheelhouse/drivers/vendor software before offline transfer.
- Test a copied repository at another location without internet or Git.
  Missing optional provenance tools must not stop required processing.
- Verify dependency manifest, exact commands, and separate HOME/WORK1/WORK2
  statuses. Drivers, vendor software, and Python environment are preinstalled.

## Deterministic commands from repository root

On HOME, use the prepared `ai` environment:

```powershell
conda activate ai
python -B scripts/02_si6_experiment.py --workflow-config config_templates/experiments/si6_two_stage_nominal.yaml
python -B scripts/02_si6_experiment.py --mock --workflow-config config_templates/experiments/si6_two_stage_fast_sim.yaml --arduino-config arduino/configs/arduino.example.yaml
python -B -m pytest tests/test_si6_two_stage.py tests/test_si6_validator_gates.py tests/test_si6_pump_channels.py tests/test_three_instrument_si6.py tests/test_si6_monitoring.py tests/test_runtime_journal.py -q -p no:cacheprovider
```

The first command validates without opening hardware; the second uses fake
instruments and explicit trend fixtures; the third tests order, cadence,
evidence, counterexamples, crash windows, compatibility, and replay. Include
production measurement tests and the copied-directory validation documented in
`OFFLINE_DEPLOYMENT.md`. Record exact commands and results in the report.
These commands require no runtime installation or downloads.
