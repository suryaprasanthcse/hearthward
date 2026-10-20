"""Bee bridge (blueprint TB-4, C-5, section 4.1): signing on the bridge side, verified ingest on ours.

The bridge reads Bee's stream, signs each JSON line with HMAC-SHA256 over the line WITHOUT its newline, and ships
(line, signature). `ingest_line` verifies the signature over the exact bytes before parsing, then ledgers a
`wrist.*` envelope exactly as `tools/orrery.py` does.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

from hearthward.contracts.canonical import ProfileError, check_profile, digest, is_unicode, parse_strict
from hearthward.contracts.specs import temporal
from hearthward.ingest.ledger import APPENDED, DUPLICATE, precheck

_SIGNATURE = re.compile(r"[0-9a-f]{64}")
MAX_EVENT_ID = 128


@dataclass(frozen=True)
class IngestResult:
    status: str  # "accepted" | "duplicate" | "ignored" | "rejected" | "quarantined"
    envelope: dict[str, Any] | None = None
    reason: str = ""


# ── bridge side ──


def _check_key(key: bytes) -> None:
    if type(key) is not bytes or not key:
        raise ValueError("the bridge key is non-empty bytes")


def sign_line(raw_line: bytes, key: bytes) -> str:
    """Hex HMAC-SHA256 of one stream line, which must not include its line terminator."""
    _check_key(key)
    if type(raw_line) is not bytes:
        raise ValueError("a stream line is bytes")
    if raw_line.endswith((b"\n", b"\r")):
        raise ValueError("the signature covers the line without its newline")
    return hmac.new(key, raw_line, hashlib.sha256).hexdigest()


def classify(obj: Any) -> str | None:
    """The ledger kind of a decoded Bee line, or None when it is not a conversation or an utterance."""
    if not isinstance(obj, dict):
        return None
    has_utterance, has_conversation = "utterance" in obj, "conversation" in obj
    if has_utterance == has_conversation:  # neither, or ambiguous
        return None
    return "wrist.utterance" if has_utterance else "wrist.conversation"


def iter_lines(stream: bytes) -> list[bytes]:
    """Split a stream blob on LF only. A CR anywhere is an error: signatures never cover line terminators."""
    if b"\r" in stream:
        raise ValueError("stream contains a carriage return; lines are LF-terminated")
    lines = stream.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


# ── ingest side ──


def _bounds(sync_bounds: tuple[int, int]) -> tuple[int, int]:
    try:
        lo, hi = sync_bounds
    except (TypeError, ValueError):
        raise ValueError("sync_bounds is a (lo, hi) pair") from None
    if type(lo) is not int or type(hi) is not int or lo > hi:
        raise ValueError("sync_bounds is a pair of integers with lo <= hi")
    return lo, hi


def ingest_line(raw_line: bytes, signature: str, received_at: int, ledger: Any, key: bytes, *, household: str,
                sync_bounds: tuple[int, int] | None = None, synthetic: bool = True) -> IngestResult:
    """Verify, interpret and ledger one signed Bee line.

    `raw_line` is exactly the signed bytes: a trailing newline is not stripped, so it fails verification.
    `sync_bounds` is the valid interval for a line recovered by `bee sync`; otherwise it is the live window
    [received_at - delta_ms.bee, received_at]. The bridge HMAC covers the line only, not the bounds, so bounds are
    held to what a recovery can honestly claim: 0 <= lo <= hi <= received_at (a line cannot be valid after we
    received it); bounds outside that are quarantined (see docs/gates/BLOCKERS.md on authenticating them). `synthetic` defaults to True: the Bee integration is a synthetic
    stand-in in this build and every envelope says so in `auth.synthetic`.

    Lines that can never be ledgered (outside HW-JCS, an invalid envelope, implausible bounds) are quarantined, and
    the quarantine claims the event id so a resend is a duplicate. Ledger exceptions propagate on purpose: they are
    transient or faults of the store, the bridge transport owns retry, and a failed append must not look like a
    rejected line."""
    _check_key(key)
    bounds = None if sync_bounds is None else _bounds(sync_bounds)
    if (type(raw_line) is not bytes or type(signature) is not str or _SIGNATURE.fullmatch(signature) is None
            or not hmac.compare_digest(hmac.new(key, raw_line, hashlib.sha256).hexdigest().encode("ascii"),
                                       signature.encode("ascii"))):
        return IngestResult("rejected", reason="signature")
    try:
        obj = parse_strict(raw_line)
    except ValueError:
        return IngestResult("rejected", reason="json")
    kind = classify(obj)
    if kind is None:
        return IngestResult("ignored")
    if kind == "wrist.conversation":
        conversation = obj["conversation"]
        uuid = conversation.get("uuid") if isinstance(conversation, dict) else None
    else:
        uuid = obj.get("conversation_uuid")
    if type(uuid) is not str or not uuid or not is_unicode(uuid):
        return IngestResult("rejected", reason="uuid")
    raw_digest = hashlib.sha256(raw_line).hexdigest()
    event_id = f"bee:{uuid}:{raw_digest[:16]}"
    if len(event_id) > MAX_EVENT_ID:
        return IngestResult("rejected", reason="uuid")
    def quarantine(reason: str) -> IngestResult:
        outcome = ledger.quarantine(household=household, source="bee", source_event_id=event_id,
                                    received_at=received_at, raw=raw_line, reason=reason)
        return IngestResult("duplicate" if outcome == DUPLICATE else "quarantined", reason=reason)

    try:
        check_profile(obj)
        payload_digest = digest(obj)
    except ProfileError as exc:
        return quarantine(f"profile: {exc}")
    if bounds is not None and not 0 <= bounds[0] <= bounds[1] <= received_at:
        return quarantine("sync_bounds: outside [0, received_at]")
    lo, hi = bounds if bounds is not None else (received_at - temporal()["delta_ms"]["bee"], received_at)
    envelope = {
        "v": 1,
        "household": household,
        "source": "bee",
        "source_event_id": event_id,
        "kind": kind,
        "valid": {"lo": lo, "hi": hi},
        "src_time": None,
        "sent_time": None,
        "received_at": received_at,
        "raw_digest": "sha256:" + raw_digest,
        "payload_digest": payload_digest,
        "auth": {"scheme": "bridge-hmac", "verified": True, "synthetic": synthetic},
    }
    problems = precheck(envelope)
    if problems:
        return quarantine("envelope: " + "; ".join(problems))
    result = ledger.append(envelope)
    if result.status == APPENDED:
        return IngestResult("accepted", result.envelope)
    return IngestResult("duplicate", result.envelope)
