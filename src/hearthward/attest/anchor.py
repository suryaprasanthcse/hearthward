"""Merkle anchors over the households' ledger heads (blueprint §9, cut-able under C4).

Every few minutes a Merkle root over `(household, seq, head)` leaves is signed and written to S3 Object Lock in
compliance mode, so a head that was anchored can later be shown not to have been rewritten. The tree is RFC 6962's,
with the `hw-anchor-v1` tag in every hash:

    leaf = SHA-256(tag || 0x00 || 0x00 || HW-JCS({household, seq, head}))
    node = SHA-256(tag || 0x00 || 0x01 || left || right)
    MTH(n leaves) splits at the largest power of two below n

Leaves are sorted by household, so the same heads always give the same root. `tools/verify_attestation.py` repeats
this arithmetic with the standard library alone; `tests/unit/test_attest.py` holds the two implementations equal.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from hearthward.attest.sign import Signer, digest_bytes, sign_bytes
from hearthward.contracts.canonical import canonical

ANCHOR_TAG = b"hw-anchor-v1"
LEAF, NODE = b"\x00", b"\x01"
DEFAULT_RETENTION_DAYS = 365


def _hash(kind: bytes, *parts: bytes) -> bytes:
    return hashlib.sha256(ANCHOR_TAG + b"\x00" + kind + b"".join(parts)).digest()


def leaf_hash(household: str, seq: int, head: str) -> bytes:
    return _hash(LEAF, canonical({"household": household, "seq": seq, "head": head}))


def _split(n: int) -> int:
    """The largest power of two strictly below n."""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    if not leaves:
        raise ValueError("an anchor needs at least one household")
    if len(leaves) == 1:
        return leaves[0]
    k = _split(len(leaves))
    return _hash(NODE, merkle_root(leaves[:k]), merkle_root(leaves[k:]))


def audit_path(leaves: Sequence[bytes], index: int) -> list[bytes]:
    """The sibling hashes from the leaf up to the root (RFC 6962 section 2.1.1)."""
    if not 0 <= index < len(leaves):
        raise IndexError(index)
    if len(leaves) == 1:
        return []
    k = _split(len(leaves))
    if index < k:
        return [*audit_path(leaves[:k], index), merkle_root(leaves[k:])]
    return [*audit_path(leaves[k:], index - k), merkle_root(leaves[:k])]


def root_from_path(leaf: bytes, index: int, size: int, path: Sequence[bytes]) -> bytes | None:
    """Recompute the root from a leaf and its audit path; None if the path has the wrong length."""
    if not 0 <= index < size:
        return None
    if size == 1:
        return leaf if not path else None
    if not path:
        return None
    k = _split(size)
    if index < k:
        sub = root_from_path(leaf, index, k, path[:-1])
        return None if sub is None else _hash(NODE, sub, path[-1])
    sub = root_from_path(leaf, index - k, size - k, path[:-1])
    return None if sub is None else _hash(NODE, path[-1], sub)


def build_anchor(heads: Mapping[str, tuple[int, str]], at_ms: int) -> dict[str, Any]:
    """The anchor document: when, how many, the root, and the sorted leaves it covers (all HW-JCS, integers only)."""
    if type(at_ms) is not int or at_ms < 0:
        raise ValueError("at_ms must be a non-negative integer")
    leaves = [{"household": hh, "seq": seq, "head": head} for hh, (seq, head) in sorted(heads.items())]
    root = merkle_root([leaf_hash(**leaf) for leaf in leaves])
    return {"v": 1, "at_ms": at_ms, "count": len(leaves), "root": "sha256:" + root.hex(), "leaves": leaves}


def anchor_bytes(anchor: Mapping[str, Any]) -> bytes:
    return canonical(dict(anchor))


def anchor_digest(data: bytes) -> str:
    return digest_bytes(ANCHOR_TAG, data)


def sign_anchor(anchor: Mapping[str, Any], signer: Signer) -> dict[str, str]:
    """`{bytes_b64, sig_b64, key_id}` over the anchor document's canonical bytes (C-6)."""
    return sign_bytes(anchor_bytes(anchor), signer)


def inclusion(anchor: Mapping[str, Any], household: str) -> dict[str, Any]:
    """Everything needed to show one household's head is under the anchor's root."""
    leaves = anchor["leaves"]
    index = next((i for i, leaf in enumerate(leaves) if leaf["household"] == household), None)
    if index is None:
        raise KeyError(household)
    hashes = [leaf_hash(**leaf) for leaf in leaves]
    return {"index": index, "size": len(leaves), "path": [p.hex() for p in audit_path(hashes, index)]}


def object_key(at_ms: int) -> str:
    moment = datetime.fromtimestamp(at_ms / 1000, tz=UTC)
    return f"anchors/{moment:%Y/%m/%d}/{at_ms}.json"


class AnchorWriter:
    """Writes signed anchors to the Object Lock bucket (`HW_ANCHOR_BUCKET`), never overwriting one.

    `client` is a boto3 S3 client. The bucket must have Object Lock enabled; each object is locked in COMPLIANCE
    mode until `retention_days` have passed, so not even the account root can shorten it.
    """

    def __init__(self, client: Any, bucket: str, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        self.client, self.bucket, self.retention_days = client, bucket, retention_days

    def put(self, anchor: Mapping[str, Any], signed: Mapping[str, str], now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        if base64.b64decode(signed["bytes_b64"]) != anchor_bytes(anchor):
            raise ValueError("the signed bytes are not this anchor's canonical bytes")
        body = json.dumps({"anchor": dict(anchor), "attestation": dict(signed)}, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        key = object_key(anchor["at_ms"])
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType="application/json", IfNoneMatch="*",
            ChecksumAlgorithm="SHA256", ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=now + timedelta(days=self.retention_days))
        return key
