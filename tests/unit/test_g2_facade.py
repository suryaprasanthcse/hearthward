"""Gate G2: the RFC 8414 facade. S256 advertised, /authorize redirects untouched, client_credentials issues ES256."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlsplit

import jwt
import pytest
from starlette.testclient import TestClient

from hearthward.authz import facade_as
from tests.g2_support import SERVICE_CLIENT_ID, SERVICE_CLIENT_SECRET, Stack, mcp_headers, rpc

pytestmark = pytest.mark.gate_g2

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def stack() -> Stack:
    return Stack()


def basic(client_id: str = SERVICE_CLIENT_ID, secret: str = SERVICE_CLIENT_SECRET) -> dict[str, str]:
    return {**FORM, "Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()}


def service_form(stack: Stack, **overrides: str | None) -> dict[str, str]:
    form: dict[str, str | None] = {"grant_type": "client_credentials", "resource": stack.config.resource_uri,
                                   "scope": "mcp:service"}
    form.update(overrides)
    return {k: v for k, v in form.items() if v is not None}


def test_metadata_advertises_s256_and_the_grants(stack: Stack) -> None:
    with stack.facade_client() as client:
        document = client.get("/.well-known/oauth-authorization-server").json()
    issuer = stack.config.facade_issuer
    assert document["issuer"] == issuer and not issuer.endswith("/")
    assert document["code_challenge_methods_supported"] == ["S256"]
    assert document["grant_types_supported"] == ["authorization_code", "refresh_token", "client_credentials"]
    assert document["response_types_supported"] == ["code"]
    assert document["authorization_endpoint"] == f"{issuer}/authorize"
    assert document["token_endpoint"] == f"{issuer}/token"
    assert document["jwks_uri"] == f"{issuer}/jwks"
    assert document["token_endpoint_auth_methods_supported"] == ["client_secret_basic", "client_secret_post"]
    assert "mcp:service" in document["scopes_supported"]


def test_openid_configuration_mirrors_the_metadata(stack: Stack) -> None:
    with stack.facade_client() as client:
        assert client.get("/.well-known/openid-configuration").json() == \
            client.get("/.well-known/oauth-authorization-server").json()


def test_metadata_has_exactly_the_blueprint_fields(stack: Stack) -> None:
    assert sorted(facade_as.authorization_server_metadata(stack.facade_config)) == sorted([
        "issuer", "authorization_endpoint", "token_endpoint", "jwks_uri", "response_types_supported",
        "grant_types_supported", "code_challenge_methods_supported", "token_endpoint_auth_methods_supported",
        "scopes_supported"])


def test_jwks_serves_only_the_public_key(stack: Stack) -> None:
    with stack.facade_client() as client:
        keys = client.get("/jwks").json()["keys"]
    assert len(keys) == 1 and keys[0]["kty"] == "EC" and keys[0]["crv"] == "P-256"
    assert keys[0]["kid"] == stack.signer.kid and "d" not in keys[0]


def test_authorize_redirects_to_cognito_with_the_query_unchanged(stack: Stack) -> None:
    query = ("client_id=synthalexaclient01&redirect_uri=https%3A%2F%2Fpitangui.amazon.com%2Fapi%2Fskill%2Flink%2FM1"
             "&response_type=code&state=a%20b%2Bc&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
             "&code_challenge_method=S256&resource=https%3A%2F%2Fexample.test%2Fmcp&scope=openid+mcp%3Aservice&scope=x")
    with stack.facade_client() as client:
        response = client.get(f"/authorize?{query}")
    assert response.status_code == 302
    location = response.headers["location"]
    assert location == f"{stack.config.cognito_domain}/oauth2/authorize?{query}"
    assert urlsplit(location).netloc == urlsplit(stack.config.cognito_domain).netloc


def test_authorize_cannot_be_pointed_elsewhere_by_the_caller(stack: Stack) -> None:
    with stack.facade_client() as client:
        response = client.get("/authorize?redirect_uri=https://evil.example&host=evil.example")
    assert response.headers["location"].startswith(f"{stack.config.cognito_domain}/oauth2/authorize?")


def test_authorization_code_and_refresh_are_proxied_byte_for_byte(stack: Stack) -> None:
    headers = basic("synthalexaclient01", "irrelevant")
    bodies = (b"grant_type=authorization_code&code=abc&redirect_uri=https%3A%2F%2Fx.test%2Fcb&code_verifier=v&resource=r",
              b"grant_type=refresh_token&refresh_token=rt&resource=r")
    for body in bodies:
        stack.upstream_calls.clear()
        with stack.facade_client() as client:
            response = client.post("/token", content=body, headers=headers)
        assert response.status_code == 200 and response.json()["access_token"] == "cognito-issued"
        ((url, sent, forwarded),) = stack.upstream_calls
        assert url == f"{stack.config.cognito_domain}/oauth2/token"
        assert sent == body and forwarded["Authorization"] == headers["Authorization"]
        assert response.headers["cache-control"] == "no-store"


def test_upstream_outage_is_503_in_rfc_6749_form(stack: Stack) -> None:
    def down(*args: Any) -> Any:
        raise OSError("unreachable")

    app = facade_as.create_app(stack.facade_config, stack.signer, down)
    with TestClient(app, base_url=stack.config.facade_issuer) as client:
        response = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": "r"})
    assert response.status_code == 503 and response.json()["error"] == "temporarily_unavailable"


def test_client_credentials_issues_a_verifiable_es256_service_token(stack: Stack) -> None:
    with stack.facade_client() as client:
        response = client.post("/token", data=service_form(stack), headers=basic())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store" and response.headers["pragma"] == "no-cache"
    body = response.json()
    assert body["token_type"] == "Bearer" and body["scope"] == "mcp:service" and body["expires_in"] == 3600
    assert "refresh_token" not in body
    header = jwt.get_unverified_header(body["access_token"])
    assert header["alg"] == "ES256" and header["kid"] == stack.signer.kid
    claims = jwt.decode(body["access_token"], jwt.PyJWK(stack.signer.public_jwk()).key, algorithms=["ES256"],
                        audience=stack.config.resource_uri, issuer=stack.config.facade_issuer)
    assert claims["scope"] == "mcp:service" and claims["sub"] == SERVICE_CLIENT_ID and claims["jti"]
    assert 0 < claims["exp"] - claims["iat"] <= 3600


def test_issued_token_works_at_mcp_for_discovery_only(stack: Stack) -> None:
    with stack.facade_client() as client:
        token = client.post("/token", data=service_form(stack), headers=basic()).json()["access_token"]
    with stack.mcp_client() as mcp:
        assert mcp.post("/mcp", json=rpc("tools/list"), headers=mcp_headers(token)).status_code == 200
        call = rpc("tools/call", {"name": "get_door_activity", "arguments": {"window": "today"}})
        assert mcp.post("/mcp", json=call, headers=mcp_headers(token)).status_code == 403


def test_every_issued_token_has_a_distinct_jti(stack: Stack) -> None:
    with stack.facade_client() as client:
        tokens = [client.post("/token", data=service_form(stack), headers=basic()).json()["access_token"]
                  for _ in range(3)]
    assert len({jwt.decode(t, options={"verify_signature": False})["jti"] for t in tokens}) == 3


def test_client_secret_post_is_accepted(stack: Stack) -> None:
    form = service_form(stack, client_id=SERVICE_CLIENT_ID, client_secret=SERVICE_CLIENT_SECRET)
    with stack.facade_client() as client:
        assert client.post("/token", data=form).status_code == 200


@pytest.mark.parametrize("headers", [basic(secret="wrong"), basic("unknown-client", SERVICE_CLIENT_SECRET),
                                     {**FORM, "Authorization": "Basic !!!notbase64"}])
def test_bad_client_authentication_is_invalid_client(stack: Stack, headers: dict[str, str]) -> None:
    with stack.facade_client() as client:
        response = client.post("/token", data=service_form(stack), headers=headers)
    assert response.status_code == 401 and response.json()["error"] == "invalid_client"


def test_missing_client_authentication_is_invalid_client_400(stack: Stack) -> None:
    with stack.facade_client() as client:
        response = client.post("/token", data=service_form(stack))
    assert response.status_code == 400 and response.json()["error"] == "invalid_client"
    assert "www-authenticate" not in {k.lower() for k in response.headers}


@pytest.mark.parametrize("resource", [None, "https://elsewhere.example/mcp",
                                      "https://mcp0synth0.execute-api.us-east-1.amazonaws.com/mcp/"])
def test_wrong_resource_is_invalid_target(stack: Stack, resource: str | None) -> None:
    with stack.facade_client() as client:
        response = client.post("/token", data=service_form(stack, resource=resource), headers=basic())
    assert response.status_code == 400 and response.json()["error"] == "invalid_target"


def test_a_repeated_resource_must_all_match(stack: Stack) -> None:
    body = ("grant_type=client_credentials&scope=mcp%3Aservice"
            f"&resource={stack.config.resource_uri}&resource=https%3A%2F%2Fevil.example")
    with stack.facade_client() as client:
        response = client.post("/token", content=body, headers=basic())
    assert response.json()["error"] == "invalid_target"


@pytest.mark.parametrize("scope", [None, "", "openid", "mcp:service openid", "mcp:admin"])
def test_wrong_scope_is_invalid_scope(stack: Stack, scope: str | None) -> None:
    with stack.facade_client() as client:
        response = client.post("/token", data=service_form(stack, scope=scope), headers=basic())
    assert response.status_code == 400 and response.json()["error"] == "invalid_scope"


@pytest.mark.parametrize("form, error", [({}, "invalid_request"), ({"grant_type": "password"}, "unsupported_grant_type"),
                                         ({"grant_type": "implicit"}, "unsupported_grant_type")])
def test_grant_type_errors(stack: Stack, form: dict[str, str], error: str) -> None:
    with stack.facade_client() as client:
        response = client.post("/token", data=form, headers=basic())
    assert response.status_code == 400 and response.json()["error"] == error


def test_json_body_is_rejected(stack: Stack) -> None:
    with stack.facade_client() as client:
        response = client.post("/token", json=service_form(stack), headers=basic())
    assert response.status_code == 400 and response.json()["error"] == "invalid_request"


def test_errors_follow_rfc_6749_section_5_2(stack: Stack) -> None:
    with stack.facade_client() as client:
        response = client.post("/token", data=service_form(stack, scope="nope"), headers=basic())
    assert set(response.json()) == {"error", "error_description"}
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"


def test_token_endpoint_takes_post_only(stack: Stack) -> None:
    with stack.facade_client() as client:
        assert client.get("/token").status_code == 405


def test_token_times_follow_the_injected_clock(stack: Stack) -> None:
    now = 1_900_000_000
    app = facade_as.create_app(stack.facade_config, stack.signer, clock=lambda: now)
    with TestClient(app, base_url=stack.config.facade_issuer) as client:
        token = client.post("/token", data=service_form(stack), headers=basic()).json()["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iat"] == now and claims["exp"] == now + 3600


def test_oversized_token_request_is_rejected(stack: Stack) -> None:
    with stack.facade_client() as client:
        response = client.post("/token", content=b"grant_type=client_credentials&x=" + b"a" * 20_000, headers=basic())
    assert response.status_code == 413 and response.json()["error"] == "invalid_request"
