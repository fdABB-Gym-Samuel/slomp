import json
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from .. import auth, db, deezer, game, state
from ..models import (
    CreateRoomRequest,
    JoinByCodeRequest,
    JoinRoomRequest,
    PhaseRequest,
    PublicRoomOut,
    RenameRequest,
    RoomInfoUpdate,
    RoomOut,
    RoomSettings,
    SubmitSongRequest,
    SubmittedSongOut,
    UserOut,
)
from ..matching import normalize_title
from ..rooms_helpers import (
    assert_leader,
    assert_status,
    broadcast_leave_results,
    broadcast_public_room_change,
    build_room_out,
    claim_orphaned_leadership,
    clear_empty_room_cleanup,
    fetch_room_out,
    generate_room_code,
    leave_other_rooms,
    list_public_rooms_payloads,
    load_room_by_code_or_404,
    load_room_or_404,
    remove_player_from_room,
    room_key,
    schedule_empty_room_cleanup,
)

router = APIRouter(prefix="/rooms", tags=["rooms"])


def _spectator_on_join(room: asyncpg.Record) -> bool:
    """Decide whether a fresh joiner should land in spectator mode. True
    when the room has left the lobby and `lock_after_lobby` is on; the new
    player is admitted but sits out until the current game ends and the
    room is back in the lobby."""
    if room["status"] == "lobby":
        return False
    raw = room["settings"]
    settings = RoomSettings.model_validate(
        json.loads(raw) if isinstance(raw, str) else (raw or {})
    )
    return settings.lock_after_lobby


async def _generate_unique_code(conn: asyncpg.Connection) -> str:
    for _ in range(20):
        code = generate_room_code()
        exists = await conn.fetchval("SELECT 1 FROM rooms WHERE code = $1", code)
        if not exists:
            return code
    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "room_code_collision",
            "message": "could not generate a unique room code",
        },
    )


async def _wipe_existing_identity(
    conn: asyncpg.Connection, incoming_session: str | None
) -> list[tuple[str, bool, UUID | None, UUID]]:
    """If the request carries a session cookie pointing to a live user,
    drop them from any room they're in (which also reaps the user row via
    `remove_player_from_room`). Returns the leave_results to broadcast
    post-commit. A no-op for fresh visitors and dead-cookie cases."""
    if not incoming_session:
        return []
    old_user_id = await auth.lookup_user_id(incoming_session)
    if old_user_id is None:
        return []
    return await leave_other_rooms(conn, old_user_id, None)


async def _establish_identity(
    conn: asyncpg.Connection,
    response: Response,
    username: str,
) -> UUID:
    """Create a fresh user row + session and stamp the cookie. Caller is
    responsible for `_wipe_existing_identity` first if there's an incoming
    cookie. Must run inside a transaction."""
    user_id = await conn.fetchval(
        "INSERT INTO users (username) VALUES ($1) RETURNING id",
        username,
    )
    token, expires_at = await auth.create_session_in_conn(conn, user_id)
    auth.set_session_cookie(response, token, expires_at)
    return user_id


async def _assert_name_free_in_room(
    conn: asyncpg.Connection,
    room_id: UUID,
    username: str,
    except_user_id: UUID | None = None,
) -> None:
    """Per-room username uniqueness — case-insensitive (users.username is
    CITEXT). Raises 409 on conflict."""
    if except_user_id is None:
        taken = await conn.fetchval(
            "SELECT 1 FROM room_players rp JOIN users u ON u.id = rp.user_id "
            "WHERE rp.room_id = $1 AND u.username = $2",
            room_id,
            username,
        )
    else:
        taken = await conn.fetchval(
            "SELECT 1 FROM room_players rp JOIN users u ON u.id = rp.user_id "
            "WHERE rp.room_id = $1 AND u.username = $2 AND rp.user_id <> $3",
            room_id,
            username,
            except_user_id,
        )
    if taken:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "name_taken", "message": "name already taken in this room"},
        )


@router.post("", status_code=201, response_model=RoomOut)
async def create_room(
    req: CreateRoomRequest,
    response: Response,
    session: str | None = Cookie(default=None, alias="session"),
) -> RoomOut:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            leave_results = await _wipe_existing_identity(conn, session)
            user_id = await _establish_identity(conn, response, req.username)
            code = await _generate_unique_code(conn)
            room_id = await conn.fetchval(
                "INSERT INTO rooms (code, leader_id) VALUES ($1, $2) RETURNING id",
                code,
                user_id,
            )
            await conn.execute(
                "INSERT INTO room_players (room_id, user_id) VALUES ($1, $2)",
                room_id,
                user_id,
            )
            room = await load_room_or_404(conn, room_id)
            out = await build_room_out(conn, room)
    await broadcast_leave_results(user_id, leave_results)
    return out


