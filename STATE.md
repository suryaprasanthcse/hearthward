# HEARTHWARD state

Snapshot for the next session. Details: `docs/gates/G1_COMPLETION_LOG.md` through `G5_COMPLETION_LOG.md`.

## HEAD

Branch `main`. Latest code commit: `9ac7000` (`feat(G5): AXIOM broker, HX invariants parity, and Step Functions
ladder`), followed by the docs commit that adds the G5 log and this file. Before it: `3b95867` (G4 PASS docs),
`d0eae7b` (the human's `vendor/gnomon` submodule, pinned at `78c8d2e`), `ac5d770` (G4 code), `6decd1e` and `c4253a1`
(G3), `273b995` (G2 PASS). Nothing is pushed (no remotes).

## Gates

| Gate | Status | Notes |
|---|---|---|
| F | PASS | run before every commit: `uv run python tools/pipeline.py --only F` |
| G0 | PASS | human attestations recorded in `2f5ad88` |
| G1 | PASS | 360 tests |
| G2 | PASS | 191 tests; alexa discovery summary attested (`99a0dbb`) |
| G3 | INCOMPLETE (exit 3) | `G3.infra`, `G3.env` PASS; `G3.probe` needs a deployment and `--probe`; `G3.simulator_linking` is yours; 55 tests |
| G4 | PASS | all 4 checks; 115 tests; `vendor/gnomon` pinned at `78c8d2e13c3b` |
| G5 | INCOMPLETE (exit 3) | `G5.modules`, `G5.env`, `G5.hx_parity`, `G5.ladder`, `G5.tests` PASS (123 tests); `G5.cloudtrail_proof_hash` is your attestation and needs a deployment |
| G6+ | not started | |

Full suite: 844 tests. Gate variables: five for G2, three for G3, three for G5 (`HW_BROKER_EXECUTOR_ROLE_ARN`,
`HW_WEBAUTHN_RP_ID`, `HW_LADDER_STATE_MACHINE_ARN`); synthetic values are in `tests/g2_support.py`,
`tests/g5_support.py` and the G3 and G5 logs.

## G5 deliverables

- `authz/hx_rules.py`: HX-01 to HX-12 as a rule table (`RULE_IDS`, pinned to the `hearthward:hx` block), evaluated in
  `declare`, `authorize` and `final` phases.
- `authz/webauthn_rp.py`: own relying party; challenge must equal the action hash; ES256; strictly increasing counter.
- `authz/broker.py` and `authz/proof_stores.py`: declare, counterfactual diff, challenge, distinct-member approvals,
  single-use nonce by conditional write, 900 s STS executor session tagged `proof_hash`, `ProofLedger` record.
- `ladder/`: `intervention_ladder.asl.json` (L1 parallel, L2 sequential Map of `.waitForTaskToken` tasks at 90 s /
  30 s heartbeat, L3 deferred) and `signals.py` (`start_ladder`, `escalate`, `acknowledge`, `heartbeat`).
- Nothing is deployed or wired: the app has no broker (so S- always returns BLOCKED), there is no `FactsProvider`,
  no passkey registration or credential table, no ladder Lambdas and no state machine in the CDK stack.

## Key decisions

- Table keys are `pk`/`sk` strings (what `ingest/ledger.py` writes), not `household`/`seq`; see the G3 log, note 1.
- S- tools return BLOCKED without a broker; only a valid broker challenge becomes PROOF_REQUIRED, and a broker's own
  BLOCKED keeps its rules. With a broker present an open episode is its decision (dual control), not a flat refusal.
- The decision core only makes dismissal *eligible* at Lambda <= B; watermarks and `certainly_*` decide (BT-A).
- Everything the MCP server serves is `data_origin: synthetic` and scored by the G2 stand-in, not the SPRT. Do not
  deploy with real data.
- HX-04 refuses a passkey counter that does not increase, so passkeys that always report 0 cannot approve (G5 log,
  deviation 4).
- Tests build a fresh MCP app per client (`tests/g2_support.py: Stack`); the SDK session manager runs once per app.

## Open items

- Human: attest `G5.cloudtrail_proof_hash`; deploy and `--probe` G3, attest `G3.simulator_linking`.
- Build and deploy the broker Lambda, approval page, executor role, ladder state machine and its five Lambdas, passkey
  registration and a real `FactsProvider`; add them to the CDK stack with CloudTrail data events.
- Test real platform passkeys' counters; decide whether HX-04 stays strict.
- Wire `decision.sprt.Accumulator`, a `CausalStore` and the broker's facts together (episode lifecycle §6.7); bind
  `GnomonBackend` to the real GNOMON graph (read `vendor/gnomon` first).
- G3 deploy-time checks: Cognito and a repeated `resource`; the `aud`/scope Cognito issues; the Host header API
  Gateway forwards; PKCE enforcement. Pin the Dockerfile dependencies; source the Alexa service client digest.
- §6.8 calibration of the HMM priors on a labeled replay corpus.
- Still open from G1: the spec questions in `docs/gates/BLOCKERS.md` (SQ-1 to SQ-5).
