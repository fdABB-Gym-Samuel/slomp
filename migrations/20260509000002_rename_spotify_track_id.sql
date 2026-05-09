-- The `spotify_track_id` column predates the switch from Spotify to
-- Deezer (Nov 2024 lockdown stripped previews/popularity from non-extended
-- apps). The column now holds Deezer integer IDs as strings; rename it so
-- the schema doesn't lie about its source. The unique constraint keeps its
-- auto-generated name — Postgres doesn't derive it from the live column
-- name, so a column rename alone doesn't break it.

-- migrate:up
ALTER TABLE room_songs RENAME COLUMN spotify_track_id TO track_id;

-- migrate:down
ALTER TABLE room_songs RENAME COLUMN track_id TO spotify_track_id;
