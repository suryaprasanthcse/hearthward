"""Integer interval arithmetic and watermarks (blueprint §4.4). No floats, no clocks: every value is a millisecond.

BT-A (asymmetry): escalation may rely on `possibly_*`; de-escalation (dismissing, closing a visit, pausing)
needs `certainly_*` and watermark passage. This module only evaluates the relations; callers choose the side.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

SAFE_INT = 2**53 - 1  # HW-JCS bound (C-2)
END_OF_STREAM = SAFE_INT  # W = infinity at the end of a replay; every real time is far below it


def _check_time(name: str, value: object) -> int:
    if type(value) is not int or not -SAFE_INT <= value <= SAFE_INT:
        raise ValueError(f"{name} must be an integer with |n| <= 2**53 - 1, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class Interval:
    """A closed interval [lo, hi] that certainly contains the true time (an instant has lo == hi)."""

    lo: int
    hi: int

    def __post_init__(self) -> None:
        _check_time("lo", self.lo)
        _check_time("hi", self.hi)
        if self.lo > self.hi:
            raise ValueError(f"empty interval: lo {self.lo} > hi {self.hi}")

    def hull(self, other: Interval) -> Interval:
        """The smallest interval containing both (conservative widening of a durative fact)."""
        return Interval(min(self.lo, other.lo), max(self.hi, other.hi))

    def widen(self, before_ms: int, after_ms: int) -> Interval:
        if before_ms < 0 or after_ms < 0:
            raise ValueError("margins must not be negative")
        return Interval(self.lo - before_ms, self.hi + after_ms)


def possibly_overlaps(x: Interval, y: Interval) -> bool:
    """X.lo <= Y.hi and Y.lo <= X.hi. Used for escalation."""
    return x.lo <= y.hi and y.lo <= x.hi


def certainly_before(x: Interval, y: Interval) -> bool:
    """X.hi < Y.lo. Used for de-escalation."""
    return x.hi < y.lo


def certainly_during(x: Interval, y: Interval) -> bool:
    """Y.lo <= X.lo and X.hi <= Y.hi. Used for de-escalation."""
    return y.lo <= x.lo and x.hi <= y.hi


def visit_close_time(max_vf_hi: int, visit_gap_ms: int) -> int:
    """The exclusive end `vt` of a durative fact once it closes: max(vf_hi) + visit_gap_ms."""
    _check_time("max_vf_hi", max_vf_hi)
    if visit_gap_ms < 0:
        raise ValueError("visit_gap_ms must not be negative")
    return max_vf_hi + visit_gap_ms


def basis_time(source: str, src_time: int, received_at: int) -> int:
    """The watermark basis: `src_time` for Ring, `received_at` for Alexa+ and Bee."""
    return src_time if source == "ring" else received_at


class Watermarks:
    """W_src = max(basis_src) - lateness_ms[src], per source, never moving backwards.

    A source that has produced nothing has no watermark (None), and None never passes any test. The end of stream,
    which exists only in replay, sets every watermark to infinity (`END_OF_STREAM`). Events that arrive after the
    watermark (Ring's dead-letter queue, 72 h) are belief revisions and never move a decision, so they do not
    move W backwards either.
    """

    def __init__(self, lateness_ms: Mapping[str, int]) -> None:
        for source, late in lateness_ms.items():
            if type(late) is not int or late < 0:
                raise ValueError(f"lateness for {source} must be a non-negative integer")
        self.lateness_ms = dict(lateness_ms)
        self._basis_max: dict[str, int] = {}
        self._ended = False

    def observe(self, source: str, basis_ms: int) -> None:
        if source not in self.lateness_ms:
            raise ValueError(f"unknown source: {source!r}")
        _check_time("basis_ms", basis_ms)
        self._basis_max[source] = max(basis_ms, self._basis_max.get(source, basis_ms))

    def end_of_stream(self) -> None:
        self._ended = True

    def value(self, source: str) -> int | None:
        if source not in self.lateness_ms:
            raise ValueError(f"unknown source: {source!r}")
        if self._ended:
            return END_OF_STREAM
        if source not in self._basis_max:
            return None
        return self._basis_max[source] - self.lateness_ms[source]

    def visit_may_close(self, visit_hi: int, visit_gap_ms: int) -> bool:
        """A visit closes only when W_ring > hi + visit_gap_ms."""
        w = self.value("ring")
        return w is not None and w > visit_hi + visit_gap_ms

    def dismissal_allowed(self, episode_hi: int, active_sources: Collection[str]) -> bool:
        """A dismissal needs W_src >= episode.hi for every active source (and at least one active source)."""
        if not active_sources:
            return False
        for source in active_sources:
            w = self.value(source)
            if w is None or w < episode_hi:
                return False
        return True
