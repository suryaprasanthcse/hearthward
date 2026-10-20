"""The hash-chained DynamoDB ledger (blueprint §4.2, E-2, E-3, E-6) on moto: no real AWS."""

from __future__ import annotations

import copy
import json
from typing import Any

import orrery
import pytest
from botocore.exceptions import ClientError

from hearthward.contracts.canonical import canonical
from hearthward.contracts.envelope import ZERO_DIGEST, chain_head, verify_chain
from hearthward.ingest.ledger import (
    APPENDED,
    DUPLICATE,
    MAX_ATTEMPTS,
    QUARANTINED,
    Ledger,
    LedgerConflict,
    LedgerError,
    LedgerValidationError,
    dedup_key,
    quarantine_key,
    seq_key,
)
from tests.support import REGION, TABLE, strip_chain

pytestmark = pytest.mark.gate_g1
HH = orrery.HOUSEHOLD


@pytest.fixture(scope="module")
def doorstep(orrery_runs: dict[str, orrery.Result]) -> list[dict[str, Any]]:
    return orrery_runs["doorstep_scam"].envelopes


def items(client: Any, prefix: str = "") -> list[dict[str, Any]]:
    found = client.scan(TableName=TABLE)["Items"]
    return sorted((i for i in found if i["sk"]["S"].startswith(prefix)), key=lambda i: i["sk"]["S"])


def test_genesis(ledger: Ledger, dynamodb: Any, doorstep: list[dict[str, Any]]) -> None:
    result = ledger.append(strip_chain(doorstep[0]))
    assert result.status == APPENDED
    assert result.envelope["seq"] == 1 and result.envelope["prev"] == ZERO_DIGEST
    assert result.envelope == doorstep[0]
    head = dynamodb.get_item(TableName=TABLE, Key={"pk": {"S": HH}, "sk": {"S": "HEAD"}})["Item"]
    assert head["head_seq"]["N"] == "1" and head["head"]["S"] == doorstep[0]["head"]
    assert [i["sk"]["S"] for i in items(dynamodb)] == sorted(
        ["HEAD", seq_key(1), dedup_key("ring", doorstep[0]["source_event_id"])])


def test_replaying_the_orrery_ledger_reproduces_every_head(ledger: Ledger, doorstep: list[dict[str, Any]]) -> None:
    for expected in doorstep:
        result = ledger.append(strip_chain(expected))
        assert result.status == APPENDED
        assert result.envelope == expected
    chain = ledger.read_chain(HH)
    assert chain == doorstep
    assert chain[-1]["head"] == "sha256:6fcae2c6b485f94a60590413412591f45dabdcd82feccd56fa00433d39e866c7"
    assert verify_chain(chain) == [] and ledger.verify(HH) == []


def test_envelopes_are_stored_as_canonical_strings(ledger: Ledger, dynamodb: Any,
                                                    doorstep: list[dict[str, Any]]) -> None:
    for expected in doorstep[:3]:
        ledger.append(strip_chain(expected))
    for item, expected in zip(items(dynamodb, "SEQ#"), doorstep[:3]):
        assert set(item["envelope"]) == {"S"}
        assert item["envelope"]["S"].encode("utf-8") == canonical(expected)


def test_duplicate_source_event_id_is_absorbed_without_new_items(ledger: Ledger, dynamodb: Any,
                                                                  doorstep: list[dict[str, Any]]) -> None:
    for expected in doorstep[:4]:
        ledger.append(strip_chain(expected))
    before = items(dynamodb)
    again = strip_chain(doorstep[1])
    again["received_at"] += 30_000  # a Ring retry: same request_id, later receipt
    result = ledger.append(again)
    assert result.status == DUPLICATE
    assert result.envelope == doorstep[1]
    assert items(dynamodb) == before
    assert ledger.verify(HH) == []


def test_same_event_id_from_another_source_is_not_a_duplicate(ledger: Ledger,
                                                               doorstep: list[dict[str, Any]]) -> None:
    door = next(e for e in doorstep if e["source"] == "ring")
    ledger.append(strip_chain(door))
    wrist = next(e for e in doorstep if e["source"] == "bee")
    clash = dict(strip_chain(wrist), source_event_id=door["source_event_id"])
    assert ledger.append(clash).status == APPENDED


def test_households_have_independent_chains(ledger: Ledger, doorstep: list[dict[str, Any]]) -> None:
    ledger.append(strip_chain(doorstep[0]))
    other = dict(strip_chain(doorstep[0]), household="hh_other0001")
    result = ledger.append(other)
    assert result.status == APPENDED and result.envelope["seq"] == 1 and result.envelope["prev"] == ZERO_DIGEST
    assert ledger.verify(HH) == [] and ledger.verify("hh_other0001") == []


