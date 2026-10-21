"""Decision records, digested with `hw-decision-v1` and signed by KMS (blueprint §9, C-6).

    record  = {episode, decision, at_seq, ledger_head, belief_seq, lambda_mnats, reasons, thresholds_mnats,
               params_digest, extractor_model_ids, cross_terms}
    bytes   = HW-JCS(record)                                  what is signed, and all a verifier needs to keep
    digest  = sha256:hex(SHA-256("hw-decision-v1" || 0x00 || bytes))
    signed  = {bytes_b64, sig_b64, key_id}                    ECDSA P-256 over `bytes`, DER signature as KMS returns it

A verifier checks the signature over the bytes first and parses them second; it never re-canonicalizes (C-6).
`params_digest` is the SHA-256 of the `hearthward:sprt` block as sorted, compact JSON: the block holds floats, so it
is digested as configuration and not as an HW-JCS document.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Protocol

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_der_public_key

from hearthward.contracts.canonical import ProfileError, canonical

DECISION_TAG = b"hw-decision-v1"
DECISION_FIELDS = ("episode", "decision", "at_seq", "ledger_head", "belief_seq", "lambda_mnats", "reasons",
                   "thresholds_mnats", "params_digest", "extractor_model_ids", "cross_terms")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_EPISODE = re.compile(r"ep_[0-9A-HJKMNP-TV-Z]{26}")
_DECISION = re.compile(r"[A-Z][A-Z_]{1,31}")


class AttestationError(ValueError):
    """A record that must not be signed."""


class Signer(Protocol):
    key_id: str

    def sign(self, message: bytes) -> bytes:
        """A DER-encoded ECDSA (SHA-256) signature over `message`."""


class KmsSigner:
    """`client` is a boto3 KMS client; `key_id` the ARN or id of the `attest-signer` ECC_NIST_P256 key."""

    def __init__(self, client: Any, key_id: str) -> None:
        self._client, self.key_id = client, key_id

    def sign(self, message: bytes) -> bytes:
        reply = self._client.sign(KeyId=self.key_id, Message=message, MessageType="RAW",
                                  SigningAlgorithm="ECDSA_SHA_256")
        return reply["Signature"]

    def public_key_der(self) -> bytes:
        """The SubjectPublicKeyInfo a verifier needs, re-encoded so it is P-256 and nothing else."""
        key = load_der_public_key(self._client.get_public_key(KeyId=self.key_id)["PublicKey"])
        return key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def params_digest(sprt: Mapping[str, Any]) -> str:
    """SHA-256 of the `hearthward:sprt` block serialized as sorted, compact JSON (§9)."""
    text = json.dumps(sprt, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(text.encode("ascii")).hexdigest()


def _is_int(value: Any) -> bool:
    return type(value) is int


def validate_decision(record: Mapping[str, Any]) -> None:
    """The shape of §9. Raises `AttestationError`; the HW-JCS profile (integers only) is enforced when serializing."""
    if not isinstance(record, Mapping) or set(record) != set(DECISION_FIELDS):
        raise AttestationError(f"a decision record has exactly the fields {list(DECISION_FIELDS)}")
    problems = []
    if not isinstance(record["episode"], str) or not _EPISODE.fullmatch(record["episode"]):
        problems.append("episode")
    if not isinstance(record["decision"], str) or not _DECISION.fullmatch(record["decision"]):
        problems.append("decision")
    for name in ("at_seq", "belief_seq"):
        if not _is_int(record[name]) or record[name] < 0:
            problems.append(name)
    if not _is_int(record["lambda_mnats"]):
        problems.append("lambda_mnats")
    for name in ("ledger_head", "params_digest"):
        if not isinstance(record[name], str) or not _DIGEST.fullmatch(record[name]):
            problems.append(name)
    thresholds = record["thresholds_mnats"]
    if not isinstance(thresholds, Mapping) or not thresholds or not all(_is_int(v) for v in thresholds.values()):
        problems.append("thresholds_mnats")
    for name in ("reasons", "extractor_model_ids"):
        if not isinstance(record[name], list) or not all(isinstance(v, str) for v in record[name]):
            problems.append(name)
    terms = record["cross_terms"]
    if not (isinstance(terms, list) and all(isinstance(v, str) for v in terms)
            or isinstance(terms, Mapping) and all(_is_int(v) for v in terms.values())):
        problems.append("cross_terms")
    if problems:
        raise AttestationError("invalid decision record: " + ", ".join(problems))


def decision_bytes(record: Mapping[str, Any]) -> bytes:
    """The canonical bytes that are signed. Floats, NaN and keys outside the profile cannot be serialized (C-2)."""
    validate_decision(record)
    try:
        return canonical(dict(record))
    except ProfileError as exc:
        raise AttestationError(f"decision record is outside HW-JCS: {exc}") from exc


def digest_bytes(tag: bytes, data: bytes) -> str:
    """`sha256:` + SHA-256(tag || 0x00 || data) over bytes that are already canonical."""
    return "sha256:" + hashlib.sha256(tag + b"\x00" + data).hexdigest()


def decision_digest(data: bytes) -> str:
    return digest_bytes(DECISION_TAG, data)


def sign_bytes(data: bytes, signer: Signer) -> dict[str, str]:
    """`{bytes_b64, sig_b64, key_id}` for canonical `data` (C-6)."""
    return {"bytes_b64": base64.b64encode(data).decode("ascii"),
            "sig_b64": base64.b64encode(signer.sign(data)).decode("ascii"), "key_id": signer.key_id}


def attest_decision(record: Mapping[str, Any], signer: Signer) -> dict[str, str]:
    """Sign a decision record. The record is immutable once decided (BT-4): sign it once and keep the bytes."""
    return sign_bytes(decision_bytes(record), signer)
