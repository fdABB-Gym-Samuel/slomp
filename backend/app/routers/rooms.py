import json
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from .. import auth, db, game, spotify, state
from ..models import (
    PhaseRequest,
    RoomOut,
    RoomSettings,
    SubmitSongRequest,
    SubmittedSongOut,
)
from ..matching import normalize_title
from ..rooms_helpers import (
    assert_leader,
    assert_status,
    build_room_out,
    fetch_room_out,
    generate_room_code,
    load_room_or_404,
)

router = APIRouter(prefix="/rooms", tags=["rooms"])


@router.post("", status_code=201, response_model=RoomOut)
async def create_room(user_id: UUID = Depends(auth.get_current_user_id)) -> RoomOut:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            for _ in range(10):
                code = generate_room_code()
                try:
                    await conn.execute(
                        "INSERT INTO rooms (code, leader_id) VALUES ($1, $2)",
                        code,
                        user_id,
                    )
                    break
                except asyncpg.UniqueViolationError:
                    continue
            else:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "code": "room_code_collision",
                        "message": "could not generate a unique room code",
                    },
                )
            await conn.execute(
                "INSERT INTO room_players (room_id, user_id) "
                "VALUES ((SELECT id FROM rooms WHERE code = $1), $2)",
                code,
                user_id,
            )
            room = await load_room_or_404(conn, code)
            return await build_room_out(conn, room)


@router.get("/{code}", response_model=RoomOut)
async def get_room(
    code: str, user_id: UUID = Depends(auth.get_current_user_id)
) -> RoomOut:
    return await fetch_room_out(code)


@router.post("/{code}/join", response_model=RoomOut)
async def join_room(
    code: str, user_id: UUID = Depends(auth.get_current_user_id)
) -> RoomOut:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, code)
            if room["status"] != "lobby":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "room_in_progress",
                        "message": "room is no longer in lobby phase",
                    },
                )
            await conn.execute(
                "INSERT INTO room_players (room_id, user_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                room["id"],
                user_id,
            )
            return await build_room_out(conn, room)


@router.post("/{code}/leave", status_code=204)
async def leave_room(
    code: str, user_id: UUID = Depends(auth.get_current_user_id)
) -> Response:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, code)
            await conn.execute(
                "DELETE FROM room_players WHERE room_id = $1 AND user_id = $2",
                room["id"],
                user_id,
            )
            remaining = await conn.fetchval(
                "SELECT count(*) FROM room_players WHERE room_id = $1",
                room["id"],
            )
            if remaining == 0:
                await conn.execute("DELETE FROM rooms WHERE id = $1", room["id"])
                await state.registry.remove(code)
            elif room["leader_id"] == user_id:
                # Leadership passes to the earliest remaining joiner
                new_leader = await conn.fetchval(
                    "SELECT user_id FROM room_players WHERE room_id = $1 "
                    "ORDER BY joined_at ASC LIMIT 1",
                    room["id"],
                )
                await conn.execute(
                    "UPDATE rooms SET leader_id = $1 WHERE id = $2",
                    new_leader,
                    room["id"],
                )

    await state.hub.broadcast(
        code,
        {"type": "player_left", "payload": {"user_id": str(user_id)}},
    )
    return Response(status_code=204)


@router.patch("/{code}/settings", response_model=RoomOut)
async def update_settings(
    code: str,
    new_settings: RoomSettings,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> RoomOut:
    brackets = new_settings.guess_brackets_seconds
    if not brackets or any(b <= 0 for b in brackets):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_brackets",
                "message": "guess_brackets_seconds must be positive",
            },
        )
    if any(brackets[i] >= brackets[i + 1] for i in range(len(brackets) - 1)):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_brackets",
                "message": "guess_brackets_seconds must be strictly ascending",
            },
        )

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, code)
            await assert_leader(room, user_id)
            await assert_status(room, "lobby")
            await conn.execute(
                "UPDATE rooms SET settings = $1::jsonb WHERE id = $2",
                json.dumps(new_settings.model_dump()),
                room["id"],
            )
            room = await load_room_or_404(conn, code)
            out = await build_room_out(conn, room)

    await state.hub.broadcast(
        code,
        {
            "type": "settings_updated",
            "payload": {"settings": new_settings.model_dump()},
        },
    )
    return out


