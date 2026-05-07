"""Shared helpers for the rooms router and the game module."""

import json
import secrets
from uuid import UUID

import asyncpg
from fastapi import HTTPException, status

from . import db, state
from .models import RoomOut, RoomPlayerOut, RoomSettings, UserOut, serialize_settings

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no O/0/I/1/L


def generate_room_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


async def load_room_or_404(conn: asyncpg.Connection, code: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, code, leader_id, status, settings FROM rooms WHERE code = $1",
        code,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "room_not_found", "message": "room not found"},
        )
    return row


async def assert_leader(room: asyncpg.Record, user_id: UUID) -> None:
    if room["leader_id"] != user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "not_leader", "message": "only the leader can do this"},
        )


async def assert_status(room: asyncpg.Record, *allowed: str) -> None:
    if room["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "wrong_phase",
                "message": f"action requires phase {allowed}, currently {room['status']}",
            },
        )


def _decode_settings(raw) -> RoomSettings:
    if isinstance(raw, str):
        return serialize_settings(json.loads(raw))
    return serialize_settings(raw or {})


async def build_room_out(conn: asyncpg.Connection, room: asyncpg.Record) -> RoomOut:
    rows = await conn.fetch(
        """
        SELECT
            rp.user_id,
            u.username,
            rp.score,
            (
                SELECT count(*)::int FROM room_song_pickers rsp
                 WHERE rsp.room_id = rp.room_id AND rsp.user_id = rp.user_id
            ) AS submitted
        FROM room_players rp
        JOIN users u ON u.id = rp.user_id
        WHERE rp.room_id = $1
        ORDER BY rp.joined_at ASC
        """,
        room["id"],
    )
    connected = await state.hub.connected_users(room["code"])

    players = [
        RoomPlayerOut(
            user=UserOut(id=r["user_id"], username=r["username"]),
            score=r["score"],
            connected=r["user_id"] in connected,
            songs_submitted=r["submitted"],
        )
        for r in rows
    ]

    game = await state.registry.get(room["code"])
    current_round_id = (
        game.active_round.round_id
        if game is not None and game.active_round is not None
        else None
    )

    return RoomOut(
        code=room["code"],
        leader_id=room["leader_id"],
        status=room["status"],
        settings=_decode_settings(room["settings"]),
        players=players,
        current_round_id=current_round_id,
    )


async def fetch_room_out(code: str) -> RoomOut:
    async with db.pool().acquire() as conn:
        room = await load_room_or_404(conn, code)
        return await build_room_out(conn, room)
