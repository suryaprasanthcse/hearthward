#!/usr/bin/env python3
"""HEARTHWARD orrery: a deterministic discrete-event model of the three sensor orbits.

DOOR (Ring webhooks), ROOM (Alexa+ tool calls) and WRIST (Bee stream lines) each run on their own clock and
delivery path. The orrery injects source clock offset and drift, delivery delay, Ring retries and duplicate
deliveries, Bee at-most-once drops recovered by sync, and skewed timestamps. It then plays the arrivals, in
arrival order, through the blueprint's contracts:

* ingest: HW-JCS envelopes on a per-household hash chain (blueprint §4.1 to §4.3);
* belief: a reference bitemporal store with valid intervals [vf_lo, vf_hi] and belief ranges [ts, te) (§4.4);
* decision: the scam-script HMM inside Wald's SPRT, plus the cross-device terms (§6);
* the broker's first answer to an S- call (§5.5).

Every parameter comes from the `hearthward:*` blocks of docs/master_blueprint.md. A run is a pure function of
(scenario, seed, injections): one seeded RNG and no wall clock. The reference store and accumulator here are what
the GNOMON adapter and `hearthward.decision.hmm` are checked against at gate G4.

    python tools/orrery.py --scenario doorstep_scam
    python tools/orrery.py --scenario late_and_lossy --format json
    python tools/orrery.py --scenario doorstep_scam --door-offset-ms 90000 --realtime 4
    python tools/orrery.py --selftest

Standard library only, so it runs before any dependency is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import hmac
import json
import math
import random
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT = ROOT / "docs" / "master_blueprint.md"

INF = 2**62  # "still open" for vt and te; rendered as null, so traces stay inside HW-JCS
EPOCH_MS = int(datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc).timestamp()) * 1000
HOUSEHOLD = "hh_demo0001"
SYNTHETIC_RING_KEY = b"hw-synthetic-ring-signing-key"  # synthetic fixtures only, never a production key
SYNTHETIC_BRIDGE_KEY = b"hw-synthetic-bridge-key"
ZERO_DIGEST = "sha256:" + "0" * 64
LANES = ("door", "room", "wrist")
SOURCE = {"door": "ring", "room": "alexa", "wrist": "bee", "system": "system"}
SCHEME = {"ring": "ring-hmac", "alexa": "oauth2.1", "bee": "bridge-hmac", "system": "internal"}
IRREVERSIBLE = frozenset({"cash", "gift_card", "wire", "crypto"})
AUTHORITY_PURPOSES = frozenset({"utility", "government", "bank", "repair"})


class SpecError(Exception):
    """The blueprint's machine-readable blocks are missing or malformed."""


# ── the blueprint as data ─────────────────────────────────────────────────────────────────────────

_BLOCK = re.compile(r"^```json hearthward:([a-z_]+)[ \t]*\r?\n(.*?)^```[ \t]*\r?$", re.M | re.S)


def load_spec(path: Path = BLUEPRINT) -> dict[str, Any]:
    """The blueprint's `hearthward:*` blocks, parsed, by name."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"cannot read {path}: {exc}") from exc
    blocks: dict[str, Any] = {}
    for match in _BLOCK.finditer(text):
        name = match.group(1)
        if name in blocks:
            raise SpecError(f"block hearthward:{name} appears more than once")
        try:
            blocks[name] = json.loads(match.group(2))
        except json.JSONDecodeError as exc:
            raise SpecError(f"block hearthward:{name} is not valid JSON: {exc}") from exc
    for name in ("sprt", "temporal"):
        if name not in blocks:
            raise SpecError(f"block hearthward:{name} is missing")
    return blocks


# ── HW-JCS, digests and the ledger chain (§4.2, §4.3) ─────────────────────────────────────────────

_KEY = re.compile(r"^[a-z0-9_]+$")
_SAFE_INT = 2**53 - 1


def canonical(value: Any) -> bytes:
    """HW-JCS bytes (C-2): RFC 8785 restricted to snake_case ASCII keys and safe integers.

    Inside that profile, sorted compact `json.dumps` produces exactly the JCS serialization."""
    _check_profile(value, "$")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _check_profile(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        if abs(value) > _SAFE_INT:
            raise ValueError(f"{path}: {value} is outside the safe integer range")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_profile(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not _KEY.match(key):
                raise ValueError(f"{path}: key {key!r} is not snake_case ASCII")
            _check_profile(item, f"{path}.{key}")
        return
    raise ValueError(f"{path}: {type(value).__name__} values are outside HW-JCS (floats are forbidden)")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def tagged_digest(tag: bytes, value: Any) -> str:
    """A domain-separated digest (C-7): SHA-256(tag || 0x00 || HW-JCS(value))."""
    return "sha256:" + hashlib.sha256(tag + b"\x00" + canonical(value)).hexdigest()


def chain_head(envelope: dict[str, Any]) -> str:
    return tagged_digest(b"hw-ledger-v1", {k: v for k, v in envelope.items() if k != "head"})


def verify_chain(envelopes: list[dict[str, Any]]) -> list[str]:
    """Problems with a ledger (E-1 to E-3 and the chain), empty when it is intact."""
    problems, prev = [], ZERO_DIGEST
    for n, env in enumerate(envelopes, 1):
        if env["seq"] != n:
            problems.append(f"seq {env['seq']} at position {n} (E-2)")
        if env["prev"] != prev:
            problems.append(f"seq {n}: prev does not match the previous head (E-3)")
        if chain_head(env) != env["head"]:
            problems.append(f"seq {n}: head does not match its contents")
        if env["valid"]["lo"] > env["valid"]["hi"]:
            problems.append(f"seq {n}: valid.lo > valid.hi (E-1)")
        prev = env["head"]
    return problems


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(ms: int, rng: random.Random) -> str:
    """A ULID from a virtual time and the run's RNG, so identifiers are reproducible."""
    n = (ms & (2**48 - 1)) << 80 | rng.getrandbits(80)
    return "".join(_CROCKFORD[(n >> shift) & 31] for shift in range(125, -1, -5))


def iso_ns(ms: int) -> str:
    """Ring's meta.time shape: ISO 8601 with nine fractional digits."""
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{(ms % 1000) * 1_000_000:09d}Z"


# ── decision core (§6) ────────────────────────────────────────────────────────────────────────────


def _log(p: float) -> float:
    return math.log(p) if p > 0 else -math.inf


def logsumexp(values: Iterable[float]) -> float:
    values = list(values)
    top = max(values)
    if top == -math.inf:
        return -math.inf
    return top + math.log(sum(math.exp(v - top) for v in values))


def mnats(x: float) -> int:
    """Evidence in integer milli-nats, the only form it takes in hashed documents (C-3)."""
    return int(round(x * 1000))