@router.post("/{code}/phase", response_model=RoomOut)
async def change_phase(
    code: str,
    req: PhaseRequest,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> RoomOut:
    target = req.target

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, code)
            await assert_leader(room, user_id)

            current = room["status"]
            valid_transitions = {
                "lobby": {"selecting"},
                "selecting": {"playing", "lobby"},
                "playing": {"results"},
                "results": {"lobby"},
            }
            if target not in valid_transitions.get(current, set()):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "illegal_transition",
                        "message": f"cannot transition {current} -> {target}",
                    },
                )

            if target == "playing":
                # Verify every player has submitted exactly songs_per_player tracks
                settings = RoomSettings.model_validate(
                    room["settings"]
                    if isinstance(room["settings"], dict)
                    else json.loads(room["settings"])
                )
                shortfall = await conn.fetchrow(
                    """
                    SELECT rp.user_id, u.username, count(rsp.song_id)::int AS submitted
                    FROM room_players rp
                    JOIN users u ON u.id = rp.user_id
                    LEFT JOIN room_song_pickers rsp
                           ON rsp.room_id = rp.room_id AND rsp.user_id = rp.user_id
                    WHERE rp.room_id = $1
                    GROUP BY rp.user_id, u.username
                    HAVING count(rsp.song_id) <> $2
                    LIMIT 1
                    """,
                    room["id"],
                    settings.songs_per_player,
                )
                if shortfall is not None:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail={
                            "code": "incomplete_submissions",
                            "message": (
                                f"{shortfall['username']} has submitted "
                                f"{shortfall['submitted']} songs (need {settings.songs_per_player})"
                            ),
                        },
                    )

            await conn.execute(
                "UPDATE rooms SET status = $1 WHERE id = $2",
                target,
                room["id"],
            )
            room = await load_room_or_404(conn, code)
            out = await build_room_out(conn, room)

    await state.hub.broadcast(
        code,
        {"type": "phase_changed", "payload": {"status": target}},
    )

    if target == "playing":
        await game.start_game(code)

    return out


@router.post("/{code}/restart", response_model=RoomOut)
async def restart_room(
    code: str, user_id: UUID = Depends(auth.get_current_user_id)
) -> RoomOut:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, code)
        await assert_leader(room, user_id)
        await assert_status(room, "results")

    await game.restart_game(code)

    out = await fetch_room_out(code)
    await state.hub.broadcast(
        code,
        {"type": "phase_changed", "payload": {"status": "lobby"}},
    )
    return out


# ---------- songs ---------------------------------------------------------


@router.get("/{code}/songs", response_model=list[SubmittedSongOut])
async def my_songs(
    code: str, user_id: UUID = Depends(auth.get_current_user_id)
) -> list[SubmittedSongOut]:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, code)
        rows = await conn.fetch(
            """
            SELECT rs.id, rs.spotify_track_id, rs.title, rs.artist,
                   rs.preview_url, rs.album_image_url
            FROM room_song_pickers rsp
            JOIN room_songs rs ON rs.id = rsp.song_id
            WHERE rsp.room_id = $1 AND rsp.user_id = $2
            ORDER BY rsp.submitted_at ASC
            """,
            room["id"],
            user_id,
        )
    return [SubmittedSongOut(**dict(r)) for r in rows]


