"""HX-01 to HX-12 as table-driven code (blueprint §5.4). No model is in the path.

Each `Rule` carries the metadata of its row in the `hearthward:hx` block and one pure check over a `Request` (what is
asked) and `Facts` (what the broker knows). `evaluate` runs every rule that applies to the tool in the given phase:

    declare    before a challenge exists: everything that does not need a proof
    authorize  a proof has been presented, the nonce is not yet consumed
    final      the nonce has been consumed by its conditional write; the action may run

A check returns True when the rule is satisfied. A rule that fails returns its `on_fail`: BLOCK and THROTTLE stop the
request, PROOF_REQUIRED and REQUIRE_DUAL keep it waiting for more proof (HX-11: every failure is a refusal, never an
approval). `tests/unit/test_broker.py` pins this table to the `hearthward:hx` block, field by field.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hearthward.mcp.runtime import normalize_name
from hearthward.mcp.schemas import SMINUS, definitions_by_name, tool_class

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
MAX_PAUSE_HOURS = 24
CONCERNS_PER_HOUR = 6
CALLS_PER_EPISODE = 3
VERIFIED_NUMBER_SOURCES = frozenset({"circle", "trusted_vendors", "curated_directory"})
PHONE_KEYS = re.compile(r"phone|number|tel", re.IGNORECASE)


class OnFail(StrEnum):
    BLOCK = "BLOCK"
    PROOF_REQUIRED = "PROOF_REQUIRED"
    REQUIRE_DUAL = "REQUIRE_DUAL"
    THROTTLE = "THROTTLE"


STOPS = frozenset({OnFail.BLOCK, OnFail.THROTTLE})


@dataclass(frozen=True)
class Request:
    tool: str
    args: Mapping[str, Any]
    household: str
    episode_version: int = 0


@dataclass(frozen=True)
class Facts:
    """Everything the checks read. `None` in a proof field means no proof has been presented yet."""

    now_ms: int
    cooldown_ms: int = 1_800_000
    # episode state (HX-01, HX-08)
    episode_open: bool = False
    episode_closed_at_ms: int | None = None
    episode_version: int = 0
    open_episode_id: str | None = None
    evidence_sources: frozenset[str] = frozenset()
    # the circle (HX-07)
    member_names: Mapping[str, str] = field(default_factory=dict)  # member id -> display name
    # proof (HX-02, HX-03, HX-04)
    assertion_member: str | None = None
    challenge_matches: bool | None = None
    counter_increased: bool | None = None
    nonce_consumed: bool | None = None
    approvers: frozenset[str] = frozenset()  # distinct circle members who approved this challenge
    # counterfactual branch (HX-09)
    closes_episode: bool = False
    removes_evidence_source: bool = False
    # provenance, history, rates, consent (HX-05, HX-06, HX-10, HX-12)
    number_sources: tuple[str, ...] = ()
    recent_name_claims: tuple[tuple[str, int], ...] = ()  # (claimed organization, at_ms)
    concerns_last_hour: int = 0
    calls_this_episode: int = 0
    elder_consent_recorded: bool = False
    # dependencies (HX-11)
    broker_up: bool = True
    ledger_up: bool = True
    nonce_store_up: bool = True


Check = Callable[[Request, Facts], bool]


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    applies_to: tuple[str, ...]  # "S-", "*" or tool names, as in the HX block
    enforced_at: str
    on_fail: OnFail
    phases: frozenset[str]
    check: Check

    def applies(self, tool: str) -> bool:
        klass = tool_class(definitions_by_name()[tool])
        return "*" in self.applies_to or klass in self.applies_to or tool in self.applies_to


# ── the twelve checks ───────────────────────────────────────────────────────────────────────────────────


def _locked(f: Facts) -> bool:
    after_close = f.episode_closed_at_ms is not None and f.now_ms - f.episode_closed_at_ms < f.cooldown_ms
    return f.episode_open or after_close


def hx01(r: Request, f: Facts) -> bool:
    """No S- action while an episode is open or intervening, or within cooldown_ms after it closes, unless two
    distinct circle members approve."""
    return not _locked(f) or len(f.approvers) >= 2


def hx02(r: Request, f: Facts) -> bool:
    """A WebAuthn assertion from a circle member's registered passkey. An Alexa token alone never suffices: nothing
    in `Request` or `Facts` carries one."""
    return f.assertion_member is not None and f.assertion_member in f.member_names


def hx03(r: Request, f: Facts) -> bool:
    """The assertion challenge equals the action hash."""
    return f.challenge_matches is True


def hx04(r: Request, f: Facts) -> bool:
    """The nonce is consumed by a conditional write and the signature counter increased."""
    return f.counter_increased is True and f.nonce_consumed is not False


def hx04_final(r: Request, f: Facts) -> bool:
    return f.counter_increased is True and f.nonce_consumed is True


def hx05(r: Request, f: Facts) -> bool:
    """Numbers come only from verified circle and vendor entries or the curated directory, never from arguments."""
    if any(PHONE_KEYS.search(key) for key in r.args):
        return False
    return all(source in VERIFIED_NUMBER_SOURCES for source in f.number_sources)


def hx06(r: Request, f: Facts) -> bool:
    """No vendor within 24 hours of a visit or consultation that claimed a matching organization name."""
    name = normalize_name(str(r.args.get("name", "")))
    # A claim stamped in the future (clock skew) counts as recent: the rule errs toward waiting.
    return not any(normalize_name(claimed) == name and f.now_ms - at < DAY_MS for claimed, at in f.recent_name_claims)


def hx07(r: Request, f: Facts) -> bool:
    """Proof from an existing member other than the person being added or removed."""
    approver = f.member_names.get(f.assertion_member or "")
    return approver is not None and normalize_name(approver) != normalize_name(str(r.args.get("display_name", "")))


def hx08(r: Request, f: Facts) -> bool:
    """At most 24 hours, and not while an episode is open."""
    hours = r.args.get("hours")
    return type(hours) is int and 1 <= hours <= MAX_PAUSE_HOURS and not f.episode_open


def hx09(r: Request, f: Facts) -> bool:
    """An action that would close an open episode or remove an evidence source needs two distinct members."""
    return not (f.closes_episode or f.removes_evidence_source) or len(f.approvers) >= 2


def hx10(r: Request, f: Facts) -> bool:
    """6 concerns per hour per household, 3 calls per episode."""
    if r.tool == "raise_concern":
        return f.concerns_last_hour < CONCERNS_PER_HOUR
    return f.calls_this_episode < CALLS_PER_EPISODE


def hx11(r: Request, f: Facts) -> bool:
    """If the broker, ledger or nonce store is unavailable, every S- tool is blocked; S+ tools keep working."""
    if tool_class(definitions_by_name()[r.tool]) != SMINUS:
        return True
    return f.broker_up and f.ledger_up and f.nonce_store_up


def hx12(r: Request, f: Facts) -> bool:
    """Evidence leaves the circle only with the elder's recorded consent (and, by HX-02, a proof)."""
    return f.elder_consent_recorded


