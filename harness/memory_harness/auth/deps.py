from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from memory_harness.auth.principal import resolve
from memory_harness.db.connection import open_reader
from memory_harness.models.domain import Principal

_bearer = HTTPBearer(auto_error=False)

_READ_ONLY_POST_PATHS = frozenset({
    "/v1/ask",
    "/v1/recall",
    "/v1/library/search",
    "/v1/explore",
    "/v1/source",
})


def _is_mutating(method: str, path: str) -> bool:
    if method in ("GET", "HEAD", "OPTIONS"):
        return False
    return not (method == "POST" and path in _READ_ONLY_POST_PATHS)


async def require_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="missing bearer token")
    settings = request.app.state.settings
    reader = open_reader(settings.db.path, settings.db.busy_timeout_ms)
    try:
        principal = resolve(reader, creds.credentials)
    finally:
        reader.close()
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid or revoked key")
    if principal.read_only and _is_mutating(request.method, request.url.path):
        raise HTTPException(
            status_code=403,
            detail="read-only key: this principal may read but not modify memory",
        )
    return principal
