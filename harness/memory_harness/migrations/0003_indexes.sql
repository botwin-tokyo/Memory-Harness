
CREATE INDEX ix_policy_principal ON policy(principal_id);

CREATE INDEX ix_events_idem   ON events(idempotency_key, recorded_at);
CREATE INDEX ix_events_scope  ON events(scope, seq);
CREATE INDEX ix_events_target ON events(target_type, target_id);
CREATE INDEX ix_events_learn  ON events(learning_eligible, seq);

CREATE INDEX ix_facts_spo     ON facts(subject_ref, predicate, scope) WHERE status='active';
CREATE INDEX ix_facts_status  ON facts(status);
CREATE INDEX ix_facts_subject ON facts(subject_ref);

CREATE INDEX ix_pref_scope    ON preferences(scope, active);
CREATE INDEX ix_epi_scope     ON episodes(scope, status);
CREATE INDEX ix_core_owner    ON core_memory(owner_scope, priority);

CREATE INDEX ix_links_src     ON memory_links(src_lineage_id, status);
CREATE INDEX ix_links_dst     ON memory_links(dst_lineage_id, status);

CREATE INDEX ix_jobs_claim    ON jobs(state, run_after);
