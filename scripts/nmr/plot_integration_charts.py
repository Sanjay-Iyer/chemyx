"""Manual vs. automated peak-area charts, per acquisition day.

Three families of slide-ready PNGs, one per day:

* ``manual_integration_<DD>.png``    -- hand-integrated areas from the workbook
* ``automated_integration_<DD>.png`` -- ``integrated_area`` from the peak CSVs
* ``integration_comparison_<DD>.png`` -- both series, each normalized to its own
  daily maximum, so the *shape* of the two curves can be compared on one axis
  (the raw scales differ by ~6x, and a dual y-axis would misstate the data)

Manual rows carry a ``Day-Time`` label (``DD-HHMM``: ``08-1115`` = the 8th,
11:15).  Automated rows carry a real acquisition ``timestamp`` plus a
``sequence-HHMM`` tag in the filename that matches the manual label, so the two
sources join exactly on (day, sequence tag).

Usage::

    # Defaults: workbook + *_peaks_simple.csv in results/runs/manual
    python scripts/nmr/plot_integration_charts.py

    # Only some of the families
    python scripts/nmr/plot_integration_charts.py --only comparison

    # Other inputs / output directory
    python scripts/nmr/plot_integration_charts.py --workbook book.xlsx \
        --csv-dir results/raw/nmr --out-dir results/figures
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import PathPatch, Patch
from matplotlib.path import Path as MplPath
from matplotlib.ticker import FuncFormatter

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chemyx_lab.analysis.plot_titles import (
    format_dataset_plot_title,
    resolve_dataset_display_name,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "results" / "runs" / "manual"
DEFAULT_WORKBOOK = DEFAULT_DIR / "06-12-26_peaks_simple_08-12_all.xlsx"
DEFAULT_SHEET = "cailyn_data_processing"
CSV_GLOB = "*_peaks_simple.csv"

# Palette: slot 1 / slot 2 of the categorical order (validated adjacent pair).
SURFACE = "#fcfcfb"
AUTOMATED = "#2a78d6"   # blue
MANUAL = "#eb6834"      # orange
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

FONT_STACK = ["Segoe UI", "DejaVu Sans", "sans-serif"]

DPI = 200
MAX_BAR_PX = 24.0      # bar thickness cap, in CSS px (96 dpi)
CORNER_PX = 4.0        # rounded data-end radius, in CSS px
GAP_PX = 2.0           # surface gap between touching bars, in CSS px
PX = DPI / 96.0        # CSS px -> render px at our output resolution

# "sequence-1115", "sequence 1645", "sequence flow 1545" all carry the same tag
SEQ_RE = re.compile(r"sequence[^0-9]*(\d{2})(\d{2})")


# --------------------------------------------------------------------------
# input
# --------------------------------------------------------------------------

def read_manual(workbook: Path, sheet: str) -> pd.DataFrame:
    """Manual integrations: Day-Time/Peaks -> day, clock, seq, area."""
    df = pd.read_excel(workbook, sheet_name=sheet)
    cols = {str(c).strip().lower(): c for c in df.columns}
    try:
        day_col, peak_col = cols["day-time"], cols["peaks"]
    except KeyError:
        raise SystemExit(
            f"sheet '{sheet}' needs 'Day-Time' and 'Peaks' columns; "
            f"found {list(df.columns)}"
        )

    out = df[[day_col, peak_col]].rename(
        columns={day_col: "day_time", peak_col: "area"}
    ).dropna(subset=["day_time", "area"])
    out["day_time"] = out["day_time"].astype(str).str.strip()

    parts = out["day_time"].str.extract(r"^(\d{2})-(\d{2})(\d{2})$")
    bad = out.loc[parts.isna().any(axis=1), "day_time"].tolist()
    if bad:
        raise SystemExit(f"unparseable Day-Time values (want DD-HHMM): {bad}")

    out["day"] = parts[0]
    out["seq"] = parts[1] + parts[2]
    out["clock"] = parts[1] + ":" + parts[2]
    out["sort_key"] = parts[1].astype(int) * 60 + parts[2].astype(int)
    return out[["day", "seq", "clock", "area", "sort_key"]].sort_values(
        ["day", "sort_key"]
    ).reset_index(drop=True)


def read_automated(csv_paths: list[Path]) -> pd.DataFrame:
    """Automated integrations: integrated_area vs. acquisition timestamp."""
    frames = []
    for path in csv_paths:
        df = pd.read_csv(path)
        missing = {"file", "timestamp", "integrated_area"} - set(df.columns)
        if missing:
            raise SystemExit(f"{path.name} is missing column(s): {sorted(missing)}")
        frames.append(df)

    data = pd.concat(frames, ignore_index=True)
    stamp = pd.to_datetime(data["timestamp"])

    out = pd.DataFrame({
        "day": stamp.dt.strftime("%d"),
        "date_label": stamp.dt.strftime("%m-%d-%y"),
        "month_label": stamp.dt.strftime("%B %Y"),
        # x values come from the real acquisition time, per the timestamp column
        "clock": stamp.dt.strftime("%H:%M"),
        "sort_key": stamp.dt.hour * 60 + stamp.dt.minute,
        "area": data["integrated_area"].astype(float),
    })
    # The sequence tag in the filename is the nominal slot the manual sheet
    # labels its rows with -- the join key between the two sources.
    seq = data["file"].astype(str).str.extract(SEQ_RE)
    out["seq"] = (seq[0] + seq[1]).where(seq.notna().all(axis=1))
    return out.dropna(subset=["area"]).sort_values(
        ["day", "sort_key"]
    ).reset_index(drop=True)


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    rank_a = pd.Series(a).rank().to_numpy()
    rank_b = pd.Series(b).rank().to_numpy()
    return _pearson(rank_a, rank_b)


def pair_day(manual: pd.DataFrame, automated: pd.DataFrame) -> pd.DataFrame:
    """Join one day's two sources on the sequence tag, else on time order."""
    if automated["seq"].notna().all() and set(manual["seq"]) == set(automated["seq"]):
        merged = manual.merge(
            automated[["seq", "clock", "area"]], on="seq",
            suffixes=("_manual", "_automated"),
        )
        return merged.sort_values("sort_key").reset_index(drop=True)

    if len(manual) != len(automated):
        raise SystemExit(
            f"cannot pair day {manual['day'].iloc[0]}: sequence tags differ and "
            f"row counts do not match ({len(manual)} manual vs "
            f"{len(automated)} automated)"
        )
    print(
        f"  note: day {manual['day'].iloc[0]} sequence tags did not line up; "
        f"pairing by time order instead"
    )
    merged = manual.reset_index(drop=True).rename(
        columns={"clock": "clock_manual", "area": "area_manual"}
    )
    merged["clock_automated"] = automated["clock"].to_numpy()
    merged["area_automated"] = automated["area"].to_numpy()
    return merged


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

