# Before-change synthetic-path audit

Inspected on HOME before adding the final processed-spectrum validator. The
independent reviewer separately recorded the same finding in
[final HOME report](SI6_FINAL_HOME_VALIDATION_REPORT.md).

| Requested audit question | Existing implementation |
|---|---|
| Existing synthetic fixtures | `chemyx_lab/testing/fixtures/si6_two_stage_trends.json`; real DX carrier `tracked_resonance_phsi4_20260810.dx`. |
| Trend fixture | JSON labeled `simulation_only`; separate Stage1 decreasing/Stage2 increasing arrays. |
| Simulated measurement creation | `workflows/si6_simulation.py:TrendSimulation` copies DX, substitutes virtual LONG DATE, writes `SIMULATION_ONLY.json`. |
| Numbers, processed spectra or raw FID? | Numbers injected directly by `analyze`; copied raw FID is unchanged. No generated processed spectrum and no synthetic raw-FID processing. |
| Normal production adapter | `Services.nmr_measurement` calls production processor, reads LONG DATE, `analyze_tracked_resonance`, `fixed_window_measurement`, `completion_evidence`. |
| 5.8ppm measurement | Production `process_fid.process_spectrum_for_peaks` and restricted simple table, then workflow tracked adapter. |
| Peak integration | `analysis/nmr.py:pick_spectrum_region`; consistent endpoint integral additionally uses retained `regional_quantitative` in `stage_measurement.py`. |
| QC | `process_fid._peak_qc`, tracked adapter thresholds; fixed-window hash/phase/noise/uncertainty/negative-lobe/absence guards. |
| Stage1 completion | `analysis/stage_completion.py:completion_evidence`, decreasing normalization to initial valid detected area. |
| Stage2 completion | Same function with independent Stage2 history, increasing normalization to greatest observed area. |
| Transition | Shared `run_experiment` checks sustained completion again, finishes cleanup, executes configured boundary. |
| One-time dose | `dose_guard.py`, durable dispatch intent, channel-specific completed move/STOP, journal replay, `dose_confirmed` receipt before Stage2. |
| Does existing simulation prove production spectral analysis? | No. It bypasses fixed-window measurement and supplies synthetic QC/areas. It proves controller sequencing and numeric endpoint behavior only. |

The new validator adds a separate mock-only processed-spectrum adapter. It reuses
existing production baseline/detection/integration/QC helpers and the shared
controller. Production raw/phase processing, pump, Arduino, workflow and endpoint
algorithms are unchanged. Validation uncovered one profile-admission bug: all
confirmation windows must meet the observation minimum, so schedules capped at
8/9 points cannot satisfy the nominal8-point/3-confirmation rule. Profile validation
now requires at least10 slots and rejects those impossible configurations before
opening transports. Endpoint criteria and all shipped YAMLs remain unchanged.
The new validator labels the copied DX as a metadata carrier and the
measured arrays as synthetic processed inputs. No full vendor FID generator is
introduced; these levels do not validate raw decoding/FFT/phase optimization.
