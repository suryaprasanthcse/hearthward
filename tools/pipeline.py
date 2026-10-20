#!/usr/bin/env python3
"""HEARTHWARD gate pipeline: the repository's deterministic compiler for gates F and G0 to G7.

Each gate is a list of checks (blueprint section 10). A check reports PASS, FAIL, PENDING (what it inspects does not
exist yet), MANUAL (an attestation is missing from docs/gates/attestations.json) or WARN (advisory). A gate
passes only when every check is PASS or WARN. `--gate Gn` runs F, G0 ... Gn in order and stops at the first
gate that does not pass; `--only Gn` runs one gate.

    python tools/pipeline.py --gate F
    python tools/pipeline.py --gate G2 --keep-going
    python tools/pipeline.py --only G3 --probe
    python tools/pipeline.py --lint-contracts
    python tools/pipeline.py --list

Exit codes: 0 every requested gate passed, 1 a check failed, 2 usage error, 3 nothing failed but checks are
pending or await attestation.

Standard library only, so it runs before any dependency is installed. Once pyproject.toml exists, run it as
`uv run python tools/pipeline.py ...` so the pytest and import checks see the project's environment. It never
prints environment variable values.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orrery  # noqa: E402  (same directory: owns the blueprint parser and the reference model)

ROOT = orrery.ROOT
BLUEPRINT = orrery.BLUEPRINT
ATTESTATIONS = ROOT / "docs" / "gates" / "attestations.json"
GATES = ("F", "G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7")
MCP_PIN = "2.3.0"
PYTHON_RANGE = ">=3.12,<3.13"

PASS, FAIL, PENDING, MANUAL, WARN = "PASS", "FAIL", "PENDING", "MANUAL", "WARN"


@dataclass
class Outcome:
    status: str
    detail: str


@dataclass
class Check:
    id: str
    title: str
    run: Callable[[Context], Outcome]


class Context:
    """What every check can see: the repository, the parsed blueprint and the command-line switches."""

    def __init__(self, probe: bool) -> None:
        self.probe = probe
        self._spec: dict[str, Any] | None = None
        self.spec_error: str | None = None
        self._orrery: tuple[list[str], dict[str, orrery.Result]] | None = None

    @property
    def spec(self) -> dict[str, Any] | None:
        if self._spec is None and self.spec_error is None:
            try:
                self._spec = orrery.load_spec(BLUEPRINT)
            except orrery.SpecError as exc:
                self.spec_error = str(exc)
        return self._spec

    def orrery_runs(self) -> tuple[list[str], dict[str, orrery.Result]]:
        if self._orrery is None:
            self._orrery = orrery.selftest(self.spec)
        return self._orrery

    @staticmethod
    def attestations() -> dict[str, Any]:
        try:
            return json.loads(ATTESTATIONS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


# ── shared helpers ────────────────────────────────────────────────────────────────────────────────


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _paths(*relative: str, missing_status: str = PENDING) -> Outcome:
    missing = [p for p in relative if not (ROOT / p).exists()]
    if missing:
        return Outcome(missing_status, "missing: " + ", ".join(missing))
    return Outcome(PASS, f"{len(relative)} paths present")


def _project_env() -> dict[str, str]:
    env = dict(os.environ)
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _python_json(code: str) -> tuple[Any, str | None]:
    """Run project code in a child interpreter with src/ on the path; returns (decoded stdout, error)."""
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=_project_env(), timeout=300)
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        return None, tail
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError:
        return None, "the child process did not print JSON"


def _pytest(marker: str) -> Outcome:
    if not (ROOT / "tests").is_dir():
        return Outcome(PENDING, "no tests/ directory yet")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-m", marker, "-q", "--no-header", "-p",
                           "no:cacheprovider", "tests"], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=_project_env(), timeout=1800)
    last = (proc.stdout.strip().splitlines() or [""])[-1]
    if proc.returncode == 0:
        return Outcome(PASS, f"pytest -m {marker}: {last}")
    if proc.returncode == 5:
        return Outcome(PENDING, f"no tests marked {marker} yet")
    if "No module named pytest" in proc.stderr:
        return Outcome(FAIL, f"pytest is not importable by {sys.executable}; run through `uv run`")
    return Outcome(FAIL, f"pytest -m {marker}: {last}")


def _attested(key: str, extra: Callable[[dict[str, Any]], str | None] | None = None) -> Callable[[Context], Outcome]:
    def run(ctx: Context) -> Outcome:
        entry = ctx.attestations().get(key)
        if not isinstance(entry, dict) or not all(isinstance(entry.get(f), str) and entry[f].strip()
                                                  for f in ("by", "at", "evidence")):
            return Outcome(MANUAL, f"add {key} with by, at and evidence to {ATTESTATIONS.relative_to(ROOT)}")
        problem = extra(entry) if extra else None
        if problem:
            return Outcome(FAIL, problem)
        return Outcome(PASS, f"attested by {entry['by']} on {entry['at']}")

    return run


# ── environment variables (values are never printed) ──────────────────────────────────────────────


def _match(pattern: str, message: str) -> Callable[[str], str | None]:
    return lambda value: None if re.fullmatch(pattern, value) else message


ENV_RULES: dict[str, Callable[[str], str | None]] = {
    "HW_MCP_RESOURCE_URI": _match(r"https://[^/\s]+/mcp", "must be https://<host>/mcp with no trailing slash"),
    "HW_FACADE_ISSUER": _match(r"https://[^/\s]+", "must be https://<host> with no path or trailing slash"),
    "HW_COGNITO_ISSUER": _match(r"https://cognito-idp\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9-]+_[A-Za-z0-9]+",
                                "must be https://cognito-idp.<region>.amazonaws.com/<user-pool-id>"),
    "HW_COGNITO_DOMAIN": _match(r"https://[^/\s]+", "must be the managed-login base URL with no path"),
    "HW_ALEXA_CLIENT_ID": _match(r"[A-Za-z0-9]{8,128}", "must be the Cognito app client id"),
    "AWS_REGION": _match(r"[a-z]{2}(-[a-z]+)+-\d", "must be a region code such as us-east-1"),
    "HW_LEDGER_TABLE": _match(r"[A-Za-z0-9_.-]{3,255}", "must be a DynamoDB table name"),
    "HW_ATTEST_KEY_ARN": _match(r"arn:aws:kms:[a-z0-9-]+:\d{12}:key/[0-9a-f-]{36}", "must be a KMS key ARN"),
    "HW_BROKER_EXECUTOR_ROLE_ARN": _match(r"arn:aws:iam::\d{12}:role/[\w+=,.@/-]+", "must be an IAM role ARN"),
    "HW_WEBAUTHN_RP_ID": _match(r"[a-z0-9.-]+\.[a-z]{2,}", "must be the relying party's host name"),
    "HW_LADDER_STATE_MACHINE_ARN": _match(r"arn:aws:states:[a-z0-9-]+:\d{12}:stateMachine:[\w-]+",
                                          "must be a Step Functions state machine ARN"),
    "HW_ANCHOR_BUCKET": _match(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", "must be an S3 bucket name"),
}
GATE_ENV = {
    "G2": ["HW_MCP_RESOURCE_URI", "HW_FACADE_ISSUER", "HW_COGNITO_ISSUER", "HW_COGNITO_DOMAIN", "HW_ALEXA_CLIENT_ID"],
    "G3": ["AWS_REGION", "HW_LEDGER_TABLE", "HW_ATTEST_KEY_ARN"],
    "G5": ["HW_BROKER_EXECUTOR_ROLE_ARN", "HW_WEBAUTHN_RP_ID", "HW_LADDER_STATE_MACHINE_ARN"],
    "G6": ["HW_ANCHOR_BUCKET"],
}


def _env(gate: str) -> Callable[[Context], Outcome]:
    def run(ctx: Context) -> Outcome:
        names = GATE_ENV[gate]
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            return Outcome(PENDING, "not set: " + ", ".join(missing))
        bad = [f"{n} {problem}" for n in names if (problem := ENV_RULES[n](os.environ[n]))]
        if bad:
            return Outcome(FAIL, "; ".join(bad))
        return Outcome(PASS, f"{len(names)} variables set and well-formed")

    return run


# ── contract linter (blueprint §5) ────────────────────────────────────────────────────────────────

TOOL_KEYS = {"name", "title", "description", "inputSchema", "outputSchema", "annotations", "_meta"}
ANNOTATION_KEYS = {"title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
SCHEMA_KEYWORDS = {"type", "properties", "required", "additionalProperties", "enum", "const", "items", "minItems",
                   "maxItems", "minLength", "maxLength", "pattern", "minimum", "maximum", "description", "title",
                   "oneOf"}
FORBIDDEN_KEYS = {"execution": "MCP-07", "taskSupport": "MCP-07", "task": "MCP-07", "tasks": "MCP-07",
                  "elicitation": "MCP-08", "elicitationId": "MCP-08"}
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
PROOF_FIELDS = {"status", "challenge_id", "approve_on", "rules", "expires_at_ms", "spoken_hint"}
LINT_RULES = {
    "MCP-01": "tool name matches ^[A-Za-z0-9_.-]{1,128}$ and is unique",
    "MCP-02": "a tool has only name, title, description, inputSchema, outputSchema, annotations, _meta",
    "MCP-03": "inputSchema is an object schema with properties",
    "MCP-04": "every required name is a declared property",
    "MCP-05": "outputSchema, when present, has type object",
    "MCP-06": "annotations are the standard hints, as booleans",
    "MCP-07": "no Tasks (execution, taskSupport): a 2025-03-26 session cannot use them",
    "MCP-08": "no URL-mode elicitation: a 2025-03-26 session cannot use it",
    "MCP-09": "schemas use only the portable keyword subset",
    "MCP-10": "every object schema is closed with additionalProperties false, or is a oneOf of closed branches",
    "MCP-11": "every string and array is bounded (maxLength, enum, const, an anchored pattern, maxItems)",
    "MCP-12": "the description is 1 to 600 characters",
    "HW-01": "_meta['hearthward/class'] is S+ or S-",
    "HW-02": "S- tools are destructive and answer DISMISSED-style success, PROOF_REQUIRED or BLOCKED",
    "HW-03": "S+ tools are not destructive and never answer PROOF_REQUIRED",
    "HW-04": "integers only: no schema uses type number (C-2, P-7)",
    "HW-05": "S+ outputs require data_origin with enum [live, synthetic] (D-2)",
}


@dataclass
class Finding:
    rule: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule} {self.where}: {self.message}"


def _types(schema: dict[str, Any]) -> list[str]:
    t = schema.get("type")
    return t if isinstance(t, list) else [t] if isinstance(t, str) else []


def _bounded_pattern(pattern: str) -> bool:
    return pattern.startswith("^") and pattern.endswith("$") and not re.search(r"(?<!\\)[*+]", pattern)


def _lint_schema(schema: Any, where: str, add: Callable[[str, str], None]) -> None:
    if not isinstance(schema, dict):
        add("MCP-09", f"{where} is not a schema object")
        return
    for key in set(schema) - SCHEMA_KEYWORDS:
        add("MCP-09", f"{where} uses keyword {key!r}")
    types = _types(schema)
    if "number" in types:
        add("HW-04", f"{where} uses type number")
    if "object" in types:
        if "oneOf" in schema:
            if "additionalProperties" in schema or "properties" in schema:
                add("MCP-10", f"{where} mixes oneOf with its own properties")
            for i, branch in enumerate(schema["oneOf"]):
                _lint_schema(branch, f"{where}.oneOf[{i}]", add)
        else:
            props = schema.get("properties")
            if not isinstance(props, dict):
                add("MCP-10", f"{where} is an object schema without properties")
                props = {}
            if schema.get("additionalProperties") is not False:
                add("MCP-10", f"{where} does not set additionalProperties false")
            for name in schema.get("required", []):
                if name not in props:
                    add("MCP-04", f"{where} requires undeclared {name!r}")
            for name, sub in props.items():
                _lint_schema(sub, f"{where}.{name}", add)
    if "string" in types or ("const" in schema and isinstance(schema["const"], str)):
        if not ("maxLength" in schema or "enum" in schema or "const" in schema
                or _bounded_pattern(schema.get("pattern", ""))):
            add("MCP-11", f"{where} is an unbounded string")
    if "array" in types:
        if "maxItems" not in schema:
            add("MCP-11", f"{where} is an array without maxItems")
        _lint_schema(schema.get("items", {}), f"{where}[]", add)


def _walk_forbidden(value: Any, where: str, add: Callable[[str, str], None]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS:
                add(FORBIDDEN_KEYS[key], f"{where} uses {key!r}")
            if key == "mode" and item == "url":
                add("MCP-08", f"{where} declares URL-mode elicitation")
            _walk_forbidden(item, f"{where}.{key}", add)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _walk_forbidden(item, f"{where}[{i}]", add)


def _branch_status(branch: dict[str, Any]) -> str | None:
    status = branch.get("properties", {}).get("status", {})
    return status.get("const") if isinstance(status, dict) else None


def lint_tools(tools: Any) -> list[Finding]:
    """Findings for the `hearthward:tools` block against MCP (2025-03-26 compatible) and HEARTHWARD's rules."""
    if not isinstance(tools, list) or not tools:
        return [Finding("MCP-02", "tools", "the block must be a non-empty array")]
    findings: list[Finding] = []
    names: set[str] = set()
    for i, tool in enumerate(tools):
        where = tool.get("name", f"#{i}") if isinstance(tool, dict) else f"#{i}"

        def add(rule: str, message: str, where: str = str(where)) -> None:
            findings.append(Finding(rule, where, message))

        if not isinstance(tool, dict):
            add("MCP-02", "a tool must be an object")
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not TOOL_NAME.match(name):
            add("MCP-01", f"invalid name {name!r}")
        elif name in names:
            add("MCP-01", "duplicate name")
        names.add(str(name))
        for key in sorted(set(tool) - TOOL_KEYS):
            add("MCP-02", f"unexpected key {key!r}")
        for key in ("name", "description", "inputSchema"):
            if key not in tool:
                add("MCP-02", f"missing {key!r}")
        description = tool.get("description")
        if not isinstance(description, str) or not 1 <= len(description) <= 600:
            add("MCP-12", "description must be 1 to 600 characters")
        _walk_forbidden(tool, "tool", add)

        inp = tool.get("inputSchema")
        if not isinstance(inp, dict) or inp.get("type") != "object" or not isinstance(inp.get("properties"), dict):
            add("MCP-03", "inputSchema must be {type: object, properties: {...}}")
        else:
            _lint_schema(inp, "inputSchema", add)
        out = tool.get("outputSchema")
        if out is not None:
            if not isinstance(out, dict) or out.get("type") != "object":
                add("MCP-05", "outputSchema must have type object")
            else:
                _lint_schema(out, "outputSchema", add)

        annotations = tool.get("annotations", {})
        if not isinstance(annotations, dict):
            add("MCP-06", "annotations must be an object")
            annotations = {}
        for key, value in annotations.items():
            if key not in ANNOTATION_KEYS:
                add("MCP-06", f"unknown annotation {key!r}")
            elif key != "title" and not isinstance(value, bool):
                add("MCP-06", f"{key} must be a boolean")

        cls = tool.get("_meta", {}).get("hearthward/class") if isinstance(tool.get("_meta"), dict) else None
        branches = out.get("oneOf", []) if isinstance(out, dict) else []
        statuses = [_branch_status(b) for b in branches if isinstance(b, dict)]
        if cls not in ("S+", "S-"):
            add("HW-01", f"class {cls!r}")
        elif cls == "S-":
            if annotations.get("destructiveHint") is not True or annotations.get("readOnlyHint") is not False:
                add("HW-02", "an S- tool must set destructiveHint true and readOnlyHint false")
            proof = [b for b in branches if isinstance(b, dict) and _branch_status(b) == "PROOF_REQUIRED"]
            if not proof or not PROOF_FIELDS <= set(proof[0].get("required", [])):
                add("HW-02", f"needs a PROOF_REQUIRED branch requiring {sorted(PROOF_FIELDS)}")
            if "BLOCKED" not in statuses:
                add("HW-02", "needs a BLOCKED branch")
            if len([s for s in statuses if s not in ("PROOF_REQUIRED", "BLOCKED")]) != 1:
                add("HW-02", "needs exactly one success branch")
        else:
            if annotations.get("destructiveHint") is not False:
                add("HW-03", "an S+ tool must set destructiveHint false")
            if "PROOF_REQUIRED" in statuses:
                add("HW-03", "an S+ tool never answers PROOF_REQUIRED")
            out = out if isinstance(out, dict) else {}
            origin = out.get("properties", {}).get("data_origin", {})
            if origin.get("enum") != ["live", "synthetic"] or "data_origin" not in out.get("required", []):
                add("HW-05", "the output must require data_origin with enum [live, synthetic]")
    return findings


