"""`podcast-time identify <url-or-query>` — friendly 'is this the one?' lookup.

Accepts:
  - Spotify show URL        → scrape og:title/og:description, then search Apple
  - Apple Podcasts URL      → resolved by Apple iTunes ID in the URL
  - apple:<id> shorthand    → iTunes lookup by ID
  - direct RSS URL          → parsed with feedparser
  - any other string        → treated as a free-text iTunes search term
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from statistics import median
from urllib.parse import urlparse

import feedparser
import requests

from . import itunes
from .feeds import search_candidates, Candidate
from .episodes import parse_duration, parse_published

USER_AGENT = "podcast-time/0.1 (identify)"
SPOTIFY_SHOW_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?show/([A-Za-z0-9]+)")
APPLE_ID_RE = re.compile(r"/id(\d+)")


def _fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15, allow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  [!] could not fetch {url}: {e}")
        return None


def _meta(html_text: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta\s+(?:property|name)="{re.escape(prop)}"\s+content="([^"]*)"',
        html_text,
    )
    return html.unescape(m.group(1)) if m else None


def _classify(target: str) -> tuple[str, str]:
    """Return (kind, normalized). kind ∈ {spotify, apple, apple_id, rss, query}."""
    t = target.strip()
    if t.startswith("apple:"):
        return "apple_id", t.split(":", 1)[1].strip()

    parsed = urlparse(t)
    if parsed.scheme in ("http", "https"):
        host = parsed.netloc.lower()
        if "open.spotify.com" in host and SPOTIFY_SHOW_RE.search(t):
            return "spotify", t
        if "podcasts.apple.com" in host and APPLE_ID_RE.search(parsed.path):
            m = APPLE_ID_RE.search(parsed.path)
            return "apple_id", m.group(1)  # type: ignore[union-attr]
        return "rss", t

    return "query", t


def _print_feed_summary(feed_url: str) -> None:
    print()
    print("Feed contents:")
    try:
        parsed = feedparser.parse(feed_url)
    except Exception as e:
        print(f"  could not parse feed: {e}")
        return
    if parsed.bozo and not parsed.entries:
        print(f"  could not parse feed: {parsed.bozo_exception}")
        return

    total = len(parsed.entries)
    print(f"  Episodes:      {total} total")

    latest_pub = None
    latest_title = None
    for ep in parsed.entries:
        pub = parse_published(ep)
        if pub is None:
            continue
        if latest_pub is None or pub > latest_pub:
            latest_pub = pub
            latest_title = (ep.get("title") or "").strip()
    if latest_pub:
        age = datetime.now(timezone.utc) - latest_pub
        print(f'  Latest:        "{_shorten(latest_title or "—", 60)}" ({_relative_age(age)})')

    # 90-day window stats
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    durs: list[int] = []
    for ep in parsed.entries:
        pub = parse_published(ep)
        if pub is None or pub < cutoff:
            continue
        secs, _ = parse_duration(ep)
        if secs is not None:
            durs.append(secs)
    if durs:
        med_min = median(durs) / 60.0
        eps_per_week = len(durs) / (90 / 7)
        mpw = eps_per_week * med_min
        print(
            f"  Last 90 days:  {len(durs)} episodes, median {med_min:.0f} min "
            f"(~{mpw:.0f} min/week)"
        )
    else:
        print(f"  Last 90 days:  0 episodes (infrequent, between seasons, or different format)")


def _shorten(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _relative_age(delta: timedelta) -> str:
    days = delta.days
    if days < 1:
        hrs = int(delta.total_seconds() // 3600)
        return f"{hrs}h ago" if hrs else "just now"
    if days == 1:
        return "1 day ago"
    if days < 30:
        return f"{days} days ago"
    if days < 365:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def _print_candidate(c: Candidate, *, winner: bool = True) -> None:
    tag = "Match found" if winner else "Alternative"
    print(f"{tag} (score {c.score}):")
    print(f"  Title:         {c.collection_name}")
    print(f"  Artist:        {c.artist_name}")
    print(f"  Apple ID:      {c.collection_id}")
    print(f"  Market:        {c.country}")
    print(f"  Feed:          {c.feed_url}")


def _override_hint(title_for_file: str, feed_url: str) -> None:
    print()
    print("Is this the one? Add this line to podcasts.txt:")
    print(f"  {title_for_file} | {feed_url}")


def identify(target: str) -> int:
    kind, payload = _classify(target)

    if kind == "spotify":
        print(f"Spotify show URL: {payload}")
        html_text = _fetch(payload)
        if not html_text:
            return 2
        title = _meta(html_text, "og:title") or ""
        desc = _meta(html_text, "og:description") or ""
        publisher = ""
        m = re.match(r"Podcast\s*·\s*([^·]+)\s*·", desc)
        if m:
            publisher = m.group(1).strip()
        print(f'  Show title:    "{title}"')
        if publisher:
            print(f"  Publisher:     {publisher}")
        if not title:
            print("  Could not extract the show title from Spotify.")
            return 2
        print()
        print("Searching Apple Podcasts for the RSS feed...")
        query = f"{title} {publisher}".strip()
        cands = search_candidates(query)
        if not cands:
            print("  No matches found on Apple. Feed may be a Spotify/Podimo exclusive.")
            return 1
        _print_candidate(cands[0], winner=True)
        _print_feed_summary(cands[0].feed_url)
        _override_hint(title, cands[0].feed_url)
        if len(cands) > 1:
            print()
            print("Other candidates (in case the match above is wrong):")
            for a in cands[1:3]:
                print(
                    f"  - {a.collection_name!r} by {a.artist_name} "
                    f"(score {a.score}, {a.country})"
                )
                print(f"    {a.feed_url}")
        return 0

    if kind == "apple_id":
        apple_id = int(payload)
        print(f"Apple Podcasts ID: {apple_id}")
        found = None
        for country in itunes.DEFAULT_MARKETS:
            try:
                found = itunes.lookup_by_id(apple_id, country=country)
            except Exception:
                continue
            if found:
                break
        if not found or not found.get("feedUrl"):
            print("  Apple ID not found.")
            return 1
        c = Candidate(
            collection_id=apple_id,
            collection_name=found.get("collectionName", "") or "",
            artist_name=found.get("artistName", "") or "",
            feed_url=found["feedUrl"],
            country="--",
            score=100,
        )
        _print_candidate(c)
        _print_feed_summary(c.feed_url)
        _override_hint(c.collection_name, c.feed_url)
        return 0

    if kind == "rss":
        print(f"RSS URL: {payload}")
        try:
            parsed = feedparser.parse(payload)
        except Exception as e:
            print(f"  could not parse: {e}")
            return 2
        if parsed.bozo and not parsed.entries:
            print(f"  could not parse: {parsed.bozo_exception}")
            return 2
        title = (parsed.feed.get("title") or "").strip()
        author = (parsed.feed.get("author") or parsed.feed.get("publisher") or "").strip()
        print(f"  Feed title:    {title}")
        if author:
            print(f"  Author:        {author}")
        _print_feed_summary(payload)
        _override_hint(title or "YourTitle", payload)
        return 0

    # free-text query
    print(f"Search query: {payload!r}")
    print("Searching Apple Podcasts...")
    cands = search_candidates(payload)
    if not cands:
        print("  No matches found.")
        return 1
    _print_candidate(cands[0])
    _print_feed_summary(cands[0].feed_url)
    _override_hint(payload, cands[0].feed_url)
    if len(cands) > 1:
        print()
        print("Other candidates:")
        for a in cands[1:3]:
            print(
                f"  - {a.collection_name!r} by {a.artist_name} "
                f"(score {a.score}, {a.country})"
            )
            print(f"    {a.feed_url}")
    return 0
