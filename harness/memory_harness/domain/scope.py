from __future__ import annotations

from memory_harness.models.domain import Principal


class Forbidden(Exception):
    'Raised when a principal attempts something its key does not permit.'


UNIVERSAL_READ_SCOPES = {"reference"}

OPT_IN_SCOPES = {"library"}


def visible_scopes(p: Principal) -> set[str]:
    s = set(p.allowed_scopes)
    s.add(f"private:{p.principal_id}")
    s |= UNIVERSAL_READ_SCOPES
    return s


def default_read_scopes(p: Principal) -> set[str]:
    'What a read sees when the caller asks for nothing in particular.'
    return visible_scopes(p) - OPT_IN_SCOPES


def effective_scopes(p: Principal, requested: list[str] | None) -> set[str]:
    'Narrow-only: intersection of requested with visible.'
    vis = visible_scopes(p)
    return default_read_scopes(p) if not requested else (vis & set(requested))


def default_write_scope(p: Principal, requested: str | None) -> str:
    scope = requested or p.home_scope
    if scope not in p.allowed_scopes and scope != f"private:{p.principal_id}":
        raise Forbidden(scope)
    return scope
