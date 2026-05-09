"""WebSocket connection registries. Single-threaded asyncio means dict
operations don't need locking; sends are fanned out concurrently so a slow
client doesn't stall the broadcast to everyone else."""

import asyncio
from typing import Any
from uuid import UUID


class WebSocketHub:
    """Tracks live WS connections per room, keyed by (code, user_id)."""

    def __init__(self) -> None:
        self._conns: dict[str, dict[UUID, Any]] = {}

    async def add(self, code: str, user_id: UUID, ws: Any) -> Any | None:
        """Add a connection, returning a previous connection for this user (if any)
        so the caller can close it."""
        room = self._conns.setdefault(code, {})
        previous = room.get(user_id)
        room[user_id] = ws
        return previous

    async def remove(self, code: str, user_id: UUID, ws: Any) -> None:
        room = self._conns.get(code)
        if room is None:
            return
        if room.get(user_id) is ws:
            room.pop(user_id, None)
        if not room:
            self._conns.pop(code, None)

    async def is_connected(self, code: str, user_id: UUID) -> bool:
        return user_id in self._conns.get(code, {})

    async def connected_users(self, code: str) -> set[UUID]:
        return set(self._conns.get(code, {}).keys())

    async def broadcast(
        self,
        code: str,
        message: dict,
        *,
        except_user: UUID | None = None,
    ) -> None:
        targets = [
            ws for uid, ws in self._conns.get(code, {}).items() if uid != except_user
        ]
        if not targets:
            return
        await asyncio.gather(
            *(_safe_send(ws, message) for ws in targets),
            return_exceptions=True,
        )

    async def send_to(self, code: str, user_id: UUID, message: dict) -> bool:
        ws = self._conns.get(code, {}).get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            return False


hub = WebSocketHub()


class LobbyHub:
    """Connections from clients viewing the home-page public-rooms browser.
    Unlike `WebSocketHub`, there's no per-room or per-user keying — every
    connected client gets every public-room update."""

    def __init__(self) -> None:
        self._conns: set[Any] = set()

    async def add(self, ws: Any) -> None:
        self._conns.add(ws)

    async def remove(self, ws: Any) -> None:
        self._conns.discard(ws)

    async def broadcast(self, message: dict) -> None:
        if not self._conns:
            return
        await asyncio.gather(
            *(_safe_send(ws, message) for ws in list(self._conns)),
            return_exceptions=True,
        )


lobby_hub = LobbyHub()


async def _safe_send(ws: Any, message: dict) -> None:
    try:
        await ws.send_json(message)
    except Exception:
        # Connection has gone bad; the WS handler will clean it up
        # on its own disconnect path.
        pass
