-- Conversation storage. Idempotent: the server applies it at startup.
-- Time is the database's clock (now()), so every server process agrees on leases and idle expiry.

CREATE TABLE IF NOT EXISTS conversations (
    id            text PRIMARY KEY,
    title         text,
    version       integer     NOT NULL DEFAULT 1,
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL,
    last_access   timestamptz NOT NULL,
    lease_token   text,
    lease_expires timestamptz,
    -- The position the next message will take. Reserved in the same statement that bumps the version.
    next_position bigint      NOT NULL DEFAULT 1
);

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS next_position bigint NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS conversations_last_access_idx ON conversations (last_access);

CREATE TABLE IF NOT EXISTS messages (
    conversation_id text        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    position        bigint      NOT NULL,
    id              text        NOT NULL,
    role            text        NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text        NOT NULL,
    attachments     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    created_at      timestamptz NOT NULL,
    PRIMARY KEY (conversation_id, position)
);
