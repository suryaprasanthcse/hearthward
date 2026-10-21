"""Gate G3: `tools/pipeline.py:g3_probe` run against the local apps instead of deployed endpoints.

`--probe` calls the deployed API with `urllib`. Here the pipeline's own `_http` is routed to in-process clients for
the same two hosts, so the real probe code decides, with no network. If these pass, the same assertions hold for the
deployed pair as long as the deployment serves the same apps.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import pipeline
import pytest

from tests.g2_support import ENV, Stack

pytestmark = pytest.mark.gate_g3


class Router:
    """Stands in for `pipeline._http`: sends each request to the client that owns the URL's host."""

    def __init__(self, mcp_client: Any, facade_client: Any, tamper: Any = None) -> None:
        self.by_host = {urlsplit(ENV["HW_MCP_RESOURCE_URI"]).netloc: mcp_client,
                        urlsplit(ENV["HW_FACADE_ISSUER"]).netloc: facade_client}
        self.requests: list[tuple[str, str]] = []
        self.tamper = tamper

    def __call__(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
        parts = urlsplit(url)
        client = self.by_host[parts.netloc]
        self.requests.append(("POST" if data else "GET", parts.path))
        if data:
            response = client.post(parts.path, content=data, headers=headers or {})
        else:
            response = client.get(parts.path, headers=headers or {})
        status, response_headers, body = response.status_code, dict(response.headers), response.content
        if self.tamper:
            status, response_headers, body = self.tamper(parts.path, status, response_headers, body)
        return status, response_headers, body


@pytest.fixture
def probe_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


def run_probe(monkeypatch: pytest.MonkeyPatch, tamper: Any = None) -> tuple[pipeline.Outcome, Router]:
    stack = Stack()
    with stack.mcp_client() as mcp, stack.facade_client() as facade:
        router = Router(mcp, facade, tamper)
        monkeypatch.setattr(pipeline, "_http", router)
        return pipeline.g3_probe(pipeline.Context(probe=True)), router


def test_the_real_probe_passes_against_the_local_apps(probe_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    outcome, router = run_probe(monkeypatch)
    assert outcome.status == pipeline.PASS, outcome.detail
    assert router.requests == [("GET", "/.well-known/oauth-protected-resource"),
                               ("GET", "/.well-known/oauth-authorization-server"), ("POST", "/mcp")]


def test_without_probe_the_check_stays_pending(probe_env: None) -> None:
    assert pipeline.g3_probe(pipeline.Context(probe=False)).status == pipeline.PENDING


def test_the_unauthenticated_call_gets_401_without_www_authenticate_or_redirect(
        probe_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[int, dict[str, str]]] = []

    def watch(path: str, status: int, headers: dict[str, str], body: bytes) -> Any:
        if path == "/mcp":
            seen.append((status, headers))
        return status, headers, body

    run_probe(monkeypatch, watch)
    ((status, headers),) = seen
    assert status == 401 and not any(k.lower() == "www-authenticate" for k in headers)
    assert "location" not in {k.lower() for k in headers}


def test_metadata_bodies_are_the_documents_the_probe_compares(probe_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: dict[str, bytes] = {}

    def watch(path: str, status: int, headers: dict[str, str], body: bytes) -> Any:
        bodies[path] = body
        return status, headers, body

    run_probe(monkeypatch, watch)
    import json

    resource = json.loads(bodies["/.well-known/oauth-protected-resource"])
    facade = json.loads(bodies["/.well-known/oauth-authorization-server"])
    assert resource["resource"] == ENV["HW_MCP_RESOURCE_URI"]
    assert resource["authorization_servers"][0] == ENV["HW_FACADE_ISSUER"] == facade["issuer"]
    assert "S256" in facade["code_challenge_methods_supported"]
    assert {"authorization_code", "refresh_token"} <= set(facade["grant_types_supported"])


# The probe must also be able to fail: each tampering below breaks one assertion it makes.


def test_probe_fails_if_the_401_carries_www_authenticate(probe_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def add_header(path: str, status: int, headers: dict[str, str], body: bytes) -> Any:
        return (status, {**headers, "WWW-Authenticate": 'Bearer resource_metadata="x"'}, body) if path == "/mcp" \
            else (status, headers, body)

    outcome, _ = run_probe(monkeypatch, add_header)
    assert outcome.status == pipeline.FAIL and "WWW-Authenticate" in outcome.detail


def test_probe_fails_if_mcp_redirects(probe_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    outcome, _ = run_probe(monkeypatch, lambda path, s, h, b: (307, h, b) if path == "/mcp" else (s, h, b))
    assert outcome.status == pipeline.FAIL and "redirects" in outcome.detail


def test_probe_fails_without_s256(probe_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    def drop_s256(path: str, status: int, headers: dict[str, str], body: bytes) -> Any:
        if path == "/.well-known/oauth-authorization-server":
            document = json.loads(body)
            document["code_challenge_methods_supported"] = ["plain"]
            body = json.dumps(document).encode()
        return status, headers, body

    outcome, _ = run_probe(monkeypatch, drop_s256)
    assert outcome.status == pipeline.FAIL and "S256" in outcome.detail


def test_probe_fails_if_the_authorization_server_is_not_the_facade(probe_env: None,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    def repoint(path: str, status: int, headers: dict[str, str], body: bytes) -> Any:
        if path == "/.well-known/oauth-protected-resource":
            document = json.loads(body)
            document["authorization_servers"] = ["https://elsewhere.example"]
            body = json.dumps(document).encode()
        return status, headers, body

    outcome, _ = run_probe(monkeypatch, repoint)
    assert outcome.status == pipeline.FAIL and "authorization_servers[0]" in outcome.detail


def test_the_g3_environment_values_pass_the_pipeline_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {"AWS_REGION": "us-east-1", "HW_LEDGER_TABLE": "HearthwardEventLedger",
              "HW_ATTEST_KEY_ARN": "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012"}
    assert set(values) == set(pipeline.GATE_ENV["G3"])
    for name, value in values.items():
        monkeypatch.setenv(name, value)
        assert pipeline.ENV_RULES[name](value) is None
    outcome = pipeline._env("G3")(pipeline.Context(probe=False))
    assert outcome.status == pipeline.PASS


def test_the_env_example_names_every_g3_variable() -> None:
    from pathlib import Path

    text = (Path(pipeline.ROOT) / ".env.example").read_text(encoding="utf-8")
    for name in pipeline.GATE_ENV["G3"]:
        assert f"\n{name}=" in text
