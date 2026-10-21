# HEARTHWARD state

Snapshot for the next session. Details: `docs/gates/G1_COMPLETION_LOG.md` through `G4_COMPLETION_LOG.md`.

## HEAD

Branch `main`. Latest code commit: `ac5d770` (`feat(G4): Causal engine, GNOMON adapter, and SPRT decision core`),
followed by the docs commit that adds the G4 log, this file and the SQ-6 blocker. Before it: `6decd1e` (G3 docs),
`c4253a1` (G3 code), `273b995` (G2 PASS). Nothing is pushed (no remotes).

## Gates

| Gate | Status | Notes |
|---|---|---|
| F | PASS | run before every commit: `uv run python tools/pipeline.py --only F` |
| G0 | PASS | human attestations recorded in `2f5ad88` |
| G1 | PASS | 360 tests |
| G2 | PASS | 191 tests; alexa discovery summary attested (`99a0dbb`) |
| G3 | INCOMPLETE (exit 3) | `G3.infra`, `G3.env` PASS; `G3.probe` needs a deployment and `--probe`; `G3.simulator_linking` is yours; 55 tests |
| G4 | INCOMPLETE (exit 3) | `G4.modules`, `G4.conformance`, `G4.tests` PASS (115 tests); `G4.submodule` PENDING, see SQ-6 |
| G5+ | not started | |

Full suite: 721 tests. G2 needs five variables from `.env.example`, G3 three more; synthetic values are in
`tests/g2_support.py` and the G3 log.

## G4 deliverables

- `decision/`: `tactics.py` (seven features, closed enum set), `hmm.py` (forward pass; `lambda_trace_mnats` equals the
  orrery's reference exactly, bit for bit on the unrounded value), `sprt.py` (`Accumulator`, A = 4554 and B = -2986
  mnats, four cross-device terms, DC-1/2/3, TL-1).
- `causal/`: `intervals.py` (`Interval`, relations, `Watermarks`), `port.py` (`CausalStore`), `gnomon_adapter.py`
  (`GnomonCausalStore` over a backend protocol with an in-memory backend).
- `vendor/gnomon` is **not** a submodule: no URL or commit in the blueprint, the sibling folder is off limits, and a
  clone needs the network. `docs/gates/BLOCKERS.md` SQ-6 has the evidence and the choices.

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

- Human: decide SQ-6 (add `vendor/gnomon` yourself, or take cut C3); deploy and `--probe` G3, attest
  `G3.simulator_linking`.
- Wire `decision.sprt.Accumulator` and a `CausalStore` into the S+ tools and the ledger (episode lifecycle §6.7,
  decision records with the `hw-decision-v1` digest); bind `GnomonBackend` to the real GNOMON graph once vendored.
- G3 deploy-time checks: Cognito and a repeated `resource`; the `aud`/scope Cognito issues; the Host header API
  Gateway forwards; PKCE enforcement. Pin the Dockerfile dependencies; source the Alexa service client digest.
- §6.8 calibration of the HMM priors on a labeled replay corpus.
- G5: the proof broker behind `ToolContext.broker`.
- Still open from G1: the spec questions in `docs/gates/BLOCKERS.md` (SQ-1 to SQ-5).
