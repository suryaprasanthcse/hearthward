"""The broker: proof-carrying S- calls (blueprint §5.5), in the AXIOM pattern.

    declare   mcp -> `Broker.challenge`: apply the action in a counterfactual branch (HX-09), run the rules that need
              no proof, then either return BLOCKED or mint a challenge
    challenge nonce (128 bits), exp = now + 300 000 ms, action_hash = SHA-256("hw-action-v1" || 0x00 || JCS({tool,
              args, household, episode_version, nonce, exp})); stored in `Nonces`; the circle is asked to approve
    approve   the web page calls `Broker.approve` with a WebAuthn assertion whose challenge is the action hash;
              distinct members accumulate until the rules (HX-01, HX-09) are satisfied
    mint      the nonce is consumed by a conditional write, `sts:AssumeRole` mints a 900 s executor session with a
              session policy scoped to the household's SecurityState item and the attributes of this one change,
              tagged `proof_hash`; the change is applied and `ProofLedger` records it

Everything is injected: facts, the simulator, stores, the relying party, the executor and the clock. With nothing
reachable the broker blocks (HX-11); it has no path that approves without a verified assertion.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from hearthward.authz import hx_rules
from hearthward.authz.hx_rules import Facts, Request, Verdict
from hearthward.authz.proof_stores import NonceStore, ProofLedger
from hearthward.authz.webauthn_rp import Assertion, CredentialStore, RelyingParty, WebAuthnError
from hearthward.contracts.canonical import tagged_digest
from hearthward.mcp.runtime import mint_id, normalize_name
from hearthward.mcp.schemas import SMINUS, definitions_by_name, input_validator, tool_class
from hearthward.mcp.tools_sminus import HINTS, blocked

ACTION_TAG = b"hw-action-v1"
PROOF_TAG = b"hw-proof-v1"
CHALLENGE_TTL_MS = 300_000
SESSION_SECONDS = 900  # the STS minimum
BROKER_RULES_AT = frozenset({"broker", "kernel", "broker+iam"})
ROLE_ARN = re.compile(r"arn:aws:iam::(\d{12}):role/[\w+=,.@/-]+")


@dataclass(frozen=True)
class BrokerConfig:
    executor_role_arn: str
    rp_id: str
    ladder_state_machine_arn: str
    region: str = "us-east-1"
    security_state_table: str = "HearthwardSecurityState"
    challenge_ttl_ms: int = CHALLENGE_TTL_MS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BrokerConfig:
        names = {"executor_role_arn": "HW_BROKER_EXECUTOR_ROLE_ARN", "rp_id": "HW_WEBAUTHN_RP_ID",
                 "ladder_state_machine_arn": "HW_LADDER_STATE_MACHINE_ARN"}
        missing = [var for var in names.values() if not env.get(var)]
        if missing:
            raise RuntimeError("not set: " + ", ".join(missing))
        extra = {"region": env["AWS_REGION"]} if env.get("AWS_REGION") else {}
        return cls(**{field: env[var] for field, var in names.items()}, **extra)

    @property
    def security_state_arn(self) -> str:
        account = ROLE_ARN.fullmatch(self.executor_role_arn)
        if account is None:
            raise ValueError("executor_role_arn is not an IAM role ARN")
        return f"arn:aws:dynamodb:{self.region}:{account.group(1)}:table/{self.security_state_table}"

    @property
    def origins(self) -> tuple[str, ...]:
        return (f"https://{self.rp_id}",)


# ── ports ───────────────────────────────────────────────────────────────────────────────────────────────


class FactsProvider(Protocol):
    def facts(self, household: str, tool: str, args: Mapping[str, Any]) -> Facts:
        """Current facts for the household; proof and counterfactual fields stay at their defaults."""


@dataclass(frozen=True)
class Diff:
    closes_episode: bool = False
    removes_evidence_source: bool = False


class Simulator(Protocol):
    def simulate(self, household: str, tool: str, args: Mapping[str, Any], facts: Facts) -> Diff:
        """Apply the action in a counterfactual branch of the household graph and diff it (HX-09)."""


class RuleSimulator:
    """A pure stand-in for the GNOMON `GRAPH.COPY` branch: derives the diff from the facts alone."""

    def simulate(self, household: str, tool: str, args: Mapping[str, Any], facts: Facts) -> Diff:
        if tool == "dismiss_alert":
            return Diff(closes_episode=facts.open_episode_id is not None and args.get("episode_id")
                        == facts.open_episode_id)
        if tool == "pause_monitoring":
            return Diff(removes_evidence_source=bool(facts.evidence_sources))
        return Diff()


class Notifier(Protocol):
    def push_approval(self, household: str, challenge_id: str, expires_at_ms: int) -> None: ...

    def circle_changed(self, household: str, summary: str) -> None: ...


@dataclass(frozen=True)
class Plan:
    """What the executor writes: one SecurityState item, the attributes it sets and the tool's success answer."""

    key: Mapping[str, str]
    attributes: Mapping[str, str | int | bool]
    result: Mapping[str, Any]