def _px_to_data(ax, px: float) -> tuple[float, float]:
    """Convert a pixel length to (x, y) lengths in data units."""
    inv = ax.transData.inverted()
    x0, y0 = inv.transform((0.0, 0.0))
    x1, y1 = inv.transform((px, px))
    return abs(x1 - x0), abs(y1 - y0)


def _rounded_bar(ax, x: float, height: float, width: float,
                 rx: float, ry: float, color: str) -> None:
    """Column with a rounded cap and square feet on the baseline."""
    if height <= 0:
        return
    left, right = x - width / 2.0, x + width / 2.0
    rx = min(rx, width / 2.0)
    ry = min(ry, height)

    verts = [
        (left, 0.0),
        (left, height - ry),
        (left, height), (left + rx, height),          # top-left corner
        (right - rx, height),
        (right, height), (right, height - ry),        # top-right corner
        (right, 0.0),
        (left, 0.0),
    ]
    codes = [
        MplPath.MOVETO,
        MplPath.LINETO,
        MplPath.CURVE3, MplPath.CURVE3,
        MplPath.LINETO,
        MplPath.CURVE3, MplPath.CURVE3,
        MplPath.LINETO,
        MplPath.CLOSEPOLY,
    ]
    ax.add_patch(
        PathPatch(MplPath(verts, codes), facecolor=color, edgecolor="none",
                  zorder=3)
    )


