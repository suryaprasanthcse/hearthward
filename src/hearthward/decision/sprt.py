"""Wald's sequential probability ratio test over the HMM likelihood (blueprint §6.5 to §6.7).

    A = ln((1 - beta) / alpha) = +4.554  ->  intervene (4554 mnats)
    B = ln(beta / (1 - alpha)) = -2.986  ->  dismiss   (-2986 mnats)

`Accumulator` is Lambda: the forward pass plus the cross-device terms applied so far. Crossing B only makes a
dismissal *eligible*: dismissing also needs watermark passage and `certainly_*` relations (BT-A), which the causal
engine decides. Crossing A is a decision to intervene.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hearthward.causal.intervals import Interval, possibly_overlaps
from hearthward.decision.hmm import ForwardFilter, HmmModel, mnats
from hearthward.decision.tactics import IRREVERSIBLE_PAYMENTS, validate_features

DOOR_UNKNOWN_OVERLAP = "door_unknown_overlap"
IDENTITY_MISMATCH = "identity_mismatch"
UNTRUSTED_ORG_PAYMENT = "untrusted_org_irreversible_payment"
VERIFIED_OFFICIAL_CALLBACK = "verified_official_callback"
TERMS = (DOOR_UNKNOWN_OVERLAP, IDENTITY_MISMATCH, UNTRUSTED_ORG_PAYMENT, VERIFIED_OFFICIAL_CALLBACK)


class Status(StrEnum):
    CONTINUE = "continue"
    INTERVENE = "intervene"  # Lambda >= A
    DISMISS_ELIGIBLE = "dismiss_eligible"  # Lambda <= B; still needs watermarks and certainly_* (BT-A)


@dataclass(frozen=True)
class Thresholds:
    alpha: float
    beta: float

    @property
    def A(self) -> float:
        return math.log((1 - self.beta) / self.alpha)

    @property
    def B(self) -> float:
        return math.log(self.beta / (1 - self.alpha))

    @property
    def a_mnats(self) -> int:
        return mnats(self.A)

    @property
    def b_mnats(self) -> int:
        return mnats(self.B)


def thresholds_from_params(params: Mapping[str, Any]) -> Thresholds:
    """Wald's bounds from alpha and beta, checked against the block's `thresholds_reported` (A and B change only
    through that block)."""
    found = Thresholds(params["alpha"], params["beta"])
    reported = params["thresholds_reported"]
    if round(found.A, 3) != reported["A"] or round(found.B, 3) != reported["B"]:
        raise ValueError("thresholds_reported disagrees with alpha and beta")
    return found


def validate_terms(cross: Mapping[str, float]) -> dict[str, float]:
    """DC-1: only terms named `verified_*` may be negative (TL-1), and only the four known terms exist."""
    if set(cross) != set(TERMS):
        raise ValueError("cross_device_llr must name exactly the four cross-device terms")
    for name, llr in cross.items():
        if isinstance(llr, bool) or not isinstance(llr, int | float) or not math.isfinite(llr):
            raise ValueError(f"{name} must be a finite number")
        if llr < 0 and not name.startswith("verified_"):
            raise ValueError(f"DC-1: {name} is negative but is not a verified_* term")
        if name.startswith("verified_") and llr > 0:
            raise ValueError(f"{name} must not raise Lambda")
    return dict(cross)


@dataclass
class Accumulator:
    """Lambda = log L1 - log L0 + the cross-device terms applied so far (§6.4)."""

    params: Mapping[str, Any]
    forward: ForwardFilter = field(init=False)
    bounds: Thresholds = field(init=False)
    cross: dict[str, float] = field(init=False)
    terms: dict[str, float] = field(default_factory=dict)
    observed: set[str] = field(default_factory=set)
    observations: int = 0

    def __post_init__(self) -> None:
        self.forward = ForwardFilter(HmmModel.from_params(self.params))
        self.bounds = thresholds_from_params(self.params)
        self.cross = validate_terms(self.params["cross_device_llr"])

    # -- Lambda ----------------------------------------------------------------------------------------------

    @property
    def hmm(self) -> float:
        return self.forward.value

    @property
    def value(self) -> float:
        return self.hmm + sum(self.terms.values())

    @property
    def hmm_mnats(self) -> int:
        return mnats(self.hmm)

    @property
    def lambda_mnats(self) -> int:
        return mnats(self.value)

    @property
    def status(self) -> Status:
        if self.lambda_mnats >= self.bounds.a_mnats:
            return Status.INTERVENE
        if self.lambda_mnats <= self.bounds.b_mnats:
            return Status.DISMISS_ELIGIBLE
        return Status.CONTINUE

    # -- observations ----------------------------------------------------------------------------------------

    def observe(self, features: Collection[str]) -> int:
        """Fold in one utterance; returns the change in Lambda in milli-nats."""
        observed = validate_features(features, self.forward.model.features)
        before = self.lambda_mnats
        self.forward.observe(observed)
        self.observed |= observed
        self.observations += 1
        return self.lambda_mnats - before

    def observe_claim(self, features: Collection[str]) -> int:
        """Fold in a ROOM claim as a partial observation, only for features not yet observed (DC-2), with no stage
        transition. A claim with nothing new changes nothing."""
        novel = validate_features(features, self.forward.model.features) - self.observed
        if not novel:
            return 0
        before = self.lambda_mnats
        self.forward.observe_claim(novel)
        self.observed |= novel
        self.observations += 1
        return self.lambda_mnats - before

    def apply(self, term: str, *, verified: bool = False) -> int:
        """Apply a cross-device term at most once per episode (DC-3); returns the change in milli-nats.

        A `verified_*` term needs `verified=True`, which only an independent channel may set (TL-1); a language
        model's output is never verified (TL-3)."""
        if term not in self.cross:
            raise ValueError(f"unknown cross-device term: {term[:40]!r}")
        if term.startswith("verified_") and not verified:
            raise PermissionError(f"{term} needs a verified fact (TL-1)")
        if term in self.terms:
            return 0
        before = self.lambda_mnats
        self.terms[term] = self.cross[term]
        return self.lambda_mnats - before


# ── conditions for the cross-device terms (§6.6) ───────────────────────────────────────────────────────────


def _fold(name: str) -> str:
    return " ".join(name.casefold().split())


def door_unknown_overlap_applies(episode: Interval, visit: Interval, visit_gap_ms: int, *, unknown_human: bool) -> bool:
    """`possibly_overlaps(episode, [visit.lo, visit.hi + visit_gap_ms])` for a visit by an unknown human."""
    return unknown_human and possibly_overlaps(episode, Interval(visit.lo, visit.hi + visit_gap_ms))


def identity_mismatch_applies(claimed_name: str | None, relationship: str | None,
                              verified_names: Collection[str]) -> bool:
    """A claimed relative whose name conflicts with the household's verified facts. The name is compared, never
    interpreted, and a missing name is not evidence on its own."""
    if relationship not in ("grandchild", "child", "other_family") or not claimed_name:
        return False
    return _fold(claimed_name) not in {_fold(n) for n in verified_names}


def untrusted_org_payment_applies(organization: str | None, trusted_vendors: Collection[str],
                                  payment_requested: str | None) -> bool:
    """A ROOM claim names an organization outside the trusted vendors and asks for an irreversible payment."""
    if not organization or payment_requested not in IRREVERSIBLE_PAYMENTS:
        return False
    return _fold(organization) not in {_fold(v) for v in trusted_vendors}