def plan_for(tool: str, args: Mapping[str, Any], household: str, now_ms: int,
             entropy: Callable[[int], bytes] = secrets.token_bytes) -> Plan:
    if tool == "dismiss_alert":
        return Plan({"pk": household, "sk": f"EPISODE#{args['episode_id']}"},
                    {"state": "dismissed", "dismiss_reason": args["reason"], "dismissed_at_ms": now_ms},
                    {"status": "DISMISSED", "episode_id": args["episode_id"]})
    if tool == "pause_monitoring":
        until = now_ms + args["hours"] * hx_rules.HOUR_MS
        return Plan({"pk": household, "sk": "PAUSE"}, {"until_ms": until, "reason": args["reason"]},
                    {"status": "PAUSED", "until_ms": until})
    if tool == "add_trusted_vendor":
        vendor_id = mint_id("ven", now_ms, entropy)
        return Plan({"pk": household, "sk": f"VENDOR#{vendor_id}"},
                    {"name": args["name"], "phone_e164": args["phone_e164"], "category": args["category"],
                     "verified": True, "added_at_ms": now_ms},
                    {"status": "ADDED", "vendor_id": vendor_id})
    if tool == "edit_trusted_circle":
        attrs: dict[str, str | int | bool] = {"operation": args["operation"], "display_name": args["display_name"],
                                              "role": args["role"], "updated_at_ms": now_ms}
        if "phone_e164" in args:
            attrs["phone_e164"] = args["phone_e164"]
        return Plan({"pk": household, "sk": f"CIRCLE#{normalize_name(args['display_name'])}"}, attrs,
                    {"status": "UPDATED"})
    if tool == "share_evidence":
        share_id = mint_id("shr", now_ms, entropy)
        return Plan({"pk": household, "sk": f"SHARE#{share_id}"},
                    {"episode_id": args["episode_id"], "recipient": args["recipient"], "shared_at_ms": now_ms},
                    {"status": "SHARED", "share_id": share_id})
    raise ValueError(f"not an S- tool: {tool!r}")


# ── minting the executor session ────────────────────────────────────────────────────────────────────────────


def session_policy(table_arn: str, household: str, plan: Plan) -> str:
    """The session policy: UpdateItem on the SecurityState table, only for this household's partition and only for
    the key attributes and the attributes of this change. (IAM cannot pin the sort key; `plan.key` does.)"""
    attributes = sorted({"pk", "sk", *plan.attributes})
    policy = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": ["dynamodb:UpdateItem"], "Resource": table_arn,
        "Condition": {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": [household],
                                                    "dynamodb:Attributes": attributes}}}]}
    return json.dumps(policy, sort_keys=True, separators=(",", ":"))


Writer = Callable[[Mapping[str, str], Plan], None]


def dynamodb_writer(region: str, table: str) -> Writer:
    """Apply a plan with the minted session credentials (a DynamoDB client built from them)."""
    import boto3

    def write(credentials: Mapping[str, str], plan: Plan) -> None:
        client = boto3.client("dynamodb", region_name=region, aws_access_key_id=credentials["AccessKeyId"],
                              aws_secret_access_key=credentials["SecretAccessKey"],
                              aws_session_token=credentials["SessionToken"])
        names: dict[str, str] = {}
        values: dict[str, dict[str, Any]] = {}
        sets: list[str] = []
        for i, (name, value) in enumerate(sorted(plan.attributes.items())):
            names[f"#a{i}"] = name
            sets.append(f"#a{i} = :v{i}")
            if isinstance(value, bool):
                values[f":v{i}"] = {"BOOL": value}
            elif isinstance(value, int):
                values[f":v{i}"] = {"N": str(value)}
            else:
                values[f":v{i}"] = {"S": value}
        client.update_item(TableName=table, Key={k: {"S": v} for k, v in plan.key.items()},
                           UpdateExpression="SET " + ", ".join(sets), ExpressionAttributeNames=names,
                           ExpressionAttributeValues=values)

    return write


