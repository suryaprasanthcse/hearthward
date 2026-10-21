"""Gate G5: HX-01 to HX-12, the WebAuthn relying party, the nonce stores and the proof-carrying S- flow."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import boto3
import pipeline
import pytest
from botocore.stub import Stubber
from cryptography.hazmat.primitives.asymmetric import ec

from hearthward.authz import hx_rules
from hearthward.authz.broker import (
    Broker,
    BrokerConfig,
    Plan,
    RuleSimulator,
    StsExecutor,
    action_hash,
    dynamodb_writer,
    plan_for,
    session_policy,
)
from hearthward.authz.hx_rules import Facts, OnFail, Request
from hearthward.authz.proof_stores import DynamoNonceStore, DynamoProofLedger, InMemoryNonceStore
from hearthward.authz.webauthn_rp import (
    Assertion,
    Credential,
    InMemoryCredentialStore,
    RelyingParty,
    WebAuthnError,
    b64url_decode,
    b64url_encode,
)
from hearthward.contracts.canonical import ProfileError, canonical
from hearthward.mcp.app import ToolService
from hearthward.mcp.runtime import Episode, Principal
from hearthward.mcp.schemas import definitions_by_name, output_validator
from tests.g2_support import synthetic_store
from tests.g5_support import (
    ENV,
    EPISODE_ID,
    HOUSEHOLD,
    MEMBERS,
    NOW,
    ORIGIN,
    RP_ID,
    Harness,
    SoftPasskey,
)

pytestmark = pytest.mark.gate_g5

SMINUS = ["dismiss_alert", "pause_monitoring", "add_trusted_vendor", "edit_trusted_circle", "share_evidence"]
ARGS: dict[str, dict[str, Any]] = {
    "dismiss_alert": {"episode_id": EPISODE_ID, "reason": "false_alarm"},
    "pause_monitoring": {"hours": 4, "reason": "travel"},
    "add_trusted_vendor": {"name": "Acme Plumbing", "phone_e164": "+15555550142", "category": "repair"},
    "edit_trusted_circle": {"operation": "add", "display_name": "Jo Synthetic", "phone_e164": "+15555550150",
                            "role": "member"},
    "share_evidence": {"episode_id": EPISODE_ID, "recipient": "bank_fraud_team"},
}
SUCCESS = {"dismiss_alert": "DISMISSED", "pause_monitoring": "PAUSED", "add_trusted_vendor": "ADDED",
           "edit_trusted_circle": "UPDATED", "share_evidence": "SHARED"}


def valid(tool: str, answer: dict[str, Any]) -> bool:
    return output_validator(definitions_by_name()[tool]).is_valid(answer)


# ── the rule table ──────────────────────────────────────────────────────────────────────────────────────


def test_rule_ids_are_hx_01_to_hx_12_in_order(spec: dict[str, Any]) -> None:
    assert hx_rules.RULE_IDS == [f"HX-{i:02d}" for i in range(1, 13)]
    assert hx_rules.RULE_IDS == [r["id"] for r in spec["hx"]]


def test_each_rule_row_equals_the_blueprint_block(spec: dict[str, Any]) -> None:
    assert len(hx_rules.RULES) == len(spec["hx"]) == 12
    for rule, row in zip(hx_rules.RULES, spec["hx"], strict=True):
        assert (rule.id, rule.title, list(rule.applies_to), rule.enforced_at, rule.on_fail.value) == (
            row["id"], row["title"], row["applies_to"], row["enforced_at"], row["on_fail"])


def test_applicability_follows_the_applies_to_column(spec: dict[str, Any]) -> None:
    classes = {t: d["_meta"]["hearthward/class"] for t, d in definitions_by_name().items()}
    for row in spec["hx"]:
        for tool, klass in classes.items():
            expected = "*" in row["applies_to"] or klass in row["applies_to"] or tool in row["applies_to"]
            assert hx_rules.BY_ID[row["id"]].applies(tool) is expected, (row["id"], tool)


def req(tool: str = "dismiss_alert", **args: Any) -> Request:
    return Request(tool, args or ARGS.get(tool, {}), HOUSEHOLD, 3)


def facts(**changes: Any) -> Facts:
    return replace(Facts(now_ms=NOW, member_names=dict(MEMBERS)), **changes)


def passes(rule: str, request: Request, f: Facts, final: bool = False) -> bool:
    check = hx_rules.hx04_final if rule == "HX-04" and final else hx_rules.BY_ID[rule].check
    return check(request, f)


def test_hx01_open_episode_lock_and_cooldown() -> None:
    r = req()
    assert passes("HX-01", r, facts())
    assert not passes("HX-01", r, facts(episode_open=True))
    assert not passes("HX-01", r, facts(episode_open=True, approvers=frozenset({"m_robin"})))
    assert passes("HX-01", r, facts(episode_open=True, approvers=frozenset({"m_robin", "m_sam"})))
    assert not passes("HX-01", r, facts(episode_closed_at_ms=NOW - 1_799_999))
    assert passes("HX-01", r, facts(episode_closed_at_ms=NOW - 1_800_000))
    assert not passes("HX-01", r, facts(episode_closed_at_ms=NOW - 1_800_000, cooldown_ms=2_000_000))


def test_hx02_needs_a_verified_circle_member_not_a_token() -> None:
    assert not passes("HX-02", req(), facts())
    assert not passes("HX-02", req(), facts(assertion_member="m_stranger"))
    assert passes("HX-02", req(), facts(assertion_member="m_robin"))
    assert "token" not in " ".join(f for f in Facts.__dataclass_fields__)  # no Alexa token can reach the rules


def test_hx03_challenge_must_equal_the_action_hash() -> None:
    assert not passes("HX-03", req(), facts())
    assert not passes("HX-03", req(), facts(challenge_matches=False))
    assert passes("HX-03", req(), facts(challenge_matches=True))


def test_hx04_counter_and_nonce() -> None:
    assert not passes("HX-04", req(), facts())
    assert not passes("HX-04", req(), facts(counter_increased=False))
    assert not passes("HX-04", req(), facts(counter_increased=True, nonce_consumed=False))
    assert passes("HX-04", req(), facts(counter_increased=True))                    # nonce not yet attempted
    assert not passes("HX-04", req(), facts(counter_increased=True), final=True)    # but final needs it consumed
    assert passes("HX-04", req(), facts(counter_increased=True, nonce_consumed=True), final=True)


@pytest.mark.parametrize("tool", ["verify_organization_contact", "call_trusted_contact"])
def test_hx05_numbers_come_only_from_verified_sources(tool: str) -> None:
    r = req(tool, organization_name="Acme")
    assert passes("HX-05", r, facts(number_sources=("circle", "trusted_vendors", "curated_directory")))
    assert passes("HX-05", r, facts())
    for bad in ("caller", "llm", "conversation", "unverified_vendor"):
        assert not passes("HX-05", r, facts(number_sources=("circle", bad)))
    for key in ("phone_e164", "official_phone", "number", "Tel"):
        assert not passes("HX-05", req(tool, **{key: "+15555550999"}), facts())


def test_hx06_vendor_cooling_off_is_24_hours_and_normalized() -> None:
    r = req("add_trusted_vendor")
    day = 86_400_000
    assert not passes("HX-06", r, facts(recent_name_claims=(("ACME   plumbing", NOW - 1_000),)))
    assert not passes("HX-06", r, facts(recent_name_claims=(("acme plumbing", NOW - day + 1),)))
    assert passes("HX-06", r, facts(recent_name_claims=(("acme plumbing", NOW - day),)))
    assert passes("HX-06", r, facts(recent_name_claims=(("someone else", NOW - 1),)))
    assert not passes("HX-06", r, facts(recent_name_claims=(("acme plumbing", NOW + 5_000),)))  # clock skew
    assert passes("HX-06", r, facts())


def test_hx07_the_approver_must_be_a_different_existing_member() -> None:
    r = req("edit_trusted_circle", operation="remove", display_name="Sam Synthetic", role="member")
    assert not passes("HX-07", r, facts(assertion_member="m_sam"))
    assert not passes("HX-07", r, facts(assertion_member="m_stranger"))
    assert not passes("HX-07", r, facts())
    assert passes("HX-07", r, facts(assertion_member="m_robin"))
    assert not passes("HX-07", req("edit_trusted_circle", operation="add", display_name=" robin  SYNTHETIC ",
                                   role="member"), facts(assertion_member="m_robin"))


def test_hx08_pause_is_bounded_and_blocked_during_an_episode() -> None:
    for hours, ok in ((1, True), (24, True), (0, False), (25, False), (-3, False), (True, False), (4.0, False)):
        assert passes("HX-08", req("pause_monitoring", hours=hours, reason="travel"), facts()) is ok, hours
    assert not passes("HX-08", req("pause_monitoring", hours=2, reason="travel"), facts(episode_open=True))


def test_hx09_a_closing_or_blinding_action_needs_two_members() -> None:
    r = req()
    assert passes("HX-09", r, facts())
    for field_name in ("closes_episode", "removes_evidence_source"):
        assert not passes("HX-09", r, facts(**{field_name: True}))
        assert not passes("HX-09", r, facts(**{field_name: True}, approvers=frozenset({"m_robin"})))
        assert passes("HX-09", r, facts(**{field_name: True}, approvers=frozenset({"m_robin", "m_sam"})))


def test_hx10_rate_limits_are_six_an_hour_and_three_per_episode() -> None:
    concern = req("raise_concern", concern="x", about="door")
    assert passes("HX-10", concern, facts(concerns_last_hour=5))
    assert not passes("HX-10", concern, facts(concerns_last_hour=6))
    call = req("call_trusted_contact")
    assert passes("HX-10", call, facts(calls_this_episode=2))
    assert not passes("HX-10", call, facts(calls_this_episode=3))
    assert passes("HX-10", call, facts(concerns_last_hour=99))


@pytest.mark.parametrize("down", ["broker_up", "ledger_up", "nonce_store_up"])
def test_hx11_fails_closed_for_sminus_and_leaves_splus_running(down: str) -> None:
    f = facts(**{down: False})
    for tool in SMINUS:
        assert not hx_rules.evaluate(req(tool), f, "declare").ok
        assert "HX-11" in hx_rules.evaluate(req(tool), f, "declare").ids()
    for tool in ("get_door_activity", "assess_visitor", "explain_last_alert", "raise_concern"):
        assert hx_rules.evaluate(Request(tool, {}, HOUSEHOLD), f, "declare").ok


def test_hx12_evidence_needs_the_elders_recorded_consent() -> None:
    r = req("share_evidence")
    assert not passes("HX-12", r, facts())
    assert passes("HX-12", r, facts(elder_consent_recorded=True))


def test_phases_and_verdicts() -> None:
    r = req()
    declare = hx_rules.evaluate(r, facts(), "declare")
    assert declare.ok  # proof rules are not checked before there is a proof
    authorize = hx_rules.evaluate(r, facts(), "authorize")
    assert set(authorize.ids()) == {"HX-02", "HX-03", "HX-04"}
    assert authorize.ids(authorize.stops) == ["HX-03", "HX-04"] and authorize.ids(authorize.waiting) == ["HX-02"]
    dual = hx_rules.evaluate(r, facts(closes_episode=True), "declare")
    assert dual.needs_dual and dual.waiting and not dual.stops
    with pytest.raises(ValueError):
        hx_rules.evaluate(r, facts(), "later")


def test_every_on_fail_value_is_a_known_outcome() -> None:
    assert {r.on_fail for r in hx_rules.RULES} <= set(OnFail)
    assert hx_rules.STOPS == {OnFail.BLOCK, OnFail.THROTTLE}


# ── the relying party ───────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def rp() -> RelyingParty:
    return RelyingParty(RP_ID, [ORIGIN])


@pytest.fixture
def passkey() -> SoftPasskey:
    return SoftPasskey("m_robin")


CHALLENGE = hashlib.sha256(b"an action").digest()


def verify(rp: RelyingParty, passkey: SoftPasskey, assertion: Assertion, challenge: bytes = CHALLENGE,
           credential: Credential | None = None) -> Any:
    return rp.verify(assertion, expected_challenge=challenge, credential=credential or passkey.credential())


def test_a_good_assertion_verifies(rp: RelyingParty, passkey: SoftPasskey) -> None:
    result = verify(rp, passkey, passkey.assert_(CHALLENGE))
    assert (result.member_id, result.sign_count) == ("m_robin", 1)


def test_hx03_a_challenge_for_another_action_is_refused(rp: RelyingParty, passkey: SoftPasskey) -> None:
    with pytest.raises(WebAuthnError) as caught:
        verify(rp, passkey, passkey.assert_(hashlib.sha256(b"a different action").digest()))
    assert caught.value.rule == "HX-03"


@pytest.mark.parametrize("make, rule", [
    (lambda p: p.assert_(CHALLENGE, origin="https://evil.example"), "HX-02"),
    (lambda p: p.assert_(CHALLENGE, rp_id="evil.example"), "HX-02"),
    (lambda p: p.assert_(CHALLENGE, flags=0x04), "HX-02"),      # no user presence
    (lambda p: p.assert_(CHALLENGE, flags=0x01), "HX-02"),      # no user verification
    (lambda p: p.assert_(CHALLENGE, type_="webauthn.create"), "HX-02"),
    (lambda p: p.assert_(CHALLENGE, cross_origin=True), "HX-02"),
    (lambda p: p.assert_(CHALLENGE, key=ec.generate_private_key(ec.SECP256R1())), "HX-02"),
    (lambda p: p.assert_(CHALLENGE, count=0), "HX-04"),
    (lambda p: p.assert_(CHALLENGE, client_override=b"not json"), "HX-02"),
    (lambda p: p.assert_(CHALLENGE, client_override=b'{"type":"webauthn.get","type":"webauthn.get"}'), "HX-02"),
    (lambda p: p.assert_(CHALLENGE, client_override=b'{"type":"webauthn.get","challenge":"!!!"}'), "HX-03"),
    (lambda p: p.assert_(CHALLENGE, client_override=b"x" * 5000), "HX-02"),
], ids=["origin", "rp_id", "no_up", "no_uv", "create", "cross_origin", "wrong_key", "zero_counter", "not_json",
        "dup_keys", "bad_challenge", "oversize"])
def test_bad_assertions_are_refused_with_the_right_rule(rp: RelyingParty, passkey: SoftPasskey, make: Any,
                                                        rule: str) -> None:
    with pytest.raises(WebAuthnError) as caught:
        verify(rp, passkey, make(passkey))
    assert caught.value.rule == rule


def test_a_tampered_assertion_does_not_verify(rp: RelyingParty, passkey: SoftPasskey) -> None:
    good = passkey.assert_(CHALLENGE)
    for bad in (replace(good, authenticator_data=good.authenticator_data[:-1] + b"\xff"),
                replace(good, signature=good.signature[:-1] + bytes([good.signature[-1] ^ 1])),
                replace(good, authenticator_data=good.authenticator_data[:36]),
                replace(good, credential_id="another")):
        with pytest.raises(WebAuthnError):
            verify(rp, passkey, bad)


def test_the_counter_must_strictly_increase(rp: RelyingParty, passkey: SoftPasskey) -> None:
    stored = replace(passkey.credential(), sign_count=5)
    for count in (5, 4):
        with pytest.raises(WebAuthnError) as caught:
            verify(rp, passkey, passkey.assert_(CHALLENGE, count=count), credential=stored)
        assert caught.value.rule == "HX-04"
    assert verify(rp, passkey, passkey.assert_(CHALLENGE, count=6), credential=stored).sign_count == 6


def test_credential_store_advances_the_counter_conditionally(passkey: SoftPasskey) -> None:
    store = InMemoryCredentialStore([passkey.credential()])
    assert store.advance_counter(passkey.credential_id, 3) is True
    assert store.advance_counter(passkey.credential_id, 3) is False and store.advance_counter("nope", 9) is False
    assert store.get(passkey.credential_id).sign_count == 3  # type: ignore[union-attr]


def test_a_non_p256_credential_is_refused(rp: RelyingParty, passkey: SoftPasskey) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    der = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(WebAuthnError):
        verify(rp, passkey, passkey.assert_(CHALLENGE), credential=replace(passkey.credential(), public_key_der=der))


def test_base64url_helpers_round_trip() -> None:
    for raw in (b"", b"\x00", b"\xff\xfe\xfd", hashlib.sha256(b"x").digest()):
        assert b64url_decode(b64url_encode(raw)) == raw
    with pytest.raises(ValueError):
        b64url_decode("é")


def test_a_relying_party_needs_an_id_and_an_origin() -> None:
    with pytest.raises(ValueError):
        RelyingParty("", [ORIGIN])
    with pytest.raises(ValueError):
        RelyingParty(RP_ID, [])


# ── the action hash ─────────────────────────────────────────────────────────────────────────────────────


def test_action_hash_is_the_tagged_sha256_of_the_jcs_document() -> None:
    args = {"hours": 4, "reason": "travel"}
    document = {"tool": "pause_monitoring", "args": args, "household": HOUSEHOLD, "episode_version": 3,
                "nonce": "ab" * 16, "exp": NOW + 300_000}
    expected = hashlib.sha256(b"hw-action-v1" + b"\x00" + canonical(document)).digest()
    assert action_hash("pause_monitoring", args, HOUSEHOLD, 3, "ab" * 16, NOW + 300_000) == expected


def test_every_field_is_bound_into_the_hash() -> None:
    base = ("pause_monitoring", {"hours": 4, "reason": "travel"}, HOUSEHOLD, 3, "ab" * 16, NOW + 300_000)
    reference = action_hash(*base)
    variants = [("dismiss_alert", *base[1:]), (base[0], {"hours": 5, "reason": "travel"}, *base[2:]),
                (*base[:2], "hh_other", *base[3:]), (*base[:3], 4, *base[4:]), (*base[:4], "cd" * 16, base[5]),
                (*base[:5], base[5] + 1)]
    assert len({action_hash(*v) for v in variants} | {reference}) == len(variants) + 1


def test_argument_order_does_not_change_the_hash_and_floats_are_refused() -> None:
    a = action_hash("pause_monitoring", {"hours": 4, "reason": "travel"}, HOUSEHOLD, 3, "n", 1)
    b = action_hash("pause_monitoring", {"reason": "travel", "hours": 4}, HOUSEHOLD, 3, "n", 1)
    assert a == b
    with pytest.raises(ProfileError):
        action_hash("pause_monitoring", {"hours": 4.5}, HOUSEHOLD, 3, "n", 1)


# ── nonce and proof stores ───────────────────────────────────────────────────────────────────────────────


@pytest.fixture(params=["memory", "dynamodb"])
def nonce_store(request: pytest.FixtureRequest) -> Any:
    if request.param == "memory":
        return InMemoryNonceStore()
    request.getfixturevalue("aws")
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(TableName="HearthwardNonces", KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                        BillingMode="PAY_PER_REQUEST")
    return DynamoNonceStore(client, "HearthwardNonces")


RECORD = {"tool": "pause_monitoring", "args": {"hours": 4}, "household": HOUSEHOLD, "exp": NOW + 300_000,
          "required": 1}


def test_a_challenge_exists_once(nonce_store: Any) -> None:
    nonce_store.put("chl_a", RECORD)
    with pytest.raises(Exception):  # noqa: B017 - a duplicate id fails in either backend
        nonce_store.put("chl_a", RECORD)
    stored = nonce_store.get("chl_a")
    assert stored["tool"] == "pause_monitoring" and stored["approvals"] == [] and stored["consumed"] is False
    assert nonce_store.get("chl_missing") is None


def test_an_approval_counts_once_per_member(nonce_store: Any) -> None:
    nonce_store.put("chl_a", RECORD)
    assert nonce_store.add_approval("chl_a", "m_robin") is True
    assert nonce_store.add_approval("chl_a", "m_robin") is False
    assert nonce_store.add_approval("chl_a", "m_sam") is True
    assert nonce_store.get("chl_a")["approvals"] == ["m_robin", "m_sam"]
    assert nonce_store.add_approval("chl_missing", "m_robin") is False


def test_a_challenge_is_consumed_exactly_once(nonce_store: Any) -> None:
    """HX-04: a conditional write."""
    nonce_store.put("chl_a", RECORD)
    assert nonce_store.consume("chl_a") is True
    assert nonce_store.consume("chl_a") is False
    assert nonce_store.get("chl_a")["consumed"] is True
    assert nonce_store.add_approval("chl_a", "m_robin") is False  # no approvals after use
    assert nonce_store.consume("chl_missing") is False


def test_dynamo_nonce_ttl_outlives_the_challenge(aws: None) -> None:
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(TableName="HearthwardNonces", KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                        BillingMode="PAY_PER_REQUEST")
    DynamoNonceStore(client, "HearthwardNonces").put("chl_a", RECORD)
    item = client.get_item(TableName="HearthwardNonces", Key={"pk": {"S": "NONCE#chl_a"}})["Item"]
    assert int(item["expires_at"]["N"]) == (NOW + 300_000) // 1000 + 3600


def test_the_proof_ledger_records_once(aws: None) -> None:
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(TableName="HearthwardProofLedger",
                        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                              {"AttributeName": "sk", "AttributeType": "S"}],
                        BillingMode="PAY_PER_REQUEST")
    ledger = DynamoProofLedger(client, "HearthwardProofLedger")
    proof = "sha256:" + "ab" * 32
    ledger.record(HOUSEHOLD, proof, {"tool": "dismiss_alert"})
    with pytest.raises(Exception):  # noqa: B017
        ledger.record(HOUSEHOLD, proof, {"tool": "dismiss_alert"})
    item = client.get_item(TableName="HearthwardProofLedger",
                           Key={"pk": {"S": HOUSEHOLD}, "sk": {"S": f"PROOF#{proof}"}})["Item"]
    assert json.loads(item["record"]["S"]) == {"tool": "dismiss_alert"}


# ── the flow: declare, challenge, approve, mint ─────────────────────────────────────────────────────────


@pytest.fixture
def h() -> Harness:
    return Harness()


def approve(h: Harness, challenge_id: str, who: SoftPasskey, **kwargs: Any) -> dict[str, Any]:
    return h.broker.approve(challenge_id, who.assert_(h.challenge_bytes(challenge_id), **kwargs))


@pytest.mark.parametrize("tool", SMINUS)
def test_a_challenge_is_proof_required_and_well_formed(h: Harness, tool: str) -> None:
    if tool == "dismiss_alert":
        h.facts.update(open_episode_id=None)
    answer = h.broker.challenge(HOUSEHOLD, tool, ARGS[tool])
    assert answer["status"] == "PROOF_REQUIRED" and valid(tool, answer)
    assert re.fullmatch(r"chl_[0-9A-HJKMNP-TV-Z]{26}", answer["challenge_id"])
    assert answer["expires_at_ms"] == NOW + 300_000 and answer["approve_on"] == "caregiver_phone"
    assert {"HX-02", "HX-03", "HX-04"} <= set(answer["rules"])
    assert h.notifier.approvals == [(HOUSEHOLD, answer["challenge_id"], NOW + 300_000)]
    record = h.nonces.get(answer["challenge_id"])
    assert record is not None and record["required"] == 1 and record["consumed"] is False
    assert record["action_hash"] == action_hash(tool, ARGS[tool], HOUSEHOLD, 3, record["nonce"], record["exp"]).hex()
    assert h.executor.calls == []  # a challenge changes nothing


def test_the_rules_a_challenge_lists_are_the_broker_side_ones(h: Harness) -> None:
    answer = h.broker.challenge(HOUSEHOLD, "pause_monitoring", ARGS["pause_monitoring"])
    assert answer["rules"] == ["HX-01", "HX-02", "HX-03", "HX-04", "HX-08", "HX-09"]
    assert "HX-05" not in answer["rules"] and "HX-10" not in answer["rules"] and "HX-11" not in answer["rules"]


def test_a_broker_only_handles_sminus_tools(h: Harness) -> None:
    for tool in ("get_door_activity", "raise_concern", "nonexistent"):
        with pytest.raises(ValueError):
            h.broker.challenge(HOUSEHOLD, tool, {})


def test_invalid_arguments_never_reach_the_nonce_store(h: Harness) -> None:
    answer = h.broker.challenge(HOUSEHOLD, "pause_monitoring", {"hours": 99, "reason": "travel"})
    assert answer["status"] == "BLOCKED" and not h.notifier.approvals


@pytest.mark.parametrize("setup, tool, rules", [
    (lambda h: h.facts.update(episode_open=True), "pause_monitoring", ["HX-08"]),
    (lambda h: h.facts.update(recent_name_claims=(("acme plumbing", NOW - 3_600_000),)), "add_trusted_vendor",
     ["HX-06"]),
    (lambda h: h.facts.update(broker_up=False), "dismiss_alert", ["HX-11"]),
    (lambda h: h.facts.update(ledger_up=False), "share_evidence", ["HX-11"]),
    (lambda h: h.facts.update(nonce_store_up=False), "edit_trusted_circle", ["HX-11"]),
])
def test_declare_time_refusals_are_blocked_with_the_rule(h: Harness, setup: Any, tool: str, rules: list[str]) -> None:
    setup(h)
    answer = h.broker.challenge(HOUSEHOLD, tool, ARGS[tool])
    assert answer["status"] == "BLOCKED" and answer["rules"] == rules and valid(tool, answer)
    assert not h.notifier.approvals and h.nonces._records == {}


def test_unreadable_facts_or_a_dead_nonce_store_block_hx_11(h: Harness) -> None:
    h.facts.fail = True
    answer = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])
    assert answer["status"] == "BLOCKED" and "HX-11" in answer["rules"]
    h.facts.fail = False
    h.nonces.fail_writes = True
    answer = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])
    assert answer["status"] == "BLOCKED" and "HX-11" in answer["rules"] and not h.notifier.approvals


def test_a_valid_approval_applies_the_change_once(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])["challenge_id"]
    answer = approve(h, cid, h.robin)
    assert answer["status"] == "DISMISSED" and answer["episode_id"] == EPISODE_ID and valid("dismiss_alert", answer)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", answer["proof_hash"])
    ((household, plan, proof_hash),) = h.executor.calls
    assert household == HOUSEHOLD and proof_hash == answer["proof_hash"]
    assert dict(plan.key) == {"pk": HOUSEHOLD, "sk": f"EPISODE#{EPISODE_ID}"}
    assert plan.attributes["state"] == "dismissed"
    assert h.ledger.records[proof_hash]["approvers"] == ["m_robin"]
    assert h.nonces.get(cid)["consumed"] is True  # type: ignore[index]


def test_replaying_a_spent_challenge_is_blocked(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])["challenge_id"]
    first = h.robin.assert_(h.challenge_bytes(cid))
    assert h.broker.approve(cid, first)["status"] == "DISMISSED"
    for assertion in (first, h.robin.assert_(h.challenge_bytes(cid)), h.sam.assert_(h.challenge_bytes(cid))):
        again = h.broker.approve(cid, assertion)
        assert again["status"] == "BLOCKED" and again["rules"] == ["HX-04"]
    assert len(h.executor.calls) == 1


def test_a_replayed_assertion_with_an_old_counter_is_blocked(h: Harness) -> None:
    h.facts.update(evidence_sources=frozenset({"door"}))  # this pause needs two members, so the challenge stays open
    cid = h.broker.challenge(HOUSEHOLD, "pause_monitoring", ARGS["pause_monitoring"])["challenge_id"]
    assertion = h.robin.assert_(h.challenge_bytes(cid))
    assert h.broker.approve(cid, assertion)["status"] == "PROOF_REQUIRED"
    replay = h.broker.approve(cid, assertion)
    assert replay["status"] == "BLOCKED" and replay["rules"] == ["HX-04"] and h.executor.calls == []


def test_an_assertion_for_another_action_is_blocked_and_changes_nothing(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])["challenge_id"]
    other = h.broker.challenge(HOUSEHOLD, "pause_monitoring", ARGS["pause_monitoring"])["challenge_id"]
    answer = h.broker.approve(cid, h.robin.assert_(h.challenge_bytes(other)))
    assert answer["status"] == "BLOCKED" and answer["rules"] == ["HX-03"]
    assert h.executor.calls == [] and h.nonces.get(cid)["consumed"] is False  # type: ignore[index]


def test_a_stranger_or_unknown_passkey_never_gets_through(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])["challenge_id"]
    stranger = SoftPasskey("m_stranger")
    assert h.broker.approve(cid, stranger.assert_(h.challenge_bytes(cid)))["status"] == "PROOF_REQUIRED"
    h.credentials.add(stranger.credential())  # registered, but not a member of the circle
    assert h.broker.approve(cid, stranger.assert_(h.challenge_bytes(cid)))["status"] == "PROOF_REQUIRED"
    forged = h.robin.assert_(h.challenge_bytes(cid), key=ec.generate_private_key(ec.SECP256R1()))
    assert h.broker.approve(cid, forged)["status"] == "PROOF_REQUIRED"
    assert h.executor.calls == [] and h.ledger.records == {}


def test_expired_unknown_and_consumed_challenges_are_blocked_hx_04(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])["challenge_id"]
    assertion = h.robin.assert_(h.challenge_bytes(cid))
    h.clock.now = NOW + 300_000
    expired = h.broker.approve(cid, assertion)
    assert expired["status"] == "BLOCKED" and expired["rules"] == ["HX-04"]
    unknown = h.broker.approve("chl_01HZX3K9M2N4P6Q8R0S2T4V6W8", assertion)
    assert unknown["status"] == "BLOCKED" and unknown["rules"] == ["HX-04"]
    assert h.executor.calls == []


def test_dual_control_when_an_episode_is_open(h: Harness) -> None:
    """HX-01 and HX-09: dismissing the open episode needs two distinct members, and one member twice is not two."""
    h.facts.update(episode_open=True, open_episode_id=EPISODE_ID)
    challenge = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])
    cid = challenge["challenge_id"]
    assert challenge["status"] == "PROOF_REQUIRED" and h.nonces.get(cid)["required"] == 2  # type: ignore[index]
    first = approve(h, cid, h.robin)
    assert first["status"] == "PROOF_REQUIRED" and h.executor.calls == []
    assert h.nonces.get(cid)["consumed"] is False  # type: ignore[index]
    assert approve(h, cid, h.robin)["status"] == "PROOF_REQUIRED"  # same member again: still one approver
    assert h.executor.calls == []
    done = approve(h, cid, h.sam)
    assert done["status"] == "DISMISSED" and len(h.executor.calls) == 1
    assert h.ledger.records[done["proof_hash"]]["approvers"] == ["m_robin", "m_sam"]


def test_hx09_a_pause_that_removes_an_evidence_source_needs_two_members(h: Harness) -> None:
    h.facts.update(evidence_sources=frozenset({"door", "room"}))
    cid = h.broker.challenge(HOUSEHOLD, "pause_monitoring", ARGS["pause_monitoring"])["challenge_id"]
    assert h.nonces.get(cid)["required"] == 2  # type: ignore[index]
    assert approve(h, cid, h.robin)["status"] == "PROOF_REQUIRED"
    assert approve(h, cid, h.sam)["status"] == "PAUSED"


def test_a_pause_with_no_evidence_to_lose_needs_one_member(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "pause_monitoring", ARGS["pause_monitoring"])["challenge_id"]
    answer = approve(h, cid, h.robin)
    assert answer["status"] == "PAUSED" and answer["until_ms"] == NOW + 4 * 3_600_000 and valid("pause_monitoring", answer)


def test_hx07_the_person_being_removed_cannot_approve_their_own_removal(h: Harness) -> None:
    args = {"operation": "remove", "display_name": "Sam Synthetic", "role": "member"}
    cid = h.broker.challenge(HOUSEHOLD, "edit_trusted_circle", args)["challenge_id"]
    assert approve(h, cid, h.sam)["status"] == "PROOF_REQUIRED" and h.executor.calls == []
    answer = approve(h, cid, h.robin)
    assert answer["status"] == "UPDATED" and valid("edit_trusted_circle", answer)
    assert h.notifier.circle == [(HOUSEHOLD, "remove Sam Synthetic")]


def test_hx12_evidence_does_not_leave_without_the_elders_consent(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "share_evidence", ARGS["share_evidence"])["challenge_id"]
    assert approve(h, cid, h.robin)["status"] == "PROOF_REQUIRED" and h.executor.calls == []
    h.facts.update(elder_consent_recorded=True)
    answer = approve(h, cid, h.robin)
    assert answer["status"] == "SHARED" and valid("share_evidence", answer)


def test_a_change_of_episode_after_the_challenge_blocks_the_approval(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])["challenge_id"]
    h.facts.update(episode_version=4)
    answer = approve(h, cid, h.robin)
    assert answer["status"] == "BLOCKED" and answer["rules"] == ["HX-03"] and h.executor.calls == []


def test_the_world_turning_dangerous_before_approval_is_refused(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "pause_monitoring", ARGS["pause_monitoring"])["challenge_id"]
    h.facts.update(episode_open=True)  # an episode opened while the challenge was pending
    answer = approve(h, cid, h.robin)
    assert answer["status"] == "BLOCKED" and "HX-08" in answer["rules"] and h.executor.calls == []


def test_a_failed_mint_is_a_refusal_and_leaves_no_proof(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])["challenge_id"]
    h.executor.fail = True
    answer = approve(h, cid, h.robin)
    assert answer["status"] == "BLOCKED" and "HX-11" in answer["rules"] and h.ledger.records == {}
    assert h.nonces.get(cid)["consumed"] is True  # type: ignore[index]  # the nonce stays spent; ask again


def test_a_racing_counter_update_loses(h: Harness) -> None:
    cid = h.broker.challenge(HOUSEHOLD, "dismiss_alert", ARGS["dismiss_alert"])["challenge_id"]
    assertion = h.robin.assert_(h.challenge_bytes(cid))
    h.credentials.advance_counter(h.robin.credential_id, 50)  # another request got there first... with a higher count
    answer = h.broker.approve(cid, assertion)
    assert answer["status"] == "BLOCKED" and answer["rules"] == ["HX-04"] and h.executor.calls == []


# ── through the MCP tool ─────────────────────────────────────────────────────────────────────────────────


def mcp_service(h: Harness) -> ToolService:
    return ToolService(synthetic_store(), clock=h.clock, broker=h.broker)


def test_an_alexa_token_alone_gets_a_challenge_never_a_change(h: Harness) -> None:
    service = mcp_service(h)
    for tool in SMINUS:
        result = service.call(tool, ARGS[tool], Principal("user-synthetic-1", "user"))
        assert result.is_error is False and result.structured_content["status"] == "PROOF_REQUIRED", tool
        assert valid(tool, result.structured_content)
    assert h.executor.calls == [] and h.ledger.records == {}


def test_a_broker_refusal_keeps_its_rules_through_the_mcp_tool(h: Harness) -> None:
    h.facts.update(recent_name_claims=(("acme plumbing", NOW - 1_000),))
    result = mcp_service(h).call("add_trusted_vendor", ARGS["add_trusted_vendor"], Principal("u", "user"))
    assert result.structured_content["status"] == "BLOCKED" and result.structured_content["rules"] == ["HX-06"]


def test_an_open_episode_reaches_the_broker_for_dual_control(h: Harness) -> None:
    service = ToolService(synthetic_store(), clock=h.clock, broker=h.broker)
    service.store.episodes.append(Episode(EPISODE_ID, "open", 2000))
    h.facts.update(episode_open=True, open_episode_id=EPISODE_ID)
    result = service.call("dismiss_alert", ARGS["dismiss_alert"], Principal("u", "user"))
    assert result.structured_content["status"] == "PROOF_REQUIRED"
    assert h.nonces.get(result.structured_content["challenge_id"])["required"] == 2  # type: ignore[index]


# ── plans, the executor session and the writer ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", SMINUS)
def test_every_plan_yields_a_schema_valid_success(tool: str) -> None:
    plan = plan_for(tool, ARGS[tool], HOUSEHOLD, NOW, lambda n: b"\x01" * n)
    assert plan.key["pk"] == HOUSEHOLD and plan.key["sk"].split("#")[0] in {
        "EPISODE", "PAUSE", "VENDOR", "CIRCLE", "SHARE"}
    answer = {**plan.result, "proof_hash": "sha256:" + "0" * 64}
    assert answer["status"] == SUCCESS[tool] and valid(tool, answer)
    assert all(isinstance(v, str | int | bool) for v in plan.attributes.values())


def test_plans_are_scoped_to_the_household_and_never_write_the_key_attributes() -> None:
    for tool in SMINUS:
        plan = plan_for(tool, ARGS[tool], HOUSEHOLD, NOW)
        assert plan.key["pk"] == HOUSEHOLD and not ({"pk", "sk"} & set(plan.attributes))
    with pytest.raises(ValueError):
        plan_for("raise_concern", {}, HOUSEHOLD, NOW)


def test_the_session_policy_is_scoped_to_the_household_and_the_attributes() -> None:
    config = BrokerConfig.from_env(ENV)
    plan = plan_for("pause_monitoring", ARGS["pause_monitoring"], HOUSEHOLD, NOW)
    policy = json.loads(session_policy(config.security_state_arn, HOUSEHOLD, plan))
    (statement,) = policy["Statement"]
    assert statement["Effect"] == "Allow" and statement["Action"] == ["dynamodb:UpdateItem"]
    assert statement["Resource"] == "arn:aws:dynamodb:us-east-1:123456789012:table/HearthwardSecurityState"
    condition = statement["Condition"]["ForAllValues:StringEquals"]
    assert condition["dynamodb:LeadingKeys"] == [HOUSEHOLD]
    assert condition["dynamodb:Attributes"] == ["pk", "reason", "sk", "until_ms"]
    assert len(session_policy(config.security_state_arn, HOUSEHOLD, plan)) < 2048


def sts_stub() -> tuple[Any, Stubber]:
    client = boto3.client("sts", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    return client, Stubber(client)


def test_the_executor_mints_a_900_second_session_tagged_with_the_proof_hash() -> None:
    config = BrokerConfig.from_env(ENV)
    plan = plan_for("pause_monitoring", ARGS["pause_monitoring"], HOUSEHOLD, NOW)
    proof = "sha256:" + "ab" * 32
    client, stub = sts_stub()
    expiry = datetime(2030, 1, 1, tzinfo=UTC)
    stub.add_response("assume_role", {
        "Credentials": {"AccessKeyId": "ASIAEXAMPLEEXAMPLE1", "SecretAccessKey": "s", "SessionToken": "t", "Expiration": expiry},
        "AssumedRoleUser": {"AssumedRoleId": "AROAEXAMPLE:x", "Arn": "arn:aws:sts::123456789012:assumed-role/x/y"}},
        {"RoleArn": ENV["HW_BROKER_EXECUTOR_ROLE_ARN"], "RoleSessionName": "hw-" + "ab" * 16, "DurationSeconds": 900,
         "Policy": session_policy(config.security_state_arn, HOUSEHOLD, plan),
         "Tags": [{"Key": "proof_hash", "Value": proof}]})
    written: list[Any] = []
    with stub:
        StsExecutor(client, config, lambda creds, p: written.append((dict(creds), p))).apply(HOUSEHOLD, plan, proof)
        stub.assert_no_pending_responses()
    assert written[0][0]["AccessKeyId"] == "ASIAEXAMPLEEXAMPLE1" and written[0][1] is plan
    assert re.fullmatch(r"[\w+=,.@-]{2,64}", "hw-" + "ab" * 16)


def test_the_writer_applies_a_plan_through_the_session_credentials(aws: None) -> None:
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(TableName="HearthwardSecurityState",
                        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                              {"AttributeName": "sk", "AttributeType": "S"}],
                        BillingMode="PAY_PER_REQUEST")
    plan = Plan({"pk": HOUSEHOLD, "sk": "VENDOR#x"}, {"name": "Acme", "verified": True, "added_at_ms": NOW}, {})
    dynamodb_writer("us-east-1", "HearthwardSecurityState")(
        {"AccessKeyId": "ASIAEXAMPLEEXAMPLE1", "SecretAccessKey": "s", "SessionToken": "t"}, plan)
    item = client.get_item(TableName="HearthwardSecurityState",
                           Key={"pk": {"S": HOUSEHOLD}, "sk": {"S": "VENDOR#x"}})["Item"]
    assert item["name"] == {"S": "Acme"} and item["verified"] == {"BOOL": True}
    assert item["added_at_ms"] == {"N": str(NOW)}


def test_the_whole_path_with_real_sts_and_dynamodb_stand_ins(aws: None) -> None:
    """Broker -> STS executor -> writer, over moto: the item lands in SecurityState and the ledger records the proof."""
    dynamo = boto3.client("dynamodb", region_name="us-east-1")
    for name in ("HearthwardSecurityState", "HearthwardProofLedger"):
        dynamo.create_table(TableName=name,
                            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                                  {"AttributeName": "sk", "AttributeType": "S"}],
                            BillingMode="PAY_PER_REQUEST")
    h = Harness()
    config = BrokerConfig.from_env(ENV)
    executor = StsExecutor(boto3.client("sts", region_name="us-east-1"), config,
                           dynamodb_writer("us-east-1", config.security_state_table))
    broker = Broker(config=config, facts=h.facts, nonces=h.nonces, credentials=h.credentials,
                    relying_party=RelyingParty(config.rp_id, config.origins), executor=executor,
                    proof_ledger=DynamoProofLedger(dynamo, "HearthwardProofLedger"), simulator=RuleSimulator(),
                    clock=h.clock, entropy=lambda n: b"\x07" * n)
    cid = broker.challenge(HOUSEHOLD, "pause_monitoring", ARGS["pause_monitoring"])["challenge_id"]
    answer = broker.approve(cid, h.robin.assert_(h.challenge_bytes(cid)))
    assert answer["status"] == "PAUSED"
    item = dynamo.get_item(TableName="HearthwardSecurityState",
                           Key={"pk": {"S": HOUSEHOLD}, "sk": {"S": "PAUSE"}})["Item"]
    assert item["until_ms"]["N"] == str(NOW + 4 * 3_600_000)
    proof = dynamo.get_item(TableName="HearthwardProofLedger",
                            Key={"pk": {"S": HOUSEHOLD}, "sk": {"S": f"PROOF#{answer['proof_hash']}"}})["Item"]
    assert json.loads(proof["record"]["S"])["tool"] == "pause_monitoring"


# ── configuration and the gate variables ───────────────────────────────────────────────────────────────


def test_broker_config_reads_the_three_g5_variables() -> None:
    config = BrokerConfig.from_env({**ENV, "AWS_REGION": "eu-west-1"})
    assert (config.executor_role_arn, config.rp_id, config.region) == (
        ENV["HW_BROKER_EXECUTOR_ROLE_ARN"], RP_ID, "eu-west-1")
    assert config.origins == (ORIGIN,)
    assert config.security_state_arn.startswith("arn:aws:dynamodb:eu-west-1:123456789012:table/")
    with pytest.raises(RuntimeError, match="HW_WEBAUTHN_RP_ID"):
        BrokerConfig.from_env({k: v for k, v in ENV.items() if k != "HW_WEBAUTHN_RP_ID"})
    with pytest.raises(ValueError):
        BrokerConfig("not-an-arn", RP_ID, "arn").security_state_arn  # noqa: B018


def test_the_g5_values_pass_the_pipeline_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(ENV) == set(pipeline.GATE_ENV["G5"])
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
        assert pipeline.ENV_RULES[name](value) is None
    assert pipeline._env("G5")(pipeline.Context(probe=False)).status == pipeline.PASS


def test_the_env_example_names_every_g5_variable() -> None:
    text = (pipeline.ROOT / ".env.example").read_text(encoding="utf-8")
    for name in pipeline.GATE_ENV["G5"]:
        assert f"\n{name}=" in text


def test_the_pipeline_parity_check_passes() -> None:
    assert pipeline.g5_hx_parity(pipeline.Context(probe=False)).status == pipeline.PASS
