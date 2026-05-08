from uuid import UUID

import asyncpg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from .. import auth, db
from ..models import (
    ChangePasswordRequest,
    ChangeUsernameRequest,
    LoginRequest,
    RegisterRequest,
    UserOut,
)
from ..password import hash_password, needs_rehash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201, response_model=UserOut)
async def register(req: RegisterRequest, response: Response) -> UserOut:
    pw_hash = hash_password(req.password)
    try:
        async with db.pool().acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO users (username, password_hash) VALUES ($1, $2) "
                "RETURNING id, username",
                req.username,
                pw_hash,
            )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "username_taken", "message": "username already taken"},
        )

    token, expires_at = await auth.create_session(row["id"])
    auth.set_session_cookie(response, token, expires_at)
    return UserOut(id=row["id"], username=row["username"])


@router.post("/login", response_model=UserOut)
async def login(req: LoginRequest, response: Response) -> UserOut:
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash FROM users WHERE username = $1",
            req.username,
        )
    if row is None or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_credentials", "message": "invalid credentials"},
        )

    if needs_rehash(row["password_hash"]):
        new_hash = hash_password(req.password)
        async with db.pool().acquire() as conn:
            await conn.execute(
                "UPDATE users SET password_hash = $1 WHERE id = $2",
                new_hash,
                row["id"],
            )

    token, expires_at = await auth.create_session(row["id"])
    auth.set_session_cookie(response, token, expires_at)
    return UserOut(id=row["id"], username=row["username"])


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    session: str | None = Cookie(default=None, alias="session"),
) -> Response:
    if session:
        await auth.delete_session(session)
    auth.clear_session_cookie(response)
    return Response(status_code=204)


router_me = APIRouter(tags=["auth"])


@router_me.get("/me", response_model=UserOut)
async def me(user_id: UUID = Depends(auth.get_current_user_id)) -> UserOut:
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username FROM users WHERE id = $1", user_id
        )
    if row is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "user_not_found", "message": "user not found"},
        )
    return UserOut(id=row["id"], username=row["username"])


@router_me.patch("/me/username", response_model=UserOut)
async def change_username(
    req: ChangeUsernameRequest,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> UserOut:
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE id = $1", user_id
        )
        if row is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail={"code": "user_not_found", "message": "user not found"},
            )
        if not verify_password(req.current_password, row["password_hash"]):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": "invalid_password", "message": "incorrect password"},
            )
        try:
            updated = await conn.fetchrow(
                "UPDATE users SET username = $1 WHERE id = $2 RETURNING id, username",
                req.username,
                user_id,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"code": "username_taken", "message": "username already taken"},
            )
    return UserOut(id=updated["id"], username=updated["username"])


@router_me.patch("/me/password", status_code=204)
async def change_password(
    req: ChangePasswordRequest,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> Response:
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE id = $1", user_id
        )
        if row is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail={"code": "user_not_found", "message": "user not found"},
            )
        if not verify_password(req.current_password, row["password_hash"]):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": "invalid_password", "message": "incorrect password"},
            )
        new_hash = hash_password(req.new_password)
        await conn.execute(
            "UPDATE users SET password_hash = $1 WHERE id = $2",
            new_hash,
            user_id,
        )
    return Response(status_code=204)
