# HEARTHWARD state

Snapshot for the next session. Details: `docs/gates/G1_COMPLETION_LOG.md` through `G6_COMPLETION_LOG.md`.

## HEAD

Branch `main`. Latest code commit: `dd88090` (`feat(G6): KMS decision attestation, offline judge verifier, and web
approval lockfile`), followed by the docs commit that adds the G6 log and this file. Before it: `a1dff48` and `9ac7000`
(G5), `3b95867`, `d0eae7b` (the human's `vendor/gnomon` submodule, `78c8d2e`) and `ac5d770` (G4), `6decd1e` and
`c4253a1` (G3), `273b995` (G2 PASS). Nothing is pushed (no remotes).

Commit policy: from G6 on, commits carry **no `Co-Authored-By` trailer** (the user is the sole author). Earlier commits
keep theirs; history is not rewritten.

## Gates

| Gate | Status | Notes |
|---|---|---|
| F | PASS | run before every commit: `uv run python tools/pipeline.py --only F` |
| G0 | PASS | human attestations recorded in `2f5ad88` |
| G1 | PASS | 360 tests |
| G2 | PASS | 191 tests; alexa discovery summary attested (`99a0dbb`) |
| G3 | INCOMPLETE (exit 3) | `G3.infra`, `G3.env` PASS; `G3.probe` needs a deployment and `--probe`; `G3.simulator_linking` is yours; 55 tests |
| G4 | PASS | 115 tests; `vendor/gnomon` pinned at `78c8d2e13c3b` |
| G5 | INCOMPLETE (exit 3) | everything automated PASS (123 tests); `G5.cloudtrail_proof_hash` is your attestation and needs a deployment |
| G6 | PASS | all 5 checks; 75 tests (+ 23 node tests inside them); verifier self-test 41/41 |
| G7 | not started | README sections, OSI license, friction log; `G7.demo_video` and `G7.devpost_submitted` are yours |

Full suite: 919 tests. Gate variables: five for G2, three for G3, three for G5, `HW_ANCHOR_BUCKET` for G6
(`hearthward-compliance-anchors` is accepted); synthetic values are in `tests/g2_support.py`, `tests/g5_support.py` and
the G3, G5 and G6 logs.

## G6 deliverables

- `attest/sign.py`: §9 decision records (exact field set), `hw-decision-v1` digest, `params_digest`, KMS signing as
  `{bytes_b64, sig_b64, key_id}` (DER signature, bytes signed exactly as canonicalized).
- `attest/anchor.py`: RFC 6962 Merkle tree with the `hw-anchor-v1` tag, audit paths, `AnchorWriter` to S3 Object Lock
  (COMPLIANCE, never overwrites).
- `tools/verify_attestation.py`: offline, standard library only; checks the signature over the raw bytes first, then the
  digest, `params_digest`, the ledger chain from the genesis head and the anchor. `--selftest` exits 0.
- `web/`: dependency-free ES modules (`approve.html`, `verify.html`), `package-lock.json` generated offline and tracked.
  The approval page signs `challenge = action_hash` and shows nothing whose action does not reproduce that hash; the
  verification page repeats the verifier's checks in the browser with WebCrypto.
- Not deployed or scheduled: the anchor job, the Object Lock bucket, the broker endpoints the pages call.

## Key decisions

- Table keys are `pk`/`sk` strings (what `ingest/ledger.py` writes), not `household`/`seq`; see the G3 log, note 1.
- S- tools return BLOCKED without a broker; only a valid broker challenge becomes PROOF_REQUIRED, and a broker's own
  BLOCKED keeps its rules. With a broker present an open episode is its decision (dual control).
- The decision core only makes dismissal *eligible* at Lambda <= B; watermarks and `certainly_*` decide (BT-A).
- Everything the MCP server serves is `data_origin: synthetic` and scored by the G2 stand-in, not the SPRT. Do not
  deploy with real data.
- HX-04 refuses a passkey counter that does not increase, so passkeys that always report 0 cannot approve.
- The web app has no npm dependencies (hard rule 3: PyPI only); adding one needs someone allowed to reach npm.
- Tests that edit session-wide fixtures (`spec`, `orrery_runs`) must deep-copy them first.

## Open items

- Human: finish G3 (deploy, `--probe`, simulator attestation) and G5 (`G5.cloudtrail_proof_hash`); G7 attestations.
- G7: README (Setup, Run, Tracks, Built during the submission window, AI assistance, Synthetic data), an OSI license,
  the friction log; then the demo video and submission.
- Build and deploy: the broker Lambda and approval endpoints, executor role, ladder state machine and its Lambdas,
  passkey registration, a real `FactsProvider`, the anchor job and Object Lock bucket; add them to the CDK stack.
- Fusion step that builds §9 decision records and signs them; publish the attest-signer public key for judges.
- Wire `decision.sprt.Accumulator`, a `CausalStore` and the broker's facts together (episode lifecycle §6.7); bind
  `GnomonBackend` to the real GNOMON graph (read `vendor/gnomon` first).
- G3 deploy-time checks: Cognito and a repeated `resource`; the `aud`/scope Cognito issues; the Host header API
  Gateway forwards; PKCE enforcement. Pin the Dockerfile dependencies; source the Alexa service client digest.
- Test real platform passkeys' counters (HX-04); §6.8 calibration of the HMM priors.
- Still open from G1: the spec questions in `docs/gates/BLOCKERS.md` (SQ-1 to SQ-5).
