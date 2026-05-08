"""Music catalog client.

Despite the module name, this calls **Deezer's** public API rather than
Spotify. The original Spotify Client-Credentials path was abandoned in late
2024 after Spotify silently stripped `preview_url`, `popularity`, and `genres`
from non-extended-quota apps — making the entire game premise (play 30-second
clips, gate by popularity) impossible.

Deezer offers what we need with no auth required:
  - `/search?q=...`         tracks with `preview` (30s MP3) and `rank` (popularity-ish)
  - `/search/artist?q=...`  artists with id, name, picture URLs
  - `/track/{id}`           full track including preview

The module name is unchanged to minimise churn elsewhere — column names and
fields like `spotify_track_id` now hold Deezer integer IDs (as strings).
"""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger("slomp.music")

_API = "https://api.deezer.com"


async def _api_get(path: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{_API}{path}", params=params)
        if resp.status_code >= 400:
            logger.error(
                "deezer GET %s -> %s body=%s",
                resp.request.url,
                resp.status_code,
                resp.text[:500],
            )
        resp.raise_for_status()
        data = resp.json()
        # Deezer signals errors with HTTP 200 + {"error": {...}} body
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            logger.error("deezer api error %s: %s", err.get("code"), err.get("message"))
            raise httpx.HTTPStatusError(
                f"Deezer error {err.get('code')}: {err.get('message')}",
                request=resp.request,
                response=resp,
            )
        return data


async def search_tracks(query: str, limit: int = 10) -> list[dict]:
    data = await _api_get("/search", {"q": query, "limit": limit})
    return data.get("data", [])


async def search_tracks_for_artists(
    query: str, artist_names: list[str], limit_per_artist: int = 25
) -> list[dict]:
    """Run one Deezer search per required artist with the artist name
    appended free-text to the query (`q=<query> <name>`), then merge unique
    tracks. We don't use Deezer's `artist:"<name>"` advanced filter because
    its name match is token-based and fuzzy — `artist:"Daft Punk"` happily
    returns tracks by "Pan Da Punk". Free-text concatenation lets Deezer's
    own relevance rank the real artist first; the caller still strips
    look-alikes by ID via `matches_rules`."""
    if not artist_names:
        return []

    async def _one(name: str) -> list[dict]:
        scoped = f"{query} {name}".strip()
        if not scoped:
            return []
        try:
            return await search_tracks(scoped, limit=limit_per_artist)
        except httpx.HTTPStatusError as e:
            logger.warning("artist-scoped search failed for %r: %s", name, e)
            return []

    batches = await asyncio.gather(*(_one(n) for n in artist_names))
    merged: dict[Any, dict] = {}
    for batch in batches:
        for t in batch:
            tid = t.get("id")
            if tid is not None and tid not in merged:
                merged[tid] = t
    return list(merged.values())


def relevance_score(track: dict, query: str) -> float:
    """Score a Deezer track against the user's query for our own ranking.
    Deezer's relevance collapses once you add an `artist:` filter, so the
    merged-search path needs to re-rank with title/token overlap and use
    `rank` only as a tiebreaker."""
    q = (query or "").strip().lower()
    title = (track.get("title") or "").lower()
    artist_name = ((track.get("artist") or {}).get("name") or "").lower()
    score = 0.0
    if q:
        if title == q:
            score += 100.0
        elif title.startswith(q):
            score += 60.0
        elif q in title:
            score += 35.0
        q_tokens = set(q.split())
        if q_tokens:
            t_tokens = set(title.split())
            score += 25.0 * len(q_tokens & t_tokens) / len(q_tokens)
        if q in artist_name:
            score += 5.0
    rank = track.get("rank") or 0
    score += rank / 1_000_000.0
    return score


async def search_artists(query: str, limit: int = 10) -> list[dict]:
    data = await _api_get("/search/artist", {"q": query, "limit": limit})
    return data.get("data", [])


async def get_track(track_id: str) -> dict:
    return await _api_get(f"/track/{track_id}")


async def get_artist(artist_id: str) -> dict:
    return await _api_get(f"/artist/{artist_id}")


def matches_rules(track: dict, rules: dict) -> tuple[bool, str | None]:
    """Validate a track against room rules. (popularity, required_artists,
    preview availability — genres are unsupported since Deezer only exposes
    them at the album level via numeric IDs.)"""
    if not track.get("preview"):
        return False, "track has no preview audio"

    # Deezer's `rank` is roughly 0..1_000_000+ for big hits. Map our
    # min_popularity (0..100) onto that linearly.
    rank = track.get("rank", 0) or 0
    min_popularity = rules.get("min_popularity", 0)
    min_rank = min_popularity * 10_000
    if rank < min_rank:
        return False, "track popularity below minimum"

    required = set(rules.get("required_artists") or [])
    if required:
        artist_ids: set[str] = set()
        primary = track.get("artist", {})
        if primary.get("id") is not None:
            artist_ids.add(str(primary["id"]))
        for c in track.get("contributors", []) or []:
            if c.get("id") is not None:
                artist_ids.add(str(c["id"]))
        if not (artist_ids & required):
            return False, "not by a required artist"

    return True, None


def serialize_candidate(track: dict) -> dict:
    """Shape a Deezer track to our SongCandidate schema."""
    artist = track.get("artist") or {}
    album = track.get("album") or {}
    image = album.get("cover_medium") or album.get("cover") or album.get("cover_big")
    rank = track.get("rank") or 0
    duration_seconds = track.get("duration") or 0
    return {
        "spotify_track_id": str(track["id"]),  # field-name vestigial; holds Deezer id
        "title": track.get("title", ""),
        "artist": artist.get("name", ""),
        "album": album.get("title"),
        "preview_url": track.get("preview"),
        "album_image_url": image,
        "duration_ms": duration_seconds * 1000,
        "popularity": min(100, rank // 10_000),
    }