HX_FIELDS = {"id", "title", "applies_to", "statement", "enforced_at", "on_fail"}
HX_ON_FAIL = {"PROOF_REQUIRED", "REQUIRE_DUAL", "BLOCK", "THROTTLE"}
HX_ENFORCED_AT = {"broker", "mcp", "kernel", "broker+iam"}


def lint_hx(hx: Any, tools: list[dict[str, Any]]) -> list[str]:
    """Problems with the `hearthward:hx` block: exactly HX-01..HX-12, well-formed, covering every S- tool."""
    if not isinstance(hx, list):
        return ["the hx block must be an array"]
    problems: list[str] = []
    expected = [f"HX-{n:02d}" for n in range(1, 13)]
    ids = [r.get("id") for r in hx if isinstance(r, dict)]
    if ids != expected:
        problems.append(f"ids are {ids}, expected HX-01..HX-12 in order")
    tool_class = {t["name"]: t.get("_meta", {}).get("hearthward/class") for t in tools if isinstance(t, dict)}
    covered: set[str] = set()
    for rule in hx:
        rid = rule.get("id", "?") if isinstance(rule, dict) else "?"
        if not isinstance(rule, dict) or set(rule) != HX_FIELDS:
            problems.append(f"{rid}: fields must be exactly {sorted(HX_FIELDS)}")
            continue
        if rule["on_fail"] not in HX_ON_FAIL:
            problems.append(f"{rid}: on_fail {rule['on_fail']!r}")
        if rule["enforced_at"] not in HX_ENFORCED_AT:
            problems.append(f"{rid}: enforced_at {rule['enforced_at']!r}")
        if not isinstance(rule["applies_to"], list) or not rule["applies_to"]:
            problems.append(f"{rid}: applies_to must be a non-empty array")
            continue
        for target in rule["applies_to"]:
            if target in ("S-", "*"):
                covered |= {n for n, c in tool_class.items() if c == "S-"}
            elif target == "S+":
                continue
            elif target not in tool_class:
                problems.append(f"{rid}: applies to unknown tool {target!r}")
            else:
                covered.add(target)
    for name, cls in tool_class.items():
        if cls == "S-" and name not in covered:
            problems.append(f"S- tool {name} is not covered by any HX rule")
    return problems


