"""The per-household hash-chained event ledger on one DynamoDB table (blueprint §4.2, E-2, E-3, E-6, BT-5).

Items share partition key `pk` = household and differ by sort key `sk`:

    SEQ#%012d                    one envelope, stored as its HW-JCS canonical string (never DynamoDB numbers)
    HEAD                         head_seq and head of the newest envelope
    DEDUP#<source>#<sha256 hex>  claims (household, source, source_event_id) once: for a sequence (`seq`) or for a
                                 quarantine record (`quar_sk`), so a redelivery is absorbed either way (E-6)
    QUAR#<received_at>#<sha256>  a quarantined delivery: kept, never ledgered or ordered (BT-5)

An append is one TransactWriteItems: put SEQ#n and DEDUP if absent, and move HEAD from n - 1 to n (or create it
at genesis). A failed DEDUP condition means the event is already ledgered or quarantined; a failed SEQ or HEAD
condition means another writer won the race, so the head is re-read and the append retried with jitter. A
quarantine is one TransactWriteItems too: put DEDUP and QUAR# if absent.

Envelopes that can never be valid raise LedgerValidationError, so transports can quarantine them instead of
retrying; every other LedgerError is transient or a fault of the store.
"""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import ClientError

from hearthward.contracts.canonical import ProfileError, canonical, is_unicode, parse_strict
from hearthward.contracts.envelope import CHAIN_FIELDS, ZERO_DIGEST, chain_head, validate_envelope, verify_chain

APPENDED, DUPLICATE, QUARANTINED = "APPENDED", "DUPLICATE", "QUARANTINED"
MAX_ATTEMPTS = 8
HEAD_SK = "HEAD"
SEQ_PREFIX = "SEQ#"
DEDUP_PREFIX = "DEDUP#"
QUAR_PREFIX = "QUAR#"
_RETRYABLE = frozenset({"ConditionalCheckFailed", "TransactionConflict"})


class LedgerError(Exception):
    """The ledger cannot accept or return an envelope."""


class LedgerValidationError(LedgerError):
    """The envelope can never be ledgered (schema, E-1, E-5, formats); retrying will not help."""


class LedgerConflict(LedgerError):
    """Every attempt lost the race for the household's head."""


@dataclass(frozen=True)
class AppendResult:
    status: str  # APPENDED or DUPLICATE
    envelope: dict[str, Any] | None  # the ledgered envelope; for a duplicate, the one already ledgered (None when the
    # earlier delivery was quarantined)


def seq_key(seq: int) -> str:
    return f"{SEQ_PREFIX}{seq:012d}"


def dedup_key(source: str, source_event_id: str) -> str:
    return f"{DEDUP_PREFIX}{source}#{hashlib.sha256(source_event_id.encode('utf-8')).hexdigest()}"


def quarantine_key(received_at: int, raw: bytes) -> str:
    return f"{QUAR_PREFIX}{received_at}#{hashlib.sha256(raw).hexdigest()}"


def precheck(envelope: Any) -> list[str]:
    """Problems that make an envelope without chain fields unledgerable, empty when only the chain is missing."""
    if not isinstance(envelope, dict):
        return ["an envelope is an object"]
    present = [f for f in CHAIN_FIELDS if f in envelope]
    if present:
        return [f"the ledger assigns {', '.join(present)}; the envelope must not carry them"]
    # placeholders stand in for seq, prev and head so the rest is validated exactly as it will be stored
    return validate_envelope({**envelope, "seq": 1, "prev": ZERO_DIGEST, "head": ZERO_DIGEST})