class Executor(Protocol):
    def apply(self, household: str, plan: Plan, proof_hash: str) -> None: ...


class StsExecutor:
    """Mints a 900 s session on the executor role, tagged `proof_hash`, and writes through it. The `mcp` role can
    never write SecurityState (TB-5); only this session can."""

    def __init__(self, sts: Any, config: BrokerConfig, writer: Writer) -> None:
        self.sts, self.config, self.writer = sts, config, writer

    def apply(self, household: str, plan: Plan, proof_hash: str) -> None:
        reply = self.sts.assume_role(
            RoleArn=self.config.executor_role_arn, RoleSessionName="hw-" + proof_hash.split(":", 1)[1][:32],
            DurationSeconds=SESSION_SECONDS, Policy=session_policy(self.config.security_state_arn, household, plan),
            Tags=[{"Key": "proof_hash", "Value": proof_hash}])
        self.writer(reply["Credentials"], plan)


# ── the broker ──────────────────────────────────────────────────────────────────────────────────────────


def action_hash(tool: str, args: Mapping[str, Any], household: str, episode_version: int, nonce: str, exp: int
                ) -> bytes:
    """SHA-256("hw-action-v1" || 0x00 || JCS({tool, args, household, episode_version, nonce, exp}))."""
    digest = tagged_digest(ACTION_TAG, {"tool": tool, "args": dict(args), "household": household,
                                        "episode_version": episode_version, "nonce": nonce, "exp": exp})
    return bytes.fromhex(digest.split(":", 1)[1])


