import asyncio
import logging
import time
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from .. import auth, db, state
from ..rooms_helpers import (
    broadcast_public_room_change,
    build_room_out,
    list_public_rooms_payloads,
    remove_player_from_room,
    room_key,
    schedule_empty_room_cleanup,
)

logger = logging.getLogger("slomp.ws")
router = APIRouter()

# Time to wait after a WS disconnect before auto-leaving a player from a
# lobby-phase room. Long enough to absorb the client's ~1.5s reconnect
# timer and quick tab swaps; short enough that tab-closes and url-changes
# free the slot promptly.
LOBBY_DISCONNECT_GRACE_SECONDS = 10.0


async def _auto_leave_if_stale(room_id: UUID, user_id: UUID) -> None:
    """After the grace period, drop the player from the room if they're
    still disconnected and the room is still in lobby phase. Lobby-only
    because mid-game leaves would forfeit a player's score and round
    progress; in those phases we keep stale members and just show them as
    disconnected."""
    key = room_key(room_id)
    try:
        await asyncio.sleep(LOBBY_DISCONNECT_GRACE_SECONDS)
    except asyncio.CancelledError:
        return

    if await state.hub.is_connected(key, user_id):
        return

    # If the deadline has been canceled (reconnect) or pushed forward (a
    # later disconnect superseded ours), let the canceller / newer task
    # handle the deadline. Don't pop it ourselves — the newer task owns it.
    deadline = state.disconnect_deadlines.get((key, user_id))
    if deadline is None or deadline > time.time() + 0.5:
        return

    new_leader: UUID | None = None
    now_empty = False
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await conn.fetchrow(
                "SELECT id, code, name, is_public, leader_id, status, settings "
                "FROM rooms WHERE id = $1",
                room_id,
            )
            if room is None or room["status"] != "lobby":
                state.disconnect_deadlines.pop((key, user_id), None)
                return
            still_member = await conn.fetchval(
                "SELECT 1 FROM room_players WHERE room_id = $1 AND user_id = $2",
                room_id,
                user_id,
            )
            if not still_member:
                state.disconnect_deadlines.pop((key, user_id), None)
                return
            now_empty, new_leader = await remove_player_from_room(conn, room, user_id)
    state.disconnect_deadlines.pop((key, user_id), None)

    if now_empty:
        schedule_empty_room_cleanup(room_id)
        await broadcast_public_room_change(room_id)
        return
    await state.hub.broadcast(
        key,
        {"type": "player_left", "payload": {"user_id": str(user_id)}},
    )
    if new_leader is not None:
        await state.hub.broadcast(
            key,
            {
                "type": "leader_changed",
                "payload": {"leader_id": str(new_leader)},
            },
        )
    await broadcast_public_room_change(room_id)


async def _replay_active_round(
    websocket: WebSocket, key: str, user_id: UUID, spectating: bool
) -> None:
    """Re-emit whatever round-scoped events a mid-round reconnect needs to
    rebuild the UI: either the live round in progress, or the post-round
    intermission scoreboard if we're between rounds. Mirrors the live
    broadcast shapes in `game._start_next_round`, `submit_guess`,
    `submit_skip`, `_send_picker_attempt`, and `_end_round`."""
    game = await state.registry.get(key)
    if game is None:
        return

    # Between rounds: the active round has ended but the next one hasn't
    # started yet (server is sleeping the intermission). Replay round_ended
    # with the remaining intermission time so the client shows the same
    # scoreboard reveal it would have seen had it stayed connected.
    if game.active_round is None:
        if game.last_round_payload is None or game.intermission_ends_at is None:
            return
        remaining = max(
            0.0, game.intermission_ends_at - asyncio.get_running_loop().time()
        )
        payload = dict(game.last_round_payload)
        payload["intermission_seconds"] = remaining
        await websocket.send_json({"type": "round_ended", "payload": payload})
        return

    # A non-spectator who joined after the round started is missing from
    # active.players (which was built from room_players at round-start). Slot
    # them in at bracket 0 so they can guess this round, see the obscured
    # cover, and fetch sliced audio — instead of being treated as "finished".
    async with game.lock:
        active = game.active_round
        if (
            active is not None
            and not spectating
            and user_id not in active.picker_ids
            and user_id not in active.players
        ):
            active.players[user_id] = state.PlayerRoundState(user_id=user_id)

    if game.active_round is None:
        return
    active = game.active_round

    audio_url = f"/rooms/{key}/rounds/{active.round_id}/audio"
    cover_url = f"/rooms/{key}/rounds/{active.round_id}/cover"
    await websocket.send_json(
        {
            "type": "round_started",
            "payload": {
                "round_id": str(active.round_id),
                "picker_ids": [str(pid) for pid in active.picker_ids],
                "started_at_server": active.started_at.isoformat(),
                "audio_url": audio_url,
                "album_image_url": cover_url
                if active.album_art_enabled and active.album_image_url
                else None,
                "guess_brackets_seconds": active.brackets,
                "round_max_seconds": active.round_max_seconds,
            },
        }
    )

    for uid, pstate in active.players.items():
        if pstate.bracket_index > 0 and not pstate.finished:
            await websocket.send_json(
                {
                    "type": "bracket_unlocked",
                    "payload": {
                        "round_id": str(active.round_id),
                        "user_id": str(uid),
                        "bracket_index": pstate.bracket_index,
                    },
                }
            )
        if pstate.finished:
            await websocket.send_json(
                {
                    "type": "player_finished",
                    "payload": {
                        "round_id": str(active.round_id),
                        "user_id": str(uid),
                        "outcome": pstate.outcome,
                        "points": pstate.points,
                    },
                }
            )

    if user_id in active.picker_ids and active.picker_attempts:
        await websocket.send_json(
            {
                "type": "picker_view",
                "payload": {
                    "round_id": str(active.round_id),
                    "attempts": active.picker_attempts,
                },
            }
        )

    # Restore the reconnecting user's own attempt history (their own
    # past guesses + skips this round) so the UI above the play button
    # is not blank after a refresh.
    own_attempts = [
        a for a in active.picker_attempts if a.get("user_id") == str(user_id)
    ]
    if own_attempts:
        await websocket.send_json(
            {
                "type": "my_attempts",
                "payload": {
                    "round_id": str(active.round_id),
                    "attempts": own_attempts,
                },
            }
        )


