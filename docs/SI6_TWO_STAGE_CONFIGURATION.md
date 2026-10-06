# Two-stage configuration reference

The authoritative strict schema is implemented by `load_si6_config`,
`_parse_stage`, `pump_channels`, `validate_completion` and `validate_profile`.
Unknown fields fail before services open. This extension uses the existing
initial/first-addition stage and cycle conventions; it adds no alternate engine.

Use `config_templates/experiments/si6_two_stage_nominal.yaml` as the complete
nominal YAML. Copy it to a working experiment config and edit it. The fast
simulation template keeps nominal timing but adds an explicit virtual fixture
backend. Stage1/Stage2 development templates remain full safe two-stage reactions:
edit the named stage for development, with both chemical prerequisites intact.
They do not silently skip a dose or provide partial-run recovery.

| YAML setting | Editable meaning |
|---|---|
| workflow.name / description | Workflow labels. Run folder provides visible dataset identity. |
| workflow.experiment_id | Unique physical reaction identity, stable across inspection/restarts; required for dose replay protection. SET_ placeholder rejected live. |
| workflow.initial_needle_position | Explicit UP; DOWN rejected for this SOP. |
| workflow.cycle[0,2,5,7,8] | Transfer channel, volume_ml, optional rate_ml_min. Nominal 8/5/13/5/5 mL on channel 1. Repeated cycles must balance each channel independently. |
| workflow.cycle[1,6].position | DOWN/UP validated at fixed roles. Geometry is separately configurable in Arduino YAML. |
| workflow.cycle[3].seconds | Pre-acquisition pause, nominal 300 s; independent of cadence. |
| workflow.pump_extra_seconds | Finite metered motion wait margin. |
| pump.default_channel | Restored selection after a transfer, nominal 1. |
| pump.channels.1 / .2 | Independent inner diameter, capacity, initial retained volume, safety margin, rate and default volume. Same serial connection; this profile requires mL/min units to match rate_ml_min fields. |
| nmr.scans | Global acquisition scan count, nominal 8. |
| nmr.route / result_type | Existing acquisition route and required FID result. |
| nmr.receiver_gain / auto_gain | Fixed gain needed for comparable areas; auto_gain must remain false. |
| nmr.spectral_center / sweep_width / target_ppm | Existing acquisition settings and target (nominal 5.8 ppm). |
| analysis.detection_window_ppm | Allowed detected peak distance from target. |
| analysis.integration_window_ppm | Half-width of added fixed integral (target ± value). |
| analysis.plot_window_ppm | Target/noise context window in retained regional arrays. Must contain enough sideband points. |
| analysis.min_peak_snr / min_prominence_snr / min_peak_area | Initial/detected peak QC, in addition to production processor QC. |
| analysis.line_broadening_hz | Existing magnitude evidence/display setting; production processor uses configs/nmr/analysis.yaml processing configuration. |
| analysis.area_epsilon | Existing numerical guard. |
| analysis.plateau_* | Legacy plateau parameters preserved; explicit stage completion rules govern the new profile. |
| analysis.measurement_qc.noise_multiplier | Conservative uncertainty = multiplier × robust sideband noise × target integration width. |
| measurement_qc.max_noise_fraction | Maximum noise relative to the initial detected peak height. |
| measurement_qc.max_area_uncertainty_fraction | Maximum uncertainty relative to initial fixed integral. |
| measurement_qc.undetected_max_fraction | Maximum upper-bound integral for independently bounded undetected signal. |
| workflow.initial_stage / first_addition_stage | Stage name, readiness prompt, interval_minutes, max_hours, measure_immediately, plateau_stopping_enabled. |
| stage.completion.trend | decreasing then increasing; required for this two-stage profile. |
| completion.minimum_points | Minimum valid observations before any candidate check. |
| completion.minimum_duration_hours | Minimum duration from first stage acquisition; distinct from expected/max duration. |
| completion.window_points | Rolling regression/range window size. |
| completion.consecutive_confirmations | Number of overlapping passing windows required; not just one adjacent interval. |
| completion.minimum_progress_fraction | Required net decline or growth before stable completion. |
| completion.low_fraction | Decreasing-stage low upper bound relative to initial fixed integral. |
| completion.max_window_range_fraction | Max uncertainty-expanded span divided by initial area (decreasing) or greatest observed area (increasing); also bounds increasing plateau decline below that observed maximum. |
| completion.max_abs_slope_fraction_per_hour | Max absolute rolling regression slope on the same scale. |
| completion.expected_duration_hours | Optional descriptive expectation, nominal Stage2 3 h; does not cause completion. |
| initial_stage.after_monitoring | Reviewed UP→DOWN→one infusion→UP boundary. Channel-2 volume/rate and needle_position are explicit and editable. Other fluid/NMR actions or multiple doses rejected. |
| output.run_root_dir | Repository-relative results root, resolved from source location. Absolute deliberate output locations remain supported. |
| simulation.fixture_file / acquisition_seconds | Mock-only labeled controller fixtures and virtual acquisition duration. Live selection rejected. |
| machine.chemyx / machine.nmr | Local COM endpoint/baud/timeouts and local instrument LAN RPC address/poll/acquisition timeout. |
| Arduino needle/motion/firmware | UP/DOWN positions, HOME, direction, steps-per-unit, speed/acceleration/travel, state path, transport/version/wiring/calibration. |

Completion fractions are measured on one consistently processed **fixed target
integral**, not on heterogeneous picked-peak integration bounds. Decreasing
normalization uses the first valid positive detected integral. Increasing uses
the greatest observed integral, allowing a near-zero baseline but requiring
later detected growth. Example thresholds require comparison with real WORK
spectra/chemical endpoints. No detector failure or zero-filled peak row alone
provides low-region evidence. No automatic stage-specific phase correction is
introduced. Missing QC/provenance, failed candidates, significant negative
lobes, invalid numbers or non-increasing metadata timing fail closed.

The real `tracked_resonance_phsi4_20260810.dx` fixture demonstrates the production
array/file contract but fails the nominal new uncertainty/noise settings:
fixed integral 30.4923, target height 423.22, sideband noise 14.7393, relative
noise 3.48%, and conservative uncertainty about 29%. Default limits are 1%
and 0.5%. These conservative defaults intentionally remain uncalibrated; their
fitness for Si6 endpoint monitoring requires WORK data/precision validation.
Diagnostic loose-QC adapter acceptance proves software loading only, not a
chemical stopping criterion. Detection QC and fixed-integral uncertainty are
distinct checks.