def _new_figure(n: int, title: str, subtitle: str | None = None,
                subtitle2: str | None = None, min_width: float = 9.0) -> tuple:
    """Slide-shaped figure with a title block that never touches the plot."""
    rotate = n > 10
    width_in = max(min_width, min(13.5, 3.4 + 0.46 * n))
    height_in = width_in * 0.56           # 16:9, the shape a slide wants
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    # Fixed margins in inches, so the title block never lands on the plot.
    if subtitle is None:
        block_in = 1.0                    # title only, plus room for the legend
    else:
        block_in = 1.15 if subtitle2 is None else 1.55
    fig.subplots_adjust(
        left=1.05 / width_in,
        right=1.0 - 0.35 / width_in,
        top=1.0 - block_in / height_in,
        bottom=(1.25 if rotate else 0.95) / height_in,
    )

    title_x = 1.05 / width_in
    fig.text(title_x, 1.0 - 0.30 / height_in, title, color=INK_PRIMARY,
             fontsize=25, fontweight="semibold", ha="left", va="top")
    if subtitle:
        fig.text(title_x, 1.0 - 0.66 / height_in, subtitle, color=INK_MUTED,
                 fontsize=13, ha="left", va="top")
    if subtitle2:
        fig.text(title_x, 1.0 - 0.94 / height_in, subtitle2, color=INK_MUTED,
                 fontsize=13, ha="left", va="top")
    return fig, ax, rotate, height_in


def _style_axes(ax, labels, rotate: bool, ylabel: str) -> None:
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1.0, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.spines["bottom"].set_linewidth(1.0)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45 if rotate else 0,
                       ha="right" if rotate else "center")
    ax.tick_params(axis="both", length=0, colors=INK_MUTED, labelsize=12)
    ax.set_ylabel(ylabel, color=INK_SECONDARY, fontsize=14, labelpad=12)
    ax.set_xlabel("Sample time (24 h)", color=INK_SECONDARY, fontsize=13,
                  labelpad=10)


def plot_single(day: str, frame: pd.DataFrame, kind: str, color: str,
                out_dir: Path, ylim_top: float | None, label_all: bool,
                *, dataset_display_name: str, date_label: str,
                month_label: str) -> Path:
    """One day, one source: peak area vs. sample time."""
    n = len(frame)
    values = frame["area"].to_numpy(dtype=float)
    source = "Automated" if kind == "automated" else "Manual"

    fig, ax, rotate, _ = _new_figure(
        n, format_dataset_plot_title(
            dataset_display_name, f"{source} Integration {date_label}"
        ),
        f"{month_label} · day {int(day)} · {n} samples",
    )
    ax.set_xlim(-0.7, n - 0.3)
    ax.set_ylim(0.0, ylim_top if ylim_top is not None else float(values.max()) * 1.18)
    _style_axes(ax, frame["clock"], rotate, "Area")

    fig.canvas.draw()  # freeze transforms before converting px -> data units
    bar_width = min(0.62, _px_to_data(ax, MAX_BAR_PX * PX)[0])
    rx, ry = _px_to_data(ax, CORNER_PX * PX)
    for i, value in enumerate(values):
        _rounded_bar(ax, i, value, bar_width, rx, ry, color)

    # Label selectively: the peak carries the story, the axis carries the rest.
    peak_i = int(values.argmax())
    pad = ax.get_ylim()[1] * 0.022
    for i, value in enumerate(values):
        if not label_all and i != peak_i:
            continue
        ax.text(i, value + pad, f"{value:.2f}", ha="center", va="bottom",
                color=INK_PRIMARY, fontsize=13 if i == peak_i else 11,
                fontweight="semibold" if i == peak_i else "normal")

    out_path = out_dir / f"{kind}_integration_{day}.png"
    fig.savefig(out_path, facecolor=SURFACE, dpi=DPI)
    plt.close(fig)
    return out_path


