"""HW-JCS (blueprint §4.3): production canonicalization equals the orrery's, and the C-2 profile is enforced."""

from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from enum import IntEnum
from typing import Any

import orrery
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hearthward.contracts.canonical import (
    SAFE_INT,
    ProfileError,
    canonical,
    check_profile,
    digest,
    parse_strict,
    tagged_digest,
)

pytestmark = pytest.mark.gate_g1


def reference_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ── equality with the orrery ──


def test_every_orrery_envelope_canonicalizes_identically(orrery_runs: dict[str, orrery.Result]) -> None:
    envelopes = [e for result in orrery_runs.values() for e in result.envelopes]
    assert len(envelopes) == 59
    for envelope in envelopes:
        assert canonical(envelope) == orrery.canonical(envelope)
        assert digest(envelope) == orrery.digest(envelope)
        assert tagged_digest(b"hw-ledger-v1", envelope) == orrery.tagged_digest(b"hw-ledger-v1", envelope)


@pytest.mark.parametrize("name", sorted(orrery.SCENARIOS))
def test_every_orrery_payload_canonicalizes_identically(spec: dict[str, Any], name: str) -> None:
    sim = orrery.Orrery(spec, orrery.SCENARIOS[name]())
    for _ in sim.run():
        pass
    payloads = [m["payload"] for m in sim.meta]
    assert payloads
    for payload in payloads:
        assert canonical(payload) == orrery.canonical(payload)
        assert digest(payload) == orrery.digest(payload)


def test_decision_records_canonicalize_identically(orrery_runs: dict[str, orrery.Result]) -> None:
    rows = [[r.as_dict() for r in result.rows] for result in orrery_runs.values()]
    for trace in rows:
        assert canonical(trace) == orrery.canonical(trace)


# ── the RFC 8785 property inside the profile ──

KEYS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=12)
SCALARS = (st.none() | st.booleans() | st.integers(min_value=-SAFE_INT, max_value=SAFE_INT)
           | st.text(st.characters(codec="utf-8"), max_size=24))
PROFILE = st.recursive(SCALARS, lambda inner: st.lists(inner, max_size=5) | st.dictionaries(KEYS, inner, max_size=5),
                       max_leaves=30)


@settings(max_examples=400, deadline=None, derandomize=True)
@given(PROFILE)
def test_rfc8785_equals_sorted_compact_json_inside_the_profile(value: Any) -> None:
    assert canonical(value) == reference_bytes(value)
    assert canonical(value) == orrery.canonical(value)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(st.dictionaries(KEYS, SCALARS, max_size=8))
def test_key_order_does_not_change_the_bytes(value: dict[str, Any]) -> None:
    reordered = dict(reversed(list(value.items())))
    assert canonical(reordered) == canonical(value)


# ── rejection classes (C-2, E-5) ──


@pytest.mark.parametrize("value", [
    1.0, 0.5, -0.0, math.nan, math.inf, -math.inf,
    {"a": 1.5}, [1, 2.0], {"n": float("nan")},
], ids=lambda v: repr(v))
def test_floats_nan_and_infinities_are_rejected(value: Any) -> None:
    with pytest.raises(ProfileError):
        canonical(value)


@pytest.mark.parametrize("value", [2**53, -(2**53), 2**64, -(2**63), {"x": [SAFE_INT + 1]}])
def test_unsafe_integers_are_rejected(value: Any) -> None:
    with pytest.raises(ProfileError):
        canonical(value)


def test_safe_integer_bounds_are_accepted() -> None:
    assert canonical([SAFE_INT, -SAFE_INT, 0]) == b"[9007199254740991,-9007199254740991,0]"


@pytest.mark.parametrize("value", [
    {1: "a"}, {(1,): "a"}, {None: 1}, {b"a": 1}, {True: 1},
    {"A": 1}, {"camelCase": 1}, {"kebab-key": 1}, {"": 1}, {"sp ace": 1}, {"é": 1},
    {"key\n": 1}, {"key\n\n": 1}, {"ke\x00y": 1}, {"ｋｅｙ": 1},
], ids=lambda v: repr(v))
def test_keys_outside_snake_case_ascii_are_rejected(value: Any) -> None:
    with pytest.raises(ProfileError):
        canonical(value)


class Flag(IntEnum):
    ON = 1


class Text(str):
    pass


