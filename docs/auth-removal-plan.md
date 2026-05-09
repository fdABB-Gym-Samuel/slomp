# Plan: rip out auth, ephemeral per-room identities, in-room rename

## Model

- No accounts. The home page (`/`) is fully anonymous: it shows the public-rooms list and the Create / Join controls.
- A `users` row is created **only at the moment you join or create a room**, lives for as long as you're in that room, and is deleted when you leave or the room is reaped.
- Username uniqueness is **per-room**, not global. Two rooms can each have a "Sam".
- The session cookie still drives identity end-to-end — gameplay code never has to care that the identity is ephemeral, because every handler still gets a `user_id` from the same `Depends(auth.get_current_user_id)`.
- In-room rename: any player can change their own display name as long as it's not taken in their current room. Broadcast to the room over the existing ws.

## Schema migration (new `migrations/20260509000001_ephemeral_users.sql`)

```sql
-- migrate:up
-- All existing accounts become useless (no password column anymore).
TRUNCATE TABLE users CASCADE;

ALTER TABLE users DROP COLUMN password_hash;
ALTER TABLE users DROP CONSTRAINT users_username_key;  -- the implicit CITEXT UNIQUE

-- migrate:down
ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD CONSTRAINT users_username_key UNIQUE (username);
```

Per-room name uniqueness is enforced in app code (the join + rename handlers are the only writers; a real DB constraint would need denormalizing `username` onto `room_players`, not worth it).

## Backend

### Delete

- `backend/app/password.py`
- `argon2-cffi` from `nix/backend.nix:22`

### `backend/app/auth.py`

- Keep as-is. Sessions, cookies, `get_current_user_id`, `lookup_user_id_for_ws` are all still load-bearing.
- Add a small helper `create_ephemeral_user(conn, username) -> UUID` that inserts a `users` row and returns its id. (Or inline it at the call sites — there are only two.)

### `backend/app/routers/auth.py`

