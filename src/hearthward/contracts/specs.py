"""The blueprint's runtime blocks, read from the JSON that `tools/export_spec.py` writes (never from markdown)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

SPEC_DIR = Path(__file__).resolve().parent / "spec"


def _load(name: str) -> dict[str, Any]:
    return json.loads((SPEC_DIR / name).read_bytes())


@cache
def envelope_schema() -> dict[str, Any]:
    """`hearthward:envelope`: the event envelope's JSON Schema (§4.1)."""
    return _load("envelope.schema.json")


@cache
def temporal() -> dict[str, Any]:
    """`hearthward:temporal`: epsilon, deltas, lateness, skew and gap parameters (§4.4)."""
    return _load("temporal.json")


@cache
def tools() -> list[dict[str, Any]]:
    """`hearthward:tools`: the eleven MCP tool definitions served by `tools/list` (§5.3)."""
    return json.loads((SPEC_DIR / "tools.json").read_bytes())


@cache
def hx() -> list[dict[str, Any]]:
    """`hearthward:hx`: the HX invariants (§5.4)."""
    return json.loads((SPEC_DIR / "hx.json").read_bytes())
