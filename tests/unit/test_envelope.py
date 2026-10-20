"""The event envelope and the ledger chain (blueprint §4.1, §4.2): schema, E-1 to E-5, digest formats, heads."""

from __future__ import annotations

import copy
from typing import Any

import orrery
import pytest

from hearthward.contracts.envelope import ZERO_DIGEST, chain_head, validate_envelope, verify_chain

pytestmark = pytest.mark.gate_g1


@pytest.fixture(scope="module")
def chain(orrery_runs: dict[str, orrery.Result]) -> list[dict[str, Any]]:
    return orrery_runs["doorstep_scam"].envelopes


@pytest.fixture
def env(chain: list[dict[str, Any]]) -> dict[str, Any]:
    return copy.deepcopy(chain[0])


def test_all_orrery_envelopes_from_all_six_scenarios_validate(orrery_runs: dict[str, orrery.Result]) -> None:
    assert len(orrery_runs) == 6
    for name, result in orrery_runs.items():
        assert result.envelopes, name
        for envelope in result.envelopes:
            assert validate_envelope(envelope) == [], (name, envelope["seq"])


def test_heads_recompute_identically_on_every_scenario(orrery_runs: dict[str, orrery.Result]) -> None:
    for name, result in orrery_runs.items():
        for envelope in result.envelopes:
            assert chain_head(envelope) == envelope["head"] == orrery.chain_head(envelope), (name, envelope["seq"])
        assert verify_chain(result.envelopes) == [], name
        assert result.envelopes[-1]["head"] == result.summary["ledger_head"]


def test_genesis_prev_is_the_zero_digest(chain: list[dict[str, Any]]) -> None:
    assert chain[0]["prev"] == ZERO_DIGEST == orrery.ZERO_DIGEST


def test_empty_chain_is_intact() -> None:
    assert verify_chain([]) == []


# ── validate_envelope: one violation at a time ──


