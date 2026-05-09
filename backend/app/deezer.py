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
import random
from typing import Any

import httpx

from app.matching import normalize_artist, normalize_title

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

    accepted: list[dict] = []
    seen_accepted: set = set()
    chunk = 25
    for i in range(0, len(candidates), chunk):
        if len(accepted) >= count:
            break
        resolved = await asyncio.gather(
            *(_resolve(t) for t in candidates[i : i + chunk])
        )
        for r in resolved:
            if r is None:
                continue
            tid = r.get("id")
            if tid is None or tid in seen_accepted:
                continue
            seen_accepted.add(tid)
            accepted.append(r)
            if len(accepted) >= count:
                break
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
        "spotify_track_id": str(track["id"]),  # field-name vestigial; holds Deezer id
        "title": track.get("title", ""),
        "artist": artist.get("name", ""),
        "album": album.get("title"),
        "preview_url": track.get("preview"),
        "album_image_url": image,
        "duration_ms": duration_seconds * 1000,
        "popularity": min(100, rank // 10_000),
    }
