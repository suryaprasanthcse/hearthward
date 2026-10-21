# HEARTHWARD state

Snapshot for the next session. Details: `docs/gates/G1_COMPLETION_LOG.md`, `G2_COMPLETION_LOG.md`,
`G3_COMPLETION_LOG.md`.

## HEAD

Branch `main`. Latest code commit: `c4253a1` (`feat(G3): CDK infrastructure stack, topology definitions, and endpoint
probe suite`), followed by the docs commit that adds the G3 log and this file. Before it: `273b995` (G2 PASS),
`99a0dbb` (G2 attestation), `813e6d6` (G2 code). Nothing is pushed (no remotes).

## Gates

| Gate | Status | Notes |
|---|---|---|
| F | PASS | run before every commit: `uv run python tools/pipeline.py --only F` |
| G0 | PASS | human attestations recorded in `2f5ad88` |
| G1 | PASS | 360 tests |
| G2 | PASS | 191 tests; alexa discovery summary attested (`99a0dbb`) |
| G3 | INCOMPLETE (exit 3) | `G3.infra` and `G3.env` PASS; `G3.probe` PENDING (needs a deployment and `--probe`); `G3.simulator_linking` MANUAL; 55 tests |
| G4+ | not started | |

Full suite: 606 tests. G2 needs the five variables from `.env.example`; G3 adds `AWS_REGION`, `HW_LEDGER_TABLE` and
`HW_ATTEST_KEY_ARN`. Synthetic G2 values are in `tests/g2_support.py`; the G3 ones are in the G3 log.

## G3 deliverables

- `infra/` is the CDK app (`cd infra && uv run python app.py` synthesizes to `infra/cdk.out`, no AWS calls; the CDK CLI
  is not installed here). Stack: four DynamoDB tables, three KMS keys, Cognito Essentials with managed login and the
  MCP resource server, `auth` HTTP API to the `facade_as` container Lambda, `mcp` HTTP API to Fargate through a VPC
  link and an internal ALB. The `mcp` role has an explicit deny on every write to `SecurityState` (TB-5).
- Runtime entry points: `authz/kms_signer.py` (KMS ES256), `authz/lambda_handler.py` (HTTP API v2 to ASGI),
  `mcp/asgi.py` (uvicorn factory), `HW_EXTRA_ALLOWED_HOSTS` in `mcp/config.py`.
- `tests/unit/test_g3_probe.py` runs the real `pipeline.g3_probe` against the local apps (no network).

## Key decisions

- Table keys are `pk`/`sk` strings (what `ingest/ledger.py` writes), not `household`/`seq`; see the G3 log, note 1.
- Lowlevel SDK `Server` serves the blueprint schemas verbatim from `contracts/spec/tools.json`. The SDK's own auth is
  not used (it sends `WWW-Authenticate`); `mcp/auth.py` is plain ASGI middleware.
- Middleware at `/mcp`, outermost first: bearer auth, POST-only, protocol version, SDK transport. 401 never carries
  `WWW-Authenticate`; service tokens may only `initialize`, `notifications/initialized`, `tools/list`, `ping`.
- S- tools return `BLOCKED` without a broker; only a valid broker challenge becomes `PROOF_REQUIRED`.
- Everything served is `data_origin: synthetic` (`SyntheticStore`, one household for every account). Do not deploy
  with real data.
- Tests build a fresh MCP app per client (`tests/g2_support.py: Stack`); the SDK session manager runs once per app.

## Open items

- Human: deploy the stack (Docker needed), set the eight output values as gate variables, run
  `uv run python tools/pipeline.py --only G3 --probe`, link the account in the simulator, attest
  `G3.simulator_linking` in `docs/gates/attestations.json`.
- Deploy-time checks: Cognito and a repeated `resource`; the `aud`/scope Cognito issues (then set
  `required_user_scopes`); the Host header API Gateway forwards; PKCE enforcement on the client.
- Pin the Dockerfile dependencies from `uv.lock`; source the Alexa service client's secret digest (Secrets Manager).
- G4: replace the stand-in scorer in `mcp/tools_splus.py` with the SPRT decision core, and back the store with the
  ledger. G5: the proof broker behind `ToolContext.broker`.
- Still open from G1: the spec questions in `docs/gates/BLOCKERS.md`.
