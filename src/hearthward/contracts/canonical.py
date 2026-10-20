"""HW-JCS (blueprint §4.3): RFC 8785 restricted to the C-2 profile, plus SHA-256 digests (C-4, C-7).

The profile is checked first and strictly: object keys match `[a-z0-9_]+` in full, values are `None`, `bool`,
`str`, `int` with |n| <= 2**53 - 1, `list` or `dict`, and nothing else (no floats, NaN, infinities, tuples,
subclasses or non-string keys). Only a value inside the profile reaches the `rfc8785` serializer, and inside
the profile its bytes equal `tools/orrery.py`'s sorted compact `json.dumps`.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import rfc8785

SAFE_INT = 2**53 - 1
MAX_DEPTH = 64
_KEY = re.compile(r"[a-z0-9_]+")


class ProfileError(ValueError):
    """The value is outside the HW-JCS profile (C-2), so it has no canonical form."""


def check_profile(value: Any) -> None:
    """Raise ProfileError unless `value` is inside the C-2 profile."""
    _check(value, None, 0)


# A path is a linked list (parent, segment) so each step costs O(1); it is rendered only when an error is raised.
_Path = tuple[Any, str] | None


def _render(path: _Path) -> str:
    segments = []
    while path is not None:
        path, segment = path
        segments.append(segment)
    return "$" + "".join(reversed(segments))


def _check(value: Any, path: _Path, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise ProfileError(f"{_render(path)}: nested deeper than {MAX_DEPTH} levels")
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind is int:
        if not -SAFE_INT <= value <= SAFE_INT:
            raise ProfileError(f"{_render(path)}: integer is outside the safe range |n| <= 2**53 - 1")
        return
    if kind is str:
        if not is_unicode(value):
            raise ProfileError(f"{_render(path)}: string is not valid Unicode (lone surrogate)")
        return
    if kind is list:
        for i, item in enumerate(value):
            _check(item, (path, f"[{i}]"), depth + 1)
        return
    if kind is dict:
        for key, item in value.items():
            if type(key) is not str or not _KEY.fullmatch(key):
                raise ProfileError(f"{_render(path)}: key {key!r:.80} is not snake_case ASCII")
            _check(item, (path, f".{key}"), depth + 1)
        return
    raise ProfileError(f"{_render(path)}: {kind.__name__} values are outside HW-JCS (floats are forbidden)")


def is_unicode(text: str) -> bool:
    """True when `text` has a UTF-8 form (no lone surrogates)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate object key")
        out[key] = value
    return out


def parse_strict(raw: bytes) -> Any:
    """Parse UTF-8 JSON strictly: no NaN or Infinity and no duplicate keys. Raises ValueError, never RecursionError.

    Parsing says nothing about the profile; callers that need HW-JCS call `check_profile` on the result."""
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_no_constant, object_pairs_hook=_no_duplicates)
    except RecursionError:
        raise ValueError("JSON nested too deeply") from None


def canonical(value: Any) -> bytes:
    """HW-JCS bytes of `value` (C-1, C-2)."""
    check_profile(value)
    try:
        return rfc8785.dumps(value)
    except rfc8785.CanonicalizationError as exc:  # unreachable inside the profile; kept as a hard stop
        raise ProfileError(f"$: {exc}") from exc


def digest(value: Any) -> str:
    """`sha256:` + 64 lowercase hex characters over the canonical bytes (C-4)."""
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def tagged_digest(tag: bytes, value: Any) -> str:
    """A domain-separated digest (C-7): SHA-256(tag || 0x00 || HW-JCS(value))."""
    if type(tag) is not bytes or not tag or b"\x00" in tag:
        raise ValueError("a domain-separation tag is non-empty bytes without NUL")
    return "sha256:" + hashlib.sha256(tag + b"\x00" + canonical(value)).hexdigest()
