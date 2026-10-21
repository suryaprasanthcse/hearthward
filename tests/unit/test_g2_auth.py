"""Gate G2: bearer validation at `/mcp`. P-5: 401 with a JSON body and never a `WWW-Authenticate` header."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.g2_support import Stack, initialize_params, mcp_headers, rpc

pytestmark = pytest.mark.gate_g2


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


def post(stack: Stack, token: str | None, message: dict[str, Any] | None = None, **kwargs: Any):
    with stack.mcp_client() as client:
        return client.post("/mcp", json=message or rpc("ping"), headers=mcp_headers(token), **kwargs)


def assert_unauthorized(response: Any) -> None:
    assert response.status_code == 401
    assert "www-authenticate" not in {k.lower() for k in response.headers}
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"] == "invalid_token"


def test_missing_token_is_401_without_www_authenticate(stack: Stack) -> None:
    assert_unauthorized(post(stack, None))


@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "Bearer ", "bearer-only", "Token abc"])
def test_malformed_authorization_header_is_401(stack: Stack, header: str) -> None:
    with stack.mcp_client() as client:
        headers = {**mcp_headers(None), "Authorization": header}
        assert_unauthorized(client.post("/mcp", json=rpc("ping"), headers=headers))


def test_garbage_token_is_401(stack: Stack) -> None:
    assert_unauthorized(post(stack, "not.a.jwt"))
    assert_unauthorized(post(stack, "x" * 9000))


def test_valid_user_token_is_accepted(stack: Stack) -> None:
    response = post(stack, stack.user_token())
    assert response.status_code == 200 and response.json()["result"] == {}


def test_expired_user_token_is_401(stack: Stack) -> None:
    now = int(time.time())
    assert_unauthorized(post(stack, stack.user_token(iat=now - 7200, exp=now - 3600)))


def test_sixty_second_leeway_on_exp(stack: Stack) -> None:
    now = int(time.time())
    assert post(stack, stack.user_token(exp=now - 30)).status_code == 200
    assert_unauthorized(post(stack, stack.user_token(exp=now - 120)))


@pytest.mark.parametrize("overrides", [
    {"aud": "https://elsewhere.example/mcp"},
    {"aud": "https://mcp0synth0.execute-api.us-east-1.amazonaws.com/mcp/"},  # trailing slash differs
    {"aud": None},
    {"token_use": "id"},
    {"token_use": None},
    {"client_id": "someotherclient1"},
    {"client_id": None},
    {"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHER0001"},
    {"exp": None},
    {"sub": None},
], ids=lambda o: ",".join(f"{k}={v}" for k, v in o.items()))
def test_user_token_claim_failures_are_401(stack: Stack, overrides: dict[str, Any]) -> None:
    assert_unauthorized(post(stack, stack.user_token(**overrides)))


def test_user_token_signed_by_another_key_is_401(stack: Stack) -> None:
    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = {"iss": stack.config.cognito_issuer, "sub": "u", "aud": stack.config.resource_uri,
              "client_id": stack.config.alexa_client_id, "token_use": "access", "exp": now + 600}
    token = jwt.encode(claims, rogue, algorithm="RS256", headers={"kid": "cognito-synth-key-1"})
    assert_unauthorized(post(stack, token))


def test_unknown_kid_is_401(stack: Stack) -> None:
    token = jwt.encode({"iss": stack.config.cognito_issuer}, "k" * 32, algorithm="HS256", headers={"kid": "nope"})
    assert_unauthorized(post(stack, token))


def test_alg_none_and_hs256_are_rejected(stack: Stack) -> None:
    def b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    now = int(time.time())
    claims = {"iss": stack.config.cognito_issuer, "sub": "u", "aud": stack.config.resource_uri,
              "client_id": stack.config.alexa_client_id, "token_use": "access", "exp": now + 600}
    unsigned = f"{b64({'alg': 'none', 'kid': 'cognito-synth-key-1'})}.{b64(claims)}."
    assert_unauthorized(post(stack, unsigned))
    hs = jwt.encode(claims, "k" * 32, algorithm="HS256", headers={"kid": "cognito-synth-key-1"})
    assert_unauthorized(post(stack, hs))


def test_unrecognized_issuer_is_401(stack: Stack) -> None:
    assert_unauthorized(post(stack, stack.user_token(iss="https://evil.example")))


def test_service_token_may_discover(stack: Stack) -> None:
    token = stack.service_token()
    with stack.mcp_client() as client:
        for message in (rpc("initialize", initialize_params("2025-03-26")), rpc("tools/list"), rpc("ping")):
            response = client.post("/mcp", json=message, headers=mcp_headers(token))
            assert response.status_code == 200, message["method"]
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert client.post("/mcp", json=notification, headers=mcp_headers(token)).status_code == 202


def test_service_token_cannot_call_tools(stack: Stack) -> None:
    call = rpc("tools/call", {"name": "get_door_activity", "arguments": {"window": "today"}})
    response = post(stack, stack.service_token(), call)
    assert response.status_code == 403
    assert "www-authenticate" not in {k.lower() for k in response.headers}
    assert response.json()["error"] == "insufficient_scope"


def test_service_token_cannot_smuggle_a_call_in_a_batch(stack: Stack) -> None:
    batch = [rpc("ping"), rpc("tools/call", {"name": "raise_concern", "arguments": {"concern": "x", "about": "door"}}, 2)]
    assert post(stack, stack.service_token(), batch).status_code == 403


def test_service_token_without_the_service_scope_is_403(stack: Stack) -> None:
    response = post(stack, stack.service_token(scope="openid"))
    assert response.status_code == 403 and "www-authenticate" not in {k.lower() for k in response.headers}


@pytest.mark.parametrize("overrides", [
    {"aud": "https://elsewhere.example/mcp"}, {"iss": "https://evil.example"}, {"jti": None},
    {"exp": int(time.time()) - 3600},
])
def test_service_token_claim_failures_are_401(stack: Stack, overrides: dict[str, Any]) -> None:
    assert_unauthorized(post(stack, stack.service_token(**overrides)))


def test_a_user_token_cannot_pass_as_a_service_token_and_vice_versa(stack: Stack) -> None:
    # A facade-issued token validated as a user token would need Cognito's issuer and RS256: it has neither.
    token = stack.service_token(iss=stack.config.cognito_issuer)
    assert_unauthorized(post(stack, token))


def test_required_user_scope_gives_403() -> None:
    strict = Stack()
    strict.validator.required_user_scopes = (strict.config.user_scope,)
    ok = strict.user_token(scope=f"openid {strict.config.user_scope}")
    assert post(strict, ok).status_code == 200
    denied = post(strict, strict.user_token(scope="openid"))
    assert denied.status_code == 403 and "www-authenticate" not in {k.lower() for k in denied.headers}
