"""What a tool handler runs against: the caller, a clock, id minting and a household store.

`SyntheticStore` is the in-memory store gate G2 ships with. It is labelled `synthetic` on the wire
(`data_origin`, HW-05); the DynamoDB-backed store arrives with the ledger wiring in a later gate.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

DataOrigin = Literal["live", "synthetic"]
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
OPEN_STATES = ("open", "intervening")
HOUR_MS = 3_600_000


def normalize_name(name: str) -> str:
    """Case-folded, whitespace-normalized (HX-06's matching rule, reused for directory lookups)."""
    return " ".join(name.casefold().split())


def mint_id(prefix: str, now_ms: int, entropy: Callable[[int], bytes] = secrets.token_bytes) -> str:
    """`<prefix>_` plus a 26-character Crockford ULID: 48-bit millisecond time, 80 random bits."""
    value = (now_ms & ((1 << 48) - 1)) << 80 | int.from_bytes(entropy(10), "big")
    return f"{prefix}_" + "".join(CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


@dataclass(frozen=True)
class Principal:
    """Who the bearer token says is calling. A token identifies the household, never the speaker."""

    subject: str
    kind: Literal["user", "service"]
    scopes: tuple[str, ...] = ()


@dataclass
class Contact:
    role: str
    display_name: str
    phone_e164: str
    verified: bool = True


@dataclass
class Vendor:
    name: str
    phone_e164: str
    verified: bool = True


@dataclass
class Episode:
    episode_id: str
    state: str
    evidence_mnats: int
    reasons: list[dict[str, str]] = field(default_factory=list)
    decided_at_ms: int = 0
    belief_seq: int = 0
    alerted: bool = True
    notified_roles: list[str] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES


class Notifier(Protocol):
    def notify(self, household: str, contact: Contact, episode_id: str | None, at_ms: int) -> None: ...


class Broker(Protocol):
    """The proof broker (blueprint §5.5). Gate G5 supplies it; until then S- tools never see one."""

    def challenge(self, household: str, tool: str, args: dict[str, Any]) -> dict[str, Any]: ...


class SyntheticStore:
    """Deterministic in-memory household data for tests and the synthetic demo; every answer is `synthetic`."""

    data_origin: DataOrigin = "synthetic"

    def __init__(self) -> None:
        self.households: dict[str, str] = {}
        self.default_household: str | None = "hh_synthetic"
        self.visits: list[dict[str, Any]] = []
        self.vendors: list[Vendor] = []
        self.directory: dict[str, tuple[str, str]] = {}
        self.circle: list[Contact] = []
        self.episodes: list[Episode] = []
        self.concern_times_ms: list[int] = []
        self.concerns: list[dict[str, Any]] = []
        self.notifications: list[tuple[str, str, str | None, int]] = []

    def resolve_household(self, subject: str) -> str | None:
        return self.households.get(subject, self.default_household)

    def visits_since(self, since_ms: int) -> list[dict[str, Any]]:
        return [v for v in self.visits if v["last_seen_ms"] >= since_ms]

    def trusted_vendor_names(self) -> set[str]:
        return {normalize_name(v.name) for v in self.vendors if v.verified}

    def circle_names(self) -> set[str]:
        return {normalize_name(c.display_name) for c in self.circle if c.verified}

    def official_number(self, organization: str) -> tuple[str, str, str] | None:
        """(display_name, phone, source) from a verified vendor, else the curated directory, else None (HX-05)."""
        key = normalize_name(organization)
        for vendor in self.vendors:
            if vendor.verified and normalize_name(vendor.name) == key:
                return vendor.name, vendor.phone_e164, "trusted_vendors"
        if key in self.directory:
            display, phone = self.directory[key]
            return display, phone, "curated_directory"
        return None

    def verified_contact(self, role: str) -> Contact | None:
        return next((c for c in self.circle if c.verified and c.role == role), None)

    def open_episode(self) -> Episode | None:
        return next((e for e in reversed(self.episodes) if e.is_open), None)

    def latest_alert(self) -> Episode | None:
        return next((e for e in reversed(self.episodes) if e.alerted), None)

    def recent_concerns(self, now_ms: int) -> int:
        return sum(1 for t in self.concern_times_ms if now_ms - t < HOUR_MS)

    def notify(self, household: str, contact: Contact, episode_id: str | None, at_ms: int) -> None:
        self.notifications.append((household, contact.role, episode_id, at_ms))


@dataclass
class ToolContext:
    principal: Principal
    household: str
    store: SyntheticStore
    clock: Callable[[], int] = lambda: int(time.time() * 1000)
    notifier: Notifier | None = None
    broker: Broker | None = None
    entropy: Callable[[int], bytes] = secrets.token_bytes

    def new_id(self, prefix: str) -> str:
        return mint_id(prefix, self.clock(), self.entropy)

    @property
    def data_origin(self) -> DataOrigin:
        return self.store.data_origin
