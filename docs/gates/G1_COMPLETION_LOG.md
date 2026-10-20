# G0 and G1 completion log

Autonomous run by the main session (Claude Opus 5.5, high effort). Gates G0 and G1 were executed end to end; G2
was not started.

- **Start:** 2026-10-20T20:29:39Z (2026-10-21 01:59 IST)
- **End:** 2026-10-20T21:25:09Z (2026-10-21 02:55 IST), when the G1 commit was verified and this log written
- **Starting state:** branch `main`, tree clean, HEAD `38bccc8` "docs(G0): attest track posture". That is the
  optional human commit, on top of the expected `a2dcd3e`. Nothing was discarded.

## Commits created

| Hash | Message |
|---|---|
| `b8b1303c5c8303f818dc21751d4b9fe9cd386a66` | chore(G0): environment, pins, subagent routing and verified constraints |
| `bf7ffee2ebcd617d62a51a497f8cf6f48d11b3c1` | test(G1): orrery fixture export and synthetic Ring and Bee fixtures |
| `bf1bf21236130f01db8239ac5125a468c50815eb` | feat(G1): HW-JCS contracts, hash-chained ledger, Ring and Bee ingest |
| (this commit) | docs(G1): completion log |

Gate F was run (PASS) before every commit. Paths were staged explicitly. There was no push, remote, amend, rebase
or force.

## Tests (`uv run pytest -m gate_g1`)

| File | Passed |
|---|---|
| `tests/unit/test_bee_bridge.py` | 61 |
| `tests/unit/test_canonical.py` | 85 |
| `tests/unit/test_envelope.py` | 54 |
| `tests/unit/test_fixture_export.py` | 4 |
| `tests/unit/test_ledger.py` | 43 |
| `tests/unit/test_ring_webhook.py` | 108 |
| `tests/unit/test_spec_export.py` | 5 |
| **Total** | **360 passed, 0 failed, 0 skipped, 0 xfailed** |

DynamoDB runs under `moto` 5.2.4 with its dummy credentials. There is no network use, no sleeps, and hypothesis
runs `derandomize=True` where it drives assertions.

## `uv run python tools/pipeline.py --gate G1 --keep-going` (exit 3: G0 awaits human attestations)

```
== F | Foundation ==
  PASS     F.files                      4 paths present
  PASS     F.gitattributes              fixtures/**, *.json and *.jsonl have text unset (no CRLF rewrites)
  PASS     F.blocks                     5 blocks parse
  PASS     F.contracts                  11 tools (6 S+, 5 S-), 17 rules, 0 findings
  PASS     F.hx                         HX-01..HX-12 well-formed; every S- tool covered
  PASS     F.sprt                       A = +4.5539, B = -2.9857; stochastic, left-to-right, benign = H0, negative terms verified only
  PASS     F.temporal                   Ring lateness covers its retry window; max_skew matches GNOMON
  PASS     F.envelope                   envelope schema uses the portable keyword subset
  PASS     F.orrery                     6 scenarios deterministic and as specified; 59 envelopes valid; chains intact
  PASS     F.gates                      every gate has a row in blueprint section 10
  PASS     F.secrets                    42 tracked or new files scanned, no secrets
gate F: PASS

== G0 | Environment and pins ==
  PASS     G0.pyproject                 requires-python >=3.12,<3.13; mcp==2.3.0
  PASS     G0.python_version            .python-version 3.12
  PASS     G0.lockfile                  uv.lock pins mcp 2.3.0
  PASS     G0.env_example               12 gate variables named, no values
  PASS     G0.interpreter               running on Python 3.12.14
  MANUAL   G0.alexa_console             add G0.alexa_console with by, at and evidence to docs\gates\attestations.json
  MANUAL   G0.bedrock_access            add G0.bedrock_access with by, at and evidence to docs\gates\attestations.json
  MANUAL   G0.cognito_tier              add G0.cognito_tier with by, at and evidence to docs\gates\attestations.json
  PASS     G0.track_posture             attested by Surya Prasanth R on 2026-10-21
gate G0: INCOMPLETE

== G1 | Contracts and ingest ==
  PASS     G1.modules                   5 paths present
  PASS     G1.fixtures                  4 fixtures byte-identical to HEAD, text unset
  PASS     G1.tests                     pytest -m gate_g1: 360 passed in 43.52s
gate G1: PASS

summary: F PASS, G0 INCOMPLETE, G1 PASS
```

## `uv run python tools/orrery.py --selftest` (exit 0)

