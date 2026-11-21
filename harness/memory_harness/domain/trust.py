from __future__ import annotations

TIER = {"untrusted": 0, "agent": 1, "trusted_tool": 2, "user": 3}


def tier_ge(a: str, b: str) -> bool:
    'True if trust tier `a` is at least as high as `b`.'
    return TIER[a] >= TIER[b]


def assign_source_trust(ceiling: str, source_class: str) -> str:
    'Stamp source_trust from the key ceiling + declared content origin.'
    if source_class == "external":
        return "untrusted"
    return ceiling
