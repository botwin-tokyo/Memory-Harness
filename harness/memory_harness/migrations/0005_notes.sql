
CREATE TABLE notes (
  note_id        TEXT PRIMARY KEY,
  note_type      TEXT NOT NULL DEFAULT 'note',
  body           TEXT NOT NULL,
  scope          TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'active',
  confidence     REAL NOT NULL DEFAULT 0.7,
  source_trust   TEXT NOT NULL,
  created_by_agent TEXT NOT NULL,
  about          TEXT NOT NULL DEFAULT '[]',
  tags           TEXT NOT NULL DEFAULT '[]',
  dispute_count  INTEGER NOT NULL DEFAULT 0,
  learning_eligible INTEGER NOT NULL DEFAULT 1,
  recorded_at    INTEGER NOT NULL,
  lineage_id     TEXT NOT NULL,
  revision       INTEGER NOT NULL,
  parent_revision_id TEXT,
  is_head        INTEGER NOT NULL DEFAULT 1,
  emotion_id     INTEGER NOT NULL REFERENCES emotions(emotion_id)
);
CREATE UNIQUE INDEX ux_notes_head ON notes(lineage_id, scope) WHERE is_head=1;
CREATE INDEX ix_notes_lineage ON notes(lineage_id, revision);
CREATE INDEX ix_notes_scope ON notes(scope, status);
