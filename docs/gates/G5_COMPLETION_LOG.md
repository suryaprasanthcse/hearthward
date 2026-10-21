# G5 completion log

Gate G5 (broker, invariant parity, intervention ladder) executed by the main session (Claude Sonnet 5.5). **G5 is
INCOMPLETE (exit 3), and only because of `G5.cloudtrail_proof_hash`**, a human attestation that needs a deployment.
The five checks the brief names all pass.

- **Starting state:** branch `main`, tree clean, HEAD `3b95867`; F, G0, G1, G2, G4 PASS; G3 INCOMPLETE (its
  deployment probe and simulator attestation are yours).
- **Result:** `G5.modules`, `G5.env`, `G5.hx_parity`, `G5.ladder`, `G5.tests` PASS; `G5.cloudtrail_proof_hash` MANUAL.

## Commits created

| Hash | Message |
|---|---|
| `9ac700081424dc36f3d1ebd754e0f4fed62248cc` | feat(G5): AXIOM broker, HX invariants parity, and Step Functions ladder |
| (this commit) | docs(G5): completion log and state snapshot |

Gate F was run (PASS) before the feat commit. Paths were staged explicitly. No push, remote, amend, rebase or force.

## Tests (`uv run pytest -m gate_g5`)

| File | Passed |
|---|---|
| `tests/unit/test_broker.py` | 97 |
| `tests/unit/test_ladder.py` | 26 |
| **Total** | **123 passed, 0 failed, 0 skipped, 0 xfailed** |

Full suite: 844 tests (360 gate_g1, 191 gate_g2, 55 gate_g3, 115 gate_g4, 123 gate_g5), all passing. No network is
used: STS is a botocore `Stubber` with exact expected parameters, DynamoDB runs under `moto`, Step Functions under
`Stubber`, and the passkeys are software authenticators (`tests/g5_support.py: SoftPasskey`).

What the tests pin down:

- **The rule table** equals the `hearthward:hx` block field by field (`id`, `title`, `applies_to`, `enforced_at`,
  `on_fail`), applicability matches the `applies_to` column for all 11 tools, and each of HX-01 to HX-12 has passing
  and failing cases with their boundaries (cooldown, 24 h, 6 an hour, 3 per episode, 1 to 24 hours).
- **The relying party** accepts a good assertion and refuses a wrong challenge (HX-03), wrong origin, wrong RP ID,
  missing user presence or verification, a wrong key, tampered bytes, duplicate JSON keys, oversize input and a
  counter that did not increase (HX-04).
- **The flow**: a challenge changes nothing; one valid assertion applies exactly one change; replays, wrong-action
  assertions, strangers, forged keys, expired and unknown challenges, a changed episode and a failed mint all end in
  BLOCKED or PROOF_REQUIRED with nothing written; dual control needs two *distinct* members; the person being
  removed cannot approve their own removal (HX-07); evidence does not leave without consent (HX-12).
- **Through the MCP tool**: an Alexa token alone gets a challenge for every S- tool and never a change.
- **The executor**: the exact `assume_role` call (role, `DurationSeconds` 900, session policy, `proof_hash` tag) and a
  full broker-to-DynamoDB path over moto.
- **The ladder**: structure, reachability, routing evaluated for ACK, ESCALATE and unknown signals, the pipeline's own
  check against weakened copies of the definition, and the Step Functions calls.

## `uv run python tools/pipeline.py --only G5` (exit 3)

With `HW_BROKER_EXECUTOR_ROLE_ARN=arn:aws:iam::123456789012:role/hearthward-broker-executor`,
`HW_WEBAUTHN_RP_ID=approve.hearthward.example` and
`HW_LADDER_STATE_MACHINE_ARN=arn:aws:states:us-east-1:123456789012:stateMachine:InterventionLadder` (synthetic).

```
== G5 | Broker and ladder ==
  PASS     G5.modules                   4 paths present
  PASS     G5.env                       3 variables set and well-formed
  PASS     G5.hx_parity                 RULE_IDS equals HX-01..HX-12
  PASS     G5.ladder                    1 callback tasks, all bounded by timeouts and heartbeats
  PASS     G5.tests                     pytest -m gate_g5: 123 passed, 721 deselected in 13.82s
  MANUAL   G5.cloudtrail_proof_hash     add G5.cloudtrail_proof_hash with by, at and evidence to docs\gates\attestations.json
gate G5: INCOMPLETE

summary: G5 INCOMPLETE
```