@router.get("/public", response_model=list[PublicRoomOut])
async def list_public_rooms() -> list[PublicRoomOut]:
    payloads = await list_public_rooms_payloads()
    return [PublicRoomOut.model_validate(p) for p in payloads]


@router.post("/join-by-code", response_model=RoomOut)
async def join_by_code(
    req: JoinByCodeRequest,
    response: Response,
    session: str | None = Cookie(default=None, alias="session"),
) -> RoomOut:
    code = req.code.strip().upper()
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_by_code_or_404(conn, code)
            await _assert_name_free_in_room(conn, room["id"], req.username)
            leave_results = await _wipe_existing_identity(conn, session)
            user_id = await _establish_identity(conn, response, req.username)
            spectating = _spectator_on_join(room)
            # Late joiners with `lock_after_lobby` off slot into the next
            # round naturally. With it on, they're flagged `spectating` so
            # `_start_next_round` skips them until the room returns to the
            # lobby.
            await conn.execute(
                "INSERT INTO room_players (room_id, user_id, spectating) "
                "VALUES ($1, $2, $3)",
                room["id"],
                user_id,
                spectating,
            )
            new_leader = await claim_orphaned_leadership(conn, room, user_id)
            if new_leader is not None:
                room = await load_room_or_404(conn, room["id"])
            out = await build_room_out(conn, room)
    clear_empty_room_cleanup(room["id"])
    await broadcast_leave_results(user_id, leave_results)
    if new_leader is not None:
        await state.hub.broadcast(
            room_key(room),
            {"type": "leader_changed", "payload": {"leader_id": str(new_leader)}},
        )
    await broadcast_public_room_change(room["id"])
    return out


@router.get("/{room_id}", response_model=RoomOut)
async def get_room(
    room_id: UUID, user_id: UUID = Depends(auth.get_current_user_id)
) -> RoomOut:
    return await fetch_room_out(room_id)


@router.patch("/{room_id}", response_model=RoomOut)
async def update_room_info(
    room_id: UUID,
    req: RoomInfoUpdate,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> RoomOut:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, room_id)
            await assert_leader(room, user_id)
            await assert_status(room, "lobby")

            sets: list[str] = []
            args: list = []
            if req.name is not None:
                trimmed = req.name.strip()
                sets.append(f"name = ${len(args) + 1}")
                args.append(trimmed if trimmed else None)
            if req.is_public is not None and req.is_public != room["is_public"]:
                sets.append(f"is_public = ${len(args) + 1}")
                args.append(req.is_public)
                # Public rooms have no join code (they're discoverable via the
                # browser). Toggling back to private mints a fresh one so any
                # previously-shared code is invalidated.
                if req.is_public:
                    sets.append(f"code = ${len(args) + 1}")
                    args.append(None)
                else:
                    new_code = await _generate_unique_code(conn)
                    sets.append(f"code = ${len(args) + 1}")
                    args.append(new_code)

            if sets:
                args.append(room["id"])
                await conn.execute(
                    f"UPDATE rooms SET {', '.join(sets)} WHERE id = ${len(args)}",
                    *args,
                )
                room = await load_room_or_404(conn, room_id)
            out = await build_room_out(conn, room)

    await state.hub.broadcast(
        room_key(room),
        {
            "type": "room_info_updated",
            "payload": {
                "name": out.name,
                "is_public": out.is_public,
                "code": out.code,
            },
        },
    )
    await broadcast_public_room_change(room["id"])
    return out


@router.post("/{room_id}/join", response_model=RoomOut)
async def join_room(
    room_id: UUID,
    req: JoinRoomRequest,
    response: Response,
    session: str | None = Cookie(default=None, alias="session"),
) -> RoomOut:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, room_id)
            # Joining by id (e.g. clicking through from the public list) is
            # only allowed on public rooms; private rooms must be joined with
            # their code via /rooms/join-by-code.
            if not room["is_public"]:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "private_room",
                        "message": "this room is private; join with its code",
                    },
                )
            await _assert_name_free_in_room(conn, room["id"], req.username)
            leave_results = await _wipe_existing_identity(conn, session)
            user_id = await _establish_identity(conn, response, req.username)
            # Late joiners (room past lobby phase) start with score 0, sit
            # out the in-flight round, and join the next round naturally.
            spectating = _spectator_on_join(room)
            await conn.execute(
                "INSERT INTO room_players (room_id, user_id, spectating) "
                "VALUES ($1, $2, $3)",
                room["id"],
                user_id,
                spectating,
            )
            new_leader = await claim_orphaned_leadership(conn, room, user_id)
            if new_leader is not None:
                room = await load_room_or_404(conn, room["id"])
            out = await build_room_out(conn, room)
    clear_empty_room_cleanup(room["id"])
    await broadcast_leave_results(user_id, leave_results)
    if new_leader is not None:
        await state.hub.broadcast(
            room_key(room),
            {"type": "leader_changed", "payload": {"leader_id": str(new_leader)}},
        )
    await broadcast_public_room_change(room["id"])
    return out


