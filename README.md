# HEARTHWARD

HEARTHWARD helps people who live alone resist scams. It joins what the front door sees, what is said in the room
and what Alexa+ is asked into one bitemporal, hash-chained record. It decides with Wald's sequential probability
ratio test over a scam-script hidden Markov model, so the error budget is explicit, and it never lets a voice in the
room lower the household's protection: anything that lowers protection (dismissing an alert, pausing monitoring,
trusting a new vendor, changing the family circle, sharing evidence) needs a passkey approval from a family member.

> **Status, 2026-10-21.** The code and tests pass their automated gates, and the CDK stack is **deployed to AWS
> (us-east-1)**. The gate's live probe passes against the deployed endpoints: the protected-resource metadata and the
> facade's S256 metadata are served, and an unauthenticated `POST /mcp` gets `401` with no `WWW-Authenticate`.
> **Nothing has been run against a live Alexa+ account yet**: linking the account in the Alexa+ simulator, the broker
> and ladder deployment, and the demo video are still to do. `STATE.md` has the current gate table, and `docs/gates/`
> has a completion log for each gate.

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
| Web | The Bitemporal Causal Radar (`web/index.html`: a WebGL fluid background, a gaze-tracking pyramid, the SPRT gauge, bitemporal orbits and a belief-sequence scrubber, replaying one synthetic scenario), an approval page (passkey signs the action hash) and a verification page, all with no dependencies | `web/` |
| Infrastructure | CDK stack: tables, keys, Cognito, the two APIs, the facade Lambda, the Fargate MCP service | `infra/` |

The blueprint, `docs/master_blueprint.md`, is the single source of truth. Its fenced `hearthward:*` blocks are read by
`tools/pipeline.py` and `tools/orrery.py`, and exported to JSON that the runtime code reads.

## Setup

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/); Node 20 or newer for the web tests. The Python
environment is locked (`uv.lock`) and `mcp` is pinned to 2.3.0.

```bash
uv sync
```