@dataclass(frozen=True)
class Model:
    """The decision-core parameters of §6.3, with extractor noise folded into every rate."""

    alpha: float
    beta: float
    features: tuple[str, ...]
    stages: tuple[str, ...]
    log_init: tuple[float, ...]
    log_trans: tuple[tuple[float, ...], ...]
    emit: tuple[tuple[float, ...], ...]
    h0: tuple[float, ...]
    cross: dict[str, float]
    cooldown_ms: int

    @property
    def A(self) -> float:  # noqa: N802 - Wald's notation
        return math.log((1 - self.beta) / self.alpha)

    @property
    def B(self) -> float:  # noqa: N802 - Wald's notation
        return math.log(self.beta / (1 - self.alpha))

    @classmethod
    def from_spec(cls, sprt: dict[str, Any]) -> Model:
        tpr, fpr = sprt["extractor"]["tpr"], sprt["extractor"]["fpr"]

        def noisy(rates: list[float]) -> tuple[float, ...]:
            return tuple(t * p + f * (1 - p) for p, t, f in zip(rates, tpr, fpr))

        return cls(
            alpha=sprt["alpha"],
            beta=sprt["beta"],
            features=tuple(sprt["features"]),
            stages=tuple(sprt["stages"]),
            log_init=tuple(_log(p) for p in sprt["initial"]),
            log_trans=tuple(tuple(_log(p) for p in row) for row in sprt["transitions"]),
            emit=tuple(noisy(row) for row in sprt["emissions"]),
            h0=noisy(sprt["h0"]),
            cross=dict(sprt["cross_device_llr"]),
            cooldown_ms=sprt["cooldown_ms"],
        )

    def log_emit(self, rates: tuple[float, ...], observed: frozenset[str]) -> float:
        return sum(math.log(p) if f in observed else math.log1p(-p) for f, p in zip(self.features, rates))


class Accumulator:
    """Lambda = log L(H1) - log L(H0) + the cross-device terms applied so far (§6.4)."""

    def __init__(self, model: Model) -> None:
        self.m = model
        self.log_alpha: list[float] | None = None
        self.log_l0 = 0.0
        self.steps = 0
        self.terms: dict[str, float] = {}

    @property
    def hmm(self) -> float:
        return 0.0 if self.log_alpha is None else logsumexp(self.log_alpha) - self.log_l0

    @property
    def value(self) -> float:
        return self.hmm + sum(self.terms.values())

    def observe(self, observed: frozenset[str]) -> float:
        """Fold one observation into the forward pass; returns the change in Lambda."""
        before = self.value
        e = [self.m.log_emit(rates, observed) for rates in self.m.emit]
        if self.log_alpha is None:
            self.log_alpha = [li + ei for li, ei in zip(self.m.log_init, e)]
        else:
            prev = self.log_alpha
            self.log_alpha = [
                logsumexp(a + row[s] for a, row in zip(prev, self.m.log_trans)) + e[s] for s in range(len(e))
            ]
        self.log_l0 += self.m.log_emit(self.m.h0, observed)
        self.steps += 1
        return self.value - before

    def observe_claim(self, present: frozenset[str]) -> float:
        """Fold in a ROOM claim as a partial observation (DC-2): the listed features are present and every other
        feature is unobserved, so it is marginalized rather than counted as absent. No stage transition happens,
        because a claim summarizes the turns already heard. Returns the change in Lambda."""
        before = self.value
        e = [sum(math.log(p) for f, p in zip(self.m.features, rates) if f in present) for rates in self.m.emit]
        if self.log_alpha is None:
            self.log_alpha = [li + ei for li, ei in zip(self.m.log_init, e)]
        else:
            self.log_alpha = [a + ei for a, ei in zip(self.log_alpha, e)]
        self.log_l0 += sum(math.log(p) for f, p in zip(self.m.features, self.m.h0) if f in present)
        self.steps += 1
        return self.value - before

    def apply(self, term: str) -> float:
        """Apply a cross-device term once per episode (DC-3); returns the change in Lambda."""
        if term in self.terms:
            return 0.0
        self.terms[term] = self.m.cross[term]
        return self.terms[term]


def reference_trace_mnats(observations: list[list[str] | dict[str, list[str]]], sprt: dict[str, Any]) -> list[int]:
    """Lambda after each observation without cross-device terms: the conformance target of §6.9.

    A list is an utterance (full observation); {"claim": [...]} is a ROOM claim (partial observation)."""
    acc = Accumulator(Model.from_spec(sprt))
    trace = []
    for observed in observations:
        if isinstance(observed, dict):
            acc.observe_claim(frozenset(observed["claim"]))
        else:
            acc.observe(frozenset(observed))
        trace.append(mnats(acc.hmm))
    return trace


CONFORMANCE_VECTORS: list[list[list[str] | dict[str, list[str]]]] = [
    [["authority"], ["pressure"], ["urgency", "payment"], ["secrecy", "pressure"], ["payment"]],
    [[], [], [], [], [], []],
    [[], ["urgency", "pressure", "payment"], ["secrecy", "pressure"], ["authority", "payment"]],
    [["authority"], [], [], [], [], []],
    [["remote_access", "info_request"], ["urgency"], ["remote_access", "pressure"]],
    [["authority"], ["pressure"], {"claim": ["secrecy", "payment"]}, ["payment"]],
]


# ── scenarios ─────────────────────────────────────────────────────────────────────────────────────


@dataclass
class Beat:
    """One scripted event at true time t, in milliseconds after the scenario starts."""

    t: int
    lane: str
    kind: str
    text: str = ""
    features: tuple[str, ...] = ()
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Injections:
    """What the orrery does to the clocks and the delivery paths."""

    door_offset_ms: int = 800
    door_drift_ppm: int = 40
    delay_ms: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {"door": (150, 900), "room": (400, 1500), "wrist": (2000, 9000), "system": (50, 200)}
    )
    ring_send_lag_ms: tuple[int, int] = (100, 600)
    ring_failed_attempts: dict[int, int] = field(default_factory=dict)
    ring_duplicates: frozenset[int] = frozenset()
    bee_drops: frozenset[int] = frozenset()
    extra_skew_ms: dict[int, int] = field(default_factory=dict)
    delay_scale_pct: int = 100
    bee_drop_p: float = 0.0
    ring_fail_p: float = 0.0


@dataclass
class Household:
    """The household's verified facts, as the belief store would hold them."""

    grandchildren: tuple[str, ...] = ("Alex", "Priya")
    trusted_vendors: tuple[str, ...] = ("Sunrise Plumbing",)
    circle: tuple[str, ...] = ("Sarah",)


@dataclass
class Scenario:
    name: str
    summary: str
    beats: list[Beat]
    injections: Injections = field(default_factory=Injections)
    household: Household = field(default_factory=Household)
    expect: dict[str, Any] = field(default_factory=dict)


