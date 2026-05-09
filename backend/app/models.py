from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------- identity ------------------------------------------------------


class UserOut(BaseModel):
    id: UUID
    username: str


class CreateRoomRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)


class JoinByCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=8)
    username: str = Field(min_length=3, max_length=32)


class JoinRoomRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)


class RenameRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)


# ---------- rooms ---------------------------------------------------------


RoomStatus = Literal["lobby", "selecting", "playing", "results"]

HintField = Literal["none", "artist", "album"]

GameMode = Literal["classic", "random"]

ObscureMode = Literal["blur", "pixelate"]


class RoomSettings(BaseModel):
    game_mode: GameMode = "classic"
    # Number of rounds in random mode. Ignored when game_mode == "classic"
    # (the pool is determined by submissions there).
    random_song_count: int = Field(default=10, ge=1, le=50)
    min_popularity: int = Field(default=0, ge=0, le=100)
    required_artists: list[str] = Field(default_factory=list)
    songs_per_player: int = Field(default=3, ge=1, le=10)
    guess_brackets_seconds: list[float] = Field(
        default_factory=lambda: [0.5, 1, 2.5, 5, 15, 30]
    )
    album_art_enabled: bool = True
    album_art_unblur: bool = True
    # How the cover is progressively revealed: a Gaussian blur whose radius
    # shrinks bracket-by-bracket, or a pixel-block mosaic whose tiles get
    # smaller until the image is sharp.
    album_art_obscure_mode: ObscureMode = "blur"
    # One intensity value per bracket — interpretation depends on mode:
    # blur → Gaussian radius in px (0..256, 0 = sharp); pixelate → block
    # size in px on the source 256-px cover (1..256, 1 = sharp). Length
    # must match guess_brackets_seconds; empty means "use the default
    # linear ramp" (24→0 for blur, 16→1 for pixelate).
    album_art_obscure_per_bracket_px: list[int] = Field(default_factory=list)
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
    leader_id: UUID | None = None
    status: RoomStatus
    settings: RoomSettings
    players: list[RoomPlayerOut]
    current_round_id: UUID | None = None


class PhaseRequest(BaseModel):
    target: RoomStatus


class RoomInfoUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    is_public: bool | None = None


class PublicRoomOut(BaseModel):
    id: UUID
    name: str | None
    leader_username: str
    player_count: int
    songs_per_player: int
    random_song_count: int
    cleanup_at: float | None = None
    status: RoomStatus = "lobby"
    joins_as_spectator: bool = False
    game_mode: GameMode = "classic"


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
