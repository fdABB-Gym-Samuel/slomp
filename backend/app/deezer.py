"""Music catalog client.

This calls **Deezer's** public API. The original Spotify Client-Credentials
path was abandoned in late 2024 after Spotify silently stripped
`preview_url`, `popularity`, and `genres` from non-extended-quota apps —
making the entire game premise (play 30-second clips, gate by popularity)
impossible.

Deezer offers what we need with no auth required:
  - `/search?q=...`         tracks with `preview` (30s MP3) and `rank` (popularity-ish)
  - `/search/artist?q=...`  artists with id, name, picture URLs
  - `/track/{id}`           full track including preview
"""

import asyncio
import logging
import random
from typing import Any

import httpx

from app import http as http_client
from app.matching import normalize_artist, normalize_title

logger = logging.getLogger("slomp.music")

_API = "https://api.deezer.com"


async def _api_get(path: str, params: dict | None = None) -> Any:
    resp = await http_client.client().get(f"{_API}{path}", params=params, timeout=10.0)
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


async def search_artists(query: str, limit: int = 10) -> list[dict]:
    data = await _api_get("/search/artist", {"q": query, "limit": limit})
    return data.get("data", [])


async def get_track(track_id: str) -> dict:
    return await _api_get(f"/track/{track_id}")


# Genre IDs used to assemble a diverse pool of "popular" tracks for the
# random gamemode. 0 = the global all-genres chart; the rest are major
# Deezer genre buckets. Pulling from several at once gets us ~600 unique
# tracks before filtering, even though each individual chart caps at 100.
_RANDOM_GENRE_IDS = (0, 132, 116, 152, 113, 165, 84, 173)


async def fetch_random_tracks(min_popularity: int, count: int) -> list[dict]:
    """Pull a pool of popular tracks from Deezer charts (multiple genres),
    then for each candidate find the version that `/search` surfaces and
    keep only ones whose search-discoverable rank passes `min_popularity`.

    Deezer often has several track IDs for the same recording with
    different ranks (e.g. an album release vs a deluxe-edition release).
    The chart endpoint exposes IDs that don't always appear in search
    results, so without this re-search a random pick can be a song
    classic-mode players cannot submit themselves — an inconsistency a
    guesser hits when their search box doesn't return the song they're
    hearing. Returning the search version also makes the stored
    `popularity` match what players see."""
    rules = {"min_popularity": min_popularity, "required_artists": []}

    async def _chart(gid: int) -> list[dict]:
        try:
            data = await _api_get(f"/chart/{gid}/tracks", {"limit": 100})
            return data.get("data", [])
        except Exception:
            logger.warning("chart fetch failed for genre=%s", gid)
            return []

    batches = await asyncio.gather(*(_chart(g) for g in _RANDOM_GENRE_IDS))
    seen_chart: set = set()
    candidates: list[dict] = []
    for batch in batches:
        for t in batch:
            tid = t.get("id")
            if tid is None or tid in seen_chart:
                continue
            seen_chart.add(tid)
            if not t.get("preview"):
                continue
            candidates.append(t)
    random.shuffle(candidates)

    async def _resolve(t: dict) -> dict | None:
        title = t.get("title") or ""
        artist_name = (t.get("artist") or {}).get("name") or ""
        if not title or not artist_name:
            return None
        norm_title = normalize_title(title)
        norm_artist = normalize_artist(artist_name)
        try:
            results = await search_tracks(f"{title} {artist_name}".strip(), limit=5)
        except Exception:
            return None
        for r in results:
            if normalize_title(r.get("title") or "") != norm_title:
                continue
            r_artist = (r.get("artist") or {}).get("name") or ""
            if normalize_artist(r_artist) != norm_artist:
                continue
            ok, _ = matches_rules(r, rules)
            if ok:
                return r
        return None

    # Resolve with a bounded concurrency window (so we don't smash Deezer's
    # rate limit) but consume in completion order — earlier chunks no
    # longer block later ones, and the moment we've accepted `count`
    # tracks we cancel the in-flight tail.
    sem = asyncio.Semaphore(25)

    async def _bounded_resolve(t: dict) -> dict | None:
        async with sem:
            return await _resolve(t)

    tasks = [asyncio.create_task(_bounded_resolve(t)) for t in candidates]
    accepted: list[dict] = []
    seen_accepted: set = set()
    try:
        for fut in asyncio.as_completed(tasks):
            r = await fut
            if r is None:
                continue
            tid = r.get("id")
            if tid is None or tid in seen_accepted:
                continue
            seen_accepted.add(tid)
            accepted.append(r)
            if len(accepted) >= count:
                break
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        # Drain so cancellations don't surface as "Task was destroyed
        # but it is pending!" warnings.
        await asyncio.gather(*tasks, return_exceptions=True)
    return accepted[:count]


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
        "track_id": str(track["id"]),
        "title": track.get("title", ""),
        "artist": artist.get("name", ""),
        "album": album.get("title"),
        "preview_url": track.get("preview"),
        "album_image_url": image,
        "duration_ms": duration_seconds * 1000,
        "popularity": min(100, rank // 10_000),
    }