@router.websocket("/rooms/{room_id}/ws")
async def room_ws(websocket: WebSocket, room_id: UUID) -> None:
    token = websocket.cookies.get("session")
    user_id = await auth.lookup_user_id_for_ws(token)
    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with db.pool().acquire() as conn:
        room = await conn.fetchrow(
            "SELECT id, code, name, is_public, leader_id, status, settings "
            "FROM rooms WHERE id = $1",
            room_id,
        )
        if room is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        is_member = await conn.fetchval(
            "SELECT 1 FROM room_players WHERE room_id = $1 AND user_id = $2",
            room["id"],
            user_id,
        )
        if not is_member:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    key = room_key(room)
    await websocket.accept()
    previous = await state.hub.add(key, user_id, websocket)
    if previous is not None:
        try:
            await previous.close(code=status.WS_1000_NORMAL_CLOSURE)
        except Exception:
            pass

    # Coming back from a drop cancels any pending auto-leave countdown for
    # this user.
    state.disconnect_deadlines.pop((key, user_id), None)

    try:
        async with db.pool().acquire() as conn:
            await conn.execute(
                "UPDATE room_players SET connected = TRUE "
                "WHERE room_id = $1 AND user_id = $2",
                room["id"],
                user_id,
            )
            spectating = bool(
                await conn.fetchval(
                    "SELECT spectating FROM room_players "
                    "WHERE room_id = $1 AND user_id = $2",
                    room["id"],
                    user_id,
                )
            )
            snapshot = await build_room_out(conn, room)

        await websocket.send_json(
            {"type": "room_state", "payload": snapshot.model_dump(mode="json")}
        )
        # Re-hydrate a mid-round reconnect. `room_state` only carries lobby
        # data; without this replay the client sits on the "Setting up the
        # next round…" placeholder until the round naturally ends.
        await _replay_active_round(websocket, key, user_id, spectating)
        await state.hub.broadcast(
            key,
            {"type": "player_joined", "payload": {"user_id": str(user_id)}},
            except_user=user_id,
        )

        while True:
            msg = await websocket.receive_json()
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong", "payload": {}})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws handler crashed for room=%s user=%s", room_id, user_id)
    finally:
        await state.hub.remove(key, user_id, websocket)
        async with db.pool().acquire() as conn:
            await conn.execute(
                "UPDATE room_players SET connected = FALSE "
                "WHERE room_id = $1 AND user_id = $2",
                room["id"],
                user_id,
            )
            current_status = await conn.fetchval(
                "SELECT status FROM rooms WHERE id = $1", room["id"]
            )
        # Only show a kick countdown when the auto-leave will actually
        # fire — i.e., the room is still in lobby phase.
        auto_leave_at: float | None = None
        if current_status == "lobby":
            auto_leave_at = time.time() + LOBBY_DISCONNECT_GRACE_SECONDS
            state.disconnect_deadlines[(key, user_id)] = auto_leave_at
        await state.hub.broadcast(
            key,
            {
                "type": "player_disconnected",
                "payload": {
                    "user_id": str(user_id),
                    "auto_leave_at": auto_leave_at,
                },
            },
        )
        asyncio.create_task(_auto_leave_if_stale(room["id"], user_id))


@router.websocket("/lobby/ws")
async def lobby_ws(websocket: WebSocket) -> None:
    """Live feed of public-room changes for clients on the home page.
    Open to anonymous visitors — the home page is public."""
    await websocket.accept()
    await state.lobby_hub.add(websocket)
    try:
        rooms = await list_public_rooms_payloads()
        await websocket.send_json(
            {"type": "public_rooms_snapshot", "payload": {"rooms": rooms}}
        )
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong", "payload": {}})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("lobby ws handler crashed")
    finally:
        await state.lobby_hub.remove(websocket)
