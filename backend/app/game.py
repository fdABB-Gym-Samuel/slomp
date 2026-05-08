"""Game loop: starting a game, running rounds, applying guesses/skips, ending."""

import asyncio
import json
import random
from collections import defaultdict
from uuid import UUID

# When everybody who could play this song is also a picker (everyone picked
# the same track, or a tiny game with one non-picker who got assigned), the
# round has nobody to guess. Hold the round_started UI for a beat so it
# doesn't feel like the song was skipped, then advance to intermission.
NO_GUESSERS_DURATION = 1.0

from fastapi import HTTPException, status

from . import db, deezer, matching, state
from .models import RoomSettings, serialize_settings


def _score_for_bracket(bracket_index: int, brackets: list[float]) -> int:
    return max(0, len(brackets) - bracket_index)


def _decode_settings(raw) -> RoomSettings:
    if isinstance(raw, str):
        return serialize_settings(json.loads(raw))
    return serialize_settings(raw or {})


async def start_game(room_key: str) -> None:
    """Build the song queue, reset scores, and start the first round.
    Must be called only when transitioning into the `playing` phase.

    Classic queue: each picker's submitted songs are kept in submission
    order, but rounds round-robin between pickers so no player has multiple
    of their songs played back-to-back. The picker order is shuffled once
    at game start for fairness. When multiple players picked the same song,
    it appears once in the queue (taken on the lap of whichever picker hits
    it first under the shuffled order).

    Random queue: every song attached to the room (which `change_phase`
    pre-populated from Deezer) is played, in shuffled order. There are no
    pickers, so every non-spectating player guesses every song."""
    async with db.pool().acquire() as conn:
        room = await conn.fetchrow(
            "SELECT id, settings FROM rooms WHERE id = $1", UUID(room_key)
        )
        if room is None:
            raise HTTPException(
                404, detail={"code": "room_not_found", "message": "room not found"}
            )

        await conn.execute(
            "UPDATE room_players SET score = 0 WHERE room_id = $1",
            room["id"],
        )
        await conn.execute(
            "UPDATE rooms SET started_at = NOW(), ended_at = NULL WHERE id = $1",
            room["id"],
        )

        settings = _decode_settings(room["settings"])
        if settings.game_mode == "random":
            song_rows = await conn.fetch(
                "SELECT id FROM room_songs WHERE room_id = $1 ORDER BY play_order ASC",
                room["id"],
            )
            queue: list[UUID] = [r["id"] for r in song_rows]
            random.shuffle(queue)
        else:
            picker_rows = await conn.fetch(
                "SELECT song_id, user_id FROM room_song_pickers WHERE room_id = $1 "
                "ORDER BY submitted_at ASC",
                room["id"],
            )
            songs_by_picker: dict[UUID, list[UUID]] = defaultdict(list)
            for r in picker_rows:
                songs_by_picker[r["user_id"]].append(r["song_id"])

            picker_order = list(songs_by_picker.keys())
            random.shuffle(picker_order)

            queue = []
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

    game = await state.registry.get_or_create(room_key)
    async with game.lock:
        game.song_queue = queue
        game.completed_round_ids = []
        game.active_round = None

    await _start_next_round(room_key)


