from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from .. import auth, db
from ..models import UserOut

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
