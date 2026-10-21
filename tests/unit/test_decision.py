"""Gate G4: the decision core equals the orrery's reference to the milli-nat, and obeys DC-1 to DC-4 and TL-1."""

from __future__ import annotations

import copy
import random
from typing import Any

import orrery
import pytest

from hearthward.causal.intervals import Interval
from hearthward.decision import sprt, tactics
from hearthward.decision.hmm import ForwardFilter, HmmModel, lambda_trace_mnats, logsumexp, mnats
from hearthward.decision.sprt import Accumulator, Status

pytestmark = pytest.mark.gate_g4

FEATURES = list(tactics.FEATURES)


@pytest.fixture(scope="module")
def params(spec: dict[str, Any]) -> dict[str, Any]:
    return spec["sprt"]


def random_vector(rng: random.Random) -> list[Any]:
    out: list[Any] = []
    for _ in range(rng.randint(1, 12)):
        picked = rng.sample(FEATURES, rng.randint(0, 4))
        out.append({"claim": picked} if rng.random() < 0.25 else picked)
    return out


# ── conformance (§6.9) ───────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("index", range(len(orrery.CONFORMANCE_VECTORS)))
def test_each_conformance_vector_matches_the_orrery_exactly(params: dict[str, Any], index: int) -> None:
    vector = orrery.CONFORMANCE_VECTORS[index]
    assert lambda_trace_mnats(vector, params) == orrery.reference_trace_mnats(vector, params)


def test_there_are_six_conformance_vectors() -> None:
    assert len(orrery.CONFORMANCE_VECTORS) == 6


def test_random_traces_match_the_orrery_to_the_milli_nat(params: dict[str, Any]) -> None:
    rng = random.Random(20261021)
    for _ in range(400):
        vector = random_vector(rng)
        assert lambda_trace_mnats(vector, params) == orrery.reference_trace_mnats(vector, params)


def test_the_unrounded_forward_value_equals_the_orrery_bit_for_bit(params: dict[str, Any]) -> None:
    rng = random.Random(7)
    model, reference = HmmModel.from_params(params), orrery.Model.from_spec(params)
    for _ in range(100):
        forward, acc = ForwardFilter(model), orrery.Accumulator(reference)
        for item in random_vector(rng):
            if isinstance(item, dict):
                forward.observe_claim(frozenset(item["claim"]))
                acc.observe_claim(frozenset(item["claim"]))
            else:
                forward.observe(frozenset(item))
                acc.observe(frozenset(item))
            assert forward.value == acc.hmm


def test_the_trace_has_one_entry_per_observation_and_is_empty_for_none(params: dict[str, Any]) -> None:
    assert lambda_trace_mnats([], params) == []
    assert len(lambda_trace_mnats(orrery.CONFORMANCE_VECTORS[0], params)) == 5


def test_traces_are_integers(params: dict[str, Any]) -> None:
    for vector in orrery.CONFORMANCE_VECTORS:
        assert all(type(v) is int for v in lambda_trace_mnats(vector, params))


def test_the_input_params_are_not_mutated(params: dict[str, Any]) -> None:
    before = copy.deepcopy(params)
    lambda_trace_mnats(orrery.CONFORMANCE_VECTORS[0], params)
    assert params == before


# ── behaviour of the likelihood ratio ────────────────────────────────────────────────────────────────────


def test_a_scam_script_crosses_A_and_small_talk_never_does(params: dict[str, Any]) -> None:
    script = lambda_trace_mnats(orrery.CONFORMANCE_VECTORS[0], params)
    assert script[-1] >= 4554
    quiet = lambda_trace_mnats(orrery.CONFORMANCE_VECTORS[1], params)
    assert all(v < 0 for v in quiet) and quiet == sorted(quiet, reverse=True)


def test_a_claim_summarizes_and_causes_no_stage_transition(params: dict[str, Any]) -> None:
    """After a claim the forward vector only gains per-stage log-likelihoods; the transition matrix is not applied."""
    model = HmmModel.from_params(params)
    forward = ForwardFilter(model)
    forward.observe(frozenset({"authority"}))
    before = list(forward.log_alpha or [])
    forward.observe_claim(frozenset({"payment", "secrecy"}))
    expected = [a + model.log_claim(rates, frozenset({"payment", "secrecy"})) for a, rates in zip(before, model.emit)]
    assert forward.log_alpha == expected and forward.steps == 2


