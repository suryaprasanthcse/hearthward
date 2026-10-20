# HEARTHWARD

HEARTHWARD helps people who live alone resist scams. It joins what the front door sees (Ring), what is said
in the room (Bee) and what Alexa+ is asked into one bitemporal, hash-chained record, decides with Wald's SPRT
over a scam-script HMM, and never lets a voice in the room lower the household's protection.

`docs/master_blueprint.md` is the single source of truth. Its fenced `json hearthward:<name>` blocks
(envelope, temporal, tools, hx, sprt) are machine-read by `tools/pipeline.py` and `tools/orrery.py`.

## Isolation

Work only inside `C:\myspace\hacckathons\hearthward`. Never list, read, write or run anything in sibling
folders of `C:\myspace\hacckathons\` (gnomon, cascade, amd-tenure, kintsugi or any other).

## Commands (always through `uv run`; the system Python is 3.14, the project is 3.12)

```bash
uv sync                                                  # install the locked environment
uv run python tools/pipeline.py --only F                 # foundation gate; run before every commit
uv run python tools/pipeline.py --gate G1 --keep-going   # F, G0, G1 in order
uv run python tools/pipeline.py --list                   # every gate and check
uv run python tools/orrery.py --selftest                 # deterministic reference simulator
uv run python tools/orrery.py --scenario doorstep_scam --envelopes
uv run python tools/orrery.py --export-fixtures fixtures # regenerate Ring and Bee fixtures (byte-deterministic)
uv run python tools/export_spec.py                       # blueprint blocks -> src/hearthward/contracts/spec/
uv run pytest -m gate_g1                                 # gate G1 tests
```

Pipeline exit codes: `0` pass, `1` a check failed, `2` usage error, `3` incomplete (pending or awaiting
attestation).

## Layout

- `src/hearthward/contracts/`: `canonical.py` (HW-JCS), `envelope.py` (schema, E-rules, chain),
  `spec/*.json` (exported from the blueprint; runtime code reads only these, never markdown).
- `src/hearthward/ingest/`: `ledger.py` (DynamoDB single table), `ring_webhook.py`, `bee_bridge.py`.
- `fixtures/`: byte-exact synthetic Ring and Bee deliveries exported by the orrery.
- `tests/unit/`: every file sets `pytestmark = pytest.mark.gate_g1` (or its gate).

## Model routing

| Role | Agent | Model | Effort | Owns |
|---|---|---|---|---|
| Main session | — | claude-opus-5-5 | high | `canonical.py`, `envelope.py`, `ledger.py`, integration, review fixes, every commit |
| Scout | `hw-scout` | claude-haiku-5-5 | low | read-only wide reads; confirms third-party APIs in `.venv` before use |
| Implementer | `hw-implementer` | claude-sonnet-5-5 | high | `ring_webhook.py`, `bee_bridge.py`, fixture export, test suites |
| Security reviewer | `hw-security-reviewer` | claude-opus-5-5 | max | read-only review of the five G1 modules, findings with file:line |

If a subagent's work fails its check twice, the main session takes it over. The main session never
switches model; it delegates instead.

## Hard rules

1. Fable is excluded: never use model `fable` or any `claude-fable-*` ID, here or in any subagent.
2. XERJ is excluded: no download, install, MCP config, field report, pull request or mention.
3. No AWS, Alexa, Ring or Bee network calls and no credentials. The only network use is PyPI via
   `uv lock` / `uv sync`. DynamoDB is tested with `moto`.
4. No `git push`, no remotes, no history rewriting (`--amend`, rebase, `reset --hard`), no force operations.
5. Never create, edit or delete `docs/gates/attestations.json`. Only the human writes attestations.
6. Never edit the `hearthward:*` spec blocks or change check semantics in `tools/pipeline.py` to make a
   check pass. Write the evidence to `docs/gates/BLOCKERS.md` instead.
7. Never weaken, skip or xfail a test or check to get to green.
8. No real secrets anywhere. Only the orrery's synthetic keys (`SYNTHETIC_RING_KEY`,
   `SYNTHETIC_BRIDGE_KEY`), labeled synthetic.
9. `.gitattributes` keeps `fixtures/**`, `*.json` and `*.jsonl` byte-exact (`-text`, and this machine has
   `core.autocrlf=true`). Never weaken it; read and write fixtures in binary mode.
10. Stage explicit paths only; run `--only F` before each commit; end each commit message with
    `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Verified facts

**MCP Python SDK 2.3.0** (published 2026-10-02, after the models' June 2026 knowledge cutoff; read the source
in `.venv/Lib/site-packages/mcp/` before using it):
- The server class is `mcp.server.mcpserver.MCPServer`.
- Handshake versions: 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25, plus the stateless 2026-07-28.
- A missing `MCP-Protocol-Version` header means 2025-03-26.
- Binding 127.0.0.1 auto-enables DNS-rebinding protection for localhost hosts only, so set
  `TransportSecuritySettings` explicitly (blueprint P-6).
- `RequireAuthMiddleware` sends `WWW-Authenticate`, which Alexa+ does not support (P-5).

**Alexa+ MCP** ([authentication](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-authentication),
[client lifecycle](https://developer.amazon.com/docs/alexaplus/add-ons/category-sdk-mcp-client-lifecycle.html)):
- The documented `initialize` sends 2025-03-26. There is no stable session ID.
- Auth is two-tier: `client_credentials` for discovery, then authorization code with PKCE S256.
- OIDC, DCR, step-up authorization and `WWW-Authenticate` on 401 are unsupported.
- Only `authorization_servers[0]` is used; `resource` must match the manifest exactly, trailing slash
  included; metadata is cached at deploy time.

**Cognito**:
- Its discovery document omits `code_challenge_methods_supported`, hence the RFC 8414 facade
  ([MCP TypeScript SDK PR #992](https://github.com/modelcontextprotocol/typescript-sdk/pull/992)).
- Resource binding works only with managed login on Essentials or Plus, never on `client_credentials`
  ([docs](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-define-resource-servers.html)).
- Accept only `token_use=access`.

**Ring** ([Partner API](https://developer.amazon.com/docs/ring/api-documentation.html)):
- `X-Signature: sha256=<hex>` is HMAC-SHA256 over the raw body, key `.encode()`d. Nonces use URL-safe
  Base64 without padding instead.
- Webhook payload v1.1; `meta.time` has nine fractional digits.
- Retries at 1 s, 5 s, 30 s, 2 min, 10 min, 1 h; the dead-letter queue keeps events 72 h.
- 2xx = delivered; 4xx is permanent and 10+ in 24 h can disable the endpoint; 5xx and timeouts are retried;
  the response deadline is 5 s. So internal errors return 500, never 4xx.
- `request_id` is the idempotency key; the rate limit is 100 requests per second.
- Staging needs US-located devices; this project uses synthetic data only.

**API Gateway HTTP API v2** lowercases header names and sets `isBase64Encoded` for binary bodies.

**Bee** ([realtime stream](https://docs.bee.computer/docs/realtime)):
- `bee stream --json` lines have no `event` field; detect them by structural keys.
- `new-utterance` carries no timestamp. Delivery is at-most-once.
- Conversation `id` is a number; `uuid` is a string. Handlebars `{{raw}}` HTML-escapes its output.

**HW-JCS (C-2)** (blueprint §4.3):
- Keys match `^[a-z0-9_]+$`; integers satisfy `|n| <= 2^53 - 1`; no floats.
- Times are integer epoch milliseconds; evidence is integer milli-nats.
- Authenticate raw bytes before parsing; never re-serialize JSON to verify a signature.
