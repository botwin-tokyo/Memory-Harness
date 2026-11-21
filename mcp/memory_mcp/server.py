'The MCP server: FastMCP over Streamable HTTP on 127.0.0.1:8099.'

from __future__ import annotations

import asyncio
import logging

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from memory_mcp.config import get_settings
from memory_mcp.fleet_collector import COUNTERS as fleet_counters
from memory_fleet import from_env as fleet_from_env, start as fleet_start
from memory_mcp.harness import Harness
from memory_mcp.runtime import set_runtime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("memory_mcp")

settings = get_settings()
harness = Harness(settings.harness_base, settings.harness_key)

mcp = FastMCP(
    "Memory-harness",
    instructions="Recall stored information and use remember to save new memories.",
    host=settings.host,
    port=settings.port,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=[f"https://{h}" for h in settings.allowed_hosts]
        + [f"http://{h}" for h in settings.allowed_hosts],
    ),
)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    "Unauthenticated liveness (mirrors the harness's healthz shape)."
    return JSONResponse({"status": "ok", "service": "memory-mcp"})


class BearerAuthMiddleware:

    def __init__(self, app, token: str, protect_prefix: str = "/mcp",
                 client_tokens: dict[str, str] | None = None) -> None:  # type: ignore[no-untyped-def]
        self.app = app
        self.token = token
        self.prefix = protect_prefix
        self.by_token = {v: k for k, v in (client_tokens or {}).items()}

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        presented = (headers.get("authorization") or "").removeprefix("Bearer ").strip()
        named = self.by_token.get(presented)
        shared_ok = bool(self.token) and presented == self.token
        if not named and not shared_ok:
            if not self.token and not self.by_token:
                log.error("no MCP credentials configured — refusing all MCP traffic")
            fleet_counters.unauthorized_request()
            response = JSONResponse({"detail": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        runtime = named or headers.get("x-runtime", "unknown")
        set_runtime(runtime)
        fleet_counters.request()
        log.info("mcp_request runtime=%s path=%s", runtime, scope["path"])
        await self.app(scope, receive, send)


class FleetLifespan:
    "Start and stop the fleet publisher around the inner app's own lifespan."

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        self.app = app
        self.publisher = None
        self.task = None

    def _start(self) -> None:
        cfg = fleet_from_env("mcp")
        if cfg is None:
            return
        from memory_mcp.fleet_collector import build_collector

        self.publisher, self.task = fleet_start(cfg, build_collector())
        log.info("fleet publisher started: %s/%s", cfg.node_id, cfg.source_id)

    async def _stop(self) -> None:
        if self.publisher is None:
            return
        await self.publisher.stop("mcp shutdown")
        await asyncio.sleep(0.2)
        self.task.cancel()
        self.publisher = None

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "lifespan":
            await self.app(scope, receive, send)
            return

        async def _send(message):  # type: ignore[no-untyped-def]
            if message["type"] == "lifespan.startup.complete":
                self._start()
            elif message["type"] == "lifespan.shutdown.complete":
                await self._stop()
            await send(message)

        await self.app(scope, receive, _send)


def create_app():  # type: ignore[no-untyped-def]
    'The ASGI app uvicorn serves (protocol at /mcp, liveness at /healthz).'
    import memory_mcp.identity  # noqa: F401  (registers resources + prompt)
    import memory_mcp.tools  # noqa: F401  (registers the tools on `mcp`)

    return FleetLifespan(
        BearerAuthMiddleware(
            mcp.streamable_http_app(), settings.mcp_access_token,
            client_tokens=settings.client_tokens))


def main() -> None:
    import uvicorn

    uvicorn.run(
        "memory_mcp.server:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
