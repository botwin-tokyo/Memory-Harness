import sqlite3

import pytest

from memory_harness.db.connection import open_writer
from memory_harness.db.migrate import apply_migrations, schema_version

EXPECTED_TABLES = {
    "schema_migrations",
    "meta",
    "emotions",
    "policy",
    "events",
    "facts",
    "preferences",
    "episodes",
    "core_memory",
    "procedures",
    "entity_aliases",
    "memory_links",
    "jobs",
}

EXPECTED_INDEXES = {
    "ux_facts_head",
    "ix_facts_lineage",
    "ux_preferences_head",
    "ix_preferences_lineage",
    "ux_episodes_head",
    "ix_episodes_lineage",
    "ux_procedures_head",
    "ix_procedures_lineage",
    "ix_policy_principal",
    "ix_events_idem",
    "ix_events_scope",
    "ix_events_target",
    "ix_events_learn",
    "ix_facts_spo",
    "ix_facts_status",
    "ix_facts_subject",
    "ix_pref_scope",
    "ix_epi_scope",
    "ix_core_owner",
    "ix_links_src",
    "ix_links_dst",
    "ix_jobs_claim",
}


@pytest.fixture
def con(tmp_path):  # type: ignore[no-untyped-def]
    c = open_writer(tmp_path / "t.db")
    yield c
    c.close()


def _names(con: sqlite3.Connection, kind: str) -> set[str]:
    return {
        str(r[0])
        for r in con.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
    }


def test_all_tables_fts_and_indexes_exist(con) -> None:  # type: ignore[no-untyped-def]
    assert apply_migrations(con) == [1, 2, 3, 5, 6, 7, 8, 10, 11, 12, 13, 14]
    tables = _names(con, "table")
    assert EXPECTED_TABLES <= tables
    assert "notes" in tables
    assert "proposed_people" in tables
    assert "conversation_turns" in tables
    assert "conversation_sessions" in tables
    assert "identities" in tables
    assert "fts_events" in tables
    assert "fts_memories" in tables
    assert EXPECTED_INDEXES <= _names(con, "index")
    assert schema_version(con) == 14


def test_rerun_is_noop(con) -> None:  # type: ignore[no-untyped-def]
    apply_migrations(con)
    assert apply_migrations(con) == []
    assert schema_version(con) == 14


def test_facts_emotion_fk_enforced(con) -> None:  # type: ignore[no-untyped-def]
    apply_migrations(con)
    con.execute(
        "INSERT INTO emotions(emotion_id, name, family, description) "
        "VALUES (127, 'Neutrality', 'neutral', 'baseline')"
    )

    def insert_fact(emotion_id: int) -> None:
        con.execute(
            "INSERT INTO facts(fact_id, subject_ref, predicate, object_json, gloss,"
            " fact_type, scope, valid_from, recorded_at, status, confidence,"
            " source_trust, created_by_agent, lineage_id, revision, emotion_id)"
            " VALUES ('f1', 's', 'p', '{}', 'g', 'project', 'shared', 1, 1, 'active',"
            " 0.9, 'user', 'tester', 'l1', 1, ?)",
            (emotion_id,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        insert_fact(999)

    insert_fact(127)


def test_fts_events_trigger_syncs(con) -> None:  # type: ignore[no-untyped-def]
    apply_migrations(con)
    con.execute(
        "INSERT INTO emotions(emotion_id, name, family, description) "
        "VALUES (127, 'Neutrality', 'neutral', 'baseline')"
    )
    con.execute(
        "INSERT INTO events(event_id, occurred_at, recorded_at, event_type, actor,"
        " created_by_agent, created_on_node, source_trust, content, summary, scope)"
        " VALUES ('e1', 1, 1, 'user_msg', 'user', 'tester', 'node', 'user',"
        " 'the atlas deploy failed', 'deploy failure', 'shared')"
    )
    rows = con.execute(
        "SELECT rowid, summary FROM fts_events WHERE fts_events MATCH 'deploy'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "deploy failure"
