

CREATE TABLE schema_migrations (
  version     INTEGER PRIMARY KEY,
  applied_at  INTEGER NOT NULL
);

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE emotions (
  emotion_id  INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  family      TEXT NOT NULL,
  description TEXT NOT NULL
);


CREATE TABLE policy (
  principal_key_id        TEXT PRIMARY KEY,
  key_hash                TEXT NOT NULL UNIQUE,
  principal_id            TEXT NOT NULL,
  node                    TEXT NOT NULL,
  allowed_scopes          TEXT NOT NULL,
  trust_ceiling           TEXT NOT NULL,
  home_scope              TEXT NOT NULL,
  learning_eligible_default INTEGER NOT NULL DEFAULT 1,
  secret_mode             TEXT NOT NULL DEFAULT 'store-but-dont-train',
  link_authority          TEXT NOT NULL DEFAULT 'propose',
  created_at              INTEGER NOT NULL,
  revoked_at              INTEGER
);


CREATE TABLE events (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id       TEXT NOT NULL UNIQUE,
  occurred_at    INTEGER NOT NULL,
  recorded_at    INTEGER NOT NULL,
  session_id     TEXT, thread_id TEXT, task_id TEXT,
  event_type     TEXT NOT NULL,
  actor          TEXT NOT NULL,
  created_by_agent TEXT NOT NULL,
  created_on_node  TEXT NOT NULL,
  source_trust   TEXT NOT NULL,
  content        TEXT,
  content_ref    TEXT,
  summary        TEXT,
  about          TEXT NOT NULL DEFAULT '[]',
  tags           TEXT NOT NULL DEFAULT '[]',
  scope          TEXT NOT NULL,
  content_hash   TEXT,
  idempotency_key TEXT,
  learning_eligible INTEGER NOT NULL DEFAULT 1,
  target_type    TEXT,
  target_id      TEXT,
  redacted       INTEGER NOT NULL DEFAULT 0,
  emotion_id     INTEGER NOT NULL DEFAULT 127 REFERENCES emotions(emotion_id)
);


CREATE TABLE facts (
  fact_id        TEXT PRIMARY KEY,
  subject_ref    TEXT NOT NULL,
  predicate      TEXT NOT NULL,
  object_json    TEXT NOT NULL,
  gloss          TEXT NOT NULL,
  fact_type      TEXT NOT NULL,
  scope          TEXT NOT NULL,
  valid_from     INTEGER NOT NULL,
  valid_to       INTEGER,
  recorded_at    INTEGER NOT NULL,
  invalidated_at INTEGER,
  status         TEXT NOT NULL,
  confidence     REAL NOT NULL,
  source_trust   TEXT NOT NULL,
  created_by_agent TEXT NOT NULL,
  about          TEXT NOT NULL DEFAULT '[]',
  tags           TEXT NOT NULL DEFAULT '[]',
  evidence_refs  TEXT NOT NULL DEFAULT '[]',
  supersedes_fact_id TEXT,
  dispute_count  INTEGER NOT NULL DEFAULT 0,
  learning_eligible INTEGER NOT NULL DEFAULT 1,
  lineage_id     TEXT NOT NULL,
  revision       INTEGER NOT NULL,
  parent_revision_id TEXT,
  is_head        INTEGER NOT NULL DEFAULT 1,
  emotion_id     INTEGER NOT NULL REFERENCES emotions(emotion_id)
);
CREATE UNIQUE INDEX ux_facts_head ON facts(lineage_id, scope) WHERE is_head=1;
CREATE INDEX ix_facts_lineage ON facts(lineage_id, revision);


CREATE TABLE preferences (
  preference_id  TEXT PRIMARY KEY,
  owner_id       TEXT NOT NULL,
  category       TEXT NOT NULL,
  statement      TEXT NOT NULL,
  scope          TEXT NOT NULL,
  strength       TEXT NOT NULL,
  explicitness   TEXT NOT NULL,
  confidence     REAL NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 1,
  last_confirmed_at INTEGER NOT NULL,
  source_trust   TEXT NOT NULL,
  created_by_agent TEXT NOT NULL,
  about          TEXT NOT NULL DEFAULT '[]',
  tags           TEXT NOT NULL DEFAULT '[]',
  active         INTEGER NOT NULL DEFAULT 1,
  in_core        INTEGER NOT NULL DEFAULT 0,
  lineage_id     TEXT NOT NULL,
  revision       INTEGER NOT NULL,
  parent_revision_id TEXT,
  is_head        INTEGER NOT NULL DEFAULT 1,
  emotion_id     INTEGER NOT NULL REFERENCES emotions(emotion_id)
);
CREATE UNIQUE INDEX ux_preferences_head ON preferences(lineage_id, scope) WHERE is_head=1;
CREATE INDEX ix_preferences_lineage ON preferences(lineage_id, revision);

