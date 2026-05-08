import asyncio
import json
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import auth, db, spotify
from ..models import RoomSettings, SongCandidate

router = APIRouter(prefix="/spotify", tags=["spotify"])


def _upstream_to_http_error(e: httpx.HTTPStatusError) -> HTTPException:
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "music_upstream_error",
            "message": f"music API error {e.response.status_code}",
        },
    )


@router.get("/search", response_model=list[SongCandidate])
async def search(
    q: str = Query(..., min_length=1),
    room_id: UUID | None = Query(default=None),
    # Spotify caps Client-Credentials search at 10 results. Their docs still
    # say 50, but anything ≥15 returns 400 "Invalid limit". Capped at 10.
    limit: int = Query(default=10, ge=1, le=10),
    user_id: UUID = Depends(auth.get_current_user_id),
) -> list[SongCandidate]:
    rules: dict | None = None
    if room_id is not None:
        async with db.pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT settings FROM rooms WHERE id = $1", room_id
            )
        if row is not None:
            raw = row["settings"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            rules = RoomSettings.model_validate(raw or {}).model_dump()

    required_ids: list[str] = []
    if rules is not None:
        required_ids = [a for a in (rules.get("required_artists") or []) if a]

    try:
        if required_ids:
            # Resolve required IDs → names and run one Deezer search per
            # artist with the name appended free-text to the user's query
            # (e.g. `q=lucky Daft Punk`). Deezer's relevance puts the real
            # artist on top; `matches_rules` below then strips covers /
            # tributes / look-alikes by ID. We re-rank merged results
            # ourselves so the artist's real tracks lead, regardless of
            # which artist's batch they came from.
            artist_lookups = await asyncio.gather(
                *(spotify.get_artist(aid) for aid in required_ids),
                return_exceptions=True,
            )
            artist_names = [
                a["name"]
                for a in artist_lookups
                if isinstance(a, dict) and a.get("name")
            ]
            tracks = await spotify.search_tracks_for_artists(q, artist_names)
            tracks.sort(key=lambda t: spotify.relevance_score(t, q), reverse=True)
        else:
            tracks = await spotify.search_tracks(q, limit=limit)
    except httpx.HTTPStatusError as e:
        raise _upstream_to_http_error(e)

    out: list[SongCandidate] = []
    for t in tracks:
        if rules is not None:
            ok, _ = spotify.matches_rules(t, rules)
            if not ok:
                continue
        out.append(SongCandidate.model_validate(spotify.serialize_candidate(t)))
        if len(out) >= limit:
            break
    return out


def _serialize_artist(a: dict) -> dict:
    return {
        "id": str(a["id"]),
        "name": a["name"],
        "image_url": a.get("picture_medium") or a.get("picture"),
        "genres": [],
        "popularity": None,
    }


@router.get("/artists/search")
async def search_artists_endpoint(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=25),
    user_id: UUID = Depends(auth.get_current_user_id),
) -> list[dict]:
    try:
        artists = await spotify.search_artists(q, limit=limit)
    except httpx.HTTPStatusError as e:
        raise _upstream_to_http_error(e)

    return [_serialize_artist(a) for a in artists]


@router.get("/artists")
async def get_artists_by_ids(
    ids: str = Query(..., description="comma-separated Deezer artist ids"),
    user_id: UUID = Depends(auth.get_current_user_id),
) -> list[dict]:
    """Resolve a list of artist IDs to their summaries. Used by the settings
    UI to render chips for required_artists that were saved previously and
    aren't in the search cache (e.g. after a page reload or game restart)."""
    id_list = [s for s in (s.strip() for s in ids.split(",")) if s]
    if not id_list:
        return []
    try:
        artists = await asyncio.gather(
            *(spotify.get_artist(aid) for aid in id_list),
            return_exceptions=True,
        )
    except httpx.HTTPStatusError as e:
        raise _upstream_to_http_error(e)

    return [
        _serialize_artist(a)
        for a in artists
        if isinstance(a, dict) and a.get("id") is not None
    ]
