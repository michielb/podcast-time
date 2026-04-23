"""Feed discovery: turn a list of podcast titles into resolved RSS URLs.

Strategy per title:
  1. If the entry has an override (rss URL, apple:<id>, skip, estimate), honor it.
  2. Otherwise, search iTunes across multiple markets with the full title AND a
     simplified variant, score every returned collectionName against the query,
     deduplicate by Apple collectionId, and keep the top 3.
  3. Flag anything with best-score < CONFIDENCE_THRESHOLD for review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from rapidfuzz import fuzz

from . import itunes
from .input import Entry, read_entries, validate

CONFIDENCE_THRESHOLD = 75  # fuzz token_set_ratio, 0-100
FEEDS_FILENAME = "feeds.json"

_STRIP_SUFFIXES = (
    " podcast",
    " the podcast",
    " - official podcast",
    " official podcast",
)


@dataclass
class Candidate:
    collection_id: int
    collection_name: str
    artist_name: str
    feed_url: str
    country: str
    score: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Match:
    rank: int
    title: str
    override: str | None
    override_kind: str | None
    winner: Candidate | None = None
    alternatives: list[Candidate] = field(default_factory=list)
    needs_review: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "title": self.title,
            "override": self.override,
            "override_kind": self.override_kind,
            "winner": self.winner.to_dict() if self.winner else None,
            "alternatives": [a.to_dict() for a in self.alternatives],
            "needs_review": self.needs_review,
            "note": self.note,
        }


def _simplify(title: str) -> str:
    low = title.lower()
    for suf in _STRIP_SUFFIXES:
        if low.endswith(suf):
            return title[: -len(suf)].strip()
    return title


def _score(query: str, candidate: str) -> int:
    return int(fuzz.token_set_ratio(query.lower(), candidate.lower()))


def _itunes_result_to_candidate(r: dict, query: str) -> Candidate | None:
    if not r.get("feedUrl") or not r.get("collectionId"):
        return None
    return Candidate(
        collection_id=int(r["collectionId"]),
        collection_name=r.get("collectionName", "") or "",
        artist_name=r.get("artistName", "") or "",
        feed_url=r["feedUrl"],
        country=r.get("_country", "??"),
        score=_score(query, r.get("collectionName", "") or ""),
    )


def search_candidates(title: str, markets: tuple[str, ...] = itunes.DEFAULT_MARKETS) -> list[Candidate]:
    """Return top candidates (deduped by collectionId) for a title.

    Tries the full title first. Only falls back to a simplified variant
    (e.g. stripping trailing "Podcast") if no confident match was found —
    this keeps iTunes API volume down.
    """
    def _collect(raw: list[dict], into: dict[int, Candidate]) -> None:
        for r in raw:
            c = _itunes_result_to_candidate(r, title)
            if c is None:
                continue
            prev = into.get(c.collection_id)
            if prev is None or c.score > prev.score:
                into[c.collection_id] = c

    cands: dict[int, Candidate] = {}
    _collect(itunes.search_many_markets(title, markets=markets, limit=5), cands)

    best = max((c.score for c in cands.values()), default=0)
    simp = _simplify(title)
    if simp != title and best < CONFIDENCE_THRESHOLD:
        _collect(itunes.search_many_markets(simp, markets=markets, limit=5), cands)

    return sorted(cands.values(), key=lambda x: -x.score)


def resolve_entry(entry: Entry, markets: tuple[str, ...] = itunes.DEFAULT_MARKETS) -> Match:
    """Resolve a single entry. Handles overrides and normal lookup."""
    match = Match(
        rank=entry.rank,
        title=entry.title,
        override=entry.override,
        override_kind=entry.override_kind,
    )

    if entry.override_kind == "skip":
        match.note = "user marked as skip (no public feed)"
        return match

    if entry.override_kind == "estimate":
        match.note = "user-provided estimate"
        return match

    if entry.override_kind == "rss":
        match.winner = Candidate(
            collection_id=0,
            collection_name=entry.title,
            artist_name="",
            feed_url=entry.rss_url or "",
            country="--",
            score=100,
        )
        match.note = "rss url pinned by user"
        return match

    if entry.override_kind == "apple_id":
        apple_id = entry.apple_id
        if apple_id is None:
            match.needs_review = True
            match.note = "invalid apple: override"
            return match
        found = None
        for country in markets:
            try:
                found = itunes.lookup_by_id(apple_id, country=country)
            except Exception:
                continue
            if found:
                break
        if found and found.get("feedUrl"):
            match.winner = Candidate(
                collection_id=int(found.get("collectionId") or apple_id),
                collection_name=found.get("collectionName", "") or entry.title,
                artist_name=found.get("artistName", "") or "",
                feed_url=found["feedUrl"],
                country="--",
                score=100,
            )
            match.note = f"pinned by Apple ID {apple_id}"
        else:
            match.needs_review = True
            match.note = f"Apple ID {apple_id} not found"
        return match

    # Normal path: lookup via iTunes search
    cands = search_candidates(entry.title, markets=markets)
    if not cands:
        match.needs_review = True
        match.note = "no results from iTunes search"
        return match

    match.winner = cands[0]
    match.alternatives = cands[1:3]
    if match.winner.score < CONFIDENCE_THRESHOLD:
        match.needs_review = True
    return match


def _load_cache(feeds_path: Path) -> dict[tuple[str, str | None], Match]:
    """Load an existing feeds.json as a cache keyed by (title, override)."""
    if not feeds_path.exists():
        return {}
    try:
        data = json.loads(feeds_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    cache: dict[tuple[str, str | None], Match] = {}
    for row in data.get("feeds", []):
        winner_d = row.get("winner")
        winner = Candidate(**winner_d) if winner_d else None
        alts = [Candidate(**a) for a in (row.get("alternatives") or [])]
        m = Match(
            rank=row.get("rank", 0),
            title=row.get("title", ""),
            override=row.get("override"),
            override_kind=row.get("override_kind"),
            winner=winner,
            alternatives=alts,
            needs_review=bool(row.get("needs_review")),
            note=row.get("note", "") or "",
        )
        cache[(m.title, m.override)] = m
    return cache


def _cache_entry_is_reusable(m: Match) -> bool:
    """A cached entry is safe to reuse when it's a confident, non-errored hit."""
    if m.override_kind in ("skip", "estimate"):
        return True
    if m.winner is None:
        return False
    if m.needs_review:
        return False
    if m.winner.score < CONFIDENCE_THRESHOLD and m.override_kind is None:
        return False
    return True