CREATE TABLE episodes (
  episode_id     TEXT PRIMARY KEY,
  start_event_id TEXT, end_event_id TEXT,
  episode_type   TEXT NOT NULL,
  summary        TEXT NOT NULL,
  timeline_json  TEXT NOT NULL DEFAULT '[]',
  participants   TEXT NOT NULL DEFAULT '[]',
  about          TEXT NOT NULL DEFAULT '[]',
  tags           TEXT NOT NULL DEFAULT '[]',
  scope          TEXT NOT NULL,
  outcome        TEXT, status TEXT NOT NULL DEFAULT 'active',
  salience       REAL NOT NULL DEFAULT 0,
  confidence     REAL NOT NULL DEFAULT 0.7,
  source_trust   TEXT NOT NULL,
  created_by_agent TEXT NOT NULL,
  derived_from   TEXT NOT NULL DEFAULT '[]',
  supersedes_episode_id TEXT,
  learning_eligible INTEGER NOT NULL DEFAULT 1,
  lineage_id     TEXT NOT NULL,
  revision       INTEGER NOT NULL,
  parent_revision_id TEXT,
  is_head        INTEGER NOT NULL DEFAULT 1,
  emotion_id     INTEGER NOT NULL REFERENCES emotions(emotion_id)
);
CREATE UNIQUE INDEX ux_episodes_head ON episodes(lineage_id, scope) WHERE is_head=1;
CREATE INDEX ix_episodes_lineage ON episodes(lineage_id, revision);

CREATE TABLE core_memory (
  slot_id        TEXT PRIMARY KEY,
  slot_key       TEXT NOT NULL,
  owner_scope    TEXT NOT NULL,
  value_text     TEXT NOT NULL,
  priority       INTEGER NOT NULL DEFAULT 100,
  token_estimate INTEGER NOT NULL,
  promoted_from  TEXT,
  created_by_agent TEXT NOT NULL,
  about          TEXT NOT NULL DEFAULT '[]',
  tags           TEXT NOT NULL DEFAULT '[]',
  updated_at     INTEGER NOT NULL,
  pinned         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE procedures (
  procedure_id   TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  intent_tags    TEXT NOT NULL DEFAULT '[]',
  trigger_json   TEXT NOT NULL DEFAULT '{}',
  precondition_json TEXT NOT NULL DEFAULT '{}',
  steps_md       TEXT NOT NULL,
  tool_bindings_json TEXT NOT NULL DEFAULT '{}',
  success_signals_json TEXT NOT NULL DEFAULT '{}',
  failure_modes_json TEXT NOT NULL DEFAULT '{}',
  confidence     REAL NOT NULL DEFAULT 0.5,
  learned_from   TEXT NOT NULL DEFAULT '[]',
  last_validated_at INTEGER,
  validation_status TEXT NOT NULL DEFAULT 'candidate',
  scope          TEXT NOT NULL,
  created_by_agent TEXT NOT NULL,
  lineage_id     TEXT NOT NULL,
  revision       INTEGER NOT NULL,
  parent_revision_id TEXT,
  is_head        INTEGER NOT NULL DEFAULT 1,
  emotion_id     INTEGER NOT NULL REFERENCES emotions(emotion_id)
);
CREATE UNIQUE INDEX ux_procedures_head ON procedures(lineage_id, scope) WHERE is_head=1;
CREATE INDEX ix_procedures_lineage ON procedures(lineage_id, revision);

CREATE TABLE entity_aliases (
  alias             TEXT NOT NULL,
  canonical_entity_id TEXT NOT NULL,
  entity_type       TEXT NOT NULL,
  confidence        REAL NOT NULL DEFAULT 0.8,
  source_ref        TEXT,
  PRIMARY KEY (alias, canonical_entity_id)
);


CREATE TABLE memory_links (
  link_id        TEXT PRIMARY KEY,
  src_lineage_id TEXT NOT NULL,
  dst_lineage_id TEXT NOT NULL,
  link_type      TEXT NOT NULL,
  weight         REAL NOT NULL DEFAULT 1.0,
  status         TEXT NOT NULL DEFAULT 'proposed',
  proposed_by    TEXT NOT NULL,
  curated_by     TEXT,
  scope          TEXT NOT NULL,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);


CREATE TABLE jobs (
  job_id      TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'pending',
  attempts    INTEGER NOT NULL DEFAULT 0,
  run_after   INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  last_error  TEXT
);
