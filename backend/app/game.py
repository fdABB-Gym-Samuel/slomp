"""Game loop: starting a game, running rounds, applying guesses/skips, ending."""

import asyncio
import json
import random
from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException, status

from . import db, matching, spotify, state
from .models import RoomSettings, serialize_settings


def _score_for_bracket(bracket_index: int, brackets: list[float]) -> int:
    return max(0, len(brackets) - bracket_index)


def _decode_settings(raw) -> RoomSettings:
    if isinstance(raw, str):
        return serialize_settings(json.loads(raw))
    return serialize_settings(raw or {})


async def start_game(code: str) -> None:
    """Build the song queue, reset scores, and start the first round.
    Must be called only when transitioning into the `playing` phase.

    Queue ordering: each picker's submitted songs are kept in submission
    order, but rounds round-robin between pickers so no player has multiple
    of their songs played back-to-back. The picker order is shuffled once
    at game start for fairness. When multiple players picked the same song,
    it appears once in the queue (taken on the lap of whichever picker hits
    it first under the shuffled order)."""
    async with db.pool().acquire() as conn:
        room = await conn.fetchrow(
            "SELECT id, settings FROM rooms WHERE code = $1", code
        )
        if room is None:
            raise HTTPException(
                404, detail={"code": "room_not_found", "message": "room not found"}
            )

        picker_rows = await conn.fetch(
            "SELECT song_id, user_id FROM room_song_pickers WHERE room_id = $1 "
            "ORDER BY submitted_at ASC",
            room["id"],
        )
        await conn.execute(
            "UPDATE room_players SET score = 0 WHERE room_id = $1",
            room["id"],
        )
        await conn.execute(
            "UPDATE rooms SET started_at = NOW(), ended_at = NULL WHERE id = $1",
            room["id"],
        )

    songs_by_picker: dict[UUID, list[UUID]] = defaultdict(list)
    for r in picker_rows:
        songs_by_picker[r["user_id"]].append(r["song_id"])

    picker_order = list(songs_by_picker.keys())
    random.shuffle(picker_order)

    queue: list[UUID] = []
    seen: set[UUID] = set()
    if songs_by_picker:
        max_count = max(len(s) for s in songs_by_picker.values())
        for lap in range(max_count):
            for pid in picker_order:
                if lap < len(songs_by_picker[pid]):
                    sid = songs_by_picker[pid][lap]
                    if sid not in seen:
                        queue.append(sid)
                        seen.add(sid)

    game = await state.registry.get_or_create(code)
    async with game.lock:
        game.song_queue = queue
        game.completed_round_ids = []
        game.active_round = None

    await _start_next_round(code)