class RacingLedger(Ledger):
    """Lets a rival writer append between this writer's head read and its transaction, `races` times."""

    def __init__(self, rival: Ledger, rival_events: list[dict[str, Any]], races: int, **kwargs: Any) -> None:
        super().__init__(TABLE, REGION, **kwargs)
        self.rival, self.rival_events, self.races, self.reads = rival, list(rival_events), races, 0

    def _read_head(self, household: str) -> tuple[int, str]:
        head = super()._read_head(household)
        self.reads += 1
        if self.races and self.rival_events:
            self.races -= 1
            assert self.rival.append(self.rival_events.pop(0)).status == APPENDED
        return head


def test_stale_head_race_retries_to_a_gapless_fork_free_chain(ledger: Ledger, dynamodb: Any,
                                                              doorstep: list[dict[str, Any]]) -> None:
    events = [strip_chain(e) for e in doorstep]
    ledger.append(events[0])
    sleeps: list[float] = []
    racer = RacingLedger(ledger, events[1:4], races=3, client=dynamodb, sleep=sleeps.append)
    result = racer.append(events[4])
    assert result.status == APPENDED
    assert result.envelope["seq"] == 5 and racer.reads == 4
    assert len(sleeps) == 3 and all(0 <= s <= 0.05 for s in sleeps)
    chain = ledger.read_chain(HH)
    assert [e["seq"] for e in chain] == [1, 2, 3, 4, 5]
    assert [e["source_event_id"] for e in chain] == [e["source_event_id"] for e in events[:5]]
    assert verify_chain(chain) == [] and ledger.verify(HH) == []
    assert len(items(dynamodb, "SEQ#")) == 5 and len(items(dynamodb, "DEDUP#")) == 5


def test_losing_every_race_raises_ledger_conflict(ledger: Ledger, dynamodb: Any,
                                                  doorstep: list[dict[str, Any]]) -> None:
    events = [strip_chain(e) for e in doorstep]
    racer = RacingLedger(ledger, events[1:], races=MAX_ATTEMPTS, client=dynamodb, sleep=lambda _: None)
    with pytest.raises(LedgerConflict):
        racer.append(events[0])
    assert racer.reads == MAX_ATTEMPTS
    chain = ledger.read_chain(HH)
    assert len(chain) == MAX_ATTEMPTS and ledger.verify(HH) == []
    assert events[0]["source_event_id"] not in {e["source_event_id"] for e in chain}


def test_race_lost_to_the_same_event_is_a_duplicate(ledger: Ledger, dynamodb: Any,
                                                    doorstep: list[dict[str, Any]]) -> None:
    event = strip_chain(doorstep[0])
    racer = RacingLedger(ledger, [event], races=1, client=dynamodb, sleep=lambda _: None)
    result = racer.append(copy.deepcopy(event))
    assert result.status == DUPLICATE and result.envelope == doorstep[0]
    assert len(ledger.read_chain(HH)) == 1


@pytest.mark.parametrize("field", ["seq", "prev", "head"])
def test_chain_fields_are_assigned_by_the_ledger_only(ledger: Ledger, doorstep: list[dict[str, Any]],
                                                      field: str) -> None:
    with pytest.raises(LedgerError):
        ledger.append({**strip_chain(doorstep[0]), field: doorstep[0][field]})


@pytest.mark.parametrize(("field", "value"), [("received_at", 1.5), ("kind", "door.knock"),
                                              ("household", "nobody"), ("household", None),
                                              ("source_event_id", None), ("valid", {"lo": 2, "hi": 1})])
def test_invalid_envelopes_are_refused_before_any_write(ledger: Ledger, dynamodb: Any,
                                                        doorstep: list[dict[str, Any]], field: str,
                                                        value: Any) -> None:
    with pytest.raises(LedgerValidationError):
        ledger.append({**strip_chain(doorstep[0]), field: value})
    assert items(dynamodb) == []


def test_other_client_errors_propagate(doorstep: list[dict[str, Any]], aws: None) -> None:
    missing = Ledger("no-such-table", REGION, sleep=lambda _: None)
    with pytest.raises(ClientError):
        missing.append(strip_chain(doorstep[0]))


# ── verify detects tampering ──


def tamper(client: Any, seq: int, edit: Any) -> None:
    key = {"pk": {"S": HH}, "sk": {"S": seq_key(seq)}}
    item = client.get_item(TableName=TABLE, Key=key)["Item"]
    edit(item)
    client.put_item(TableName=TABLE, Item=item)


@pytest.fixture
def full(ledger: Ledger, doorstep: list[dict[str, Any]]) -> Ledger:
    for expected in doorstep:
        ledger.append(strip_chain(expected))
    assert ledger.verify(HH) == []
    return ledger


