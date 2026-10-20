"""Reopen exported benchmark results for independent file audits and easy review."""
from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path
from urllib.parse import quote

import numpy as np
from PIL import Image

from .scipy_peak_benchmark_plots import FigureWriter, observation_comparison


def read_rows(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def numerical_row(row):
    row = dict(row)
    for key in ("peak_center_ppm", "peak_width_ppm", "prominence", "snr", "boundary_low_ppm", "boundary_high_ppm",
                "area_dynamic", "area_signed", "area_fixed", "elapsed_hours", "area_difference", "relative_difference_percent"):
        if row.get(key):
            row[key] = float(row[key])
    row["integration_valid"] = row["integration_valid"] == "True"
    row["qc_flags"] = json.loads(row["qc_flags"])
    return row


def finalize_review(output):
    """No Fourier reprocessing: audit saved CSV/PNG files and add review links."""
    output = Path(output).resolve()
    rows = [numerical_row(r) for r in read_rows(output / "cross_dataset_comparison/area_comparison.csv")]
    metadata = read_rows(output / "acquisition_metadata.csv")
    writer = FigureWriter(output)
    reference_profile = "prominence_8"
    for item in metadata:
        dataset, observation = item["dataset"], item["observation"]
        path = output / dataset / "processed_spectra" / f"{observation}.npz"
        arrays = np.load(path)
        provenance = json.loads(path.with_name(path.stem + "_provenance.json").read_text(encoding="utf-8"))
        spectrum = {"ppm": arrays["ppm"], "intensity": arrays["intensity"], "regional_ppm": arrays["regional_ppm"],
            "regional_intensity": arrays["regional_intensity"], "noise": provenance["noise"],
            "observation": observation, "acquisition_time": item["acquisition_time"]}
        selected = [r for r in rows if r["dataset"] == dataset and r["observation"] == observation]
        observation_comparison(writer, dataset, spectrum, selected, configuration=reference_profile)
    manifest = read_rows(output / "plot_manifest.csv") + writer.manifest
    from .scipy_peak_benchmark import write_csv, write_json
    write_csv(output / "plot_manifest.csv", manifest)
    # Read the serialized coordinates rather than trusting only in-memory values.
    coordinate_audits = []
    for row in rows:
        if not row["integration_valid"]:
            continue
        coordinates = np.loadtxt(row["integration_coordinates_csv"], delimiter=",", skiprows=1)
        order = np.argsort(coordinates[:, 0])
        coordinates = coordinates[order]
        area = float(np.trapezoid(coordinates[:, 3], coordinates[:, 0]))
        signed = float(np.trapezoid(coordinates[:, 1] - coordinates[:, 2], coordinates[:, 0]))
        integrand_error = float(np.max(abs(coordinates[:, 3] - np.maximum(coordinates[:, 1] - coordinates[:, 2], 0))))
        error = abs(area - row["area_dynamic"])
        signed_error = abs(signed - row["area_signed"])
        if error > 1e-8 or signed_error > 1e-8 or integrand_error > 1e-10:
            raise AssertionError("Serialized coordinates do not reproduce reported/shaded area")
        if row["method"] not in {"fixed", "production_variable"}:
            if abs(coordinates[0, 0] - row["boundary_low_ppm"]) > 1e-10 or abs(coordinates[-1, 0] - row["boundary_high_ppm"]) > 1e-10:
                raise AssertionError("Interpolated plotted endpoints differ from reported dynamic boundaries")
        coordinate_audits.append({"dataset": row["dataset"], "observation": row["observation"], "configuration": row["configuration"],
            "method": row["method"], "csv_area": area, "area_error": error, "signed_error": signed_error, "integrand_error": integrand_error, "passed": True})
    write_csv(output / "serialized_coordinate_audit.csv", coordinate_audits)
    references = {(r["dataset"], r["observation"]): r for r in rows if r["configuration"] == "reference" and r["method"] == "width_95"}
    associations = []
    for row in rows:
        if row["method"] != "width_95":
            continue
        reference = references[(row["dataset"], row["observation"])]
        delta = row["peak_center_ppm"] - reference["peak_center_ppm"]
        associations.append({"dataset": row["dataset"], "observation": row["observation"], "configuration": row["configuration"],
            "center_ppm": row["peak_center_ppm"], "reference_profile_center_ppm": reference["peak_center_ppm"],
            "difference_ppm": delta, "center_disagreement_over_002ppm": math.isfinite(delta) and abs(delta) > .02,
            "note": "Profile agreement diagnostic, not chemical-identity ground truth"})
    write_csv(output / "candidate_association_audit.csv", associations)
    png_audits = []
    for record in manifest:
        with Image.open(record["file"]) as im:
            if im.info.get("Title") != record["visible_title"] or record["dataset"] not in record["visible_title"]:
                raise AssertionError("Image/manifest dataset title mismatch")
            im.thumbnail((200, 150))
            contrast = float(np.std(np.asarray(im.convert("L"))))
            if contrast < 5:
                raise AssertionError("Empty or effectively blank PNG")
            png_audits.append({"file": record["file"], "dataset": record["dataset"], "visible_title": record["visible_title"],
                               "thumbnail_contrast": contrast, "passed": True})
    write_csv(output / "png_review_audit.csv", png_audits)
    verification = json.loads((output / "verification.json").read_text(encoding="utf-8"))
    verification.update(total_plots=len(manifest), serialized_coordinate_audits=len(coordinate_audits),
                        nonblank_png_audits=len(png_audits), additional_high_prominence_comparisons=len(writer.manifest))
    write_json(output / "verification.json", verification)
    # An ordinary static index makes thousands of PNGs usable without Python.
    def link(relative, text):
        return f'<a href="{quote(str(relative).replace(chr(92), "/"))}">{html.escape(text)}</a>'
    dataset_names = " + ".join(sorted(set(r["dataset"] for r in metadata)))
    lines = ['<!doctype html><meta charset="utf-8"><title>NMR boundary review</title>',
        '<style>body{font:16px system-ui;margin:32px;max-width:1400px}td,th{padding:9px;border:1px solid #ddd}table{border-collapse:collapse}a{color:#075c9b}h2{margin-top:38px}</style>',
        '<h1>' + html.escape(dataset_names) + ' NMR boundary review</h1>',
        '<p>Saved real data. No instruments. All dynamic areas use the unchanged ACME/ALS arrays.</p>',
        '<p>' + link('cross_dataset_comparison/findings.md', 'Findings and recommendation') + ' · ' +
        link('cross_dataset_comparison/parameter_ranking.csv', 'Parameter ranking CSV') + ' · ' + link('verification.json', 'Verification') + '</p>',
        '<p>Default comparisons use prominence ≥5σ; conservative comparisons use ≥8σ. Missing/ambiguous results are shown explicitly. Boundary/area ground truth is unavailable.</p>']
    for dataset in sorted(set(r["dataset"] for r in metadata)):
        lines += [f'<h2>{html.escape(dataset)}</h2><p>']
        for filename, label in [('area_vs_time', 'Area vs time'), ('dynamic_vs_fixed', 'Difference from fixed'),
            ('center_vs_time', 'Chemical shift'), ('boundaries_vs_time', 'Boundaries'),
            ('peak_width_vs_time', 'Peak width'), ('spectrum_overlays', 'Spectrum overlays')]:
            lines.append(link(Path(dataset) / 'time_series' / (filename + '.png'), label) + ' · ')
        lines.append('</p><table><tr><th>Acquisition (JCAMP)</th><th>Observation</th><th>Default six methods</th><th>Conservative six methods</th><th>95% prominence width</th><th>Adaptive noise return</th></tr>')
        for row in metadata:
            if row["dataset"] != dataset:
                continue
            obs = row["observation"]
            base = Path(dataset)
            lines.append('<tr><td>' + html.escape(row["acquisition_time"]) + '</td><td>' + obs + '</td><td>' +
                link(base / 'parameter_comparisons' / (obs + '_methods.png'), 'Open default') + '</td><td>' +
                link(base / 'parameter_comparisons' / (obs + '_methods_prominence_8.png'), 'Open ≥8σ') + '</td><td>' +
                link(base / 'individual_spectra' / obs / 'prominence_8/width_95.png', 'Open boundaries') + '</td><td>' +
                link(base / 'individual_spectra' / obs / 'prominence_8/adaptive_noise.png', 'Open boundaries') + '</td></tr>')
        lines.append('</table>')
    (output / "review_index.html").write_text('\n'.join(lines), encoding="utf-8")
    return verification