## `uv run python tools/pipeline.py --gate G5 --keep-going` (exit 3), G2 onward

All eight synthetic variables for G2, G3 and G5 set. F, G0 and G1 were PASS in the same run (F.secrets: 100 files
scanned, no secrets; G1.tests: 360 passed).

```
== G2 | MCP server and facade ==
  PASS     G2.modules                   7 paths present
  PASS     G2.env                       5 variables set and well-formed
  PASS     G2.schema_drift              11 served tool schemas identical to the blueprint
  PASS     G2.tests                     pytest -m gate_g2: 191 passed, 653 deselected in 11.46s
  PASS     G2.alexa_discovery_summary   attested by Surya Prasanth R on 2026-10-21
gate G2: PASS

== G3 | Deployed and linked ==
  PASS     G3.infra                     3 paths present
  PASS     G3.env                       3 variables set and well-formed
  PENDING  G3.probe                     run with --probe to check the deployed endpoints
  MANUAL   G3.simulator_linking         add G3.simulator_linking with by, at and evidence to docs\gates\attestations.json
gate G3: INCOMPLETE

== G4 | Causal engine and decision core ==
  PASS     G4.submodule                 vendor/gnomon pinned at 78c8d2e13c3b
  PASS     G4.modules                   6 paths present
  PASS     G4.conformance               6 conformance vectors match the orrery to the milli-nat
  PASS     G4.tests                     pytest -m gate_g4: 115 passed, 729 deselected in 10.53s
gate G4: PASS

== G5 | Broker and ladder ==
  PASS     G5.modules                   4 paths present
  PASS     G5.env                       3 variables set and well-formed
  PASS     G5.hx_parity                 RULE_IDS equals HX-01..HX-12
  PASS     G5.ladder                    1 callback tasks, all bounded by timeouts and heartbeats
  PASS     G5.tests                     pytest -m gate_g5: 123 passed, 721 deselected in 13.67s
  MANUAL   G5.cloudtrail_proof_hash     add G5.cloudtrail_proof_hash with by, at and evidence to docs\gates\attestations.json
gate G5: INCOMPLETE

summary: F PASS, G0 PASS, G1 PASS, G2 PASS, G3 INCOMPLETE, G4 PASS, G5 INCOMPLETE
```

## Why G5 cannot pass yet

`G5.cloudtrail_proof_hash` is an attestation (hard rule 5): after a real S- action, CloudTrail shows the DynamoDB
write made under the executor session carrying the `proof_hash` session tag. That needs the deployed stack, a real
executor role, a registered passkey and a CloudTrail trail, none of which exist yet (see "Not built" below). The
attestation is yours to write once it is true.

## What was built

| Module | Role |
|---|---|
| `authz/hx_rules.py` | `Rule` table, `RULE_IDS`, `Facts` and `Request`, `evaluate(request, facts, phase)` returning a `Verdict` |
| `authz/webauthn_rp.py` | `RelyingParty.verify`, `Credential`, `Assertion`, `WebAuthnError(rule, code)`, `InMemoryCredentialStore` |
| `authz/proof_stores.py` | `NonceStore` and `ProofLedger` protocols; in-memory and DynamoDB versions with conditional writes |
| `authz/broker.py` | `BrokerConfig`, `Broker` (`challenge`, `approve`), `action_hash`, `plan_for`, `session_policy`, `StsExecutor`, `dynamodb_writer`, `RuleSimulator` |
| `ladder/intervention_ladder.asl.json` | the `InterventionLadder` definition |
| `ladder/signals.py` | `start_ladder`, `send_signal`, `escalate`, `acknowledge`, `heartbeat` |
| `mcp/tools_sminus.py` (changed) | keeps a broker's BLOCKED rules; with a broker present, an open episode is the broker's decision |

How a request moves: `mcp` calls `Broker.challenge`, which reads the facts, applies the action in a counterfactual
branch, runs the declare-phase rules, and either returns BLOCKED or stores a challenge and returns PROOF_REQUIRED.
The approval page calls `Broker.approve` with an assertion; the relying party checks it against the action hash, the
counter is advanced by a conditional write, the facts are read again (so a world that changed since the challenge is
caught), distinct members accumulate until HX-01 and HX-09 are met, the nonce is consumed by a conditional write, and
only then does `StsExecutor` mint the session and write. Every failure on that path is a refusal (HX-11).