def test_verify_detects_a_tampered_envelope(full: Ledger, dynamodb: Any) -> None:
    def edit(item: dict[str, Any]) -> None:
        env = json.loads(item["envelope"]["S"])
        env["received_at"] += 1
        item["envelope"]["S"] = canonical(env).decode()

    tamper(dynamodb, 5, edit)
    assert any("seq 5: head does not match" in p for p in full.verify(HH))


def test_verify_detects_a_rehashed_tampered_envelope(full: Ledger, dynamodb: Any) -> None:
    def edit(item: dict[str, Any]) -> None:
        env = json.loads(item["envelope"]["S"])
        env["kind"] = "door.button"
        env["head"] = chain_head(env)
        item["envelope"]["S"] = canonical(env).decode()
        item["head"]["S"] = env["head"]

    tamper(dynamodb, 3, edit)
    assert any("seq 4" in p and "E-3" in p for p in full.verify(HH))


def test_verify_detects_a_deleted_entry(full: Ledger, dynamodb: Any) -> None:
    dynamodb.delete_item(TableName=TABLE, Key={"pk": {"S": HH}, "sk": {"S": seq_key(7)}})
    problems = full.verify(HH)
    assert any("E-2" in p for p in problems) and any("HEAD" in p for p in problems)


def test_verify_detects_a_float_injected_into_storage(full: Ledger, dynamodb: Any) -> None:
    def edit(item: dict[str, Any]) -> None:
        item["envelope"]["S"] = item["envelope"]["S"].replace('"v":1', '"v":1.0')

    tamper(dynamodb, 2, edit)
    assert any("E-5" in p for p in full.verify(HH))


def test_verify_detects_a_stale_head_item(full: Ledger, dynamodb: Any) -> None:
    dynamodb.update_item(TableName=TABLE, Key={"pk": {"S": HH}, "sk": {"S": "HEAD"}},
                         UpdateExpression="SET head_seq = :n", ExpressionAttributeValues={":n": {"N": "3"}})
    assert any("HEAD" in p for p in full.verify(HH))


def test_verify_detects_a_missing_dedup_claim(full: Ledger, dynamodb: Any, doorstep: list[dict[str, Any]]) -> None:
    key = dedup_key(doorstep[0]["source"], doorstep[0]["source_event_id"])
    dynamodb.delete_item(TableName=TABLE, Key={"pk": {"S": HH}, "sk": {"S": key}})
    assert any("E-6" in p for p in full.verify(HH))


# ── quarantine ──


def test_quarantine_keeps_the_delivery_out_of_the_chain(ledger: Ledger, dynamodb: Any,
                                                        doorstep: list[dict[str, Any]]) -> None:
    raw = b'{"meta": {}}'
    status = ledger.quarantine(household=HH, source="ring", received_at=1_792_486_801_372, raw=raw,
                               reason="src_time beyond max_skew_ms", source_event_id="req-1")
    assert status == QUARANTINED
    quarantined = items(dynamodb, "QUAR#")
    assert [i["sk"]["S"] for i in quarantined] == [quarantine_key(1_792_486_801_372, raw)]
    assert quarantined[0]["sk"]["S"].startswith("QUAR#1792486801372#")
    assert quarantined[0]["source_event_id"]["S"] == "req-1"
    assert ledger.read_chain(HH) == [] and ledger.verify(HH) == []
    ledger.append(strip_chain(doorstep[0]))
    assert ledger.verify(HH) == []


