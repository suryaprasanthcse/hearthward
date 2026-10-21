"""The `CausalStore` port: bitemporal fact versions, interval relations and watermarks (blueprint §4.4).

Storage is abstract. A concrete store supplies `append`, `versions` and `fact_ids`; the relations, belief-time
queries and watermark helpers are written once here, over `Interval`, so every backend (GNOMON, the in-memory
reference) answers them identically. A store is built around the temporal block's lateness table.

Versions are immutable (BT-1): a change closes the current version (`te = seq`) and appends a new one. Replaying to
sequence S reproduces the beliefs held at S exactly (BT-3), which is what `as_of(S)` returns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from hearthward.causal import intervals
from hearthward.causal.intervals import Interval, Watermarks


@dataclass(frozen=True)
class FactVersion:
    """One believed version of a fact.

    `vf_lo`/`vf_hi` bound the valid time; belief time is `[ts, te)` in ledger sequences, `te = None` while current;
    `tt` is the wall time (ms) of `ts`. `n` numbers the versions of one fact from 1."""

    fact_id: str
    kind: str
    n: int
    vf_lo: int
    vf_hi: int
    ts: int
    te: int | None
    tt: int
    data: Mapping[str, Any]

    @property
    def valid(self) -> Interval:
        return Interval(self.vf_lo, self.vf_hi)

    @property
    def is_current(self) -> bool:
        return self.te is None

    def believed_at(self, seq: int) -> bool:
        return self.ts <= seq and (self.te is None or seq < self.te)


class CausalStoreError(ValueError):
    """A write that would edit history, reorder sequences or leave the HW-JCS profile."""


class CausalStore(ABC):
    def __init__(self, lateness_ms: Mapping[str, int]) -> None:
        self.watermarks = Watermarks(lateness_ms)

    # -- storage, supplied by the backend ------------------------------------------------------------------

    @abstractmethod
    def append(self, fact_id: str, *, kind: str, valid: Interval, seq: int, tt: int,
               data: Mapping[str, Any] | None = None) -> FactVersion:
        """Close the fact's current version at `seq` (if any) and append a new one believed from `seq`.

        `seq` must exceed the current version's `ts`; nothing already stored is edited except `te` (BT-1)."""

    @abstractmethod
    def versions(self, fact_id: str) -> tuple[FactVersion, ...]:
        """Every version of the fact, oldest first."""

    @abstractmethod
    def fact_ids(self, kind: str | None = None) -> tuple[str, ...]:
        """Fact ids in first-appearance order, optionally of one kind."""

    # -- belief time ---------------------------------------------------------------------------------------

    def current(self, fact_id: str) -> FactVersion | None:
        history = self.versions(fact_id)
        return history[-1] if history and history[-1].is_current else None

    def version_at(self, fact_id: str, as_of: int | None = None) -> FactVersion | None:
        """The version believed at ledger sequence `as_of` (the current one when None)."""
        if as_of is None:
            return self.current(fact_id)
        return next((v for v in self.versions(fact_id) if v.believed_at(as_of)), None)

    def as_of(self, seq: int) -> list[FactVersion]:
        """The beliefs held at sequence `seq`, one version per fact (BT-3)."""
        held = (self.version_at(fid, seq) for fid in self.fact_ids())
        return [v for v in held if v is not None]

    # -- relations over uncertain valid intervals ------------------------------------------------------------

    def _valid(self, fact_id: str, as_of: int | None) -> Interval:
        version = self.version_at(fact_id, as_of)
        if version is None:
            raise KeyError(f"no version of {fact_id!r} is believed at {as_of if as_of is not None else 'now'}")
        return version.valid

    def possibly_overlaps(self, fact_id: str, other: Interval, *, as_of: int | None = None) -> bool:
        """For escalation (BT-A)."""
        return intervals.possibly_overlaps(self._valid(fact_id, as_of), other)

    def certainly_before(self, fact_id: str, other: Interval, *, as_of: int | None = None) -> bool:
        """For de-escalation (BT-A)."""
        return intervals.certainly_before(self._valid(fact_id, as_of), other)

    def certainly_during(self, fact_id: str, other: Interval, *, as_of: int | None = None) -> bool:
        """For de-escalation (BT-A)."""
        return intervals.certainly_during(self._valid(fact_id, as_of), other)

    # -- watermarks --------------------------------------------------------------------------------------------

    def observe_time(self, source: str, basis_ms: int) -> None:
        self.watermarks.observe(source, basis_ms)

    def watermark(self, source: str) -> int | None:
        return self.watermarks.value(source)

    def end_of_stream(self) -> None:
        self.watermarks.end_of_stream()

    def visit_may_close(self, fact_id: str, visit_gap_ms: int) -> bool:
        """A visit closes only when W_ring > hi + visit_gap_ms."""
        version = self.current(fact_id)
        return version is not None and self.watermarks.visit_may_close(version.vf_hi, visit_gap_ms)

    def dismissal_allowed(self, episode_id: str, active_sources: Collection[str]) -> bool:
        """A dismissal needs W_src >= episode.hi for every active source."""
        version = self.current(episode_id)
        return version is not None and self.watermarks.dismissal_allowed(version.vf_hi, active_sources)

    # -- helpers ---------------------------------------------------------------------------------------------

    def hull(self, fact_ids: Iterable[str], *, as_of: int | None = None) -> Interval | None:
        """The smallest interval covering the listed facts' valid times (a durative fact's conservative bound)."""
        result: Interval | None = None
        for fid in fact_ids:
            valid = self._valid(fid, as_of)
            result = valid if result is None else result.hull(valid)
        return result