def check_sprt(sprt: Any, tools: list[dict[str, Any]]) -> list[str]:
    """Problems with the `hearthward:sprt` block (blueprint §6)."""
    p: list[str] = []
    try:
        alpha, beta = sprt["alpha"], sprt["beta"]
        stages, features = sprt["stages"], sprt["features"]
        n_s, n_k = len(stages), len(features)
        if not (0 < alpha < 0.5 and 0 < beta < 0.5):
            p.append("alpha and beta must lie in (0, 0.5)")
        a, b = math.log((1 - beta) / alpha), math.log(beta / (1 - alpha))
        reported = sprt["thresholds_reported"]
        if abs(reported["A"] - a) > 5e-4 or abs(reported["B"] - b) > 5e-4:
            p.append(f"reported thresholds {reported} disagree with A = {a:.4f}, B = {b:.4f}")
        init = sprt["initial"]
        if len(init) != n_s or abs(sum(init) - 1) > 1e-9 or any(x < 0 for x in init):
            p.append("initial must be a distribution over the stages")
        trans = sprt["transitions"]
        if len(trans) != n_s or any(len(row) != n_s for row in trans):
            p.append("transitions must be stages x stages")
        else:
            for i, row in enumerate(trans):
                if abs(sum(row) - 1) > 1e-9 or any(x < 0 for x in row):
                    p.append(f"transition row {stages[i]} is not a distribution")
                if any(row[j] > 0 for j in range(i)):
                    p.append(f"transition row {stages[i]} returns to an earlier stage (the script is left-to-right)")
        emissions, h0 = sprt["emissions"], sprt["h0"]
        if len(emissions) != n_s or any(len(row) != n_k for row in emissions) or len(h0) != n_k:
            p.append("emissions must be stages x features and h0 must cover every feature")
        else:
            if any(not 0 < x < 1 for row in emissions for x in row) or any(not 0 < x < 1 for x in h0):
                p.append("every emission and h0 rate must lie strictly between 0 and 1")
            if emissions[0] != h0:
                p.append("the benign stage must emit exactly like H0, so small talk carries no evidence")
            for k, name in enumerate(features):
                if max(row[k] for row in emissions) <= h0[k]:
                    p.append(f"feature {name} never raises evidence: no stage exceeds its H0 rate")
        tpr, fpr = sprt["extractor"]["tpr"], sprt["extractor"]["fpr"]
        if len(tpr) != n_k or len(fpr) != n_k or any(not 0 < f < t < 1 for t, f in zip(tpr, fpr)):
            p.append("extractor tpr and fpr must cover every feature with 0 < fpr < tpr < 1")
        for term, value in sprt["cross_device_llr"].items():
            if value < 0 and not term.startswith("verified_"):
                p.append(f"cross-device term {term} is negative but not verified_* (DC-1)")
            if abs(value) > 5:
                p.append(f"cross-device term {term} exceeds 5 nats")
        if not isinstance(sprt["cooldown_ms"], int) or sprt["cooldown_ms"] <= 0:
            p.append("cooldown_ms must be a positive integer")
        expected = orrery.mnats(a)
        for tool in tools:
            for name, prop in tool.get("outputSchema", {}).get("properties", {}).items():
                if name == "intervene_threshold_mnats" and prop.get("const") != expected:
                    p.append(f"{tool['name']}.intervene_threshold_mnats is {prop.get('const')}, A is {expected} mnats")
    except (KeyError, TypeError) as exc:
        p.append(f"malformed block: {exc!r}")
    return p


