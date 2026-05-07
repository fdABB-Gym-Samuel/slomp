import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_db_url() -> str:
    user = os.environ.get("PGUSER") or os.environ.get("USER", "postgres")
    port = os.environ.get("PGPORT", "5432")
    db = os.environ.get("PGDATABASE", "slomp")
    return f"postgres://{user}@localhost:{port}/{db}?sslmode=disable"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default_factory=_default_db_url, alias="DATABASE_URL")
    valkey_url: str = Field(default="redis://127.0.0.1:6379/0", alias="VALKEY_URL")

    # Spotify creds were used for the original Spotify Client-Credentials path
    # but the backend now talks to Deezer's free public API (no auth). Keys are
    # optional just so leftover entries in .env don't trip pydantic.
    spotify_client_id: str | None = Field(default=None, alias="SPOTIFY_CLIENT_ID")
    spotify_client_secret: str | None = Field(
        default=None, alias="SPOTIFY_CLIENT_SECRET"
    )

    session_ttl_seconds: int = 60 * 60 * 24 * 7
    session_cookie_name: str = "session"
    session_cookie_secure: bool = False

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )


settings = Settings()  # type: ignore[call-arg]
