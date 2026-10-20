"""Ring webhook ingest (blueprint TB-3, C-5, section 4.1 and 4.4).

`handle` is transport-agnostic: it authenticates the raw body bytes (HMAC-SHA256, `X-Signature: sha256=<hex>`)
BEFORE parsing anything, maps the delivery to a `door.*` envelope exactly as `tools/orrery.py` does, and ledgers it
once. `make_lambda_handler` adapts it to an API Gateway HTTP API (payload v2) event.

Status codes are chosen for Ring's retry behavior: Ring disables an endpoint after repeated 4xx answers, so once a
delivery is authenticated the only non-2xx answers are 400 for a body we cannot interpret at all and 500 for our own
failures (never a 4xx for something we caused). Responses are small fixed JSON documents: they never echo input and
never carry exception text.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from hearthward.contracts.canonical import ProfileError, check_profile, digest, is_unicode, parse_strict
from hearthward.contracts.specs import temporal
from hearthward.ingest.ledger import APPENDED, DUPLICATE, precheck

_SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}")
_META_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{1,9})Z", re.ASCII)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_KINDS = {"motion_detected": "door.motion", "button_press": "door.button"}
MAX_REQUEST_ID = 128


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


def _reply(code: int, **fields: Any) -> Response:
    return Response(code, json.dumps(fields, separators=(",", ":")).encode("utf-8"))


class _Reject(Exception):
    """A delivery that cannot be interpreted (answered 400); carries no text out of the module."""


def parse_ring_time(text: Any) -> int:
    """Epoch milliseconds of Ring's `meta.time` (`YYYY-MM-DDTHH:MM:SS.fffffffffZ`), truncating below one ms.

    Ring documents nine fractional digits; one to nine are accepted (RFC 3339 senders often trim trailing zeros, and
    a 400 for that would cost availability, not security, since the body is already authenticated). The exact
    inverse of `orrery.iso_ns` on whole milliseconds. Raises ValueError on any other shape."""
    if type(text) is not str:
        raise ValueError("meta.time is not a string")
    match = _META_TIME.fullmatch(text)
    if match is None:
        raise ValueError("meta.time is not ISO 8601 with nine fractional digits and a Z suffix")
    *fields, fraction = match.groups()
    year, month, day, hour, minute, second = (int(g) for g in fields)
    moment = datetime(year, month, day, hour, minute, second, tzinfo=UTC)  # ValueError on bad calendar
    return (moment - _EPOCH) // timedelta(seconds=1) * 1000 + int(fraction.ljust(9, "0")) // 1_000_000


def _signature_header(headers: Mapping[str, str]) -> str | None:
    """The one `x-signature` value, looked up case-insensitively; None when absent, conflicting or not a string."""
    found: set[Any] = set()
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() == "x-signature":
            found.add(value)
    if len(found) != 1:
        return None
    (value,) = found
    return value if type(value) is str else None


def _authentic(headers: Mapping[str, str], raw_body: bytes, signing_key: bytes) -> bool:
    presented = _signature_header(headers)
    if presented is None or _SIGNATURE.fullmatch(presented) is None:
        return False
    expected = "sha256=" + hmac.new(signing_key, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), presented.encode("ascii"))


def _interpret(raw_body: bytes) -> tuple[Any, str, int, int, str] | None:
    """(parsed, kind, src_time, sent_time, request_id), or None for a type we do not ledger. Raises _Reject."""
    try:
        parsed = parse_strict(raw_body)
    except ValueError:
        raise _Reject from None
    if not isinstance(parsed, dict):
        raise _Reject
    meta, data = parsed.get("meta"), parsed.get("data")
    if not isinstance(meta, dict) or meta.get("version") != "1.1" or not isinstance(data, dict):
        raise _Reject
    event_type = data.get("type")
    kind = _KINDS.get(event_type) if type(event_type) is str else None
    if kind is None:
        return None
    attributes = data.get("attributes")
    src_time = attributes.get("timestamp") if isinstance(attributes, dict) else None
    if type(src_time) is not int or src_time < 0:
        raise _Reject
    try:
        sent_time = parse_ring_time(meta.get("time"))
    except ValueError:
        raise _Reject from None
    request_id = meta.get("request_id")
    if type(request_id) is not str or not 0 < len(request_id) <= MAX_REQUEST_ID or not is_unicode(request_id):
        raise _Reject
    return parsed, kind, src_time, sent_time, request_id


def handle(headers: Mapping[str, str], raw_body: bytes, received_at: int, ledger: Any, signing_key: bytes, *,
           household: str, synthetic: bool = True) -> Response:
    """Authenticate, interpret and ledger one Ring webhook delivery.

    `household` is required: the Ring payload carries none, so the deployment binds a webhook to one household.
    `synthetic` defaults to True because the blueprint (section 1, E-4) allows DOOR data to be synthetic only:
    there is no real Ring integration, and every envelope this module writes says so in `auth.synthetic`.
    A deployment that receives genuine Ring deliveries must pass `synthetic=False` explicitly."""
    if type(signing_key) is not bytes or not signing_key:
        return _reply(500, status="error")  # misconfiguration: never authenticate with an empty key
    if type(raw_body) is not bytes or not _authentic(headers, raw_body, signing_key):
        return _reply(401, status="unauthorized")
    try:
        try:
            interpreted = _interpret(raw_body)
        except _Reject:
            return _reply(400, status="bad_request")
        if interpreted is None:
            return _reply(200, status="ignored")
        parsed, kind, src_time, sent_time, request_id = interpreted
        params = temporal()
        eps = params["epsilon_ring_ms"]

        def quarantine(reason: str) -> Response:
            # the quarantine claims request_id, so a Ring retry of this delivery is absorbed, never ledgered (BT-5)
            outcome = ledger.quarantine(household=household, source="ring", source_event_id=request_id,
                                        received_at=received_at, raw=raw_body, reason=reason)
            return _reply(200, status="duplicate" if outcome == DUPLICATE else "quarantined")

        if src_time > received_at + params["max_skew_ms"]:
            return quarantine("skew: src_time is beyond max_skew_ms ahead of receipt")
        try:
            check_profile(parsed)
            payload_digest = digest(parsed)
        except ProfileError as exc:
            return quarantine(f"profile: {exc}")
        envelope = {
            "v": 1,
            "household": household,
            "source": "ring",
            "source_event_id": request_id,
            "kind": kind,
            "valid": {"lo": src_time - eps, "hi": src_time + eps},
            "src_time": src_time,
            "sent_time": sent_time,
            "received_at": received_at,
            "raw_digest": "sha256:" + hashlib.sha256(raw_body).hexdigest(),
            "payload_digest": payload_digest,
            "auth": {"scheme": "ring-hmac", "verified": True, "synthetic": synthetic},
        }
        problems = precheck(envelope)
        if problems:  # e.g. a timestamp so early that valid.lo < 0: answering 500 would make Ring retry it forever
            return quarantine("envelope: " + "; ".join(problems))
        result = ledger.append(envelope)
        if result.status == APPENDED:
            return _reply(200, status="accepted", seq=result.envelope["seq"])
        return _reply(200, status="duplicate")
    except Exception:  # noqa: BLE001 - after authentication every failure is ours: 500, never a 4xx
        return _reply(500, status="error")


def make_lambda_handler(key_provider: Callable[[], bytes], ledger: Any, *, household: str,
                        synthetic: bool = True) -> Callable[[dict, Any], dict]:
    """An API Gateway HTTP API (payload v2) handler around `handle`.

    A request without a well-formed `x-signature` is answered 401 before `key_provider` runs, so unsigned floods
    never reach the key store; `key_provider` should still cache the key it fetches."""

    def respond(response: Response) -> dict[str, Any]:
        return {"statusCode": response.status, "headers": {"content-type": "application/json"},
                "body": response.body.decode("utf-8")}

    def lambda_handler(event: dict, context: Any) -> dict:
        try:
            received_at = int(event["requestContext"]["timeEpoch"])
            headers = event.get("headers") or {}
            body = event.get("body")
            if body is None:
                raw = b""
            elif event.get("isBase64Encoded"):
                try:
                    raw = base64.b64decode(body, validate=True)
                except (binascii.Error, ValueError, TypeError):
                    return respond(_reply(401, status="unauthorized"))  # garbage cannot be authenticated
            else:
                raw = body.encode("utf-8")
            presented = _signature_header(headers)
            if presented is None or _SIGNATURE.fullmatch(presented) is None:
                return respond(_reply(401, status="unauthorized"))  # before any (possibly remote) key lookup
            key = key_provider()
            return respond(handle(headers, raw, received_at, ledger, key, household=household,
                                  synthetic=synthetic))
        except Exception:  # noqa: BLE001 - malformed event or key lookup failure: our fault
            return respond(_reply(500, status="error"))

    return lambda_handler