def check_temporal(t: Any) -> list[str]:
    p: list[str] = []
    try:
        schedule = t["ring_retry_schedule_ms"]
        if schedule != [1000, 5000, 30000, 120000, 600000, 3600000]:
            p.append("ring_retry_schedule_ms must match Ring's documented schedule")
        if t["lateness_ms"]["ring"] < sum(schedule):
            p.append(f"Ring lateness {t['lateness_ms']['ring']} is shorter than its retry window {sum(schedule)}")
        if t["max_skew_ms"] != 60_000:
            p.append("max_skew_ms must equal GNOMON's MAX_SKEW_MS (60000)")
        for path, value in (("epsilon_ring_ms", t["epsilon_ring_ms"]), ("delta_ms.alexa", t["delta_ms"]["alexa"]),
                            ("delta_ms.bee", t["delta_ms"]["bee"]), ("visit_gap_ms", t["visit_gap_ms"]),
                            ("bee_sync_interval_ms", t["bee_sync_interval_ms"]),
                            ("lateness_ms.bee", t["lateness_ms"]["bee"]),
                            ("lateness_ms.alexa", t["lateness_ms"]["alexa"])):
            if not isinstance(value, int) or value <= 0:
                p.append(f"{path} must be a positive integer")
    except (KeyError, TypeError) as exc:
        p.append(f"malformed block: {exc!r}")
    return p


# ── a JSON Schema subset validator, for envelopes ─────────────────────────────────────────────────