def _doorstep_beats() -> list[Beat]:
    human = {"sub_type": "human"}
    return [
        Beat(0, "door", "door.motion", attrs={**human, "camera": "front_door"}),
        Beat(4_000, "door", "door.button", attrs={"camera": "front_door"}),
        Beat(9_000, "wrist", "wrist.conversation"),
        Beat(12_000, "wrist", "wrist.utterance", "Hi ma'am, I'm with Apex Roofing. We're doing your street today.",
             ("authority",)),
        Beat(20_000, "wrist", "wrist.utterance", "Your roof took storm damage. It could cave in on you.", ("pressure",)),
        Beat(28_000, "door", "door.motion", attrs={**human, "camera": "driveway"}),
        Beat(30_000, "wrist", "wrist.utterance", "We can fix it today, but I need a cash deposit right now.",
             ("urgency", "payment")),
        Beat(41_000, "wrist", "wrist.utterance", "Don't call your son. He'll only slow things down.",
             ("secrecy", "pressure")),
        Beat(47_000, "room", "room.consult", "assess_visitor",
             attrs={"claimed_organization": "Apex Roofing", "payment_requested": "cash", "urgency_claimed": True,
                    "asked_secrecy": True}),
        Beat(52_000, "wrist", "wrist.utterance", "Gift cards work too if you're short on cash.", ("payment",)),
        Beat(60_000, "room", "room.s_minus", "dismiss_alert", attrs={"tool": "dismiss_alert"}),
        Beat(75_000, "door", "door.motion", attrs={**human, "camera": "front_door"}),
    ]


def doorstep_scam() -> Scenario:
    return Scenario(
        "doorstep_scam",
        "A roofing impostor at the door; the visitor tries to have the alert dismissed by voice.",
        _doorstep_beats(),
        expect={"decision": "INTERVENE", "max_intervene_observation": 5, "events": ["PROOF_REQ"],
                "terms": ["door_unknown_overlap"], "full_coverage": True},
    )


def late_and_lossy() -> Scenario:
    return Scenario(
        "late_and_lossy",
        "The doorstep scam with Ring retrying the first motion four times, a duplicate Ring delivery and a "
        "Bee line lost by the at-most-once stream and recovered by sync after the decision.",
        _doorstep_beats(),
        Injections(ring_failed_attempts={0: 4}, ring_duplicates=frozenset({5}), bee_drops=frozenset({7})),
        expect={"decision": "INTERVENE", "min": {"duplicates": 1, "drops_recovered": 1,
                                                 "revisions_after_decision": 1}, "full_coverage": True},
    )


def skewed_door() -> Scenario:
    return Scenario(
        "skewed_door",
        "The Ring clock runs 5 s fast, beyond epsilon_ring_ms, and one delivery is stamped 90 s in the future.",
        _doorstep_beats(),
        Injections(door_offset_ms=5_000, extra_skew_ms={5: 85_000}),
        expect={"decision": "INTERVENE", "min": {"quarantined": 1}, "door_coverage_misses": True},
    )


def grandparent_call() -> Scenario:
    return Scenario(
        "grandparent_call",
        "A caller claims to be a grandson the household's facts do not know, then asks for secret bail money.",
        [
            Beat(0, "wrist", "wrist.conversation"),
            Beat(3_000, "wrist", "wrist.utterance", "Grandma? It's me, Tyler.",
                 attrs={"claimed_identity": "Tyler", "claimed_relationship": "grandchild"}),
            Beat(9_000, "wrist", "wrist.utterance", "I'm in trouble. I got arrested and need bail money tonight.",
                 ("urgency", "pressure", "payment")),
            Beat(16_000, "wrist", "wrist.utterance", "Please don't tell Mom, she'll be so angry.",
                 ("secrecy", "pressure")),
            Beat(24_000, "wrist", "wrist.utterance", "A lawyer will call about payment. Gift cards are fastest.",
                 ("authority", "payment")),
            Beat(40_000, "room", "room.consult", "assess_visitor",
                 attrs={"claimed_identity_name": "Tyler", "claimed_relationship": "grandchild",
                        "payment_requested": "gift_card", "urgency_claimed": True, "asked_secrecy": True}),
        ],
        expect={"decision": "INTERVENE", "max_intervene_observation": 3, "terms": ["identity_mismatch"],
                "full_coverage": True},
    )


def benign_delivery() -> Scenario:
    human = {"sub_type": "human"}
    return Scenario(
        "benign_delivery",
        "A courier at the door asks for a signature. Nothing to see.",
        [
            Beat(0, "door", "door.motion", attrs={**human, "camera": "front_door"}),
            Beat(3_000, "door", "door.button", attrs={"camera": "front_door"}),
            Beat(5_000, "wrist", "wrist.conversation"),
            Beat(6_000, "wrist", "wrist.utterance", "Hi, delivery for Mrs. Rao."),
            Beat(9_000, "wrist", "wrist.utterance", "Could you sign here for me?"),
            Beat(13_000, "wrist", "wrist.utterance", "Great, thank you. Have a good day!"),
            Beat(15_000, "wrist", "wrist.utterance", "You too, take care."),
            Beat(21_000, "door", "door.motion", attrs={**human, "camera": "front_door"}),
        ],
        expect={"decision": None, "final_state": ["closed_inconclusive", "dismissed"], "full_coverage": True},
    )


def verified_plumber() -> Scenario:
    human = {"sub_type": "human"}
    return Scenario(
        "verified_plumber",
        "A trusted plumber's visit, confirmed by calling the official number: verified evidence lowers Lambda "
        "below B and the episode is dismissed once the watermarks pass it.",
        [
            Beat(0, "door", "door.motion", attrs={**human, "camera": "front_door"}),
            Beat(3_000, "door", "door.button", attrs={"camera": "front_door"}),
            Beat(5_000, "wrist", "wrist.conversation"),
            Beat(7_000, "wrist", "wrist.utterance", "Hi, I'm from Sunrise Plumbing, here for the two o'clock "
                                                     "appointment.", ("authority",)),
            Beat(15_000, "wrist", "wrist.utterance", "The water heater valve needs replacing. It's a small part."),
            Beat(22_000, "room", "room.consult", "assess_visitor",
                 attrs={"claimed_organization": "Sunrise Plumbing", "payment_requested": "card"}),
            Beat(30_000, "system", "system.verified_callback", "official number confirmed the appointment",
                 attrs={"organization": "Sunrise Plumbing"}),
            Beat(35_000, "wrist", "wrist.utterance", "I'll have it done in twenty minutes."),
            Beat(48_000, "wrist", "wrist.utterance", "Mind if I turn the water off for a bit?"),
            Beat(70_000, "door", "door.motion", attrs={**human, "camera": "front_door"}),
        ],
        expect={"decision": None, "final_state": ["dismissed"], "terms": ["verified_official_callback"],
                "full_coverage": True},
    )


SCENARIOS: dict[str, Callable[[], Scenario]] = {
    s.__name__: s
    for s in (doorstep_scam, late_and_lossy, skewed_door, grandparent_call, benign_delivery, verified_plumber)
}


# ── the bitemporal reference store (§4.4) ─────────────────────────────────────────────────────────


@dataclass
class Version:
    """One version of a fact: valid from [vf_lo, vf_hi] until vt, believed for ledger sequences [ts, te)."""

    fid: str
    n: int
    vf_lo: int
    vf_hi: int
    vt: int
    ts: int
    te: int
    data: dict[str, Any]


