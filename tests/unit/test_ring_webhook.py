"""Ring webhook ingest (TB-3, C-5): signature before parsing, the byte-exact orrery fixture, skew and profile
quarantine, Ring-safe status codes and the API Gateway adapter."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import orrery
import pytest
from botocore.exceptions import ClientError
from hypothesis import given
from hypothesis import strategies as st

from hearthward.contracts.specs import temporal
from hearthward.ingest.ledger import AppendResult, LedgerConflict
from hearthward.ingest.ring_webhook import Response, handle, make_lambda_handler, parse_ring_time
from tests.support import FIXTURES, TABLE, read_json, strip_chain

pytestmark = pytest.mark.gate_g1

KEY = orrery.SYNTHETIC_RING_KEY
HOUSEHOLD = orrery.HOUSEHOLD
RAW = (FIXTURES / "ring" / "sample.json").read_bytes()
MANIFEST = read_json(FIXTURES / "ring" / "manifest.json")
SIGNATURE = MANIFEST["x_signature"]
RECEIVED_AT = MANIFEST["received_at"]
SRC_TIME = MANIFEST["expected_envelope"]["src_time"]


def sign(raw: bytes, key: bytes = KEY) -> str:
    return "sha256=" + hmac.new(key, raw, hashlib.sha256).hexdigest()


def body_of(response: Response) -> dict[str, Any]:
    return json.loads(response.body)


def deliver(ledger: Any, raw: bytes = RAW, headers: dict[str, str] | None = None, received_at: int = RECEIVED_AT,
            key: bytes = KEY) -> Response:
    if headers is None:
        headers = {"X-Signature": sign(raw)}
    return handle(headers, raw, received_at, ledger, key, household=HOUSEHOLD)


def edited(mutate: Any) -> bytes:
    payload = json.loads(RAW)
    mutate(payload)
    return json.dumps(payload).encode("utf-8")


def quarantined(dynamodb: Any) -> list[dict[str, Any]]:
    return dynamodb.query(
        TableName=TABLE, KeyConditionExpression="pk = :pk AND begins_with(sk, :p)",
        ExpressionAttributeValues={":pk": {"S": HOUSEHOLD}, ":p": {"S": "QUAR#"}})["Items"]


# ── the fixture ──


def test_fixture_file_matches_its_manifest_digest() -> None:
    assert hashlib.sha256(RAW).hexdigest() == MANIFEST["sha256"]
    assert sign(RAW) == SIGNATURE


def test_fixture_verifies_byte_exact_and_matches_orrery(ledger: Any, orrery_runs: dict[str, orrery.Result]) -> None:
    response = handle({"X-Signature": SIGNATURE}, RAW, RECEIVED_AT, ledger, KEY, household=HOUSEHOLD)
    assert response.status == 200
    assert body_of(response) == {"status": "accepted", "seq": 1}
    (stored,) = ledger.read_chain(HOUSEHOLD)
    assert strip_chain(stored) == MANIFEST["expected_envelope"]
    request_id = MANIFEST["expected_envelope"]["source_event_id"]
    (reference,) = [e for e in orrery_runs["doorstep_scam"].envelopes if e["source_event_id"] == request_id]
    assert strip_chain(stored) == strip_chain(reference)
    assert ledger.verify(HOUSEHOLD) == []


def test_synthetic_flag_defaults_true_and_can_be_cleared(ledger: Any) -> None:
    handle({"x-signature": SIGNATURE}, RAW, RECEIVED_AT, ledger, KEY, household=HOUSEHOLD, synthetic=False)
    assert ledger.read_chain(HOUSEHOLD)[0]["auth"]["synthetic"] is False


# ── authentication ──


def flip_positions() -> list[int]:
    n = len(RAW)
    return sorted({0, 1, 7, n // 4, n // 3, n // 2, (2 * n) // 3, (3 * n) // 4, n - 2, n - 1})


@pytest.mark.parametrize("position", flip_positions())
def test_any_flipped_byte_is_401_and_nothing_is_ledgered(ledger: Any, dynamodb: Any, position: int) -> None:
    tampered = bytearray(RAW)
    tampered[position] ^= 0x01
    response = handle({"X-Signature": SIGNATURE}, bytes(tampered), RECEIVED_AT, ledger, KEY, household=HOUSEHOLD)
    assert response.status == 401
    assert ledger.read_chain(HOUSEHOLD) == []
    assert quarantined(dynamodb) == []


def test_appended_or_truncated_body_is_401(ledger: Any) -> None:
    assert deliver(ledger, RAW + b" ", {"X-Signature": SIGNATURE}).status == 401
    assert deliver(ledger, RAW[:-1], {"X-Signature": SIGNATURE}).status == 401
    assert ledger.read_chain(HOUSEHOLD) == []


@pytest.mark.parametrize("name", ["X-Signature", "x-signature", "X-SIGNATURE", "x-SiGnAtUrE"])
def test_header_name_is_case_insensitive(ledger: Any, name: str) -> None:
    assert deliver(ledger, headers={name: SIGNATURE}).status == 200


@pytest.mark.parametrize("headers", [
    {},
    {"X-Other": SIGNATURE},
    {"X-Signature": SIGNATURE.upper()},
    {"X-Signature": "sha256=" + SIGNATURE[7:].upper()},
    {"X-Signature": SIGNATURE[7:]},
    {"X-Signature": "sha256 =" + SIGNATURE[7:]},
    {"X-Signature": "sha256= " + SIGNATURE[7:]},
    {"X-Signature": " " + SIGNATURE},
    {"X-Signature": SIGNATURE + " "},
    {"X-Signature": SIGNATURE + "\n"},
    {"X-Signature": SIGNATURE[:-1]},
    {"X-Signature": SIGNATURE + "0"},
    {"X-Signature": "sha1=" + SIGNATURE[7:]},
    {"X-Signature": ""},
    {"X-Signature": 5},
], ids=["missing", "other-header", "uppercase-all", "uppercase-hex", "no-prefix", "space-before-equals",
        "space-after-equals", "leading-space", "trailing-space", "trailing-newline", "short", "long", "wrong-algo",
        "empty", "non-string"])
def test_malformed_or_missing_signature_is_401(ledger: Any, headers: dict[str, Any]) -> None:
    assert deliver(ledger, headers=headers).status == 401
    assert ledger.read_chain(HOUSEHOLD) == []


def test_conflicting_duplicate_header_casings_are_401_even_if_one_is_valid(ledger: Any) -> None:
    headers = {"X-Signature": SIGNATURE, "x-signature": "sha256=" + "0" * 64}
    assert deliver(ledger, headers=headers).status == 401
    assert ledger.read_chain(HOUSEHOLD) == []


def test_identical_duplicate_header_casings_are_accepted(ledger: Any) -> None:
    assert deliver(ledger, headers={"X-Signature": SIGNATURE, "x-signature": SIGNATURE}).status == 200


def test_wrong_key_is_401(ledger: Any) -> None:
    assert deliver(ledger, key=b"another-synthetic-key").status == 401


def test_body_must_be_bytes(ledger: Any) -> None:
    response = handle({"X-Signature": SIGNATURE}, RAW.decode(), RECEIVED_AT, ledger, KEY, household=HOUSEHOLD)  # type: ignore[arg-type]
    assert response.status == 401


def test_signature_is_checked_before_the_body_is_parsed(ledger: Any) -> None:
    # unparseable bytes with a bad signature are a 401, not the 400 an authenticated parse failure earns
    assert deliver(ledger, b"{not json", {"X-Signature": SIGNATURE}).status == 401


def test_empty_signing_key_is_500_even_with_the_hmac_of_the_empty_key(ledger: Any) -> None:
    empty_key_signature = sign(RAW, b"")
    response = handle({"X-Signature": empty_key_signature}, RAW, RECEIVED_AT, ledger, b"", household=HOUSEHOLD)
    assert response.status == 500
    assert ledger.read_chain(HOUSEHOLD) == []


# ── duplicates and ignored types ──


def test_duplicate_delivery_is_two_200s_and_one_entry(ledger: Any) -> None:
    first, second = deliver(ledger), deliver(ledger)
    assert (first.status, second.status) == (200, 200)
    assert body_of(first)["status"] == "accepted"
    assert body_of(second) == {"status": "duplicate"}
    assert len(ledger.read_chain(HOUSEHOLD)) == 1


@pytest.mark.parametrize("event_type", ["ding", "device_online", "", "MOTION_DETECTED"])
def test_other_event_types_are_ignored(ledger: Any, event_type: str) -> None:
    raw = edited(lambda p: p["data"].update(type=event_type))
    response = deliver(ledger, raw)
    assert (response.status, body_of(response)) == (200, {"status": "ignored"})
    assert ledger.read_chain(HOUSEHOLD) == []


def test_button_press_becomes_door_button(ledger: Any) -> None:
    raw = edited(lambda p: p["data"].update(type="button_press"))
    assert deliver(ledger, raw).status == 200
    assert ledger.read_chain(HOUSEHOLD)[0]["kind"] == "door.button"


# ── skew and profile quarantine ──


def test_skew_beyond_max_is_quarantined_not_ledgered(ledger: Any, dynamodb: Any) -> None:
    received_at = SRC_TIME - temporal()["max_skew_ms"] - 1
    response = deliver(ledger, received_at=received_at)
    assert (response.status, body_of(response)) == (200, {"status": "quarantined"})
    assert ledger.read_chain(HOUSEHOLD) == []
    (item,) = quarantined(dynamodb)
    assert item["source"]["S"] == "ring"
    assert item["raw_digest"]["S"] == "sha256:" + MANIFEST["sha256"]
    assert item["source_event_id"]["S"] == MANIFEST["expected_envelope"]["source_event_id"]


def test_skew_exactly_at_max_is_accepted(ledger: Any, dynamodb: Any) -> None:
    received_at = SRC_TIME - temporal()["max_skew_ms"]
    response = deliver(ledger, received_at=received_at)
    assert body_of(response)["status"] == "accepted"
    assert len(ledger.read_chain(HOUSEHOLD)) == 1
    assert quarantined(dynamodb) == []


def test_float_anywhere_is_quarantined(ledger: Any, dynamodb: Any) -> None:
    raw = edited(lambda p: p["data"]["attributes"].update(battery=0.5))
    response = deliver(ledger, raw)
    assert (response.status, body_of(response)) == (200, {"status": "quarantined"})
    assert ledger.read_chain(HOUSEHOLD) == []
    assert len(quarantined(dynamodb)) == 1


def test_key_outside_snake_case_is_quarantined(ledger: Any, dynamodb: Any) -> None:
    raw = edited(lambda p: p["data"]["attributes"].update(requestId="x"))
    assert body_of(deliver(ledger, raw)) == {"status": "quarantined"}
    assert len(quarantined(dynamodb)) == 1


# ── authenticated but uninterpretable: 400 ──


def test_invalid_json_with_valid_signature_is_400(ledger: Any) -> None:
    assert deliver(ledger, b"{not json").status == 400


@pytest.mark.parametrize("raw", [
    b"", b"[]", b"7", b"null", b"\xff\xfe{}", b'{"meta": {"version": "1.1"}, "x": NaN}',
    b'{"meta": {"version": "1.1"}, "x": Infinity}', b'{"meta": {"version": "1.1"}, "meta": {}}',
    b"[" * 100000,
], ids=["empty", "array", "number", "null", "not-utf8", "nan", "infinity", "duplicate-key", "deeply-nested"])
def test_unparseable_bodies_with_valid_signature_are_400(ledger: Any, raw: bytes) -> None:
    assert deliver(ledger, raw).status == 400
    assert ledger.read_chain(HOUSEHOLD) == []


@pytest.mark.parametrize("mutate", [
    lambda p: p["meta"].update(version="1.0"),
    lambda p: p["meta"].pop("version"),
    lambda p: p["meta"].update(time="2026-10-20T09:00:01Z"),
    lambda p: p["meta"].update(time="2026-10-20T09:00:01.3750000000Z"),
    lambda p: p["meta"].update(time="2026-10-20T09:00:01.375000000+00:00"),
    lambda p: p["meta"].update(time="2026-02-30T09:00:01.375000000Z"),
    lambda p: p["meta"].update(time="2026-10-20T09:00:61.375000000Z"),
    lambda p: p["meta"].update(time=1792486801375),
    lambda p: p["meta"].pop("time"),
    lambda p: p["meta"].update(request_id=""),
    lambda p: p["meta"].update(request_id="x" * 129),
    lambda p: p["meta"].update(request_id=7),
    lambda p: p["meta"].pop("request_id"),
    lambda p: p["data"]["attributes"].update(timestamp="1792486800800"),
    lambda p: p["data"]["attributes"].update(timestamp=True),
    lambda p: p["data"]["attributes"].update(timestamp=-1),
    lambda p: p["data"]["attributes"].pop("timestamp"),
    lambda p: p["data"].pop("attributes"),
    lambda p: p.update(data=[]),
    lambda p: p.pop("meta"),
], ids=["version-1.0", "no-version", "no-fraction", "ten-digit-fraction", "offset-not-z", "bad-day", "bad-second",
        "time-number", "no-time", "empty-request-id", "long-request-id", "numeric-request-id", "no-request-id",
        "string-timestamp", "bool-timestamp", "negative-timestamp", "no-timestamp", "no-attributes", "data-array",
        "no-meta"])
def test_uninterpretable_deliveries_are_400(ledger: Any, mutate: Any) -> None:
    response = deliver(ledger, edited(mutate))
    assert response.status == 400
    assert body_of(response) == {"status": "bad_request"}
    assert ledger.read_chain(HOUSEHOLD) == []


def test_request_id_of_128_characters_is_accepted(ledger: Any) -> None:
    assert deliver(ledger, edited(lambda p: p["meta"].update(request_id="r" * 128))).status == 200


# ── our failures are 500, never 4xx ──


class FakeLedger:
    def __init__(self, append: Any = None, quarantine: Any = None) -> None:
        self._append, self._quarantine = append, quarantine

    def append(self, envelope: dict[str, Any]) -> AppendResult:
        raise self._append

    def quarantine(self, **kwargs: Any) -> None:
        raise self._quarantine


SECRET = "boom-secret-4f1c"
FAILURES = [
    LedgerConflict(SECRET),
    RuntimeError(SECRET),
    KeyError(SECRET),
    ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": SECRET}}, "TransactWriteItems"),
]


@pytest.mark.parametrize("failure", FAILURES, ids=lambda e: type(e).__name__)
def test_ledger_failure_is_500_without_leaking_the_message(failure: Exception) -> None:
    response = deliver(FakeLedger(append=failure))
    assert response.status == 500
    assert body_of(response) == {"status": "error"}
    assert SECRET.encode() not in response.body


def test_quarantine_failure_is_500_for_skew_and_for_profile() -> None:
    fake = FakeLedger(quarantine=RuntimeError(SECRET))
    skewed = deliver(fake, received_at=SRC_TIME - temporal()["max_skew_ms"] - 1)
    floaty = deliver(fake, edited(lambda p: p["data"]["attributes"].update(battery=0.5)))
    for response in (skewed, floaty):
        assert response.status == 500
        assert SECRET.encode() not in response.body


def test_bad_ledger_result_is_500() -> None:
    class Weird:
        def append(self, envelope: dict[str, Any]) -> Any:
            return object()

    assert deliver(Weird()).status == 500


# ── sent_time parsing ──


@given(st.integers(min_value=0, max_value=2**41))
def test_parse_ring_time_inverts_iso_ns(ms: int) -> None:
    assert parse_ring_time(orrery.iso_ns(ms)) == ms


def test_parse_ring_time_truncates_below_a_millisecond() -> None:
    assert parse_ring_time("2026-10-20T09:00:01.375999999Z") == parse_ring_time("2026-10-20T09:00:01.375000000Z")
    assert parse_ring_time("1970-01-01T00:00:00.000000000Z") == 0
    assert parse_ring_time("1970-01-01T00:00:00.000999999Z") == 0


def test_fixture_sent_time_is_parsed_from_meta_time() -> None:
    assert parse_ring_time(json.loads(RAW)["meta"]["time"]) == MANIFEST["expected_envelope"]["sent_time"]


# ── the API Gateway adapter ──


def event_for(raw: bytes, *, encoded: bool, signature: str = SIGNATURE, time_epoch: int = RECEIVED_AT) -> dict[str, Any]:
    body = base64.b64encode(raw).decode() if encoded else raw.decode("utf-8")
    return {"version": "2.0", "headers": {"x-signature": signature, "content-type": "application/json"},
            "body": body, "isBase64Encoded": encoded, "requestContext": {"timeEpoch": time_epoch}}


def test_lambda_handler_accepts_a_base64_body(ledger: Any) -> None:
    handler = make_lambda_handler(lambda: KEY, ledger, household=HOUSEHOLD)
    out = handler(event_for(RAW, encoded=True), None)
    assert out["statusCode"] == 200
    assert out["headers"] == {"content-type": "application/json"}
    assert json.loads(out["body"]) == {"status": "accepted", "seq": 1}
    assert strip_chain(ledger.read_chain(HOUSEHOLD)[0]) == MANIFEST["expected_envelope"]


def test_lambda_handler_accepts_a_plain_body(ledger: Any) -> None:
    handler = make_lambda_handler(lambda: KEY, ledger, household=HOUSEHOLD)
    assert handler(event_for(RAW, encoded=False), None)["statusCode"] == 200


def test_lambda_handler_invalid_base64_is_401(ledger: Any) -> None:
    handler = make_lambda_handler(lambda: KEY, ledger, household=HOUSEHOLD)
    event = event_for(RAW, encoded=True)
    event["body"] = "!!!not base64!!!"
    assert handler(event, None)["statusCode"] == 401
    event["body"] = base64.b64encode(RAW).decode() + "="  # bad padding
    assert handler(event, None)["statusCode"] == 401
    assert ledger.read_chain(HOUSEHOLD) == []


def test_lambda_handler_uses_request_context_time_epoch_as_received_at(ledger: Any) -> None:
    handler = make_lambda_handler(lambda: KEY, ledger, household=HOUSEHOLD)
    assert handler(event_for(RAW, encoded=True, time_epoch=RECEIVED_AT + 5), None)["statusCode"] == 200
    assert ledger.read_chain(HOUSEHOLD)[0]["received_at"] == RECEIVED_AT + 5


def test_lambda_handler_time_epoch_drives_the_skew_rule(ledger: Any) -> None:
    handler = make_lambda_handler(lambda: KEY, ledger, household=HOUSEHOLD)
    out = handler(event_for(RAW, encoded=True, time_epoch=SRC_TIME - temporal()["max_skew_ms"] - 1), None)
    assert json.loads(out["body"]) == {"status": "quarantined"}


def test_lambda_handler_missing_body_is_401(ledger: Any) -> None:
    handler = make_lambda_handler(lambda: KEY, ledger, household=HOUSEHOLD)
    event = event_for(RAW, encoded=False)
    del event["body"]
    assert handler(event, None)["statusCode"] == 401


def test_lambda_handler_headers_are_looked_up_case_insensitively(ledger: Any) -> None:
    handler = make_lambda_handler(lambda: KEY, ledger, household=HOUSEHOLD)
    event = event_for(RAW, encoded=False)
    event["headers"] = {"X-SIGNATURE": SIGNATURE}
    assert handler(event, None)["statusCode"] == 200


def test_lambda_handler_key_provider_failure_is_500_without_the_message(ledger: Any) -> None:
    def broken() -> bytes:
        raise RuntimeError(SECRET)

    out = make_lambda_handler(broken, ledger, household=HOUSEHOLD)(event_for(RAW, encoded=True), None)
    assert out["statusCode"] == 500
    assert SECRET not in out["body"]
    assert ledger.read_chain(HOUSEHOLD) == []


def test_lambda_handler_empty_key_is_500_and_malformed_event_is_500(ledger: Any) -> None:
    assert make_lambda_handler(lambda: b"", ledger, household=HOUSEHOLD)(event_for(RAW, encoded=True), None)[
        "statusCode"] == 500
    handler = make_lambda_handler(lambda: KEY, ledger, household=HOUSEHOLD)
    assert handler({"headers": {}, "body": ""}, None)["statusCode"] == 500


# ── security-review regressions (docs/gates/G1_COMPLETION_LOG.md) ──


def test_m1_retry_of_a_skew_quarantined_delivery_is_absorbed_not_ledgered(ledger: Any, dynamodb: Any) -> None:
    early = SRC_TIME - temporal()["max_skew_ms"] - 1
    assert body_of(deliver(ledger, received_at=early)) == {"status": "quarantined"}
    # our 200 was lost; Ring retries 1 s later, when the skew is no longer beyond max_skew_ms
    retry = deliver(ledger, received_at=early + 1_000)
    assert (retry.status, body_of(retry)) == (200, {"status": "duplicate"})
    assert ledger.read_chain(HOUSEHOLD) == []
    assert len(quarantined(dynamodb)) == 1
    assert ledger.verify(HOUSEHOLD) == []


def test_m1_repeated_quarantine_writes_one_record(ledger: Any, dynamodb: Any) -> None:
    raw = edited(lambda p: p["data"]["attributes"].update(confidence=0.5))
    assert body_of(deliver(ledger, raw)) == {"status": "quarantined"}
    assert body_of(deliver(ledger, raw, received_at=RECEIVED_AT + 5_000)) == {"status": "duplicate"}
    assert len(quarantined(dynamodb)) == 1


@pytest.mark.parametrize("mutate", [
    lambda p: p["data"]["attributes"].update(timestamp=1_500),
    lambda p: p["meta"].update(time="1969-12-31T23:59:59.000000000Z"),
], ids=["valid-lo-negative", "sent-time-negative"])
def test_m2_unledgerable_envelopes_are_quarantined_not_500(ledger: Any, dynamodb: Any, mutate: Any) -> None:
    raw = edited(mutate)
    for attempt in range(2):  # a retry is absorbed by the quarantine claim, not answered 500 forever
        response = deliver(ledger, raw, received_at=RECEIVED_AT + attempt * 1_000)
        assert response.status == 200
    (item,) = quarantined(dynamodb)
    assert item["reason"]["S"].startswith("envelope: ")
    assert ledger.read_chain(HOUSEHOLD) == []


@pytest.mark.parametrize(("fraction", "ms"), [("375", 375), ("37512345", 375), ("3", 300), ("000000001", 0)])
def test_l1_trimmed_fractions_are_accepted_and_truncated(ledger: Any, fraction: str, ms: int) -> None:
    raw = edited(lambda p: p["meta"].update(time=f"2026-10-20T09:00:01.{fraction}Z"))
    assert deliver(ledger, raw).status == 200
    assert ledger.read_chain(HOUSEHOLD)[0]["sent_time"] == orrery.EPOCH_MS + 1_000 + ms


def test_l7_unsigned_requests_never_reach_the_key_provider(ledger: Any) -> None:
    calls: list[int] = []

    def provider() -> bytes:
        calls.append(1)
        return KEY

    handler = make_lambda_handler(provider, ledger, household=HOUSEHOLD)
    for signature in ("", "sha256=xyz", "SHA256=" + "0" * 64):
        assert handler(event_for(RAW, encoded=False, signature=signature), None)["statusCode"] == 401
    event = event_for(RAW, encoded=False)
    del event["headers"]["x-signature"]
    assert handler(event, None)["statusCode"] == 401
    assert calls == []
    assert handler(event_for(RAW, encoded=False), None)["statusCode"] == 200 and calls == [1]


def test_l8_request_id_with_a_lone_surrogate_is_400_and_never_stored(ledger: Any, dynamodb: Any) -> None:
    raw = RAW.replace(MANIFEST["expected_envelope"]["source_event_id"].encode(), rb"\ud800")
    assert deliver(ledger, raw).status == 400
    assert quarantined(dynamodb) == [] and ledger.read_chain(HOUSEHOLD) == []