class Ledger:
    """One DynamoDB table; in production `table_name` comes from HW_LEDGER_TABLE and `region` from AWS_REGION."""

    def __init__(self, table_name: str, region: str, *, client: Any = None,
                 sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None) -> None:
        self.table = table_name
        self.client = client if client is not None else boto3.client("dynamodb", region_name=region)
        self._sleep = sleep
        self._rng = rng or random.Random()

    # ── writes ──

    def append(self, envelope: dict[str, Any]) -> AppendResult:
        """Assign seq, prev and head to an envelope without chain fields and ledger it exactly once."""
        problems = precheck(envelope)
        if problems:
            raise LedgerValidationError("invalid envelope: " + "; ".join(problems))
        household, source, event_id = envelope["household"], envelope["source"], envelope["source_event_id"]
        for attempt in range(MAX_ATTEMPTS):
            head_seq, head = self._read_head(household)
            entry = {**envelope, "seq": head_seq + 1, "prev": head}
            entry["head"] = chain_head(entry)
            problems = validate_envelope(entry)
            if problems:  # unreachable after precheck unless the stored head is corrupt
                raise LedgerError("invalid chained envelope: " + "; ".join(problems))
            try:
                self.client.transact_write_items(TransactItems=self._append_items(entry, head_seq))
                return AppendResult(APPENDED, entry)
            except ClientError as exc:
                codes = self._cancellation(exc, 3)  # in TransactItems order: SEQ, DEDUP, HEAD
                if codes[1] == "ConditionalCheckFailed":
                    return AppendResult(DUPLICATE, self._claimed(household, source, event_id))
                if not set(codes) <= _RETRYABLE | {"None"}:
                    raise LedgerError(f"append cancelled: {codes}") from exc
            self._sleep(self._rng.uniform(0, min(0.05, 0.002 * 2**attempt)))
        raise LedgerConflict(f"{household}: lost the head race {MAX_ATTEMPTS} times")

    @staticmethod
    def _cancellation(exc: ClientError, expected: int) -> list[str]:
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise exc
        codes = [r.get("Code", "None") for r in exc.response.get("CancellationReasons", [])]
        if len(codes) != expected:
            raise LedgerError(f"unexpected cancellation reasons {codes}") from exc
        return codes

    def _append_items(self, entry: dict[str, Any], head_seq: int) -> list[dict[str, Any]]:
        household, n = entry["household"], entry["seq"]
        absent = "attribute_not_exists(pk)"
        head_update: dict[str, Any] = {
            "TableName": self.table,
            "Key": {"pk": {"S": household}, "sk": {"S": HEAD_SK}},
            "UpdateExpression": "SET head_seq = :n, head = :h",
            "ExpressionAttributeValues": {":n": {"N": str(n)}, ":h": {"S": entry["head"]}},
        }
        if head_seq == 0:
            head_update["ConditionExpression"] = absent
        else:
            head_update["ConditionExpression"] = "head_seq = :prev_n"
            head_update["ExpressionAttributeValues"][":prev_n"] = {"N": str(head_seq)}
        return [
            {"Put": {"TableName": self.table, "ConditionExpression": absent, "Item": {
                "pk": {"S": household}, "sk": {"S": seq_key(n)}, "seq": {"N": str(n)},
                "head": {"S": entry["head"]}, "envelope": {"S": canonical(entry).decode("utf-8")}}}},
            {"Put": {"TableName": self.table, "ConditionExpression": absent, "Item": {
                "pk": {"S": household}, "sk": {"S": dedup_key(entry["source"], entry["source_event_id"])},
                "seq": {"N": str(n)}, "source_event_id": {"S": entry["source_event_id"]}}}},
            {"Update": head_update},
        ]

    def quarantine(self, *, household: str, source: str, source_event_id: str, received_at: int, raw: bytes,
                   reason: str) -> str:
        """Keep a delivery out of the ledger and belief (BT-5) but on record, and claim its source_event_id so a
        redelivery is absorbed rather than ledgered. Returns QUARANTINED, or DUPLICATE when the id was already
        ledgered or quarantined (nothing is written then)."""
        if type(source_event_id) is not str or not source_event_id or not is_unicode(source_event_id):
            raise LedgerValidationError("a quarantined delivery needs a non-empty Unicode source_event_id")
        absent = "attribute_not_exists(pk)"
        quar_sk = quarantine_key(received_at, raw)
        claim = {"pk": {"S": household}, "sk": {"S": dedup_key(source, source_event_id)},
                 "quar_sk": {"S": quar_sk}, "source_event_id": {"S": source_event_id}}
        record = {"pk": {"S": household}, "sk": {"S": quar_sk}, "source": {"S": source},
                  "source_event_id": {"S": source_event_id}, "received_at": {"N": str(received_at)},
                  "reason": {"S": reason[:500]}, "raw_digest": {"S": "sha256:" + hashlib.sha256(raw).hexdigest()}}
        try:
            self.client.transact_write_items(TransactItems=[
                {"Put": {"TableName": self.table, "ConditionExpression": absent, "Item": claim}},
                {"Put": {"TableName": self.table, "ConditionExpression": absent, "Item": record}},
            ])
        except ClientError as exc:
            claim_code, _ = self._cancellation(exc, 2)
            if claim_code == "ConditionalCheckFailed":
                return DUPLICATE
            raise LedgerError(f"quarantine cancelled: {claim_code}") from exc
        return QUARANTINED

    # ── reads ──

    def _get(self, household: str, sk: str) -> dict[str, Any] | None:
        return self.client.get_item(TableName=self.table, Key={"pk": {"S": household}, "sk": {"S": sk}},
                                    ConsistentRead=True).get("Item")

    def _read_head(self, household: str) -> tuple[int, str]:
        item = self._get(household, HEAD_SK)
        if item is None:
            return 0, ZERO_DIGEST
        return int(item["head_seq"]["N"]), item["head"]["S"]

    def _claimed(self, household: str, source: str, event_id: str) -> dict[str, Any] | None:
        """The envelope that holds a claim, None when a quarantine holds it. A claim pointing nowhere is an error:
        reporting it as a duplicate would silently swallow the delivery."""
        claim = self._get(household, dedup_key(source, event_id))
        if claim is not None and "quar_sk" in claim and "seq" not in claim:
            return None
        if claim is None or "seq" not in claim:
            raise LedgerError(f"{household}: a DEDUP claim for {source} blocks the append but names no entry")
        item = self._get(household, seq_key(int(claim["seq"]["N"])))
        if item is None:
            raise LedgerError(f"{household}: a DEDUP claim names {seq_key(int(claim['seq']['N']))}, which is missing")
        return parse_strict(item["envelope"]["S"].encode("utf-8"))

    def _items(self, household: str, prefix: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {
            "TableName": self.table, "ConsistentRead": True,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
            "ExpressionAttributeValues": {":pk": {"S": household}, ":prefix": {"S": prefix}},
        }
        while True:
            page = self.client.query(**kwargs)
            items += page.get("Items", [])
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def read_chain(self, household: str) -> list[dict[str, Any]]:
        """The household's envelopes in sequence order, parsed strictly from their canonical strings."""
        return [parse_strict(item["envelope"]["S"].encode("utf-8")) for item in self._items(household, SEQ_PREFIX)]

    def verify(self, household: str) -> list[str]:
        """Problems with the stored chain, empty when it is intact. Never raises on tampered items: every stored
        string is canonical HW-JCS, every envelope valid and every head recomputed; sort keys, stored heads and the
        HEAD item agree with the envelopes; and DEDUP claims and entries match one to one (E-6)."""
        problems: list[str] = []
        envelopes: list[Any] = []
        for item in self._items(household, SEQ_PREFIX):
            sk = item["sk"]["S"]
            try:
                stored = item["envelope"]["S"].encode("utf-8")
                env = parse_strict(stored)
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"{sk}: unreadable envelope ({type(exc).__name__})")
                env = None
            envelopes.append(env)
            if not isinstance(env, dict):
                continue
            try:
                if canonical(env) != stored:
                    problems.append(f"{sk}: stored string is not the envelope's HW-JCS bytes")
            except ProfileError:
                pass  # validate_envelope reports it as E-5
            problems += [f"{sk}: {p}" for p in validate_envelope(env)]
            if type(env.get("seq")) is not int or sk != seq_key(env["seq"]):
                problems.append(f"{sk}: holds seq {env.get('seq')!r}")
            if item.get("head", {}).get("S") != env.get("head"):
                problems.append(f"{sk}: stored head differs from the envelope's head")
            if env.get("household") != household:
                problems.append(f"{sk}: envelope belongs to {env.get('household')!r}")
        problems += verify_chain(envelopes)
        try:
            head_seq, head = self._read_head(household)
        except (KeyError, TypeError, ValueError):
            problems.append("HEAD: unreadable")
            head_seq, head = -1, ""
        last = envelopes[-1] if envelopes else None
        last_head = last.get("head") if isinstance(last, dict) else ZERO_DIGEST
        if (head_seq, head) != (len(envelopes), last_head):
            problems.append(f"HEAD is ({head_seq}, {head[:19]}), the chain ends at ({len(envelopes)}, "
                            f"{str(last_head)[:19]})")
        problems += self._verify_claims(household, envelopes)
        return problems

    def _verify_claims(self, household: str, envelopes: list[Any]) -> list[str]:
        problems: list[str] = []
        expected = {}
        for env in envelopes:
            if isinstance(env, dict) and type(env.get("seq")) is int:
                try:
                    expected[dedup_key(str(env.get("source")), str(env.get("source_event_id")))] = env["seq"]
                except UnicodeEncodeError:
                    problems.append(f"{seq_key(env['seq'])}: source_event_id has no UTF-8 form")
        quarantined = {item["sk"]["S"] for item in self._items(household, QUAR_PREFIX)}
        claimed = set()
        for claim in self._items(household, DEDUP_PREFIX):
            sk = claim["sk"]["S"]
            claimed.add(sk)
            if "seq" in claim:
                if expected.get(sk) is None or claim["seq"].get("N") != str(expected[sk]):
                    problems.append(f"{sk}: claims seq {claim['seq'].get('N')}, which holds no such event (E-6)")
            elif "quar_sk" in claim:
                if claim["quar_sk"].get("S") not in quarantined:
                    problems.append(f"{sk}: claims a quarantine record that does not exist")
                if sk in expected:
                    problems.append(f"{sk}: an event is both quarantined and ledgered (BT-5)")
            else:
                problems.append(f"{sk}: names neither a sequence nor a quarantine record")
        for sk, seq in expected.items():
            if sk not in claimed:
                problems.append(f"{seq_key(seq)}: no matching DEDUP claim (E-6)")
        return problems
