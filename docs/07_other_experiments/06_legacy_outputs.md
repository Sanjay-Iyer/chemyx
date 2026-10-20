> [Documentation home](../00_START_HERE.md) | [Other and older experiment workflows](00_START_HERE.md)

> Other/older workflow. For the full 48-hour / 6-hour run use the [fixed-time experiment](../03_fixed_time_experiment/00_START_HERE.md). Older Stage 2 endpoint rules can finish before 6 hours.

> Older workflow reference: [legacy experiment index](01_legacy_experiment_index.md).
> The real Si6 recipe now explicitly selects ACME; see [phase selection and
> post-session comparison](../05_nmr_analysis/01_phase_methods.md). Before replacing software,
> [back up rig state](../06_deployment_and_backup/03_rig_state_backup.md). The shared portable inventory is
> `offline/release_manifest.yaml`; `prepare_si6_release.py --working-tree
> --with-offline-payload` builds an uncommitted review snapshot. Older dated
> engineering evidence below retains its original validation scope.

# Main Si6 experiment outputs

`scripts/02_si6_experiment.py` now allocates one directory per execution under
`output.run_root_dir` (default `results/experiments`). This applies to its real
and synthetic mock modes. Diagnostic, soak, standalone Arduino and legacy
workflows retain their existing layouts. No historical result folders are moved.

Change the name alongside the chemistry settings in
`configs/experiments/si6_real_two_stage.yaml`:

```yaml
workflow:
  experiment_id: exp1
output:
  run_root_dir: results/experiments
```

Keep the rest of the YAML. The existing ID is the **physical reaction identity**,
not a run counter. A separately prepared reaction needs its own ID. Use 1–80
letters/digits/underscore/dot/hyphen, beginning with a letter or digit. The
filename component preserves accepted IDs exactly; sanitization rejects empty
IDs, traversal, separators, Windows device names, trailing dots and unsafe
characters instead of silently mapping distinct chemical identities to one name.
There are no new command flags or acknowledgment prompts.

The allocator captures the instrument computer's local date and UTC offset once
at run start. For `exp1` on October 15 it exclusively creates
`20261015_exp1`, then `_1`, then `_2`. Existing files/directories are never
overwritten. It selects the first free numeric suffix, including gaps, and retries
an atomic `mkdir` collision. A date rollover during the experiment cannot change
its path. A new run of `exp2` uses `20261015_exp2`; next-day `exp1` uses
`20261016_exp1`. Folder dates are operational labels, independent of NMR timing.

Relative roots resolve against the checkout, including Windows backslashes;
absolute roots are supported. Launching Python elsewhere does not redirect
results. Supply absolute CLI config/script paths when launching from elsewhere.

```text
results/experiments/20261015_exp1/
  metadata/
    experiment_info.json             original ID, suffix, full path, offset, status
    configuration_snapshot.yaml      effective recipe at startup
    config_snapshot.json             same recipe for existing JSON tooling
    instrument_settings_snapshot.json  resolved pump/NMR settings
    arduino_config_snapshot.json
    software_version.json            commit, source hashes, Python/packages
    manifest.json                    dataset and figure identities
  logs/
    experiment.log                   readable operational events
    experiment_journal.jsonl          authoritative durable journal
    run_state.json                   replayable state projection
    live_status.json
  step_1/
    nmr_raw/                         retrieved JCAMP acquisitions
    nmr_processed/<acquisition>/      pipeline tables, spectra, phase audit, plots
    analysis/
      time_series.csv
      evidence/0001.json             endpoint evidence
      solvent_evidence/0001.json
      stage_completion.json          end decision and fallback events
      qc_summary.csv, qc_summary.json
      summary.json                   stage report when applicable
    figures/                         stage time-series plots (PNG/SVG/PDF)
    logs/operations.jsonl            stage instrument event view
  channel_2_dose/
    dose_record.json                 reservation/intent/receipt, ledger reference,
                                     timestamps, command outcomes and errors
    operations.jsonl                intermediate addition event view
  step_2/                            same subdirectories as step_1
  final_qc/
    summary.json, summary.csv        overall status and stage end reasons
    full_time_series.csv             shared cross-stage observation table
    qc_summary.json, qc_summary.csv, qc_report.md
    experiment_summary.json, plot_manifest.json  metadata-timed stage report
    figures/                         cross-stage and QC figures
    nmr_summary/                     supplemental saved-data summary after closing
```

Optional reports/figures require observations and applicable authoritative
metadata; unavailable spectra remain unavailable. Processed acquisition plots
stay with their processing evidence under the correct step. Long-format spectrum
exports, when produced, are in that step's `analysis/spectra_long.csv` or its
processing folder. Original raw acquisitions are stored once, never copied into
shared summary folders. The acquisition/export contract is unchanged; recorded
retrieved paths identify actual local data. Pump and needle events also remain
in the authoritative cross-stage journal; per-step logs are small views of those
events, not separate recovery authorities.

`experiment_info.json` records both stage names, completion reasons, timed
fallback and chemical verification separately. Source-file hashes identify
uncommitted source too; a commit alone does not describe a dirty checkout. The
startup snapshot includes the processing YAML and resolved instrument settings.
No environment credentials are copied. JCAMP LONG DATE remains the scientific
time source; directory dates, log times and file modification times never
substitute for it in NMR figures.

Live reagent reservations remain at `runtime/si6_doses/<original-ID>.json`.
`_1` is only an execution suffix and **cannot authorize another dose**. A new
execution after any reservation is refused before transports open. Attempts that
reach allocation retain their own metadata/journal; the existing live configuration
preflight can reject an already reserved ID before allocating a folder. Neither
case resumes or retries dosing. There is no automatic partial-run resume.
Keep the original folder, canonical ledger and needle reference/pending marker
together when transferring a physical experiment. Offline journal inspection
accepts both old root journals and the new `logs/` paths. It does not resume.

Mocks use `<output-root>/.mock_dose_ledger/<original-ID>.json`, isolated from live
reservations but shared across executions within that mock root. Repeat attempts
are therefore blocked too. For an independent mock simulation choose a new mock
ID or fresh mock root; retain reservation history. The offline validator creates
a separate mock-rig namespace for each validation invocation.

The CLI prints the exact run folder. To find the latest start, inspect
`metadata/experiment_info.json` and sort its `started_at_local` values, checking
`mode` and `experiment_id`. Do not use lexicographic suffix order (`_10` versus
`_2`) or NMR file modification time. The existing main startup remains one `y`,
followed by uninterrupted automation; HOME recovery remains as documented in
[the Arduino guide](../01_arduino/02_home_recovery.md).
