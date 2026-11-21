from __future__ import annotations

from memory_contract import (
    COLLAPSED_TAGS,
    KNOWS_LINK,
    book_tags,
    journal_tags,
    node_kind,
    person_entity_id,
    person_slug,
    person_tags,
)
from memory_contract.vocabulary import LINK_TYPES


def test_a_person_hub_is_a_person() -> None:
    assert node_kind(person_tags("sample", display="Sample")) == "person"


def test_person_does_not_collide_with_the_other_kinds() -> None:
    'Order matters in node_kind; a new kind can shadow an existing one.'
    assert node_kind(book_tags("sample")) == "book"
    assert node_kind(journal_tags("2000-01-01", "full-day")) == "journal"
    assert node_kind(["seed"]) == "memory"


def test_one_person_cannot_become_three() -> None:
    'Without a single slugifier, "Sample Person" splits three ways.'
    spellings = ("Sample Person", "sample person", "Sample-Person", "  SAMPLE   PERSON ")
    assert len({person_slug(s) for s in spellings}) == 1
    assert person_slug("Sample Person") == "sample-person"


def test_the_entity_id_follows_the_house_convention() -> None:
    assert person_entity_id("sample") == "person:sample"
    assert person_entity_id(person_slug("Sample Person")) == "person:sample-person"


def test_knows_is_a_real_link_type() -> None:
    assert KNOWS_LINK == "knows"
    assert KNOWS_LINK in LINK_TYPES


def test_a_person_hub_is_not_collapsed_out_of_the_graph() -> None:
    'Books collapse their pages; a person has no parts to collapse.'
    assert not (set(person_tags("sample")) & set(COLLAPSED_TAGS))


def test_an_empty_name_still_yields_something_addressable() -> None:
    assert person_slug("") == "unknown"
    assert person_slug("!!!") == "unknown"
