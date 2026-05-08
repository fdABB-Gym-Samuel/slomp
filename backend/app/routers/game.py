from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from .. import audio, auth, db, game as game_logic, state
from ..models import GuessRequest, GuessResultOut, SkipRequest, SkipResultOut
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
    full = await audio.fetch_full(active.spotify_track_id, active.preview_url)
    sliced = await audio.slice_audio(full, bracket_seconds)

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
            SELECT rs.spotify_track_id, rs.preview_url, rnd.ended_at
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

    full = await audio.fetch_full(row["spotify_track_id"], row["preview_url"])
    return Response(
        content=full,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