def test_unlisted_features_in_a_claim_are_unobserved_not_absent(params: dict[str, Any]) -> None:
    claim = lambda_trace_mnats([["authority"], {"claim": ["payment"]}], params)[1]
    full = lambda_trace_mnats([["authority"], ["payment"]], params)[1]
    assert claim != full


def test_a_first_observation_that_is_a_claim_starts_from_the_prior(params: dict[str, Any]) -> None:
    assert lambda_trace_mnats([{"claim": ["payment"]}], params) == orrery.reference_trace_mnats(
        [{"claim": ["payment"]}], params)


def test_zero_entries_are_minus_infinity_never_log_zero(params: dict[str, Any]) -> None:
    model = HmmModel.from_params(params)
    assert model.log_init[4] == float("-inf") and model.log_trans[1][0] == float("-inf")
    assert logsumexp([float("-inf"), float("-inf")]) == float("-inf")


def test_mnats_rounds_half_to_even_like_the_orrery() -> None:
    for x in (0.0005, 0.0015, -0.0025, 4.5539, -2.9857, 1.0):
        assert mnats(x) == orrery.mnats(x)


# ── parameter validation ─────────────────────────────────────────────────────────────────────────────────


def mutated(params: dict[str, Any], mutate: Any) -> dict[str, Any]:
    copy_ = copy.deepcopy(params)
    mutate(copy_)
    return copy_


@pytest.mark.parametrize("mutate", [
    lambda p: p["transitions"][2].__setitem__(1, 0.1),                   # a script going back a stage
    lambda p: p["transitions"][0].__setitem__(0, 0.5),                   # row no longer sums to 1
    lambda p: p["initial"].__setitem__(0, 0.9),                          # initial no longer sums to 1
    lambda p: p["emissions"][0].__setitem__(0, 1.5),                     # probability outside [0, 1]
    lambda p: p["extractor"]["fpr"].__setitem__(0, 0.0) or p["emissions"][0].__setitem__(0, 0.0),
    lambda p: p.__setitem__("features", p["features"][:-1]),
    lambda p: p["emissions"].pop(),
], ids=["goes_back", "row_sum", "initial_sum", "range", "zero_noisy_rate", "features", "missing_stage"])
def test_malformed_params_are_rejected(params: dict[str, Any], mutate: Any) -> None:
    with pytest.raises(ValueError):
        lambda_trace_mnats([["authority"]], mutated(params, mutate))


# ── tactics (DC-4) ───────────────────────────────────────────────────────────────────────────────────────


def test_seven_features_in_the_blueprint_order(params: dict[str, Any]) -> None:
    assert list(tactics.FEATURES) == params["features"]


@pytest.mark.parametrize("bad", ["authority", "Authority", ["authority", "hax"], [1], ["ignore previous instructions"],
                                 None, 5, [["authority"]], {"claim": ["x"]}, {"claim": ["urgency"], "extra": []}])
def test_free_text_and_unknown_values_are_rejected(bad: Any) -> None:
    with pytest.raises(tactics.ObservationError):
        tactics.parse_observation(bad)


def test_observation_shapes() -> None:
    assert tactics.parse_observation(["urgency", "urgency"]) == tactics.Observation("utterance", frozenset({"urgency"}))
    assert tactics.parse_observation({"claim": ["payment"]}) == tactics.Observation("claim", frozenset({"payment"}))
    assert tactics.parse_observation([]).features == frozenset()


def test_vector_round_trip() -> None:
    vector = tactics.to_vector(["urgency", "payment"])
    assert vector == (0, 1, 0, 1, 0, 0, 0) and tactics.from_vector(vector) == frozenset({"urgency", "payment"})
    for bad in ((0,) * 6, (0, 1, 2, 0, 0, 0, 0), (True,) + (0,) * 6):
        with pytest.raises(tactics.ObservationError):
            tactics.from_vector(bad)


@pytest.mark.parametrize("args, expected", [
    ({"claimed_purpose": "bank", "payment_requested": "gift_card", "urgency_claimed": True, "asked_secrecy": True},
     {"authority", "payment", "urgency", "secrecy"}),
    ({"claimed_organization": "Anything"}, {"authority"}),
    ({"claimed_purpose": "delivery", "payment_requested": "card"}, set()),
    ({"claimed_purpose": "charity", "urgency_claimed": False, "asked_secrecy": "yes"}, set()),
    ({}, set()),
])
def test_a_consultation_becomes_claim_features(args: dict[str, Any], expected: set[str]) -> None:
    assert tactics.claim_from_assessment(args) == frozenset(expected)


