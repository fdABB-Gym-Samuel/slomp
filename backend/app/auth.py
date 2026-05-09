import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from fastapi import Cookie, HTTPException, Response, status

from . import db
from .config import settings


async def create_session_in_conn(
    conn: asyncpg.Connection, user_id: UUID
) -> tuple[str, datetime]:
    """Insert a session row on an open connection so the caller can keep it
    inside a wider transaction (e.g. atomic user-create + session-create)."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.session_ttl_seconds
    )
    await conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES ($1, $2, $3)",
        token,
        user_id,
        expires_at,
    )
    return token, expires_at


async def lookup_user_id(token: str) -> UUID | None:
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id FROM sessions WHERE token = $1 AND expires_at > NOW()",
            token,
        )
    return row["user_id"] if row else None


def set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        expires=expires_at,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )


async def get_current_user_id(
    session: str | None = Cookie(default=None, alias="session"),
) -> UUID:
    if not session:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "not_authenticated", "message": "not logged in"},
        )
    user_id = await lookup_user_id(session)
    if user_id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "session_expired", "message": "invalid or expired session"},
        )
    return user_id


async def lookup_user_id_for_ws(token: str | None) -> UUID | None:
    if not token:
        return None
    return await lookup_user_id(token)
