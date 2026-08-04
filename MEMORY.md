# Project memory

## Scientific visualization preference

The user expects every experimental-data plot to visibly show the dataset or
experiment name in its title so figures can be identified during presentations
without relying on filenames or surrounding context. This is a project-wide
preference, not a rule specific to one NMR run.

NMR code implements the preference through
`chemyx_lab.analysis.plot_titles.format_dataset_plot_title`. Single-panel plots
prefix their descriptive title; multi-panel figures use a dataset-aware
suptitle. Reusable functions must receive or resolve the dataset from
configuration, metadata, or dataset/run paths and must not hard-code it.

The user also expects timing figures to distinguish nominal filename/schedule
time from actual recorded event time. For offline NMR, JCAMP metadata time is
authoritative; `sequence-HHMM` is only a nominal filename label. Plots should
preserve real irregular acquisition intervals and make the timing source clear.