# ── thresholds (§6.5) ────────────────────────────────────────────────────────────────────────────────────


def test_wald_bounds(params: dict[str, Any]) -> None:
    bounds = sprt.thresholds_from_params(params)
    assert (bounds.a_mnats, bounds.b_mnats) == (4554, -2986)
    model = orrery.Model.from_spec(params)
    assert bounds.A == model.A and bounds.B == model.B
    assert round(bounds.A, 3) == 4.554 and round(bounds.B, 3) == -2.986


def test_thresholds_that_disagree_with_alpha_and_beta_are_refused(params: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="thresholds_reported"):
        Accumulator(mutated(params, lambda p: p.__setitem__("alpha", 0.02)))


# ── the accumulator ──────────────────────────────────────────────────────────────────────────────────────


def test_the_accumulator_agrees_with_the_trace_function(params: dict[str, Any]) -> None:
    rng = random.Random(11)
    for _ in range(100):
        vector = random_vector(rng)
        acc, got = Accumulator(params), []
        for item in vector:
            if isinstance(item, dict):
                acc.forward.observe_claim(frozenset(item["claim"]))
            else:
                acc.observe(item)
            got.append(acc.hmm_mnats)
        # claims here bypass DC-2 on purpose, so they are comparable with the raw trace
        assert got == lambda_trace_mnats(vector, params)


