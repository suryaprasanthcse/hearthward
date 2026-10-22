#!/usr/bin/env python3
"""HUMAN-RUN: record the G3 probe requests against the deployed stack.

    uv run python tools/record_probe.py                 # needs HW_MCP_RESOURCE_URI and HW_FACADE_ISSUER (env or .env)
    uv run python tools/record_probe.py --offline-check # validate the output shape with canned responses; no network

Sends the same four requests as `pipeline.g3_probe` (no redirects followed, 10 s timeout) and writes
`web/console/data/probe.recorded.json`: per probe the method, URL, status, selected headers, whether
`WWW-Authenticate` is present, the body, and the UTC time. Claude never runs this (CLAUDE.md rule 3); you do.
Exit 0 only if `POST /mcp` answered 401/403 with no `WWW-Authenticate` and no redirect (P-5).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "console" / "data" / "probe.recorded.json"
TOOL = "tools/record_probe.py"
KEEP = ("content-type", "cache-control", "location", "www-authenticate", "server", "date", "content-length",
        "x-amzn-requestid", "apigw-requestid", "x-amz-apigw-id")
MAX_BODY = 16384
CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "explain_last_alert", "arguments": {}}}
MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-03-26"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def send(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    at = now()
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
            status, raw, got = response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        status, raw, got = exc.code, exc.read(), exc.headers
    except (urllib.error.URLError, OSError) as exc:
        return {"method": method, "url": url, "at_utc": at, "error": str(exc)}
    names = [k.lower() for k in got.keys()]
    text = raw[:MAX_BODY].decode("utf-8", errors="replace")
    try:
        parsed: Any = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
    return {"method": method, "url": url, "at_utc": at, "status": status,
            "headers": {k: got[k] for k in got.keys() if k.lower() in KEEP},
            "header_names": sorted(names), "www_authenticate_present": "www-authenticate" in names,
            "body_json": parsed, "body_text": None if parsed is not None else text,
            "body_truncated": len(raw) > MAX_BODY}


def run(resource: str, issuer: str, transport=send) -> dict[str, Any]:
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(resource))
    plan = [("protected_resource", "GET", origin + "/.well-known/oauth-protected-resource", None, {}, 200),
            ("facade_metadata", "GET", issuer + "/.well-known/oauth-authorization-server", None, {}, 200),
            ("facade_jwks", "GET", issuer + "/jwks", None, {}, 200),
            ("mcp_no_token", "POST", resource, json.dumps(CALL).encode(), MCP_HEADERS, 401)]
    probes = []
    for probe_id, method, url, body, headers, expected in plan:
        probes.append({"id": probe_id, "expected_status": expected, **transport(method, url, body, headers)})
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"v": 1, "tool": TOOL, "recorded_at_utc": now(), "git_commit": commit,
            "resource": resource, "issuer": issuer, "probes": probes,
            "note": "Recorded from the deployed stack by a human-run tool. No credentials are sent."}


def verdict(record: dict[str, Any]) -> list[str]:
    problems = []
    mcp = next(p for p in record["probes"] if p["id"] == "mcp_no_token")
    if "error" in mcp:
        problems.append(f"POST /mcp failed: {mcp['error']}")
    else:
        if mcp["status"] not in (401, 403):
            problems.append(f"POST /mcp returned {mcp['status']}, expected 401 or 403")
        if mcp["www_authenticate_present"]:
            problems.append("POST /mcp carries WWW-Authenticate (P-5)")
    problems += [f"{p['id']}: {p['error']}" for p in record["probes"] if "error" in p and p["id"] != "mcp_no_token"]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record the G3 probes against the deployed stack (human-run).")
    parser.add_argument("--out", type=Path, default=OUT, help=f"output file (default {OUT.relative_to(ROOT)})")
    parser.add_argument("--offline-check", action="store_true", help="use canned responses; no network, no write")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env")

    if args.offline_check:
        def canned(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> dict[str, Any]:
            return {"method": method, "url": url, "at_utc": now(), "status": 401 if method == "POST" else 200,
                    "headers": {}, "header_names": ["content-type"], "www_authenticate_present": False,
                    "body_json": {}, "body_text": None, "body_truncated": False}
        record = run("https://example.invalid/mcp", "https://example.invalid", canned)
        print("offline check:", "ok" if not verdict(record) else verdict(record))
        return 0

    resource, issuer = os.environ.get("HW_MCP_RESOURCE_URI"), os.environ.get("HW_FACADE_ISSUER")
    if not resource or not issuer:
        print("HW_MCP_RESOURCE_URI and HW_FACADE_ISSUER must be set (environment or .env)", file=sys.stderr)
        return 2
    record = run(resource, issuer.rstrip("/"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes((json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))
    for p in record["probes"]:
        print(f"{p['method']:4} {p['url']}  ->  {p.get('status', p.get('error'))}  "
              f"WWW-Authenticate {'PRESENT' if p.get('www_authenticate_present') else 'absent'}")
    print(f"wrote {args.out}")
    problems = verdict(record)
    for problem in problems:
        print(f"FAIL  {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
