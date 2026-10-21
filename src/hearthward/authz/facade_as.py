"""RFC 8414 authorization-server facade in front of Cognito (blueprint §7).

Cognito cannot be Alexa+'s authorization server on its own: it publishes no `code_challenge_methods_supported`,
only OIDC discovery, and has no resource-bound `client_credentials`. This ASGI app adds those pieces and
otherwise stays out of the way: `/authorize` redirects to Cognito managed login with the query untouched,
`authorization_code` and `refresh_token` requests are proxied byte for byte, and `client_credentials` is answered
here with an ES256 service token.

Nothing here talks to AWS on its own. The signer and the Cognito transport are injected; `LocalEs256Signer`
holds a throwaway key for tests, and a KMS-backed signer replaces it at deploy time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from hearthward.mcp.config import SERVICE_SCOPE

TOKEN_LIFETIME_S = 3600
MAX_FORM_BYTES = 16 * 1024
UNKNOWN_CLIENT_DIGEST = "0" * 64
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
GRANTS = ("authorization_code", "refresh_token", "client_credentials")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def secret_digest(secret: str) -> str:
    """How a service client's secret is stored: SHA-256 hex. The plaintext never lives in configuration."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class Signer(Protocol):
    kid: str

    def sign(self, signing_input: bytes) -> bytes:
        """Raw ES256 signature: 32-byte r followed by 32-byte s (JWS form, not DER)."""
        ...

    def public_jwk(self) -> dict[str, str]: ...


