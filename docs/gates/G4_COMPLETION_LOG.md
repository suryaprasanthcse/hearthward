# G4 completion log

Gate G4 (causal engine and decision core) executed by the main session (Claude Sonnet 5.5). **G4 is INCOMPLETE
(exit 3) because of one check: `G4.submodule`.** Modules, conformance and tests all pass.

- **Starting state:** branch `main`, tree clean, HEAD `6decd1e`; F, G0, G1, G2 PASS; G3 INCOMPLETE (its deployment
  probe and simulator attestation are yours to finish).
- **Result:** `G4.modules` PASS, `G4.conformance` PASS, `G4.tests` PASS, `G4.submodule` PENDING.

## Commits created

| Hash | Message |
|---|---|
| `ac5d77018daf8e6ffb40d0236db04ba812ea390a` | feat(G4): Causal engine, GNOMON adapter, and SPRT decision core |
| (this commit) | docs(G4): completion log, state snapshot and the submodule blocker |

Gate F was run (PASS) before the feat commit. Paths were staged explicitly. No push, remote, amend, rebase or force.

## Tests (`uv run pytest -m gate_g4`)

| File | Passed |
|---|---|
| `tests/unit/test_decision.py` | 71 |
| `tests/unit/test_causal.py` | 44 |
| **Total** | **115 passed, 0 failed, 0 skipped, 0 xfailed** |

Full suite: 721 tests (360 gate_g1, 191 gate_g2, 55 gate_g3, 115 gate_g4), all passing as of the pipeline run below.

Conformance, checked three ways:

- `uv run python -c "import tools.pipeline as p; print(p.g4_conformance(p.Context(False)))"` printed
  `Outcome(status='PASS', detail='6 conformance vectors match the orrery to the milli-nat')`.
- The tests compare `lambda_trace_mnats` with `orrery.reference_trace_mnats` on each of the six vectors and on 400
  seeded random traces (utterances and ROOM claims mixed).
- They also compare the **unrounded** forward value with the orrery's `Accumulator.hmm` using `==`, bit for bit, over
  100 random sequences. The arithmetic and its order are the orrery's, so there is no floating-point drift to hide
  behind rounding.

## `uv run python tools/pipeline.py --only G4` (exit 3)

```
== G4 | Causal engine and decision core ==
  PENDING  G4.submodule                 vendor/gnomon is not a submodule yet
  PASS     G4.modules                   6 paths present
  PASS     G4.conformance               6 conformance vectors match the orrery to the milli-nat
  PASS     G4.tests                     pytest -m gate_g4: 115 passed, 606 deselected in 10.19s
gate G4: INCOMPLETE

summary: G4 INCOMPLETE
```

## `uv run python tools/pipeline.py --gate G4 --keep-going` (exit 3), G2 onward

Run with the five G2 and three G3 synthetic variables set. F, G0 and G1 were PASS in the same run (F.secrets: 87
files scanned, no secrets; G1.tests: 360 passed).

```
== G2 | MCP server and facade ==
  PASS     G2.modules                   7 paths present
  PASS     G2.env                       5 variables set and well-formed
  PASS     G2.schema_drift              11 served tool schemas identical to the blueprint
  PASS     G2.tests                     pytest -m gate_g2: 191 passed, 530 deselected in 11.27s
  PASS     G2.alexa_discovery_summary   attested by Surya Prasanth R on 2026-10-21
gate G2: PASS

== G3 | Deployed and linked ==
  PASS     G3.infra                     3 paths present
  PASS     G3.env                       3 variables set and well-formed
  PENDING  G3.probe                     run with --probe to check the deployed endpoints
  MANUAL   G3.simulator_linking         add G3.simulator_linking with by, at and evidence to docs\gates\attestations.json
gate G3: INCOMPLETE

== G4 | Causal engine and decision core ==
  PENDING  G4.submodule                 vendor/gnomon is not a submodule yet
  PASS     G4.modules                   6 paths present
  PASS     G4.conformance               6 conformance vectors match the orrery to the milli-nat
  PASS     G4.tests                     pytest -m gate_g4: 115 passed, 606 deselected in 10.15s
gate G4: INCOMPLETE

summary: F PASS, G0 PASS, G1 PASS, G2 PASS, G3 INCOMPLETE, G4 INCOMPLETE
```

## Why `G4.submodule` is not done

