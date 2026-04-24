"""Compute per-show + cumulative stats; write report.md."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from statistics import median

REPORT_FILENAME = "report.md"


def fmt_hm(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}:{m:02d}"


def eps_per_week(feed: dict, n_eps: int, window_weeks: float) -> float:
    """Cadence from actual episode spacing: 7 / avg_gap_days.

    Falls back to n_eps/window_weeks when span can't be measured (n<2 or dates missing).
    """
    if n_eps < 2:
        return n_eps / window_weeks
    earliest = feed.get("earliest_in_window")
    latest = feed.get("latest_in_window")
    if not earliest or not latest:
        return n_eps / window_weeks
    try:
        span_days = (datetime.fromisoformat(latest) - datetime.fromisoformat(earliest)).total_seconds() / 86400.0
    except ValueError:
        return n_eps / window_weeks
    if span_days <= 0:
        return n_eps / window_weeks
    avg_gap_days = span_days / (n_eps - 1)
    return 7.0 / avg_gap_days


def compute_rows(data: dict) -> list[dict]:
    window_days = data["window_days"]
    window_weeks = window_days / 7.0
    feeds = sorted(data["feeds"], key=lambda x: x["rank"])

    rows = []
    for feed in feeds:
        durations = feed.get("episode_durations_sec") or []
        n_eps = len(durations)
        note = ""
        if feed.get("override_kind") == "estimate" and feed.get("estimate"):
            est = feed["estimate"]
            rows.append({
                "rank": feed["rank"],
                "name": feed["title"],
                "eps_per_week": est.get("eps_per_week", 0.0),
                "median_min": est.get("median_min", 0.0),
                "min_per_week": est.get("eps_per_week", 0.0) * est.get("median_min", 0.0),
                "note": "user estimate",
            })
            continue
        if n_eps == 0:
            rows.append({
                "rank": feed["rank"],
                "name": feed["title"],
                "eps_per_week": 0.0,
                "median_min": 0.0,
                "min_per_week": 0.0,
                "note": feed.get("error") or "no episodes in window",
            })
            continue
        epw = eps_per_week(feed, n_eps, window_weeks)
        median_min = median(durations) / 60.0
        rows.append({
            "rank": feed["rank"],
            "name": feed["title"],
            "eps_per_week": epw,
            "median_min": median_min,
            "min_per_week": epw * median_min,
            "note": note,
        })
    return rows


def print_table(rows: list[dict]) -> None:
    print(
        f"\n{'#':>3}  {'Podcast':<45} {'eps/wk':>7} {'med min':>8} "
        f"{'min/wk':>7} {'cum 1.5x':>9} {'cum 1.8x':>9} {'cum 2x':>8}"
    )
    print("-" * 110)
    cum = 0.0
    for r in rows:
        cum += r["min_per_week"]
        note = f"  {r['note']}" if r["note"] else ""
        print(
            f"{r['rank']:>3}  {r['name'][:45]:<45} "
            f"{r['eps_per_week']:>7.2f} {r['median_min']:>8.1f} {r['min_per_week']:>7.1f} "
            f"{fmt_hm(cum/1.5):>9} {fmt_hm(cum/1.8):>9} {fmt_hm(cum/2.0):>8}{note}"
        )
    total = sum(r["min_per_week"] for r in rows)
    print("-" * 110)
    print(
        f"TOTAL:  {fmt_hm(total/1.5)} at 1.5x  |  "
        f"{fmt_hm(total/1.8)} at 1.8x  |  {fmt_hm(total/2.0)} at 2x"
    )


def write_report(rows: list[dict], path: Path, window_days: int) -> None:
    lines = []
    lines.append("# Podcast time budget\n")
    lines.append(f"Window: last {window_days} days.\n")
    lines.append("")
    lines.append("| # | Podcast | eps/wk | median min | min/wk | cum 1.5x | cum 1.8x | cum 2x |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    cum = 0.0
    for r in rows:
        cum += r["min_per_week"]
        name = r["name"] + (f" _({r['note']})_" if r["note"] else "")
        lines.append(
            f"| {r['rank']} | {name} | {r['eps_per_week']:.2f} | "
            f"{r['median_min']:.1f} | {r['min_per_week']:.1f} | "
            f"{fmt_hm(cum/1.5)} | {fmt_hm(cum/1.8)} | {fmt_hm(cum/2.0)} |"
        )
    total = sum(r["min_per_week"] for r in rows)
    lines.append("")
    lines.append(
        f"**Total:** {fmt_hm(total/1.5)} at 1.5x · "
        f"{fmt_hm(total/1.8)} at 1.8x · "
        f"{fmt_hm(total/2.0)} at 2x"
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
