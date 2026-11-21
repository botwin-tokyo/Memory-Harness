ALTER TABLE preferences ADD COLUMN item TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX ux_preferences_identity
  ON preferences(owner_id, subject_class, scope, category, item)
  WHERE is_head=1 AND active=1;

CREATE INDEX ix_preferences_category ON preferences(category, subject_class);
