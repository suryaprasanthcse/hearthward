"""Tool definitions served by `tools/list`: the blueprint's `hearthward:tools` block, as exported to JSON."""

from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator

from hearthward.contracts import specs

CLASS_KEY = "hearthward/class"
SPLUS = "S+"
SMINUS = "S-"


def tool_definitions() -> list[dict[str, Any]]:
    """A fresh copy of the eleven tool definitions, exactly as the blueprint declares them (P-4, P-7, P-8)."""
    return copy.deepcopy(specs.tools())


def tool_class(definition: dict[str, Any]) -> str:
    return definition["_meta"][CLASS_KEY]


def definitions_by_name() -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in tool_definitions()}


def input_validator(definition: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(definition["inputSchema"])


def output_validator(definition: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(definition["outputSchema"])