def plot_comparison(day: str, paired: pd.DataFrame, out_dir: Path,
                    minimal: bool = False, *, dataset_display_name: str,
                    date_label: str, month_label: str) -> tuple[Path, float, float]:
    """Both sources for one day, each scaled to its own daily maximum.

    The raw scales differ by roughly 6x, so plotting them against a shared raw
    axis would flatten the manual series and a second y-axis would misstate
    both.  Normalizing each to its own day maximum keeps one axis and puts the
    question actually being asked -- do the two rise and fall together? -- in
    the geometry.

    ``minimal`` strips the subtitle block (sample count, correlations, day
    maxima) down to a dated title, for slides that carry that context in the
    surrounding text.  The correlations are still computed and returned.
    """
    auto_raw = paired["area_automated"].to_numpy(dtype=float)
    man_raw = paired["area_manual"].to_numpy(dtype=float)
    n = len(paired)

    auto_norm = auto_raw / auto_raw.max() if auto_raw.max() else auto_raw
    man_norm = man_raw / man_raw.max() if man_raw.max() else man_raw
    r = _pearson(auto_raw, man_raw)
    rho = _spearman(auto_raw, man_raw)

    if minimal:
        fig, ax, rotate, _ = _new_figure(
            n, format_dataset_plot_title(
                dataset_display_name,
                f"Automated vs Manual Integration {date_label}",
            ),
            min_width=10.5,
        )
    else:
        fig, ax, rotate, _ = _new_figure(
            n, format_dataset_plot_title(
                dataset_display_name,
                f"Automated vs Manual Integration {date_label}",
            ),
            f"{month_label} · day {int(day)} · {n} paired samples · "
            f"Pearson r = {r:.2f} · Spearman ρ = {rho:.2f}",
            f"day max: {auto_raw.max():.2f} automated · {man_raw.max():.2f} manual "
            f"(each series scaled to its own maximum)",
            min_width=10.5,
        )
    ax.set_xlim(-0.7, n - 0.3)
    ax.set_ylim(0.0, 1.06)
    _style_axes(ax, paired["clock_automated"], rotate,
                "Area" if minimal else "Area (% of day max)")
    ax.set_yticks([0.0, 0.25, 0.5, 0.75, 1.0])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v * 100:.0f}%"))

    fig.canvas.draw()  # freeze transforms before converting px -> data units
    gap = _px_to_data(ax, GAP_PX * PX)[0]
    bar_width = min(0.30, _px_to_data(ax, MAX_BAR_PX * PX)[0])
    rx, ry = _px_to_data(ax, CORNER_PX * PX)
    offset = (bar_width + gap) / 2.0

    for i in range(n):
        _rounded_bar(ax, i - offset, auto_norm[i], bar_width, rx, ry, AUTOMATED)
        _rounded_bar(ax, i + offset, man_norm[i], bar_width, rx, ry, MANUAL)

    # Legend rides just above the frame so it can never sit on a bar.
    ax.legend(
        handles=[Patch(facecolor=AUTOMATED, label="Automated"),
                 Patch(facecolor=MANUAL, label="Manual")],
        loc="lower right", bbox_to_anchor=(1.0, 1.01), borderaxespad=0.0,
        frameon=False, fontsize=13, ncol=2,
        handlelength=1.1, handleheight=1.1, labelcolor=INK_SECONDARY,
    )

    out_path = out_dir / f"integration_comparison_{day}.png"
    fig.savefig(out_path, facecolor=SURFACE, dpi=DPI)
    plt.close(fig)
    return out_path, r, rho


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Per-day bar charts of manual and automated peak areas, "
                    "plus a normalized comparison of the two."
    )
    parser.add_argument(
        "--workbook", type=Path, default=DEFAULT_WORKBOOK,
        help=f"xlsx with Day-Time/Peaks columns (default: {DEFAULT_WORKBOOK.name})",
    )
    parser.add_argument(
        "--sheet", default=DEFAULT_SHEET,
        help=f"worksheet holding the manual values (default: {DEFAULT_SHEET})",
    )
    parser.add_argument(
        "--csv-dir", type=Path, default=DEFAULT_DIR,
        help=f"directory searched for {CSV_GLOB} (default: alongside the workbook)",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="where to write PNGs (default: alongside the workbook)",
    )
    parser.add_argument(
        "--only", choices=("manual", "automated", "comparison"), action="append",
        help="restrict output to these chart families (repeatable)",
    )
    parser.add_argument(
        "--shared-ylim", action="store_true",
        help="one y-scale per source across all days, so days compare directly",
    )
    parser.add_argument(
        "--label-all", action="store_true",
        help="print the value on every bar (default: peak bar only)",
    )
    parser.add_argument(
        "--day", action="append",
        help="restrict output to these days, as DD (repeatable)",
    )
    parser.add_argument(
        "--minimal", action="store_true",
        help="comparison charts carry a dated title only, no subtitle block",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    workbook = args.workbook.resolve()
    if not workbook.is_file():
        raise SystemExit(f"workbook not found: {workbook}")

    csv_paths = sorted(args.csv_dir.resolve().glob(CSV_GLOB))
    if not csv_paths:
        raise SystemExit(f"no {CSV_GLOB} files under {args.csv_dir.resolve()}")

    out_dir = (args.out_dir or workbook.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_display_name = resolve_dataset_display_name(
        input_paths=[workbook, args.csv_dir.resolve()]
    )
    families = set(args.only or ("manual", "automated", "comparison"))

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = FONT_STACK

    manual = read_manual(workbook, args.sheet)
    automated = read_automated(csv_paths)

    manual_top = float(manual["area"].max()) * 1.18 if args.shared_ylim else None
    auto_top = float(automated["area"].max()) * 1.18 if args.shared_ylim else None

    days = sorted(set(manual["day"]) | set(automated["day"]))
    if args.day:
        wanted = {d.zfill(2) for d in args.day}
        unknown = wanted - set(days)
        if unknown:
            raise SystemExit(
                f"no data for day(s) {sorted(unknown)}; have {days}"
            )
        days = [d for d in days if d in wanted]

    for day in days:
        man_day = manual[manual["day"] == day].reset_index(drop=True)
        auto_day = automated[automated["day"] == day].reset_index(drop=True)
        date_label = (
            str(auto_day["date_label"].iloc[0]) if len(auto_day) else f"day {day}"
        )
        month_label = (
            str(auto_day["month_label"].iloc[0])
            if len(auto_day) else "Date unavailable from automated metadata"
        )
        print(f"day {day}: {len(man_day)} manual / {len(auto_day)} automated")

        if "manual" in families and len(man_day):
            path = plot_single(day, man_day, "manual", MANUAL, out_dir,
                               manual_top, args.label_all,
                               dataset_display_name=dataset_display_name,
                               date_label=date_label, month_label=month_label)
            print(f"  {path.name}")
        if "automated" in families and len(auto_day):
            path = plot_single(day, auto_day, "automated", AUTOMATED, out_dir,
                               auto_top, args.label_all,
                               dataset_display_name=dataset_display_name,
                               date_label=date_label, month_label=month_label)
            print(f"  {path.name}")
        if "comparison" in families:
            if not len(man_day) or not len(auto_day):
                print("  comparison skipped: one source has no rows for this day")
                continue
            paired = pair_day(man_day, auto_day)
            path, r, rho = plot_comparison(
                day, paired, out_dir, args.minimal,
                dataset_display_name=dataset_display_name,
                date_label=date_label, month_label=month_label,
            )
            print(f"  {path.name}  (Pearson r={r:.2f}, Spearman rho={rho:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
