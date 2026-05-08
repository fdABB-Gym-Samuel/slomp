import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from .. import auth, db, state
from ..rooms_helpers import build_room_out, room_key

logger = logging.getLogger("slomp.ws")
router = APIRouter()


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

    try:
        async with db.pool().acquire() as conn:
            await conn.execute(
                "UPDATE room_players SET connected = TRUE "
                "WHERE room_id = $1 AND user_id = $2",
                room["id"],
                user_id,
            )
            snapshot = await build_room_out(conn, room)

        await websocket.send_json(
            {"type": "room_state", "payload": snapshot.model_dump(mode="json")}
        )
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
        await state.hub.broadcast(
            key,
            {"type": "player_disconnected", "payload": {"user_id": str(user_id)}},
        )
