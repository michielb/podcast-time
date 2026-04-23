"""Thin wrapper over the iTunes / Apple Podcasts Search API.

iTunes Search rate-limits aggressive callers (roughly ~20 req/minute per IP).
We keep request volume low by defaulting to two markets and pausing between
calls; when a 403 or 429 does come back, we back off and retry.
"""

from __future__ import annotations

import time
from typing import Any

import requests

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
USER_AGENT = "podcast-time/0.1 (+https://github.com/michielb/podcast-time)"
DEFAULT_TIMEOUT = 15
# US covers most English and many Dutch shows. NL picks up remaining NL-only feeds.
# Users can pass markets= to include more (GB, DE, FR, etc.) for edge cases.
DEFAULT_MARKETS = ("US", "NL")
INTER_REQUEST_PAUSE = 1.0  # seconds between iTunes calls — Apple throttles around 20 req/min


def _get(url: str, params: dict[str, Any], retries: int = 2) -> dict:
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url, params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code in (403, 429, 503):
                # Rate-limited or throttled — back off and try again
                wait = 2.0 * (attempt + 1)
                time.sleep(wait)
                last_err = RuntimeError(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    raise last_err or RuntimeError("itunes request failed")


def search(term: str, country: str = "US", limit: int = 5) -> list[dict]:
    data = _get(
        SEARCH_URL,
        {
            "term": term,
            "media": "podcast",
            "entity": "podcast",
            "limit": limit,
            "country": country,
        },
    )
    return data.get("results", []) or []


def lookup_by_id(collection_id: int, country: str = "US") -> dict | None:
    data = _get(LOOKUP_URL, {"id": collection_id, "country": country})
    results = data.get("results", []) or []
    return results[0] if results else None


def search_many_markets(
    term: str, markets: tuple[str, ...] = DEFAULT_MARKETS, limit: int = 5,
    pause: float = INTER_REQUEST_PAUSE,
) -> list[dict]:
    """Search every market and return all results annotated with their country.

    Errors (including rate-limits after retries) are printed but don't abort
    the whole search — partial results are still useful.
    """
    out: list[dict] = []
    for country in markets:
        try:
            results = search(term, country=country, limit=limit)
        except Exception as e:
            print(f"     [warn] iTunes search failed for {country}: {e}")
            continue
        for r in results:
            r = dict(r)
            r["_country"] = country
            out.append(r)
        time.sleep(pause)
    return out