def resolve_all(path: Path, feeds_cache_path: Path | None = None) -> list[Match]:
    entries = read_entries(path)
    warnings = validate(entries)
    for w in warnings:
        print(f"warning: {w}")

    cache = _load_cache(feeds_cache_path) if feeds_cache_path else {}
    if cache:
        print(f"(found {len(cache)} cached matches in {feeds_cache_path.name})")  # type: ignore[union-attr]

    out: list[Match] = []
    for e in entries:
        print(f"[{e.rank:>3}] {e.title}")
        cached = cache.get((e.title, e.override))
        if cached and _cache_entry_is_reusable(cached):
            cached.rank = e.rank  # rank may have shifted; title + override defines identity
            if cached.winner:
                print(
                    f"     [cache] {cached.winner.collection_name!r} "
                    f"(score {cached.winner.score})"
                )
            else:
                print(f"     [cache] {cached.note or 'override'}")
            out.append(cached)
            continue

        m = resolve_entry(e)
        if m.winner:
            tag = "[?]" if m.needs_review else "[ok]"
            print(
                f"     {tag} {m.winner.collection_name!r} by {m.winner.artist_name} "
                f"(score {m.winner.score}, {m.winner.country})"
            )
        else:
            print(f"     [note] {m.note}")
        out.append(m)
    return out


def write_feeds(matches: list[Match], path: Path) -> None:
    data = {"feeds": [m.to_dict() for m in matches]}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_feeds(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `podcast-time find-feeds` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))["feeds"]


def print_review(matches: list[Match]) -> int:
    """Print the review summary. Returns the number of entries needing review."""
    review = [m for m in matches if m.needs_review]
    confident = [m for m in matches if (m.winner is not None and not m.needs_review)]
    skipped = [m for m in matches if m.override_kind in ("skip", "estimate")]

    print()
    print(f"Found {len(matches)} entries.")
    print(f"  [ok]    {len(confident)} confident matches")
    if skipped:
        print(f"  [note]  {len(skipped)} skip/estimate overrides")
    print(f"  [?]     {len(review)} need review")

    if not review:
        return 0

    print()
    print("REVIEW NEEDED")
    print("-" * 72)
    for m in review:
        print(f"\n[{m.rank:>3}] {m.title}")
        if m.winner:
            print(
                f"      Best guess:  {m.winner.collection_name!r} by {m.winner.artist_name} "
                f"(score {m.winner.score}, {m.winner.country})"
            )
            print(f"                   {m.winner.feed_url}")
        else:
            print(f"      Best guess:  (none) — {m.note}")
        if m.alternatives:
            print("      Alternatives found:")
            for a in m.alternatives:
                print(
                    f"        - {a.collection_name!r} by {a.artist_name} "
                    f"(score {a.score}, {a.country})"
                )
                print(f"          {a.feed_url}")
        print(f"      To pin in podcasts.txt:")
        print(f"        {m.title} | <paste rss url>")
        print(f"      Or use `podcast-time identify <url-or-query>` for a guided lookup.")
    return len(review)
