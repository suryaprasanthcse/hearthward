"""The HEARTHWARD MCP server as a Starlette app: Streamable HTTP at exactly `/mcp` (blueprint §5.2).

P-1 stateless, JSON responses, no redirect; P-2 protocol versions 2025-11-25, 2025-06-18 and 2025-03-26 with a
missing `MCP-Protocol-Version` header counted as 2025-03-26; P-3 only synchronous tools; P-4 `structuredContent`
plus the same JSON as text; P-5 401 without `WWW-Authenticate` (see `auth.py`); P-6 DNS-rebinding protection with
explicit `allowed_hosts`.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hearthward.mcp import tools_sminus, tools_splus, wellknown
from hearthward.mcp.auth import MAX_BODY_BYTES, PRINCIPAL_KEY, BearerAuthMiddleware, JwksKeySet, TokenValidator
from hearthward.mcp.config import ALLOWED_PROTOCOL_VERSIONS, DEFAULT_PROTOCOL_VERSION, McpConfig
from hearthward.mcp.runtime import Broker, Notifier, Principal, SyntheticStore, ToolContext
from hearthward.mcp.schemas import SMINUS, input_validator, output_validator, tool_class, tool_definitions

log = logging.getLogger(__name__)
PROTOCOL_HEADER = b"mcp-protocol-version"
MCP_PATH = "/mcp"


def _text(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


class ToolService:
    """Validates a call against the blueprint schemas, runs the handler, and validates what it returns."""

    def __init__(self, store: SyntheticStore, *, clock: Callable[[], int] | None = None,
                 notifier: Notifier | None = None, broker: Broker | None = None) -> None:
        self.store, self.notifier, self.broker = store, notifier, broker
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.definitions = {d["name"]: d for d in tool_definitions()}
        self.inputs = {n: input_validator(d) for n, d in self.definitions.items()}
        self.outputs = {n: output_validator(d) for n, d in self.definitions.items()}

    def list_tools(self) -> list[types.Tool]:
        return [types.Tool.model_validate(d) for d in tool_definitions()]

    def call(self, name: str, arguments: dict[str, Any], principal: Principal | None) -> types.CallToolResult:
        definition = self.definitions.get(name)
        if definition is None:
            return _error(f"unknown tool: {name[:80]}")
        if principal is None or principal.kind != "user":
            return _error("this tool needs a linked household account")
        household = self.store.resolve_household(principal.subject)
        if household is None:
            return _error("no household is linked to this account")
        problems = sorted({".".join(str(p) for p in e.absolute_path) or "(arguments)"
                           for e in self.inputs[name].iter_errors(arguments)})
        if problems:
            return _error("invalid arguments: " + ", ".join(problems))
        ctx = ToolContext(principal, household, self.store, self.clock, self.notifier, self.broker)
        sminus = tool_class(definition) == SMINUS
        handlers = tools_sminus.HANDLERS if sminus else tools_splus.HANDLERS
        try:
            result = handlers[name](arguments, ctx)
            if not self.outputs[name].is_valid(result):
                raise ValueError("handler output does not match outputSchema")
        except Exception:
            log.exception("tool %s failed", name)
            if sminus:  # HX-11: a failure below S- is a refusal, never a pass
                result = tools_sminus.blocked(name, ("HX-02", "HX-11"))
            else:
                return _error("the tool could not complete; nothing was changed")
        return types.CallToolResult(content=[types.TextContent(type="text", text=_text(result))],
                                    structured_content=result, is_error=False)


class PostOnlyMiddleware:
    """P-1: Alexa+ only POSTs, and API Gateway buffers server-sent events, so GET and DELETE are refused."""

    def __init__(self, app: ASGIApp, path: str = MCP_PATH) -> None:
        self.app, self.path = app, path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == self.path and scope["method"] != "POST":
            await JSONResponse({"error": "method_not_allowed"}, status_code=405, headers={"Allow": "POST"})(
                scope, receive, send)
            return
        await self.app(scope, receive, send)


class ProtocolVersionMiddleware:
    """P-2: reject unsupported `MCP-Protocol-Version` values; a missing header counts as 2025-03-26.

    An `initialize` that asks for a version outside the accepted set is answered with the newest accepted one,
    which is how MCP version negotiation works; the client then decides whether to continue.
    """

    def __init__(self, app: ASGIApp, path: str = MCP_PATH) -> None:
        self.app, self.path = app, path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return
        receive = await self._negotiate_initialize(scope, receive)
        headers = list(scope["headers"])
        versions = [v.decode("latin-1").strip() for k, v in headers if k == PROTOCOL_HEADER]
        if not versions:
            scope = {**scope, "headers": [*headers, (PROTOCOL_HEADER, DEFAULT_PROTOCOL_VERSION.encode())]}
        elif len(versions) != 1 or versions[0] not in ALLOWED_PROTOCOL_VERSIONS:
            body = {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600, "message": "Unsupported MCP-Protocol-Version",
                "data": {"supported": list(ALLOWED_PROTOCOL_VERSIONS)}}}
            await JSONResponse(body, status_code=400)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _negotiate_initialize(self, scope: Scope, receive: Receive) -> Receive:
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if len(body) > MAX_BODY_BYTES:
                return _replay(bytes(body))  # too big to inspect; the SDK enforces its own limit
            if not message.get("more_body"):
                break
        raw = bytes(body)
        try:
            parsed = json.loads(raw)
        except ValueError:
            return _replay(raw)
        params = parsed.get("params") if isinstance(parsed, dict) else None
        if (isinstance(parsed, dict) and parsed.get("method") == "initialize" and isinstance(params, dict)
                and isinstance(params.get("protocolVersion"), str)
                and params["protocolVersion"] not in ALLOWED_PROTOCOL_VERSIONS):
            params["protocolVersion"] = ALLOWED_PROTOCOL_VERSIONS[0]
            raw = json.dumps(parsed, separators=(",", ":")).encode()
        return _replay(raw)


def _replay(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def create_app(config: McpConfig, validator: TokenValidator, store: SyntheticStore | None = None, *,
               clock: Callable[[], int] | None = None, notifier: Notifier | None = None,
               broker: Broker | None = None) -> Starlette:
    service = ToolService(store or SyntheticStore(), clock=clock, notifier=notifier, broker=broker)

    async def on_list_tools(ctx: ServerRequestContext, params: types.PaginatedRequestParams | None
                            ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=service.list_tools())

    async def on_call_tool(ctx: ServerRequestContext, params: types.CallToolRequestParams) -> types.CallToolResult:
        scope = getattr(ctx.request, "scope", None) or {}
        return service.call(params.name, params.arguments or {}, scope.get(PRINCIPAL_KEY))

    server: Server = Server("hearthward", version="0.2.0", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH, json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=config.allowed_hosts,
            allowed_origins=config.allowed_origins))
    app.router.redirect_slashes = False
    app.router.routes.extend(wellknown.routes(config))
    app.add_middleware(PostOnlyMiddleware)
    app.add_middleware(ProtocolVersionMiddleware)
    app.add_middleware(BearerAuthMiddleware, validator=validator, path=MCP_PATH)  # last added = outermost
    return app


def create_app_from_env() -> Starlette:
    """The deployable app: keys come from Cognito's and the facade's JWKS URLs, data from the synthetic store."""
    config = McpConfig.from_env()
    validator = TokenValidator(config, JwksKeySet.from_url(config.cognito_jwks_url),
                               JwksKeySet.from_url(config.facade_jwks_url))
    return create_app(config, validator)
