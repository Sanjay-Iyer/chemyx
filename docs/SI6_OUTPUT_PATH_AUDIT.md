# Si6 output-path audit (2026-10-15)

This trace was completed before changing output code. It covers
`scripts/02_si6_experiment.py`, not the independent diagnostic, soak or legacy runners.

| Producer / reader | Previous location or behavior |
|---|---|
| `three_instrument_si6.prepare`, `config.resolve_repo_path` | Existing YAML `output.run_root_dir`, resolved against the checkout; main real config used `results/runs/si6_two_stage` |
| `create_identified_run` → `base.create_run_paths` | `<root>/YYYYMMDD_HHMMSS_si6_live`; mocks used a sibling `_mock` root; collision suffix `_01` |
| `open_services`, `begin_run` | Root config/instrument/Arduino JSON snapshots and manifest; `metadata/run_metadata.json` |
| `create_run_recorder`, runtime journal | Root `operation_journal.jsonl`, `run_state.json`; all pump, needle, STOP, cleanup, stage and recovery events |
| `nmr_measurement` → acquisition runner | Root `raw_nmr/`; acquisition helper preserves the instrument retrieval contract and writes its acquisition records alongside retrieved files |
| `base.run_process_fid_postprocessing` | Root `processed_nmr/<acquisition>/`; pipeline CSV/JSON, exported spectra, phase audits and plots; root peak-tracking config |
| `MockProcessFid`, `TrendSimulation.process` | Separate hardcoded root `processed_nmr/` assumptions |
| `untrusted_sample`, solvent and endpoint writers | Root processed placeholder plus `stages/<internal-stage>/solvent_evidence` and `evidence` |
| Measurement CSV and spectrum append | Root `time_series.csv`, `spectra_long.csv`; internal stage column identifies observations |
| `publish_observation` | Root `operator_status.json` |
| Dose transition | Root `transition/channel2_addition.json`; canonical live ledger stays in `runtime/si6_doses/<physical-ID>.json`; mock ledger was local to each run |
| `si6_stage_reports` | `stages/<stage>/time_series.csv`, summary and figures; cross-stage `final/` |
| `si6_final_qc` | `final_qc/` CSV/JSON/report and figures |
| Entry-point post-session `final_nmr_summary` | Root `final_nmr_summary/`; readers assume root journal/manifest/time series/raw/processed directories |
| `finish_run`, previous-run inspection, `recovery.inspect_run` | Assume root status/journal/state; old journals must remain readable |
| Persistent physical state | Needle reference and pending HOME marker outside experiments; canonical dose reservation outside experiments; these must remain shared |

The existing `RunPaths` centralizes some names, but processing, stage reports,
metadata and summary readers independently build others. Raw/processed files are
mixed across stages, final reports are split across three directories, and folder
names do not expose the configured physical experiment ID. Moving only the run
root would leave those assumptions unresolved. Historical folders are not moved
or rewritten by this update. The new centralized layout is documented in
`SI6_EXPERIMENT_OUTPUTS.md` after implementation and validation.
