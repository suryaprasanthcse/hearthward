"""Challenge (`Nonces`) and proof (`ProofLedger`) stores for the broker, in memory and on DynamoDB.

Every state change that must happen once is a conditional write: adding an approval, and consuming a challenge
(HX-04). The DynamoDB versions use the key layout of the CDK stack: `HearthwardNonces` has only the partition key
`pk` and a TTL on `expires_at`; `HearthwardProofLedger` has `pk` (household) and `sk` (`PROOF#<proof_hash>`).
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from botocore.exceptions import ClientError

CONDITION_FAILED = "ConditionalCheckFailedException"
TTL_GRACE_S = 3600  # keep an expired challenge long enough to answer "expired" rather than "unknown"


class NonceStore(Protocol):
    def put(self, challenge_id: str, record: dict[str, Any]) -> None:
        """Store a new challenge; raises if the id exists."""

    def get(self, challenge_id: str) -> dict[str, Any] | None:
        """The record with `approvals` (a sorted list of member ids) and `consumed` (bool)."""

    def add_approval(self, challenge_id: str, member_id: str) -> bool:
        """Record an approval unless this member already approved or the challenge is consumed (conditional)."""

    def consume(self, challenge_id: str) -> bool:
        """Mark the challenge used. True exactly once (a conditional write)."""


class ProofLedger(Protocol):
    def record(self, household: str, proof_hash: str, record: dict[str, Any]) -> None: ...


class InMemoryNonceStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self.fail_writes = False  # lets tests model a nonce store outage

    def put(self, challenge_id: str, record: dict[str, Any]) -> None:
        if self.fail_writes:
            raise OSError("nonce store unavailable")
        if challenge_id in self._records:
            raise KeyError(f"challenge {challenge_id} already exists")
        self._records[challenge_id] = {**record, "approvals": [], "consumed": False}

    def get(self, challenge_id: str) -> dict[str, Any] | None:
        record = self._records.get(challenge_id)
        return None if record is None else {**record, "approvals": sorted(record["approvals"])}

    def add_approval(self, challenge_id: str, member_id: str) -> bool:
        record = self._records.get(challenge_id)
        if record is None or record["consumed"] or member_id in record["approvals"]:
            return False
        record["approvals"].append(member_id)
        return True

    def consume(self, challenge_id: str) -> bool:
        record = self._records.get(challenge_id)
        if record is None or record["consumed"]:
            return False
        record["consumed"] = True
        return True


class InMemoryProofLedger:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def record(self, household: str, proof_hash: str, record: dict[str, Any]) -> None:
        self.records[proof_hash] = {"household": household, **record}


class DynamoNonceStore:
    """`client` is a boto3 DynamoDB client; `table` is `HearthwardNonces`."""

    def __init__(self, client: Any, table: str) -> None:
        self.client, self.table = client, table

    @staticmethod
    def _key(challenge_id: str) -> dict[str, Any]:
        return {"pk": {"S": f"NONCE#{challenge_id}"}}

    def put(self, challenge_id: str, record: dict[str, Any]) -> None:
        item = {**self._key(challenge_id), "record": {"S": json.dumps(record, sort_keys=True, separators=(",", ":"))},
                "expires_at": {"N": str(record["exp"] // 1000 + TTL_GRACE_S)}}
        self.client.put_item(TableName=self.table, Item=item, ConditionExpression="attribute_not_exists(pk)")

    def get(self, challenge_id: str) -> dict[str, Any] | None:
        item = self.client.get_item(TableName=self.table, Key=self._key(challenge_id), ConsistentRead=True).get("Item")
        if item is None:
            return None
        return {**json.loads(item["record"]["S"]), "approvals": sorted(item.get("approvals", {}).get("SS", [])),
                "consumed": "consumed" in item}

    def add_approval(self, challenge_id: str, member_id: str) -> bool:
        try:
            self.client.update_item(
                TableName=self.table, Key=self._key(challenge_id), UpdateExpression="ADD approvals :m",
                ConditionExpression="attribute_exists(pk) AND attribute_not_exists(#c) "
                                    "AND NOT contains(approvals, :one)",
                ExpressionAttributeNames={"#c": "consumed"},  # CONSUMED is a DynamoDB reserved word
                ExpressionAttributeValues={":m": {"SS": [member_id]}, ":one": {"S": member_id}})
        except ClientError as exc:
            if exc.response["Error"]["Code"] == CONDITION_FAILED:
                return False
            raise
        return True

    def consume(self, challenge_id: str) -> bool:
        try:
            self.client.update_item(
                TableName=self.table, Key=self._key(challenge_id), UpdateExpression="SET #c = :t",
                ConditionExpression="attribute_exists(pk) AND attribute_not_exists(#c)",
                ExpressionAttributeNames={"#c": "consumed"},
                ExpressionAttributeValues={":t": {"BOOL": True}})
        except ClientError as exc:
            if exc.response["Error"]["Code"] == CONDITION_FAILED:
                return False
            raise
        return True


class DynamoProofLedger:
    def __init__(self, client: Any, table: str) -> None:
        self.client, self.table = client, table

    def record(self, household: str, proof_hash: str, record: dict[str, Any]) -> None:
        self.client.put_item(
            TableName=self.table,
            Item={"pk": {"S": household}, "sk": {"S": f"PROOF#{proof_hash}"},
                  "record": {"S": json.dumps(record, sort_keys=True, separators=(",", ":"))}},
            ConditionExpression="attribute_not_exists(pk)")
