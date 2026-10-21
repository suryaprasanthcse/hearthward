# G6 completion log

Gate G6 (integrity, offline verifier, web) executed by the main session. **G6 PASS (exit 0)** for all five checks.
The overall chain is still INCOMPLETE, because of G3 and G5 attestations that need a deployment.

- **Starting state:** branch `main`, tree clean, HEAD `a1dff48`; F, G0, G1, G2, G4 PASS; G3 and G5 INCOMPLETE.
- **Result:** `G6.modules`, `G6.env`, `G6.verifier`, `G6.web_lock`, `G6.tests` PASS.

## Commits created

| Hash | Message |
|---|---|
| `dd8809008e8694f5e6a485f8d13c993dd2149a5a` | feat(G6): KMS decision attestation, offline judge verifier, and web approval lockfile |
| (this commit) | docs(G6): completion log and state snapshot |

Gate F was run (PASS) before each commit. Paths were staged explicitly. No push, remote, amend, rebase or force.
**Neither G6 commit has a `Co-Authored-By` trailer**, at the user's instruction (Surya Prasanth R is the sole author).
Earlier commits keep the trailers they were made with; history was not rewritten.

## Tests

`uv run pytest -m gate_g6`: **75 passed**, 0 failed, 0 skipped, 0 xfailed (`tests/unit/test_attest.py`).
`npm test` in `web/` (run by one of those 75): **23 passed** under `node --test`.
Full suite: **919 passed** (360 gate_g1, 191 gate_g2, 55 gate_g3, 115 gate_g4, 123 gate_g5, 75 gate_g6). The verifier's
own self-test: **41/41 checks passed**. No network is used: KMS and S3 Object Lock run under `moto`, and the web
tests use Node's built-in WebCrypto with locally generated keys.

What the tests pin down:

- **Signing.** A decision record has exactly the §9 fields; every wrong type or value is refused before anything is
  signed; its bytes equal `canonical(record)`; its digest equals `tagged_digest(b"hw-decision-v1", record)` and is
  domain-separated; `params_digest` equals the orrery's; a real KMS (moto) signature verifies with `cryptography`.
- **The verifier is stdlib only.** An AST check proves it imports nothing outside the standard library, and the
  self-test also passes under `python -I -S` (no site-packages, no working directory on the path).
- **It agrees with the reference implementations.** Its P-256 verification matches `cryptography` on 25 random keys,
  its signer's output verifies in `cryptography`, its JCS equals `rfc8785` on 200 generated documents, and it replays
  every orrery scenario's ledger.
- **It catches tampering** in a KMS-signed bundle: edited record, signature, key, claimed digest, sprt block, an
  envelope, a truncated ledger, a bad audit path; it also rejects pretty-printed (re-serialized) signed bytes.
- **Anchors.** The Python Merkle tree and the verifier's agree for every size from 1 to 17, with audit paths for every
  leaf; leaves and nodes cannot be confused; anchors are written to a bucket with Object Lock in COMPLIANCE mode and
  a second write to the same key fails.
- **Web.** The lockfile is tracked and holds no dependencies; both pages have a strict CSP, no inline script and no
  remote resource; the browser verifier accepts the Python/KMS bundle and rejects three tamperings; the JavaScript
  action hash equals `authz.broker.action_hash`.

## `uv run python tools/pipeline.py --only G6` (exit 0)

With `HW_ANCHOR_BUCKET=hearthward-compliance-anchors`.

```
== G6 | Integrity and web ==
  PASS     G6.modules                   3 paths present
  PASS     G6.env                       1 variables set and well-formed
  PASS     G6.verifier                  verify_attestation --selftest: selftest: 41/41 checks passed
  PASS     G6.web_lock                  web lockfile committed
  PASS     G6.tests                     pytest -m gate_g6: 75 passed, 844 deselected in 23.20s
gate G6: PASS

summary: G6 PASS
```

## `uv run python tools/pipeline.py --gate G6 --keep-going` (exit 3), summary

All eleven synthetic variables for G2, G3, G5 and G6 set. F and G0 PASS; F.secrets scanned 119 files, no secrets.

```
G1.tests    pytest -m gate_g1: 360 passed, 559 deselected
G2.tests    pytest -m gate_g2: 191 passed, 728 deselected
gate G3: INCOMPLETE        (G3.probe PENDING; G3.simulator_linking MANUAL)
G4.tests    pytest -m gate_g4: 115 passed, 804 deselected
G5.tests    pytest -m gate_g5: 123 passed, 796 deselected
gate G5: INCOMPLETE        (G5.cloudtrail_proof_hash MANUAL)
G6.modules  3 paths present
G6.env      1 variables set and well-formed
G6.verifier verify_attestation --selftest: selftest: 41/41 checks passed
G6.web_lock web lockfile committed
G6.tests    pytest -m gate_g6: 75 passed, 844 deselected
gate G6: PASS

summary: F PASS, G0 PASS, G1 PASS, G2 PASS, G3 INCOMPLETE, G4 PASS, G5 INCOMPLETE, G6 PASS
```

