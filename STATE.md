# HEARTHWARD state

Snapshot for the next session. Details: `docs/gates/G1_COMPLETION_LOG.md` through `G4_COMPLETION_LOG.md`.

## HEAD

Branch `main`. G4: `ac5d770` (`feat(G4): Causal engine, GNOMON adapter, and SPRT decision core`), `f93721c` (docs),
`d0eae7b` (`feat(G4): link vendor/gnomon submodule`, added by the human; pins GNOMON at `78c8d2e`), then the docs
commit that records G4 PASS. Before them: `6decd1e` (G3 docs), `c4253a1` (G3 code), `273b995` (G2 PASS). Nothing is
pushed (no remotes).

## Gates

| Gate | Status | Notes |
|---|---|---|
| F | PASS | run before every commit: `uv run python tools/pipeline.py --only F` |
| G0 | PASS | human attestations recorded in `2f5ad88` |
| G1 | PASS | 360 tests |
| G2 | PASS | 191 tests; alexa discovery summary attested (`99a0dbb`) |
| G3 | INCOMPLETE (exit 3) | `G3.infra`, `G3.env` PASS; `G3.probe` needs a deployment and `--probe`; `G3.simulator_linking` is yours; 55 tests |
| G4 | PASS | all 4 checks; 115 tests; `vendor/gnomon` pinned at `78c8d2e13c3b` by `d0eae7b`; `--only G4` exits 0 |
| G5+ | not started | |

Full suite: 721 tests. G2 needs five variables from `.env.example`, G3 three more; synthetic values are in
`tests/g2_support.py` and the G3 log.

## G4 deliverables

- `decision/`: `tactics.py` (seven features, closed enum set), `hmm.py` (forward pass; `lambda_trace_mnats` equals the
  orrery's reference exactly, bit for bit on the unrounded value), `sprt.py` (`Accumulator`, A = 4554 and B = -2986
  mnats, four cross-device terms, DC-1/2/3, TL-1).
- `causal/`: `intervals.py` (`Interval`, relations, `Watermarks`), `port.py` (`CausalStore`), `gnomon_adapter.py`
  (`GnomonCausalStore` over a backend protocol with an in-memory backend).
- `vendor/gnomon` is a submodule (`.gitmodules`: `url = ../gnomon`), added by the human in `d0eae7b`, pinned at
  `78c8d2e13c3be526a010d2b3558b4ac04fd2cded`. Nothing in this repository reads it yet: `GnomonCausalStore` still talks
  to its own `GnomonBackend` protocol (in-memory backend), written from the blueprint's mapping rather than GNOMON's
  real API.

## Key decisions

- Table keys are `pk`/`sk` strings (what `ingest/ledger.py` writes), not `household`/`seq`; see the G3 log, note 1.
- Lowlevel SDK `Server` serves the blueprint schemas verbatim from `contracts/spec/tools.json`. The SDK's own auth is
  not used (it sends `WWW-Authenticate`); `mcp/auth.py` is plain ASGI middleware.
- S- tools return `BLOCKED` without a broker; only a valid broker challenge becomes `PROOF_REQUIRED`.
- Everything the MCP server serves is `data_origin: synthetic` (`SyntheticStore`, one household for every account),
  and its scorer is still the G2 stand-in, not the SPRT. Do not deploy with real data.
- The decision core only makes dismissal *eligible* at Lambda <= B; watermarks and `certainly_*` decide (BT-A).
- Tests build a fresh MCP app per client (`tests/g2_support.py: Stack`); the SDK session manager runs once per app.

## Open items

- Human: deploy and `--probe` G3, attest `G3.simulator_linking`.
- Wire `decision.sprt.Accumulator` and a `CausalStore` into the S+ tools and the ledger (episode lifecycle §6.7,
  decision records with the `hw-decision-v1` digest); bind `GnomonBackend` to the real GNOMON graph now that
  `vendor/gnomon` exists (read its source first; the protocol is a guess at its shape).
- G3 deploy-time checks: Cognito and a repeated `resource`; the `aud`/scope Cognito issues; the Host header API
  Gateway forwards; PKCE enforcement. Pin the Dockerfile dependencies; source the Alexa service client digest.
- §6.8 calibration of the HMM priors on a labeled replay corpus.
- G5: the proof broker behind `ToolContext.broker`.
- Still open from G1: the spec questions in `docs/gates/BLOCKERS.md` (SQ-1 to SQ-5; SQ-6 is resolved).
