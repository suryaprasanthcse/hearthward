"""Bearer-token validation for `/mcp` (blueprint §7.3) as plain ASGI middleware.

P-5: a missing, malformed, expired or otherwise invalid token gets `401` with a JSON body and **no
`WWW-Authenticate` header** (Alexa+ does not support it, which is why the SDK's `RequireAuthMiddleware` is
not used). A valid token without the right scope gets `403`.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hearthward.mcp.config import LEEWAY_SECONDS, SERVICE_SCOPE, McpConfig
from hearthward.mcp.runtime import Principal

PRINCIPAL_KEY = "hearthward.principal"
MAX_TOKEN_CHARS = 8192
MAX_BODY_BYTES = 4 * 1024 * 1024
COGNITO_ALGORITHMS = ("RS256",)
FACADE_ALGORITHMS = ("ES256",)
SERVICE_METHODS = frozenset({"initialize", "notifications/initialized", "tools/list", "ping"})


class AuthError(Exception):
    def __init__(self, status: int, error: str, description: str) -> None:
        super().__init__(description)
        self.status, self.error, self.description = status, error, description


def _invalid(description: str) -> AuthError:
    return AuthError(401, "invalid_token", description)


class JwksKeySet:
    """Signing keys by `kid`. `loader` returns a JWKS document; an unknown `kid` triggers one throttled reload."""

    def __init__(self, loader: Callable[[], Mapping[str, Any]], *, refresh_after_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._loader, self._refresh_after, self._clock = loader, refresh_after_s, clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._loaded_at: float | None = None

    def _reload(self) -> None:
        document = self._loader()
        self._keys = {k["kid"]: jwt.PyJWK(k) for k in document.get("keys", []) if "kid" in k}
        self._loaded_at = self._clock()

    def key_for(self, kid: str) -> Any:
        if kid not in self._keys and (self._loaded_at is None or self._clock() - self._loaded_at >= self._refresh_after):
            self._reload()
        if kid not in self._keys:
            raise _invalid("unknown signing key")
        return self._keys[kid].key

    @classmethod
    def static(cls, document: Mapping[str, Any]) -> JwksKeySet:
        return cls(lambda: document)

    @classmethod
    def from_url(cls, url: str) -> JwksKeySet:
        def load() -> Mapping[str, Any]:
            with urllib.request.urlopen(url, timeout=3) as response:
                return json.loads(response.read())

        return cls(load)


def _scopes(claims: Mapping[str, Any]) -> tuple[str, ...]:
    raw = claims.get("scope", "")
    return tuple(raw.split()) if isinstance(raw, str) else ()


class TokenValidator:
    """User tokens come from Cognito, service tokens from the facade; each issuer has exactly one algorithm."""

    def __init__(self, config: McpConfig, cognito_keys: JwksKeySet, facade_keys: JwksKeySet,
                 required_user_scopes: Iterable[str] = ()) -> None:
        self.config, self.cognito_keys, self.facade_keys = config, cognito_keys, facade_keys
        self.required_user_scopes = tuple(required_user_scopes)

    def validate(self, token: str) -> Principal:
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise _invalid("malformed token")
        try:
            header = jwt.get_unverified_header(token)
            issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
        except jwt.PyJWTError:
            raise _invalid("malformed token") from None
        if issuer == self.config.cognito_issuer:
            return self._user(token, header)
        if issuer == self.config.facade_issuer:
            return self._service(token, header)
        raise _invalid("unrecognized issuer")

    def _decode(self, token: str, header: Mapping[str, Any], keys: JwksKeySet, algorithms: tuple[str, ...],
                issuer: str, required: list[str]) -> dict[str, Any]:
        kid = header.get("kid")
        if header.get("alg") not in algorithms or not isinstance(kid, str):
            raise _invalid("unsupported token header")
        try:
            return jwt.decode(token, keys.key_for(kid), algorithms=list(algorithms), audience=self.config.resource_uri,
                              issuer=issuer, leeway=LEEWAY_SECONDS, options={"require": required})
        except jwt.ExpiredSignatureError:
            raise _invalid("token expired") from None
        except jwt.PyJWTError:
            raise _invalid("token rejected") from None

    def _user(self, token: str, header: Mapping[str, Any]) -> Principal:
        claims = self._decode(token, header, self.cognito_keys, COGNITO_ALGORITHMS, self.config.cognito_issuer,
                              ["exp", "iss", "aud", "sub", "client_id", "token_use"])
        if claims["token_use"] != "access":
            raise _invalid("ID tokens are not accepted")
        if claims["client_id"] != self.config.alexa_client_id:
            raise _invalid("token was issued to another client")
        scopes = _scopes(claims)
        if any(needed not in scopes for needed in self.required_user_scopes):
            raise AuthError(403, "insufficient_scope", "the token lacks a required scope")
        return Principal(subject=str(claims["sub"]), kind="user", scopes=scopes)

    def _service(self, token: str, header: Mapping[str, Any]) -> Principal:
        claims = self._decode(token, header, self.facade_keys, FACADE_ALGORITHMS, self.config.facade_issuer,
                              ["exp", "iss", "aud", "sub", "jti"])
        scopes = _scopes(claims)
        if SERVICE_SCOPE not in scopes:
            raise AuthError(403, "insufficient_scope", f"the token lacks the {SERVICE_SCOPE} scope")
        return Principal(subject=str(claims["sub"]), kind="service", scopes=scopes)


def bearer_token(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            parts = value.decode("latin-1").split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
                return parts[1].strip()
            return None
    return None


def error_response(error: AuthError) -> JSONResponse:
    """Never carries `WWW-Authenticate` (P-5)."""
    return JSONResponse({"error": error.error, "error_description": error.description}, status_code=error.status)


def _messages(body: bytes) -> list[Any] | None:
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else [parsed]


class BearerAuthMiddleware:
    """Guards `path`: authenticates, restricts service tokens to `SERVICE_METHODS`, then replays the request body."""

    def __init__(self, app: ASGIApp, validator: TokenValidator, path: str = "/mcp") -> None:
        self.app, self.validator, self.path = app, validator, path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return
        try:
            token = bearer_token(scope)
            if token is None:
                raise _invalid("missing bearer token")
            principal = self.validator.validate(token)
            scope[PRINCIPAL_KEY] = principal
            if principal.kind == "service":
                receive = await self._restrict_service(scope, receive)
        except AuthError as error:
            await error_response(error)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _restrict_service(self, scope: Scope, receive: Receive) -> Receive:
        request = Request(scope, receive)
        if scope["method"] != "POST":
            return receive
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise AuthError(413, "request_too_large", "request body too large")
        messages = _messages(bytes(body))
        if messages is not None:
            for message in messages:
                method = message.get("method") if isinstance(message, dict) else None
                if method not in SERVICE_METHODS:
                    raise AuthError(403, "insufficient_scope", "service tokens may only discover the server")
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        return replay