ALL = frozenset({"declare", "authorize", "final"})
PROOF = frozenset({"authorize", "final"})
RULES: tuple[Rule, ...] = (
    Rule("HX-01", "Open-episode lock", ("S-",), "broker", OnFail.REQUIRE_DUAL, ALL, hx01),
    Rule("HX-02", "Proof origin", ("S-",), "broker", OnFail.PROOF_REQUIRED, PROOF, hx02),
    Rule("HX-03", "Action binding", ("S-",), "broker", OnFail.BLOCK, PROOF, hx03),
    Rule("HX-04", "Single use", ("S-",), "broker", OnFail.BLOCK, PROOF, hx04),
    Rule("HX-05", "Number provenance", ("verify_organization_contact", "call_trusted_contact"), "mcp",
         OnFail.BLOCK, ALL, hx05),
    Rule("HX-06", "Vendor cooling-off", ("add_trusted_vendor",), "broker", OnFail.BLOCK, ALL, hx06),
    Rule("HX-07", "Circle changes", ("edit_trusted_circle",), "broker", OnFail.PROOF_REQUIRED, PROOF, hx07),
    Rule("HX-08", "Bounded pause", ("pause_monitoring",), "broker", OnFail.BLOCK, ALL, hx08),
    Rule("HX-09", "Simulated effect", ("S-",), "kernel", OnFail.REQUIRE_DUAL, ALL, hx09),
    Rule("HX-10", "Safety-raising rate limits", ("raise_concern", "call_trusted_contact"), "mcp",
         OnFail.THROTTLE, ALL, hx10),
    Rule("HX-11", "Fail closed only downward", ("*",), "mcp", OnFail.BLOCK, ALL, hx11),
    Rule("HX-12", "Evidence egress", ("share_evidence",), "broker+iam", OnFail.PROOF_REQUIRED, ALL, hx12),
)
RULE_IDS = [rule.id for rule in RULES]
BY_ID = {rule.id: rule for rule in RULES}


@dataclass(frozen=True)
class Verdict:
    failures: tuple[Rule, ...]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def stops(self) -> tuple[Rule, ...]:
        """Failures that end the request (BLOCK, THROTTLE)."""
        return tuple(r for r in self.failures if r.on_fail in STOPS)

    @property
    def waiting(self) -> tuple[Rule, ...]:
        """Failures that only mean more proof is needed (PROOF_REQUIRED, REQUIRE_DUAL)."""
        return tuple(r for r in self.failures if r.on_fail not in STOPS)

    @property
    def needs_dual(self) -> bool:
        return any(r.on_fail is OnFail.REQUIRE_DUAL for r in self.failures)

    def ids(self, rules: tuple[Rule, ...] | None = None) -> list[str]:
        return sorted(r.id for r in (self.failures if rules is None else rules))


def applicable(tool: str) -> tuple[Rule, ...]:
    return tuple(rule for rule in RULES if rule.applies(tool))


def evaluate(request: Request, facts: Facts, phase: str = "final") -> Verdict:
    """Run every rule that applies to the tool in `phase` and collect the failures."""
    if phase not in ALL:
        raise ValueError(f"unknown phase: {phase!r}")
    failures = []
    for rule in applicable(request.tool):
        if phase not in rule.phases:
            continue
        check = hx04_final if rule.id == "HX-04" and phase == "final" else rule.check
        if not check(request, facts):
            failures.append(rule)
    return Verdict(tuple(failures))
