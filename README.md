# HEARTHWARD

HEARTHWARD helps people who live alone resist scams. It joins what the front door sees, what is said in the room
and what Alexa+ is asked into one bitemporal, hash-chained record. It decides with Wald's sequential probability
ratio test over a scam-script hidden Markov model, so the error budget is explicit, and it never lets a voice in the
room lower the household's protection: anything that lowers protection (dismissing an alert, pausing monitoring,
trusting a new vendor, changing the family circle, sharing evidence) needs a passkey approval from a family member.

> **Status, 2026-10-21.** The code, tests and infrastructure definition are complete and pass their automated gates.
> **Nothing is deployed yet and nothing has been run against a live Alexa+ account.** The deployed endpoints, the
> Alexa+ simulator account linking and the demo video are still to do. `STATE.md` has the current gate table, and
> `docs/gates/` has a completion log for each gate.

## Synthetic data

**Every Ring (front door) and Bee (wearable) event in this project is synthetic.** HEARTHWARD is not entered in the
Ring or Bee tracks, whose rules require real devices, and no real Ring or Bee device, account or credential is used
anywhere in the repository.

- The events come from a deterministic reference simulator, `tools/orrery.py`, which replays six scripted scenarios.
  Its Ring and Bee deliveries are byte-exact fixtures under `fixtures/`, signed with keys named `SYNTHETIC_RING_KEY`
  and `SYNTHETIC_BRIDGE_KEY` that protect nothing.
- Every ledger envelope records `auth.synthetic`, and the simulator sets it to `true` on every event it produces
  (blueprint rule E-4).
- Every MCP tool output carries `data_origin` (`live` or `synthetic`). The server built here serves an in-memory
  synthetic store, so every answer it gives says `synthetic`.
- Test credentials, keys and account numbers (for example `123456789012`) are placeholders.

The same disclosure is meant to appear on screen in the demo video.

## Tracks

- **Primary: Alexa+.** A self-hosted MCP add-on over Streamable HTTP at exactly `/mcp`: stateless, JSON responses,
  protocol versions 2025-11-25, 2025-06-18 and 2025-03-26 (a request without `MCP-Protocol-Version` counts as
  2025-03-26). Authentication is an OAuth 2.1 bearer token, and a missing or invalid token gets `401` with a JSON body
  and no `WWW-Authenticate` header, because Alexa+ does not support that header. An RFC 8414 facade in front of
  Cognito supplies what Cognito alone lacks (S256 advertised in the metadata, RFC 8414 discovery, a resource-bound
  `client_credentials` grant).
- **Mini challenge: Open Source.** MIT licensed (see `LICENSE`).
- **Mini challenge: AWS Builder.** An AWS CDK app (`infra/`) defines DynamoDB, KMS, Cognito, two API Gateway HTTP
  APIs, the facade Lambda and the Fargate MCP service. It has been synthesized but not deployed. The Step Functions
  ladder definition and the KMS attestation code exist and are tested, but are not in the stack yet.

## What it does

| Part | What it is | Where |
|---|---|---|
| Ledger | One hash-chained envelope per event, HW-JCS (RFC 8785) canonical bytes, DynamoDB single table, authenticate-then-parse ingest for Ring webhooks and Bee lines | `src/hearthward/contracts/`, `src/hearthward/ingest/` |
| Alexa+ surface | Eleven MCP tools: six that read or raise safety (fail open, rate limited) and five that lower protection (fail closed) | `src/hearthward/mcp/` |
| OAuth facade | RFC 8414 metadata, `/authorize` redirect to Cognito managed login, `/token`, `/jwks`, ES256 service tokens signed with a KMS key | `src/hearthward/authz/facade_as.py`, `kms_signer.py` |
| Decision core | Five-stage left-to-right HMM likelihood inside Wald's SPRT, thresholds A = +4.554 and B = -2.986, cross-device evidence terms. It matches the reference simulator to the milli-nat on its conformance vectors | `src/hearthward/decision/` |
| Causal engine | Bitemporal fact versions, `possibly_` and `certainly_` interval relations, watermarks. Escalation may use `possibly_`; de-escalation needs `certainly_` and watermark passage | `src/hearthward/causal/` |
| Broker | Twelve invariants (HX-01 to HX-12) as a rule table, a WebAuthn relying party, challenges bound to an action hash, single-use nonces, and a 900 s STS executor session tagged with the proof hash | `src/hearthward/authz/` |
| Intervention ladder | A Step Functions definition: notify in parallel, then walk the family circle with callback tasks (90 s timeout, 30 s heartbeat) | `src/hearthward/ladder/` |
| Attestation | Decisions signed by KMS, Merkle anchors for S3 Object Lock, and a standard-library-only offline verifier a judge can run | `src/hearthward/attest/`, `tools/verify_attestation.py` |
| Web | An approval page (passkey signs the action hash) and a verification page, with no dependencies | `web/` |
| Infrastructure | CDK stack: tables, keys, Cognito, the two APIs, the facade Lambda, the Fargate MCP service | `infra/` |

