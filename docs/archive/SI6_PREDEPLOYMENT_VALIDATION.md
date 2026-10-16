> **Historical document — archived 2026-10-16.** Preserved for provenance; its commands/settings may be superseded. Use [the current simple operating guide](../SI6_SIMPLE_RUN_GUIDE.md) and [errors/fixes](../SI6_ERRORS_AND_FIXES.md) at the instruments.

# Si6 final software validation - October 14, 2026

Primary operator instructions: [SI6_FINAL_REAL_RUN_CHECKLIST.md](SI6_FINAL_REAL_RUN_CHECKLIST.md).
Transfer inventory: [SI6_DEPLOYMENT_MANIFEST.md](../SI6_DEPLOYMENT_MANIFEST.md).
Prompt inventory: [SI6_PROMPT_AUDIT.md](SI6_PROMPT_AUDIT.md).
The generated release manifest records the exact delivered commit and hashes;
the final delivery report records the confirmed remote commit.

All work on HOME used static checks, archived spectra, synthetic spectra or
mock transports. No physical serial or NMR connection was opened. Validation
used Windows x64 CPython 3.11.15 in the `ai` environment. A fresh, independent
virtual environment installed all 27 pinned packages using only the local
wheelhouse and `--no-index`. Core imports, optional GUI imports and both real
configuration profiles resolve offline. The bundled Python installer is 3.11.9
x64; the supplied wheels target the compatible CPython 3.11 Windows x64 ABI.

## Solvent and fallback evidence

All 15 archived acquisitions pass the widened solvent detector. The shared
[CSV audit](audits/SI6_SOLVENT_REFERENCE_CHECK_2026-10-14.csv) retains raw hashes,
JCAMP LONG DATE, both peak positions, heights, prominences, SNRs, noise and
applied thresholds. No new experimental figures or acquisition times were
invented. Existing numerical target processing remains unchanged.

Synthetic tests cover normal and moderately shifted pairs, each single peak,
the 7.12 ppm neighbor and a shifted neighbor, high noise, flat elevated
background, blank acquisitions and confirmed solvent without a target. Peak
finding uses the full spectrum, then window/center limits and agreement of
paired shifts; a clipped neighboring peak flank cannot become a local maximum.
This is conservative sample-presence evidence, not a chemical identity assay.

Production-orchestrator tests run virtual 48-hour Stage 1 and 4-hour Stage 2
deadlines, independently and together. They check 24/8 scheduled starts, blank
target areas, permanent fallback despite solvent returning, original stage
timer origins, ordinary count-cap waiver, one protected 1.8 mL Channel 2 dose,
separate Stage 2 timing and safe return/cleanup. Missing, empty and malformed
retrieved data use the same fallback. Transport failure during active fallback
still stops and does not authorize a dose. Healthy solvent-confirmed target
disappearance and growth/plateau still finish early after four qualifying
observations. Untrusted data reset the consecutive endpoint window.

The independent synthetic spectrum-to-production-analysis validator completed
both stages and exactly one matched Channel 2 dose. Its counterexamples include
transient target dips, continued growth and temporary plateaus. A copied source
directory also completed the mock workflow without relying on the original
checkout or historical large datasets.

## Safety and deployment checks

- Main live entry rejects legacy/manual recipes before prompting or opening a
  transport. Current two-stage operation has one startup `y` and no nested
  prompts. Declining starts no instrument action.
- Manual set-home/home/up/down each require one `y`; status and immediate STOP
  require none. Absolute UP/DOWN remain idempotent, calibration stays 200
  steps/unit and normal destinations remain HOME=0/UP=1.
- Unknown, corrupt or interrupted needle state never initializes HOME
  implicitly. Explicit physical re-reference quarantines corrupt state. A failed
  STOP acknowledgment invalidates any possibly moving saved position.
- Communications-only diagnostics forbid needle movement and need no HOME
  reference; establishing HOME is a separate explicit physical reference action.
- Both COM4 and COM6 real recipes pass explicit `--validate-config` without
  transports. Invalid boolean, nonfinite, negative and overflowing durations
  fail before communication. Startup prints loaded durations and schedule bounds.
- Unique run paths, durable journal/dose reservation, atomic status/metadata,
  startup commit/source/config hashes and cleanup records remain available.
  Pre-transport reservation failures also close metadata with failure status.
- Current operational timestamps include an explicit UTC offset; acquisition
  timestamps retain their JCAMP metadata source. The clocks remain separate.
- A Windows permission failure during atomic needle-state replacement
  exposed a missing retry policy. Needle persistence now uses
  the runtime writer's existing bounded retry policy (six attempts, at most
  0.75 seconds total delay). Persistent denial still raises; it cannot turn an
  uncertain/pending position into a confirmed one or bypass required writes.