def test_read_chain_paginates(ledger: Ledger, doorstep: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    for expected in doorstep:
        ledger.append(strip_chain(expected))
    query = ledger.client.query

    def small_pages(**kwargs: Any) -> dict[str, Any]:
        return query(**kwargs, Limit=2)

    monkeypatch.setattr(ledger.client, "query", small_pages)
    assert ledger.read_chain(HH) == doorstep


# ── security-review regressions (docs/gates/G1_COMPLETION_LOG.md) ──


def test_m1_quarantine_claims_the_event_id(ledger: Ledger, dynamodb: Any, doorstep: list[dict[str, Any]]) -> None:
    event = strip_chain(doorstep[0])
    status = ledger.quarantine(household=HH, source="ring", source_event_id=event["source_event_id"],
                               received_at=event["received_at"], raw=b"raw", reason="skew")
    assert status == QUARANTINED
    result = ledger.append(event)
    assert result.status == DUPLICATE and result.envelope is None
    again = ledger.quarantine(household=HH, source="ring", source_event_id=event["source_event_id"],
                              received_at=event["received_at"] + 1, raw=b"raw", reason="skew")
    assert again == DUPLICATE
    assert ledger.read_chain(HH) == [] and len(items(dynamodb, "QUAR#")) == 1
    assert ledger.verify(HH) == []


def test_m1_quarantine_of_a_ledgered_event_writes_nothing(ledger: Ledger, dynamodb: Any,
                                                         doorstep: list[dict[str, Any]]) -> None:
    ledger.append(strip_chain(doorstep[0]))
    before = items(dynamodb)
    status = ledger.quarantine(household=HH, source="ring", source_event_id=doorstep[0]["source_event_id"],
                               received_at=1, raw=b"raw", reason="late copy")
    assert status == DUPLICATE and items(dynamodb) == before


@pytest.mark.parametrize("event_id", [None, "", chr(0xD800)], ids=["none", "empty", "surrogate"])
def test_l8_quarantine_requires_a_unicode_event_id(ledger: Ledger, dynamodb: Any, event_id: Any) -> None:
    with pytest.raises(LedgerValidationError):
        ledger.quarantine(household=HH, source="ring", source_event_id=event_id, received_at=1, raw=b"r",
                          reason="x")
    assert items(dynamodb) == []


def test_l3_orphan_claim_left_by_a_deleted_entry_is_reported(full: Ledger, dynamodb: Any,
                                                             doorstep: list[dict[str, Any]]) -> None:
    last, prior = doorstep[-1], doorstep[-2]
    dynamodb.delete_item(TableName=TABLE, Key={"pk": {"S": HH}, "sk": {"S": seq_key(last["seq"])}})
    dynamodb.put_item(TableName=TABLE, Item={"pk": {"S": HH}, "sk": {"S": "HEAD"},
                                             "head_seq": {"N": str(prior["seq"])}, "head": {"S": prior["head"]}})
    assert any("E-6" in p for p in full.verify(HH))
    with pytest.raises(LedgerError):  # a redelivery must not be swallowed as a silent duplicate
        full.append(strip_chain(last))


def test_l3_planted_claim_is_reported_and_never_absorbs_silently(full: Ledger, dynamodb: Any,
                                                                 doorstep: list[dict[str, Any]]) -> None:
    planted = dict(strip_chain(doorstep[0]), source_event_id="r-planted")
    dynamodb.put_item(TableName=TABLE, Item={"pk": {"S": HH}, "sk": {"S": dedup_key("ring", "r-planted")},
                                             "seq": {"N": "999"}})
    assert any("seq 999" in p for p in full.verify(HH))
    with pytest.raises(LedgerError):
        full.append(planted)


def test_l3_claim_naming_nothing_is_reported(full: Ledger, dynamodb: Any) -> None:
    dynamodb.put_item(TableName=TABLE, Item={"pk": {"S": HH}, "sk": {"S": dedup_key("ring", "r-x")}})
    assert any("neither" in p for p in full.verify(HH))


def test_l4_duplicate_keys_in_storage_are_reported(full: Ledger, dynamodb: Any) -> None:
    def edit(item: dict[str, Any]) -> None:
        text = item["envelope"]["S"]
        item["envelope"]["S"] = text.replace('"kind":"door.motion"', '"kind":"door.button","kind":"door.motion"')

    tamper(dynamodb, 1, edit)
    assert any("unreadable" in p for p in full.verify(HH))


def test_l4_non_canonical_storage_is_reported(full: Ledger, dynamodb: Any) -> None:
    def spaced(item: dict[str, Any]) -> None:
        item["envelope"]["S"] = item["envelope"]["S"].replace('"v":1', '"v": 1')

    tamper(dynamodb, 2, spaced)
    assert any("not the envelope's HW-JCS bytes" in p for p in full.verify(HH))


@pytest.mark.parametrize("edit", [
    lambda text: text.replace('"seq":2', '"seq":2.0'),
    lambda text: text.replace('"source":"bee"', '"source":["bee"]').replace('"source":"ring"', '"source":["ring"]'),
    lambda text: text.replace('"v":1', '"v":' + "9" * 5000),
    lambda text: "[]",
    lambda text: "not json",
], ids=["float-seq", "list-source", "huge-int", "array", "garbage"])
def test_l5_verify_reports_instead_of_raising(full: Ledger, dynamodb: Any, edit: Any) -> None:
    def apply(item: dict[str, Any]) -> None:
        item["envelope"]["S"] = edit(item["envelope"]["S"])

    tamper(dynamodb, 2, apply)
    assert full.verify(HH)


def test_l5_dedup_claim_without_seq_does_not_raise(full: Ledger, dynamodb: Any,
                                                   doorstep: list[dict[str, Any]]) -> None:
    key = dedup_key(doorstep[0]["source"], doorstep[0]["source_event_id"])
    dynamodb.put_item(TableName=TABLE, Item={"pk": {"S": HH}, "sk": {"S": key}})
    assert full.verify(HH)
