"""Gate G5 test support: a software passkey, fake facts and a wired broker. Nothing here touches a network."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from hearthward.authz.broker import Broker, BrokerConfig, Plan, RuleSimulator
from hearthward.authz.hx_rules import Facts
from hearthward.authz.proof_stores import InMemoryNonceStore, InMemoryProofLedger
from hearthward.authz.webauthn_rp import (
    Assertion,
    Credential,
    InMemoryCredentialStore,
    RelyingParty,
    b64url_encode,
)

# SYNTHETIC values shaped like the ones tools/pipeline.py checks for G5.
ENV = {
    "HW_BROKER_EXECUTOR_ROLE_ARN": "arn:aws:iam::123456789012:role/hearthward-broker-executor",
    "HW_WEBAUTHN_RP_ID": "approve.hearthward.example",
    "HW_LADDER_STATE_MACHINE_ARN": "arn:aws:states:us-east-1:123456789012:stateMachine:InterventionLadder",
}
RP_ID = ENV["HW_WEBAUTHN_RP_ID"]
ORIGIN = f"https://{RP_ID}"
NOW = 1_800_000_000_000
MEMBERS = {"m_robin": "Robin Synthetic", "m_sam": "Sam Synthetic"}
HOUSEHOLD = "hh_synthetic"
EPISODE_ID = "ep_01HZX3K9M2N4P6Q8R0S2T4V6W8"


class SoftPasskey:
    """A software authenticator: an ES256 key that signs WebAuthn assertions with a counter it controls."""

    def __init__(self, member_id: str, credential_id: str | None = None) -> None:
        self.member_id = member_id
        self.credential_id = credential_id or b64url_encode(hashlib.sha256(member_id.encode()).digest()[:16])
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.counter = 0

    def credential(self) -> Credential:
        der = self.key.public_key().public_bytes(serialization.Encoding.DER,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo)
        return Credential(self.credential_id, self.member_id, der, 0)

    def assert_(self, challenge: bytes, *, count: int | None = None, flags: int = 0x05, rp_id: str = RP_ID,
                origin: str = ORIGIN, type_: str = "webauthn.get", cross_origin: bool = False,
                client_override: bytes | None = None, key: ec.EllipticCurvePrivateKey | None = None) -> Assertion:
        self.counter = self.counter + 1 if count is None else count
        client = client_override or json.dumps({"type": type_, "challenge": b64url_encode(challenge),
                                                "origin": origin, "crossOrigin": cross_origin}).encode()
        data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + self.counter.to_bytes(4, "big")
        signature = (key or self.key).sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return Assertion(self.credential_id, client, data, signature)


@dataclass
class FakeFacts:
    """A FactsProvider whose answer tests edit freely."""

    template: Facts = field(default_factory=lambda: Facts(now_ms=NOW, member_names=dict(MEMBERS),
                                                          episode_version=3))
    fail: bool = False

    def facts(self, household: str, tool: str, args: Mapping[str, Any]) -> Facts:
        if self.fail:
            raise OSError("ledger unavailable")
        return self.template

    def update(self, **changes: Any) -> None:
        self.template = replace(self.template, **changes)


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Plan, str]] = []
        self.fail = False

    def apply(self, household: str, plan: Plan, proof_hash: str) -> None:
        if self.fail:
            raise OSError("sts unavailable")
        self.calls.append((household, plan, proof_hash))


class RecordingNotifier:
    def __init__(self) -> None:
        self.approvals: list[tuple[str, str, int]] = []
        self.circle: list[tuple[str, str]] = []

    def push_approval(self, household: str, challenge_id: str, expires_at_ms: int) -> None:
        self.approvals.append((household, challenge_id, expires_at_ms))

    def circle_changed(self, household: str, summary: str) -> None:
        self.circle.append((household, summary))


class Clock:
    def __init__(self, now: int = NOW) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


class Harness:
    """A broker over in-memory stores with two circle members who each hold a passkey."""

    def __init__(self) -> None:
        self.config = BrokerConfig.from_env(ENV)
        self.facts = FakeFacts()
        self.clock = Clock()
        self.nonces = InMemoryNonceStore()
        self.ledger = InMemoryProofLedger()
        self.executor = RecordingExecutor()
        self.notifier = RecordingNotifier()
        self.robin, self.sam = SoftPasskey("m_robin"), SoftPasskey("m_sam")
        self.credentials = InMemoryCredentialStore([self.robin.credential(), self.sam.credential()])
        counter = iter(range(1, 10_000))
        self.broker = Broker(
            config=self.config, facts=self.facts, nonces=self.nonces, credentials=self.credentials,
            relying_party=RelyingParty(self.config.rp_id, self.config.origins), executor=self.executor,
            proof_ledger=self.ledger, simulator=RuleSimulator(), notifier=self.notifier, clock=self.clock,
            entropy=lambda n: next(counter).to_bytes(n, "big"))

    def challenge_bytes(self, challenge_id: str) -> bytes:
        record = self.nonces.get(challenge_id)
        assert record is not None
        return bytes.fromhex(record["action_hash"])
