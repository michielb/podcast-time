"""Command-line interface.

Subcommands:
  find-feeds     Resolve RSS URLs for each line in podcasts.txt → feeds.json
  fetch          Pull each feed; parse episodes → episodes.json
  analyze        Compute cumulative stats → report.md + stdout table
  plot           Render stacked-bar chart → podcast-stacked-bars.png
  run            All of the above, in order
  identify       Friendly lookup of a single URL or query (no state writes)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def _add_dir_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--dir",
        dest="workdir",
        default=".",
        help="Directory containing podcasts.txt and where outputs land (default: .)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="podcast-time",
        description="Turn a ranked podcast list into a weekly time budget.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find-feeds", help="Resolve RSS URLs for podcasts.txt")
    _add_dir_arg(p_find)

    p_fetch = sub.add_parser("fetch", help="Fetch each RSS feed; write episodes.json")
    _add_dir_arg(p_fetch)
    p_fetch.add_argument("--window-days", type=int, default=90, help="Recency window (default: 90)")

    p_analyze = sub.add_parser("analyze", help="Compute and write report.md")
    _add_dir_arg(p_analyze)

    p_plot = sub.add_parser("plot", help="Render the stacked-bar chart")
    _add_dir_arg(p_plot)
    p_plot.add_argument(
        "--fonts", choices=("normal", "large", "xlarge"), default="normal",
        help="Chart font scale (default: normal)",
    )
    p_plot.add_argument(
        "--title", default=None, help="Override chart title (default: auto)",
    )
    p_plot.add_argument(
        "--speed", type=float, default=1.0,
        help="Playback speed to scale durations by (default: 1.0)",
    )
    p_plot.add_argument(
        "--out", default=None,
        help="Output filename (default: podcast-stacked-bars.png, or -<speed>x variant)",
    )

    p_run = sub.add_parser("run", help="find-feeds + fetch + analyze + plot")
    _add_dir_arg(p_run)
    p_run.add_argument("--window-days", type=int, default=90)
    p_run.add_argument("--fonts", choices=("normal", "large", "xlarge"), default="normal")

    p_id = sub.add_parser("identify", help="Inspect a single URL or query")
    p_id.add_argument(
        "target",
        help="Spotify URL, Apple Podcasts URL, apple:<id>, RSS URL, or a search term",
    )

    return parser


def cmd_find_feeds(workdir: Path) -> int:
    from .feeds import resolve_all, write_feeds, print_review, FEEDS_FILENAME

    src = workdir / "podcasts.txt"
    out = workdir / FEEDS_FILENAME
    matches = resolve_all(src, feeds_cache_path=out)
    write_feeds(matches, out)
    n_review = print_review(matches)
    print()
    print(f"Wrote {out}")
    return 0 if n_review == 0 else 0  # non-zero review count is informational, not an error


def cmd_fetch(workdir: Path, window_days: int) -> int:
    from .feeds import load_feeds, FEEDS_FILENAME
    from .episodes import fetch_all, write_episodes, EPISODES_FILENAME
    from .input import read_entries

    feeds_path = workdir / FEEDS_FILENAME
    out_path = workdir / EPISODES_FILENAME

    feeds = load_feeds(feeds_path)

    # Carry estimate data from podcasts.txt into the fetch step
    entries = {e.rank: e for e in read_entries(workdir / "podcasts.txt")}
    for f in feeds:
        e = entries.get(f["rank"])
        if e and e.estimate:
            f["_estimate"] = e.estimate

    results = fetch_all(feeds, window_days=window_days)
    write_episodes(results, out_path, window_days=window_days)
    print()
    print(f"Wrote {out_path}")
    return 0


def cmd_analyze(workdir: Path) -> int:
    from .episodes import load_episodes, EPISODES_FILENAME
    from .analyze import compute_rows, print_table, write_report, REPORT_FILENAME

    data = load_episodes(workdir / EPISODES_FILENAME)
    rows = compute_rows(data)
    print_table(rows)
    report_path = workdir / REPORT_FILENAME
    write_report(rows, report_path, window_days=data["window_days"])
    print()
    print(f"Wrote {report_path}")
    return 0


def cmd_plot(workdir: Path, fonts: str, title: str | None, speed: float = 1.0, out_name: str | None = None) -> int:
    from .episodes import load_episodes, EPISODES_FILENAME
    from .plot import render, CHART_FILENAME

    data = load_episodes(workdir / EPISODES_FILENAME)
    if out_name:
        filename = out_name
    elif speed != 1.0:
        stem = CHART_FILENAME.rsplit(".", 1)[0]
        filename = f"{stem}-{speed:g}x.png"
    else:
        filename = CHART_FILENAME
    out = workdir / filename
    render(
        data, out,
        font_size=fonts,
        title_text=title,
        subtitle="Each row adds one podcast — wide segments are the time hogs",
        speed=speed,
    )
    print(f"Wrote {out}")
    return 0


def cmd_run(workdir: Path, window_days: int, fonts: str) -> int:
    rc = cmd_find_feeds(workdir)
    if rc != 0:
        return rc
    rc = cmd_fetch(workdir, window_days=window_days)
    if rc != 0:
        return rc
    rc = cmd_analyze(workdir)
    if rc != 0:
        return rc
    rc = cmd_plot(workdir, fonts=fonts, title=None)
    return rc


def cmd_identify(target: str) -> int:
    from .identify import identify
    return identify(target)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "identify":
        return cmd_identify(args.target)

    workdir = Path(args.workdir).expanduser().resolve()
    if not workdir.is_dir():
        print(f"error: {workdir} is not a directory", file=sys.stderr)
        return 2

    try:
        if args.cmd == "find-feeds":
            return cmd_find_feeds(workdir)
        if args.cmd == "fetch":
            return cmd_fetch(workdir, window_days=args.window_days)
        if args.cmd == "analyze":
            return cmd_analyze(workdir)
        if args.cmd == "plot":
            return cmd_plot(workdir, fonts=args.fonts, title=args.title, speed=args.speed, out_name=args.out)
        if args.cmd == "run":
            return cmd_run(workdir, window_days=args.window_days, fonts=args.fonts)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
