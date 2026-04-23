"""Parse podcasts.txt — the user's ranked list with optional overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Entry:
    rank: int
    title: str
    override: str | None = None  # raw override string after the "|", if any

    @property
    def override_kind(self) -> str | None:
        """Classify the override: 'rss', 'apple_id', 'skip', 'estimate', or None."""
        if self.override is None:
            return None
        s = self.override.strip().lower()
        if s == "skip":
            return "skip"
        if s.startswith("apple:"):
            return "apple_id"
        if s.startswith("estimate:"):
            return "estimate"
        if s.startswith(("http://", "https://")):
            return "rss"
        return None  # unrecognized — will be surfaced as a parse warning

    @property
    def apple_id(self) -> int | None:
        if self.override_kind == "apple_id":
            return int(self.override.strip().split(":", 1)[1])  # type: ignore[union-attr]
        return None

    @property
    def rss_url(self) -> str | None:
        if self.override_kind == "rss":
            return self.override.strip()  # type: ignore[union-attr]
        return None

    @property
    def estimate(self) -> dict | None:
        """Parse 'estimate:eps=1.3,min=32' → {'eps_per_week': 1.3, 'median_min': 32.0}."""
        if self.override_kind != "estimate":
            return None
        body = self.override.strip()[len("estimate:"):]  # type: ignore[union-attr]
        out: dict = {}
        for part in body.split(","):
            k, _, v = part.partition("=")
            k = k.strip()
            try:
                val = float(v.strip())
            except ValueError:
                continue
            if k == "eps":
                out["eps_per_week"] = val
            elif k == "min":
                out["median_min"] = val
        if "eps_per_week" in out and "median_min" in out:
            return out
        return None


def read_entries(path: Path) -> list[Entry]:
    """Read podcasts.txt into a list of Entry objects. Rank starts at 1."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Create it (see podcasts.example.txt) and re-run."
        )

    entries: list[Entry] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            title, override = line.split("|", 1)
            title = title.strip()
            override = override.strip() or None
        else:
            title = line
            override = None
        if not title:
            continue
        entries.append(Entry(rank=len(entries) + 1, title=title, override=override))

    if not entries:
        raise ValueError(f"{path} has no podcast entries.")
    return entries


def validate(entries: list[Entry]) -> list[str]:
    """Return a list of human-readable warnings about the input."""
    warnings: list[str] = []
    seen_titles: dict[str, int] = {}
    for e in entries:
        key = re.sub(r"\s+", " ", e.title.lower())
        if key in seen_titles:
            warnings.append(
                f"Duplicate title at rank {e.rank}: {e.title!r} also appears at rank {seen_titles[key]}"
            )
        else:
            seen_titles[key] = e.rank
        if e.override is not None and e.override_kind is None:
            warnings.append(
                f"Rank {e.rank} ({e.title!r}): unrecognized override {e.override!r} "
                f"(expected rss URL, 'apple:<id>', 'skip', or 'estimate:eps=...,min=...')"
            )
    return warnings