The brief asks to configure and initialize `vendor/gnomon`. This session could not, without breaking `CLAUDE.md`:
the blueprint gives no GNOMON URL or commit; the only copy known here is the sibling folder
`C:\myspace\hacckathons\gnomon`, which the isolation rule forbids touching; and a clone from a remote is a network
call outside PyPI (hard rule 3). A `.gitmodules` entry with no clone would make the check FAIL, and any workaround
would weaken it (hard rule 7), so nothing was added. The evidence and the two ways forward (you add the submodule, or
cut C3) are in `docs/gates/BLOCKERS.md` as **SQ-6**. To finish: run
`git submodule add <GNOMON url> vendor/gnomon` at the commit to pin (or give me the URL, commit and permission), then
`uv run python tools/pipeline.py --only G4`.

## What was built

| Module | Role |
|---|---|
| `decision/tactics.py` | the seven features as a closed enum set; `parse_observation` for the two conformance shapes; `claim_from_assessment` maps `assess_visitor` enum and boolean arguments to claim features. Free text is rejected (DC-4) |
| `decision/hmm.py` | `HmmModel.from_params` (validates shape, stochastic rows, left-to-right, noisy rates inside (0, 1)), `ForwardFilter`, `lambda_trace_mnats` |
| `decision/sprt.py` | `Accumulator`, `Thresholds` (A = 4554, B = -2986 mnats, checked against `thresholds_reported`), the four cross-device terms and their conditions |
| `causal/intervals.py` | `Interval`, `possibly_overlaps`, `certainly_before`, `certainly_during`, `Watermarks` |
| `causal/port.py` | `CausalStore` (abstract storage; belief-time queries, relations and watermark helpers written once), `FactVersion` |
| `causal/gnomon_adapter.py` | `GnomonCausalStore` over a `GnomonBackend` protocol, with `InMemoryGnomonBackend` |

## Deviations and architectural notes

1. **The GNOMON adapter is not bound to GNOMON yet.** It implements the blueprint's mapping (`vf = vf_lo`, `vf_hi`
   as an extra property, belief bounds `[ts, te)`) against a narrow backend protocol. I wrote that protocol without
   seeing GNOMON's code, so it describes the *record shape* the blueprint states, not GNOMON's real API (graph
   queries, `GRAPH.UDF LOAD`, audits K1 to K9). Expect a thin translation layer when the submodule arrives.
2. **Where the relations live.** The brief puts the relations on the port; they are concrete methods of the abstract
   `CausalStore`, over `Interval`, so every backend answers identically. Backends supply only `append`, `versions`
   and `fact_ids`.
3. **`SprtAccumulator` is `sprt.Accumulator`.** `observe_claim` drops features already observed (DC-2) and returns 0
   when nothing is new. `lambda_trace_mnats` does *not* apply DC-2: it is the raw §6.9 interface and must equal the
   orrery's `reference_trace_mnats`, which does not either.
4. **`B` is eligibility only.** `Status.DISMISS_ELIGIBLE` means Lambda <= B. Dismissing also needs
   `Watermarks.dismissal_allowed` and `certainly_*` relations (BT-A); nothing in the decision core dismisses.
5. **Terms and trust.** `apply("verified_official_callback")` raises `PermissionError` unless `verified=True`, which
   only an independent channel may pass (TL-1, TL-3). `validate_terms` refuses a negative LLR on any term not named
   `verified_*` (DC-1) and refuses an unknown term set.
6. **Watermarks.** A source with no events has no watermark (`None`), and `None` never passes a test, including
   dismissal. `END_OF_STREAM` is `2**53 - 1` (the HW-JCS integer bound), standing in for infinity so everything stays
   an integer. Late events never lower a watermark.
7. **Not wired in.** The decision core and the causal store are not yet connected to `assess_visitor`,
   `explain_last_alert` or the ledger; the G2 stand-in scorer in `mcp/tools_splus.py` is still what the tools use.
   `claim_from_assessment` is the bridge for that step. Episode lifecycle (§6.7), decision records and the
   `hw-decision-v1` digest are not built here either; they belong with the fusion service.

## Open items

- **Human:** decide SQ-6 (the GNOMON submodule); finish G3 (deploy, `--probe`, simulator attestation).
- Replace the stand-in scorer with `Accumulator` behind the S+ tools and back the store with the ledger.
- §6.8 calibration: the parameters are priors until fitted on a labeled replay corpus; `tpr`/`fpr` come from held-out
  data. `A` and `B` ship from replay-measured false-alarm rates, through the `hearthward:sprt` block only.
