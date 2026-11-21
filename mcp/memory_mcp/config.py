'Settings from the environment (systemd EnvironmentFile in production).'

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from memory_contract.environment import load_environment


DEFAULT_ALLOWED_HOSTS = (

    "127.0.0.1:8099",
    "localhost:8099",
)


class Settings(BaseModel):
    harness_base: str = "http://127.0.0.1:8080"
    harness_key: str = ""
    mcp_access_token: str = ""
    client_tokens: dict[str, str] = {}
    host: str = "127.0.0.1"
    port: int = 8099
    allowed_hosts: list[str] = list(DEFAULT_ALLOWED_HOSTS)
    ingest_base: str = "http://127.0.0.1:8100"
    seed_api_key: str = ""


def get_settings() -> Settings:
    load_environment()
    hosts = os.environ.get("MCP_ALLOWED_HOSTS", "")
    return Settings(
        harness_base=os.environ.get("HARNESS_BASE", "http://127.0.0.1:8080"),
        harness_key=os.environ.get("MCP_HARNESS_KEY", ""),
        mcp_access_token=os.environ.get("MCP_ACCESS_TOKEN", ""),
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8099")),
        allowed_hosts=(
            [h.strip() for h in hosts.split(",") if h.strip()]
            if hosts
            else list(DEFAULT_ALLOWED_HOSTS)
        ),
        ingest_base=os.environ.get("INGEST_BASE", "http://127.0.0.1:8100"),
        seed_api_key=os.environ.get("SEED_API_KEY", ""),
        client_tokens=_parse_client_tokens(os.environ.get("MCP_CLIENT_TOKENS", "")),
    )


def _parse_client_tokens(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in raw.split(","):
        name, _, token = entry.strip().partition(":")
        if name.strip() and token.strip():
            out[name.strip()] = token.strip()
    return out
