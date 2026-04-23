"""Render the cumulative stacked horizontal bar chart."""

from __future__ import annotations

from pathlib import Path
from statistics import median

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns

CHART_FILENAME = "podcast-stacked-bars.png"

_FONT_SCALES = {
    "normal": 1.0,
    "large": 1.2,
    "xlarge": 1.5,
}


def _short_name(name: str, maxlen: int = 34) -> str:
    return name if len(name) <= maxlen else name[: maxlen - 1] + "…"


def render(
    data: dict,
    out_path: Path,
    font_size: str = "normal",
    title_text: str | None = None,
    subtitle: str | None = None,
    x_label: str = "Cumulative listening time per week (at 1x)",
) -> None:
    """Render the stacked bar chart.

    data: contents of episodes.json (dict with 'window_days' and 'feeds')
    """
    scale = _FONT_SCALES.get(font_size, 1.0)

    feeds = sorted(data["feeds"], key=lambda x: x["rank"])
    window_weeks = data["window_days"] / 7.0

    rows = []
    for f in feeds:
        durs = f.get("episode_durations_sec") or []
        if f.get("override_kind") == "estimate" and f.get("estimate"):
            est = f["estimate"]
            mpw = est.get("eps_per_week", 0.0) * est.get("median_min", 0.0)
        elif durs:
            mpw = (len(durs) / window_weeks) * (median(durs) / 60.0)
        else:
            mpw = 0.0
        rows.append({"rank": f["rank"], "name": f["title"], "mpw": mpw})

    n = len(rows)
    if n == 0:
        raise ValueError("No feeds to plot.")
    palette = sns.color_palette("husl", n)

    sns.set_style("white")
    fig, ax = plt.subplots(figsize=(13, max(6, 0.4 * n)))

    for row_idx in range(n):
        x = 0.0
        for seg_idx in range(row_idx + 1):
            w = rows[seg_idx]["mpw"]
            is_new = seg_idx == row_idx
            ax.barh(
                y=row_idx,
                width=w,
                left=x,
                color=palette[seg_idx],
                edgecolor="#222" if is_new else "white",
                linewidth=1.4 if is_new else 0.4,
                height=0.78,
            )
            x += w
        cum_min = int(round(x))
        h, m = divmod(cum_min, 60)
        ax.text(
            x + 6, row_idx, f"{h}:{m:02d}",
            va="center", fontsize=int(round(9 * scale)),
            color="#333", fontweight="bold",
        )

    ax.set_yticks(range(n))
    ax.set_yticklabels(
        [f"{r['rank']:>2}  {_short_name(r['name'])}" for r in rows],
        fontsize=int(round(10 * scale)),
    )
    ax.invert_yaxis()

    max_cum = sum(r["mpw"] for r in rows)
    if max_cum <= 0:
        max_cum = 60  # just to have a sensible x-axis
    ax.set_xlim(0, max_cum * 1.09)
    ax.xaxis.set_major_locator(mticker.MultipleLocator(60))
    ax.xaxis.set_minor_locator(mticker.MultipleLocator(15))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x/60)}h"))
    ax.tick_params(axis="x", labelsize=int(round(10 * scale)))
    ax.set_xlabel(x_label, fontsize=int(round(11 * scale)))
    ax.grid(axis="x", which="major", linestyle="--", alpha=0.45)
    ax.grid(axis="x", which="minor", linestyle=":", alpha=0.2)

    if title_text or subtitle:
        heading = title_text or "Cumulative listening time per week, in ranked order"
        if subtitle:
            heading = f"{heading}\n{subtitle}"
        ax.set_title(heading, fontsize=int(round(13 * scale)), pad=14, loc="left")

    sns.despine(left=False, bottom=False)
    plt.tight_layout()
    fig.savefig(out_path, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
