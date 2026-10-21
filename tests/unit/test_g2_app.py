"""Gate G2: the Streamable HTTP app. P-1 route and statelessness, P-2 protocol versions, P-4 results, P-6 hosts."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from hearthward.mcp.config import ALLOWED_PROTOCOL_VERSIONS
from hearthward.mcp.schemas import definitions_by_name, tool_definitions
from tests.g2_support import Stack, initialize_params, mcp_headers, rpc

pytestmark = pytest.mark.gate_g2


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


@pytest.fixture(scope="module")
def token(stack: Stack) -> str:
    return stack.user_token()


@pytest.mark.parametrize("version", ALLOWED_PROTOCOL_VERSIONS)
def test_initialize_negotiates_each_accepted_version(stack: Stack, token: str, version: str) -> None:
    with stack.mcp_client() as client:
        response = client.post("/mcp", json=rpc("initialize", initialize_params(version)),
                               headers=mcp_headers(token, None))
    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == version


@pytest.mark.parametrize("requested", ["2024-11-05", "2026-07-28", "1999-01-01"])
def test_initialize_with_an_unaccepted_version_falls_back_to_the_newest_accepted(
        stack: Stack, token: str, requested: str) -> None:
    with stack.mcp_client() as client:
        response = client.post("/mcp", json=rpc("initialize", initialize_params(requested)),
                               headers=mcp_headers(token, None))
    assert response.json()["result"]["protocolVersion"] == "2025-11-25"


def test_missing_protocol_header_counts_as_2025_03_26(stack: Stack, token: str) -> None:
    with stack.mcp_client() as client:
        response = client.post("/mcp", json=rpc("tools/list"), headers=mcp_headers(token, None))
    assert response.status_code == 200
    assert len(response.json()["result"]["tools"]) == 11


@pytest.mark.parametrize("version", ALLOWED_PROTOCOL_VERSIONS)
def test_requests_under_each_accepted_header_work(stack: Stack, token: str, version: str) -> None:
    with stack.mcp_client() as client:
        response = client.post("/mcp", json=rpc("tools/list"), headers=mcp_headers(token, version))
    assert response.status_code == 200


@pytest.mark.parametrize("version", ["2024-11-05", "2026-07-28", "latest", ""])
def test_unaccepted_protocol_header_is_400(stack: Stack, token: str, version: str) -> None:
    headers = {**mcp_headers(token, None), "MCP-Protocol-Version": version}
    with stack.mcp_client() as client:
        response = client.post("/mcp", json=rpc("tools/list"), headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["data"]["supported"] == list(ALLOWED_PROTOCOL_VERSIONS)


def test_the_server_is_stateless_and_answers_plain_json(stack: Stack, token: str) -> None:
    with stack.mcp_client() as client:
        response = client.post("/mcp", json=rpc("initialize", initialize_params("2025-03-26")),
                               headers=mcp_headers(token))
        assert response.headers["content-type"].startswith("application/json")
        assert "mcp-session-id" not in {k.lower() for k in response.headers}
        again = client.post("/mcp", json=rpc("tools/list"), headers=mcp_headers(token))
    assert again.status_code == 200  # no session was needed


def test_route_is_exactly_mcp_with_no_redirect(stack: Stack, token: str) -> None:
    with stack.mcp_client() as client:
        slash = client.post("/mcp/", json=rpc("ping"), headers=mcp_headers(token), follow_redirects=False)
    assert slash.status_code == 404 and "location" not in {k.lower() for k in slash.headers}


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_only_post_is_served(stack: Stack, token: str, method: str) -> None:
    with stack.mcp_client() as client:
        response = client.request(method, "/mcp", headers=mcp_headers(token))
        anonymous = client.request(method, "/mcp", headers=mcp_headers(None))
    assert response.status_code == 405 and response.headers["allow"] == "POST"
    assert anonymous.status_code == 401  # authentication comes first


def test_dns_rebinding_protection_rejects_foreign_hosts(stack: Stack, token: str) -> None:
    with stack.mcp_client() as client:
        headers = {**mcp_headers(token), "Host": "evil.example"}
        response = client.post("/mcp", json=rpc("ping"), headers=headers)
        origin = client.post("/mcp", json=rpc("ping"), headers={**mcp_headers(token), "Origin": "https://evil.example"})
    assert response.status_code in (400, 403, 421)
    assert origin.status_code in (400, 403)


def test_allowed_hosts_name_the_gateway_host_and_localhost(stack: Stack) -> None:
    assert stack.config.allowed_hosts == [stack.config.host, "localhost:*", "127.0.0.1:*"]


def test_localhost_with_a_port_is_allowed(stack: Stack, token: str) -> None:
    with stack.mcp_client() as client:
        response = client.post("/mcp", json=rpc("ping"), headers={**mcp_headers(token), "Host": "localhost:8080"})
    assert response.status_code == 200


def test_tools_list_equals_the_blueprint(stack: Stack, token: str) -> None:
    with stack.mcp_client() as client:
        tools = client.post("/mcp", json=rpc("tools/list"), headers=mcp_headers(token)).json()["result"]["tools"]
    assert tools == tool_definitions()


def call(stack: Stack, token: str, name: str, arguments: dict[str, Any]) -> Any:
    with stack.mcp_client() as client:
        response = client.post("/mcp", json=rpc("tools/call", {"name": name, "arguments": arguments}),
                               headers=mcp_headers(token))
    assert response.status_code == 200
    return response.json()["result"]


def test_structured_content_and_text_block_carry_the_same_json(stack: Stack, token: str) -> None:
    """P-4."""
    result = call(stack, token, "get_door_activity", {"window": "today"})
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    Draft202012Validator(definitions_by_name()["get_door_activity"]["outputSchema"]).validate(
        result["structuredContent"])


def test_invalid_arguments_are_a_tool_error_not_a_crash(stack: Stack, token: str) -> None:
    for name, arguments in (("get_door_activity", {"window": "yesterday"}), ("get_door_activity", {}),
                            ("get_door_activity", {"window": "today", "extra": 1}),
                            ("raise_concern", {"concern": "x" * 281, "about": "door"})):
        result = call(stack, token, name, arguments)
        assert result["isError"] is True and "invalid arguments" in result["content"][0]["text"]


def test_unknown_tool_is_a_tool_error(stack: Stack, token: str) -> None:
    result = call(stack, token, "format_disk", {})
    assert result["isError"] is True


def test_protected_resource_metadata_is_public_and_exact(stack: Stack) -> None:
    with stack.mcp_client() as client:
        response = client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    assert response.json() == {
        "resource": stack.config.resource_uri,
        "authorization_servers": [stack.config.facade_issuer],
        "scopes_supported": ["openid", f"{stack.config.resource_uri}/tools", "mcp:service"]}
    assert not stack.config.resource_uri.endswith("/")


def test_no_response_ever_carries_www_authenticate(stack: Stack, token: str) -> None:
    with stack.mcp_client() as client:
        responses = [client.post("/mcp", json=rpc("ping"), headers=mcp_headers(t)) for t in (None, "bad", token)]
        responses.append(client.get("/.well-known/oauth-protected-resource"))
    for response in responses:
        assert "www-authenticate" not in {k.lower() for k in response.headers}
