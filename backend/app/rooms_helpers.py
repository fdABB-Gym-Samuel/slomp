"""Shared helpers for the rooms router and the game module."""

import asyncio
import logging
import secrets
import time
from uuid import UUID

import asyncpg
from fastapi import HTTPException, status

from . import db, state
from .models import RoomOut, RoomPlayerOut, UserOut, decode_settings

logger = logging.getLogger("slomp.rooms")

# When the last member leaves, hold the room for this long before deleting.
# Gives drop-and-rejoin (e.g., URL bar fumbles) a window to re-populate the
# room before it's torn down for real.
EMPTY_ROOM_DELETE_DELAY_SECONDS = 60.0

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no O/0/I/1/L


def generate_room_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


_ROOM_COLUMNS = "id, code, name, is_public, leader_id, status, settings"


async def load_room_or_404(conn: asyncpg.Connection, room_id: UUID) -> asyncpg.Record:
    row = await conn.fetchrow(
        f"SELECT {_ROOM_COLUMNS} FROM rooms WHERE id = $1",
        room_id,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "room_not_found", "message": "room not found"},
        )
    return row


async def load_room_by_code_or_404(
    conn: asyncpg.Connection, code: str
) -> asyncpg.Record:
    row = await conn.fetchrow(
        f"SELECT {_ROOM_COLUMNS} FROM rooms WHERE code = $1",
        code,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "room_not_found", "message": "room not found"},
        )
    return row


async def assert_leader(room: asyncpg.Record, user_id: UUID) -> None:
    if room["leader_id"] != user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "not_leader", "message": "only the leader can do this"},
        )


async def assert_status(room: asyncpg.Record, *allowed: str) -> None:
    if room["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "wrong_phase",
                "message": f"action requires phase {allowed}, currently {room['status']}",
            },
        )


def room_key(room: asyncpg.Record | UUID) -> str:
    """Stable in-memory key for the WS hub and game registry. Uses the room
    id (which is permanent) so it survives the room's code being cleared
    when the room is made public."""
    if isinstance(room, UUID):
        return str(room)
    return str(room["id"])


async def remove_player_from_room(
    conn: asyncpg.Connection, room: asyncpg.Record, user_id: UUID
) -> tuple[bool, UUID | None]:
    """Drop the user from the room (membership + ephemeral identity) and
    hand off leadership if needed. Must run inside a transaction. Returns
    (room_now_empty, new_leader_id). The caller is responsible for kicking
    off `schedule_empty_room_cleanup` when room_now_empty is True — the
    actual room delete is deferred so a player who immediately rejoins
    (or someone who joins via code/link) can save it.

    Order matters: leader-transfer (or letting `rooms.leader_id` go NULL via
    the SET NULL FK) must happen before the user delete."""
    await conn.execute(
        "DELETE FROM room_players WHERE room_id = $1 AND user_id = $2",
        room["id"],
        user_id,
    )
    remaining = await conn.fetchval(
        "SELECT count(*) FROM room_players WHERE room_id = $1",
        room["id"],
    )
    new_leader: UUID | None = None
    if remaining == 0:
        # Drop the orphaned user. The SET NULL FK on rooms.leader_id keeps
        # the empty room alive for its cleanup-grace window.
        await conn.execute("DELETE FROM users WHERE id = $1", user_id)
        return True, None
    if room["leader_id"] == user_id:
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
    await conn.execute("DELETE FROM users WHERE id = $1", user_id)
    return False, new_leader