async def _start_next_round(code: str) -> None:
    """Pop the next song from the queue and start a round. If empty → end game."""
    game = await state.registry.get_or_create(code)
    async with game.lock:
        if not game.song_queue:
            await _end_game(code)
            return
        next_song_id = game.song_queue.pop(0)

    async with db.pool().acquire() as conn:
        song = await conn.fetchrow(
            """
            SELECT rs.id, rs.spotify_track_id, rs.title, rs.artist, rs.preview_url,
                   rs.album_image_url, rs.room_id, r.settings
            FROM room_songs rs
            JOIN rooms r ON r.id = rs.room_id
            WHERE rs.id = $1
            """,
            next_song_id,
        )
        if song is None:
            return
        room_id = song["room_id"]
        picker_rows = await conn.fetch(
            "SELECT user_id FROM room_song_pickers WHERE song_id = $1",
            next_song_id,
        )
        round_row = await conn.fetchrow(
            "INSERT INTO rounds (room_id, song_id, started_at) "
            "VALUES ($1, $2, NOW()) RETURNING id, started_at",
            room_id,
            next_song_id,
        )
        player_rows = await conn.fetch(
            "SELECT user_id FROM room_players WHERE room_id = $1",
            room_id,
        )

    settings = _decode_settings(song["settings"])
    brackets = settings.guess_brackets_seconds
    picker_ids: set[UUID] = {r["user_id"] for r in picker_rows}

    players: dict[UUID, state.PlayerRoundState] = {}
    for r in player_rows:
        if r["user_id"] not in picker_ids:
            players[r["user_id"]] = state.PlayerRoundState(user_id=r["user_id"])

    active = state.ActiveRound(
        round_id=round_row["id"],
        song_id=song["id"],
        spotify_track_id=song["spotify_track_id"],
        title=song["title"],
        artist=song["artist"],
        preview_url=song["preview_url"] or "",
        album_image_url=song["album_image_url"],
        picker_ids=picker_ids,
        started_at=round_row["started_at"],
        brackets=brackets,
        players=players,
    )

    async with game.lock:
        game.active_round = active

    audio_url = f"/rooms/{code}/rounds/{active.round_id}/audio"
    await state.hub.broadcast(
        code,
        {
            "type": "round_started",
            "payload": {
                "round_id": str(active.round_id),
                "picker_ids": [str(pid) for pid in active.picker_ids],
                "started_at_server": active.started_at.isoformat(),
                "audio_url": audio_url,
                "album_image_url": active.album_image_url
                if settings.album_art_enabled
                else None,
                "guess_brackets_seconds": brackets,
            },
        },
    )


async def submit_guess(
    code: str, user_id: UUID, round_id: UUID, guessed_track_id: str
) -> dict:
    """Apply a guess. The user picks a track from a search; we resolve its
    title/artist via Deezer and compare to the round's stored values.
    Returns dict with correct/points/bracket_index/finished."""
    game = await state.registry.get(code)
    if (
        game is None
        or game.active_round is None
        or game.active_round.round_id != round_id
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "round_not_active",
                "message": "round is not currently active",
            },
        )

    # Look up the guessed track outside the room lock so we don't block other
    # players' guesses on the network round-trip.
    guess_title = "<unknown>"
    guess_artist = ""
    try:
        guess_track = await spotify.get_track(guessed_track_id)
        guess_title = guess_track.get("title", "<unknown>")
        artist_obj = guess_track.get("artist") or {}
        guess_artist = artist_obj.get("name", "")
    except Exception:
        # Treat unresolvable track as a wrong guess. Player still gets a
        # bracket-advance penalty.
        pass

    guess_label = f"{guess_title} — {guess_artist}" if guess_artist else guess_title

    async with game.lock:
        active = game.active_round
        if active is None or active.round_id != round_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "round_not_active",
                    "message": "round is not currently active",
                },
            )

        if user_id in active.picker_ids:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "is_picker",
                    "message": "the picker can't guess on their own song",
                },
            )

        player = active.players.get(user_id)
        if player is None or player.finished:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "already_finished",
                    "message": "you've already finished this round",
                },
            )

        bracket_index = player.bracket_index
        correct = matching.is_correct_guess(
            active.title, active.artist, guess_title, guess_artist
        )

        if correct:
            points = _score_for_bracket(bracket_index, active.brackets)
            player.outcome = "correct"
            player.points = points
            await _persist_guess(
                round_id,
                user_id,
                bracket_index,
                active.brackets,
                guess_label,
                True,
                points,
            )
            await _broadcast_player_finished(code, active, user_id, "correct", points)
        else:
            await _persist_guess(
                round_id, user_id, bracket_index, active.brackets, guess_label, False, 0
            )
            new_index = bracket_index + 1
            if new_index >= len(active.brackets):
                player.outcome = "exhausted"
                player.points = 0
                await _broadcast_player_finished(code, active, user_id, "exhausted", 0)
            else:
                player.bracket_index = new_index
                await state.hub.broadcast(
                    code,
                    {
                        "type": "bracket_unlocked",
                        "payload": {
                            "round_id": str(round_id),
                            "user_id": str(user_id),
                            "bracket_index": new_index,
                        },
                    },
                )

        await _send_picker_attempt(
            code, active, user_id, "guess", guess_label, correct, bracket_index
        )

        result = {
            "correct": correct,
            "points": player.points if correct else 0,
            "bracket_index": player.bracket_index,
            "finished": player.finished,
        }
        all_done = active.all_finished()

    if all_done:
        await _end_round(code)
    return result


