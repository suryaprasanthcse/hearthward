"""Gate G3: the runtime pieces the stack deploys: KMS signer, Lambda adapter, container entry point."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

import boto3
import jwt
import pytest
from moto import mock_aws
from starlette.testclient import TestClient

from hearthward.authz import facade_as, lambda_handler
from hearthward.authz.kms_signer import KmsEs256Signer
from hearthward.mcp import asgi
from hearthward.mcp.auth import JwksKeySet, TokenValidator
from hearthward.mcp.config import McpConfig
from tests.g2_support import ENV, SERVICE_CLIENT_ID, SERVICE_CLIENT_SECRET, Stack, mcp_headers, rpc

pytestmark = pytest.mark.gate_g3


@pytest.fixture
def kms(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                        "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(name, value)
    with mock_aws():
        yield boto3.client("kms")


@pytest.fixture
def key_id(kms: Any) -> str:
    return kms.create_key(KeySpec="ECC_NIST_P256", KeyUsage="SIGN_VERIFY")["KeyMetadata"]["KeyId"]


def test_kms_signature_verifies_against_the_published_jwk(kms: Any, key_id: str) -> None:
    signer = KmsEs256Signer(kms, key_id)
    header = facade_as.b64url(json.dumps({"alg": "ES256", "kid": signer.kid}).encode())
    payload = facade_as.b64url(json.dumps({"hello": "world"}).encode())
    signature = signer.sign(f"{header}.{payload}".encode())
    assert len(signature) == 64
    token = f"{header}.{payload}.{facade_as.b64url(signature)}"
    assert jwt.decode(token, jwt.PyJWK(signer.public_jwk()).key, algorithms=["ES256"]) == {"hello": "world"}


def test_the_jwk_is_public_and_p256(kms: Any, key_id: str) -> None:
    jwk = KmsEs256Signer(kms, key_id).public_jwk()
    assert jwk["kty"] == "EC" and jwk["crv"] == "P-256" and jwk["alg"] == "ES256" and "d" not in jwk


def test_a_non_p256_key_is_refused(kms: Any) -> None:
    other = kms.create_key(KeySpec="ECC_NIST_P384", KeyUsage="SIGN_VERIFY")["KeyMetadata"]["KeyId"]
    with pytest.raises(ValueError, match="ECC_NIST_P256"):
        KmsEs256Signer(kms, other).public_jwk()


def test_a_kms_signed_facade_token_is_accepted_at_mcp(kms: Any, key_id: str) -> None:
    stack = Stack()
    signer = KmsEs256Signer(kms, key_id)
    facade = facade_as.create_app(stack.facade_config, signer)
    with TestClient(facade, base_url=stack.config.facade_issuer) as client:
        form = {"grant_type": "client_credentials", "resource": stack.config.resource_uri, "scope": "mcp:service"}
        basic = base64.b64encode(f"{SERVICE_CLIENT_ID}:{SERVICE_CLIENT_SECRET}".encode()).decode()
        token = client.post("/token", data=form, headers={"Authorization": f"Basic {basic}"}).json()["access_token"]
    validator = TokenValidator(stack.config, JwksKeySet.static(stack.cognito_jwks),
                               JwksKeySet.static({"keys": [signer.public_jwk()]}))
    assert validator.validate(token).kind == "service"


# ── Lambda adapter ───────────────────────────────────────────────────────────────────────────────────────


def event(method: str, path: str, query: str = "", body: bytes = b"", headers: dict[str, str] | None = None,
          base64_body: bool = False) -> dict[str, Any]:
    return {"version": "2.0", "rawPath": path, "rawQueryString": query,
            "headers": {"Host": "auth.example", "Content-Type": "application/x-www-form-urlencoded",
                        **(headers or {})},
            "requestContext": {"http": {"method": method, "path": path, "sourceIp": "203.0.113.9"}},
            "body": base64.b64encode(body).decode() if base64_body else body.decode(), "isBase64Encoded": base64_body}


@pytest.fixture
def lambda_stack() -> Stack:
    return Stack()


def call(stack: Stack, e: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    return asyncio.run(lambda_handler.call_asgi(stack.facade, e))


def test_adapter_serves_metadata(lambda_stack: Stack) -> None:
    reply = call(lambda_stack, event("GET", "/.well-known/oauth-authorization-server"))
    assert reply["statusCode"] == 200 and reply["isBase64Encoded"] is False
    assert json.loads(reply["body"])["code_challenge_methods_supported"] == ["S256"]


def test_adapter_passes_the_authorize_query_through_untouched(lambda_stack: Stack) -> None:
    query = "client_id=abc&state=a%20b%2Bc&code_challenge_method=S256&scope=openid+mcp%3Aservice"
    reply = call(lambda_stack, event("GET", "/authorize", query))
    assert reply["statusCode"] == 302
    assert reply["headers"]["location"] == f"{lambda_stack.config.cognito_domain}/oauth2/authorize?{query}"


@pytest.mark.parametrize("base64_body", [False, True])
def test_adapter_decodes_a_token_request_body(lambda_stack: Stack, base64_body: bool) -> None:
    form = (f"grant_type=client_credentials&resource={lambda_stack.config.resource_uri}&scope=mcp%3Aservice").encode()
    basic = base64.b64encode(f"{SERVICE_CLIENT_ID}:{SERVICE_CLIENT_SECRET}".encode()).decode()
    reply = call(lambda_stack, event("POST", "/token", body=form, base64_body=base64_body,
                                     headers={"Authorization": f"Basic {basic}"}))
    assert reply["statusCode"] == 200 and json.loads(reply["body"])["token_type"] == "Bearer"


def test_adapter_lowercases_headers_like_api_gateway_v2_does(lambda_stack: Stack) -> None:
    scope, _ = lambda_handler.build_scope(event("GET", "/jwks", headers={"X-Mixed-Case": "1"}))
    assert (b"x-mixed-case", b"1") in scope["headers"] and scope["scheme"] == "https"


def test_unknown_route_is_a_404_through_the_adapter(lambda_stack: Stack) -> None:
    assert call(lambda_stack, event("GET", "/nope"))["statusCode"] == 404


def test_handler_end_to_end_with_a_kms_key(kms: Any, key_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    lambda_handler.app_from_env.cache_clear()
    digest = facade_as.secret_digest(SERVICE_CLIENT_SECRET)
    for name, value in {"HW_FACADE_ISSUER": ENV["HW_FACADE_ISSUER"], "HW_MCP_RESOURCE_URI": ENV["HW_MCP_RESOURCE_URI"],
                        "HW_COGNITO_DOMAIN": ENV["HW_COGNITO_DOMAIN"], "HW_FACADE_SIGNER_KEY_ID": key_id,
                        "HW_FACADE_SERVICE_CLIENTS": json.dumps({SERVICE_CLIENT_ID: digest})}.items():
        monkeypatch.setenv(name, value)
    try:
        keys = lambda_handler.handler(event("GET", "/jwks"), None)
        assert json.loads(keys["body"])["keys"][0]["alg"] == "ES256"
        basic = base64.b64encode(f"{SERVICE_CLIENT_ID}:{SERVICE_CLIENT_SECRET}".encode()).decode()
        form = f"grant_type=client_credentials&resource={ENV['HW_MCP_RESOURCE_URI']}&scope=mcp%3Aservice".encode()
        token = lambda_handler.handler(event("POST", "/token", body=form, headers={"Authorization": f"Basic {basic}"}),
                                       None)
        assert token["statusCode"] == 200
        claims = jwt.decode(json.loads(token["body"])["access_token"], options={"verify_signature": False})
        assert claims["iss"] == ENV["HW_FACADE_ISSUER"] and claims["aud"] == ENV["HW_MCP_RESOURCE_URI"]
    finally:
        lambda_handler.app_from_env.cache_clear()


# ── container entry point and configuration ─────────────────────────────────────────────────────────────────


def test_the_container_factory_builds_the_mcp_app_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    app = asgi.create()
    assert [r.path for r in app.routes] == ["/mcp", "/.well-known/oauth-protected-resource"]


def test_extra_allowed_hosts_extend_but_never_replace_the_pinned_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    base = McpConfig.from_env(ENV)
    extended = McpConfig.from_env({**ENV, "HW_EXTRA_ALLOWED_HOSTS": "internal-alb.example, other.example "})
    assert extended.allowed_hosts == [base.host, "internal-alb.example", "other.example", "localhost:*", "127.0.0.1:*"]
    assert base.allowed_hosts == [base.host, "localhost:*", "127.0.0.1:*"]


def test_the_alb_host_is_accepted_and_a_stranger_is_not() -> None:
    stack = Stack()
    stack.config = McpConfig.from_env({**ENV, "HW_EXTRA_ALLOWED_HOSTS": "internal-alb-1.us-east-1.elb.amazonaws.com"})
    stack.validator.config = stack.config
    token = stack.user_token()
    with stack.mcp_client() as client:
        ok = client.post("/mcp", json=rpc("ping"), headers={**mcp_headers(token),
                                                              "Host": "internal-alb-1.us-east-1.elb.amazonaws.com"})
        bad = client.post("/mcp", json=rpc("ping"), headers={**mcp_headers(token), "Host": "evil.example"})
    assert ok.status_code == 200 and bad.status_code in (400, 403, 421)


def test_dockerfiles_point_at_entry_points_that_exist() -> None:
    from pathlib import Path

    infra = Path(__file__).resolve().parents[2] / "infra"
    assert "hearthward.authz.lambda_handler.handler" in (infra / "Dockerfile.facade").read_text(encoding="utf-8")
    assert "hearthward.mcp.asgi:create" in (infra / "Dockerfile.mcp").read_text(encoding="utf-8")
    assert callable(lambda_handler.handler) and callable(asgi.create)
