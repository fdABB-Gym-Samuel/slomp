from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from .. import audio, auth, cover, db, game as game_logic, state
from ..models import (
    GuessRequest,
    GuessResultOut,
    SkipRequest,
    SkipResultOut,
    decode_settings,
)
from ..rooms_helpers import room_key

router = APIRouter(prefix="/rooms", tags=["game"])


@router.post("/{room_id}/guess", response_model=GuessResultOut)
async def submit_guess(
    room_id: UUID,
    req: GuessRequest,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> GuessResultOut:
    result = await game_logic.submit_guess(
        room_key=room_key(room_id),
        user_id=user_id,
        round_id=req.round_id,
        guessed_track_id=req.guessed_track_id,
    )
    return GuessResultOut(**result)


@router.post("/{room_id}/skip", response_model=SkipResultOut)
async def submit_skip(
    room_id: UUID,
    req: SkipRequest,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> SkipResultOut:
    result = await game_logic.submit_skip(
        room_key=room_key(room_id), user_id=user_id, round_id=req.round_id
    )
    return SkipResultOut(**result)


@router.get("/{room_id}/rounds/{round_id}/audio")
async def get_round_audio(
    room_id: UUID,
    round_id: UUID,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> Response:
    rg = await state.registry.get(room_key(room_id))
    if rg is None or rg.active_round is None or rg.active_round.round_id != round_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "round_not_found", "message": "round is not active"},
        )

    active = rg.active_round
    if user_id in active.picker_ids:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "is_picker",
                "message": "the picker can't fetch their own song",
            },
        )

    player = active.players.get(user_id)
    if player is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "not_in_round",
                "message": "you're not a participant in this round",
            },
        )
    if player.finished:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "already_finished",
                "message": "you've finished this round",
            },
        )
    if not active.preview_url:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "no_preview", "message": "this song has no preview audio"},
        )

    bracket_seconds = active.brackets[player.bracket_index]
    full = await audio.fetch_full(active.track_id, active.preview_url)
    sliced = await audio.get_slice(active.track_id, full, bracket_seconds)

    return Response(
        content=sliced,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{room_id}/rounds/{round_id}/full-audio")
async def get_round_full_audio(
    room_id: UUID,
    round_id: UUID,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> Response:
    """Full, unsliced preview for a completed round. Served during the
    intermission so everyone can hear the song. Refuses to serve while the
    round is still active — that would leak the answer."""
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT rs.track_id, rs.preview_url, rnd.ended_at
            FROM rounds rnd
            JOIN room_songs rs ON rs.id = rnd.song_id
            JOIN rooms r ON r.id = rnd.room_id
            JOIN room_players rp
              ON rp.room_id = r.id AND rp.user_id = $1
            WHERE r.id = $2 AND rnd.id = $3
            """,
            user_id,
            room_id,
            round_id,
        )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "round_not_found", "message": "round not found"},
        )
    if row["ended_at"] is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "round_not_ended", "message": "round is still in progress"},
        )
    if not row["preview_url"]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "no_preview", "message": "this song has no preview audio"},
        )

    full = await audio.fetch_full(row["track_id"], row["preview_url"])
    return Response(
        content=full,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


def _obscure_intensity_for_player(
    settings_raw,
    active: state.ActiveRound,
    user_id: UUID,
) -> tuple[str, float]:
    """Compute (mode, intensity) for the requesting player's current state.
    Picker, finished players, and spectators all see sharp; active guessers
    get the configured schedule (or a mode-specific default linear ramp
    when none is set)."""
    settings = decode_settings(settings_raw)
    mode = settings.album_art_obscure_mode
    # Sharp value depends on mode: blur radius 0 is identity, but pixelate
    # treats 0 as a 1×1 mosaic — clamp to the source side length so the
    # mosaic step is skipped entirely.
    sharp = 256.0 if mode == "pixelate" else 0.0
    if not settings.album_art_unblur or not active.brackets:
        return mode, sharp
    if user_id in active.picker_ids:
        return mode, sharp
    player = active.players.get(user_id)
    if player is None or player.finished:
        return mode, sharp
    idx = min(player.bracket_index, len(active.brackets) - 1)
    schedule = settings.album_art_obscure_per_bracket_px
    if schedule and len(schedule) == len(active.brackets):
        return mode, max(0.0, float(schedule[idx]))
    n = max(1, len(active.brackets) - 1)
    t = idx / n
    # Default ramps progress from heavily obscured (t=0) to sharp (t=1):
    # blur uses Gaussian radius 24→0; pixelate uses target-side 4→256
    # (a 4×4 mosaic at the start, full resolution at the last bracket).
    if mode == "pixelate":
        return mode, max(1.0, 4.0 + (256.0 - 4.0) * t)
    return mode, max(0.0, 24.0 - 24.0 * t)


@router.get("/{room_id}/rounds/{round_id}/cover")
async def get_round_cover(
    room_id: UUID,
    round_id: UUID,
    user_id: UUID = Depends(auth.get_current_user_id),
) -> Response:
    """Album art proxy. Applies a server-side blur or pixelation whose
    intensity is derived from the requesting player's bracket so the
    unobscured bytes never reach the client until the round ends (or the
    requester is the picker / has finished)."""
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT rs.album_image_url, rnd.ended_at, r.settings
            FROM rounds rnd
            JOIN room_songs rs ON rs.id = rnd.song_id
            JOIN rooms r ON r.id = rnd.room_id
            JOIN room_players rp
              ON rp.room_id = r.id AND rp.user_id = $1
            WHERE r.id = $2 AND rnd.id = $3
            """,
            user_id,
            room_id,
            round_id,
        )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "round_not_found", "message": "round not found"},
        )
    if not row["album_image_url"]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "no_cover", "message": "this song has no cover image"},
        )

    mode: cover.ObscureMode = "blur"
    intensity = 0.0
    if row["ended_at"] is None:
        rg = await state.registry.get(room_key(room_id))
        active = rg.active_round if rg is not None else None
        if active is not None and active.round_id == round_id:
            mode_str, intensity = _obscure_intensity_for_player(
                row["settings"], active, user_id
            )
            mode = mode_str  # type: ignore[assignment]

    rendered = await cover.render(row["album_image_url"], mode, intensity)
    return Response(
        content=rendered,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