async def submit_skip(code: str, user_id: UUID, round_id: UUID) -> dict:
    game = await state.registry.get(code)
    if (
        game is None
        or game.active_round is None
        or game.active_round.round_id != round_id
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "round_not_active",
                "message": "round is not currently active",
            },
        )

    async with game.lock:
        active = game.active_round
        if active is None or active.round_id != round_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "round_not_active",
                    "message": "round is not currently active",
                },
            )

        if user_id in active.picker_ids:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"code": "is_picker", "message": "the picker can't skip"},
            )

        player = active.players.get(user_id)
        if player is None or player.finished:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "already_finished",
                    "message": "you've already finished this round",
                },
            )

        prev_index = player.bracket_index
        new_index = prev_index + 1
        if new_index >= len(active.brackets):
            player.outcome = "exhausted"
            player.points = 0
            await _broadcast_player_finished(code, active, user_id, "exhausted", 0)
        else:
            player.bracket_index = new_index
            await state.hub.broadcast(
                code,
                {
                    "type": "bracket_unlocked",
                    "payload": {
                        "round_id": str(round_id),
                        "user_id": str(user_id),
                        "bracket_index": new_index,
                    },
                },
            )

        await _send_picker_attempt(
            code, active, user_id, "skip", None, None, prev_index
        )

        result = {"bracket_index": player.bracket_index, "finished": player.finished}
        all_done = active.all_finished()

    if all_done:
        await _end_round(code)
    return result


