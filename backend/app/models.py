from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------- auth ----------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: UUID
    username: str


# ---------- rooms ---------------------------------------------------------


RoomStatus = Literal["lobby", "selecting", "playing", "results"]

HintField = Literal["none", "artist", "album"]


class RoomSettings(BaseModel):
    min_popularity: int = Field(default=0, ge=0, le=100)
    required_artists: list[str] = Field(default_factory=list)
    songs_per_player: int = Field(default=3, ge=1, le=10)
    guess_brackets_seconds: list[float] = Field(
        default_factory=lambda: [0.5, 1, 2.5, 5, 15, 30]
    )
    album_art_enabled: bool = True
    album_art_unblur: bool = True
    hint_field: HintField = "none"
    round_intermission_seconds: int = Field(default=6, ge=0, le=30)
    round_max_seconds: int = Field(default=60, ge=10, le=600)
    post_game_delay_seconds: int = Field(default=15, ge=0)
    lock_after_lobby: bool = False


class RoomPlayerOut(BaseModel):
    user: UserOut
    score: int
    connected: bool
    songs_submitted: int
    spectating: bool = False
    auto_leave_at: float | None = None


class RoomOut(BaseModel):
    id: UUID
    code: str | None = None
    name: str | None = None
    is_public: bool = False
    leader_id: UUID
    status: RoomStatus
    settings: RoomSettings
    players: list[RoomPlayerOut]
    current_round_id: UUID | None = None


class PhaseRequest(BaseModel):
    target: RoomStatus


class RoomInfoUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    is_public: bool | None = None


class JoinByCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=8)


class PublicRoomOut(BaseModel):
    id: UUID
    name: str | None
    leader_username: str
    player_count: int
    songs_per_player: int
    cleanup_at: float | None = None
    status: RoomStatus = "lobby"
    joins_as_spectator: bool = False


class MyRoomOut(BaseModel):
    id: UUID
    name: str | None
    status: RoomStatus


# ---------- songs ---------------------------------------------------------


class SongCandidate(BaseModel):
    spotify_track_id: str
    title: str
    artist: str
    album: str | None = None
    preview_url: str | None = None
    album_image_url: str | None = None
    duration_ms: int | None = None
    popularity: int | None = None


class SubmitSongRequest(BaseModel):
    spotify_track_id: str


class SubmittedSongOut(BaseModel):
    id: UUID
    spotify_track_id: str
    title: str
    artist: str
    preview_url: str | None
    album_image_url: str | None


# ---------- gameplay -----------------------------------------------------


class GuessRequest(BaseModel):
    round_id: UUID
    guessed_track_id: str = Field(min_length=1, max_length=64)


class SkipRequest(BaseModel):
    round_id: UUID


class GuessResultOut(BaseModel):
    correct: bool
    points: int
    bracket_index: int
    finished: bool
    hint_fulfilled: bool = False


class SkipResultOut(BaseModel):
    bracket_index: int
    finished: bool


class ScoreboardEntry(BaseModel):
    user: UserOut
    score: int


# ---------- WS event payloads (for typing) ------------------------------


class WSEvent(BaseModel):
    type: str
    payload: dict


# ---------- DB row helpers ---------------------------------------------


def serialize_settings(raw: dict | None) -> RoomSettings:
    if raw is None:
        return RoomSettings()
    return RoomSettings.model_validate(raw)
