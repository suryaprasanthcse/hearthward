# HEARTHWARD state

Snapshot for the next session. Details: `docs/gates/G1_COMPLETION_LOG.md`, `docs/gates/G2_COMPLETION_LOG.md`.

## HEAD

Branch `main`. G2 code is commit `813e6d6` (`feat(G2): Streamable HTTP MCP server, RFC 8414 OAuth facade, and S+
tools`), then `4d7506b` (this file and the G2 log), `99a0dbb` (the G2 attestation) and the docs commit that records
G2 PASS. Nothing is pushed (no remotes).

## Gates

| Gate | Status | Notes |
|---|---|---|
| F | PASS | run before every commit: `uv run python tools/pipeline.py --only F` |
| G0 | PASS | human attestations recorded in `2f5ad88` |
| G1 | PASS | 360 tests |
| G2 | PASS | 191 tests; `G2.alexa_discovery_summary` attested by Surya Prasanth R on 2026-10-21 (`99a0dbb`); `--gate G2 --keep-going` exits 0 |
| G3+ | not started | G3 needs CDK, deployed endpoints and the AWS-side items below |

G2 needs the five variables from `.env.example` set (synthetic values are in `tests/g2_support.py`):
`HW_MCP_RESOURCE_URI`, `HW_FACADE_ISSUER`, `HW_COGNITO_ISSUER`, `HW_COGNITO_DOMAIN`, `HW_ALEXA_CLIENT_ID`.

## Key G2 decisions

- Lowlevel SDK `Server` serves the blueprint schemas verbatim from `contracts/spec/tools.json` (exported by
  `tools/export_spec.py`, which now also writes `hx.json`). The SDK's own auth is not used (it sends
  `WWW-Authenticate`); `mcp/auth.py` is plain ASGI middleware.
- Middleware order at `/mcp`, outermost first: bearer auth, POST-only, protocol version, SDK transport (with DNS
  rebinding protection). 401 never carries `WWW-Authenticate`; service tokens (facade, `mcp:service`) may only
  `initialize`, `notifications/initialized`, `tools/list`, `ping`.
- An `initialize` asking for a version outside {2025-11-25, 2025-06-18, 2025-03-26} is answered with 2025-11-25.
- S- tools return `BLOCKED` without a broker; only a valid broker challenge becomes `PROOF_REQUIRED`.
- Everything served is `data_origin: synthetic` until the ledger-backed store replaces `SyntheticStore`.
- Tests build a fresh app per client (`tests/g2_support.py: Stack`); the SDK session manager runs once per app.

## Open items

- G3: KMS `facade-token-signer`, Secrets Manager for the Alexa service client, deployed answers to blueprint §7.4,
  confirm Cognito `aud`/scope behaviour (see the G2 log), DynamoDB-backed household store.
- G4: replace the stand-in scorer in `mcp/tools_splus.py` with the SPRT decision core.
- G5: the proof broker behind `ToolContext.broker`.
- Still open from G1: the spec questions in `docs/gates/BLOCKERS.md`.