async def _start_next_round(room_key: str) -> None:
    """Pop the next song from the queue and start a round. If empty → end game."""
    game = await state.registry.get_or_create(room_key)
    async with game.lock:
        # The intermission window has lapsed by definition once we're starting
        # the next round, so clear the cached payload to avoid stale-replay on
        # a mid-round reconnect.
        game.last_round_payload = None
        game.intermission_ends_at = None
        if not game.song_queue:
            await _end_game(room_key)
            return
        next_song_id = game.song_queue.pop(0)

    async with db.pool().acquire() as conn:
        song = await conn.fetchrow(
            """
            SELECT rs.id, rs.spotify_track_id, rs.title, rs.artist, rs.album,
                   rs.preview_url, rs.album_image_url, rs.room_id, r.settings
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
            "SELECT user_id FROM room_players "
            "WHERE room_id = $1 AND spectating = FALSE",
            room_id,
        )

    settings = _decode_settings(song["settings"])
    brackets = settings.guess_brackets_seconds
    picker_ids: set[UUID] = {r["user_id"] for r in picker_rows}

    players: dict[UUID, state.PlayerRoundState] = {}
    for r in player_rows:
        if r["user_id"] not in picker_ids:
            players[r["user_id"]] = state.PlayerRoundState(user_id=r["user_id"])

    duration = NO_GUESSERS_DURATION if not players else settings.round_max_seconds
    active = state.ActiveRound(
        round_id=round_row["id"],
        song_id=song["id"],
        spotify_track_id=song["spotify_track_id"],
        title=song["title"],
        artist=song["artist"],
        album=song["album"],
        preview_url=song["preview_url"] or "",
        album_image_url=song["album_image_url"],
        picker_ids=picker_ids,
        started_at=round_row["started_at"],
        brackets=brackets,
        players=players,
        hint_field=settings.hint_field,
        round_max_seconds=duration,
        album_art_enabled=settings.album_art_enabled,
    )

    async with game.lock:
        game.active_round = active
        if game.timeout_task is not None:
            game.timeout_task.cancel()
        game.timeout_task = asyncio.create_task(
            _round_timeout_watcher(room_key, active.round_id, duration)
        )

    audio_url = f"/rooms/{room_key}/rounds/{active.round_id}/audio"
    cover_url = f"/rooms/{room_key}/rounds/{active.round_id}/cover"
    await state.hub.broadcast(
        room_key,
        {
            "type": "round_started",
            "payload": {
                "round_id": str(active.round_id),
                "picker_ids": [str(pid) for pid in active.picker_ids],
                "started_at_server": active.started_at.isoformat(),
                "audio_url": audio_url,
                # Send the proxy URL rather than Deezer's CDN URL — the proxy
                # blurs server-side based on the requesting player's bracket,
                # so the unblurred bytes never reach the wire until reveal.
                "album_image_url": cover_url
                if settings.album_art_enabled and active.album_image_url
                else None,
                "guess_brackets_seconds": brackets,
                "round_max_seconds": duration,
            },
        },
    )


async def submit_guess(
    room_key: str, user_id: UUID, round_id: UUID, guessed_track_id: str
) -> dict:
    """Apply a guess. The user picks a track from a search; we resolve its
    title/artist via Deezer and compare to the round's stored values.
    Returns dict with correct/points/bracket_index/finished."""
    game = await state.registry.get(room_key)
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
    guess_album: str | None = None
    try:
        guess_track = await deezer.get_track(guessed_track_id)
        guess_title = guess_track.get("title", "<unknown>")
        artist_obj = guess_track.get("artist") or {}
        guess_artist = artist_obj.get("name", "")
        album_obj = guess_track.get("album") or {}
        guess_album = album_obj.get("title")
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
        hint_fulfilled = (
            False
            if correct
            else matching.is_hint_fulfilled(
                active.hint_field,
                active.artist,
                active.album,
                guess_artist,
                guess_album,
            )
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
            await _broadcast_player_finished(
                room_key, active, user_id, "correct", points
            )
        else:
            await _persist_guess(
                round_id, user_id, bracket_index, active.brackets, guess_label, False, 0
            )
            new_index = bracket_index + 1
            if new_index >= len(active.brackets):
                player.outcome = "exhausted"
                player.points = 0
                await _broadcast_player_finished(
                    room_key, active, user_id, "exhausted", 0
                )
            else:
                player.bracket_index = new_index
                await state.hub.broadcast(
                    room_key,
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
            room_key,
            active,
            user_id,
            "guess",
            guess_label,
            correct,
            bracket_index,
            hint_fulfilled,
        )

        result = {
            "correct": correct,
            "points": player.points if correct else 0,
            "bracket_index": player.bracket_index,
            "finished": player.finished,
            "hint_fulfilled": hint_fulfilled,
        }
        all_done = active.all_finished()

    if all_done:
        # Kick the round transition off in the background so the HTTP
        # response returns immediately. Awaiting here would block /guess and
        # /skip for the entire intermission window (typically 6s), and would
        # also let the WS round_ended/round_started events for the *next*
        # round arrive at the client before this response — meaning the
        # caller would append the just-finished guess into the next round's
        # state. The client still guards the append against a stale round id.
        asyncio.create_task(_end_round(room_key))
    return result


async def submit_skip(room_key: str, user_id: UUID, round_id: UUID) -> dict:
    game = await state.registry.get(room_key)
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
            await _broadcast_player_finished(room_key, active, user_id, "exhausted", 0)
        else:
            player.bracket_index = new_index
            await state.hub.broadcast(
                room_key,
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
            room_key, active, user_id, "skip", None, None, prev_index, False
        )

        result = {"bracket_index": player.bracket_index, "finished": player.finished}
        all_done = active.all_finished()

    if all_done:
        # Kick the round transition off in the background so the HTTP
        # response returns immediately. Awaiting here would block /guess and
        # /skip for the entire intermission window (typically 6s), and would
        # also let the WS round_ended/round_started events for the *next*
        # round arrive at the client before this response — meaning the
        # caller would append the just-finished guess into the next round's
        # state. The client still guards the append against a stale round id.
        asyncio.create_task(_end_round(room_key))
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
    room_key: str,
    active: state.ActiveRound,
    user_id: UUID,
    outcome: str,
    points: int,
) -> None:
    await state.hub.broadcast(
        room_key,
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
    room_key: str,
    active: state.ActiveRound,
    user_id: UUID,
    kind: str,
    guess_text: str | None,
    correct: bool | None,
    bracket_index: int,
    hint_fulfilled: bool,
) -> None:
    attempt = {
        "user_id": str(user_id),
        "kind": kind,
        "guess_text": guess_text,
        "correct": correct,
        "bracket_index": bracket_index,
        "hint_fulfilled": hint_fulfilled,
    }
    # Persist on the round so a picker reconnecting mid-round can replay
    # the same attempts they would have seen had they stayed connected.
    active.picker_attempts.append(attempt)
    payload = {"round_id": str(active.round_id), "attempts": [attempt]}
    message = {"type": "picker_view", "payload": payload}
    for picker_id in active.picker_ids:
        await state.hub.send_to(room_key, picker_id, message)


async def _round_timeout_watcher(room_key: str, round_id: UUID, timeout: float) -> None:
    """Wait `timeout` seconds; if the round identified by `round_id` is still
    active, force any unfinished player to outcome=exhausted (0 pts) and end
    the round. Cancelled when the round ends naturally."""
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return

    game = await state.registry.get(room_key)
    if game is None:
        return

    finished_users: list[UUID] = []
    async with game.lock:
        active = game.active_round
        if active is None or active.round_id != round_id:
            return
        for uid, p in active.players.items():
            if not p.finished:
                p.outcome = "exhausted"
                p.points = 0
                finished_users.append(uid)

    for uid in finished_users:
        await _broadcast_player_finished(room_key, active, uid, "exhausted", 0)
    await _end_round(room_key)


async def _end_round(room_key: str) -> None:
    game = await state.registry.get(room_key)
    if game is None:
        return

    # Claim the active round under the lock so concurrent callers (e.g. the
    # last guess and the timeout watcher firing at nearly the same instant)
    # don't both broadcast round_ended.
    async with game.lock:
        active = game.active_round
        if active is None:
            return
        game.active_round = None
        game.completed_round_ids.append(active.round_id)
        # Don't cancel ourselves — _end_round can be called from inside the
        # timeout watcher, in which case task.cancel() would interrupt the
        # rest of this function at its next await.
        if (
            game.timeout_task is not None
            and game.timeout_task is not asyncio.current_task()
        ):
            game.timeout_task.cancel()
        game.timeout_task = None

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
            WHERE rp.room_id = $1 AND rp.spectating = FALSE
            ORDER BY rp.score DESC, u.username ASC
            """,
            UUID(room_key),
        )

    scoreboard = [
        {
            "user": {"id": str(r["id"]), "username": r["username"]},
            "score": r["score"],
            "previous_score": r["score"] - delta_by_id.get(str(r["id"]), 0),
        }
        for r in scoreboard_rows
    ]

    settings = await _load_settings(room_key)
    intermission = settings.round_intermission_seconds if settings else 6
    is_last_round = not game.song_queue

    full_audio_url = (
        f"/rooms/{room_key}/rounds/{active.round_id}/full-audio"
        if active.preview_url
        else None
    )
    cover_url = (
        f"/rooms/{room_key}/rounds/{active.round_id}/cover"
        if active.album_image_url
        else None
    )
    payload = {
        "round_id": str(active.round_id),
        "title": active.title,
        "artist": active.artist,
        "album_image_url": cover_url,
        "full_audio_url": full_audio_url,
        "scoreboard": scoreboard,
        "intermission_seconds": intermission,
        "is_last_round": is_last_round,
    }
    # Stash before broadcasting so a reconnect that races with the broadcast
    # still finds it. Cleared at the top of `_start_next_round` once the
    # window has lapsed.
    game.last_round_payload = payload
    game.intermission_ends_at = (
        asyncio.get_running_loop().time() + intermission if intermission > 0 else None
    )
    await state.hub.broadcast(
        room_key,
        {"type": "round_ended", "payload": payload},
    )

    if intermission > 0:
        await asyncio.sleep(intermission)
    if is_last_round:
        await _end_game(room_key)
    else:
        await _start_next_round(room_key)