@router.post("/{room_id}/leave", status_code=204)
async def leave_room(
    room_id: UUID,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> Response:
    key = room_key(room_id)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, room_id)
            now_empty, new_leader = await remove_player_from_room(conn, room, user_id)

    # The user row was cascade-deleted inside remove_player_from_room, so
    # the session is gone too. Clear the cookie so the browser stops
    # advertising a dead token.
    out = Response(status_code=204)
    auth.clear_session_cookie(out)

    if now_empty:
        schedule_empty_room_cleanup(room_id)
        await broadcast_public_room_change(room_id)
        return out
    await state.hub.broadcast(
        key,
        {"type": "player_left", "payload": {"user_id": str(user_id)}},
    )
    if new_leader is not None:
        await state.hub.broadcast(
            key,
            {"type": "leader_changed", "payload": {"leader_id": str(new_leader)}},
        )
    await broadcast_public_room_change(room_id)
    return out


@router.patch("/{room_id}/me/username", response_model=UserOut)
async def rename_in_room(
    room_id: UUID,
    req: RenameRequest,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> UserOut:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            is_member = await conn.fetchval(
                "SELECT 1 FROM room_players WHERE room_id = $1 AND user_id = $2",
                room_id,
                user_id,
            )
            if not is_member:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "not_in_room",
                        "message": "you are not in this room",
                    },
                )
            await _assert_name_free_in_room(
                conn, room_id, req.username, except_user_id=user_id
            )
            row = await conn.fetchrow(
                "UPDATE users SET username = $1 WHERE id = $2 RETURNING id, username",
                req.username,
                user_id,
            )

    out = UserOut(id=row["id"], username=row["username"])
    await state.hub.broadcast(
        room_key(room_id),
        {
            "type": "player_renamed",
            "payload": {"user_id": str(user_id), "username": out.username},
        },
    )
    # Public-rooms list shows the leader's username, so refresh it on a
    # leader rename. Cheap to fire unconditionally.
    await broadcast_public_room_change(room_id)
    return out