class LocalEs256Signer:
    """An in-process P-256 key. Synthetic and test use only; production signs with the KMS key."""

    def __init__(self, kid: str = "facade-token-signer-local", key: ec.EllipticCurvePrivateKey | None = None) -> None:
        self.kid = kid
        self._key = key or ec.generate_private_key(ec.SECP256R1())

    def sign(self, signing_input: bytes) -> bytes:
        r, s = decode_dss_signature(self._key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    def public_jwk(self) -> dict[str, str]:
        numbers = self._key.public_key().public_numbers()
        return {"kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "kid": self.kid,
                "x": b64url(numbers.x.to_bytes(32, "big")), "y": b64url(numbers.y.to_bytes(32, "big"))}


@dataclass(frozen=True)
class ProxyResponse:
    status: int
    body: bytes
    content_type: str


Upstream = Callable[[str, bytes, Mapping[str, str]], ProxyResponse]


def http_upstream(url: str, body: bytes, headers: Mapping[str, str]) -> ProxyResponse:
    """POST to Cognito's token endpoint. Used only when the facade runs deployed; tests inject their own."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return ProxyResponse(response.status, response.read(), response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        return ProxyResponse(exc.code, exc.read(), exc.headers.get("Content-Type", ""))


@dataclass(frozen=True)
class FacadeConfig:
    issuer: str
    resource_uri: str
    cognito_domain: str
    service_clients: Mapping[str, str]  # client_id -> secret_digest(client_secret)

    @property
    def scopes_supported(self) -> list[str]:
        return ["openid", f"{self.resource_uri}/tools", SERVICE_SCOPE]


def authorization_server_metadata(config: FacadeConfig) -> dict[str, Any]:
    return {
        "issuer": config.issuer,
        "authorization_endpoint": f"{config.issuer}/authorize",
        "token_endpoint": f"{config.issuer}/token",
        "jwks_uri": f"{config.issuer}/jwks",
        "response_types_supported": ["code"],
        "grant_types_supported": list(GRANTS),
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "scopes_supported": config.scopes_supported,
    }


def oauth_error(error: str, description: str, status: int = 400, headers: Mapping[str, str] | None = None) -> Response:
    """RFC 6749 §5.2 error body."""
    return JSONResponse({"error": error, "error_description": description}, status_code=status,
                        headers={**NO_STORE, **(headers or {})})


def _basic_credentials(header: str | None) -> tuple[str, str] | None:
    if not header or header[:6].lower() != "basic ":
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except ValueError:
        return None
    client_id, sep, secret = decoded.partition(":")
    return (client_id, secret) if sep else None


class Facade:
    def __init__(self, config: FacadeConfig, signer: Signer, upstream: Upstream = http_upstream,
                 clock: Callable[[], float] = time.time) -> None:
        self.config, self.signer, self.upstream, self.clock = config, signer, upstream, clock

    # -- metadata ------------------------------------------------------------------------------------------

    async def metadata(self, request: Request) -> Response:
        return JSONResponse(authorization_server_metadata(self.config), headers={"Cache-Control": "public, max-age=3600"})

    async def jwks(self, request: Request) -> Response:
        return JSONResponse({"keys": [self.signer.public_jwk()]}, headers={"Cache-Control": "public, max-age=300"})

    # -- /authorize ----------------------------------------------------------------------------------------

    async def authorize(self, request: Request) -> Response:
        """302 to Cognito managed login with every query parameter exactly as received (no re-encoding)."""
        raw = request.scope["query_string"].decode("latin-1")
        target = f"{self.config.cognito_domain}/oauth2/authorize" + (f"?{raw}" if raw else "")
        return RedirectResponse(target, status_code=302, headers=NO_STORE)

    # -- /token --------------------------------------------------------------------------------------------

    async def token(self, request: Request) -> Response:
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            return oauth_error("invalid_request", "the request body is too large", 413)
        media =request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media != "application/x-www-form-urlencoded":
            return oauth_error("invalid_request", "the token endpoint takes application/x-www-form-urlencoded")
        try:
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=bool(body))
        except ValueError:
            return oauth_error("invalid_request", "the request body is not a valid form")
        grant = form.get("grant_type", [""])
        if len(grant) != 1 or not grant[0]:
            return oauth_error("invalid_request", "grant_type is required exactly once")
        if grant[0] in ("authorization_code", "refresh_token"):
            return self._proxy(request, body)
        if grant[0] == "client_credentials":
            return self._client_credentials(request, form)
        return oauth_error("unsupported_grant_type", f"unsupported grant_type: {grant[0][:40]}")

    def _proxy(self, request: Request, body: bytes) -> Response:
        """Cognito answers authorization_code and refresh_token; Basic auth and the form pass through unchanged."""
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if authorization := request.headers.get("authorization"):
            headers["Authorization"] = authorization
        try:
            reply = self.upstream(f"{self.config.cognito_domain}/oauth2/token", body, headers)
        except Exception:  # noqa: BLE001 - an upstream outage is the client's retry, not a crash
            return oauth_error("temporarily_unavailable", "the identity provider did not answer", 503)
        return Response(reply.body, status_code=reply.status, media_type=reply.content_type or "application/json",
                        headers=NO_STORE)

    def _authenticate(self, request: Request, form: Mapping[str, list[str]]) -> str | Response:
        basic = _basic_credentials(request.headers.get("authorization"))
        if basic is not None:
            client_id, secret = basic
        else:
            client_id, secret = form.get("client_id", [""])[0], form.get("client_secret", [""])[0]
        known = client_id in self.config.service_clients
        expected = self.config.service_clients.get(client_id, UNKNOWN_CLIENT_DIGEST)  # same work for unknown ids
        if hmac.compare_digest(expected, secret_digest(secret)) and known:
            return client_id
        used_header = request.headers.get("authorization") is not None
        return oauth_error("invalid_client", "client authentication failed", 401 if used_header else 400,
                            {"WWW-Authenticate": 'Basic realm="hearthward"'} if used_header else None)

    def _client_credentials(self, request: Request, form: Mapping[str, list[str]]) -> Response:
        client = self._authenticate(request, form)
        if isinstance(client, Response):
            return client
        resources = form.get("resource", [])
        if not resources or any(r != self.config.resource_uri for r in resources):
            return oauth_error("invalid_target", "resource must equal the canonical MCP URI")
        scopes = form.get("scope", [""])
        if len(scopes) != 1 or scopes[0].split() != [SERVICE_SCOPE]:
            return oauth_error("invalid_scope", f"scope must be {SERVICE_SCOPE}")
        now = int(self.clock())
        header = {"alg": "ES256", "typ": "JWT", "kid": self.signer.kid}
        claims = {"iss": self.config.issuer, "sub": client, "client_id": client, "aud": self.config.resource_uri,
                  "scope": SERVICE_SCOPE, "iat": now, "nbf": now, "exp": now + TOKEN_LIFETIME_S,
                  "jti": b64url(secrets.token_bytes(16))}
        signing_input = ".".join(b64url(json.dumps(part, separators=(",", ":"), sort_keys=True).encode())
                                 for part in (header, claims)).encode("ascii")
        token = f"{signing_input.decode()}.{b64url(self.signer.sign(signing_input))}"
        return JSONResponse({"access_token": token, "token_type": "Bearer", "expires_in": TOKEN_LIFETIME_S,
                             "scope": SERVICE_SCOPE}, headers=NO_STORE)


def create_app(config: FacadeConfig, signer: Signer, upstream: Upstream = http_upstream,
               clock: Callable[[], float] = time.time) -> Starlette:
    facade = Facade(config, signer, upstream, clock)
    app = Starlette(routes=[
        Route("/.well-known/oauth-authorization-server", facade.metadata, methods=["GET"]),
        Route("/.well-known/openid-configuration", facade.metadata, methods=["GET"]),
        Route("/jwks", facade.jwks, methods=["GET"]),
        Route("/authorize", facade.authorize, methods=["GET"]),
        Route("/token", facade.token, methods=["POST"]),
    ])
    app.router.redirect_slashes = False
    return app