- Offline bundle verification checks 28 assets, including all 27 wheels.
  PowerShell installer/builder parsing and Python compilation pass. UTF-8 guide
  decoding passes; two mixed-encoding additions were normalized.

## Regression record

Evidence logs are retained locally under
`results/final_hardening_*`; generated experiment/test results are deliberately
excluded from Git and the deployment package.

| Validation | Result |
|---|---|
| Current 1,321-test collection, reconciled across broad run and corrected reruns | 1,319 unique passed, 2 skipped; no missing results or unresolved failures |
| Initial full regression invocation | 1,293 passed, 9 failed, 2 skipped; all nine failures resolved by the reruns below |
| Final current startup/preflight/logging plus three-instrument regression | 116 passed, zero failed/skipped |
| Windows needle-state suite plus originally failed Stage 1 fallback case | 15 passed, zero failed/skipped |
| Final complete solvent/fallback file, including all virtual 48 h/4 h cases | 30 passed, zero failed/skipped |
| Historical/template files plus final historical-hash rerun | 60 passed, 2 skipped across those corrected files |
| Standalone new pre-deployment cases before final UTC change | 57 passed, zero failed/skipped |
| Archived solvent spectra | 15/15 confirmed |
| Fresh offline installation | All 27 locked packages installed with no index/network |
| Copied-directory full workflow | Completed both stages and one protected dose |
| Independent synthetic analysis-driven workflow | Completed; all required counterexamples passed |

These are overlapping runs, not additive counts or a claim of one clean full
invocation. `results/final_hardening_aggregate.json` maps the current collection
to its latest JUnit evidence and confirms no tests are missing. The two skips
require unfinished historical synthetic inference jobs and their figure
collection; neither is a dependency of the live Si6 workflow or portable core.

The initial broad run loaded six outdated test assertions before their repair:
disposable sandbox folders were being treated as immutable scientific inputs,
one existing human review checkpoint was assumed absent, and four templates
were all assumed to use a legacy 300-second settling time. Repairs exclude only
disposable test folders, compare existing checkpoints by path/hash, and assert
the correct settling time per recipe family. The focused repair run passes
19 tests. No experimental input, human checkpoint or numerical processing
algorithm was changed to satisfy these tests.

Two further historical-audit cases had the same assumptions about disposable
copies and later human reviews. Their repair also retains two intentionally
mutable review documents/indexes, permits exactly three pre-existing October 7
comments in the analysis YAML, and recognizes the exact SHA-256 of the
pre-existing October 7 live-profile processing entry point. Frozen Python
scientific implementations are compared by complete AST, permitting comments
but preserving every statement/literal/default; the JavaScript implementation
retains its exact hash. No archived spectra, numerical outputs, review evidence,
processing entry point or scientific implementation was modified by this repair.

An initial portable-core invocation reused an older ACL-protected test scratch
directory and failed fixture setup. The validator now allocates a fresh unique
scratch directory instead of attempting to remove previous test artifacts.
The corrected invocation completed fixture setup and ran all 315 checks:
314 passed and one failed at the previously unretried Windows atomic needle
state replacement. The state-writer fix, its transient/permanent failure tests
and the full 30-case solvent/fallback rerun subsequently pass. A separately
started 535-case controls/Arduino run likewise had 534 passes and one occurrence
of that same old loaded state-writer failure; it had zero skips. These initial
failures are retained in the evidence logs, rather than presented as clean runs.

## Physical limitations and required live verification

Software validation cannot prove physical needle position, immersion or sample
flow, motor direction/missed steps, dose accuracy, pump STOP delivery, solvent
threshold suitability or reaction yield. Before the real reaction, follow the
checklist's supervised reference, communication and channel/NMR commissioning
checks. Retain installed USB drivers, commissioned firmware and working NMR
vendor software; these are separate rig prerequisites.

The two solvent signals may also be present in stale retained liquid; this
detector cannot prove fresh sample exchange or diagnose evaporation uniquely.
Conservative rejection activates timed operation and leaves chemistry
unverified. A 48-hour timer is elapsed time from stage startup, not time since
fallback activation. Short stages must allow at least three attempts for the
fallback latch to activate. Already-running cycles finish safe cleanup before
transition. RPC timeouts, pump/needle uncertainty, STOP failure or required
persistence failure still stop execution.

There is no automatic partial-run resume. Keep the reaction's dose reservations,
needle reference and complete journals; never clear them or invent a new ID to
repeat an uncertain dose. A new ID is for a separately prepared reaction.
