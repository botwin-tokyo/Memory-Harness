from __future__ import annotations

from memory_harness.domain.scope import (
    OPT_IN_SCOPES,
    default_read_scopes,
    effective_scopes,
    visible_scopes,
)
from memory_harness.models.domain import Principal


def principal(scopes: list[str], pid: str = "self",
              learns: bool = True) -> Principal:
    return Principal(
        principal_key_id="k1", principal_id=pid, node="node-ingest",
        allowed_scopes=scopes, trust_ceiling="agent", home_scope="shared",
        learning_eligible_default=learns,
    )


def test_the_library_is_not_in_a_default_read() -> None:
    p = principal(["shared", "reference", "library"])
    assert "library" in visible_scopes(p), "still authorised"
    assert "library" not in default_read_scopes(p), "but not by default"
    assert "library" not in effective_scopes(p, None)
    assert "shared" in effective_scopes(p, None)


def test_the_library_is_readable_when_asked_for_by_name() -> None:
    p = principal(["shared", "reference", "library"])
    assert effective_scopes(p, ["library"]) == {"library"}


def test_a_key_without_the_library_cannot_read_it_even_by_asking() -> None:
    'Opt-in is not a bypass: narrowing can only ever intersect.'
    p = principal(["shared"], pid="agent-a")
    assert effective_scopes(p, ["library"]) == set()


def test_reference_is_still_universally_readable() -> None:
    p = principal(["shared"], pid="agent-a")
    assert "reference" in effective_scopes(p, None)


def test_only_the_library_is_opt_in() -> None:
    assert OPT_IN_SCOPES == {"library"}


def test_a_caller_may_turn_learning_off_but_never_on() -> None:
    from memory_harness.domain.remember import prepare_write

    p = principal(["shared"])
    off = prepare_write(p, type="note", body="a passage from a book", scope=None,
                        source_class="external", confidence=None,
                        entropy_threshold=4.5, learning_eligible=False)
    assert off.learning_eligible is False

    on = prepare_write(p, type="note", body="a passage", scope=None,
                       source_class="external", confidence=None,
                       entropy_threshold=4.5, learning_eligible=True)
    assert on.learning_eligible is True

    denied = principal(["shared"], learns=False)
    still_off = prepare_write(denied, type="note", body="x", scope=None,
                              source_class="external", confidence=None,
                              entropy_threshold=4.5, learning_eligible=True)
    assert still_off.learning_eligible is False, "a client cannot promote itself"
