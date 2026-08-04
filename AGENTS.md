# Repository agent guidance

## Dataset-aware plot titles

Every saved experimental-data figure must visibly identify its source dataset.
For a single-panel plot, use `<dataset display name> <descriptive title>`. For a
multi-panel figure, put the dataset identity in the figure-level suptitle;
panel titles may then remain concise. A dataset identifier in the filename
alone is insufficient.

For NMR processing, use the existing `statistics.dataset_display_name` or
`target_peak.dataset_display_name` value in `configs/nmr/analysis.yaml` when it
is available. Otherwise resolve authoritative metadata, the input dataset
directory, or the output run directory using
`chemyx_lab.analysis.plot_titles`; never hard-code a run name in reusable plot
code. Use `format_dataset_plot_title` so whitespace is normalized and an
existing prefix is not duplicated. All formats of one figure (PNG, SVG, and
PDF) must carry the same visible title, and manifests must record the same
dataset identity and visible title.

Correct examples:

```text
06-09-26 Focused Spectral Evolution
06-09-26 Area vs Time
TEST-RUN-001 Completion Detection
```

Incorrect examples:

```text
Focused Spectral Evolution
Area vs Time
06-09-26 06-09-26 Area vs Time
```

Any new experimental plotting path requires a test of this title convention.

## Authoritative NMR timing

Treat JCAMP acquisition metadata as the authoritative NMR time source. For
NMReady data, `LONG DATE` is preferred by the existing timestamp hierarchy.
A filename token such as `sequence-1015` is a nominal `HHMM` schedule label,
not an actual acquisition timestamp. Never silently substitute filename time
or file modification time in a plot presented as metadata timing. Timing
comparison plots must expose their source fields, use one shared CSV table, and
fail closed when no metadata timestamp is available.
