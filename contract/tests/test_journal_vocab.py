from __future__ import annotations

from memory_contract import (
    COLLAPSED_TAGS,
    JOURNAL_SECTIONS,
    JOURNAL_TYPES,
    book_chunk_tags,
    book_tags,
    conversation_tags,
    journal_lineage_key,
    journal_section_tags,
    journal_tags,
    node_kind,
    turn_tags,
)


def test_a_journal_hub_is_a_journal_and_a_section_is_not() -> None:
    "The section carries its parent's vocabulary."
    assert node_kind(journal_tags("2000-01-01", "full-day")) == "journal"
    assert node_kind(
        journal_section_tags("2000-01-01", "full-day", "reflection")) != "journal"


def test_every_kind_resolves() -> None:
    cases = {
        "journal": journal_tags("2000-01-01", "morning"),
        "conversation": conversation_tags("hermes", "s1"),
        "book": book_tags("sample"),
        "memory": ["seed"],
    }
    for expected, tags in cases.items():
        assert node_kind(tags) == expected, f"{tags} -> {node_kind(tags)}"


def test_the_parts_of_a_whole_are_all_collapsed() -> None:
    for tags in (book_chunk_tags("sample", 3),
                 journal_section_tags("2000-01-01", "full-day", "reflection"),
                 turn_tags("user", 0)):
        assert set(tags) & set(COLLAPSED_TAGS), f"{tags} would render as a node"


def test_the_lineage_key_carries_the_type_not_just_the_date() -> None:
    'The source system is unique by date and says so is a problem.'
    assert journal_lineage_key("2000-01-01", "full-day") == "journal:2000-01-01:full-day"
    assert journal_lineage_key("2000-01-01", "morning") != \
        journal_lineage_key("2000-01-01", "evening")


def test_metrics_are_tags_and_belong_to_companion() -> None:
    tags = journal_tags("2000-01-01", "full-day",
                        metrics={"mood": 4, "sleep": 3}, tone="steady")
    assert "mood:4" in tags and "sleep:3" in tags and "tone:steady" in tags


def test_the_sections_and_types_are_frozen() -> None:
    assert len(JOURNAL_SECTIONS) == 8
    assert "emotional_context" in JOURNAL_SECTIONS
    assert set(JOURNAL_TYPES) == {"morning", "evening", "full-day"}