Strip down to **just** `/me` (the frontend uses it to detect "do I have a live session?"). Drop:

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/logout` — replaced; logout is implicit on leave
- `PATCH /me/username` — replaced by the in-room rename endpoint below
- `PATCH /me/password`

`/me` keeps its current shape (`UserOut { id, username }`) so the frontend doesn't have to change its type.

### `backend/app/routers/rooms.py`

**`POST /rooms` (create):** now takes `{ username }` body. Inside the existing transaction:

1. Create the `users` row.
2. Create the session + set cookie (move `auth.create_session` + `set_session_cookie` calls here from the deleted login handler).
3. Create the room, insert into `room_players`, run the existing `leave_other_rooms` no-op (will be empty for a fresh user).

**`POST /rooms/by-code/{code}/join` and `POST /rooms/{room_id}/join`:** also take `{ username }`. Same pattern: create user + session, then existing join logic. **Validate** `username` is not already used by anyone else in `room_players` for this room (case-insensitive — `users.username` is `CITEXT`). On collision, return 409.

**`POST /rooms/{room_id}/leave`:** after the existing leave logic, **delete the `users` row** for the leaver. The session row cascades via the existing FK; clear the cookie on the response so the browser doesn't keep sending a stale token.

**NEW `PATCH /rooms/{room_id}/me/username`:** body `{ username }`. Requires session + membership in this room. Validates per-room uniqueness (excluding self). Updates `users.username`. Broadcasts a new `player_renamed` event over the room ws (`{ user_id, username }`), shape consistent with the existing `player_left` etc.

**Other endpoints (`GET /rooms/{id}`, leader actions, etc.):** unchanged — they still depend on `get_current_user_id` and that still works.

### `backend/app/routers/ws.py`

- `/lobby/ws` (line 320): drop the auth gate. Anonymous home-page visitors need to see the public-rooms feed. (The handler doesn't actually use `user_id` for anything — it just sends snapshots and pongs.)
- `/rooms/{id}/ws`: keep its session check. Identity is required to be in a room.
- Disconnect cleanup (`_auto_leave_if_stale` and `remove_player_from_room` callers): when a player is auto-removed past their deadline, also delete their `users` row. Important: leader-transfer (`claim_orphaned_leadership`) must run **before** the user delete, since `rooms.leader_id` has `ON DELETE CASCADE` on the user.

### `backend/app/models.py`

- Delete `RegisterRequest`, `LoginRequest`, `ChangePasswordRequest`, `ChangeUsernameRequest`.
- Add `JoinRoomRequest { username: str (3..32) }` and `RenameRequest { username: str (3..32) }`.
- `UserOut` stays.

### Helper safety check

`backend/app/rooms_helpers.py:_cleanup_empty_room` deletes the room when `room_players` is empty. Since by then no users are tied to that room (we delete users on leave, above), there's nothing to clean up here. But if disconnect-deadline cleanup is the path that empties the room, make sure that path also runs the user-delete — i.e. user-delete lives in `remove_player_from_room`, the one shared exit point.

## Frontend

### Delete

- `frontend/src/routes/login/`
- `frontend/src/routes/register/`
- `frontend/src/routes/profile/`

### `frontend/src/routes/+layout.svelte`

- Drop the `PUBLIC_ROUTES` redirect to `/login`. The home page is now public.
- Show `<Navbar>` only when `auth.user` is set (i.e. while in a room).

### `frontend/src/routes/+page.svelte`

- Drop the "My Rooms" section entirely (you can never be in a room while logged out).
- Drop the `if (!auth.user) return;` guards on `loadPublicRooms` and the lobby ws — both work anonymously now.
- Replace the immediate `createRoom()` / `joinRoom()` actions with a "pick a username" modal step:
  - Modal: single text input (3–32 chars), submit button, error slot for 409 collisions.
  - On submit: call the relevant API method with `{ username }`, then `goto('/room/...')`.
- Persist the last-used username in `localStorage` and pre-fill the modal next time. Tiny QoL, no privacy cost — the value is already client-visible.

### `frontend/src/lib/auth.svelte.ts`

- Drop `login`, `register`, `changeUsername`, `changePassword`.
- Keep `refresh` and `user`. Add `clear()` for use after a successful leave.

### `frontend/src/lib/api.ts`

- Drop login/register/logout/changeUsername/changePassword methods.
- `createRoom(username)`, `joinByCode(code, username)`, `joinRoom(roomId, username)` — all now take a username.
- Add `renameInRoom(roomId, username): Promise<UserOut>`.

### In-room rename UI

- In `RoomHeader.svelte` or `PlayerList.svelte`, render the current user's row with a small pencil icon. Clicking swaps it for an inline text input (Esc cancels, Enter submits).
- On submit, call `api.renameInRoom`. On 409, show "name's taken" inline.
- The room ws handler (`ws.svelte.ts`) gains a `player_renamed` case that updates the local players list. The server-side event shape mirrors `player_left`.

### `Navbar.svelte`

- Drop any "profile" / "logout" links (logout = leave room).
- Add (or keep, if it's already there) a "Leave room" button that calls the leave endpoint and routes back to `/`.

## Edge cases & decisions

- **Refresh while in a room:** session cookie still points to a live `users.id` → `/me` succeeds → frontend reconnects. ✓
- **Refresh after the room was reaped (closed your laptop, came back later):** session cookie points to a dead row → `/me` 401 → frontend shows anonymous home. We should also `clear_session_cookie` on any 401 from `/me` so the dead cookie doesn't linger.
- **Two tabs, two rooms:** today's `leave_other_rooms` enforces single-room-per-user. With ephemeral identities, each tab's create/join produces its own `users` row → both stick. Probably the right call for guests, but worth flagging — if we want to keep the old behavior, it requires a per-browser identifier (cookie outside of `session`), which is more complexity than the feature deserves.
- **Username at create vs join:** symmetric — both flows go through the modal.
- **Validation:** the existing `Field(min_length=3, max_length=32)` on the new `JoinRoomRequest` / `RenameRequest`, plus the `username_length` `CHECK` already on the table, covers it. No regex; `CITEXT` handles case-insensitive comparison.
- **Leader rename:** no special handling needed — `rooms.leader_id` is `users.id` (stable across rename), and the room ws broadcast updates the leader-name display everywhere.
- **Existing `users` rows:** `TRUNCATE users CASCADE` in the migration wipes them along with all dependent rooms/sessions/etc. Nothing of value lost since the app isn't shipped yet.

## Suggested commit order

1. Migration + backend changes (auth router gut, rooms router username flow, leave/disconnect deletes user, lobby ws unauthed, in-room rename endpoint + ws event).
2. Frontend: delete login/register/profile, fix `+layout`, anon-friendly home, username modal.
3. In-room rename UI + ws handler.
4. Cleanup: drop `password.py`, drop `argon2-cffi` from nix.

Each step leaves the app in a working state.
