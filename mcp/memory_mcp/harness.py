from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp.exceptions import ToolError


class Harness:
    'Async client for the harness REST API. Never touches the DB.'

    def __init__(
        self,
        base_url: str,
        key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            r = await self._client.request(method, path, json=json, params=params)
        except httpx.HTTPError as exc:
            raise ToolError(f"memory-harness unreachable: {exc}") from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ToolError(f"memory-harness {r.status_code}: {detail}")
        return r.json()


    async def whoami(self) -> dict[str, Any]:
        return await self._call("GET", "/v1/whoami")  # type: ignore[no-any-return]

    async def emotions(self) -> list[dict[str, Any]]:
        return await self._call("GET", "/v1/emotions")  # type: ignore[no-any-return]

    async def library_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/library/search", json=payload)  # type: ignore[no-any-return]

    async def library_books(self) -> dict[str, Any]:
        return await self._call("GET", "/v1/library/books")  # type: ignore[no-any-return]

    async def conversations(self, source: str | None = None) -> dict[str, Any]:
        q = f"?source={source}" if source else ""
        return await self._call("GET", f"/v1/conversations{q}")  # type: ignore[no-any-return]

    async def transcript(self, source: str, session_id: str) -> dict[str, Any]:
        return await self._call(  # type: ignore[no-any-return]
            "GET", f"/v1/conversations/{source}/{session_id}/transcript")

    async def graph_ids(self, lineage: str) -> dict[str, Any]:
        return await self._call(  # type: ignore[no-any-return]
            "GET", f"/v1/graph?ids={lineage}&limit=1")

    async def private_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/private/write", json=payload)  # type: ignore[no-any-return]

    async def private_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/private/search", json=payload)  # type: ignore[no-any-return]

    async def private_recent(self, limit: int) -> dict[str, Any]:
        return await self._call("GET", f"/v1/private/recent?limit={int(limit)}")  # type: ignore[no-any-return]

    async def private_forget(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/private/forget", json=payload)  # type: ignore[no-any-return]

    async def recall(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/recall", json=payload)  # type: ignore[no-any-return]

    async def remember(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/remember", json=payload)  # type: ignore[no-any-return]

    async def intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/intake", json=payload)  # type: ignore[no-any-return]

    async def explore(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/explore", json=payload)  # type: ignore[no-any-return]

    async def history(self, lineage_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/v1/history/{lineage_id}")  # type: ignore[no-any-return]

    async def link(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/link", json=payload)  # type: ignore[no-any-return]

    async def source(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/source", json=payload)  # type: ignore[no-any-return]

    async def forget(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v1/forget", json=payload)  # type: ignore[no-any-return]
