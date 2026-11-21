
CREATE VIRTUAL TABLE fts_events USING fts5(
  summary, content, about, tags,
  content='events', content_rowid='seq', tokenize='porter unicode61'
);

CREATE TRIGGER fts_events_ai AFTER INSERT ON events BEGIN
  INSERT INTO fts_events(rowid, summary, content, about, tags)
  VALUES (new.seq, new.summary, new.content, new.about, new.tags);
END;

CREATE TRIGGER fts_events_ad AFTER DELETE ON events BEGIN
  INSERT INTO fts_events(fts_events, rowid, summary, content, about, tags)
  VALUES ('delete', old.seq, old.summary, old.content, old.about, old.tags);
END;

CREATE TRIGGER fts_events_au AFTER UPDATE ON events BEGIN
  INSERT INTO fts_events(fts_events, rowid, summary, content, about, tags)
  VALUES ('delete', old.seq, old.summary, old.content, old.about, old.tags);
  INSERT INTO fts_events(rowid, summary, content, about, tags)
  VALUES (new.seq, new.summary, new.content, new.about, new.tags);
END;

CREATE VIRTUAL TABLE fts_memories USING fts5(
  body, about, tags, kind UNINDEXED, ref_id UNINDEXED,
  tokenize='porter unicode61'
);
