"""`CausalStore` over GNOMON fact versions (blueprint §4.4, "GNOMON mapping").

GNOMON fact versions carry a point `vf` and the belief bounds `[ts, te)`. HEARTHWARD stores `vf = vf_lo` in the
kernel's field and `vf_hi` as an extra property, and computes the interval relations in the query layer (the port),
so GNOMON's kernel UDFs, audits K1 to K9 and mutation suite stay untouched.

Status: the GNOMON repository is not vendored yet (`vendor/gnomon` is a pending submodule, see
`docs/gates/BLOCKERS.md`), so this adapter talks to a narrow `GnomonBackend` protocol shaped like a version record
and ships `InMemoryGnomonBackend` behind it. Binding the protocol to the FalkorDB graph `t:hh:<household>` is the
step that needs the submodule; nothing above the protocol changes when it lands.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from hearthward.causal.intervals import Interval
from hearthward.causal.port import CausalStore, CausalStoreError, FactVersion
from hearthward.contracts.canonical import ProfileError, check_profile

# A GNOMON version record. Keys use the HW-JCS profile (lowercase, digits, underscores).
#   id, kind, n        the fact, its kind and its version number
#   vf                 the kernel's point valid-time field; HEARTHWARD stores vf_lo here
#   vf_hi              HEARTHWARD's extra property
#   ts, te             belief bounds [ts, te) in ledger sequences; te is None while current
#   tt                 wall time (ms) of ts
#   props              the fact's other properties
Record = dict[str, Any]


class GnomonBackend(Protocol):
    def put(self, record: Record) -> None:
        """Insert a new version record. Never overwrites."""

    def close(self, fact_id: str, n: int, te: int) -> None:
        """Set `te` on version `n`: the only change a stored version ever receives (BT-1)."""

    def scan(self, fact_id: str | None = None) -> list[Record]:
        """Version records in insertion order, for one fact or all."""


class InMemoryGnomonBackend:
    """A stand-in for the GNOMON tenant graph, holding records exactly as the protocol describes them."""

    def __init__(self) -> None:
        self._records: list[Record] = []

    def put(self, record: Record) -> None:
        if any(r["id"] == record["id"] and r["n"] == record["n"] for r in self._records):
            raise CausalStoreError(f"version {record['n']} of {record['id']!r} already exists")
        self._records.append(dict(record))

    def close(self, fact_id: str, n: int, te: int) -> None:
        for record in self._records:
            if record["id"] == fact_id and record["n"] == n:
                if record["te"] is not None:
                    raise CausalStoreError(f"version {n} of {fact_id!r} is already closed")
                record["te"] = te
                return
        raise KeyError((fact_id, n))

    def scan(self, fact_id: str | None = None) -> list[Record]:
        return [dict(r) for r in self._records if fact_id is None or r["id"] == fact_id]


def to_record(version: FactVersion) -> Record:
    return {"id": version.fact_id, "kind": version.kind, "n": version.n, "vf": version.vf_lo,
            "vf_hi": version.vf_hi, "ts": version.ts, "te": version.te, "tt": version.tt, "props": dict(version.data)}


def from_record(record: Mapping[str, Any]) -> FactVersion:
    return FactVersion(fact_id=record["id"], kind=record["kind"], n=record["n"], vf_lo=record["vf"],
                       vf_hi=record["vf_hi"], ts=record["ts"], te=record["te"], tt=record["tt"],
                       data=dict(record["props"]))


class GnomonCausalStore(CausalStore):
    def __init__(self, backend: GnomonBackend, lateness_ms: Mapping[str, int]) -> None:
        super().__init__(lateness_ms)
        self.backend = backend

    def versions(self, fact_id: str) -> tuple[FactVersion, ...]:
        return tuple(sorted((from_record(r) for r in self.backend.scan(fact_id)), key=lambda v: v.n))

    def fact_ids(self, kind: str | None = None) -> tuple[str, ...]:
        seen: dict[str, str] = {}
        for record in self.backend.scan():
            seen.setdefault(record["id"], record["kind"])
        return tuple(fid for fid, k in seen.items() if kind is None or k == kind)

    def append(self, fact_id: str, *, kind: str, valid: Interval, seq: int, tt: int,
               data: Mapping[str, Any] | None = None) -> FactVersion:
        props = dict(data or {})
        try:
            check_profile(props)
        except ProfileError as exc:
            raise CausalStoreError(f"properties are outside the HW-JCS profile: {exc}") from exc
        for name, value in (("seq", seq), ("tt", tt)):
            if type(value) is not int or value < 0:
                raise CausalStoreError(f"{name} must be a non-negative integer")
        history = self.versions(fact_id)
        if history and history[0].kind != kind:
            raise CausalStoreError(f"{fact_id!r} is a {history[0].kind!r} fact, not {kind!r}")
        if history:
            last = history[-1]
            if seq <= last.ts:
                raise CausalStoreError(f"seq {seq} does not follow version {last.n} (ts {last.ts})")
            if last.is_current:
                self.backend.close(fact_id, last.n, seq)
        version = FactVersion(fact_id=fact_id, kind=kind, n=len(history) + 1, vf_lo=valid.lo, vf_hi=valid.hi,
                              ts=seq, te=None, tt=tt, data=props)
        self.backend.put(to_record(version))
        return version
