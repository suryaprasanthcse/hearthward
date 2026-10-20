"""The event envelope (blueprint §4.1) and the ledger chain (§4.2).

`validate_envelope` applies the exported JSON Schema (Draft 2020-12) plus what the schema cannot express:
E-1 (`valid.lo <= valid.hi`), E-5 (the HW-JCS profile), full-string formats for the household and every digest
(JSON Schema `pattern` is a search, so `$` would accept a trailing newline), and the source-to-scheme and
source-to-kind mapping of the §4.1 table. `chain_head` and `verify_chain` reproduce `tools/orrery.py` exactly.
"""

from __future__ import annotations

import re
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator

from hearthward.contracts.canonical import ProfileError, check_profile, tagged_digest
from hearthward.contracts.specs import envelope_schema

LEDGER_TAG = b"hw-ledger-v1"
ZERO_DIGEST = "sha256:" + "0" * 64
CHAIN_FIELDS = ("seq", "prev", "head")
SCHEME = {"ring": "ring-hmac", "bee": "bridge-hmac", "alexa": "oauth2.1", "system": "internal"}
KIND_PREFIX = {"ring": "door.", "bee": "wrist.", "alexa": "room.", "system": "system."}

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_HOUSEHOLD = re.compile(r"hh_[0-9a-z]{8,32}")


@cache
def _validator() -> Draft202012Validator:
    schema = envelope_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _where(path: Any) -> str:
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)


def validate_envelope(env: Any) -> list[str]:
    """Problems with one envelope, empty when it satisfies the schema, E-1, E-5 and the digest formats."""
    try:
        check_profile(env)
    except ProfileError as exc:
        return [f"E-5: {exc}"]
    errors = sorted(f"{_where(e.absolute_path)}: {e.message}" for e in _validator().iter_errors(env))
    if not isinstance(env, dict):
        return errors
    valid = env.get("valid")
    if (isinstance(valid, dict) and type(valid.get("lo")) is int and type(valid.get("hi")) is int
            and valid["lo"] > valid["hi"]):
        errors.append("$.valid: lo > hi (E-1)")
    household = env.get("household")
    if isinstance(household, str) and not _HOUSEHOLD.fullmatch(household):
        errors.append("$.household: not hh_ followed by 8 to 32 of [0-9a-z]")
    for name in ("raw_digest", "payload_digest", "prev", "head"):
        value = env.get(name)
        if isinstance(value, str) and not _DIGEST.fullmatch(value):
            errors.append(f"$.{name}: not sha256: followed by 64 lowercase hex characters (C-4)")
    source, auth, kind = env.get("source"), env.get("auth"), env.get("kind")
    if isinstance(source, str) and source in SCHEME:
        if isinstance(auth, dict) and isinstance(auth.get("scheme"), str) and auth["scheme"] != SCHEME[source]:
            errors.append(f"$.auth.scheme: {auth['scheme']!r} does not authenticate source {source!r}")
        if isinstance(kind, str) and not kind.startswith(KIND_PREFIX[source]):
            errors.append(f"$.kind: {kind!r} cannot come from source {source!r}")
    return errors


def chain_head(env: dict[str, Any]) -> str:
    """head_n = sha256:hex(SHA-256("hw-ledger-v1" || 0x00 || JCS(envelope_n without "head"))) (§4.2)."""
    return tagged_digest(LEDGER_TAG, {k: v for k, v in env.items() if k != "head"})


def verify_chain(envelopes: list[dict[str, Any]]) -> list[str]:
    """Problems with one household's ledger in sequence order (E-1 to E-3 and every head), empty when intact."""
    problems: list[str] = []
    prev, household = ZERO_DIGEST, None
    for n, env in enumerate(envelopes, 1):
        if not isinstance(env, dict) or any(f not in env for f in CHAIN_FIELDS):
            problems.append(f"position {n}: not an envelope with seq, prev and head")
            prev = None
            continue
        if type(env["seq"]) is not int or env["seq"] != n:
            problems.append(f"seq {env['seq']!r} at position {n} (E-2)")
        if prev is None or env["prev"] != prev:
            problems.append(f"seq {n}: prev does not match the previous head (E-3)")
        try:
            if chain_head(env) != env["head"]:
                problems.append(f"seq {n}: head does not match its contents")
        except ProfileError as exc:
            problems.append(f"seq {n}: outside HW-JCS, so its head cannot be recomputed (E-5): {exc}")
        valid = env.get("valid")
        if isinstance(valid, dict) and type(valid.get("lo")) is int and type(valid.get("hi")) is int:
            if valid["lo"] > valid["hi"]:
                problems.append(f"seq {n}: valid.lo > valid.hi (E-1)")
        else:
            problems.append(f"seq {n}: valid is not an integer interval")
        if household is None:
            household = env.get("household")
        elif env.get("household") != household:
            problems.append(f"seq {n}: household {env.get('household')!r} in the chain of {household!r}")
        prev = env["head"]
    return problems
