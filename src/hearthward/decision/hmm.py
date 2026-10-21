"""The five-stage left-to-right HMM forward pass of blueprint §6.4, in log space.

    log a_1(s) = log pi_s + e_s(f_1)
    log a_t(s) = logsumexp_s'(log a_{t-1}(s') + log T_{s's}) + e_s(f_t)
    log L1_t   = logsumexp_s log a_t(s)          log L0_t = running sum of H0's log-likelihoods
    Lambda_t   = log L1_t - log L0_t

Zero entries of `T` and `pi` are -inf (never `log 0`). A ROOM claim is a partial observation: its listed features
are present, every other feature is marginalized out, and no stage transition happens. The arithmetic (order of
operations included) follows the orrery's reference so the traces agree to the milli-nat.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hearthward.decision.tactics import FEATURES, parse_observation

NEG_INF = -math.inf


def log_or_neg_inf(p: float) -> float:
    return math.log(p) if p > 0 else NEG_INF


def logsumexp(values: Iterable[float]) -> float:
    items = list(values)
    top = max(items)
    if top == NEG_INF:
        return NEG_INF
    return top + math.log(sum(math.exp(v - top) for v in items))


def mnats(nats: float) -> int:
    """Evidence as integer milli-nats, the only form it takes in hashed documents (C-3)."""
    return round(nats * 1000)


def _row(name: str, values: Sequence[float], width: int, *, stochastic: bool) -> tuple[float, ...]:
    if len(values) != width or any(isinstance(v, bool) or not isinstance(v, int | float) for v in values):
        raise ValueError(f"{name} must hold {width} numbers")
    if any(not 0.0 <= v <= 1.0 for v in values):
        raise ValueError(f"{name} holds a probability outside [0, 1]")
    if stochastic and abs(sum(values) - 1.0) > 1e-9:
        raise ValueError(f"{name} must sum to 1")
    return tuple(float(v) for v in values)


@dataclass(frozen=True)
class HmmModel:
    """The parameters of §6.3 with the extractor's noise folded into every rate: p_hat = tpr*p + fpr*(1-p)."""

    features: tuple[str, ...]
    stages: tuple[str, ...]
    log_init: tuple[float, ...]
    log_trans: tuple[tuple[float, ...], ...]
    emit: tuple[tuple[float, ...], ...]
    h0: tuple[float, ...]

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> HmmModel:
        features, stages = tuple(params["features"]), tuple(params["stages"])
        if set(features) != set(FEATURES) or len(features) != len(FEATURES):
            raise ValueError("params.features must be exactly the seven tactic features")
        n, k = len(stages), len(features)
        init = _row("initial", params["initial"], n, stochastic=True)
        trans = [_row(f"transitions[{i}]", r, n, stochastic=True) for i, r in enumerate(params["transitions"])]
        if len(trans) != n:
            raise ValueError("transitions must be a square matrix")
        for i, row in enumerate(trans):  # left-to-right: a script never returns to an earlier stage
            if any(row[j] > 0 for j in range(i)):
                raise ValueError(f"transitions[{i}] moves back to an earlier stage")
        tpr = _row("extractor.tpr", params["extractor"]["tpr"], k, stochastic=False)
        fpr = _row("extractor.fpr", params["extractor"]["fpr"], k, stochastic=False)
        emissions = [_row(f"emissions[{i}]", r, k, stochastic=False) for i, r in enumerate(params["emissions"])]
        if len(emissions) != n:
            raise ValueError("emissions needs one row per stage")

        def noisy(rates: Sequence[float]) -> tuple[float, ...]:
            out = tuple(t * p + f * (1 - p) for p, t, f in zip(rates, tpr, fpr, strict=True))
            if any(not 0.0 < p < 1.0 for p in out):
                raise ValueError("a noisy rate reached 0 or 1; the extractor must keep every rate inside (0, 1)")
            return out

        return cls(features=features, stages=stages,
                   log_init=tuple(log_or_neg_inf(p) for p in init),
                   log_trans=tuple(tuple(log_or_neg_inf(p) for p in row) for row in trans),
                   emit=tuple(noisy(row) for row in emissions),
                   h0=noisy(_row("h0", params["h0"], k, stochastic=False)))

    def log_emit(self, rates: tuple[float, ...], observed: frozenset[str]) -> float:
        """e_s(f): present features contribute log p, absent ones log(1 - p)."""
        return sum(math.log(p) if f in observed else math.log1p(-p) for f, p in zip(self.features, rates, strict=True))

    def log_claim(self, rates: tuple[float, ...], present: frozenset[str]) -> float:
        """A claim: only the listed features contribute; the rest are unobserved."""
        return sum(math.log(p) for f, p in zip(self.features, rates, strict=True) if f in present)


class ForwardFilter:
    """Incremental forward pass. `value` is Lambda without cross-device terms."""

    def __init__(self, model: HmmModel) -> None:
        self.model = model
        self.log_alpha: list[float] | None = None
        self.log_l0 = 0.0
        self.steps = 0

    @property
    def value(self) -> float:
        return 0.0 if self.log_alpha is None else logsumexp(self.log_alpha) - self.log_l0

    def observe(self, observed: frozenset[str]) -> None:
        m = self.model
        e = [m.log_emit(rates, observed) for rates in m.emit]
        if self.log_alpha is None:
            self.log_alpha = [li + ei for li, ei in zip(m.log_init, e, strict=True)]
        else:
            prev = self.log_alpha
            self.log_alpha = [logsumexp(a + row[s] for a, row in zip(prev, m.log_trans, strict=True)) + e[s]
                              for s in range(len(e))]
        self.log_l0 += m.log_emit(m.h0, observed)
        self.steps += 1

    def observe_claim(self, present: frozenset[str]) -> None:
        m = self.model
        e = [m.log_claim(rates, present) for rates in m.emit]
        if self.log_alpha is None:
            self.log_alpha = [li + ei for li, ei in zip(m.log_init, e, strict=True)]
        else:
            self.log_alpha = [a + ei for a, ei in zip(self.log_alpha, e, strict=True)]
        self.log_l0 += m.log_claim(m.h0, present)
        self.steps += 1


def lambda_trace_mnats(observations: list[list[str] | dict[str, list[str]]], params: Mapping[str, Any]) -> list[int]:
    """Lambda in milli-nats after each observation, without cross-device terms (§6.9).

    A list is an utterance; `{"claim": [...]}` is a ROOM claim. `params` is the `hearthward:sprt` block."""
    model = HmmModel.from_params(params)
    forward = ForwardFilter(model)
    trace = []
    for item in observations:
        observation = parse_observation(item, model.features)
        if observation.kind == "claim":
            forward.observe_claim(observation.features)
        else:
            forward.observe(observation.features)
        trace.append(mnats(forward.value))
    return trace
