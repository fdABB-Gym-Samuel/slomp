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
    room_code: str | None = Query(default=None),
    # Spotify caps Client-Credentials search at 10 results. Their docs still
    # say 50, but anything ≥15 returns 400 "Invalid limit". Capped at 10.
    limit: int = Query(default=10, ge=1, le=10),
    user_id: UUID = Depends(auth.get_current_user_id),
) -> list[SongCandidate]:
    rules: dict | None = None
    if room_code:
        async with db.pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT settings FROM rooms WHERE code = $1", room_code
            )
        if row is not None:
            raw = row["settings"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            rules = RoomSettings.model_validate(raw or {}).model_dump()

    try:
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
    return out


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

    return [
        {
            "id": str(a["id"]),
            "name": a["name"],
            "image_url": a.get("picture_medium") or a.get("picture"),
            "genres": [],
            "popularity": None,
        }
        for a in artists
    ]