```
doorstep_scam      decision INTERVENE obs 4    state intervening         Lambda  +8.256 trace sha256:9e2f22791794
late_and_lossy     decision INTERVENE obs 4    state intervening         Lambda  +9.831 trace sha256:8aca84bbf60a
skewed_door        decision INTERVENE obs 4    state intervening         Lambda  +8.256 trace sha256:1d427b5c7d19
grandparent_call   decision INTERVENE obs 2    state intervening         Lambda  +9.330 trace sha256:a1502156a04d
benign_delivery    decision None      obs None state closed_inconclusive Lambda  -0.287 trace sha256:fad82553c438
verified_plumber   decision None      obs None state dismissed           Lambda  -3.552 trace sha256:6a35e864a8aa
orrery self-test: PASS
```

All six full trace digests equal the baseline taken before the fixture export was added. This is pinned in
`tests/unit/test_fixture_export.py::test_selftest_traces_are_unchanged`.

## Fixtures (synthetic; doorstep_scam, seed 7)

| File | SHA-256 |
|---|---|
| `fixtures/ring/sample.json` | `27a512a9b4acc11866980b339caab5573f056dfc86bd445d3fff6526287e64e9` |
| `fixtures/ring/manifest.json` | `ce6e2fc3f31ea379f4a1ea87c19ce406b2e6b8a1ae9a8ef062b9ff07c72cb309` |
| `fixtures/bee/stream.jsonl` | `cb3857d27983c14c1bc277767c48a86c8f739511f8d36c2b5b04692e00044c74` |
| `fixtures/bee/manifest.json` | `4b1842c529ec10b3c5d4a6a48db61cc73e9532cec84a28618d68d6d47d8034c6` |

- They are produced by `uv run python tools/orrery.py --export-fixtures fixtures`, which is byte-deterministic
  (re-export test in place).
- They are signed with the orrery's `SYNTHETIC_RING_KEY` and `SYNTHETIC_BRIDGE_KEY` and labeled
  `"key_label": "synthetic"`.

## Pins

- `mcp` in `uv.lock`: **2.3.0**. `pyproject.toml` has the exact string `"mcp==2.3.0"`.
- Interpreter: CPython 3.12.14 (uv-managed).
- Confirmed from `.venv` by hw-scout: `rfc8785` 0.1.4, `jsonschema` 4.26.0, `moto` 5.2.4.
  - The MCP facts copied into `CLAUDE.md` were confirmed against `mcp` 2.3.0 sources: `MCPServer`, the handshake
    versions, the `2025-03-26` default, `TransportSecuritySettings` and `RequireAuthMiddleware`.

## Security review (hw-security-reviewer, Opus, max effort, read-only)

The review found no high-severity issues. It found no HMAC bypass, no canonicalization gap, no fork or gap in
append, no double ledgering and no information leak.