async def _cleanup_empty_room(room_id: UUID) -> None:
    """Reap an empty room. Lobby-phase rooms get the 60s grace window so a
    URL-bar fumble or quick refresh can recover them. Once the game has
    been started (any non-lobby phase), the room is dead — there's no game
    state worth saving and a late-joiner couldn't sensibly resume — so we
    delete immediately."""
    key = room_key(room_id)
    async with db.pool().acquire() as conn:
        status = await conn.fetchval("SELECT status FROM rooms WHERE id = $1", room_id)
    if status is None:
        return  # already gone

    if status == "lobby":
        state.empty_room_deadlines[key] = time.time() + EMPTY_ROOM_DELETE_DELAY_SECONDS
        try:
            await asyncio.sleep(EMPTY_ROOM_DELETE_DELAY_SECONDS)
        except asyncio.CancelledError:
            state.empty_room_deadlines.pop(key, None)
            return

    deleted = False
    try:
        async with db.pool().acquire() as conn:
            async with conn.transaction():
                still_empty = not await conn.fetchval(
                    "SELECT 1 FROM room_players WHERE room_id = $1",
                    room_id,
                )
                if not still_empty:
                    return
                exists = await conn.fetchval(
                    "SELECT 1 FROM rooms WHERE id = $1", room_id
                )
                if not exists:
                    return
                await conn.execute("DELETE FROM rooms WHERE id = $1", room_id)
                deleted = True
        await state.registry.remove(key)
    except Exception:
        logger.exception("empty-room cleanup failed for room=%s", room_id)
    finally:
        state.empty_room_deadlines.pop(key, None)
        if deleted:
            await broadcast_public_room_change(room_id)


def schedule_empty_room_cleanup(room_id: UUID) -> None:
    """Reap an empty room. Lobby phase gets a grace window so transient
    drops can recover; later phases get immediate cleanup."""
    asyncio.create_task(_cleanup_empty_room(room_id))


def clear_empty_room_cleanup(room_id: UUID) -> None:
    """Cancel any pending empty-room cleanup deadline. Use when a join
    repopulates a room — the deferred delete task self-checks and no-ops,
    but the deadline shouldn't keep advertising itself to clients."""
    state.empty_room_deadlines.pop(room_key(room_id), None)


async def claim_orphaned_leadership(
    conn: asyncpg.Connection, room: asyncpg.Record, user_id: UUID
) -> UUID | None:
    """If the room's leader is missing or no longer a member (e.g., they
    left during the empty-room cleanup window and someone else just joined),
    promote `user_id` to leader. Must be called inside a transaction *after*
    `user_id` has been inserted into room_players. Returns the new leader id
    when a transfer happened, else None.

    The leader_id is re-read from the DB rather than read off the in-memory
    `room` record, since `_wipe_existing_identity` may have already deleted
    the prior leader and (via `ON DELETE SET NULL`) cleared `leader_id`
    underneath us."""
    current_leader = await conn.fetchval(
        "SELECT leader_id FROM rooms WHERE id = $1", room["id"]
    )
    if current_leader is not None:
        leader_present = await conn.fetchval(
            "SELECT 1 FROM room_players WHERE room_id = $1 AND user_id = $2",
            room["id"],
            current_leader,
        )
        if leader_present:
            return None
    await conn.execute(
        "UPDATE rooms SET leader_id = $1 WHERE id = $2",
        user_id,
        room["id"],
    )
    return user_id


_PUBLIC_ROOM_QUERY = """
    SELECT r.id, r.name, r.is_public, r.status, r.settings,
           u.username AS leader_username,
           (SELECT count(*)::int FROM room_players rp WHERE rp.room_id = r.id)
               AS player_count
    FROM rooms r
    JOIN users u ON u.id = r.leader_id
    WHERE r.id = $1
"""


def _public_room_payload(row: asyncpg.Record) -> dict:
    settings = decode_settings(row["settings"])
    spp = settings.songs_per_player
    rsc = settings.random_song_count
    locked = settings.lock_after_lobby
    game_mode = settings.game_mode
    # When `lock_after_lobby` is on and the game has left the lobby, joining
    # is still allowed but the new player sits out until the current game
    # finishes. The browser surfaces this so the user knows what they're
    # signing up for before clicking Join.
    joins_as_spectator = locked and row["status"] != "lobby"
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "leader_username": row["leader_username"],
        "player_count": row["player_count"],
        "songs_per_player": spp,
        "random_song_count": rsc,
        "cleanup_at": state.empty_room_deadlines.get(str(row["id"])),
        "status": row["status"],
        "joins_as_spectator": joins_as_spectator,
        "game_mode": game_mode,
    }