@router.patch("/{room_id}/settings", response_model=RoomOut)
async def update_settings(
    room_id: UUID,
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
            room = await load_room_or_404(conn, room_id)
            await assert_leader(room, user_id)
            await assert_status(room, "lobby")
            await conn.execute(
                "UPDATE rooms SET settings = $1::jsonb WHERE id = $2",
                json.dumps(new_settings.model_dump()),
                room["id"],
            )
            room = await load_room_or_404(conn, room_id)
            out = await build_room_out(conn, room)

    await state.hub.broadcast(
        room_key(room),
        {
            "type": "settings_updated",
            "payload": {"settings": new_settings.model_dump()},
        },
    )
    await broadcast_public_room_change(room["id"])
    return out


_CLASSIC_TRANSITIONS = {
    "lobby": {"selecting"},
    "selecting": {"playing", "lobby"},
    "playing": {"results"},
    "results": {"lobby"},
}

# Random mode skips the selecting phase entirely — the leader hits "Start"
# from the lobby and the server fills the queue with chart picks.
_RANDOM_TRANSITIONS = {
    "lobby": {"playing"},
    "playing": {"results"},
    "results": {"lobby"},
}


@router.post("/{room_id}/phase", response_model=RoomOut)
async def change_phase(
    room_id: UUID,
    req: PhaseRequest,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> RoomOut:
    target = req.target

    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, room_id)
        await assert_leader(room, user_id)

    settings = RoomSettings.model_validate(
        room["settings"]
        if isinstance(room["settings"], dict)
        else json.loads(room["settings"])
    )
    is_random = settings.game_mode == "random"
    transitions = _RANDOM_TRANSITIONS if is_random else _CLASSIC_TRANSITIONS
    if target not in transitions.get(room["status"], set()):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "illegal_transition",
                "message": f"cannot transition {room['status']} -> {target}",
            },
        )

    # In random mode, fetch the song pool from Deezer up-front (outside any
    # DB transaction) so a Deezer outage leaves the room in lobby with a
    # clean error rather than stranding it in `playing` with no songs.
    random_tracks: list[dict] = []
    if is_random and target == "playing":
        async with db.pool().acquire() as conn:
            active_count = await conn.fetchval(
                "SELECT count(*) FROM room_players "
                "WHERE room_id = $1 AND spectating = FALSE",
                room["id"],
            )
        if active_count < 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "not_enough_players",
                    "message": "need at least 1 active player to start",
                },
            )
        try:
            random_tracks = await deezer.fetch_random_tracks(
                settings.min_popularity, settings.random_song_count
            )
        except Exception:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "music_upstream_error",
                    "message": "could not fetch tracks from Deezer",
                },
            )
        if not random_tracks:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "no_tracks_match",
                    "message": (
                        "no tracks matched the popularity threshold; "
                        "lower min_popularity and try again"
                    ),
                },
            )

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, room_id)
            await assert_leader(room, user_id)
            if target not in transitions.get(room["status"], set()):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "illegal_transition",
                        "message": f"cannot transition {room['status']} -> {target}",
                    },
                )

            if target == "selecting":
                # Solo games are degenerate (the picker can't guess their
                # own songs), so block the transition before everyone wastes
                # time picking.
                player_count = await conn.fetchval(
                    "SELECT count(*) FROM room_players WHERE room_id = $1",
                    room["id"],
                )
                if player_count < 2:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail={
                            "code": "not_enough_players",
                            "message": "need at least 2 players to start",
                        },
                    )

            if target == "playing" and not is_random:
                # Verify every active (non-spectator) player has submitted
                # exactly songs_per_player tracks. Spectators sit out the
                # current game entirely, so they don't count toward the
                # shortfall.
                active_count = await conn.fetchval(
                    "SELECT count(*) FROM room_players "
                    "WHERE room_id = $1 AND spectating = FALSE",
                    room["id"],
                )
                if active_count < 2:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail={
                            "code": "not_enough_players",
                            "message": "need at least 2 active players to start",
                        },
                    )
                shortfall = await conn.fetchrow(
                    """
                    SELECT rp.user_id, u.username, count(rsp.song_id)::int AS submitted
                    FROM room_players rp
                    JOIN users u ON u.id = rp.user_id
                    LEFT JOIN room_song_pickers rsp
                           ON rsp.room_id = rp.room_id AND rsp.user_id = rp.user_id
                    WHERE rp.room_id = $1 AND rp.spectating = FALSE
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

            if target == "playing" and is_random:
                # Wipe stale songs from any prior game (restart_game also
                # clears these, but a leader could have toggled game_mode
                # mid-lobby) and seed the queue with the fetched pool.
                await conn.execute(
                    "DELETE FROM room_songs WHERE room_id = $1", room["id"]
                )
                for i, track in enumerate(random_tracks):
                    cand = deezer.serialize_candidate(track)
                    await conn.execute(
                        """
                        INSERT INTO room_songs
                            (room_id, spotify_track_id, title, title_normalized,
                             artist, album, preview_url, album_image_url,
                             duration_ms, popularity, play_order)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        """,
                        room["id"],
                        cand["spotify_track_id"],
                        cand["title"],
                        normalize_title(cand["title"]),
                        cand["artist"],
                        cand.get("album"),
                        cand["preview_url"],
                        cand["album_image_url"],
                        cand["duration_ms"],
                        cand["popularity"],
                        i,
                    )

            await conn.execute(
                "UPDATE rooms SET status = $1 WHERE id = $2",
                target,
                room["id"],
            )
            if target == "lobby":
                # Returning to the lobby promotes any waiting spectators
                # back to full members for the next game.
                await conn.execute(
                    "UPDATE room_players SET spectating = FALSE WHERE room_id = $1",
                    room["id"],
                )
            room = await load_room_or_404(conn, room_id)
            out = await build_room_out(conn, room)

    key = room_key(room)
    await state.hub.broadcast(
        key,
        {"type": "phase_changed", "payload": {"status": target}},
    )
    await broadcast_public_room_change(room["id"])

    if target == "playing":
        await game.start_game(key)

    return out


@router.post("/{room_id}/players/{target_user_id}/promote", response_model=RoomOut)
async def promote_player(
    room_id: UUID,
    target_user_id: UUID,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> RoomOut:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, room_id)
            await assert_leader(room, user_id)
            await assert_status(room, "lobby")
            if target_user_id == user_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "already_leader",
                        "message": "you are already the leader",
                    },
                )
            is_member = await conn.fetchval(
                "SELECT 1 FROM room_players WHERE room_id = $1 AND user_id = $2",
                room["id"],
                target_user_id,
            )
            if not is_member:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "player_not_in_room",
                        "message": "target user is not in this room",
                    },
                )
            await conn.execute(
                "UPDATE rooms SET leader_id = $1 WHERE id = $2",
                target_user_id,
                room["id"],
            )
            room = await load_room_or_404(conn, room_id)
            out = await build_room_out(conn, room)

    await state.hub.broadcast(
        room_key(room),
        {"type": "leader_changed", "payload": {"leader_id": str(target_user_id)}},
    )
    await broadcast_public_room_change(room["id"])
    return out


@router.delete("/{room_id}/players/{target_user_id}", status_code=204)
async def kick_player(
    room_id: UUID,
    target_user_id: UUID,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> Response:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, room_id)
            await assert_leader(room, user_id)
            await assert_status(room, "lobby")
            if target_user_id == user_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "cannot_kick_self",
                        "message": "use leave instead of kicking yourself",
                    },
                )
            is_member = await conn.fetchval(
                "SELECT 1 FROM room_players WHERE room_id = $1 AND user_id = $2",
                room["id"],
                target_user_id,
            )
            if not is_member:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "player_not_in_room",
                        "message": "target user is not in this room",
                    },
                )
            now_empty, new_leader = await remove_player_from_room(
                conn, room, target_user_id
            )

    await state.hub.broadcast(
        room_key(room),
        {"type": "player_kicked", "payload": {"user_id": str(target_user_id)}},
    )
    if new_leader is not None:
        await state.hub.broadcast(
            room_key(room),
            {"type": "leader_changed", "payload": {"leader_id": str(new_leader)}},
        )
    if now_empty:
        schedule_empty_room_cleanup(room["id"])
    await broadcast_public_room_change(room["id"])
    return Response(status_code=204)


@router.post("/{room_id}/restart", response_model=RoomOut)
async def restart_room(
    room_id: UUID, user_id: UUID = Depends(auth.get_current_user_id)
) -> RoomOut:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, room_id)
        await assert_leader(room, user_id)
        await assert_status(room, "results")

    key = room_key(room)
    await game.restart_game(key)

    out = await fetch_room_out(room_id)
    await state.hub.broadcast(
        key,
        {"type": "phase_changed", "payload": {"status": "lobby"}},
    )
    await broadcast_public_room_change(room_id)
    return out


# ---------- songs ---------------------------------------------------------


@router.get("/{room_id}/songs", response_model=list[SubmittedSongOut])
async def my_songs(
    room_id: UUID, user_id: UUID = Depends(auth.get_current_user_id)
) -> list[SubmittedSongOut]:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, room_id)
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


@router.post("/{room_id}/songs", status_code=201, response_model=SubmittedSongOut)
async def submit_song(
    room_id: UUID,
    req: SubmitSongRequest,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> SubmittedSongOut:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, room_id)
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

    track = await deezer.get_track(req.spotify_track_id)
    ok, reason = deezer.matches_rules(track, settings.model_dump())
    if not ok:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "rule_violation",
                "message": reason or "track does not match room rules",
            },
        )

    candidate = deezer.serialize_candidate(track)

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
        room_key(room),
        {
            "type": "song_submitted",
            "payload": {"user_id": str(user_id), "count": already + 1},
        },
    )
    return SubmittedSongOut(**dict(row))


@router.delete("/{room_id}/songs/{song_id}", status_code=204)
async def delete_song(
    room_id: UUID,
    song_id: UUID,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> Response:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await load_room_or_404(conn, room_id)
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
            remaining = await conn.fetchval(
                "SELECT count(*)::int FROM room_song_pickers "
                "WHERE room_id = $1 AND user_id = $2",
                room["id"],
                user_id,
            )

    await state.hub.broadcast(
        room_key(room),
        {
            "type": "song_removed",
            "payload": {"user_id": str(user_id), "count": remaining},
        },
    )
    return Response(status_code=204)


# ---------- results -------------------------------------------------------


@router.get("/{room_id}/results")
async def get_results(
    room_id: UUID, user_id: UUID = Depends(auth.get_current_user_id)
) -> list[dict]:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, room_id)
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
            WHERE rp.room_id = $1 AND rp.spectating = FALSE
            ORDER BY rp.score DESC, u.username ASC
            """,
            room["id"],
        )
    return [
        {"user": {"id": str(r["id"]), "username": r["username"]}, "score": r["score"]}
        for r in rows
    ]