(Condensed from the full run; the per-check lines for F, G0, G1, G2, G3, G4 and G5 are unchanged from the earlier logs.)

## What was built

| Path | Role |
|---|---|
| `src/hearthward/attest/sign.py` | `validate_decision`, `decision_bytes`, `decision_digest`, `params_digest`, `sign_bytes`, `attest_decision`, `KmsSigner` |
| `src/hearthward/attest/anchor.py` | `build_anchor`, `merkle_root`, `audit_path`, `inclusion`, `sign_anchor`, `AnchorWriter` (S3 Object Lock, COMPLIANCE) |
| `tools/verify_attestation.py` | the offline judge verifier: pure-Python P-256 ECDSA, strict DER, strict HW-JCS, ledger replay, anchor checks, `--selftest` |
| `web/` | `package.json`, offline-generated `package-lock.json`, `approve.html`, `verify.html`, `src/*.js`, `tests/attest.test.js` |

The verifier's order follows rule C-6: the signature over the **raw signed bytes** is checked first, and only then are
the bytes parsed. It never re-canonicalizes the signed bytes; it only recomputes JSON for ledger heads, because the
chain is defined over canonical JSON. A bundle (`public_key_b64`, `decision`, `ledger`, optional `anchor`,
`sprt_params`, `decision_digest`) is described in the file's docstring.

## Deviations and architectural notes

1. **The web app has no dependencies, on purpose.** Hard rule 3 allows PyPI only, and `npm install` of any package
   would contact the npm registry. The app is plain ES modules for the browser, tested with `node --test`, so
   `package-lock.json` could be generated offline (`npm install --package-lock-only --offline`) and holds just the
   root package. If dependencies are added later, the lockfile must be regenerated by someone allowed to reach npm.
2. **`G6.web_lock` says "committed" but the pipeline checks the git index** (`git ls-files --error-unmatch`). It
   therefore passes once the file is staged, and was verified again after the commit.
3. **Decision record shape.** The record has the §9 fields exactly. `cross_terms` accepts a list of term names (what the
   orrery writes) or a mapping of name to integer milli-nats; `thresholds_mnats` is any non-empty mapping of integers
   (the orrery uses keys `a` and `b`); `decision` is an upper-case word such as `INTERVENE`. The orrery's own record has
   extra and missing fields (`observed_features`, no `reasons`), so it is not signable as is; the fusion worker that
   builds real records must produce the §9 shape.
4. **The verifier's self-test signs with a deterministic, non-RFC-6979 nonce.** That is fine for a public synthetic
   key and fixed test data, and the file says so. It also carries one known-answer test from RFC 6979 appendix A.2.5.
5. **Bundles hold the whole household ledger from seq 1.** Replaying a chain needs every envelope; there is no
   checkpointing yet. The verifier says so ("the bundle holds no ledger") rather than skipping the check.
6. **Anchor key.** One public key is used for the decision and the anchor, since both are signed by `attest-signer`.
7. **Anchors are not scheduled or deployed.** `AnchorWriter` exists and is tested; nothing runs it every five minutes,
   there is no anchor Lambda, and the Object Lock bucket is not in the CDK stack. `IfNoneMatch` on `put_object`
   needs a recent boto3 and S3; moto honors it.
8. **The web pages call endpoints that do not exist yet** (`/api/challenges/<id>`, `/api/challenges/<id>/approve`).
   The approval page refuses to offer anything unless the displayed action reproduces the challenge's action hash.
   The server-side half of TB-8 (a signed challenge document the page can verify) is not built: the page currently
   trusts the document it is given for what to display, and relies on the hash match for authenticity.
9. **Test hygiene.** Two of my first G6 tests mutated session-wide fixtures (`orrery_runs`, `spec`) and broke 14 later,
   unrelated tests in the full suite. The G6 helpers now deep-copy; the full suite is green.

## Open items

- **Human:** the G3 deployment, probe and simulator attestation; the G5 CloudTrail attestation.
- Run `AnchorWriter` on a schedule; add the Object Lock bucket (`HW_ANCHOR_BUCKET`) and an anchor Lambda to the stack.
- Build the fusion step that produces §9 decision records, signs them with `attest-signer` and stores the bundle
  inputs; serve `/api/challenges/...` for the approval page.
- Publish the attest-signer public key somewhere a judge can compare it with the one in a bundle (the verifier prints
  a fingerprint but cannot know which key is the real one).
