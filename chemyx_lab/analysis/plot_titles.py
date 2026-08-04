"""Central dataset-aware title formatting for NMR figures.

Title provenance follows one documented hierarchy:

1. an explicitly configured dataset display name;
2. dataset metadata (when supplied by a caller);
3. a date-like dataset identifier embedded in an input/output path;
4. an input dataset directory or file stem;
5. a run/output directory name;
6. the literal ``"Unspecified dataset"`` as a visible final fallback.

The helper is intentionally independent of matplotlib so every current and
compatible plotting path can use the same convention.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable, Mapping


_DATE_DATASET_RE = re.compile(r"(?<!\d)(\d{2}-\d{2}-\d{2})(?!\d)")
_METADATA_KEYS = (
    "dataset_display_name",
    "dataset_name",
    "sample_name",
    "title",
    "TITLE",
)
_GENERIC_DIRECTORY_NAMES = {
    "analysis",
    "paper",
    "plots",
    "processed",
    "qc",
    "raw",
    "region",
    "slides",
    "statistics",
    "supplementary",
    "target_peak",
}


def format_dataset_plot_title(
    dataset_display_name: str,
    plot_title: str,
) -> str:
    """Return a plot title beginning with the dataset display name.

    Existing correctly-prefixed titles are returned unchanged, making the
    formatter safe to apply in shared helpers and callers simultaneously.
    """

    dataset = " ".join(str(dataset_display_name or "").split())
    title = " ".join(str(plot_title or "").split())
    if not dataset:
        dataset = "Unspecified dataset"
    if not title:
        return dataset
    if title.casefold() == dataset.casefold() or title.casefold().startswith(
        f"{dataset} ".casefold()
    ):
        return title
    return f"{dataset} {title}"


def _paths(values: Iterable[str | Path] | str | Path | None) -> list[Path]:
    if values is None:
        return []
    if isinstance(values, (str, Path)):
        return [Path(values)]
    return [Path(value) for value in values]


def _date_identifier(paths: Iterable[Path]) -> str | None:
    for path in paths:
        match = _DATE_DATASET_RE.search(str(path))
        if match:
            return match.group(1)
    return None


def _path_identifier(path: Path, *, prefer_input: bool) -> str | None:
    candidates: list[str] = []
    if prefer_input:
        if path.suffix:
            candidates.extend((path.parent.name, path.stem))
        else:
            candidates.append(path.name)
    else:
        base = path.parent if path.suffix else path
        candidates.append(base.name)
        candidates.extend(part.name for part in base.parents)
    for value in candidates:
        cleaned = value.strip()
        if cleaned and cleaned.casefold() not in _GENERIC_DIRECTORY_NAMES:
            return cleaned
    return None


def resolve_dataset_display_name(
    configured_name: str | None = None,
    *,
    metadata: Mapping[str, object] | None = None,
    input_paths: Iterable[str | Path] | str | Path | None = None,
    output_path: str | Path | None = None,
) -> str:
    """Resolve one authoritative display name using the documented hierarchy."""

    if configured_name is not None and str(configured_name).strip():
        return str(configured_name).strip()
    if metadata:
        for key in _METADATA_KEYS:
            value = metadata.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()

    inputs = _paths(input_paths)
    outputs = _paths(output_path)
    dated = _date_identifier([*inputs, *outputs])
    if dated:
        return dated
    for path in inputs:
        if value := _path_identifier(path, prefer_input=True):
            return value
    for path in outputs:
        if value := _path_identifier(path, prefer_input=False):
            return value
    return "Unspecified dataset"


def dataset_plot_title(
    plot_title: str,
    *,
    configured_name: str | None = None,
    metadata: Mapping[str, object] | None = None,
    input_paths: Iterable[str | Path] | str | Path | None = None,
    output_path: str | Path | None = None,
) -> str:
    """Resolve the dataset name and format a visible plot title in one call."""

    return format_dataset_plot_title(
        resolve_dataset_display_name(
            configured_name,
            metadata=metadata,
            input_paths=input_paths,
            output_path=output_path,
        ),
        plot_title,
    )