def _is_type(value: Any, name: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(name, False)


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Errors from validating `value` against the portable keyword subset (SCHEMA_KEYWORDS)."""
    errors: list[str] = []
    if "oneOf" in schema:
        matches = sum(1 for branch in schema["oneOf"] if not validate(value, branch, path))
        if matches != 1:
            errors.append(f"{path}: matches {matches} oneOf branches")
    types = _types(schema)
    if types and not any(_is_type(value, t) for t in types):
        return errors + [f"{path}: expected {'/'.join(types)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            errors.append(f"{path}: length {len(value)} is out of bounds")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, int) and not isinstance(value, bool):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            errors.append(f"{path}: {value} is out of bounds")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            errors.append(f"{path}: {len(value)} items is out of bounds")
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items", {}), f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing {name!r}")
        for name, item in value.items():
            if name in props:
                errors += validate(item, props[name], f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected {name!r}")
    return errors


# ── gate F: the foundation ────────────────────────────────────────────────────────────────────────


def _need_spec(ctx: Context) -> Outcome | None:
    if ctx.spec is None:
        return Outcome(FAIL, ctx.spec_error or "the blueprint did not parse")
    return None


def f_files(ctx: Context) -> Outcome:
    return _paths("docs/master_blueprint.md", "tools/pipeline.py", "tools/orrery.py", ".gitattributes",
                  missing_status=FAIL)


def f_gitattributes(ctx: Context) -> Outcome:
    probes = ["fixtures/ring/sample.json", "fixtures/bee/stream.jsonl", "fixtures/cassettes/x.txt", "any/where.json",
              "any/where.jsonl"]
    proc = _git("check-attr", "text", "--", *probes)
    if proc.returncode != 0:
        return Outcome(FAIL, f"git check-attr failed: {proc.stderr.strip()}")
    wrong = [line for line in proc.stdout.splitlines() if not line.endswith(": text: unset")]
    if wrong:
        return Outcome(FAIL, "EOL conversion is still possible for: " + "; ".join(wrong))
    return Outcome(PASS, "fixtures/**, *.json and *.jsonl have text unset (no CRLF rewrites)")


def f_blocks(ctx: Context) -> Outcome:
    if (bad := _need_spec(ctx)) is not None:
        return bad
    expected = {"envelope", "temporal", "tools", "hx", "sprt"}
    missing = expected - set(ctx.spec)
    extra = set(ctx.spec) - expected
    if missing:
        return Outcome(FAIL, f"missing blocks: {sorted(missing)}")
    return Outcome(PASS, f"{len(expected)} blocks parse" + (f"; unrecognized: {sorted(extra)}" if extra else ""))


def f_contracts(ctx: Context) -> Outcome:
    if (bad := _need_spec(ctx)) is not None:
        return bad
    findings = lint_tools(ctx.spec.get("tools"))
    if findings:
        return Outcome(FAIL, f"{len(findings)} findings; first: {findings[0]}")
    tools = ctx.spec["tools"]
    split = {c: sum(1 for t in tools if t["_meta"]["hearthward/class"] == c) for c in ("S+", "S-")}
    return Outcome(PASS, f"{len(tools)} tools ({split['S+']} S+, {split['S-']} S-), "
                         f"{len(LINT_RULES)} rules, 0 findings")


def f_hx(ctx: Context) -> Outcome:
    if (bad := _need_spec(ctx)) is not None:
        return bad
    problems = lint_hx(ctx.spec.get("hx"), ctx.spec.get("tools", []))
    if problems:
        return Outcome(FAIL, f"{len(problems)} problems; first: {problems[0]}")
    return Outcome(PASS, "HX-01..HX-12 well-formed; every S- tool covered")


def f_sprt(ctx: Context) -> Outcome:
    if (bad := _need_spec(ctx)) is not None:
        return bad
    problems = check_sprt(ctx.spec.get("sprt"), ctx.spec.get("tools", []))
    if problems:
        return Outcome(FAIL, f"{len(problems)} problems; first: {problems[0]}")
    model = orrery.Model.from_spec(ctx.spec["sprt"])
    return Outcome(PASS, f"A = {model.A:+.4f}, B = {model.B:+.4f}; stochastic, left-to-right, benign = H0, "
                         "negative terms verified only")


def f_temporal(ctx: Context) -> Outcome:
    if (bad := _need_spec(ctx)) is not None:
        return bad
    problems = check_temporal(ctx.spec.get("temporal"))
    if problems:
        return Outcome(FAIL, f"{len(problems)} problems; first: {problems[0]}")
    return Outcome(PASS, "Ring lateness covers its retry window; max_skew matches GNOMON")


def f_envelope_schema(ctx: Context) -> Outcome:
    if (bad := _need_spec(ctx)) is not None:
        return bad
    findings: list[str] = []

    def walk(schema: Any, where: str) -> None:
        if not isinstance(schema, dict):
            findings.append(f"{where} is not a schema")
            return
        findings.extend(f"{where} uses {k!r}" for k in set(schema) - SCHEMA_KEYWORDS)
        for name, sub in schema.get("properties", {}).items():
            walk(sub, f"{where}.{name}")

    walk(ctx.spec["envelope"], "envelope")
    if findings:
        return Outcome(FAIL, "; ".join(findings))
    return Outcome(PASS, "envelope schema uses the portable keyword subset")


def f_orrery(ctx: Context) -> Outcome:
    if (bad := _need_spec(ctx)) is not None:
        return bad
    problems, results = ctx.orrery_runs()
    if problems:
        return Outcome(FAIL, f"{len(problems)} problems; first: {problems[0]}")
    envelopes = [e for r in results.values() for e in r.envelopes]
    errors = [err for e in envelopes for err in validate(e, ctx.spec["envelope"], f"seq {e['seq']}")]
    if errors:
        return Outcome(FAIL, f"{len(errors)} envelope errors; first: {errors[0]}")
    return Outcome(PASS, f"{len(results)} scenarios deterministic and as specified; {len(envelopes)} envelopes "
                         "valid; chains intact")


def f_gate_table(ctx: Context) -> Outcome:
    text = BLUEPRINT.read_text(encoding="utf-8")
    missing = [g for g in GATES if not re.search(rf"^\| {g} \|", text, re.M)]
    if missing:
        return Outcome(FAIL, f"blueprint section 10 has no row for {missing}")
    return Outcome(PASS, "every gate has a row in blueprint section 10")


SECRET_PATTERNS = [
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key block", re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
    ("AWS secret access key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+]{40}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("client secret value", re.compile(r"(?i)client_secret['\"]?\s*[:=]\s*['\"][A-Za-z0-9._~+/=-]{24,}['\"]")),
]


def f_secrets(ctx: Context) -> Outcome:
    proc = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if proc.returncode != 0:
        return Outcome(FAIL, f"git ls-files failed: {proc.stderr.strip()}")
    hits = []
    files = [f for f in proc.stdout.split("\0") if f]
    for name in files:
        path = ROOT / name
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > 2_000_000 or b"\0" in data[:8000]:
            continue
        text = data.decode("utf-8", errors="ignore")
        hits += [f"{name}: {label}" for label, pattern in SECRET_PATTERNS if pattern.search(text)]
    if hits:
        return Outcome(FAIL, "possible secrets: " + "; ".join(hits))
    return Outcome(PASS, f"{len(files)} tracked or new files scanned, no secrets")


# ── gate G0: environment and pins ─────────────────────────────────────────────────────────────────


def g0_pyproject(ctx: Context) -> Outcome:
    path = ROOT / "pyproject.toml"
    if not path.exists():
        return Outcome(PENDING, "pyproject.toml does not exist yet")
    try:
        project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
    except tomllib.TOMLDecodeError as exc:
        return Outcome(FAIL, f"pyproject.toml: {exc}")
    problems = []
    if project.get("requires-python") != PYTHON_RANGE:
        problems.append(f"requires-python must be {PYTHON_RANGE!r}")
    deps = [re.sub(r"\s+", "", d) for d in project.get("dependencies", [])]
    if f"mcp=={MCP_PIN}" not in deps:
        problems.append(f"dependencies must pin mcp=={MCP_PIN} exactly")
    if problems:
        return Outcome(FAIL, "; ".join(problems))
    return Outcome(PASS, f"requires-python {PYTHON_RANGE}; mcp=={MCP_PIN}")


def g0_python_version(ctx: Context) -> Outcome:
    path = ROOT / ".python-version"
    if not path.exists():
        return Outcome(PENDING, ".python-version does not exist yet")
    value = path.read_text(encoding="utf-8").strip()
    return Outcome(PASS, f".python-version {value}") if value.startswith("3.12") else Outcome(
        FAIL, f".python-version is {value!r}, expected 3.12.x")


def g0_lockfile(ctx: Context) -> Outcome:
    path = ROOT / "uv.lock"
    if not path.exists():
        return Outcome(PENDING, "uv.lock does not exist yet (run `uv lock`)")
    try:
        lock = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return Outcome(FAIL, f"uv.lock: {exc}")
    versions = [p.get("version") for p in lock.get("package", []) if p.get("name") == "mcp"]
    if versions != [MCP_PIN]:
        return Outcome(FAIL, f"uv.lock resolves mcp to {versions}, expected [{MCP_PIN!r}]")
    tracked = _git("ls-files", "--error-unmatch", "uv.lock").returncode == 0
    return Outcome(PASS if tracked else FAIL, f"uv.lock pins mcp {MCP_PIN}" + ("" if tracked else ", but is not committed"))


def g0_env_example(ctx: Context) -> Outcome:
    path = ROOT / ".env.example"
    if not path.exists():
        return Outcome(PENDING, ".env.example does not exist yet")
    names, filled = set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            names.add(name.strip())
            if value.strip() and not re.fullmatch(r"<[^>]*>", value.strip()):
                filled.append(name.strip())
    missing = sorted(set(ENV_RULES) - names)
    if missing:
        return Outcome(FAIL, "missing from .env.example: " + ", ".join(missing))
    if filled:
        return Outcome(FAIL, "values must stay empty or <placeholder> in .env.example: " + ", ".join(filled))
    return Outcome(PASS, f"{len(ENV_RULES)} gate variables named, no values")


def g0_interpreter(ctx: Context) -> Outcome:
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info[:2] == (3, 12):
        return Outcome(PASS, f"running on Python {version}")
    return Outcome(WARN, f"running on Python {version}; project code targets 3.12, so run gates G1+ through `uv run`")


# ── gates G1 to G7 ────────────────────────────────────────────────────────────────────────────────


def g1_fixtures(ctx: Context) -> Outcome:
    fixtures = ROOT / "fixtures"
    if not fixtures.is_dir():
        return Outcome(PENDING, "fixtures/ does not exist yet")
    proc = _git("ls-files", "-z", "fixtures")
    files = [f for f in proc.stdout.split("\0") if f]
    if not files:
        return Outcome(PENDING, "no committed fixtures yet")
    changed = []
    for name in files:
        blob = subprocess.run(["git", "cat-file", "blob", f"HEAD:{name}"], cwd=ROOT, capture_output=True)
        if blob.returncode != 0:
            changed.append(f"{name} (not in HEAD)")
        elif (ROOT / name).read_bytes() != blob.stdout:
            changed.append(name)
    attrs = _git("check-attr", "text", "--", *files).stdout.splitlines()
    converted = [line.split(":")[0] for line in attrs if not line.endswith(": text: unset")]
    if changed or converted:
        return Outcome(FAIL, "not byte-identical to HEAD: " + ", ".join(changed + converted))
    return Outcome(PASS, f"{len(files)} fixtures byte-identical to HEAD, text unset")


def g2_schema_drift(ctx: Context) -> Outcome:
    if not (ROOT / "src/hearthward/mcp/schemas.py").exists():
        return Outcome(PENDING, "src/hearthward/mcp/schemas.py does not exist yet")
    if (bad := _need_spec(ctx)) is not None:
        return bad
    served, error = _python_json("import json; from hearthward.mcp.schemas import tool_definitions; "
                                 "print(json.dumps(tool_definitions()))")
    if error:
        return Outcome(FAIL, f"tool_definitions() failed: {error}")

    def by_name(tools: list[dict[str, Any]]) -> str:
        return json.dumps(sorted(tools, key=lambda t: t["name"]), sort_keys=True)

    if by_name(served) != by_name(ctx.spec["tools"]):
        names = {t["name"] for t in served} ^ {t["name"] for t in ctx.spec["tools"]}
        return Outcome(FAIL, "served tool schemas differ from hearthward:tools" + (f" (names {sorted(names)})"
                                                                                  if names else ""))
    return Outcome(PASS, f"{len(served)} served tool schemas identical to the blueprint")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _http(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str],
                                                                                                 bytes]:
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def g3_probe(ctx: Context) -> Outcome:
    if not ctx.probe:
        return Outcome(PENDING, "run with --probe to check the deployed endpoints")
    resource, issuer = os.environ.get("HW_MCP_RESOURCE_URI"), os.environ.get("HW_FACADE_ISSUER")
    if not resource or not issuer:
        return Outcome(PENDING, "HW_MCP_RESOURCE_URI and HW_FACADE_ISSUER must be set")
    problems = []
    try:
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(resource))
        status, _, body = _http(origin + "/.well-known/oauth-protected-resource")
        meta = json.loads(body) if status == 200 else {}
        if meta.get("resource") != resource:
            problems.append(f"protected-resource metadata: resource is {meta.get('resource')!r} (HTTP {status})")
        if (meta.get("authorization_servers") or [None])[0] != issuer:
            problems.append("authorization_servers[0] is not the facade issuer")
        status, _, body = _http(issuer + "/.well-known/oauth-authorization-server")
        auth = json.loads(body) if status == 200 else {}
        if auth.get("issuer") != issuer:
            problems.append(f"facade metadata issuer mismatch (HTTP {status})")
        if "S256" not in auth.get("code_challenge_methods_supported", []):
            problems.append("facade metadata does not advertise S256")
        if not {"authorization_code", "refresh_token"} <= set(auth.get("grant_types_supported", [])):
            problems.append("facade metadata lacks authorization_code or refresh_token")
        call = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "explain_last_alert", "arguments": {}}}).encode()
        status, headers, _ = _http(resource, call, {"Content-Type": "application/json",
                                                    "Accept": "application/json, text/event-stream",
                                                    "MCP-Protocol-Version": "2025-03-26"})
        if 300 <= status < 400:
            problems.append(f"/mcp redirects (HTTP {status}); Alexa+ needs the exact resource URI (P-1)")
        elif status not in (401, 403):
            problems.append(f"tools/call without a token returned HTTP {status}, expected 401 or 403")
        if any(k.lower() == "www-authenticate" for k in headers):
            problems.append("the 401 carries WWW-Authenticate, which Alexa+ does not support (P-5)")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        problems.append(f"probe failed: {exc}")
    if problems:
        return Outcome(FAIL, "; ".join(problems))
    return Outcome(PASS, "metadata documents agree, S256 advertised, /mcp answers 401 without WWW-Authenticate")


def g4_submodule(ctx: Context) -> Outcome:
    gitmodules = ROOT / ".gitmodules"
    if not gitmodules.exists() or "path = vendor/gnomon" not in gitmodules.read_text(encoding="utf-8"):
        return Outcome(PENDING, "vendor/gnomon is not a submodule yet")
    line = _git("submodule", "status", "vendor/gnomon").stdout.strip("\n")
    if not line:
        return Outcome(FAIL, "git submodule status printed nothing for vendor/gnomon")
    if line[0] == "-":
        return Outcome(FAIL, "vendor/gnomon is not initialized (git submodule update --init)")
    if line[0] in "+U":
        return Outcome(FAIL, "vendor/gnomon is not at the recorded commit")
    return Outcome(PASS, f"vendor/gnomon pinned at {line.split()[0][:12]}")


def g4_conformance(ctx: Context) -> Outcome:
    if not (ROOT / "src/hearthward/decision/hmm.py").exists():
        return Outcome(PENDING, "src/hearthward/decision/hmm.py does not exist yet")
    if (bad := _need_spec(ctx)) is not None:
        return bad
    vectors = json.dumps(orrery.CONFORMANCE_VECTORS)
    params = json.dumps(ctx.spec["sprt"])
    got, error = _python_json("import json; from hearthward.decision.hmm import lambda_trace_mnats; "
                              f"v = json.loads({vectors!r}); p = json.loads({params!r}); "
                              "print(json.dumps([lambda_trace_mnats(x, p) for x in v]))")
    if error:
        return Outcome(FAIL, f"lambda_trace_mnats failed: {error}")
    want = [orrery.reference_trace_mnats(v, ctx.spec["sprt"]) for v in orrery.CONFORMANCE_VECTORS]
    if got != want:
        first = next(i for i, (g, w) in enumerate(zip(got, want)) if g != w) if len(got) == len(want) else 0
        return Outcome(FAIL, f"vector {first}: got {got[first] if got else None}, reference {want[first]}")
    return Outcome(PASS, f"{len(want)} conformance vectors match the orrery to the milli-nat")


def g5_hx_parity(ctx: Context) -> Outcome:
    if not (ROOT / "src/hearthward/authz/hx_rules.py").exists():
        return Outcome(PENDING, "src/hearthward/authz/hx_rules.py does not exist yet")
    if (bad := _need_spec(ctx)) is not None:
        return bad
    got, error = _python_json("import json; from hearthward.authz.hx_rules import RULE_IDS; "
                              "print(json.dumps(list(RULE_IDS)))")
    if error:
        return Outcome(FAIL, f"RULE_IDS failed: {error}")
    want = [r["id"] for r in ctx.spec["hx"]]
    return Outcome(PASS, "RULE_IDS equals HX-01..HX-12") if got == want else Outcome(
        FAIL, f"RULE_IDS is {got}, the blueprint has {want}")


def _states(definition: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for name, state in definition.get("States", {}).items():
        yield name, state
        for nested in ("ItemProcessor", "Iterator"):
            if isinstance(state.get(nested), dict):
                yield from _states(state[nested])
        for branch in state.get("Branches", []):
            yield from _states(branch)


def g5_ladder(ctx: Context) -> Outcome:
    path = ROOT / "src/hearthward/ladder/intervention_ladder.asl.json"
    if not path.exists():
        return Outcome(PENDING, f"{path.relative_to(ROOT)} does not exist yet")
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return Outcome(FAIL, f"invalid JSON: {exc}")
    if definition.get("StartAt") not in definition.get("States", {}):
        return Outcome(FAIL, "StartAt does not name a state")
    waits = [(n, s) for n, s in _states(definition)
             if s.get("Type") == "Task" and str(s.get("Resource", "")).endswith(".waitForTaskToken")]
    if not waits:
        return Outcome(FAIL, "no .waitForTaskToken task: the ladder cannot wait for a caregiver")
    unbounded = [n for n, s in waits if not isinstance(s.get("TimeoutSeconds"), int)
                 or not isinstance(s.get("HeartbeatSeconds"), int)]
    if unbounded:
        return Outcome(FAIL, "callback tasks without TimeoutSeconds and HeartbeatSeconds: " + ", ".join(unbounded))
    return Outcome(PASS, f"{len(waits)} callback tasks, all bounded by timeouts and heartbeats")


def g6_verifier(ctx: Context) -> Outcome:
    path = ROOT / "tools/verify_attestation.py"
    if not path.exists():
        return Outcome(PENDING, "tools/verify_attestation.py does not exist yet")
    proc = subprocess.run([sys.executable, str(path), "--selftest"], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=_project_env(), timeout=300)
    last = (proc.stdout.strip().splitlines() or proc.stderr.strip().splitlines() or [""])[-1]
    return Outcome(PASS if proc.returncode == 0 else FAIL, f"verify_attestation --selftest: {last}")


def g6_web_lock(ctx: Context) -> Outcome:
    if not (ROOT / "web/package.json").exists():
        return Outcome(PENDING, "web/package.json does not exist yet")
    if not (ROOT / "web/package-lock.json").exists():
        return Outcome(FAIL, "web/package-lock.json is missing; commit the lockfile")
    tracked = _git("ls-files", "--error-unmatch", "web/package-lock.json").returncode == 0
    return Outcome(PASS if tracked else FAIL, "web lockfile " + ("committed" if tracked else "not committed"))


README_SECTIONS = ("Setup", "Run", "Tracks", "Built during the submission window", "AI assistance", "Synthetic data")


def g7_readme(ctx: Context) -> Outcome:
    path = ROOT / "README.md"
    if not path.exists():
        return Outcome(PENDING, "README.md does not exist yet")
    text = path.read_text(encoding="utf-8")
    headings = [h.strip().lower() for h in re.findall(r"^#{2,3}\s+(.+)$", text, re.M)]
    missing = [s for s in README_SECTIONS if not any(h.startswith(s.lower()) for h in headings)]
    problems = [f"missing sections: {missing}"] if missing else []
    if not re.search(r"synthetic", text, re.I) or "Ring" not in text or "Bee" not in text:
        problems.append("the README must say that Ring and Bee data are synthetic (D-1)")
    if problems:
        return Outcome(FAIL, "; ".join(problems))
    return Outcome(PASS, f"{len(README_SECTIONS)} required sections and the synthetic-data disclosure")


def g7_license(ctx: Context) -> Outcome:
    path = ROOT / "LICENSE"
    if not path.exists():
        return Outcome(PENDING, "LICENSE does not exist yet")
    head = path.read_text(encoding="utf-8", errors="ignore")[:400]
    known = ("MIT License", "Apache License", "BSD 3-Clause", "BSD 2-Clause", "Mozilla Public License")
    found = next((k for k in known if k in head), None)
    return Outcome(PASS, found) if found else Outcome(FAIL, "LICENSE is not a recognized OSI license")


def g7_docs(ctx: Context) -> Outcome:
    short = [p for p in ("docs/product-feedback.md", "docs/friction-log.md")
             if not (ROOT / p).exists() or (ROOT / p).stat().st_size < 200]
    if short:
        return Outcome(PENDING, "missing or under 200 bytes: " + ", ".join(short))
    return Outcome(PASS, "product feedback and friction log written")


def _video(entry: dict[str, Any]) -> str | None:
    if not re.match(r"https://(www\.)?(youtube\.com|youtu\.be|vimeo\.com)/", entry["evidence"]):
        return "G7.demo_video evidence must be a public YouTube or Vimeo URL"
    if not isinstance(entry.get("duration_s"), int) or entry["duration_s"] >= 180:
        return "G7.demo_video needs duration_s under 180"
    return None


# ── the gate table ────────────────────────────────────────────────────────────────────────────────

TITLES = {
    "F": "Foundation", "G0": "Environment and pins", "G1": "Contracts and ingest", "G2": "MCP server and facade",
    "G3": "Deployed and linked", "G4": "Causal engine and decision core", "G5": "Broker and ladder",
    "G6": "Integrity and web", "G7": "Submission",
}

CHECKS: dict[str, list[Check]] = {
    "F": [
        Check("F.files", "foundation files present", f_files),
        Check("F.gitattributes", "fixtures and JSON are byte-exact", f_gitattributes),
        Check("F.blocks", "blueprint spec blocks parse", f_blocks),
        Check("F.contracts", "tool contract lint", f_contracts),
        Check("F.hx", "HX invariant table", f_hx),
        Check("F.sprt", "SPRT and HMM consistency", f_sprt),
        Check("F.temporal", "temporal parameters", f_temporal),
        Check("F.envelope", "envelope schema", f_envelope_schema),
        Check("F.orrery", "orrery self-test and envelope validation", f_orrery),
        Check("F.gates", "gate table documented", f_gate_table),
        Check("F.secrets", "secret scan", f_secrets),
    ],
    "G0": [
        Check("G0.pyproject", "requires-python and the mcp pin", g0_pyproject),
        Check("G0.python_version", ".python-version", g0_python_version),
        Check("G0.lockfile", "uv.lock pins mcp", g0_lockfile),
        Check("G0.env_example", ".env.example names every gate variable", g0_env_example),
        Check("G0.interpreter", "interpreter version", g0_interpreter),
        Check("G0.alexa_console", "Alexa+ add-on and simulator access", _attested("G0.alexa_console")),
        Check("G0.bedrock_access", "Bedrock model access", _attested("G0.bedrock_access")),
        Check("G0.cognito_tier", "Cognito Essentials with managed login", _attested("G0.cognito_tier")),
        Check("G0.track_posture", "track posture and synthetic disclosure", _attested("G0.track_posture")),
    ],
    "G1": [
        Check("G1.modules", "contracts and ingest modules", lambda ctx: _paths(
            "src/hearthward/contracts/envelope.py", "src/hearthward/contracts/canonical.py",
            "src/hearthward/ingest/ring_webhook.py", "src/hearthward/ingest/bee_bridge.py",
            "src/hearthward/ingest/ledger.py")),
        Check("G1.fixtures", "fixtures byte-identical to HEAD", g1_fixtures),
        Check("G1.tests", "pytest -m gate_g1", lambda ctx: _pytest("gate_g1")),
    ],
    "G2": [
        Check("G2.modules", "MCP server and facade modules", lambda ctx: _paths(
            "src/hearthward/mcp/app.py", "src/hearthward/mcp/auth.py", "src/hearthward/mcp/wellknown.py",
            "src/hearthward/mcp/tools_splus.py", "src/hearthward/mcp/tools_sminus.py",
            "src/hearthward/mcp/schemas.py", "src/hearthward/authz/facade_as.py")),
        Check("G2.env", "gate variables", _env("G2")),
        Check("G2.schema_drift", "served schemas equal the blueprint", g2_schema_drift),
        Check("G2.tests", "pytest -m gate_g2", lambda ctx: _pytest("gate_g2")),
        Check("G2.alexa_discovery_summary", "alexa-ai discovery shows S256",
              _attested("G2.alexa_discovery_summary")),
    ],
    "G3": [
        Check("G3.infra", "CDK app", lambda ctx: _paths("infra/app.py", "infra/cdk.json", "infra/cdk.context.json")),
        Check("G3.env", "gate variables", _env("G3")),
        Check("G3.probe", "deployed endpoints (--probe)", g3_probe),
        Check("G3.simulator_linking", "account linking in the simulator", _attested("G3.simulator_linking")),
    ],
    "G4": [
        Check("G4.submodule", "GNOMON submodule pinned", g4_submodule),
        Check("G4.modules", "causal and decision modules", lambda ctx: _paths(
            "src/hearthward/causal/port.py", "src/hearthward/causal/gnomon_adapter.py",
            "src/hearthward/causal/intervals.py", "src/hearthward/decision/sprt.py",
            "src/hearthward/decision/hmm.py", "src/hearthward/decision/tactics.py")),
        Check("G4.conformance", "decision core equals the orrery", g4_conformance),
        Check("G4.tests", "pytest -m gate_g4", lambda ctx: _pytest("gate_g4")),
    ],
    "G5": [
        Check("G5.modules", "broker modules", lambda ctx: _paths(
            "src/hearthward/authz/broker.py", "src/hearthward/authz/hx_rules.py",
            "src/hearthward/authz/webauthn_rp.py", "src/hearthward/ladder/intervention_ladder.asl.json")),
        Check("G5.env", "gate variables", _env("G5")),
        Check("G5.hx_parity", "RULE_IDS equals the HX block", g5_hx_parity),
        Check("G5.ladder", "ladder callback tasks bounded", g5_ladder),
        Check("G5.tests", "pytest -m gate_g5", lambda ctx: _pytest("gate_g5")),
        Check("G5.cloudtrail_proof_hash", "CloudTrail shows proof_hash", _attested("G5.cloudtrail_proof_hash")),
    ],
    "G6": [
        Check("G6.modules", "integrity modules", lambda ctx: _paths(
            "src/hearthward/attest/sign.py", "src/hearthward/attest/anchor.py", "tools/verify_attestation.py")),
        Check("G6.env", "gate variables", _env("G6")),
        Check("G6.verifier", "verify_attestation self-test", g6_verifier),
        Check("G6.web_lock", "web lockfile committed", g6_web_lock),
        Check("G6.tests", "pytest -m gate_g6", lambda ctx: _pytest("gate_g6")),
    ],
    "G7": [
        Check("G7.readme", "README sections and disclosure", g7_readme),
        Check("G7.license", "OSI license", g7_license),
        Check("G7.docs", "product feedback and friction log", g7_docs),
        Check("G7.demo_video", "public demo video under 3 minutes", _attested("G7.demo_video", _video)),
        Check("G7.devpost_submitted", "Devpost submission", _attested("G7.devpost_submitted")),
    ],
}


# ── running and reporting ─────────────────────────────────────────────────────────────────────────


def run_gate(gate: str, ctx: Context) -> tuple[str, list[tuple[Check, Outcome]]]:
    results = []
    for check in CHECKS[gate]:
        try:
            outcome = check.run(ctx)
        except Exception as exc:  # a crashing check is a failed check, never a crashed pipeline
            outcome = Outcome(FAIL, f"check crashed: {exc!r}")
        results.append((check, outcome))
    statuses = {o.status for _, o in results}
    status = FAIL if FAIL in statuses else "INCOMPLETE" if statuses & {PENDING, MANUAL} else PASS
    return status, results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HEARTHWARD gate pipeline (blueprint section 10).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gate", choices=GATES, help="run F through this gate, in order")
    mode.add_argument("--only", choices=GATES, help="run one gate")
    mode.add_argument("--lint-contracts", action="store_true", help="lint the tool, HX and SPRT blocks and list "
                                                                    "every finding")
    mode.add_argument("--list", action="store_true", help="list gates and checks")
    parser.add_argument("--keep-going", action="store_true", help="continue past a gate that does not pass")
    parser.add_argument("--probe", action="store_true", help="let G3 call the deployed endpoints")
    parser.add_argument("--json", action="store_true", help="print one JSON report")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")
    ctx = Context(args.probe)

    if args.list:
        for gate in GATES:
            print(f"{gate:<3} {TITLES[gate]}")
            for check in CHECKS[gate]:
                print(f"      {check.id:<28} {check.title}")
        return 0

    if args.lint_contracts:
        if ctx.spec is None:
            print(f"blueprint: {ctx.spec_error}")
            return 1
        findings = [str(f) for f in lint_tools(ctx.spec.get("tools"))]
        findings += [f"HX {p}" for p in lint_hx(ctx.spec.get("hx"), ctx.spec.get("tools", []))]
        findings += [f"SPRT {p}" for p in check_sprt(ctx.spec.get("sprt"), ctx.spec.get("tools", []))]
        for line in findings:
            print(line)
        print(f"{len(findings)} findings across {len(ctx.spec.get('tools', []))} tools ({len(LINT_RULES)} tool rules)")
        return 1 if findings else 0

    gates = [args.only] if args.only else list(GATES[: GATES.index(args.gate or "F") + 1])
    report = []
    for gate in gates:
        status, results = run_gate(gate, ctx)
        report.append({"gate": gate, "title": TITLES[gate], "status": status,
                       "checks": [{"id": c.id, "title": c.title, "status": o.status, "detail": o.detail}
                                  for c, o in results]})
        if not args.json:
            print(f"== {gate} | {TITLES[gate]} ==")
            for check, outcome in results:
                print(f"  {outcome.status:<8} {check.id:<28} {outcome.detail}")
            print(f"gate {gate}: {status}\n")
        if status != PASS and not args.keep_going:
            break
    statuses = [g["status"] for g in report]
    code = 1 if FAIL in statuses else 3 if "INCOMPLETE" in statuses else 0
    if args.json:
        print(json.dumps({"gates": report, "exit": code}, indent=2))
    else:
        ran = ", ".join(f"{g['gate']} {g['status']}" for g in report)
        skipped = [g for g in gates if g not in {r["gate"] for r in report}]
        print(f"summary: {ran}" + (f" (stopped; not run: {', '.join(skipped)})" if skipped else ""))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
