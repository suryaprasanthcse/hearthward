"""S- tools: they lower trust or protection, so they fail closed (blueprint §5.1, HX-11).

Gate G2 ships no proof broker. Without one every S- call returns `BLOCKED`, and no code path here can return a
success status (DISMISSED, PAUSED, ADDED, UPDATED, SHARED). When a broker is injected (gate G5) it may only
answer with a `PROOF_REQUIRED` challenge; anything else from it, or any failure of it, becomes `BLOCKED`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from hearthward.mcp.runtime import ToolContext

RULE = re.compile(r"^HX-(0[1-9]|1[0-2])$")
CHALLENGE_ID = re.compile(r"^chl_[0-9A-HJKMNP-TV-Z]{26}$")
APPROVERS = ("caregiver_phone", "elder_phone")

# Rules that apply on top of the common ones (HX-01, HX-02, HX-03, HX-04, HX-09, HX-11).
TOOL_RULES = {
    "dismiss_alert": (),
    "pause_monitoring": ("HX-08",),
    "add_trusted_vendor": ("HX-06",),
    "edit_trusted_circle": ("HX-07",),
    "share_evidence": ("HX-12",),
}
HINTS = {
    "dismiss_alert": "I can't dismiss that from here. A family member has to confirm it on their phone.",
    "pause_monitoring": "I can't pause monitoring from here. A family member has to confirm it on their phone.",
    "add_trusted_vendor": "I can't add a vendor from here. A family member has to confirm it on their phone.",
    "edit_trusted_circle": "I can't change your circle from here. Another family member has to confirm it.",
    "share_evidence": "I can't share that from here. It needs a family member's confirmation and your consent.",
}


def blocked(tool: str, rules: tuple[str, ...]) -> dict[str, Any]:
    return {"status": "BLOCKED", "rules": sorted(set(rules)), "spoken_hint": HINTS[tool]}


def _proof_required(tool: str, answer: dict[str, Any], now_ms: int) -> dict[str, Any] | None:
    """Accept a broker answer only if it is a well-formed, unexpired PROOF_REQUIRED challenge."""
    rules = answer.get("rules")
    ok = (
        answer.get("status") == "PROOF_REQUIRED"
        and isinstance(answer.get("challenge_id"), str) and CHALLENGE_ID.fullmatch(answer["challenge_id"])
        and answer.get("approve_on") in APPROVERS
        and isinstance(rules, list) and len(rules) <= 12 and all(isinstance(r, str) and RULE.fullmatch(r) for r in rules)
        and isinstance(answer.get("expires_at_ms"), int) and not isinstance(answer["expires_at_ms"], bool)
        and answer["expires_at_ms"] > now_ms
    )
    if not ok:
        return None
    return {"status": "PROOF_REQUIRED", "challenge_id": answer["challenge_id"], "approve_on": answer["approve_on"],
            "rules": rules, "expires_at_ms": answer["expires_at_ms"], "spoken_hint": HINTS[tool]}


def _broker_refusal(tool: str, answer: dict[str, Any]) -> dict[str, Any] | None:
    """A broker that refuses says which rules stopped the action; keep those (HX-05 must not read as HX-11)."""
    rules = answer.get("rules")
    if (answer.get("status") == "BLOCKED" and isinstance(rules, list) and 0 < len(rules) <= 12
            and all(isinstance(r, str) and RULE.fullmatch(r) for r in rules)):
        return blocked(tool, tuple(rules))
    return None


def _handler(tool: str) -> Callable[[dict[str, Any], ToolContext], dict[str, Any]]:
    def handle(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        base = ("HX-02", *TOOL_RULES[tool])
        if ctx.broker is None:  # nobody to ask: refuse (HX-11), naming the open-episode lock when it applies
            locked = ("HX-01",) if ctx.store.open_episode() is not None else ()
            return blocked(tool, (*base, *locked, "HX-11"))
        # With a broker, HX-01 is its to decide: an open episode needs two distinct members, not a flat refusal.
        try:
            answer = ctx.broker.challenge(ctx.household, tool, args)
        except Exception:  # noqa: BLE001 - HX-11: a failing broker may never open the door
            return blocked(tool, (*base, "HX-11"))
        if not isinstance(answer, dict):
            return blocked(tool, (*base, "HX-11"))
        return (_proof_required(tool, answer, ctx.clock()) or _broker_refusal(tool, answer)
                or blocked(tool, (*base, "HX-11")))

    return handle


HANDLERS: dict[str, Callable[[dict[str, Any], ToolContext], dict[str, Any]]] = {t: _handler(t) for t in TOOL_RULES}