`vendor/gnomon` is a git submodule whose URL is relative (`../gnomon`), so it resolves only when a clone of
[GNOMON](https://github.com/suryaprasanthcse/gnomon) sits next to this one. Nothing in the runtime reads it yet; a gate
check only confirms it is pinned.

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
(cd web && python -m http.server 8000)                 # then open http://localhost:8000/ (the radar), /verify.html, /approve.html
uv run python tools/export_ui_scenario.py              # regenerate the radar's synthetic scenario from the orrery
(cd infra && uv run python app.py)                     # synthesize the CDK app; calls no AWS API
```

The radar page needs an HTTP server (it is made of ES modules, which browsers will not load from `file://`). It replays a
synthetic orrery run, shows the frame rate it actually measures, and pauses its fluid by default when the browser asks
for reduced motion.

Pipeline exit codes: `0` passed, `1` a check failed, `2` usage error, `3` incomplete (something is pending or needs a
human attestation). Some gate variables are needed for the later gates; `.env.example` lists them, and the gate logs
give synthetic values.

To exercise the MCP server, run the tests: `tests/g2_support.py` wires the server, the facade and local signing keys
together in one process. Running it for real needs the five `HW_*` variables for G2 and a reachable Cognito and
facade for key lookup, which means a deployment.

## Built during the submission window

The hackathon window ran from August 31, 2026 to October 23, 2026, 12:00 PM PT. HEARTHWARD and every earlier system
listed below were written by **Surya Prasanth R**, the sole human author and contributor of all of them, within that
window. HEARTHWARD itself was built with AI assistance, disclosed in full under [AI assistance](#ai-assistance).

### What was assembled for this submission

This repository's git history starts on 2026-10-20, and the gates were completed over the days after
(`docs/gates/G1_COMPLETION_LOG.md` to `G7_COMPLETION_LOG.md` record what each gate added and how it was checked).
The HEARTHWARD core runtime is all new code here:

- A Starlette **Streamable HTTP MCP server** at `/mcp`: stateless, JSON responses, protocol versions 2025-11-25,
  2025-06-18 and 2025-03-26 negotiated (a request without `MCP-Protocol-Version` counts as 2025-03-26), and a
  `401` without `WWW-Authenticate`.
- An **RFC 8414 OAuth 2.1 facade** (a Lambda in the CDK stack) that supplies what Cognito's discovery omits: S256
  advertised for PKCE, RFC 8414 metadata, a resource-bound `client_credentials` grant.
- **Wald's SPRT** over a five-state left-to-right **HMM**, with evidence reported, compared and hashed as integer
  milli-nats. The arithmetic runs in floating-point log space and is rounded to the milli-nat; it matches the
  reference simulator exactly on the conformance vectors.
- The **`InterventionLadder`** Step Functions Standard state machine, with callback tasks that wait on a task token
  under a bounded timeout and heartbeat, and an escalation signal sent with `SendTaskSuccess`.
- A **standard-library-only offline verifier** (`tools/verify_attestation.py`) for KMS-signed decisions and ledger
  anchors, and a **WebAuthn relying party** (`src/hearthward/authz/webauthn_rp.py`) behind the broker.
- An **AWS CDK stack** (`infra/app.py`) covering the DynamoDB tables, KMS keys, Cognito, the two HTTP APIs, the facade
  Lambda and the Fargate MCP service. It has been synthesized, not deployed; the ladder and the anchor job are not in it
  yet.

What it does **not** include: the gate-by-gate logs list what is stubbed. Notably the tools run on an in-memory
synthetic store, the decision core is not yet wired into the tools (a placeholder scorer answers them), the broker has
no facts source or passkey registration, and the Ring, Bee, fusion, FalkorDB and web-API pieces of the blueprint's
topology are not built.

### Earlier systems and what HEARTHWARD took from them

All of these are by Surya Prasanth R and were built within the same window. They are separate repositories; the
pattern is what carried over, and no code from them is copied into `src/`.

- [**GNOMON**](https://github.com/suryaprasanthcse/gnomon): a bitemporal causal reasoning engine. HEARTHWARD takes its
  model of valid intervals `[vf_lo, vf_hi]`, belief sequences `[ts, te)` and watermark progression, maps its facts onto
  that shape in `src/hearthward/causal/gnomon_adapter.py`, and pins the repository as the `vendor/gnomon` submodule.
- [**AXIOM**](https://github.com/suryaprasanthcse/AXIOM): the asymmetric broker authorization pattern, in which actions
  that raise safety fail open and actions that lower protection fail closed behind a cryptographic proof. This is the
  S+ and S- split and the proof-carrying flow in `src/hearthward/authz/broker.py`.
- [**KINTSUGI**](https://github.com/suryaprasanthcse/kintsugi-omega-master): the tamper-evident ledger architecture:
  hash-chained envelopes, a genesis anchor and immutable event auditability. This is the ledger chain in
  `src/hearthward/contracts/` and `src/hearthward/ingest/ledger.py`.
- [**TENURE**](https://github.com/suryaprasanthcse/amd-tenure): scoped delegation semantics, the short-lived (900 s)
  STS executor session bound to an action hash that the broker mints to apply an approved change.
- [**CASCADE**](https://github.com/suryaprasanthcse/CASCADE-GLOF-Telemetry): multi-source telemetry synchronization,
  meaning out-of-order event ingestion and absorbing network jitter. This is the late-event and watermark handling in
  the ingest and causal modules.
- Supporting spatial and telemetry research by the same author, listed for provenance only (HEARTHWARD does not depend
  on them):
  [GOLEM-HY3D-Orchestrator](https://github.com/suryaprasanthcse/GOLEM-HY3D-Orchestrator),
  [Luminara Spatial Engine](https://github.com/suryaprasanthcse/luminara-spatial-engine) and
  [Synapse-3D](https://github.com/suryaprasanthcse/SYNAPSE-3D-AA32).

## AI assistance

HEARTHWARD was engineered with AI assistance under the sole direction and architectural specification of Surya
Prasanth R. Code, test harnesses, and documentation were written with **Claude Code** (Anthropic) running Claude Opus
5.5 and Claude Sonnet 5.5. All system requirements, trust boundaries, mathematical invariants (HX-01 through HX-12),
and SPRT decision formulations were defined by the author, and the work was verified against a machine-checked gate
pipeline under isolation constraints, except for the steps that need a deployment, which are still open.

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
