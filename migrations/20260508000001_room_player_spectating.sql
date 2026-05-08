-- migrate:up
-- Late joiners under the `lock_after_lobby` setting are added to a room
-- mid-game but sit out until the current game finishes. We track that
-- explicitly per (room, player) rather than recomputing from joined_at vs
-- started_at, since started_at is null during the selecting phase but late
-- joiners during selecting still need to spectate.
ALTER TABLE room_players
    ADD COLUMN spectating BOOLEAN NOT NULL DEFAULT FALSE;

-- migrate:down
ALTER TABLE room_players DROP COLUMN spectating;