@router.post("/{code}/songs", status_code=201, response_model=SubmittedSongOut)
async def submit_song(
    code: str,
    req: SubmitSongRequest,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> SubmittedSongOut:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, code)
        await assert_status(room, "selecting")

        settings = RoomSettings.model_validate(
            room["settings"]
            if isinstance(room["settings"], dict)
            else json.loads(room["settings"])
        )

        already = await conn.fetchval(
            "SELECT count(*)::int FROM room_song_pickers "
            "WHERE room_id = $1 AND user_id = $2",
            room["id"],
            user_id,
        )
        if already >= settings.songs_per_player:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "quota_reached",
                    "message": "you've already submitted enough songs",
                },
            )

    track = await spotify.get_track(req.spotify_track_id)
    ok, reason = spotify.matches_rules(track, settings.model_dump())
    if not ok:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "rule_violation",
                "message": reason or "track does not match room rules",
            },
        )

    candidate = spotify.serialize_candidate(track)

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                """
                SELECT id, spotify_track_id, title, artist, preview_url, album_image_url
                FROM room_songs
                WHERE room_id = $1 AND spotify_track_id = $2
                """,
                room["id"],
                req.spotify_track_id,
            )
            if existing is None:
                order_row = await conn.fetchval(
                    "SELECT COALESCE(MAX(play_order), -1) + 1 FROM room_songs WHERE room_id = $1",
                    room["id"],
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO room_songs
                        (room_id, spotify_track_id, title, title_normalized,
                         artist, album, preview_url, album_image_url,
                         duration_ms, popularity, play_order)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING id, spotify_track_id, title, artist, preview_url, album_image_url
                    """,
                    room["id"],
                    req.spotify_track_id,
                    candidate["title"],
                    normalize_title(candidate["title"]),
                    candidate["artist"],
                    candidate.get("album"),
                    candidate["preview_url"],
                    candidate["album_image_url"],
                    candidate["duration_ms"],
                    candidate["popularity"],
                    order_row,
                )
            else:
                row = existing
            try:
                await conn.execute(
                    "INSERT INTO room_song_pickers (room_id, song_id, user_id) "
                    "VALUES ($1, $2, $3)",
                    room["id"],
                    row["id"],
                    user_id,
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "duplicate_song",
                        "message": "you've already picked this track",
                    },
                )

    await state.hub.broadcast(
        code,
        {
            "type": "song_submitted",
            "payload": {"user_id": str(user_id), "count": already + 1},
        },
    )
    return SubmittedSongOut(**dict(row))


@router.delete("/{code}/songs/{song_id}", status_code=204)
async def delete_song(
    code: str,
    song_id: UUID,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> Response:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, code)
            await assert_status(room, "selecting")
            deleted = await conn.execute(
                "DELETE FROM room_song_pickers "
                "WHERE song_id = $1 AND room_id = $2 AND user_id = $3",
                song_id,
                room["id"],
                user_id,
            )
            if deleted == "DELETE 0":
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "song_not_found",
                        "message": "no such song or not yours",
                    },
                )
            # If this was the last picker, drop the song row so it doesn't
            # linger in the queue. ON DELETE CASCADE handles the picker rows.
            await conn.execute(
                """
                DELETE FROM room_songs
                WHERE id = $1
                  AND NOT EXISTS (
                      SELECT 1 FROM room_song_pickers WHERE song_id = $1
                  )
                """,
                song_id,
            )
    return Response(status_code=204)


# ---------- results -------------------------------------------------------


@router.get("/{code}/results")
async def get_results(
    code: str, user_id: UUID = Depends(auth.get_current_user_id)
) -> list[dict]:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, code)
        if room["status"] != "results":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "wrong_phase",
                    "message": "results are only available after the game ends",
                },
            )
        rows = await conn.fetch(
            """
            SELECT u.id, u.username, rp.score
            FROM room_players rp
            JOIN users u ON u.id = rp.user_id
            WHERE rp.room_id = $1
            ORDER BY rp.score DESC, u.username ASC
            """,
            room["id"],
        )
    return [
        {"user": {"id": str(r["id"]), "username": r["username"]}, "score": r["score"]}
        for r in rows
    ]
