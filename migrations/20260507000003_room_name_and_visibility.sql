-- migrate:up
ALTER TABLE rooms ADD COLUMN name TEXT;
ALTER TABLE rooms ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX idx_rooms_public_lobby ON rooms(created_at DESC) WHERE is_public AND status = 'lobby';

-- migrate:down
DROP INDEX IF EXISTS idx_rooms_public_lobby;
ALTER TABLE rooms DROP COLUMN is_public;
ALTER TABLE rooms DROP COLUMN name;
