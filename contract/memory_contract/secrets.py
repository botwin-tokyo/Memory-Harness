from __future__ import annotations

import math
import re
from collections import Counter

SECRET_PATTERNS = [
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*\S{8,}",
    r"gh[pousr]_[A-Za-z0-9]{36,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"\b[0-9a-f]{48,}\b",
]

REDACTED = "[REDACTED-SECRET]"
DEFAULT_ENTROPY_THRESHOLD = 4.5
_MIN_TOKEN_LEN = 20

_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_-]{20,}")


def _shannon(s: str) -> float:
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def _redact_high_entropy_tokens(text: str, threshold: float) -> tuple[str, bool]:
    found = False

    def repl(m: re.Match[str]) -> str:
        nonlocal found
        token = m.group(0)
        if len(token) >= _MIN_TOKEN_LEN and _shannon(token) >= threshold:
            found = True
            return REDACTED
        return token

    return _TOKEN_RE.sub(repl, text), found


def scan_and_redact(
    text: str | None, entropy_threshold: float = DEFAULT_ENTROPY_THRESHOLD
) -> tuple[str | None, bool]:
    'Redact secrets from `text`. Returns (redacted_text, found_secret).'
    if not text:
        return text, False
    found = REDACTED in text
    for pat in SECRET_PATTERNS:
        text, n = re.subn(pat, REDACTED, text)
        found = found or n > 0
    text, ent = _redact_high_entropy_tokens(text, entropy_threshold)
    return text, (found or ent)