async def _load_settings(room_key: str):
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT settings FROM rooms WHERE id = $1", UUID(room_key)
        )
    if row is None:
        return None
    return _decode_settings(row["settings"])


async def _end_game(room_key: str) -> None:
    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE rooms SET status = 'results', ended_at = NOW() WHERE id = $1",
            UUID(room_key),
        )
        scoreboard_rows = await conn.fetch(
            """
            SELECT u.id, u.username, rp.score
            FROM room_players rp
            JOIN users u ON u.id = rp.user_id
            WHERE rp.room_id = $1 AND rp.spectating = FALSE
            ORDER BY rp.score DESC, u.username ASC
            """,
            UUID(room_key),
        )

    scoreboard = [
        {"user": {"id": str(r["id"]), "username": r["username"]}, "score": r["score"]}
        for r in scoreboard_rows
    ]
    await state.hub.broadcast(
        room_key,
        {
            "type": "game_ended",
            "payload": {
                "scoreboard": scoreboard,
                "restart_unlocks_at": None,
            },
        },
    )


async def restart_game(room_key: str) -> None:
    """Reset scores, songs, and rounds for a room and return to the lobby phase."""
    room_id = UUID(room_key)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            room = await conn.fetchrow("SELECT id FROM rooms WHERE id = $1", room_id)
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
                "UPDATE room_players SET score = 0, spectating = FALSE "
                "WHERE room_id = $1",
                room["id"],
            )
            await conn.execute(
                "UPDATE rooms SET status = 'lobby', started_at = NULL, ended_at = NULL "
                "WHERE id = $1",
                room["id"],
            )
    existing = await state.registry.get(room_key)
    if existing is not None and existing.timeout_task is not None:
        existing.timeout_task.cancel()
    await state.registry.remove(room_key)
