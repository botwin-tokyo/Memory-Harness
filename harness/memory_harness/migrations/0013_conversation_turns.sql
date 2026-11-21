CREATE TABLE IF NOT EXISTS conversation_turns (
    source        TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    turn_id       TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    ts            TEXT NOT NULL,
    tool_name     TEXT,
    tool_call_id  TEXT,
    redacted      INTEGER NOT NULL DEFAULT 0,
    window_index  INTEGER,
    received_at   INTEGER NOT NULL,
    PRIMARY KEY (source, session_id, turn_id)
);

CREATE INDEX IF NOT EXISTS ix_conversation_turns_seq
    ON conversation_turns(source, session_id, seq);

CREATE TABLE IF NOT EXISTS conversation_sessions (
    source          TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    hub_lineage     TEXT NOT NULL,
    agent           TEXT,
    model           TEXT,
    title           TEXT,
    started_at      TEXT,
    windows_sealed  INTEGER NOT NULL DEFAULT 0,
    last_push_at    INTEGER NOT NULL,
    PRIMARY KEY (source, session_id)
);
