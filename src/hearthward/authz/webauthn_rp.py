"""HEARTHWARD's own WebAuthn relying party (blueprint §5.5, step 5).

Cognito's passkeys cannot sign a caller-chosen challenge, so approvals use this relying party instead. It verifies a
`navigator.credentials.get` assertion against a registered passkey and ties it to one action:

    challenge in clientDataJSON == the action hash                        (HX-03)
    rpIdHash == SHA-256(rp_id), user present and user verified            (HX-02)
    ES256 signature over authenticatorData || SHA-256(clientDataJSON)     (HX-02)
    signCount strictly greater than the stored counter                    (HX-04)

Only ES256 credentials (P-256, as the SPKI DER bytes `getPublicKey()` returns) are accepted. A passkey that always
reports a zero counter can never satisfy HX-04 and is refused by design.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

FLAG_UP, FLAG_UV = 0x01, 0x04
MAX_CLIENT_DATA = 4096
MAX_AUTH_DATA = 4096


class WebAuthnError(Exception):
    """An assertion that must not be accepted. `rule` is the HX rule it violates; `code` says why."""

    def __init__(self, rule: str, code: str) -> None:
        super().__init__(f"{rule}: {code}")
        self.rule, self.code = rule, code


@dataclass(frozen=True)
class Credential:
    credential_id: str  # base64url, as the browser reports it
    member_id: str
    public_key_der: bytes  # SubjectPublicKeyInfo, ES256 (P-256)
    sign_count: int = 0

    def public_key(self) -> ec.EllipticCurvePublicKey:
        key = load_der_public_key(self.public_key_der)
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            raise WebAuthnError("HX-02", "credential is not an ES256 (P-256) key")
        return key


@dataclass(frozen=True)
class Assertion:
    credential_id: str
    client_data_json: bytes
    authenticator_data: bytes
    signature: bytes  # DER ECDSA, as the authenticator returns it


@dataclass(frozen=True)
class Verified:
    member_id: str
    credential_id: str
    sign_count: int


class CredentialStore(Protocol):
    def get(self, credential_id: str) -> Credential | None: ...

    def advance_counter(self, credential_id: str, new_count: int) -> bool:
        """Set the counter to `new_count` only if it is greater than the stored one (a conditional write)."""


class InMemoryCredentialStore:
    def __init__(self, credentials: Collection[Credential] = ()) -> None:
        self._by_id = {c.credential_id: c for c in credentials}

    def add(self, credential: Credential) -> None:
        self._by_id[credential.credential_id] = credential

    def get(self, credential_id: str) -> Credential | None:
        return self._by_id.get(credential_id)

    def advance_counter(self, credential_id: str, new_count: int) -> bool:
        current = self._by_id.get(credential_id)
        if current is None or new_count <= current.sign_count:
            return False
        self._by_id[credential_id] = Credential(current.credential_id, current.member_id, current.public_key_der,
                                                new_count)
        return True


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str) or not text.isascii():
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


class RelyingParty:
    def __init__(self, rp_id: str, origins: Collection[str]) -> None:
        if not rp_id or not origins:
            raise ValueError("a relying party needs an RP ID and at least one origin")
        self.rp_id, self.origins = rp_id, frozenset(origins)
        self._rp_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()

    def verify(self, assertion: Assertion, *, expected_challenge: bytes, credential: Credential) -> Verified:
        """Raises `WebAuthnError` unless the assertion is a fresh, user-verified signature by `credential` over
        exactly `expected_challenge`. The caller still has to advance the stored counter with a conditional write."""
        if assertion.credential_id != credential.credential_id:
            raise WebAuthnError("HX-02", "assertion is for another credential")
        if len(assertion.client_data_json) > MAX_CLIENT_DATA or len(assertion.authenticator_data) > MAX_AUTH_DATA:
            raise WebAuthnError("HX-02", "assertion is too large")
        try:
            client = json.loads(assertion.client_data_json.decode("utf-8"), object_pairs_hook=_no_duplicates)
        except (UnicodeDecodeError, ValueError):
            raise WebAuthnError("HX-02", "clientDataJSON is not valid JSON") from None
        if not isinstance(client, dict) or client.get("type") != "webauthn.get":
            raise WebAuthnError("HX-02", "not a webauthn.get assertion")
        try:
            presented = b64url_decode(client.get("challenge"))  # type: ignore[arg-type]
        except (ValueError, binascii.Error, TypeError):
            raise WebAuthnError("HX-03", "challenge is not base64url") from None
        if not hmac.compare_digest(presented, expected_challenge):
            raise WebAuthnError("HX-03", "challenge does not equal the action hash")
        if client.get("origin") not in self.origins or client.get("crossOrigin") is True:
            raise WebAuthnError("HX-02", "origin is not the approval page")
        data = assertion.authenticator_data
        if len(data) < 37:
            raise WebAuthnError("HX-02", "authenticatorData is too short")
        if not hmac.compare_digest(data[:32], self._rp_hash):
            raise WebAuthnError("HX-02", "rpIdHash does not match the relying party")
        flags = data[32]
        if not flags & FLAG_UP or not flags & FLAG_UV:
            raise WebAuthnError("HX-02", "user presence and verification are required")
        signed = data + hashlib.sha256(assertion.client_data_json).digest()
        try:
            credential.public_key().verify(assertion.signature, signed, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            raise WebAuthnError("HX-02", "signature does not verify") from None
        sign_count = int.from_bytes(data[33:37], "big")
        if sign_count <= credential.sign_count:
            raise WebAuthnError("HX-04", "signature counter did not increase")
        return Verified(credential.member_id, credential.credential_id, sign_count)
