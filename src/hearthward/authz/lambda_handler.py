"""AWS Lambda entry point for `facade_as`: an API Gateway HTTP API (payload v2) event in, the Starlette app inside.

Configuration comes from the environment the CDK stack sets: `HW_FACADE_ISSUER`, `HW_MCP_RESOURCE_URI`,
`HW_COGNITO_DOMAIN`, `HW_FACADE_SIGNER_KEY_ID` and `HW_FACADE_SERVICE_CLIENTS` (JSON: client id to the SHA-256
hex digest of its secret; never the secret itself).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import Mapping
from functools import cache
from typing import Any

from starlette.types import ASGIApp, Message, Scope

from hearthward.authz import facade_as
from hearthward.authz.kms_signer import KmsEs256Signer


def build_scope(event: Mapping[str, Any]) -> tuple[Scope, bytes]:
    http = event["requestContext"]["http"]
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if event.get("cookies"):
        headers["cookie"] = "; ".join(event["cookies"])
    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode("utf-8")
    path = event["rawPath"]
    scope: Scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": http["method"],
        "scheme": "https", "path": path, "raw_path": path.encode("ascii", "ignore"),
        "query_string": (event.get("rawQueryString") or "").encode("latin-1"),
        "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "client": (http.get("sourceIp", ""), 0), "server": (headers.get("host", ""), 443)}
    return scope, raw


async def call_asgi(app: ASGIApp, event: Mapping[str, Any]) -> dict[str, Any]:
    scope, body = build_scope(event)
    sent = False
    status, headers, chunks = 500, {}, []

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        nonlocal status, headers
        if message["type"] == "http.response.start":
            status = message["status"]
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    payload = b"".join(chunks)
    try:
        return {"statusCode": status, "headers": headers, "body": payload.decode("utf-8"), "isBase64Encoded": False}
    except UnicodeDecodeError:
        return {"statusCode": status, "headers": headers, "body": base64.b64encode(payload).decode("ascii"),
                "isBase64Encoded": True}


@cache
def app_from_env() -> ASGIApp:
    import boto3

    env = os.environ
    config = facade_as.FacadeConfig(
        issuer=env["HW_FACADE_ISSUER"], resource_uri=env["HW_MCP_RESOURCE_URI"],
        cognito_domain=env["HW_COGNITO_DOMAIN"], service_clients=json.loads(env["HW_FACADE_SERVICE_CLIENTS"]))
    signer = KmsEs256Signer(boto3.client("kms"), env["HW_FACADE_SIGNER_KEY_ID"])
    return facade_as.create_app(config, signer)


def handler(event: Mapping[str, Any], context: Any) -> dict[str, Any]:
    return asyncio.run(call_asgi(app_from_env(), event))
