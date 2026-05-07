-- migrate:up
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      CITEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT username_length CHECK (char_length(username) BETWEEN 3 AND 32)
);

CREATE TABLE sessions (
    token       TEXT PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);

CREATE TYPE room_status AS ENUM ('lobby', 'selecting', 'playing', 'results');

CREATE TABLE rooms (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code        VARCHAR(8) UNIQUE NOT NULL,
    leader_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status      room_status NOT NULL DEFAULT 'lobby',
    settings    JSONB NOT NULL DEFAULT '{
        "min_popularity": 0,
        "required_artists": [],
        "songs_per_player": 3,
        "guess_brackets_seconds": [0.5, 1, 2.5, 5, 15, 30],
        "album_art_enabled": true,
        "album_art_unblur": true,
        "post_game_delay_seconds": 15
    }'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ
);

CREATE INDEX idx_rooms_status ON rooms(status);
CREATE INDEX idx_rooms_leader ON rooms(leader_id);

CREATE TABLE room_players (
    room_id     UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    score       INT NOT NULL DEFAULT 0,
    connected   BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (room_id, user_id)
);

CREATE TABLE room_songs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id           UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    spotify_track_id  TEXT NOT NULL,
    title             TEXT NOT NULL,
    title_normalized  TEXT NOT NULL,
    artist            TEXT NOT NULL,
    preview_url       TEXT,
    album_image_url   TEXT,
    duration_ms       INT,
    popularity        SMALLINT,
    play_order        INT NOT NULL,
    submitted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (room_id, spotify_track_id),
    UNIQUE (room_id, play_order)
);

-- Multiple players in the same room can pick the same track; their rounds
-- are merged into a single round with all of them excluded from guessing.
CREATE TABLE room_song_pickers (
    room_id       UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    song_id       UUID NOT NULL REFERENCES room_songs(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (song_id, user_id)
);

CREATE INDEX idx_room_song_pickers_room_user ON room_song_pickers(room_id, user_id);

CREATE TABLE rounds (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id     UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    song_id     UUID NOT NULL REFERENCES room_songs(id) ON DELETE CASCADE,
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ,
    UNIQUE (room_id, song_id)
);

CREATE INDEX idx_rounds_room_started ON rounds(room_id, started_at);

CREATE TABLE guesses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id        UUID NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    guessed_at_ms   INT NOT NULL,
    bracket_seconds NUMERIC(4, 1),
    guess_text      TEXT NOT NULL,
    correct         BOOLEAN NOT NULL,
    points          INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_guesses_round_user ON guesses(round_id, user_id);
CREATE INDEX idx_guesses_round_correct ON guesses(round_id) WHERE correct = TRUE;

-- migrate:down
DROP TABLE IF EXISTS guesses;
DROP TABLE IF EXISTS rounds;
DROP TABLE IF EXISTS room_song_pickers;
DROP TABLE IF EXISTS room_songs;
DROP TABLE IF EXISTS room_players;
DROP TABLE IF EXISTS rooms;
DROP TYPE IF EXISTS room_status;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS users;