| ID | Finding | Action |
|---|---|---|
| M1 | A skew-quarantined Ring delivery was ledgered when Ring retried it, because quarantine claimed nothing (BT-5). | **Fixed.** `quarantine()` now claims the same `DEDUP#` key, so a later append or quarantine of the id is a duplicate (the orrery's single `seen` set). Regressions: `test_m1_*` in the ledger, Ring and Bee suites. |
| M2 | Authenticated deliveries the ledger rejects, such as `timestamp` 0–1999 (`valid.lo < 0`), a pre-1970 `meta.time`, or negative sync bounds, got a 500 forever (poison message). | **Fixed.** Ring and Bee run `ledger.precheck` and quarantine unledgerable envelopes with a 200. The ledger raises `LedgerValidationError` (a `LedgerError` subclass) for permanent failures. Regressions: `test_m2_*`. |
| M3 | Bee `sync_bounds` are not covered by the bridge HMAC but become `valid` of a `verified` envelope. | **Mitigated; spec question SQ-1.** Bounds must satisfy `0 <= lo <= hi <= received_at`, and anything else is quarantined. The claim limits an attacker to suppressing one line. Authenticating the bounds needs a TB-4 change. |
| L1 | A 400 for authenticated-but-uninterpretable bodies risks Ring disabling the endpoint; the `meta.time` parser required exactly nine digits. | **Partly fixed.** `meta.time` accepts 1–9 fractional digits, still with a `Z` suffix and truncated to ms. Invalid JSON and a non-1.1 version still get 400, as the G1 brief specifies. Regressions: `test_l1_*`. |
| L2 | Nothing binds `meta.account_id` to the household, so a delivery can replay across households that share a key. | **Deferred to G3, SQ-2.** Use a key per household, or map the account and quarantine on mismatch. |
| L3 | `verify` missed orphan or planted DEDUP claims, and such a claim silently absorbed deliveries. | **Fixed.** Claims are checked one to one against entries and quarantine records, and an append blocked by a claim that names nothing raises `LedgerError`. Regressions: `test_l3_*`. |
| L4 | `verify` parsed leniently and did not check that the stored string is canonical. | **Fixed.** Strict parse (no NaN, no duplicate keys), then a byte comparison with `canonical(env)`. Regressions: `test_l4_*`. |
| L5 | `verify` raised on tampered items: float seq, list source, huge int, a DEDUP item without seq. | **Fixed.** Every item problem is reported, never raised. `validate_envelope` guards unhashable `source`. Regressions: `test_l5_*`. |
| L6 | `check_profile` built path strings in quadratic time (authenticated CPU DoS). | **Fixed.** Paths are linked tuples rendered only on error. Regression: `test_l6_profile_check_is_linear_in_the_input`. |
| L7 | `key_provider()` ran before any cheap check. | **Fixed.** The Lambda adapter answers 401 for a missing or malformed `x-signature` before fetching the key. Regression: `test_l7_*`. |
| L8 | A lone-surrogate `request_id` or `uuid` could reach DynamoDB through quarantine. | **Fixed.** Ring answers 400, Bee rejects with `uuid`, and `quarantine()` refuses non-Unicode ids. Regressions: `test_l8_*`. |
| L9 | The Bee dedup id hashes the raw line, so identical utterances collide. | **Not changed; spec question SQ-3.** The code follows blueprint §4.1 and the orrery exactly. |
| L10 | Quarantine records share the EventLedger table, unlike the separate table in §4.4. | **Not changed; spec question SQ-4.** The G1 brief mandates the single-table `QUAR#` layout; the G3 Pipe must filter on `SEQ#`. |

The reviewer also confirmed that production canonicalization only rejects more than the orrery. Example:
`orrery._KEY` uses `^…$` with `.match`, which accepts a key `"a\n"`. Production uses `fullmatch`. The orrery was
left unchanged, since it is the reference and its digests must not move.

## Deviations from the prompt

1. **Required `household` argument.** `household` is a required keyword-only argument of `ring_webhook.handle`,
   `make_lambda_handler` and `bee_bridge.ingest_line`. The Ring payload carries no household (see SQ-2).
2. **Extra keyword arguments.** `handle`, `make_lambda_handler` and `ingest_line` also take
   `synthetic: bool = True`, because blueprint §1 and E-4 allow DOOR and WRIST data to be synthetic only.
   `Ledger(...)` takes `client`, `sleep` and `rng` keywords so the moto tests are deterministic.
   `Ledger.quarantine` returns `QUARANTINED` or `DUPLICATE`.
3. **Model routing.** The project subagent files were created, but this running session's agent registry did not
   load them (`Agent type 'hw-scout' not found`). Each role therefore ran on a built-in agent with the role file as
   its first instruction, on the prescribed model:
   - hw-scout: `Explore`, haiku;
   - hw-implementer: `general-purpose`, sonnet;
   - hw-security-reviewer: `Explore` (read-only), opus, max effort.

   No Fable model was used anywhere.
4. **Who wrote which tests.** The main session wrote the canonical, envelope, ledger, spec-export and
   fixture-export suites, plus the review regressions across all suites. That let the work run in parallel with the
   implementer, who wrote `ring_webhook.py`, `bee_bridge.py`, the fixture export and the Ring and Bee suites.
5. **Ledger key layout.** The ledger uses the brief's `pk`/`sk` single-table layout (`SEQ#…`, `HEAD`, `DEDUP#…`,
   `QUAR#…`), not blueprint §4.2's wording ("partition key `household`, sort key `seq`"). Quarantine uses the same
   table (SQ-4).
6. **One test change.** The implementer's `ms-fraction` 400 case became the accepted-and-truncated
   `test_l1_trimmed_fractions_are_accepted_and_truncated`. A `ten-digit-fraction` 400 case was added in its place.
   This tightens the test to the new behavior; it does not weaken it.
7. **MANUAL row count.** The prompt anticipated four MANUAL G0 rows. There are three, because the human had already
   attested `G0.track_posture` in `38bccc8`.
8. **Additions.** `src/hearthward/contracts/specs.py` (loaders for the exported JSON) and
   `export_spec.py --check` were added. A mid-run edit truncated `tests/unit/test_ledger.py` (untracked at the
   time); it was rebuilt in full from the session's own copy before the commit.

## Open items

- **MANUAL attestations (human only):** `G0.alexa_console`, `G0.bedrock_access`, `G0.cognito_tier` in
  `docs/gates/attestations.json`. This run never touched that file.
- **Spec questions:** SQ-1 to SQ-5 in `docs/gates/BLOCKERS.md` (Bee sync-bound authentication, Ring household
  binding, Bee dedup id, quarantine table versus the Streams Pipe filter, stability of Ring `request_id`). None
  blocks G1.
- **G2 not started,** as instructed.