The blueprint, `docs/master_blueprint.md`, is the single source of truth. Its fenced `hearthward:*` blocks are read by
`tools/pipeline.py` and `tools/orrery.py`, and exported to JSON that the runtime code reads.

## Setup

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/); Node 20 or newer for the web tests. The Python
environment is locked (`uv.lock`) and `mcp` is pinned to 2.3.0.

```bash
uv sync
```

`vendor/gnomon` is a git submodule whose URL is relative (`../gnomon`), so it resolves only when a GNOMON repository
sits next to this one. Nothing in the runtime reads it yet; a gate check only confirms it is pinned.

```bash
git submodule update --init
```

Everything below runs offline. Tests use `moto` for AWS services and local keys for tokens; none of it contacts AWS,
Alexa, Ring or Bee.

## Run

```bash
uv run python tools/pipeline.py --list                 # every gate and check
uv run python tools/pipeline.py --only F               # the foundation gate
uv run python tools/pipeline.py --gate G6 --keep-going # F, G0, ..., G6 in order
uv run pytest                                          # all Python tests
uv run python tools/orrery.py --selftest               # the deterministic reference simulator
uv run python tools/orrery.py --scenario doorstep_scam --envelopes
python tools/verify_attestation.py --selftest          # the offline judge verifier (standard library only)
python tools/verify_attestation.py bundle.json         # verify a real attestation bundle
(cd web && npm test)                                   # the web app's tests
(cd infra && uv run python app.py)                     # synthesize the CDK app; calls no AWS API
```

Pipeline exit codes: `0` passed, `1` a check failed, `2` usage error, `3` incomplete (something is pending or needs a
human attestation). Some gate variables are needed for the later gates; `.env.example` lists them, and the gate logs
give synthetic values.

To exercise the MCP server, run the tests: `tests/g2_support.py` wires the server, the facade and local signing keys
together in one process. Running it for real needs the five `HW_*` variables for G2 and a reachable Cognito and
facade for key lookup, which means a deployment.

## Built during the submission window

Everything in this repository was written for this submission. Its git history starts on 2026-10-20 and the gates
were completed over the days after (`docs/gates/G1_COMPLETION_LOG.md` to `G6_COMPLETION_LOG.md` record what each gate
added and how it was checked). The 11 MCP tool schemas, the decision core, the broker, the ladder, the attestation
path and the web pages are all new code here.

What it does **not** include: the gate-by-gate logs list what is stubbed. Notably the tools run on an in-memory
synthetic store, the decision core is not yet wired into the tools (a placeholder scorer answers them), the broker has
no facts source or passkey registration, and the Ring, Bee, fusion, FalkorDB and web-API pieces of the blueprint's
topology are not built.

### Earlier repositories

The blueprint credits earlier projects of the same author. What the repository records about them:

- **GNOMON**: the bitemporal model (fact versions with a point valid time and belief bounds `[ts, te)`, a kernel
  with audits K1 to K9 and a mutation suite, `MAX_SKEW_MS = 60000`, branch-and-diff with `GRAPH.COPY`). HEARTHWARD
  maps its facts onto that shape in `causal/gnomon_adapter.py` and pins GNOMON as `vendor/gnomon`; no GNOMON code is
  copied into `src/`.
- **AXIOM**: the broker pattern (a Lambda that checks rules, then mints a short STS session with a session policy and a
  proof tag). `authz/broker.py` follows it.
- **KINTSUGI, TENURE, CASCADE**: credited in the blueprint (section 12, rule D-3), but this repository does not
  record which patterns each contributed. **The author must fill in those three lines before submission.**

## AI assistance

HEARTHWARD was built with AI assistance. The code, tests and documentation were drafted with **Claude Code**
(Anthropic's coding agent) running Claude models: Claude Opus 5.5 for the first gates and Claude Sonnet 5.5 for
the middle ones (visible in those commits' trailers); the last gates' commits carry no trailer, at the author's request. The work followed a written blueprint and a machine-checked gate
pipeline, and it was run under project rules that forbid network calls, real credentials and weakening any check.
Commits are authored by Surya Prasanth R, who set the direction, made the design decisions and wrote the human
attestations.

At runtime, nothing in this repository calls a language model. The blueprint describes a Bedrock extractor that turns
speech into seven schema-validated enum features, with recorded cassettes in tests; in this repository the features
that feed the decision core are hand-labeled by the simulator, and the decision core accepts only those enum values,
never free text.

## Known limits

- Not deployed, and not tested against live Alexa+. Several facts about Alexa+ and Cognito come from their
  documentation and are listed as unverified in blueprint section 13.
- A passkey whose signature counter does not increase cannot approve anything (rule HX-04). Some synced passkeys
  report a constant 0 and would be refused; this needs testing on real devices.
- The web pages call broker endpoints that do not exist yet.
- No performance figure is claimed anywhere in this project; none has been measured.

See `docs/product-feedback.md` and `docs/friction-log.md` for what building on these platforms was like.

## License

MIT. See `LICENSE`.