async def broadcast_public_room_change(room_id: UUID) -> None:
    """Refetch the room and broadcast the right upsert/remove event to the
    home-page lobby. If the room is missing or no longer public, fire a
    `public_room_removed`; clients that don't have it stored simply ignore
    the event."""
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(_PUBLIC_ROOM_QUERY, room_id)
    if row is None or not row["is_public"]:
        await state.lobby_hub.broadcast(
            {
                "type": "public_room_removed",
                "payload": {"id": str(room_id)},
            }
        )
        return
    await state.lobby_hub.broadcast(
        {
            "type": "public_room_upsert",
            "payload": _public_room_payload(row),
        }
    )


async def list_public_rooms_payloads() -> list[dict]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.name, r.is_public, r.status, r.settings,
                   u.username AS leader_username,
                   (SELECT count(*)::int FROM room_players rp
                     WHERE rp.room_id = r.id) AS player_count
            FROM rooms r
            JOIN users u ON u.id = r.leader_id
            WHERE r.is_public
            ORDER BY r.created_at DESC
            LIMIT 100
            """
        )
    return [_public_room_payload(r) for r in rows]


async def leave_other_rooms(
    conn: asyncpg.Connection, user_id: UUID, except_room_id: UUID | None
) -> list[tuple[str, bool, UUID | None, UUID]]:
    """Remove the user from every room they're in except `except_room_id`.
    Must be called inside a transaction. Returns a list of
    (room_key, room_now_empty, new_leader_id, room_id) the caller should
    broadcast and act on after the transaction commits."""
    # Load every affected room in one query, then iterate. The previous
    # version did one fetchrow per room id (N+1) — almost always N=0 or 1
    # since a user can only be in one room at a time, but the loop pattern
    # invited misuse if anything ever changed.
    if except_room_id is None:
        rooms = await conn.fetch(
            f"SELECT {_ROOM_COLUMNS} FROM rooms WHERE id IN ("
            "SELECT room_id FROM room_players WHERE user_id = $1)",
            user_id,
        )
    else:
        rooms = await conn.fetch(
            f"SELECT {_ROOM_COLUMNS} FROM rooms WHERE id IN ("
            "SELECT room_id FROM room_players WHERE user_id = $1 AND room_id <> $2)",
            user_id,
            except_room_id,
        )
    results: list[tuple[str, bool, UUID | None, UUID]] = []
    for room in rooms:
        now_empty, new_leader = await remove_player_from_room(conn, room, user_id)
        results.append((room_key(room), now_empty, new_leader, room["id"]))
    return results


async def broadcast_leave_results(
    user_id: UUID, results: list[tuple[str, bool, UUID | None, UUID]]
) -> None:
    """Run the post-commit broadcasts for `leave_other_rooms` results."""
    for key, now_empty, new_leader, room_id in results:
        if now_empty:
            schedule_empty_room_cleanup(room_id)
            await broadcast_public_room_change(room_id)
            continue
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


async def build_room_out(conn: asyncpg.Connection, room: asyncpg.Record) -> RoomOut:
    rows = await conn.fetch(
        """
        SELECT
            rp.user_id,
            u.username,
            rp.score,
            rp.spectating,
            (
                SELECT count(*)::int FROM room_song_pickers rsp
                 WHERE rsp.room_id = rp.room_id AND rsp.user_id = rp.user_id
            ) AS submitted
        FROM room_players rp
        JOIN users u ON u.id = rp.user_id
        WHERE rp.room_id = $1
        ORDER BY rp.joined_at ASC
        """,
        room["id"],
    )
    key = room_key(room)
    connected = await state.hub.connected_users(key)

    players = [
        RoomPlayerOut(
            user=UserOut(id=r["user_id"], username=r["username"]),
            score=r["score"],
            connected=r["user_id"] in connected,
            songs_submitted=r["submitted"],
            spectating=r["spectating"],
            auto_leave_at=state.disconnect_deadlines.get((key, r["user_id"])),
        )
        for r in rows
    ]

    game = await state.registry.get(key)
    current_round_id = (
        game.active_round.round_id
        if game is not None and game.active_round is not None
        else None
    )

    return RoomOut(
        id=room["id"],
        code=room["code"],
        name=room["name"],
        is_public=room["is_public"],
        leader_id=room["leader_id"],
        status=room["status"],
        settings=decode_settings(room["settings"]),
        players=players,
        current_round_id=current_round_id,
    )


async def fetch_room_out(room_id: UUID) -> RoomOut:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, room_id)
        return await build_room_out(conn, room)