class BeliefStore:
    """Versions are never edited: a change closes the current version (te) and appends the next (BT-1)."""

    def __init__(self) -> None:
        self.versions: dict[str, list[Version]] = {}

    def assert_new(self, fid: str, vf_lo: int, vf_hi: int, seq: int, data: dict[str, Any]) -> Version:
        version = Version(fid, 1, vf_lo, vf_hi, INF, seq, INF, data)
        self.versions[fid] = [version]
        return version

    def supersede(self, fid: str, seq: int, *, vf_lo: int | None = None, vf_hi: int | None = None,
                  vt: int | None = None, data: dict[str, Any] | None = None) -> Version:
        cur = self.current(fid)
        cur.te = seq
        version = Version(fid, cur.n + 1, cur.vf_lo if vf_lo is None else vf_lo, cur.vf_hi if vf_hi is None else vf_hi,
                          cur.vt if vt is None else vt, seq, INF, dict(cur.data) if data is None else data)
        self.versions[fid].append(version)
        return version

    def current(self, fid: str) -> Version:
        return self.versions[fid][-1]

    def as_of(self, fid: str, seq: int) -> Version | None:
        """The version believed after ledger sequence `seq` (BT-3)."""
        return next((v for v in self.versions[fid] if v.ts <= seq < v.te), None)


def possibly_overlaps(x: tuple[int, int], y: tuple[int, int]) -> bool:
    return x[0] <= y[1] and y[0] <= x[1]


# ── the simulation ────────────────────────────────────────────────────────────────────────────────


@dataclass(order=True)
class Arrival:
    at: int
    order: int
    index: int = field(compare=False)
    via: str = field(compare=False)  # push | retry | duplicate | sync


@dataclass
class Row:
    """One line of the Threat Braid."""

    at: int
    lane: str
    event: str
    label: str
    seq: int | None
    lambda_mnats: int
    delta_mnats: int
    features: list[str]
    note: str
    head: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"at": self.at, "lane": self.lane, "event": self.event, "label": self.label, "seq": self.seq,
                "lambda_mnats": self.lambda_mnats, "delta_mnats": self.delta_mnats, "features": self.features,
                "note": self.note, "head": self.head}


@dataclass
class Result:
    scenario: str
    seed: int
    rows: list[Row]
    envelopes: list[dict[str, Any]]
    summary: dict[str, Any]


def _short(fid: str) -> str:
    return fid[:4] + fid[-4:]


def fmt_clock(ms: int) -> str:
    sign = "-" if ms < 0 else "+"
    ms = abs(ms)
    return f"{sign}{ms // 60_000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def fmt_rel(abs_ms: int) -> str:
    return fmt_clock(abs_ms - EPOCH_MS)