## Deviations and architectural notes

1. **Three evaluation phases.** The brief says "table-driven checks"; the table runs in `declare`, `authorize` and
   `final` phases so that HX-02 to HX-04 are not "failed" before any proof exists, and so the nonce is consumed only
   after everything else passed. HX-04's counter and nonce halves are checked at `authorize` and `final`.
2. **A real bug found by the DynamoDB tests.** `consumed` is a DynamoDB reserved word; the first conditional update
   failed under moto. The stores now use `ExpressionAttributeNames`.
3. **IAM cannot pin the sort key.** The session policy limits `dynamodb:UpdateItem` to the household's partition
   (`dynamodb:LeadingKeys`) and to the key attributes plus the attributes of this one change. The exact sort key is
   enforced by the plan the broker writes, not by IAM. moto does not evaluate IAM, so the session policy is tested
   for its content and for the exact `assume_role` call, not for enforcement.
4. **Passkey counters.** HX-04 says the signature counter must increase, and the relying party refuses a counter that
   does not. Several platform passkeys (synced ones in particular) report a constant 0, and those can never approve
   anything. That follows the blueprint, but it is a real compatibility risk to test with real devices before the
   demo; relaxing it is a decision for the blueprint, not for code.
5. **Credential storage and registration** are in memory. There is no registration ceremony, no table for credentials,
   and no mapping of members to credentials outside the tests.
6. **Facts and simulation are stand-ins.** `FactsProvider` (episode state, circle, cooldown, counts, consent, recent
   name claims, dependency health) has no real implementation, and `RuleSimulator` derives the HX-09 diff from those
   facts instead of running a GNOMON `GRAPH.COPY` branch. With no provider wired, nothing deployed can approve.
7. **The deployed app still has no broker.** `create_app_from_env` passes none, so every S- call in a deployment
   returns BLOCKED (HX-11), which is the safe state.
8. **HX-05 and HX-10 exist twice.** They are enforced at `mcp` by the G2 S+ code, which has its own constants and
   does not call `hx_rules`. The table's versions are tested against the same boundaries, but the two copies are not
   linked; point the S+ tools at the table when the tools get wired to real facts.
9. **Challenge `approve_on`** is always `caregiver_phone`. The elder's phone is never a proof origin.
10. **Results of `approve`** go to the approval page, not to Alexa, which only ever sees PROOF_REQUIRED or BLOCKED.
11. **Ladder wiring.** The definition uses `${...}` placeholders for five Lambda functions (notify the caregiver web
    view, notify the elder's phone, check the episode, notify a circle member, conclude the ladder). None of them is
    written, and the CDK stack has no state machine, broker Lambda, executor role or web API yet. The task token is
    delivered to the circle-member Lambda; whoever holds it must heartbeat inside 30 s and call `signals.escalate` or
    `signals.acknowledge`. Circle items must carry an `id`.
12. **Ladder routing rule.** Any signal other than ACK, including a malformed one, is treated as ESCALATE, and an
    unreadable episode check does not silence the ladder: both fail toward more protection. If the conclusion step
    fails the ladder ends as `L3Deferred`, not as resolved.
13. **L3 and the Map's order.** L3 (voice call) is deferred as specified. The Map runs one member at a time
    (`MaxConcurrency` 1) in the order of the `circle` array, so the caller must pass the circle in priority order.

## Open items

- **Human:** attest `G5.cloudtrail_proof_hash` after a real S- action on a deployed stack; finish G3 (deploy,
  `--probe`, simulator attestation).
- Build and deploy: the broker Lambda and web approval page, the executor role (with `aws:RequestTag/proof_hash`
  conditions), the state machine with its five Lambdas, a passkey registration flow and credential storage, and a
  real `FactsProvider`; add these to the CDK stack and CloudTrail data events for `HearthwardSecurityState`.
- Test platform passkeys' counters on real devices (deviation 4).
- Wire `decision.sprt.Accumulator`, the `CausalStore` and the broker's facts together (episode lifecycle §6.7).
