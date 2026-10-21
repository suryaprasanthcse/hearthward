"""S+ tools: read-only, or they raise safety. They fail open; worst case is a false alarm, bounded by HX-10.

The visitor scorer here is a placeholder additive model in milli-nats. The SPRT over the scam-script HMM
(blueprint §6) replaces it in gate G4; the tool contract (§5.3) does not change when it does.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from hearthward.mcp.runtime import Contact, Episode, ToolContext, normalize_name

INTERVENE_MNATS = 4554
ELEVATED_MNATS = 1000
EVIDENCE_CAP = 100_000
CONCERN_BOOST_MNATS = 1500
CONCERNS_PER_HOUR = 6
CALLS_PER_EPISODE = 3
WINDOWS_MS = {"last_15_min": 900_000, "last_hour": 3_600_000, "today": 86_400_000}
E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
VISIT_KEYS = ("visit_id", "first_seen_ms", "last_seen_ms", "cameras", "trigger", "vehicle_seen", "visitor", "open")

REASON_TEXT = {
    "NO_VISIT_ON_CAMERA": ("The door cameras show no recent visit.", 800),
    "VISIT_IN_PROGRESS": ("Someone is at the door right now.", 300),
    "NOT_IN_TRUSTED_VENDORS": ("That organization is not one of your trusted vendors.", 700),
    "SECRECY_REQUEST": ("They asked you to keep this secret.", 1800),
    "IRREVERSIBLE_PAYMENT": ("They asked for a payment that cannot be taken back.", 2200),
    "IDENTITY_MISMATCH": ("The person they claim to be is not in your trusted circle.", 1500),
    "URGENCY": ("They are rushing you to act now.", 900),
    "AUTHORITY_CLAIM": ("They claim to be an official or a bank.", 900),
}
IRREVERSIBLE = {"cash", "gift_card", "wire", "crypto"}


def get_door_activity(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    now = ctx.clock()
    recent = ctx.store.visits_since(now - WINDOWS_MS[args["window"]])
    recent = sorted(recent, key=lambda v: v["last_seen_ms"], reverse=True)[:20]
    visits = [{k: v[k] for k in VISIT_KEYS if k in v} for v in recent]
    unknown = sum(1 for v in visits if v["visitor"] == "unknown")
    if not visits:
        summary = "No one has come to the door in that time."
    else:
        summary = f"{len(visits)} visit{'s' if len(visits) != 1 else ''}, {unknown} from someone unknown."
    return {"visits": visits, "summary": summary, "data_origin": ctx.data_origin}


def _risk_level(evidence_mnats: int) -> str:
    if evidence_mnats >= INTERVENE_MNATS:
        return "high"
    return "elevated" if evidence_mnats >= ELEVATED_MNATS else "low"


def _flags(args: dict[str, Any], ctx: ToolContext) -> list[str]:
    """Reason codes the claims raise. Every argument is the customer's report, so none lowers the score."""
    flags: list[str] = []
    now = ctx.clock()
    recent = ctx.store.visits_since(now - WINDOWS_MS["last_hour"])
    if any(v.get("open") for v in recent):
        flags.append("VISIT_IN_PROGRESS")
    elif not recent:
        flags.append("NO_VISIT_ON_CAMERA")
    org = args.get("claimed_organization")
    if org and normalize_name(org) not in ctx.store.trusted_vendor_names():
        flags.append("NOT_IN_TRUSTED_VENDORS")
    if args.get("asked_secrecy"):
        flags.append("SECRECY_REQUEST")
    if args.get("payment_requested") in IRREVERSIBLE:
        flags.append("IRREVERSIBLE_PAYMENT")
    name = args.get("claimed_identity_name")
    family = args.get("claimed_relationship") in ("grandchild", "child", "other_family")
    if family and (not name or normalize_name(name) not in ctx.store.circle_names()):
        flags.append("IDENTITY_MISMATCH")
    if args.get("urgency_claimed"):
        flags.append("URGENCY")
    if args.get("claimed_purpose") in ("government", "bank") or args.get("claimed_relationship") == "official":
        flags.append("AUTHORITY_CLAIM")
    return flags


def _reasons(flags: list[str]) -> list[dict[str, str]]:
    ranked = sorted(flags, key=lambda code: -REASON_TEXT[code][1])[:5]
    return [{"code": code, "plain_text": REASON_TEXT[code][0]} for code in ranked]


def _raise_episode(ctx: ToolContext, evidence: int, reasons: list[dict[str, str]], *, alerted: bool) -> Episode:
    """Attach to the open episode, only ever raising its evidence, or start a new one (monotone: never lowers)."""
    now = ctx.clock()
    episode = ctx.store.open_episode()
    if episode is None:
        state = "open" if evidence >= ELEVATED_MNATS else "closed_inconclusive"
        episode = Episode(ctx.new_id("ep"), state, 0, alerted=alerted and state == "open")
        ctx.store.episodes.append(episode)
    episode.evidence_mnats = min(max(episode.evidence_mnats, evidence), EVIDENCE_CAP)
    known = {r["code"] for r in episode.reasons}
    episode.reasons = (episode.reasons + [r for r in reasons if r["code"] not in known])[:5]
    episode.decided_at_ms = now
    episode.belief_seq += 1
    return episode


