> Historical audit of the implementation before the tracked-area refactor.
> Its integral descriptions and source line links do not describe current Stage 1/2 behavior.
> See [current implementation](../SI6_TRACKED_PEAK_IMPLEMENTATION.md).

# Si6 continuation audit — before implementation

HOME inspection, 2026-10-06. No instruments are connected. This audit precedes
the two-stage implementation; independent findings are in
`docs/SI6_TWO_STAGE_VALIDATION_REPORT.md`.

| Question | Existing implementation |
|---|---|
| Architecture | `scripts/02_si6_experiment.py` calls shared `three_instrument_si6.Services`; the diagnostic uses the same pump, needle, NMR and journal interfaces. |
| Stages | `Stage`, `build_stages`, initial/first-addition/repeating stages; a monotonic start-to-start scheduler and stage limits already exist. |
| Channels | `pump_channels.py`, `pump.default_channel`, independent `pump.channels`, transfer overrides, independent retained estimates/totals/motion, STOP on all configured channels. |
| Needle | Logical UP/DOWN map to Arduino configuration; `TrackedNeedle` preserves commanded state and uncertainty. Sampling checks UP, lowers before liquid sampling, returns liquid DOWN, raises before clearing. |
| Acquisition | Existing local NMR RPC/iFlow acquisition retrieves a JCAMP FID; live and mock share orchestration. |
| Scans | Workflow `nmr.scans`, propagated through `NmrSettings`. |
| Timing | Cycle pause seconds, stage interval minutes/max hours; acquisition timeout is separate machine configuration. Mock previously replaced cadence/count. |
| Spectrum analysis | Production `process_fid.py`, existing phase correction/baseline/peak QC, tracked-window CSVs; additive retained phase arrays already exist. |
| Target | Workflow `nmr.target_ppm` and analysis detection/integration windows, currently 5.8 ppm in three-instrument examples. |
| Time series | Combined time_series.csv and spectra_long.csv; LONG DATE acquisition metadata is authoritative in integrated runner. |
| Completion | Global adjacent percent plateau band, consecutive intervals; no explicit direction, low region, minimum duration or sustained progress prerequisite. |
| Recovery | Append-only fsynced journal, replayed state, inspection and fail-closed uncertain moves; automatic run resumption is not implemented. Demo startup only warns about prior unresolved runs. |
| Outputs | Timestamped run directories, raw_nmr, processed_nmr, journal, snapshots, CSV, manifest and offline final summary. |
| Paths | Repository root derived from source location. Relative output paths currently depend on working directory. Acquisition processing names truncate stems and can collide. |
| External resources | Installed Python/dependencies, serial drivers, Arduino firmware/calibration and local NMR RPC software. No cloud model needed. Offline wheel/installer/toolchain payload exists as ignored files and must be copied separately from a Git pull. |
| Config roles | `configs/experiments` shared starting workflows, `config_templates/experiments` editable examples, `configs/nmr/analysis.yaml` processor configuration; machine and Arduino local YAML are tracked shared starting points, requiring per-rig review. |

## Portability classification

Repository resources must resolve from the derived repository root, including
output, processing configuration, fixtures, and templates. HOME command examples
and historical result provenance containing absolute paths are documentation or
audit records, not runtime dependencies; historical results must remain intact.
Optional external phase-comparator download scripts are development tools, not
part of experiment startup. Optional GUI system fonts are OS resources. Local
NMR RPC addresses are instrument LAN endpoints, not internet/cloud dependencies.
Driver and vendor executable locations belong in local machine setup. Offline
wheelhouse/installers/drivers/Arduino toolchain are transfer payload, not Git
source. Copying a virtual environment is not a portable installation method.

## Smallest planned changes

Add explicit per-stage conservative completion criteria and evidence, preserve
production spectral processing, derive fixed-window measurements from retained
corrected arrays, add a durable experiment/dose replay barrier before transports,
and supplement existing templates and reporting. Use the existing scheduler with
a labeled virtual simulation clock. Keep legacy workflows and their behavior.
Physical volume, geometry, calibration, serial acknowledgements and NMR response
remain WORK verification items.

## Post-implementation path classification

Source/config/template scan for drive roots, user-profile paths, Desktop,
OneDrive, Downloads and HTTP URLs found no HOME path in required experiment
source/config. `nmr.py` and reference/metadata audit scripts contain literature
URLs as attribution only. `audit_external_phase_sources.py` downloads optional
development comparator assets and is outside startup/normal runtime.
`verify_additional_phase_package.py` verifies those optional downloaded audit
assets; this is not an experiment dependency. `phase5.py` and
`validation_phase_gui.py` use an optional Windows Arial system-font path with
fallback, classified as an OS resource. GUI DesktopServices identifiers and
ordinary strings ending `:\\n` are scan false positives, not drive paths.
Documentation command examples and historical acquisition/provenance paths
remain intentionally unchanged. Local RPC network addresses are instrument LAN
settings, not internet assumptions. Relative result roots and missing optional
Git provenance were corrected; acquisition folder collisions now fail closed.

No machine-specific executable or external calibration file was newly required.
The same offline dependency lock remains in use. Ignored offline payload and
rig state are deliberately documented as hard-drive transfer requirements.
