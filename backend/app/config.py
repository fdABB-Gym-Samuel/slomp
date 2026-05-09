import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_db_url() -> str:
    user = os.environ.get("PGUSER") or os.environ.get("USER", "postgres")
    port = os.environ.get("PGPORT", "5432")
    db = os.environ.get("PGDATABASE", "slomp")
    return f"postgres://{user}@localhost:{port}/{db}?sslmode=disable"


def _default_cookie_secure() -> bool:
    """Default the Secure flag based on `ENV` so a fresh prod deploy can't
    forget it: anything that isn't an explicit dev/test env requires Secure.
    Override via `SESSION_COOKIE_SECURE=0/1` when needed."""
    env = os.environ.get("ENV", "").lower()
    return env not in ("dev", "development", "test", "testing", "")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default_factory=_default_db_url, alias="DATABASE_URL")
    valkey_url: str = Field(default="redis://127.0.0.1:6379/0", alias="VALKEY_URL")

    session_ttl_seconds: int = 60 * 60 * 24 * 7
    session_cookie_name: str = "session"
    session_cookie_secure: bool = Field(
        default_factory=_default_cookie_secure, alias="SESSION_COOKIE_SECURE"
    )

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )


settings = Settings()  # type: ignore[call-arg]
