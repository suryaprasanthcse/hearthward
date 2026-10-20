"""Bee bridge (TB-4, C-5): signing, classification, the byte-exact orrery stream and the sync-recovery path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import orrery
import pytest

from hearthward.ingest.bee_bridge import IngestResult, classify, ingest_line, iter_lines, sign_line
from hearthward.ingest.ledger import AppendResult
from tests.support import FIXTURES, TABLE, read_json, strip_chain

pytestmark = pytest.mark.gate_g1

KEY = orrery.SYNTHETIC_BRIDGE_KEY
HOUSEHOLD = orrery.HOUSEHOLD
NOW = orrery.EPOCH_MS + 60_000
STREAM = (FIXTURES / "bee" / "stream.jsonl").read_bytes()
MANIFEST = read_json(FIXTURES / "bee" / "manifest.json")
LINES = iter_lines(STREAM)


def replay(ledger: Any, stream: bytes, manifest: dict[str, Any]) -> list[IngestResult]:
    lines = iter_lines(stream)
    assert len(lines) == len(manifest["lines"])
    results = []
    for line, entry in zip(lines, manifest["lines"], strict=True):
        bounds = tuple(entry["sync_bounds"]) if entry["sync_bounds"] is not None else None
        results.append(ingest_line(line, entry["signature"], entry["received_at"], ledger, KEY,
                                   household=HOUSEHOLD, sync_bounds=bounds))
    return results


def line_of(obj: Any) -> tuple[bytes, str]:
    raw = json.dumps(obj).encode("utf-8")
    return raw, sign_line(raw, KEY)


def quarantined(dynamodb: Any) -> list[dict[str, Any]]:
    return dynamodb.query(
        TableName=TABLE, KeyConditionExpression="pk = :pk AND begins_with(sk, :p)",
        ExpressionAttributeValues={":pk": {"S": HOUSEHOLD}, ":p": {"S": "QUAR#"}})["Items"]


# ── the fixture stream ──


def test_stream_fixture_matches_its_manifest() -> None:
    assert len(LINES) == len(MANIFEST["lines"]) > 1
    for line, entry in zip(LINES, MANIFEST["lines"], strict=True):
        assert hashlib.sha256(line).hexdigest() == entry["sha256"]
        assert sign_line(line, KEY) == entry["signature"]


def test_replay_matches_every_expected_envelope(ledger: Any) -> None:
    results = replay(ledger, STREAM, MANIFEST)
    assert [r.status for r in results] == ["accepted"] * len(LINES)
    chain = ledger.read_chain(HOUSEHOLD)
    assert [strip_chain(e) for e in chain] == [e["expected_envelope"] for e in MANIFEST["lines"]]
    assert [r.envelope for r in results] == chain
    assert ledger.verify(HOUSEHOLD) == []


def test_replay_matches_the_orrery_wrist_envelopes(ledger: Any, orrery_runs: dict[str, orrery.Result]) -> None:
    replay(ledger, STREAM, MANIFEST)
    reference = [strip_chain(e) for e in orrery_runs["doorstep_scam"].envelopes if e["source"] == "bee"]
    assert [strip_chain(e) for e in ledger.read_chain(HOUSEHOLD)] == reference


def test_sync_recovered_lines_replay_with_their_bounds(ledger: Any, spec: dict[str, Any], tmp_path: Path) -> None:
    orrery.export_fixtures(spec, tmp_path, scenario="late_and_lossy")
    stream = (tmp_path / "bee" / "stream.jsonl").read_bytes()
    manifest = read_json(tmp_path / "bee" / "manifest.json")
    synced = [e for e in manifest["lines"] if e["via"] == "sync"]
    assert synced and all(e["sync_bounds"] is not None for e in synced)
    results = replay(ledger, stream, manifest)
    assert [r.status for r in results] == ["accepted"] * len(manifest["lines"])
    for entry, result in zip(manifest["lines"], results, strict=True):
        assert strip_chain(result.envelope) == entry["expected_envelope"]
        if entry["sync_bounds"] is not None:
            lo, hi = entry["sync_bounds"]
            assert result.envelope["valid"] == {"lo": lo, "hi": hi}
    assert ledger.verify(HOUSEHOLD) == []


def test_sync_bounds_override_the_live_window(ledger: Any) -> None:
    raw, signature = line_of({"conversation": {"id": 1, "uuid": "u-1", "state": "processing", "title": None}})
    result = ingest_line(raw, signature, NOW, ledger, KEY, household=HOUSEHOLD, sync_bounds=(100, 200))
    assert result.envelope["valid"] == {"lo": 100, "hi": 200}
    live, live_signature = line_of({"utterance": {"text": "hi", "speaker": "speaker_1"}, "conversation_uuid": "u-1"})
    out = ingest_line(live, live_signature, NOW, ledger, KEY, household=HOUSEHOLD)
    assert out.envelope["valid"] == {"lo": NOW - 10_000, "hi": NOW}


@pytest.mark.parametrize("bounds", [(5, 4), (1.0, 2), (True, 2), (1,), (1, 2, 3), "ab"])
def test_bad_sync_bounds_are_a_value_error(ledger: Any, bounds: Any) -> None:
    raw, signature = line_of({"conversation": {"uuid": "u-1"}})
    with pytest.raises(ValueError):
        ingest_line(raw, signature, NOW, ledger, KEY, household=HOUSEHOLD, sync_bounds=bounds)


# ── signing and classification ──


def test_sign_line_rejects_a_line_terminator() -> None:
    for bad in (LINES[0] + b"\n", LINES[0] + b"\r", LINES[0] + b"\r\n"):
        with pytest.raises(ValueError):
            sign_line(bad, KEY)


def test_sign_line_rejects_an_empty_key() -> None:
    with pytest.raises(ValueError):
        sign_line(LINES[0], b"")
    with pytest.raises(ValueError):
        ingest_line(LINES[0], "0" * 64, 1, None, b"", household=HOUSEHOLD)


@pytest.mark.parametrize(("obj", "kind"), [
    ({"utterance": {"text": "x"}, "conversation_uuid": "u"}, "wrist.utterance"),
    ({"conversation": {"uuid": "u"}}, "wrist.conversation"),
    ({"utterance": {}, "conversation": {}}, None),
    ({"other": 1}, None),
    ({"event": "utterance"}, None),
    ({}, None),
    ([], None),
    ("utterance", None),
    (None, None),
    (7, None),
])
def test_classify(obj: Any, kind: str | None) -> None:
    assert classify(obj) == kind


def test_iter_lines_splits_on_lf_and_rejects_cr() -> None:
    assert iter_lines(b"a\nb\n") == [b"a", b"b"]
    assert iter_lines(b"a\nb") == [b"a", b"b"]
    assert iter_lines(b"") == []
    assert iter_lines(b"a\n\nb\n") == [b"a", b"", b"b"]
    for bad in (b"a\r\nb\n", b"a\rb", b"\r"):
        with pytest.raises(ValueError):
            iter_lines(bad)


# ── authentication ──


def test_every_flipped_byte_is_rejected_and_nothing_is_ledgered(ledger: Any) -> None:
    line, signature = LINES[1], MANIFEST["lines"][1]["signature"]
    for position in range(len(line)):
        tampered = bytearray(line)
        tampered[position] ^= 0x01
        result = ingest_line(bytes(tampered), signature, NOW, ledger, KEY, household=HOUSEHOLD)
        assert (result.status, result.reason, result.envelope) == ("rejected", "signature", None)
    assert ledger.read_chain(HOUSEHOLD) == []


def test_trailing_newline_fails_verification(ledger: Any) -> None:
    result = ingest_line(LINES[0] + b"\n", MANIFEST["lines"][0]["signature"], NOW, ledger, KEY, household=HOUSEHOLD)
    assert result.status == "rejected"
    assert ledger.read_chain(HOUSEHOLD) == []


@pytest.mark.parametrize("mangle", [str.upper, lambda s: s[:-1], lambda s: s + "0", lambda s: " " + s,
                                    lambda s: s + "\n", lambda s: "sha256=" + s, lambda s: "", lambda s: "0" * 64])
def test_malformed_or_wrong_signature_is_rejected(ledger: Any, mangle: Any) -> None:
    signature = mangle(MANIFEST["lines"][0]["signature"])
    assert ingest_line(LINES[0], signature, NOW, ledger, KEY, household=HOUSEHOLD).status == "rejected"


def test_non_string_signature_and_wrong_key_are_rejected(ledger: Any) -> None:
    good = MANIFEST["lines"][0]["signature"]
    assert ingest_line(LINES[0], None, NOW, ledger, KEY, household=HOUSEHOLD).status == "rejected"  # type: ignore[arg-type]
    assert ingest_line(LINES[0], good.encode(), NOW, ledger, KEY, household=HOUSEHOLD).status == "rejected"  # type: ignore[arg-type]
    assert ingest_line(LINES[0], good, 1, ledger, b"other-key", household=HOUSEHOLD).status == "rejected"


# ── interpretation ──


def test_replaying_a_line_twice_is_a_duplicate(ledger: Any) -> None:
    entry = MANIFEST["lines"][0]
    first = ingest_line(LINES[0], entry["signature"], entry["received_at"], ledger, KEY, household=HOUSEHOLD)
    second = ingest_line(LINES[0], entry["signature"], entry["received_at"] + 999, ledger, KEY, household=HOUSEHOLD)
    assert (first.status, second.status) == ("accepted", "duplicate")
    assert second.envelope == first.envelope
    assert len(ledger.read_chain(HOUSEHOLD)) == 1


def test_unclassifiable_signed_line_is_ignored(ledger: Any) -> None:
    raw, signature = line_of({"event": "heartbeat"})
    result = ingest_line(raw, signature, NOW, ledger, KEY, household=HOUSEHOLD)
    assert (result.status, result.envelope) == ("ignored", None)
    assert ledger.read_chain(HOUSEHOLD) == []


@pytest.mark.parametrize("raw", [b"{not json", b"", b'{"conversation": NaN}', b'{"conversation": Infinity}',
                                 b'{"conversation": {}, "conversation": {}}', b"\xff\xfe{}", b"[" * 100000],
                         ids=["bad-json", "empty", "nan", "infinity", "duplicate-key", "not-utf8", "deeply-nested"])
def test_signed_garbage_is_rejected_as_json(ledger: Any, raw: bytes) -> None:
    result = ingest_line(raw, sign_line(raw, KEY), NOW, ledger, KEY, household=HOUSEHOLD)
    assert (result.status, result.reason) == ("rejected", "json")


@pytest.mark.parametrize("obj", [
    {"conversation": {"id": 1}},
    {"conversation": "u"},
    {"conversation": {"uuid": ""}},
    {"conversation": {"uuid": 7}},
    {"utterance": {"text": "x"}},
    {"utterance": {"text": "x"}, "conversation_uuid": None},
    {"conversation": {"uuid": "u" * 200}},
])
def test_missing_or_oversized_uuid_is_rejected(ledger: Any, obj: Any) -> None:
    raw, signature = line_of(obj)
    result = ingest_line(raw, signature, NOW, ledger, KEY, household=HOUSEHOLD)
    assert (result.status, result.reason) == ("rejected", "uuid")
    assert ledger.read_chain(HOUSEHOLD) == []


def test_profile_violation_is_quarantined(ledger: Any, dynamodb: Any) -> None:
    raw, signature = line_of({"utterance": {"text": "x", "confidence": 0.5}, "conversation_uuid": "u-1"})
    result = ingest_line(raw, signature, 77, ledger, KEY, household=HOUSEHOLD)
    assert result.status == "quarantined" and result.envelope is None
    assert ledger.read_chain(HOUSEHOLD) == []
    (item,) = quarantined(dynamodb)
    assert item["source"]["S"] == "bee"
    assert item["raw_digest"]["S"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert item["source_event_id"]["S"].startswith("bee:u-1:")


def test_non_snake_case_key_is_quarantined(ledger: Any) -> None:
    raw, signature = line_of({"conversation": {"uuid": "u-1", "startedAt": 1}})
    assert ingest_line(raw, signature, NOW, ledger, KEY, household=HOUSEHOLD).status == "quarantined"


def test_synthetic_flag_and_event_id_shape(ledger: Any) -> None:
    raw, signature = line_of({"conversation": {"uuid": "u-1"}})
    result = ingest_line(raw, signature, NOW, ledger, KEY, household=HOUSEHOLD, synthetic=False)
    assert result.envelope["auth"] == {"scheme": "bridge-hmac", "verified": True, "synthetic": False}
    assert result.envelope["source_event_id"] == "bee:u-1:" + hashlib.sha256(raw).hexdigest()[:16]
    assert result.envelope["src_time"] is None and result.envelope["sent_time"] is None


def test_ledger_errors_propagate(ledger: Any) -> None:
    class Failing:
        def append(self, envelope: dict[str, Any]) -> AppendResult:
            raise RuntimeError("transport decides")

    entry = MANIFEST["lines"][0]
    with pytest.raises(RuntimeError):
        ingest_line(LINES[0], entry["signature"], NOW, Failing(), KEY, household=HOUSEHOLD)


# ── security-review regressions (docs/gates/G1_COMPLETION_LOG.md) ──


@pytest.mark.parametrize("bounds", [(-1, 5), (0, 2**53 - 1), (NOW - 10, NOW + 1)], ids=["negative", "unbounded",
                                                                                     "after-receipt"])
def test_m2_m3_implausible_sync_bounds_are_quarantined_not_ledgered(ledger: Any, dynamodb: Any,
                                                                    bounds: tuple[int, int]) -> None:
    raw, signature = line_of({"conversation": {"uuid": "u-1"}})
    result = ingest_line(raw, signature, NOW, ledger, KEY, household=HOUSEHOLD, sync_bounds=bounds)
    assert result.status == "quarantined" and result.envelope is None
    assert ledger.read_chain(HOUSEHOLD) == [] and len(quarantined(dynamodb)) == 1
    # the claim absorbs the bridge's own later delivery of the same line instead of letting it in twice
    again = ingest_line(raw, signature, NOW + 5, ledger, KEY, household=HOUSEHOLD)
    assert again.status == "duplicate" and ledger.read_chain(HOUSEHOLD) == []
    assert ledger.verify(HOUSEHOLD) == []


def test_sync_bounds_ending_at_receipt_are_accepted(ledger: Any) -> None:
    raw, signature = line_of({"conversation": {"uuid": "u-1"}})
    result = ingest_line(raw, signature, NOW, ledger, KEY, household=HOUSEHOLD, sync_bounds=(0, NOW))
    assert result.status == "accepted" and result.envelope["valid"] == {"lo": 0, "hi": NOW}


def test_m1_quarantined_line_resent_is_a_duplicate_with_one_record(ledger: Any, dynamodb: Any) -> None:
    raw, signature = line_of({"utterance": {"text": "x", "confidence": 0.5}, "conversation_uuid": "u-1"})
    assert ingest_line(raw, signature, NOW, ledger, KEY, household=HOUSEHOLD).status == "quarantined"
    assert ingest_line(raw, signature, NOW + 1, ledger, KEY, household=HOUSEHOLD).status == "duplicate"
    assert len(quarantined(dynamodb)) == 1


def test_l8_uuid_with_a_lone_surrogate_is_rejected_and_never_stored(ledger: Any, dynamodb: Any) -> None:
    raw = b'{"conversation": {"uuid": "' + b"\\" + b'ud800"}}'
    result = ingest_line(raw, sign_line(raw, KEY), NOW, ledger, KEY, household=HOUSEHOLD)
    assert (result.status, result.reason) == ("rejected", "uuid")
    assert quarantined(dynamodb) == [] and ledger.read_chain(HOUSEHOLD) == []