@pytest.mark.parametrize("value", [
    (1, 2), {1, 2}, frozenset(), b"bytes", bytearray(b"x"), object(), Flag.ON, Text("x"),
    OrderedDict(a=1), {"a": (1,)}, [{"b": {1, 2}}], 1j,
], ids=lambda v: type(v).__name__)
def test_other_types_and_subclasses_are_rejected(value: Any) -> None:
    with pytest.raises(ProfileError):
        canonical(value)


def test_lone_surrogates_are_rejected() -> None:
    with pytest.raises(ProfileError):
        canonical("\ud800")
    with pytest.raises(ProfileError):
        canonical({"a": ["ok", "\udfff"]})


def test_excessive_nesting_is_rejected_before_recursion_fails() -> None:
    value: Any = 0
    for _ in range(100):
        value = [value]
    with pytest.raises(ProfileError):
        canonical(value)
    with pytest.raises(ProfileError):
        check_profile(value)


def test_bool_and_none_are_inside_the_profile() -> None:
    assert canonical({"t": True, "f": False, "n": None}) == b'{"f":false,"n":null,"t":true}'


def test_profile_error_is_a_value_error() -> None:
    assert issubclass(ProfileError, ValueError)


# ── known-answer vectors ──

VECTORS: list[tuple[Any, bytes]] = [
    (None, b"null"),
    (True, b"true"),
    (0, b"0"),
    (-1, b"-1"),
    ("", b'""'),
    ([], b"[]"),
    ({}, b"{}"),
    ({"b": 2, "a": 1}, b'{"a":1,"b":2}'),
    ({"a_1": [1, {"z": None, "a": "x"}]}, b'{"a_1":[1,{"a":"x","z":null}]}'),
    ({"k": "é€\U0001f600"}, '{"k":"é€\U0001f600"}'.encode()),
    ({"k": "q\"b\\s/"}, b'{"k":"q\\"b\\\\s/"}'),
    ({"k": "\x00\x08\t\n\x0c\r\x1f\x7f"}, b'{"k":"\\u0000\\b\\t\\n\\f\\r\\u001f\x7f"}'),
    ({"k": "  "}, '{"k":"  "}'.encode()),
    ({"10": 1, "9": 2, "_": 3, "a": 4}, b'{"10":1,"9":2,"_":3,"a":4}'),
]


@pytest.mark.parametrize(("value", "expected"), VECTORS, ids=[repr(v) for v, _ in VECTORS])
def test_known_answer_vectors(value: Any, expected: bytes) -> None:
    assert canonical(value) == expected


def test_digest_known_answers() -> None:
    assert digest({}) == "sha256:" + hashlib.sha256(b"{}").hexdigest()
    assert digest({"b": 2, "a": 1}) == "sha256:" + hashlib.sha256(b'{"a":1,"b":2}').hexdigest()
    assert tagged_digest(b"hw-ledger-v1", {"a": 1}) == (
        "sha256:" + hashlib.sha256(b'hw-ledger-v1\x00{"a":1}').hexdigest())


@pytest.mark.parametrize("tag", [b"", b"hw\x00ledger", "hw-ledger-v1"], ids=repr)
def test_tags_are_nonempty_bytes_without_nul(tag: Any) -> None:
    with pytest.raises(ValueError):
        tagged_digest(tag, {})


# ── security-review regressions (docs/gates/G1_COMPLETION_LOG.md) ──


def test_l6_profile_check_is_linear_in_the_input() -> None:
    import time

    wide = {"a" * 200_000: [0] * 200_000}  # a quadratic path build would copy ~4e10 bytes here
    start = time.perf_counter()
    check_profile(wide)
    assert time.perf_counter() - start < 5.0


def test_profile_errors_still_name_the_path() -> None:
    with pytest.raises(ProfileError, match=r"^\$\.a\[1\]\.b_2: float"):
        check_profile({"a": [0, {"b_2": 1.5}]})


@pytest.mark.parametrize("raw", [
    b'{"x": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}', b'{"a": 1, "a": 2}', b"[" * 100_000, b"\xff",
    b"{", b"",
], ids=["nan", "inf", "-inf", "duplicate-key", "deep", "not-utf8", "truncated", "empty"])
def test_parse_strict_rejects_with_value_error(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_strict(raw)


def test_parse_strict_round_trips_canonical_bytes(orrery_runs: dict[str, orrery.Result]) -> None:
    for envelope in orrery_runs["late_and_lossy"].envelopes:
        assert parse_strict(canonical(envelope)) == envelope
