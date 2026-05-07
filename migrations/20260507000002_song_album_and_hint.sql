-- migrate:up
ALTER TABLE room_songs ADD COLUMN album TEXT;

-- migrate:down
ALTER TABLE room_songs DROP COLUMN album;
