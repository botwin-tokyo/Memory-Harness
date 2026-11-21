'Per-request runtime tag (`X-Runtime` header) via a contextvar.'

from __future__ import annotations

from contextvars import ContextVar

_runtime: ContextVar[str] = ContextVar("mcp_runtime", default="unknown")


def set_runtime(value: str) -> None:
    _runtime.set(value or "unknown")


def current_runtime() -> str:
    return _runtime.get()
