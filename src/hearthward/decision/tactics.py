"""The seven binary tactic features (blueprint §6.1) and the only way observations reach the accumulator.

DC-4: extractor output is a closed set of enum values. Anything else, free text included, is rejected here, so no
string the household or a caller wrote can reach the decision core.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

FEATURES = ("authority", "urgency", "secrecy", "payment", "remote_access", "pressure", "info_request")
IRREVERSIBLE_PAYMENTS = frozenset({"cash", "gift_card", "wire", "crypto"})
AUTHORITY_PURPOSES = frozenset({"utility", "government", "bank", "repair"})


class ObservationError(ValueError):
    """The input is not a schema-valid observation."""


def validate_features(items: Iterable[Any], allowed: Iterable[str] = FEATURES) -> frozenset[str]:
    """A set of feature enum values; raises on a non-string, an unknown name or a bare string (not a list)."""
    if isinstance(items, str | bytes) or not isinstance(items, Iterable):
        raise ObservationError("features must be a list of enum values, not a string")
    names = frozenset(allowed)
    found = []
    for item in items:
        if not isinstance(item, str) or item not in names:
            raise ObservationError(f"not a tactic feature: {str(item)[:40]!r}")
        found.append(item)
    return frozenset(found)


def to_vector(features: Iterable[str], order: tuple[str, ...] = FEATURES) -> tuple[int, ...]:
    """The binary vector of §6.1 in `order`."""
    present = validate_features(features, order)
    return tuple(int(name in present) for name in order)


def from_vector(vector: Iterable[int], order: tuple[str, ...] = FEATURES) -> frozenset[str]:
    bits = list(vector)
    if len(bits) != len(order) or any(type(b) is not int or b not in (0, 1) for b in bits):
        raise ObservationError(f"a feature vector holds {len(order)} bits, each 0 or 1")
    return frozenset(name for name, bit in zip(order, bits, strict=True) if bit)


@dataclass(frozen=True)
class Observation:
    """`utterance`: a full binary vector (absent features count as absent). `claim`: a ROOM claim, a partial
    observation whose unlisted features are unobserved (§6.4)."""

    kind: Literal["utterance", "claim"]
    features: frozenset[str]


def parse_observation(item: Any, allowed: Iterable[str] = FEATURES) -> Observation:
    """Accepts the conformance interface's two shapes: a list of features, or `{"claim": [features]}`."""
    if isinstance(item, Mapping):
        if set(item) != {"claim"}:
            raise ObservationError("a claim is exactly {'claim': [...]}")
        return Observation("claim", validate_features(item["claim"], allowed))
    return Observation("utterance", validate_features(item, allowed))


def claim_from_assessment(arguments: Mapping[str, Any]) -> frozenset[str]:
    """The features an `assess_visitor` consultation claims (enum and boolean arguments only; names and
    organizations are never read as text)."""
    claimed: set[str] = set()
    if arguments.get("claimed_organization") or arguments.get("claimed_purpose") in AUTHORITY_PURPOSES:
        claimed.add("authority")
    if arguments.get("payment_requested") in IRREVERSIBLE_PAYMENTS:
        claimed.add("payment")
    if arguments.get("urgency_claimed") is True:
        claimed.add("urgency")
    if arguments.get("asked_secrecy") is True:
        claimed.add("secrecy")
    return frozenset(claimed)
