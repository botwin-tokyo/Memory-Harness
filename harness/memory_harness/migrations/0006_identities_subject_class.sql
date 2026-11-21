
CREATE TABLE identities (
  id            TEXT PRIMARY KEY,
  display       TEXT NOT NULL,
  subject_class TEXT NOT NULL,
  pronouns      TEXT,
  description   TEXT
);

ALTER TABLE facts       ADD COLUMN subject_class TEXT NOT NULL DEFAULT 'world';
ALTER TABLE preferences ADD COLUMN subject_class TEXT NOT NULL DEFAULT 'world';
ALTER TABLE episodes    ADD COLUMN subject_class TEXT NOT NULL DEFAULT 'world';
ALTER TABLE notes       ADD COLUMN subject_class TEXT NOT NULL DEFAULT 'world';
ALTER TABLE procedures  ADD COLUMN subject_class TEXT NOT NULL DEFAULT 'world';
ALTER TABLE core_memory ADD COLUMN subject_class TEXT NOT NULL DEFAULT 'world';

CREATE INDEX ix_facts_subjclass       ON facts(subject_class);
CREATE INDEX ix_preferences_subjclass ON preferences(subject_class);
CREATE INDEX ix_episodes_subjclass    ON episodes(subject_class);
CREATE INDEX ix_notes_subjclass       ON notes(subject_class);
CREATE INDEX ix_procedures_subjclass  ON procedures(subject_class);
CREATE INDEX ix_core_subjclass        ON core_memory(subject_class);