def assess_visitor(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    flags = _flags(args, ctx)
    evidence = min(sum(REASON_TEXT[code][1] for code in flags), EVIDENCE_CAP)
    reasons = _reasons(flags)
    episode = _raise_episode(ctx, evidence, reasons, alerted=True)
    risk = _risk_level(episode.evidence_mnats)
    if risk == "high" or "SECRECY_REQUEST" in flags or "IRREVERSIBLE_PAYMENT" in flags:
        step = "call_trusted_contact"
    elif risk == "elevated":
        step = "verify_via_official_number" if args.get("claimed_organization") else "decline_politely"
    else:
        step = "no_action"
    return {"risk_level": risk, "reasons": reasons, "recommended_next_step": step,
            "episode_id": episode.episode_id, "evidence_mnats": episode.evidence_mnats,
            "data_origin": ctx.data_origin}


def verify_organization_contact(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """HX-05: the number comes from a verified vendor or the curated directory, never from the arguments."""
    hit = ctx.store.official_number(args["organization_name"])
    if hit is None or not E164.fullmatch(hit[1]):
        return {"found": False, "source": "none", "advice": "no_official_record", "data_origin": ctx.data_origin}
    display, phone, source = hit
    return {"found": True, "display_name": display[:80], "official_phone_e164": phone, "source": source,
            "advice": "call_official_number", "data_origin": ctx.data_origin}


def explain_last_alert(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    episode = ctx.store.latest_alert()
    base = {"intervene_threshold_mnats": INTERVENE_MNATS, "data_origin": ctx.data_origin}
    if episode is None:
        return {"state": "none", "reasons": [], **base}
    return {"episode_id": episode.episode_id, "state": episode.state, "reasons": list(episode.reasons),
            "evidence_mnats": episode.evidence_mnats, "decided_at_ms": episode.decided_at_ms,
            "belief_seq": episode.belief_seq, **base}


def _notify(ctx: ToolContext, contact: Contact, episode_id: str | None) -> None:
    now = ctx.clock()
    ctx.store.notify(ctx.household, contact, episode_id, now)
    if ctx.notifier is not None:
        ctx.notifier.notify(ctx.household, contact, episode_id, now)


def _calls_so_far(ctx: ToolContext, episode_id: str | None) -> list[str]:
    now = ctx.clock()
    return [role for household, role, ep, at in ctx.store.notifications
            if household == ctx.household and ep == episode_id and (episode_id is not None or now - at < 3_600_000)]


def raise_concern(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """The text is stored as untrusted data and never read as instructions; it can only add fixed evidence (HX-10)."""
    now = ctx.clock()
    throttled = ctx.store.recent_concerns(now) >= CONCERNS_PER_HOUR
    ctx.store.concern_times_ms.append(now)
    ctx.store.concerns.append({"at_ms": now, "about": args["about"], "text": args["concern"], "throttled": throttled})
    prior = ctx.store.open_episode()
    evidence = (prior.evidence_mnats if prior else 0) + CONCERN_BOOST_MNATS
    episode = _raise_episode(ctx, evidence, [], alerted=True)
    step = "monitoring"
    if not throttled:
        if episode.evidence_mnats >= INTERVENE_MNATS:
            step = "call_trusted_contact"
        elif episode.evidence_mnats >= ELEVATED_MNATS:
            contact = ctx.store.verified_contact("primary")
            if contact is not None:
                if "primary" not in _calls_so_far(ctx, episode.episode_id):
                    _notify(ctx, contact, episode.episode_id)
                step = "family_notified"
    return {"acknowledged": True, "episode_id": episode.episode_id, "next_step": step,
            "data_origin": ctx.data_origin}


def call_trusted_contact(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """HX-05: the contact comes from the verified circle by role; HX-10: three per episode."""
    role = args.get("contact_role", "primary")
    episode = ctx.store.open_episode()
    episode_id = episode.episode_id if episode else None
    prior = _calls_so_far(ctx, episode_id)
    level = ("L1", "L2", "L3")[min(len(prior), 2)]
    contact = ctx.store.verified_contact(role)
    if contact is None or not E164.fullmatch(contact.phone_e164):
        return {"status": "no_contact_configured", "ladder_level": level, "data_origin": ctx.data_origin}
    if role in prior or len(prior) >= CALLS_PER_EPISODE:
        return {"status": "already_notified", "contact_display_name": contact.display_name[:60],
                "ladder_level": level, "data_origin": ctx.data_origin}
    _notify(ctx, contact, episode_id)
    return {"status": "notified", "contact_display_name": contact.display_name[:60], "ladder_level": level,
            "data_origin": ctx.data_origin}


HANDLERS: dict[str, Callable[[dict[str, Any], ToolContext], dict[str, Any]]] = {
    "get_door_activity": get_door_activity,
    "assess_visitor": assess_visitor,
    "verify_organization_contact": verify_organization_contact,
    "explain_last_alert": explain_last_alert,
    "raise_concern": raise_concern,
    "call_trusted_contact": call_trusted_contact,
}