async def _persist_guess(
    round_id: UUID,
    user_id: UUID,
    bracket_index: int,
    brackets: list[float],
    guess_text: str,
    correct: bool,
    points: int,
) -> None:
    bracket_seconds = brackets[bracket_index] if bracket_index < len(brackets) else None
    async with db.pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guesses
                (round_id, user_id, guessed_at_ms, bracket_seconds, guess_text, correct, points)
            VALUES ($1, $2, 0, $3, $4, $5, $6)
            """,
            round_id,
            user_id,
            bracket_seconds,
            guess_text,
            correct,
            points,
        )
        if correct and points > 0:
            await conn.execute(
                """
                UPDATE room_players SET score = score + $1
                WHERE user_id = $2
                  AND room_id = (SELECT room_id FROM rounds WHERE id = $3)
                """,
                points,
                user_id,
                round_id,
            )


async def _broadcast_player_finished(
    code: str,
    active: state.ActiveRound,
    user_id: UUID,
    outcome: str,
    points: int,
) -> None:
    await state.hub.broadcast(
        code,
        {
            "type": "player_finished",
            "payload": {
                "round_id": str(active.round_id),
                "user_id": str(user_id),
                "outcome": outcome,
                "points": points,
            },
        },
    )


async def _send_picker_attempt(
    code: str,
    active: state.ActiveRound,
    user_id: UUID,
    kind: str,
    guess_text: str | None,
    correct: bool | None,
    bracket_index: int,
) -> None:
    payload = {
        "round_id": str(active.round_id),
        "attempts": [
            {
                "user_id": str(user_id),
                "kind": kind,
                "guess_text": guess_text,
                "correct": correct,
                "bracket_index": bracket_index,
            }
        ],
    }
    message = {"type": "picker_view", "payload": payload}
    for picker_id in active.picker_ids:
        await state.hub.send_to(code, picker_id, message)


async def _end_round(code: str) -> None:
    game = await state.registry.get(code)
    if game is None or game.active_round is None:
        return

    active = game.active_round
    # Per-guess score updates were applied live during submit_guess, so the
    # DB already has the post-round scores. Reconstruct the pre-round scores
    # from each player's in-memory round delta so the client can animate the
    # rank movement from old → new.
    delta_by_id: dict[str, int] = {
        str(uid): (p.points if p.outcome == "correct" else 0)
        for uid, p in active.players.items()
    }

    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE rounds SET ended_at = NOW() WHERE id = $1", active.round_id
        )
        scoreboard_rows = await conn.fetch(
            """
            SELECT u.id, u.username, rp.score
            FROM room_players rp
            JOIN users u ON u.id = rp.user_id
            JOIN rooms r ON r.id = rp.room_id
            WHERE r.code = $1
            ORDER BY rp.score DESC, u.username ASC
            """,
            code,
        )

    scoreboard = [
        {
            "user": {"id": str(r["id"]), "username": r["username"]},
            "score": r["score"],
            "previous_score": r["score"] - delta_by_id.get(str(r["id"]), 0),
        }
        for r in scoreboard_rows
    ]

    settings = await _load_settings(code)
    intermission = settings.round_intermission_seconds if settings else 6
    is_last_round = not game.song_queue

    full_audio_url = (
        f"/rooms/{code}/rounds/{active.round_id}/full-audio"
        if active.preview_url
        else None
    )
    await state.hub.broadcast(
        code,
        {
            "type": "round_ended",
            "payload": {
                "round_id": str(active.round_id),
                "title": active.title,
                "artist": active.artist,
                "album_image_url": active.album_image_url,
                "full_audio_url": full_audio_url,
                "scoreboard": scoreboard,
                "intermission_seconds": 0 if is_last_round else intermission,
                "is_last_round": is_last_round,
            },
        },
    )

    async with game.lock:
        game.completed_round_ids.append(active.round_id)
        game.active_round = None

    if is_last_round:
        await _end_game(code)
    else:
        if intermission > 0:
            await asyncio.sleep(intermission)
        await _start_next_round(code)


async def _load_settings(code: str):
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow("SELECT settings FROM rooms WHERE code = $1", code)
    if row is None:
        return None
    return _decode_settings(row["settings"])


async def _end_game(code: str) -> None:
    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE rooms SET status = 'results', ended_at = NOW() WHERE code = $1",
            code,
        )
        scoreboard_rows = await conn.fetch(
            """
            SELECT u.id, u.username, rp.score
            FROM room_players rp
            JOIN users u ON u.id = rp.user_id
            JOIN rooms r ON r.id = rp.room_id
            WHERE r.code = $1
            ORDER BY rp.score DESC, u.username ASC
            """,
            code,
        )

    scoreboard = [
        {"user": {"id": str(r["id"]), "username": r["username"]}, "score": r["score"]}
        for r in scoreboard_rows
    ]
    await state.hub.broadcast(
        code,
        {
            "type": "game_ended",
            "payload": {
                "scoreboard": scoreboard,
                "restart_unlocks_at": None,
            },
        },
    )


async def restart_game(code: str) -> None:
    """Reset scores, songs, and rounds for a room and return to the lobby phase."""
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await conn.fetchrow("SELECT id FROM rooms WHERE code = $1", code)
            if room is None:
                return
            await conn.execute(
                "DELETE FROM guesses WHERE round_id IN ("
                "SELECT id FROM rounds WHERE room_id = $1)",
                room["id"],
            )
            await conn.execute("DELETE FROM rounds WHERE room_id = $1", room["id"])
            await conn.execute("DELETE FROM room_songs WHERE room_id = $1", room["id"])
            await conn.execute(
                "UPDATE room_players SET score = 0 WHERE room_id = $1", room["id"]
            )
            await conn.execute(
                "UPDATE rooms SET status = 'lobby', started_at = NULL, ended_at = NULL "
                "WHERE id = $1",
                room["id"],
            )
    await state.registry.remove(code)