def mutate(env: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    target = env
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return env


_DELETE = object()

SCHEMA_VIOLATIONS = [
    (("v",), 2),
    (("v",), True),
    (("household",), "household"),
    (("household",), "hh_short"),
    (("source",), "camera"),
    (("source_event_id",), ""),
    (("source_event_id",), "x" * 129),
    (("kind",), "door.knock"),
    (("valid", "lo"), -1),
    (("valid", "extra"), 1),
    (("valid", "hi"), _DELETE),
    (("src_time",), "1792486800800"),
    (("received_at",), None),
    (("seq",), 0),
    (("raw_digest",), "sha256:" + "A" * 64),
    (("payload_digest",), None),
    (("auth", "scheme"), "basic"),
    (("auth", "verified"), 1),
    (("auth", "extra"), True),
    (("prev",), "sha256:" + "0" * 63),
    (("head",), _DELETE),
    (("extra",), 1),
]


@pytest.mark.parametrize(("path", "value"), SCHEMA_VIOLATIONS, ids=[f"{'.'.join(p)}={v!r}"[:60]
                                                                    for p, v in SCHEMA_VIOLATIONS])
def test_schema_violations_are_detected(env: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    assert validate_envelope(mutate(env, path, value))


def test_e1_lo_greater_than_hi_is_detected(env: dict[str, Any]) -> None:
    env["valid"] = {"lo": env["valid"]["hi"] + 1, "hi": env["valid"]["hi"]}
    assert any("E-1" in p for p in validate_envelope(env))


def test_e1_point_interval_is_valid(env: dict[str, Any]) -> None:
    env["valid"] = {"lo": 5, "hi": 5}
    assert validate_envelope(env) == []


@pytest.mark.parametrize("value", [1.0, float("nan"), float("inf")], ids=repr)
def test_e5_floats_are_detected_even_where_the_schema_allows_integers(env: dict[str, Any], value: float) -> None:
    env["received_at"] = value
    assert any("E-5" in p for p in validate_envelope(env))


def test_e5_unsafe_integer_is_detected(env: dict[str, Any]) -> None:
    env["received_at"] = 2**53
    assert any("E-5" in p for p in validate_envelope(env))


@pytest.mark.parametrize("name", ["raw_digest", "payload_digest", "prev", "head"])
def test_digest_formats_reject_a_trailing_newline(env: dict[str, Any], name: str) -> None:
    env[name] = env[name] + "\n"
    assert any(name in p for p in validate_envelope(env))


def test_household_rejects_a_trailing_newline(env: dict[str, Any]) -> None:
    env["household"] += "\n"
    assert validate_envelope(env)


def test_source_must_match_its_auth_scheme(env: dict[str, Any]) -> None:
    env["auth"]["scheme"] = "bridge-hmac"
    assert any("auth.scheme" in p for p in validate_envelope(env))


def test_source_must_match_its_kind(env: dict[str, Any]) -> None:
    env["kind"] = "wrist.utterance"
    assert any("$.kind" in p for p in validate_envelope(env))


def test_null_raw_digest_is_allowed_for_system_events(orrery_runs: dict[str, orrery.Result]) -> None:
    system = [e for e in orrery_runs["verified_plumber"].envelopes if e["source"] == "system"]
    assert system and all(e["raw_digest"] is None and validate_envelope(e) == [] for e in system)


@pytest.mark.parametrize("value", [None, [], "envelope", 1])
def test_non_objects_are_invalid(value: Any) -> None:
    assert validate_envelope(value)


# ── verify_chain: E-2, E-3 and head recomputation ──


def copied(chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return copy.deepcopy(chain)


def test_e2_gap_is_detected(chain: list[dict[str, Any]]) -> None:
    broken = copied(chain)
    del broken[3]
    problems = verify_chain(broken)
    assert any("E-2" in p for p in problems) and any("E-3" in p for p in problems)


def test_e2_seq_not_starting_at_one_is_detected(chain: list[dict[str, Any]]) -> None:
    assert any("E-2" in p for p in verify_chain(copied(chain)[1:]))


def test_e2_reordering_is_detected(chain: list[dict[str, Any]]) -> None:
    broken = copied(chain)
    broken[2], broken[3] = broken[3], broken[2]
    assert any("E-2" in p for p in verify_chain(broken))


def test_e3_wrong_prev_is_detected_even_with_a_consistent_head(chain: list[dict[str, Any]]) -> None:
    broken = copied(chain)
    broken[4]["prev"] = ZERO_DIGEST
    broken[4]["head"] = chain_head(broken[4])
    problems = verify_chain(broken)
    assert any("seq 5" in p and "E-3" in p for p in problems)


def test_tampered_content_breaks_its_head(chain: list[dict[str, Any]]) -> None:
    broken = copied(chain)
    broken[6]["received_at"] += 1
    assert any("seq 7: head does not match" in p for p in verify_chain(broken))


def test_rehashing_a_tampered_entry_breaks_the_next_link(chain: list[dict[str, Any]]) -> None:
    broken = copied(chain)
    broken[6]["received_at"] += 1
    broken[6]["head"] = chain_head(broken[6])
    problems = verify_chain(broken)
    assert any("seq 8" in p and "E-3" in p for p in problems)


def test_e1_inside_a_chain_is_detected(chain: list[dict[str, Any]]) -> None:
    broken = copied(chain)
    broken[0]["valid"] = {"lo": 10, "hi": 9}
    broken[0]["head"] = chain_head(broken[0])
    assert any("E-1" in p for p in verify_chain(broken))


def test_mixed_households_are_detected(chain: list[dict[str, Any]]) -> None:
    broken = copied(chain)
    broken[1]["household"] = "hh_other0001"
    broken[1]["head"] = chain_head(broken[1])
    assert any("household" in p for p in verify_chain(broken))


def test_malformed_entries_are_reported_not_raised(chain: list[dict[str, Any]]) -> None:
    broken = copied(chain)
    del broken[2]["head"]
    broken[5]["payload_digest"] = 1.5
    problems = verify_chain(broken)
    assert any("position 3" in p for p in problems) and any("E-5" in p for p in problems)


def test_head_excludes_only_the_head_field(env: dict[str, Any]) -> None:
    head = chain_head(env)
    env["head"] = "sha256:" + "f" * 64
    assert chain_head(env) == head
    env["seq"] += 1
    assert chain_head(env) != head
