'A minimal MCP Streamable-HTTP client for the registration CLI.'

from __future__ import annotations

import json
import os
import random
import time
from typing import Any

import httpx

from memory_contract.environment import load_environment

ENV_MCP_KEY = "MCP_AGENT_KEY"
_ACCEPT = "application/json, text/event-stream"
MAX_RETRIES = 5


class McpError(RuntimeError):
    pass


class ToolError(RuntimeError):
    'A domain rejection from a tool.'

    def __init__(self, tool: str, detail: Any) -> None:
        super().__init__(f"{tool}: {detail}")
        self.tool = tool
        self.detail = detail


class HubClient:
    def __init__(self, key: str | None = None, url: str = "") -> None:
        load_environment()
        url = url or os.environ.get("MEMORY_FLEET_MCP_URL", "")
        if not url.startswith("https://"):
            raise McpError("Set MEMORY_FLEET_MCP_URL to the HTTPS registration endpoint")
        key = key or os.environ.get(ENV_MCP_KEY, "")
        if not key:
            raise McpError(
                f"no MCP key: set ${ENV_MCP_KEY} in this shell (never in the repo)"
            )
        self._url = url
        self._http = httpx.Client(
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": _ACCEPT,
            },
            timeout=60.0,
        )
        self._id = 0
        self._initialized = False

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> HubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params

        backoff = 2.0
        for attempt in range(MAX_RETRIES):
            resp = self._http.post(self._url, content=json.dumps(body))
            if resp.status_code == 429:
                if attempt == MAX_RETRIES - 1:
                    raise McpError("rate limited (429) after retries; back off and rerun")
                time.sleep(backoff * (0.5 + random.random()))
                backoff = min(backoff * 2, 30.0)
                continue
            if resp.status_code == 401:
                raise McpError(
                    f"401 from the hub: ${ENV_MCP_KEY} is missing, wrong, expired or "
                    "revoked"
                )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise McpError(f"{method}: {data['error']}")
            result: dict[str, Any] = data.get("result", {})
            return result
        raise McpError("unreachable")

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "memory-fleet-register", "version": "0.1.0"},
            },
        )
        self._id += 1
        self._http.post(
            self._url,
            content=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        )
        self._initialized = True
        return result

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        'Call a hub tool. Raises ToolError on either failure shape.'
        if not self._initialized:
            self.initialize()
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments or {}})

        if result.get("isError"):
            texts = [c.get("text", "") for c in result.get("content", [])]
            raise ToolError(tool, " ".join(texts).strip() or result)

        payload = result.get("structuredContent")
        if payload is None:
            texts = [c.get("text", "") for c in result.get("content", []) if c.get("text")]
            joined = "\n".join(texts)
            try:
                payload = json.loads(joined)
            except (ValueError, TypeError):
                payload = joined

        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            raise ToolError(tool, payload["error"])
        return payload
