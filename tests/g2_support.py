"""Gate G2 test support: synthetic keys, token minting and a fully wired app. Nothing here touches a network."""

from __future__ import annotations

import json
import time
from functools import cache
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from hearthward.authz import facade_as
from hearthward.mcp import app as mcp_app
from hearthward.mcp.auth import JwksKeySet, TokenValidator
from hearthward.mcp.config import McpConfig
from hearthward.mcp.runtime import Contact, SyntheticStore, Vendor

# Synthetic values shaped like the ones tools/pipeline.py checks. SYNTHETIC: they name no real resource.
ENV = {
    "HW_MCP_RESOURCE_URI": "https://mcp0synth0.execute-api.us-east-1.amazonaws.com/mcp",
    "HW_FACADE_ISSUER": "https://auth0synth0.execute-api.us-east-1.amazonaws.com",
    "HW_COGNITO_ISSUER": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_SYNTH0001",
    "HW_COGNITO_DOMAIN": "https://hearthward-synth.auth.us-east-1.amazoncognito.com",
    "HW_ALEXA_CLIENT_ID": "synthalexaclient01",
}
SERVICE_CLIENT_ID = "synthetic-alexa-service"
SERVICE_CLIENT_SECRET = "synthetic-svc-secret"  # SYNTHETIC, not a credential
COGNITO_KID = "cognito-synth-key-1"
NOW_MS = int(time.time() * 1000)


@cache
def _rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jws(header: dict[str, Any], claims: dict[str, Any], signer: facade_as.Signer) -> str:
    parts = (header, claims)
    signing_input = ".".join(facade_as.b64url(json.dumps(p, separators=(",", ":")).encode()) for p in parts).encode()
    return f"{signing_input.decode()}.{facade_as.b64url(signer.sign(signing_input))}"


class Stack:
    """One synthetic deployment: Cognito-like RSA keys, the facade's ES256 signer, the facade app and the MCP app."""

    def __init__(self, store: SyntheticStore | None = None, **app_kwargs: Any) -> None:
        self.config = McpConfig.from_env(ENV)
        self.cognito_key = _rsa_key()
        self._pem = self.cognito_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption())
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.cognito_key.public_key()))
        self.cognito_jwks = {"keys": [{**jwk, "kid": COGNITO_KID, "alg": "RS256", "use": "sig"}]}
        self.signer = facade_as.LocalEs256Signer()
        self.upstream_calls: list[tuple[str, bytes, dict[str, str]]] = []
        self.facade_config = facade_as.FacadeConfig(
            issuer=self.config.facade_issuer, resource_uri=self.config.resource_uri,
            cognito_domain=self.config.cognito_domain,
            service_clients={SERVICE_CLIENT_ID: facade_as.secret_digest(SERVICE_CLIENT_SECRET)})
        self.facade = facade_as.create_app(self.facade_config, self.signer, self._upstream)
        self.validator = TokenValidator(self.config, JwksKeySet.static(self.cognito_jwks),
                                        JwksKeySet.static({"keys": [self.signer.public_jwk()]}))
        self.store = store or synthetic_store()
        self._app_kwargs = app_kwargs

    def _upstream(self, url: str, body: bytes, headers: Any) -> facade_as.ProxyResponse:
        self.upstream_calls.append((url, body, dict(headers)))
        return facade_as.ProxyResponse(200, b'{"access_token":"cognito-issued","token_type":"Bearer"}',
                                       "application/json;charset=UTF-8")

    def user_token(self, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.config.cognito_issuer, "sub": "user-synthetic-1", "aud": self.config.resource_uri,
            "client_id": self.config.alexa_client_id, "token_use": "access", "scope": "openid",
            "iat": now, "exp": now + 3600, "jti": "synthetic-jti"}
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self._pem, algorithm="RS256", headers={"kid": COGNITO_KID})

    def service_token(self, **overrides: Any) -> str:
        """A token shaped like the facade's own `/token` output, signed by the same signer."""
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.config.facade_issuer, "sub": SERVICE_CLIENT_ID, "client_id": SERVICE_CLIENT_ID,
            "aud": self.config.resource_uri, "scope": "mcp:service", "iat": now, "exp": now + 3600, "jti": "j1"}
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return _jws({"alg": "ES256", "typ": "JWT", "kid": self.signer.kid}, claims, self.signer)

    def new_mcp_app(self) -> Any:
        """A fresh app: the SDK's session manager can run its lifespan only once per instance."""
        return mcp_app.create_app(self.config, self.validator, self.store, clock=lambda: NOW_MS, **self._app_kwargs)

    def mcp_client(self) -> TestClient:
        return TestClient(self.new_mcp_app(), base_url=self.config.resource_uri.rsplit("/mcp", 1)[0])

    def facade_client(self) -> TestClient:
        return TestClient(self.facade, base_url=self.config.facade_issuer, follow_redirects=False)


def synthetic_store() -> SyntheticStore:
    store = SyntheticStore()
    store.visits = [{"visit_id": "vis_01HZX3K9M2N4P6Q8R0S2T4V6W8", "first_seen_ms": NOW_MS - 300_000,
                     "last_seen_ms": NOW_MS - 240_000, "cameras": ["front_door"], "trigger": "motion_human",
                     "vehicle_seen": "unknown", "visitor": "unknown", "open": False}]
    store.vendors = [Vendor("Acme Water Utility", "+15555550100")]
    store.directory = {"first national bank": ("First National Bank", "+15555550111")}
    store.circle = [Contact("primary", "Robin Synthetic", "+15555550123"),
                    Contact("secondary", "Sam Synthetic", "+15555550124")]
    return store


def rpc(method: str, params: dict[str, Any] | None = None, id_: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize_params(version: str) -> dict[str, Any]:
    return {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "synthetic-alexa", "version": "0"}}


def mcp_headers(token: str | None, version: str | None = "2025-03-26") -> dict[str, str]:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if version:
        headers["MCP-Protocol-Version"] = version
    return headers
