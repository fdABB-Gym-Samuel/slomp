"""Live deadlines for the two grace-period mechanisms so we can surface
countdowns to clients. Single-threaded asyncio access — no lock needed.

`disconnect_deadlines` keys on (room_key, user_id) → unix seconds at which
the player will be auto-leaved from the room.

`empty_room_deadlines` keys on room_key → unix seconds at which an empty
room will be deleted."""

from uuid import UUID

disconnect_deadlines: dict[tuple[str, UUID], float] = {}
empty_room_deadlines: dict[str, float] = {}
