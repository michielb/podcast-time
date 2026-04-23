"""Fetch each RSS feed, extract episodes in the last N days with durations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import feedparser

DEFAULT_WINDOW_DAYS = 90
ASSUMED_BITRATE_KBPS = 128  # for the enclosure-bytes fallback
EPISODES_FILENAME = "episodes.json"


def parse_duration_field(raw) -> tuple[int | None, str]:
    if not raw:
        return None, "no-duration"
    s = str(raw).strip()
    try:
        if s.isdigit():
            return int(s), "itunes:duration(seconds)"
        parts = s.split(":")
        if all(p.isdigit() for p in parts):
            nums = [int(p) for p in parts]
            if len(nums) == 3:
                return nums[0] * 3600 + nums[1] * 60 + nums[2], "itunes:duration(hms)"
            if len(nums) == 2:
                return nums[0] * 60 + nums[1], "itunes:duration(ms)"
    except Exception:
        pass
    return None, "no-duration"


def parse_duration(ep) -> tuple[int | None, str]:
    secs, source = parse_duration_field(ep.get("itunes_duration"))
    if secs is not None:
        return secs, source

    for link in ep.get("links", []) or []:
        if link.get("rel") == "enclosure":
            length = link.get("length")
            if length and str(length).isdigit() and int(length) > 0:
                seconds = int(length) * 8 // (ASSUMED_BITRATE_KBPS * 1000)
                if 30 < seconds < 24 * 3600:
                    return seconds, "enclosure-bytes-estimated"
    return None, "no-duration"


def parse_published(ep) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = ep.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                continue
    return None


def _process_feed(entry: dict, cutoff: datetime, window_days: int) -> dict:
    result = {
        "rank": entry["rank"],
        "title": entry["title"],
        "feed_url_used": None,
        "episodes_in_window": 0,
        "window_days": window_days,
        "earliest_in_window": None,
        "latest_in_window": None,
        "episode_durations_sec": [],
        "duration_sources": {},
        "skipped_no_duration": 0,
        "error": None,
        "estimate": None,
        "override_kind": entry.get("override_kind"),
    }

    # Skip overrides
    if entry.get("override_kind") == "skip":
        result["error"] = "user-marked skip (no public feed)"
        return result

    # Estimate overrides: read from Match and carry through
    if entry.get("override_kind") == "estimate":
        # The estimate is not in feeds.json; caller must pass it. See cmd_fetch.
        result["error"] = "estimate override — no RSS fetched"
        return result

    winner = entry.get("winner")
    if not winner or not winner.get("feed_url"):
        result["error"] = "no feed url resolved"
        return result
    url = winner["feed_url"]
    result["feed_url_used"] = url

    try:
        parsed = feedparser.parse(url)
    except Exception as e:
        result["error"] = f"feedparser exception: {e}"
        return result

    if parsed.bozo and not parsed.entries:
        result["error"] = f"feed parse failed: {parsed.bozo_exception}"
        return result

    for ep in parsed.entries:
        pub = parse_published(ep)
        if pub is None or pub < cutoff:
            continue
        secs, source = parse_duration(ep)
        if secs is None:
            result["skipped_no_duration"] += 1
            continue
        result["episode_durations_sec"].append(secs)
        result["duration_sources"][source] = result["duration_sources"].get(source, 0) + 1
        iso = pub.isoformat()
        if result["earliest_in_window"] is None or iso < result["earliest_in_window"]:
            result["earliest_in_window"] = iso
        if result["latest_in_window"] is None or iso > result["latest_in_window"]:
            result["latest_in_window"] = iso

    result["episodes_in_window"] = len(result["episode_durations_sec"])
    return result


def fetch_all(feeds: list[dict], window_days: int = DEFAULT_WINDOW_DAYS) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    print(
        f"Fetching episodes published after {cutoff.date().isoformat()} "
        f"({window_days}-day window)\n"
    )

    out = []
    for entry in feeds:
        print(f"[{entry['rank']:>3}] {entry['title']}")
        r = _process_feed(entry, cutoff, window_days)

        if entry.get("override_kind") == "estimate" and "_estimate" in entry:
            est = entry["_estimate"]
            r["estimate"] = est
            eps = est.get("eps_per_week", 0.0) * (window_days / 7.0)
            r["error"] = None
            r["episodes_in_window"] = round(eps)
            # Synthesize durations so median downstream is correct:
            secs = int(round(est.get("median_min", 0.0) * 60))
            r["episode_durations_sec"] = [secs] * round(eps)
            r["duration_sources"] = {"user-estimate": round(eps)}
            print(
                f"     [estimate] {est.get('eps_per_week', 0):.2f} eps/wk, "
                f"{est.get('median_min', 0):.1f} min median"
            )
            out.append(r)
            continue

        if r["error"]:
            print(f"     [!] {r['error']}")
        else:
            n = r["episodes_in_window"]
            med = median(r["episode_durations_sec"]) if r["episode_durations_sec"] else 0
            print(f"     [ok] {n} eps in window, median {med/60:.1f} min")
        out.append(r)

    return out


def write_episodes(feeds: list[dict], path: Path, window_days: int = DEFAULT_WINDOW_DAYS) -> None:
    path.write_text(
        json.dumps({"window_days": window_days, "feeds": feeds}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_episodes(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `podcast-time fetch` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))
