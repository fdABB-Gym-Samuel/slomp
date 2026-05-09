-- Drop accounts. Users are now ephemeral identities tied to a room
-- membership: created when you join/create a room, destroyed when you leave.
-- Username uniqueness becomes per-room (enforced in app code), not global.
-- rooms.leader_id becomes nullable + SET NULL so deleting a leader doesn't
-- kill the room before its empty-room cleanup window can run.

-- migrate:up
TRUNCATE TABLE users CASCADE;

ALTER TABLE users DROP COLUMN password_hash;
ALTER TABLE users DROP CONSTRAINT users_username_key;

ALTER TABLE rooms DROP CONSTRAINT rooms_leader_id_fkey;
ALTER TABLE rooms ALTER COLUMN leader_id DROP NOT NULL;
ALTER TABLE rooms ADD CONSTRAINT rooms_leader_id_fkey
    FOREIGN KEY (leader_id) REFERENCES users(id) ON DELETE SET NULL;

-- migrate:down
ALTER TABLE rooms DROP CONSTRAINT rooms_leader_id_fkey;
ALTER TABLE rooms ALTER COLUMN leader_id SET NOT NULL;
ALTER TABLE rooms ADD CONSTRAINT rooms_leader_id_fkey
    FOREIGN KEY (leader_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE users ADD CONSTRAINT users_username_key UNIQUE (username);
ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT '';
