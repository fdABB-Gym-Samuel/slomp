"""In-memory game state. The Postgres tables are the source of truth for
durable data (users, room metadata, submitted songs, finished rounds, guesses);
this module owns the live, frequently-mutated per-player progress for the
currently-playing round and the WebSocket connection registry."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass
class PlayerRoundState:
    user_id: UUID
    bracket_index: int = 0
    outcome: str | None = None  # "correct" | "exhausted" | None
    points: int = 0

    @property
    def finished(self) -> bool:
        return self.outcome is not None


@dataclass
class ActiveRound:
    round_id: UUID
    song_id: UUID
    spotify_track_id: str
    title: str
    artist: str
    album: str | None
    preview_url: str
    album_image_url: str | None
    picker_ids: set[UUID]
    started_at: datetime
    brackets: list[float]
    players: dict[UUID, PlayerRoundState]
    hint_field: str = "none"
    # Captured at round start so a reconnecting client can be re-hydrated
    # without re-deriving from settings: the gated cover-art URL the picker
    # would have seen, the round's wall-clock duration, and the running
    # log of attempts that has been mirrored to the picker(s).
    round_max_seconds: float = 0.0
    album_art_enabled: bool = True
    picker_attempts: list[dict] = field(default_factory=list)

    def all_finished(self) -> bool:
        return all(p.finished for p in self.players.values())


@dataclass
class RoomGame:
    """Per-room in-memory gameplay state. Created when phase transitions to
    `playing`; cleared/reset on phase transition back to lobby."""

    code: str
    song_queue: list[UUID] = field(default_factory=list)
    active_round: ActiveRound | None = None
    completed_round_ids: list[UUID] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timeout_task: asyncio.Task | None = None
    # The most recent `round_ended` broadcast payload + the wall-clock deadline
    # at which the intermission lapses, so a mid-intermission reconnect can be
    # re-shown the same scoreboard reveal instead of landing on the empty
    # "setting up the next round…" placeholder. Cleared at the start of the
    # next round (or on game end / restart).
    last_round_payload: dict | None = None
    intermission_ends_at: float | None = None


class GameRegistry:
    def __init__(self) -> None:
        self._rooms: dict[str, RoomGame] = {}
        self._lock = asyncio.Lock()

    async def get(self, code: str) -> RoomGame | None:
        return self._rooms.get(code)

    async def get_or_create(self, code: str) -> RoomGame:
        async with self._lock:
            game = self._rooms.get(code)
            if game is None:
                game = RoomGame(code=code)
                self._rooms[code] = game
            return game

    async def remove(self, code: str) -> None:
        async with self._lock:
            self._rooms.pop(code, None)


registry = GameRegistry()


# ---------- WebSocket hub --------------------------------------------------


class WebSocketHub:
    """Tracks live WS connections per room, keyed by (code, user_id)."""

    def __init__(self) -> None:
        self._conns: dict[str, dict[UUID, Any]] = {}
        self._lock = asyncio.Lock()

    async def add(self, code: str, user_id: UUID, ws: Any) -> Any | None:
        """Add a connection, returning a previous connection for this user (if any)
        so the caller can close it."""
        async with self._lock:
            room = self._conns.setdefault(code, {})
            previous = room.get(user_id)
            room[user_id] = ws
            return previous

    async def remove(self, code: str, user_id: UUID, ws: Any) -> None:
        async with self._lock:
            room = self._conns.get(code)
            if room is None:
                return
            if room.get(user_id) is ws:
                room.pop(user_id, None)
            if not room:
                self._conns.pop(code, None)

    async def is_connected(self, code: str, user_id: UUID) -> bool:
        async with self._lock:
            return user_id in self._conns.get(code, {})

    async def connected_users(self, code: str) -> set[UUID]:
        async with self._lock:
            return set(self._conns.get(code, {}).keys())

    async def broadcast(
        self,
        code: str,
        message: dict,
        *,
        except_user: UUID | None = None,
    ) -> None:
        async with self._lock:
            targets = list(self._conns.get(code, {}).items())
        for user_id, ws in targets:
            if user_id == except_user:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                # Connection has gone bad; the WS handler will clean it up
                # on its own disconnect path.
                pass

    async def send_to(self, code: str, user_id: UUID, message: dict) -> bool:
        async with self._lock:
            ws = self._conns.get(code, {}).get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            return False


hub = WebSocketHub()


# Live deadlines for the two grace-period mechanisms so we can surface
# countdowns to clients. Single-threaded asyncio access — no lock needed.
# `disconnect_deadlines` keys on (room_key, user_id) → unix seconds at which
# the player will be auto-leaved from the room. `empty_room_deadlines` keys
# on room_key → unix seconds at which an empty room will be deleted.
disconnect_deadlines: dict[tuple[str, UUID], float] = {}
empty_room_deadlines: dict[str, float] = {}


class LobbyHub:
    """Connections from clients viewing the home-page public-rooms browser.
    Unlike `WebSocketHub`, there's no per-room or per-user keying — every
    connected client gets every public-room update."""

    def __init__(self) -> None:
        self._conns: set[Any] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: Any) -> None:
        async with self._lock:
            self._conns.add(ws)

    async def remove(self, ws: Any) -> None:
        async with self._lock:
            self._conns.discard(ws)

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            targets = list(self._conns)
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                pass


lobby_hub = LobbyHub()
