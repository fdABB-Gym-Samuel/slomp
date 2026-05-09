-- The `guessed_at_ms` column was reserved for guess-timing analytics that
-- never landed; every insert hardcodes `0` and nothing reads it back. Drop
-- it before the dead column starts looking like state somebody depends on.

-- migrate:up
ALTER TABLE guesses DROP COLUMN guessed_at_ms;

-- migrate:down
ALTER TABLE guesses ADD COLUMN guessed_at_ms INT NOT NULL DEFAULT 0;
