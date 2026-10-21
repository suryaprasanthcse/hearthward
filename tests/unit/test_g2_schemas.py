"""Gate G2: the served tool schemas are the blueprint's, and the contract rules P-4, P-7, P-8, HW-02 and HW-05 hold."""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator

from hearthward.mcp.schemas import SMINUS, SPLUS, definitions_by_name, tool_class, tool_definitions

pytestmark = pytest.mark.gate_g2

SPLUS_TOOLS = ["get_door_activity", "assess_visitor", "verify_organization_contact", "explain_last_alert",
               "raise_concern", "call_trusted_contact"]
SMINUS_TOOLS = ["dismiss_alert", "pause_monitoring", "add_trusted_vendor", "edit_trusted_circle", "share_evidence"]


def walk(schema: Any):
    """Every sub-schema (dicts that carry a `type`, `oneOf`, `properties` or `items`)."""
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from walk(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from walk(item)


def test_served_definitions_equal_the_blueprint_block(spec: dict[str, Any]) -> None:
    def key(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(tools, key=lambda t: t["name"])

    assert key(tool_definitions()) == key(spec["tools"])


def test_eleven_tools_six_splus_five_sminus() -> None:
    by_name = definitions_by_name()
    assert sorted(by_name) == sorted(SPLUS_TOOLS + SMINUS_TOOLS)
    assert [n for n, d in by_name.items() if tool_class(d) == SPLUS] == SPLUS_TOOLS
    assert [n for n, d in by_name.items() if tool_class(d) == SMINUS] == SMINUS_TOOLS


def test_definitions_are_fresh_copies() -> None:
    first = tool_definitions()
    first[0]["name"] = "tampered"
    assert tool_definitions()[0]["name"] != "tampered"


@pytest.mark.parametrize("name", SPLUS_TOOLS)
def test_every_splus_output_requires_data_origin_live_or_synthetic(name: str) -> None:
    """HW-05."""
    output = definitions_by_name()[name]["outputSchema"]
    assert "data_origin" in output["required"]
    assert output["properties"]["data_origin"] == {"type": "string", "enum": ["live", "synthetic"]}


@pytest.mark.parametrize("name", SMINUS_TOOLS)
def test_every_sminus_output_has_proof_required_and_blocked_branches(name: str) -> None:
    """HW-02."""
    branches = definitions_by_name()[name]["outputSchema"]["oneOf"]
    statuses = {b["properties"]["status"]["const"] for b in branches}
    assert {"PROOF_REQUIRED", "BLOCKED"} <= statuses
    assert len(statuses) == 3


@pytest.mark.parametrize("name", sorted(definitions_by_name()))
def test_schemas_are_valid_closed_bounded_and_integer_only(name: str) -> None:
    definition = definitions_by_name()[name]
    for part in ("inputSchema", "outputSchema"):
        Draft202012Validator.check_schema(definition[part])
        for node in walk(definition[part]):
            assert node.get("type") != "number", "P-7: integers only"
            if node.get("type") == "object" and "oneOf" not in node:
                assert node.get("additionalProperties") is False, "P-8: closed objects"
            if node.get("type") == "string" and "enum" not in node and "const" not in node:
                assert "maxLength" in node or "pattern" in node, "P-8: bounded strings"


@pytest.mark.parametrize("name", sorted(definitions_by_name()))
def test_no_task_or_elicitation_keys(name: str) -> None:
    """P-3."""
    text = str(definitions_by_name()[name])
    for forbidden in ("execution", "taskSupport", "elicitation"):
        assert forbidden not in text
