"""`GET /.well-known/oauth-protected-resource` (RFC 9728), served by the MCP API (blueprint §7.2).

Alexa+ reads only `authorization_servers[0]` and caches this document at deploy time, so these values must
never change after a deploy. `resource` is the canonical MCP URI exactly as the add-on manifest spells it.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hearthward.mcp.config import McpConfig

PATH = "/.well-known/oauth-protected-resource"


def protected_resource_metadata(config: McpConfig) -> dict[str, Any]:
    return {"resource": config.resource_uri, "authorization_servers": [config.facade_issuer],
            "scopes_supported": config.scopes_supported}


def routes(config: McpConfig) -> list[Route]:
    document = protected_resource_metadata(config)

    async def metadata(request: Request) -> JSONResponse:
        return JSONResponse(document, headers={"Cache-Control": "public, max-age=3600"})

    return [Route(PATH, metadata, methods=["GET"])]
