from __future__ import annotations

from memory_contract import (
    COLLAPSED_TAGS,
    SAME_DAY_LINK,
    TODO_ITEM_TAG,
    TODO_TAG,
    journal_section_tags,
    journal_tags,
    node_kind,
    todo_day_key,
    todo_item_key,
    todo_item_tags,
    todo_tags,
)
from memory_contract.vocabulary import LINK_TYPES


def test_the_day_key_is_the_date_and_nothing_else() -> None:
    assert todo_day_key("2000-01-01") == "todo_day:2000-01-01"


def test_an_items_key_carries_its_day_and_source_id() -> None:
    'One lineage per item: a checkbox toggle revises, never duplicates.'
    assert (todo_item_key("2000-01-01", "todo_item_000000000001")
            == "todo_item:2000-01-01:todo_item_000000000001")


def test_hub_and_item_tags_carry_the_date() -> None:
    hub = todo_tags("2000-01-01", done=3, total=7)
    assert hub == ["todo", "date:2000-01-01", "done:3", "total:7"]
    item = todo_item_tags("2000-01-01", completed=True, position=2)
    assert item == ["todo-item", "date:2000-01-01", "completed:true",
                    "position:2"]
    assert "completed:false" in todo_item_tags("2000-01-01", completed=False,
                                               position=0)


def test_todo_is_a_kind_and_items_are_not_collapsed() -> None:
    assert node_kind(todo_tags("2000-01-01", done=0, total=3)) == "todo"
    assert node_kind(todo_item_tags("2000-01-01", completed=False,
                                    position=0)) == "todo"
    assert not (set(todo_item_tags("2000-01-01", completed=False, position=0))
                & set(COLLAPSED_TAGS))
    assert TODO_TAG not in COLLAPSED_TAGS and TODO_ITEM_TAG not in COLLAPSED_TAGS


def test_todo_does_not_shadow_the_other_kinds() -> None:
    'Order matters in node_kind; a new kind can eat an existing one.'
    assert node_kind(journal_tags("2000-01-01", "full-day")) == "journal"
    assert node_kind(journal_section_tags("2000-01-01", "full-day",
                                          "reflection")) == "artifact"
    assert node_kind(["seed"]) == "memory"


def test_same_day_is_a_real_link_type() -> None:
    assert SAME_DAY_LINK == "same_day"
    assert SAME_DAY_LINK in LINK_TYPES
