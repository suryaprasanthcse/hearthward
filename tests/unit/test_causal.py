"""Gate G4: interval relations, watermarks and the bitemporal store (blueprint §4.4, BT-A, BT-1 to BT-4)."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hearthward.causal import intervals
from hearthward.causal.gnomon_adapter import (
    GnomonCausalStore,
    InMemoryGnomonBackend,
    from_record,
    to_record,
)
from hearthward.causal.intervals import Interval, Watermarks
from hearthward.causal.port import CausalStore, CausalStoreError, FactVersion
from hearthward.contracts import specs

pytestmark = pytest.mark.gate_g4

times = st.integers(min_value=0, max_value=10**13)


@st.composite
def interval_strategy(draw: Any) -> Interval:
    lo = draw(times)
    return Interval(lo, lo + draw(st.integers(min_value=0, max_value=10**9)))


# ── intervals ────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lo, hi", [(5, 4), (1.0, 2), (1, 2.5), (True, 2), (1, True), (-(2**53), 0), (0, 2**53)])
def test_bad_intervals_are_rejected(lo: Any, hi: Any) -> None:
    with pytest.raises(ValueError):
        Interval(lo, hi)


def test_an_instant_is_a_degenerate_interval() -> None:
    assert Interval(5, 5).lo == Interval(5, 5).hi == 5


@pytest.mark.parametrize("x, y, overlap, before, during", [
    ((0, 10), (5, 15), True, False, False),
    ((0, 10), (10, 20), True, False, False),      # touching at one point still possibly overlaps
    ((0, 10), (11, 20), False, True, False),
    ((0, 9), (10, 20), False, True, False),
    ((5, 6), (0, 10), True, False, True),
    ((0, 10), (0, 10), True, False, True),
    ((0, 10), (1, 9), True, False, False),
    ((20, 30), (0, 10), False, False, False),
])
def test_relation_table(x: tuple[int, int], y: tuple[int, int], overlap: bool, before: bool, during: bool) -> None:
    a, b = Interval(*x), Interval(*y)
    assert intervals.possibly_overlaps(a, b) is overlap
    assert intervals.certainly_before(a, b) is before
    assert intervals.certainly_during(a, b) is during


@given(interval_strategy(), interval_strategy())
@settings(max_examples=300, derandomize=True)
def test_relation_laws(x: Interval, y: Interval) -> None:
    assert intervals.possibly_overlaps(x, y) == intervals.possibly_overlaps(y, x)
    if intervals.certainly_before(x, y):
        assert not intervals.possibly_overlaps(x, y)
    if intervals.certainly_during(x, y):
        assert intervals.possibly_overlaps(x, y)
    assert intervals.possibly_overlaps(x, y) or intervals.certainly_before(x, y) or intervals.certainly_before(y, x)
    assert intervals.certainly_during(x, x) and intervals.possibly_overlaps(x, x)


@given(interval_strategy(), interval_strategy())
@settings(max_examples=200, derandomize=True)
def test_hull_contains_both(x: Interval, y: Interval) -> None:
    h = x.hull(y)
    assert intervals.certainly_during(x, h) and intervals.certainly_during(y, h)


def test_widen_and_close_time() -> None:
    assert Interval(100, 200).widen(10, 20) == Interval(90, 220)
    with pytest.raises(ValueError):
        Interval(1, 2).widen(-1, 0)
    assert intervals.visit_close_time(1_000, 120_000) == 121_000
    with pytest.raises(ValueError):
        intervals.visit_close_time(1_000, -1)


# ── watermarks ───────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def lateness() -> dict[str, int]:
    return dict(specs.temporal()["lateness_ms"])


def test_the_ring_lateness_covers_its_whole_retry_schedule(lateness: dict[str, int]) -> None:
    assert lateness["ring"] == sum(specs.temporal()["ring_retry_schedule_ms"]) == 4_356_000


def test_watermark_is_max_basis_minus_lateness(lateness: dict[str, int]) -> None:
    w = Watermarks(lateness)
    assert w.value("ring") is None
    w.observe("ring", 10_000_000)
    w.observe("ring", 9_000_000)  # an older event never lowers it
    assert w.value("ring") == 10_000_000 - 4_356_000
    w.observe("bee", 700_000)
    assert w.value("bee") == 100_000


def test_a_late_dead_letter_event_never_moves_a_watermark_backwards(lateness: dict[str, int]) -> None:
    w = Watermarks(lateness)
    w.observe("ring", 50_000_000)
    before = w.value("ring")
    w.observe("ring", 1_000)
    assert w.value("ring") == before


def test_basis_is_src_time_for_ring_and_received_at_otherwise() -> None:
    assert intervals.basis_time("ring", 100, 900) == 100
    assert intervals.basis_time("bee", 100, 900) == intervals.basis_time("alexa", 100, 900) == 900


def test_a_visit_closes_only_strictly_after_hi_plus_the_gap(lateness: dict[str, int]) -> None:
    w, gap, hi = Watermarks(lateness), 120_000, 10_000_000
    assert not w.visit_may_close(hi, gap)                  # no Ring basis yet
    w.observe("ring", hi + gap + lateness["ring"])         # W == hi + gap: not yet
    assert not w.visit_may_close(hi, gap)
    w.observe("ring", hi + gap + lateness["ring"] + 1)
    assert w.visit_may_close(hi, gap)


def test_dismissal_needs_every_active_source_at_or_past_the_episode(lateness: dict[str, int]) -> None:
    w, hi = Watermarks(lateness), 5_000_000
    assert not w.dismissal_allowed(hi, [])                  # no active source proves nothing
    w.observe("alexa", hi + lateness["alexa"])
    assert w.dismissal_allowed(hi, ["alexa"])
    assert not w.dismissal_allowed(hi, ["alexa", "bee"])    # Bee has said nothing
    w.observe("bee", hi + lateness["bee"] - 1)
    assert not w.dismissal_allowed(hi, ["alexa", "bee"])
    w.observe("bee", hi + lateness["bee"])
    assert w.dismissal_allowed(hi, ["alexa", "bee"])


def test_end_of_stream_sets_every_watermark_to_infinity(lateness: dict[str, int]) -> None:
    w = Watermarks(lateness)
    w.end_of_stream()
    assert all(w.value(s) == intervals.END_OF_STREAM for s in lateness)
    assert w.dismissal_allowed(10**12, ["ring", "bee", "alexa"]) and w.visit_may_close(10**12, 120_000)


def test_unknown_sources_and_bad_values_are_errors(lateness: dict[str, int]) -> None:
    w = Watermarks(lateness)
    for call in (lambda: w.observe("phone", 1), lambda: w.value("phone"), lambda: w.observe("ring", 1.5),
                 lambda: Watermarks({"ring": -1}), lambda: Watermarks({"ring": 1.0})):
        with pytest.raises(ValueError):
            call()


# ── the store ────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def store(lateness: dict[str, int]) -> GnomonCausalStore:
    return GnomonCausalStore(InMemoryGnomonBackend(), lateness)


def test_the_port_is_abstract() -> None:
    with pytest.raises(TypeError):
        CausalStore({"ring": 1})  # type: ignore[abstract]


def test_append_creates_version_one_believed_from_seq(store: GnomonCausalStore) -> None:
    v = store.append("vis_a", kind="visit", valid=Interval(100, 200), seq=1, tt=1_000, data={"open": True})
    assert (v.n, v.vf_lo, v.vf_hi, v.ts, v.te, v.tt) == (1, 100, 200, 1, None, 1_000)
    assert v.is_current and store.current("vis_a") == v and store.fact_ids() == ("vis_a",)


def test_a_change_closes_the_old_version_and_appends_a_new_one(store: GnomonCausalStore) -> None:
    first = store.append("vis_a", kind="visit", valid=Interval(100, 200), seq=1, tt=1_000)
    second = store.append("vis_a", kind="visit", valid=Interval(90, 260), seq=5, tt=5_000)
    old, new = store.versions("vis_a")
    assert old.te == 5 and new.ts == 5 and new.te is None and second.n == 2
    assert first.te is None  # the object returned earlier is immutable: it never changed (BT-1)
    assert (old.vf_lo, old.vf_hi, old.data) == (100, 200, {})


def test_versions_are_frozen_dataclasses(store: GnomonCausalStore) -> None:
    v = store.append("vis_a", kind="visit", valid=Interval(1, 2), seq=1, tt=1)
    with pytest.raises(AttributeError):
        v.vf_lo = 0  # type: ignore[misc]


def test_sequences_must_advance(store: GnomonCausalStore) -> None:
    store.append("vis_a", kind="visit", valid=Interval(1, 2), seq=4, tt=1)
    for seq in (4, 3):
        with pytest.raises(CausalStoreError):
            store.append("vis_a", kind="visit", valid=Interval(1, 3), seq=seq, tt=2)
    assert len(store.versions("vis_a")) == 1


def test_bad_writes_change_nothing(store: GnomonCausalStore) -> None:
    store.append("vis_a", kind="visit", valid=Interval(1, 2), seq=1, tt=1)
    bad: list[dict[str, Any]] = [
        {"kind": "episode", "seq": 2, "tt": 2}, {"kind": "visit", "seq": 2, "tt": -1},
        {"kind": "visit", "seq": 2.5, "tt": 1}, {"kind": "visit", "seq": 2, "tt": 2, "data": {"ratio": 0.5}},
        {"kind": "visit", "seq": 2, "tt": 2, "data": {"Bad Key": 1}}]
    for kwargs in bad:
        with pytest.raises(CausalStoreError):
            store.append("vis_a", valid=Interval(1, 3), **kwargs)
    assert [v.n for v in store.versions("vis_a")] == [1] and store.current("vis_a").te is None


def test_replay_to_a_sequence_reproduces_the_beliefs_held_then(store: GnomonCausalStore) -> None:
    """BT-3."""
    snapshots: dict[int, list[FactVersion]] = {}
    plan = [("vis_a", 1, (100, 200)), ("vis_b", 2, (500, 600)), ("vis_a", 3, (90, 260)), ("vis_a", 7, (90, 400)),
            ("vis_b", 9, (500, 650))]
    for fid, seq, (lo, hi) in plan:
        store.append(fid, kind="visit", valid=Interval(lo, hi), seq=seq, tt=seq * 1000)
        snapshots[seq] = [store.version_at(f) for f in store.fact_ids()]
    for seq, held in snapshots.items():
        replayed = store.as_of(seq)
        assert [(v.fact_id, v.n, v.vf_lo, v.vf_hi) for v in replayed] == [
            (v.fact_id, v.n, v.vf_lo, v.vf_hi) for v in held if v is not None]
    assert store.as_of(0) == [] and [v.n for v in store.as_of(8)] == [3, 1]


def test_a_late_event_adds_a_version_and_leaves_earlier_beliefs_intact(store: GnomonCausalStore) -> None:
    """BT-4: the belief held at an earlier sequence does not change when a late event revises the valid time."""
    store.append("vis_a", kind="visit", valid=Interval(1_000, 2_000), seq=1, tt=1)
    held = store.version_at("vis_a", 1)
    store.append("vis_a", kind="visit", valid=Interval(400, 2_000), seq=6, tt=6)   # the start moved earlier
    assert store.version_at("vis_a", 1).valid == held.valid == Interval(1_000, 2_000)
    assert store.version_at("vis_a").valid == Interval(400, 2_000)


def test_relations_answer_as_of_a_belief_time(store: GnomonCausalStore) -> None:
    store.append("vis_a", kind="visit", valid=Interval(1_000, 2_000), seq=1, tt=1)
    store.append("vis_a", kind="visit", valid=Interval(1_000, 5_000), seq=4, tt=4)
    probe = Interval(3_000, 3_500)
    assert store.possibly_overlaps("vis_a", probe) is True
    assert store.possibly_overlaps("vis_a", probe, as_of=2) is False
    assert store.certainly_before("vis_a", probe, as_of=2) is True
    assert store.certainly_before("vis_a", probe) is False
    assert store.certainly_during("vis_a", Interval(0, 6_000)) is True
    assert store.certainly_during("vis_a", Interval(0, 4_000), as_of=2) is True


def test_a_fact_not_believed_at_that_time_is_an_error(store: GnomonCausalStore) -> None:
    store.append("vis_a", kind="visit", valid=Interval(1, 2), seq=5, tt=1)
    with pytest.raises(KeyError):
        store.possibly_overlaps("vis_a", Interval(1, 2), as_of=4)
    with pytest.raises(KeyError):
        store.possibly_overlaps("vis_zzz", Interval(1, 2))


def test_fact_ids_by_kind_and_hull(store: GnomonCausalStore) -> None:
    store.append("vis_a", kind="visit", valid=Interval(100, 200), seq=1, tt=1)
    store.append("ep_a", kind="episode", valid=Interval(150, 900), seq=2, tt=2)
    store.append("vis_b", kind="visit", valid=Interval(700, 800), seq=3, tt=3)
    assert store.fact_ids("visit") == ("vis_a", "vis_b") and store.fact_ids("episode") == ("ep_a",)
    assert store.hull(["vis_a", "vis_b"]) == Interval(100, 800) and store.hull([]) is None


def test_visit_closing_and_dismissal_use_the_current_version_and_the_watermarks(store: GnomonCausalStore) -> None:
    gap = specs.temporal()["visit_gap_ms"]
    store.append("vis_a", kind="visit", valid=Interval(10_000_000, 10_060_000), seq=1, tt=1)
    store.append("ep_a", kind="episode", valid=Interval(10_000_000, 10_100_000), seq=2, tt=2)
    assert not store.visit_may_close("vis_a", gap) and not store.dismissal_allowed("ep_a", ["alexa"])
    store.observe_time("ring", 10_060_000 + gap + 4_356_000 + 1)
    store.observe_time("alexa", 10_100_000 + 5_000)
    assert store.visit_may_close("vis_a", gap) and store.dismissal_allowed("ep_a", ["alexa"])
    assert store.watermark("ring") == 10_060_000 + gap + 1
    store.append("vis_a", kind="visit", valid=Interval(10_000_000, 99_000_000), seq=3, tt=3)  # revised: still open
    assert not store.visit_may_close("vis_a", gap)
    assert not store.visit_may_close("vis_unknown", gap)


def test_end_of_stream_lets_everything_close(store: GnomonCausalStore) -> None:
    store.append("ep_a", kind="episode", valid=Interval(1, 10**12), seq=1, tt=1)
    assert not store.dismissal_allowed("ep_a", ["bee"])
    store.end_of_stream()
    assert store.dismissal_allowed("ep_a", ["bee"])


# ── the GNOMON record shape ──────────────────────────────────────────────────────────────────────────────


def test_records_follow_the_gnomon_mapping(store: GnomonCausalStore) -> None:
    """vf = vf_lo (the kernel's point field), vf_hi an extra property, belief bounds [ts, te)."""
    store.append("vis_a", kind="visit", valid=Interval(100, 250), seq=3, tt=3_000, data={"cameras": ["front_door"]})
    store.append("vis_a", kind="visit", valid=Interval(90, 250), seq=8, tt=8_000)
    first, second = store.backend.scan("vis_a")
    assert first == {"id": "vis_a", "kind": "visit", "n": 1, "vf": 100, "vf_hi": 250, "ts": 3, "te": 8, "tt": 3_000,
                     "props": {"cameras": ["front_door"]}}
    assert second["vf"] == 90 and second["te"] is None


def test_records_round_trip(store: GnomonCausalStore) -> None:
    v = store.append("vis_a", kind="visit", valid=Interval(1, 9), seq=2, tt=7, data={"open": False, "n": 3})
    assert from_record(to_record(v)) == v


def test_the_backend_only_ever_edits_te() -> None:
    backend = InMemoryGnomonBackend()
    record = {"id": "f", "kind": "visit", "n": 1, "vf": 1, "vf_hi": 2, "ts": 1, "te": None, "tt": 1, "props": {}}
    backend.put(record)
    with pytest.raises(CausalStoreError):
        backend.put(record)
    backend.close("f", 1, 5)
    assert backend.scan("f")[0] == {**record, "te": 5}
    with pytest.raises(CausalStoreError):
        backend.close("f", 1, 6)
    with pytest.raises(KeyError):
        backend.close("f", 2, 6)


def test_scan_returns_copies() -> None:
    backend = InMemoryGnomonBackend()
    backend.put({"id": "f", "kind": "visit", "n": 1, "vf": 1, "vf_hi": 2, "ts": 1, "te": None, "tt": 1, "props": {}})
    backend.scan()[0]["vf"] = 999
    assert backend.scan()[0]["vf"] == 1