class Broker:
    """Implements `hearthward.mcp.runtime.Broker` (`challenge`) plus the approval half (`approve`)."""

    def __init__(self, *, config: BrokerConfig, facts: FactsProvider, nonces: NonceStore,
                 credentials: CredentialStore, relying_party: RelyingParty, executor: Executor,
                 proof_ledger: ProofLedger, simulator: Simulator | None = None, notifier: Notifier | None = None,
                 clock: Callable[[], int] | None = None, entropy: Callable[[int], bytes] = secrets.token_bytes) -> None:
        self.config, self.facts_provider, self.nonces = config, facts, nonces
        self.credentials, self.rp, self.executor, self.proof_ledger = credentials, relying_party, executor, proof_ledger
        self.simulator, self.notifier = simulator or RuleSimulator(), notifier
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.entropy = entropy
        self.definitions = definitions_by_name()
        self.inputs = {name: input_validator(d) for name, d in self.definitions.items()}

    # -- declare and challenge ---------------------------------------------------------------------------------

    def _facts(self, household: str, tool: str, args: Mapping[str, Any]) -> tuple[Facts, Request]:
        base = self.facts_provider.facts(household, tool, args)
        base = replace(base, now_ms=self.clock())
        diff = self.simulator.simulate(household, tool, args, base)
        facts = replace(base, closes_episode=diff.closes_episode, removes_evidence_source=diff.removes_evidence_source)
        return facts, Request(tool, dict(args), household, facts.episode_version)

    def _refuse(self, tool: str, verdict: Verdict) -> dict[str, Any]:
        return blocked(tool, tuple(verdict.ids(verdict.stops)) or ("HX-11",))

    def challenge(self, household: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Declare, decide, challenge. Returns BLOCKED or PROOF_REQUIRED; never a success."""
        definition = self.definitions.get(tool)
        if definition is None or tool_class(definition) != SMINUS:
            raise ValueError(f"the broker only handles S- tools, not {tool!r}")
        if not self.inputs[tool].is_valid(args):
            return blocked(tool, ("HX-02", "HX-11"))
        try:
            facts, request = self._facts(household, tool, args)
        except Exception:  # noqa: BLE001 - HX-11: if the facts cannot be read, nothing is approved
            return blocked(tool, ("HX-02", "HX-11"))
        verdict = hx_rules.evaluate(request, facts, "declare")
        if verdict.stops:
            return self._refuse(tool, verdict)
        now = self.clock()
        nonce, exp = self.entropy(16).hex(), now + self.config.challenge_ttl_ms
        challenge_id = mint_id("chl", now, self.entropy)
        digest = action_hash(tool, args, household, request.episode_version, nonce, exp)
        required = 2 if verdict.needs_dual else 1
        rules = sorted({r.id for r in hx_rules.applicable(tool) if r.enforced_at in BROKER_RULES_AT})
        record = {"tool": tool, "args": dict(args), "household": household,
                  "episode_version": request.episode_version, "nonce": nonce, "exp": exp,
                  "action_hash": digest.hex(), "required": required, "rules": rules}
        try:
            self.nonces.put(challenge_id, record)
        except Exception:  # noqa: BLE001 - HX-11: no nonce store, no challenge
            return blocked(tool, ("HX-02", "HX-04", "HX-11"))
        if self.notifier is not None:
            self.notifier.push_approval(household, challenge_id, exp)
        return self._waiting(tool, challenge_id, rules, exp)

    @staticmethod
    def _waiting(tool: str, challenge_id: str, rules: list[str], exp: int) -> dict[str, Any]:
        return {"status": "PROOF_REQUIRED", "challenge_id": challenge_id, "approve_on": "caregiver_phone",
                "rules": rules, "expires_at_ms": exp, "spoken_hint": HINTS[tool]}

    # -- approve and mint ------------------------------------------------------------------------------------------

    def approve(self, challenge_id: str, assertion: Assertion) -> dict[str, Any]:
        """One member's assertion for one challenge. Returns PROOF_REQUIRED while more proof is needed, BLOCKED when
        a rule stops the action, and the tool's success answer once the change has been applied."""
        try:
            record = self.nonces.get(challenge_id)
        except Exception:  # noqa: BLE001
            return blocked("dismiss_alert", ("HX-02", "HX-04", "HX-11"))
        if record is None:
            return blocked("dismiss_alert", ("HX-04",))
        tool, household, args = record["tool"], record["household"], record["args"]
        now = self.clock()
        if record["consumed"] or now >= record["exp"]:
            return blocked(tool, ("HX-04",))
        credential = self.credentials.get(assertion.credential_id)
        if credential is None:
            return self._waiting(tool, challenge_id, record["rules"], record["exp"])
        try:
            verified = self.rp.verify(assertion, expected_challenge=bytes.fromhex(record["action_hash"]),
                                      credential=credential)
        except WebAuthnError as exc:
            if exc.rule == "HX-02":
                return self._waiting(tool, challenge_id, record["rules"], record["exp"])
            return blocked(tool, (exc.rule,))
        if not self.credentials.advance_counter(credential.credential_id, verified.sign_count):
            return blocked(tool, ("HX-04",))
        try:
            facts, request = self._facts(household, tool, args)
        except Exception:  # noqa: BLE001
            return blocked(tool, ("HX-02", "HX-11"))
        if request.episode_version != record["episode_version"]:
            return blocked(tool, ("HX-03",))  # the world changed after the action hash was bound
        if verified.member_id in facts.member_names:
            self.nonces.add_approval(challenge_id, verified.member_id)
        latest = self.nonces.get(challenge_id) or record
        approvers = frozenset(latest["approvals"]) & frozenset(facts.member_names)
        facts = replace(facts, assertion_member=verified.member_id, challenge_matches=True, counter_increased=True,
                        approvers=approvers)
        verdict = hx_rules.evaluate(request, facts, "authorize")
        if verdict.stops:
            return self._refuse(tool, verdict)
        if not verdict.ok:
            return self._waiting(tool, challenge_id, record["rules"], record["exp"])
        if not self.nonces.consume(challenge_id):
            return blocked(tool, ("HX-04",))
        facts = replace(facts, nonce_consumed=True)
        verdict = hx_rules.evaluate(request, facts, "final")
        if not verdict.ok:  # unreachable unless the rules and the flow disagree; refuse rather than guess
            return self._refuse(tool, verdict)
        return self._mint(request, record, verified, sorted(approvers), now)

    def _mint(self, request: Request, record: Mapping[str, Any], verified: Any, approvers: list[str], now: int
              ) -> dict[str, Any]:
        tool, household = request.tool, request.household
        plan = plan_for(tool, request.args, household, now, self.entropy)
        proof_hash = tagged_digest(PROOF_TAG, {"action_hash": record["action_hash"], "approvers": approvers,
                                               "counter": verified.sign_count,
                                               "credential": verified.credential_id})
        try:
            self.executor.apply(household, plan, proof_hash)
            self.proof_ledger.record(household, proof_hash, {
                "tool": tool, "action_hash": record["action_hash"], "approvers": approvers, "at_ms": now,
                "sk": dict(plan.key)["sk"]})
        except Exception:  # noqa: BLE001 - HX-11: a failed mint is a refusal; the nonce stays spent
            return blocked(tool, ("HX-02", "HX-11"))
        if tool == "edit_trusted_circle" and self.notifier is not None:
            self.notifier.circle_changed(household, f"{request.args['operation']} {request.args['display_name']}")
        return {**plan.result, "proof_hash": proof_hash}