class Orrery:
    def __init__(self, spec: dict[str, Any], scenario: Scenario, seed: int = 7,
                 injections: Injections | None = None) -> None:
        self.temporal = spec["temporal"]
        self.model = Model.from_spec(spec["sprt"])
        self.params_digest = "sha256:" + hashlib.sha256(
            json.dumps(spec["sprt"], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.scenario = scenario
        self.seed = seed
        self.inj = injections or scenario.injections
        self.rng = random.Random(f"orrery:{scenario.name}:{seed}")
        self.ledger: list[dict[str, Any]] = []
        self.head = ZERO_DIGEST
        self.store = BeliefStore()
        self.acc = Accumulator(self.model)
        self.seen: set[str] = set()
        self.basis: dict[str, int] = {}
        self.flushed = False
        self.visits: list[str] = []
        self.episode: dict[str, Any] | None = None
        self.decision: dict[str, Any] | None = None
        self.decision_digest: str | None = None
        self.intervene_observation: int | None = None
        self.observed: set[str] = set()
        self.observations = 0
        self.hold_noted = False
        self.counters = {"duplicates": 0, "quarantined": 0, "drops_recovered": 0, "revisions": 0,
                         "revisions_after_decision": 0}
        self.coverage = {lane: [0, 0] for lane in LANES}
        self.meta: list[dict[str, Any]] = []
        self.heap: list[Arrival] = []
        self.pushed = 0
        self.last_at = 0
        self._prepare()

    # ── delivery paths ──

    def _delay(self, lane: str) -> int:
        lo, hi = self.inj.delay_ms[lane]
        return self.rng.randint(lo, hi) * self.inj.delay_scale_pct // 100

    def _push(self, at: int, index: int, via: str) -> None:
        self.pushed += 1  # ties in arrival time resolve in scheduling order
        heapq.heappush(self.heap, Arrival(at, self.pushed, index, via))

    def _prepare(self) -> None:
        """Turn each beat into its source payload, raw bytes and arrival times, drawing from the RNG in beat order."""
        schedule = self.temporal["ring_retry_schedule_ms"]
        sync = self.temporal["bee_sync_interval_ms"]
        conversation: dict[str, Any] | None = None
        for i, beat in enumerate(self.scenario.beats):
            m: dict[str, Any] = {"index": i, "src_time": None, "sent_time": None, "raw": None, "signature": None}
            if beat.lane == "door":
                skew = (self.inj.door_offset_ms + self.inj.door_drift_ppm * beat.t // 1_000_000
                        + self.inj.extra_skew_ms.get(i, 0))
                fails = self.inj.ring_failed_attempts.get(i, 0)
                if not fails and self.inj.ring_fail_p and self.rng.random() < self.inj.ring_fail_p:
                    fails = 1
                send = beat.t + self.rng.randint(*self.inj.ring_send_lag_ms) + sum(schedule[:fails])
                m["src_time"] = EPOCH_MS + beat.t + skew
                m["sent_time"] = EPOCH_MS + send + skew
                m["attempt"] = fails + 1
                m["source_event_id"] = str(uuid.UUID(int=self.rng.getrandbits(128), version=4))
                m["payload"] = self._ring_payload(beat, m)
                m["raw"] = json.dumps(m["payload"]).encode("utf-8")
                m["signature"] = "sha256=" + hmac.new(SYNTHETIC_RING_KEY, m["raw"], hashlib.sha256).hexdigest()
                self._push(send + self._delay("door"), i, "retry" if fails else "push")
                if i in self.inj.ring_duplicates:
                    # our 200 was lost, so Ring retries on its schedule and the same request arrives twice
                    self._push(send + schedule[fails] + self._delay("door"), i, "duplicate")
            elif beat.lane == "wrist":
                if beat.kind == "wrist.conversation":
                    conversation = {"id": self.rng.randint(1000, 99999),
                                    "uuid": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
                                    "start": beat.t, "end": beat.t}
                    m["payload"] = {"conversation": {"id": conversation["id"], "uuid": conversation["uuid"],
                                                     "state": "processing", "title": None}}
                else:
                    if conversation is None:
                        raise SpecError(f"{self.scenario.name}: utterance {i} has no conversation")
                    conversation["end"] = beat.t
                    m["payload"] = {"utterance": {"text": beat.text, "speaker": "speaker_1"},
                                    "conversation_uuid": conversation["uuid"]}
                m["conversation"] = conversation
                m["raw"] = json.dumps(m["payload"]).encode("utf-8")
                m["signature"] = hmac.new(SYNTHETIC_BRIDGE_KEY, m["raw"], hashlib.sha256).hexdigest()
                line_digest = hashlib.sha256(m["raw"]).hexdigest()[:16]
                m["source_event_id"] = f"bee:{conversation['uuid']}:{line_digest}"
                arrive = beat.t + self._delay("wrist")
                dropped = i in self.inj.bee_drops or (
                    bool(self.inj.bee_drop_p) and self.rng.random() < self.inj.bee_drop_p)
                if dropped:
                    # at-most-once: the line never streams; the next `bee sync` brings it back
                    self._push((arrive // sync + 1) * sync + self._delay("system"), i, "sync")
                else:
                    self._push(arrive, i, "push")
            elif beat.lane == "room":
                arrive = beat.t + self._delay("room")
                m["source_event_id"] = "alexa:" + ulid(EPOCH_MS + arrive, self.rng)
                m["payload"] = {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                "params": {"name": beat.text, "arguments": dict(beat.attrs)}}
                m["raw"] = json.dumps(m["payload"]).encode("utf-8")
                self._push(arrive, i, "push")
            else:
                arrive = beat.t + self._delay("system")
                m["source_event_id"] = "sys:" + ulid(EPOCH_MS + arrive, self.rng)
                m["payload"] = {"kind": beat.kind, "text": beat.text, "attrs": dict(beat.attrs)}
                self._push(arrive, i, "push")
            self.meta.append(m)

    def _ring_payload(self, beat: Beat, m: dict[str, Any]) -> dict[str, Any]:
        device = "dev_" + beat.attrs.get("camera", "front_door")
        kind = "button_press" if beat.kind == "door.button" else "motion_detected"
        attributes: dict[str, Any] = {"source": device, "source_type": "devices", "timestamp": m["src_time"]}
        if kind == "motion_detected":
            attributes["sub_type"] = beat.attrs.get("sub_type", "human")
        return {
            "meta": {"version": "1.1", "time": iso_ns(m["sent_time"]), "request_id": m["source_event_id"],
                     "account_id": "ava1.ring.account.SYNTHETIC"},
            "data": {"id": f"{device}_{kind}_{m['src_time']}", "type": kind, "attributes": attributes,
                     "relationships": {"devices": {"links": {"self": f"/v1/devices/{device}"}}}},
        }

    # ── ingest ──

    def _authenticate(self, lane: str, m: dict[str, Any]) -> bool:
        """Verify before parsing, over the raw bytes exactly as received (C-5)."""
        if lane == "door":
            expected = "sha256=" + hmac.new(SYNTHETIC_RING_KEY, m["raw"], hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, m["signature"])
        if lane == "wrist":
            expected = hmac.new(SYNTHETIC_BRIDGE_KEY, m["raw"], hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, m["signature"])
        return True  # ROOM: the bearer token is validated by the MCP server; SYSTEM: internal

    def _valid(self, lane: str, m: dict[str, Any], now: int, via: str) -> tuple[int, int]:
        t = self.temporal
        if lane == "door":
            return m["src_time"] - t["epsilon_ring_ms"], m["src_time"] + t["epsilon_ring_ms"]
        if lane == "wrist" and via == "sync":
            conversation = m["conversation"]  # sync only knows the conversation's bounds
            return EPOCH_MS + conversation["start"] - 1000, EPOCH_MS + conversation["end"] + 1000
        if lane == "wrist":
            return now - t["delta_ms"]["bee"], now
        if lane == "room":
            return now - t["delta_ms"]["alexa"], now
        return now, now

    def _append(self, lane: str, beat: Beat, m: dict[str, Any], now: int, lo: int, hi: int,
                verified: bool) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "v": 1,
            "household": HOUSEHOLD,
            "source": SOURCE[lane],
            "source_event_id": m["source_event_id"],
            "kind": beat.kind,
            "valid": {"lo": lo, "hi": hi},
            "src_time": m["src_time"],
            "sent_time": m["sent_time"],
            "received_at": now,
            "seq": len(self.ledger) + 1,
            "raw_digest": None if m["raw"] is None else "sha256:" + hashlib.sha256(m["raw"]).hexdigest(),
            "payload_digest": digest(m["payload"]),
            "auth": {"scheme": SCHEME[SOURCE[lane]], "verified": verified, "synthetic": True},
            "prev": self.head,
        }
        envelope["head"] = chain_head(envelope)
        self.head = envelope["head"]
        self.ledger.append(envelope)
        return envelope

    def watermark(self, lane: str) -> int:
        if self.flushed or lane not in self.basis:
            return INF
        return self.basis[lane] - self.temporal["lateness_ms"][SOURCE[lane]]

    # ── the main loop ──

    def run(self) -> Iterator[Row]:
        while self.heap:
            yield from self._process(heapq.heappop(self.heap))
        yield from self._flush()

    def _row(self, at: int, lane: str, event: str, label: str, seq: int | None, delta: float,
             features: Iterable[str], note: str, head: str | None = None) -> Row:
        return Row(at, lane, event, label, seq, mnats(self.acc.value), mnats(delta), sorted(features), note, head)

    def _process(self, arrival: Arrival) -> Iterator[Row]:
        beat = self.scenario.beats[arrival.index]
        m = self.meta[arrival.index]
        lane, now = beat.lane, EPOCH_MS + arrival.at
        self.last_at = arrival.at
        label = self._label(beat)
        verified = self._authenticate(lane, m)
        if not verified:
            yield self._row(arrival.at, lane, "REJECTED", label, None, 0, (), "signature mismatch; 401, never parsed")
            return
        if m["source_event_id"] in self.seen:
            self.counters["duplicates"] += 1
            yield self._row(arrival.at, lane, "DUPLICATE", label, None, 0, (),
                            f"request {m['source_event_id'][:8]} delivered again by a Ring retry; absorbed (E-6)")
            return
        self.seen.add(m["source_event_id"])
        if m["src_time"] is not None and m["src_time"] > now + self.temporal["max_skew_ms"]:
            self.counters["quarantined"] += 1
            yield self._row(arrival.at, lane, "QUARANTINE", label, None, 0, (),
                            f"src_time is {(m['src_time'] - now) // 1000} s ahead of receipt, beyond max_skew_ms; "
                            "kept out of the ledger and belief (BT-5)")
            return
        lo, hi = self._valid(lane, m, now, arrival.via)
        envelope = self._append(lane, beat, m, now, lo, hi, verified)
        if lane in self.coverage:
            self.coverage[lane][1] += 1
            self.coverage[lane][0] += lo <= EPOCH_MS + beat.t <= hi
        if lane in LANES:
            basis = m["src_time"] if lane == "door" else now
            self.basis[lane] = max(self.basis.get(lane, basis), basis)
        if arrival.via == "sync":
            self.counters["drops_recovered"] += 1
        event, delta, observed, note = self._apply(beat, m, envelope, arrival)
        cross_delta, cross_note = self._cross_checks()
        if cross_note:
            note = f"{note}; {cross_note}" if note else cross_note
        yield self._row(arrival.at, lane, event, label, envelope["seq"], delta + cross_delta, observed, note,
                        envelope["head"])
        yield from self._decide(arrival.at)

    def _label(self, beat: Beat) -> str:
        if beat.lane == "door":
            what = "button" if beat.kind == "door.button" else "motion"
            return f"{what} {beat.attrs.get('camera', '')}".strip()
        if beat.kind == "wrist.conversation":
            return "conversation starts"
        if beat.lane == "wrist":
            return '"' + beat.text + '"'
        if beat.kind == "room.s_minus":
            return f"{beat.text} (S-)"
        if beat.lane == "room":
            return beat.text
        return beat.kind

    def _apply(self, beat: Beat, m: dict[str, Any], envelope: dict[str, Any],
               arrival: Arrival) -> tuple[str, float, frozenset[str], str]:
        event = {"retry": f"INGEST@{m.get('attempt', 1)}", "sync": "INGEST+SYNC"}.get(arrival.via, "INGEST")
        lo, hi, seq = envelope["valid"]["lo"], envelope["valid"]["hi"], envelope["seq"]
        if beat.lane == "door":
            note = self._apply_door(beat, lo, hi, seq)
            if arrival.via == "retry":
                note += f" (Ring attempt {m['attempt']})"
            return event, 0.0, frozenset(), note
        if beat.kind == "wrist.conversation":
            return event, 0.0, frozenset(), "Bee conversation opened"
        if beat.kind == "room.s_minus":
            return "PROOF_REQ", 0.0, frozenset(), self._broker(beat)
        notes = [self._touch_episode(lo, hi, envelope["received_at"])]
        delta = 0.0
        observed: frozenset[str] = frozenset()
        if beat.kind == "wrist.utterance":
            observed = frozenset(beat.features)
            delta += self._observe(observed, notes, late=self.decision is not None)
            delta += self._identity(beat.attrs.get("claimed_identity"), beat.attrs.get("claimed_relationship"), notes)
        elif beat.kind == "room.consult":
            delta, observed = self._consult(beat.attrs, notes)
        elif beat.kind == "system.verified_callback":
            delta += self.acc.apply("verified_official_callback")
            notes.append("verified via the official number (TL-1): verified_official_callback "
                         f"{self.model.cross['verified_official_callback']:+.2f}")
        return event, delta, observed, "; ".join(n for n in notes if n)

    def _apply_door(self, beat: Beat, lo: int, hi: int, seq: int) -> str:
        gap = self.temporal["visit_gap_ms"]
        camera = beat.attrs.get("camera", "front_door")
        for fid in self.visits:
            cur = self.store.current(fid)
            if cur.data["open"] and possibly_overlaps((lo, hi), (cur.vf_lo - gap, cur.vf_hi + gap)):
                data = dict(cur.data, cameras=sorted(set(cur.data["cameras"]) | {camera}))
                new = self.store.supersede(fid, seq, vf_lo=min(cur.vf_lo, lo), vf_hi=max(cur.vf_hi, hi), data=data)
                if new.vf_lo < cur.vf_lo:
                    self.counters["revisions"] += 1
                    after = self.decision is not None
                    self.counters["revisions_after_decision"] += after
                    return (f"{_short(fid)} v{new.n}: start revised {fmt_rel(cur.vf_lo)} -> {fmt_rel(new.vf_lo)}"
                            + (" after the decision; the record is unchanged (BT-4)" if after else " (late event)"))
                return f"{_short(fid)} v{new.n}: extended to {fmt_rel(new.vf_hi)}"
        trigger = "button_press" if beat.kind == "door.button" else (
            "motion_human" if beat.attrs.get("sub_type", "human") == "human" else "motion_other")
        fid = "vis_" + ulid(lo, self.rng)
        self.store.assert_new(fid, lo, hi, seq, {"trigger": trigger, "visitor": "unknown", "cameras": [camera],
                                                 "open": True})
        self.visits.append(fid)
        return f"{_short(fid)} opened [{fmt_rel(lo)}, {fmt_rel(hi)}], unknown visitor"

    def _touch_episode(self, lo: int, hi: int, now: int) -> str:
        if self.episode is None:
            self.episode = {"id": "ep_" + ulid(now, self.rng), "lo": lo, "hi": hi, "state": "open"}
            return f"episode {_short(self.episode['id'])} opened"
        self.episode["lo"] = min(self.episode["lo"], lo)
        self.episode["hi"] = max(self.episode["hi"], hi)
        return ""

    def _observe(self, observed: frozenset[str], notes: list[str], *, late: bool, claim: bool = False) -> float:
        delta = self.acc.observe_claim(observed) if claim else self.acc.observe(observed)
        self.observations += 1
        self.observed |= observed
        notes.append(f"obs {self.observations} [{', '.join(sorted(observed)) or 'none'}]"
                     + (" as a partial observation" if claim else "")
                     + ("; after the decision, the record is unchanged (BT-4)" if late else ""))
        return delta

    def _identity(self, name: str | None, relationship: str | None, notes: list[str]) -> float:
        known = self.scenario.household.grandchildren
        if relationship == "grandchild" and name and name not in known:
            delta = self.acc.apply("identity_mismatch")
            if delta:
                notes.append(f"'{name}' is not a known grandchild ({', '.join(known)}): identity_mismatch "
                             f"{delta:+.2f}")
            return delta
        return 0.0

    def _consult(self, attrs: dict[str, Any], notes: list[str]) -> tuple[float, frozenset[str]]:
        claimed = set()
        if attrs.get("claimed_organization") or attrs.get("claimed_purpose") in AUTHORITY_PURPOSES:
            claimed.add("authority")
        if attrs.get("payment_requested") in IRREVERSIBLE:
            claimed.add("payment")
        if attrs.get("urgency_claimed"):
            claimed.add("urgency")
        if attrs.get("asked_secrecy"):
            claimed.add("secrecy")
        novel = frozenset(claimed - self.observed)  # DC-2: the elder's summary never double counts WRIST
        notes.append(f"claims [{', '.join(sorted(claimed))}], novel [{', '.join(sorted(novel))}] (DC-2)")
        delta = self._observe(novel, notes, late=self.decision is not None, claim=True) if novel else 0.0
        org = attrs.get("claimed_organization")
        if org and org not in self.scenario.household.trusted_vendors and attrs.get("payment_requested") in IRREVERSIBLE:
            term = self.acc.apply("untrusted_org_irreversible_payment")
            if term:
                notes.append(f"'{org}' is not a trusted vendor and wants an irreversible payment: "
                             f"untrusted_org_irreversible_payment {term:+.2f}")
            delta += term
        delta += self._identity(attrs.get("claimed_identity_name"), attrs.get("claimed_relationship"), notes)
        return delta, novel

    def _broker(self, beat: Beat) -> str:
        state = self.episode["state"] if self.episode else None
        rules = ["HX-01", "HX-02", "HX-09"] if state in ("open", "intervening") else ["HX-02"]
        return (f"{beat.attrs.get('tool', beat.text)} returns PROOF_REQUIRED [{', '.join(rules)}]; the circle is "
                "asked to approve on a passkey; nothing changed (TB-1: the token names the household, not the speaker)")

    def _cross_checks(self) -> tuple[float, str]:
        if self.episode is None or "door_unknown_overlap" in self.acc.terms:
            return 0.0, ""
        gap = self.temporal["visit_gap_ms"]
        for fid in self.visits:
            visit = self.store.current(fid)
            if visit.data["visitor"] == "unknown" and possibly_overlaps(
                    (self.episode["lo"], self.episode["hi"]), (visit.vf_lo, visit.vf_hi + gap)):
                delta = self.acc.apply("door_unknown_overlap")
                return delta, f"conversation possibly overlaps {_short(fid)}: door_unknown_overlap {delta:+.2f}"
        return 0.0, ""

    # ── decisions ──

    def _decide(self, at: int) -> Iterator[Row]:
        episode = self.episode
        if episode is None or episode["state"] != "open":
            return
        lam = self.acc.value
        if lam >= self.model.A:
            episode["state"] = "intervening"
            self.intervene_observation = self.observations
            self.decision = {
                "episode": episode["id"],
                "decision": "INTERVENE",
                "at_seq": len(self.ledger),
                "belief_seq": len(self.ledger),
                "ledger_head": self.head,
                "lambda_mnats": mnats(lam),
                "thresholds_mnats": {"a": mnats(self.model.A), "b": mnats(self.model.B)},
                "observed_features": sorted(self.observed),
                "cross_terms": sorted(self.acc.terms),
                "params_digest": self.params_digest,
            }
            self.decision_digest = tagged_digest(b"hw-decision-v1", self.decision)
            yield self._row(at, "system", "INTERVENE", "", None, 0, (),
                            f"Lambda >= A at observation {self.observations}; InterventionLadder started (L1 and L2); "
                            f"decision record {self.decision_digest[:19]}")
        elif lam <= self.model.B:
            if all(self.watermark(lane) >= episode["hi"] for lane in self.basis):
                episode["state"] = "dismissed"
                yield self._row(at, "system", "DISMISS", "", None, 0, (), "Lambda <= B and every watermark passed the "
                                                                          "episode")
            elif not self.hold_noted:
                self.hold_noted = True
                yield self._row(at, "system", "HOLD", "", None, 0, (),
                                "Lambda <= B, but dismissal waits until every watermark passes the episode (BT-A)")

    def _flush(self) -> Iterator[Row]:
        """End of stream (replay only): every watermark becomes infinite and pending closures resolve."""
        self.flushed = True
        last_at = self.last_at
        end_seq = len(self.ledger) + 1  # closures are stamped one past the final ledger sequence
        gap = self.temporal["visit_gap_ms"]
        for fid in self.visits:
            cur = self.store.current(fid)
            if cur.data["open"]:
                closed = self.store.supersede(fid, end_seq, vt=cur.vf_hi + gap, data=dict(cur.data, open=False))
                yield self._row(last_at, "system", "VISIT_CLOSED", "", None, 0, (),
                                f"{_short(fid)} v{closed.n}: vt = {fmt_rel(closed.vt)} once the Ring watermark passed")
        yield from self._decide(last_at)
        if self.episode and self.episode["state"] == "open":
            self.episode["state"] = "closed_inconclusive"
            yield self._row(last_at, "system", "INCONCLUSIVE", "", None, 0, (),
                            "the stream ended with B < Lambda < A; no action, nothing dismissed")
        if self.decision:
            for fid in self.visits:
                then, now = self.store.as_of(fid, self.decision["at_seq"]), self.store.current(fid)
                if then is not None and (then.vf_lo, then.vf_hi) != (now.vf_lo, now.vf_hi):
                    yield self._row(last_at, "system", "AS_OF", "", None, 0, (),
                                    f"{_short(fid)} believed at seq {self.decision['at_seq']}: "
                                    f"[{fmt_rel(then.vf_lo)}, {fmt_rel(then.vf_hi)}] v{then.n}; now "
                                    f"[{fmt_rel(now.vf_lo)}, {fmt_rel(now.vf_hi)}] v{now.n}")
        stable = self.decision is None or tagged_digest(b"hw-decision-v1", self.decision) == self.decision_digest
        state = self.episode["state"] if self.episode else "none"
        yield self._row(last_at, "system", "END", "", None, 0, (),
                        f"episode {state}; {len(self.ledger)} envelopes; chain head {self.head[:19]}; "
                        f"decision record {'stable' if stable else 'CHANGED'}")

    def summary(self, rows: list[Row]) -> dict[str, Any]:
        stable = self.decision is None or tagged_digest(b"hw-decision-v1", self.decision) == self.decision_digest
        return {
            "scenario": self.scenario.name,
            "seed": self.seed,
            "rows": len(rows),
            "envelopes": len(self.ledger),
            "ledger_head": self.head,
            "decision": self.decision["decision"] if self.decision else None,
            "decision_digest": self.decision_digest,
            "decision_record_stable": stable,
            "intervene_at_observation": self.intervene_observation,
            "observations": self.observations,
            "final_state": self.episode["state"] if self.episode else "none",
            "lambda_mnats": mnats(self.acc.value),
            "cross_terms": sorted(self.acc.terms),
            **self.counters,
            "coverage": {lane: {"inside": inside, "total": total} for lane, (inside, total) in self.coverage.items()},
            "trace_digest": "sha256:" + hashlib.sha256(canonical([r.as_dict() for r in rows])).hexdigest(),
        }


def simulate(spec: dict[str, Any], name: str, seed: int = 7, injections: Injections | None = None,
             on_row: Callable[[Row], None] | None = None) -> Result:
    orrery = Orrery(spec, SCENARIOS[name](), seed, injections)
    rows: list[Row] = []
    for row in orrery.run():
        rows.append(row)
        if on_row is not None:
            on_row(row)
    return Result(name, seed, rows, orrery.ledger, orrery.summary(rows))


# ── self-test ─────────────────────────────────────────────────────────────────────────────────────


def selftest(spec: dict[str, Any] | None = None, seed: int = 7) -> tuple[list[str], dict[str, Result]]:
    """Problems found, empty when every scenario behaves as the blueprint says, and the runs themselves."""
    spec = spec or load_spec()
    problems: list[str] = []
    results: dict[str, Result] = {}
    for name, make in SCENARIOS.items():
        first, second = simulate(spec, name, seed), simulate(spec, name, seed)
        results[name] = first
        s, expect = first.summary, make().expect
        if s["trace_digest"] != second.summary["trace_digest"]:
            problems.append(f"{name}: two runs with seed {seed} differ")
        problems += [f"{name}: {p}" for p in verify_chain(first.envelopes)]
        if "decision" in expect and s["decision"] != expect["decision"]:
            problems.append(f"{name}: decision {s['decision']}, expected {expect['decision']}")
        limit = expect.get("max_intervene_observation")
        if limit is not None and (s["intervene_at_observation"] or 10**6) > limit:
            problems.append(f"{name}: intervened at observation {s['intervene_at_observation']}, expected <= {limit}")
        if "final_state" in expect and s["final_state"] not in expect["final_state"]:
            problems.append(f"{name}: final state {s['final_state']}, expected one of {expect['final_state']}")
        for term in expect.get("terms", []):
            if term not in s["cross_terms"]:
                problems.append(f"{name}: cross-device term {term} was never applied")
        for counter, least in expect.get("min", {}).items():
            if s[counter] < least:
                problems.append(f"{name}: {counter} = {s[counter]}, expected >= {least}")
        events = {row.event for row in first.rows}
        for event in expect.get("events", []):
            if event not in events:
                problems.append(f"{name}: no {event} row")
        if not s["decision_record_stable"]:
            problems.append(f"{name}: the decision record changed after it was made (BT-4)")
        if expect.get("full_coverage"):
            for lane, c in s["coverage"].items():
                if c["inside"] != c["total"]:
                    problems.append(f"{name}: {lane} valid intervals missed the true time "
                                    f"{c['total'] - c['inside']} of {c['total']} times")
        if expect.get("door_coverage_misses") and s["coverage"]["door"]["inside"] == s["coverage"]["door"]["total"]:
            problems.append(f"{name}: a clock offset beyond epsilon_ring_ms went unnoticed")
    for vector in CONFORMANCE_VECTORS:
        if len(reference_trace_mnats(vector, spec["sprt"])) != len(vector):
            problems.append("conformance vector trace has the wrong length")
    return problems, results


# ── rendering ─────────────────────────────────────────────────────────────────────────────────────

BAR_LO, BAR_HI, BAR_WIDTH = -4.0, 7.0, 34


def _bar(lam: float, model: Model) -> str:
    def pos(x: float) -> int:
        return max(0, min(BAR_WIDTH - 1, round((x - BAR_LO) / (BAR_HI - BAR_LO) * (BAR_WIDTH - 1))))

    cells = ["."] * BAR_WIDTH
    cells[pos(0.0)], cells[pos(model.B)], cells[pos(model.A)] = "0", "B", "A"
    cells[pos(lam)] = "#"
    return "[" + "".join(cells) + "]"


def render_header(scenario: Scenario, seed: int, inj: Injections, model: Model) -> str:
    delays = ", ".join(f"{lane} {lo}-{hi}" for lane, (lo, hi) in inj.delay_ms.items() if lane in LANES)
    return "\n".join([
        f"HEARTHWARD orrery | scenario {scenario.name} | seed {seed} | DOOR and WRIST are synthetic",
        f"  {scenario.summary}",
        f"  injections: door offset {inj.door_offset_ms:+d} ms, drift {inj.door_drift_ppm:+d} ppm, delays ms "
        f"{delays} (x{inj.delay_scale_pct}%)",
        f"  A = {model.A:+.3f} (intervene)   B = {model.B:+.3f} (dismiss)   alpha {model.alpha}   beta {model.beta}",
        "",
        f"{'arrival':<10} {'DOOR (Ring)':<21} {'ROOM (Alexa+)':<21} {'WRIST (Bee)':<21} {'seq':>4} {'Lambda':>7} "
        f"{'delta':>6} {'-4' + ' ' * (BAR_WIDTH - 2) + '+7':<{BAR_WIDTH + 2}} event",
    ])


def render_row(row: Row, model: Model) -> str:
    cells = {lane: "" for lane in LANES}
    if row.lane in cells:
        cells[row.lane] = row.label
    lanes = " ".join(f"{cells[lane][:21]:<21}" for lane in LANES)
    seq = f"#{row.seq}" if row.seq is not None else ""
    lam = row.lambda_mnats / 1000
    delta = f"{row.delta_mnats / 1000:+.2f}" if row.delta_mnats else ""
    return f"{fmt_clock(row.at):<10} {lanes} {seq:>4} {lam:+7.3f} {delta:>6} {_bar(lam, model)} {row.event:<12} {row.note}"


def render_summary(summary: dict[str, Any]) -> str:
    coverage = ", ".join(f"{lane} {c['inside']}/{c['total']}" for lane, c in summary["coverage"].items())
    return "\n".join([
        "",
        f"decision {summary['decision']} at observation {summary['intervene_at_observation']}; final state "
        f"{summary['final_state']}; Lambda {summary['lambda_mnats'] / 1000:+.3f}; terms {summary['cross_terms']}",
        f"duplicates {summary['duplicates']}, quarantined {summary['quarantined']}, recovered by sync "
        f"{summary['drops_recovered']}, revisions {summary['revisions']} ({summary['revisions_after_decision']} after "
        f"the decision); interval coverage {coverage}",
        f"ledger head {summary['ledger_head']}",
        f"trace digest {summary['trace_digest']}",
    ])


# ── command line ──────────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HEARTHWARD orrery: deterministic multi-stream simulation of "
                                                 "DOOR, ROOM and WRIST through the blueprint's contracts.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="doorstep_scam")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--format", choices=("ascii", "json"), default="ascii")
    parser.add_argument("--realtime", type=float, default=0.0, metavar="SPEED",
                        help="pace rows by virtual time divided by SPEED (0 prints at once)")
    parser.add_argument("--door-offset-ms", type=int, help="Ring clock offset")
    parser.add_argument("--door-drift-ppm", type=int, help="Ring clock drift")
    parser.add_argument("--delay-scale-pct", type=int, help="scale every delivery delay")
    parser.add_argument("--bee-drop-p", type=float, help="probability that a Bee line is lost and recovered by sync")
    parser.add_argument("--ring-fail-p", type=float, help="probability that a Ring delivery needs one retry")
    parser.add_argument("--envelopes", action="store_true", help="print the ledger as JSON lines after the trace")
    parser.add_argument("--spec", type=Path, default=BLUEPRINT, help="blueprint to read the hearthward blocks from")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")

    if args.list_scenarios:
        for name, make in sorted(SCENARIOS.items()):
            print(f"{name:<18} {make().summary}")
        return 0
    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        print(f"orrery: {exc}", file=sys.stderr)
        return 2
    if args.selftest:
        problems, results = selftest(spec, args.seed)
        for name, result in results.items():
            s = result.summary
            print(f"{name:<18} decision {str(s['decision']):<9} obs {str(s['intervene_at_observation']):<4} "
                  f"state {s['final_state']:<19} Lambda {s['lambda_mnats'] / 1000:+7.3f} "
                  f"trace {s['trace_digest'][:19]}")
        for problem in problems:
            print(f"FAIL {problem}")
        print("orrery self-test: " + ("PASS" if not problems else f"FAIL ({len(problems)} problems)"))
        return 0 if not problems else 1

    scenario = SCENARIOS[args.scenario]()
    overrides = {k: v for k, v in {
        "door_offset_ms": args.door_offset_ms, "door_drift_ppm": args.door_drift_ppm,
        "delay_scale_pct": args.delay_scale_pct, "bee_drop_p": args.bee_drop_p, "ring_fail_p": args.ring_fail_p,
    }.items() if v is not None}
    injections = replace(scenario.injections, **overrides)
    model = Model.from_spec(spec["sprt"])
    if args.format == "ascii":
        print(render_header(scenario, args.seed, injections, model))

    previous = [0]

    def emit(row: Row) -> None:
        if args.realtime > 0:
            time.sleep(min(2.0, max(0, row.at - previous[0]) / 1000 / args.realtime))
            previous[0] = row.at
        print(json.dumps(row.as_dict()) if args.format == "json" else render_row(row, model), flush=True)

    result = simulate(spec, args.scenario, args.seed, injections, emit)
    if args.format == "json":
        print(json.dumps({"summary": result.summary}))
    else:
        print(render_summary(result.summary))
    if args.envelopes:
        for envelope in result.envelopes:
            print(json.dumps(envelope))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
