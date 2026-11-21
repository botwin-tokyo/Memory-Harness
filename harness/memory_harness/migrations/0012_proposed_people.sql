CREATE TABLE IF NOT EXISTS proposed_people (
    proposal_id  TEXT PRIMARY KEY,
    kind         TEXT NOT NULL DEFAULT 'person',
    name         TEXT NOT NULL,
    slug         TEXT NOT NULL,
    sentence     TEXT NOT NULL DEFAULT '',
    relationship TEXT NOT NULL DEFAULT '',
    mentions     INTEGER NOT NULL DEFAULT 0,
    merge_into   TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'proposed',
    proposed_by  TEXT NOT NULL DEFAULT '',
    created_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposed_people_status
    ON proposed_people(status, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_proposed_people_slug
    ON proposed_people(kind, slug);
