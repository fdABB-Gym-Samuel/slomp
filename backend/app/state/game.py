"""In-memory per-room gameplay state. The Postgres tables are the source
of truth for durable data; this module owns the live, frequently-mutated
per-player progress for the currently-playing round."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
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
    track_id: str
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

    async def get(self, code: str) -> RoomGame | None:
        return self._rooms.get(code)

    async def get_or_create(self, code: str) -> RoomGame:
        game = self._rooms.get(code)
        if game is None:
            game = RoomGame(code=code)
            self._rooms[code] = game
        return game

    async def remove(self, code: str) -> None:
        self._rooms.pop(code, None)


registry = GameRegistry()