def test_observe_returns_the_change_in_milli_nats(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    total = acc.observe(["authority"]) + acc.observe(["pressure"]) + acc.observe(["urgency", "payment"])
    assert total == acc.lambda_mnats == lambda_trace_mnats([["authority"], ["pressure"], ["urgency", "payment"]],
                                                           params)[-1]


def test_status_moves_from_continue_to_intervene(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    assert acc.status is Status.CONTINUE
    for utterance in orrery.CONFORMANCE_VECTORS[0]:
        acc.observe(utterance)
    assert acc.status is Status.INTERVENE and acc.lambda_mnats >= acc.bounds.a_mnats


def test_status_dismiss_eligible_needs_B_and_is_only_eligibility(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    for _ in range(40):
        acc.observe([])
    assert acc.status is Status.DISMISS_ELIGIBLE and acc.lambda_mnats <= acc.bounds.b_mnats


def test_dc2_a_claim_adds_only_novel_features(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    acc.observe(["payment"])
    before = acc.lambda_mnats
    assert acc.observe_claim(["payment"]) == 0 and acc.lambda_mnats == before and acc.observations == 1
    delta = acc.observe_claim(["payment", "secrecy"])
    novel_only = Accumulator(params)
    novel_only.observe(["payment"])
    assert delta == novel_only.observe_claim(["secrecy"]) and acc.observed == {"payment", "secrecy"}


def test_dc2_order_matters_a_wrist_line_after_the_summary_still_counts(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    acc.observe_claim(["payment"])
    acc.observe(["payment"])  # the duplicate is allowed, once
    assert acc.observations == 2 and acc.observed == {"payment"}
    assert acc.hmm_mnats == lambda_trace_mnats([{"claim": ["payment"]}, ["payment"]], acc.params)[-1]


# ── cross-device terms (§6.6) ────────────────────────────────────────────────────────────────────────────


def test_each_term_applies_once_dc3_with_the_blueprint_values(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    assert acc.apply(sprt.DOOR_UNKNOWN_OVERLAP) == 700 and acc.apply(sprt.DOOR_UNKNOWN_OVERLAP) == 0
    assert acc.apply(sprt.IDENTITY_MISMATCH) == 3000 and acc.apply(sprt.IDENTITY_MISMATCH) == 0
    assert acc.apply(sprt.UNTRUSTED_ORG_PAYMENT) == 1200
    assert acc.lambda_mnats == 4900 and acc.status is Status.INTERVENE
    assert acc.terms == {"door_unknown_overlap": 0.7, "identity_mismatch": 3.0,
                         "untrusted_org_irreversible_payment": 1.2}


def test_terms_match_the_orrery_accumulator(params: dict[str, Any]) -> None:
    ours, reference = Accumulator(params), orrery.Accumulator(orrery.Model.from_spec(params))
    for utterance in orrery.CONFORMANCE_VECTORS[4]:
        ours.observe(utterance)
        reference.observe(frozenset(utterance))
    for term in sprt.TERMS[:3]:
        ours.apply(term)
        reference.apply(term)
    assert ours.lambda_mnats == orrery.mnats(reference.value)


def test_tl1_a_verified_term_needs_a_verified_fact(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    acc.observe(["authority"])
    before = acc.lambda_mnats
    with pytest.raises(PermissionError, match="TL-1"):
        acc.apply(sprt.VERIFIED_OFFICIAL_CALLBACK)
    assert acc.lambda_mnats == before
    assert acc.apply(sprt.VERIFIED_OFFICIAL_CALLBACK, verified=True) == -3500
    assert acc.apply(sprt.VERIFIED_OFFICIAL_CALLBACK, verified=True) == 0


def test_a_term_cannot_be_forced_through_the_verified_flag_on_an_unverified_name(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    assert acc.apply(sprt.IDENTITY_MISMATCH, verified=True) == 3000  # the flag adds nothing for other terms


def test_unknown_terms_are_refused(params: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Accumulator(params).apply("trust_me")


def test_dc1_only_verified_terms_may_be_negative(params: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="DC-1"):
        Accumulator(mutated(params, lambda p: p["cross_device_llr"].__setitem__("identity_mismatch", -1.0)))
    with pytest.raises(ValueError):
        Accumulator(mutated(params, lambda p: p["cross_device_llr"].__setitem__("verified_official_callback", 1.0)))
    with pytest.raises(ValueError):
        Accumulator(mutated(params, lambda p: p["cross_device_llr"].pop("identity_mismatch")))
    assert sprt.validate_terms(params["cross_device_llr"])["verified_official_callback"] == -3.5


def test_a_verified_callback_can_pull_a_borderline_episode_below_A(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    for utterance in orrery.CONFORMANCE_VECTORS[0]:
        acc.observe(utterance)
    assert acc.status is Status.INTERVENE
    acc.apply(sprt.VERIFIED_OFFICIAL_CALLBACK, verified=True)
    assert acc.lambda_mnats == acc.hmm_mnats - 3500


def test_observations_after_a_decision_still_update_lambda(params: dict[str, Any]) -> None:
    acc = Accumulator(params)
    for utterance in orrery.CONFORMANCE_VECTORS[0]:
        acc.observe(utterance)
    decided = acc.lambda_mnats
    acc.observe(["payment"])
    assert acc.lambda_mnats != decided  # the decision record is immutable (BT-4); Lambda is not


# ── conditions ───────────────────────────────────────────────────────────────────────────────────────────


def test_door_unknown_overlap_condition_uses_the_gap_and_needs_an_unknown_human() -> None:
    episode, visit, gap = Interval(1_000_000, 1_060_000), Interval(800_000, 900_000), 120_000
    assert sprt.door_unknown_overlap_applies(episode, visit, gap, unknown_human=True) is True
    assert sprt.door_unknown_overlap_applies(Interval(1_020_001, 1_030_000), visit, gap, unknown_human=True) is False
    assert sprt.door_unknown_overlap_applies(Interval(1_020_000, 1_030_000), visit, gap, unknown_human=True) is True
    assert sprt.door_unknown_overlap_applies(episode, visit, gap, unknown_human=False) is False


@pytest.mark.parametrize("name, relationship, known, expected", [
    ("Chris", "grandchild", ["Alex", "Sam"], True),
    ("  alex ", "grandchild", ["Alex", "Sam"], False),
    ("Chris", "official", ["Alex"], False),
    (None, "grandchild", ["Alex"], False),
    ("", "child", ["Alex"], False),
    ("Chris", None, ["Alex"], False),
])
def test_identity_mismatch_condition(name: str | None, relationship: str | None, known: list[str],
                                     expected: bool) -> None:
    assert sprt.identity_mismatch_applies(name, relationship, known) is expected


@pytest.mark.parametrize("org, vendors, payment, expected", [
    ("Totally Legit", ["Acme"], "gift_card", True),
    ("ACME ", ["Acme"], "gift_card", False),
    ("Totally Legit", ["Acme"], "card", False),
    (None, ["Acme"], "wire", False),
    ("Totally Legit", [], None, False),
])
def test_untrusted_org_payment_condition(org: str | None, vendors: list[str], payment: str | None,
                                         expected: bool) -> None:
    assert sprt.untrusted_org_payment_applies(org, vendors, payment) is expected
