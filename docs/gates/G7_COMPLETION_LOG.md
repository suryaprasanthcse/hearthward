# G7 completion log

Gate G7 (submission) executed by the main session. **Three of five checks pass; G7 is INCOMPLETE (exit 3)** because
`G7.demo_video` and `G7.devpost_submitted` are attestations only the author can make.

- **Starting state:** branch `main`, tree clean, HEAD `4c356a7`; F, G0, G1, G2, G4, G6 PASS; G3 and G5 INCOMPLETE.
- **Result:** `G7.readme`, `G7.license`, `G7.docs` PASS; `G7.demo_video`, `G7.devpost_submitted` MANUAL.

## Commits created

| Hash | Message |
|---|---|
| `a30f831` | docs(G7): README, MIT license, friction log and product feedback |
| (this commit) | docs(G7): completion log and state snapshot |

Gate F was run (PASS) before each commit. Paths were staged explicitly. No push, remote, amend, rebase or force.
Neither commit has a `Co-Authored-By` trailer, at the author's instruction.

## `uv run python tools/pipeline.py --only G7` (exit 3)

```
== G7 | Submission ==
  PASS     G7.readme                    6 required sections and the synthetic-data disclosure
  PASS     G7.license                   MIT License
  PASS     G7.docs                      product feedback and friction log written
  MANUAL   G7.demo_video                add G7.demo_video with by, at and evidence to docs\gates\attestations.json
  MANUAL   G7.devpost_submitted         add G7.devpost_submitted with by, at and evidence to docs\gates\attestations.json
gate G7: INCOMPLETE

summary: G7 INCOMPLETE
```

No code changed in this gate, so no test counts changed: the full suite is still 919 tests, all passing.

## What was written

| File | Role |
|---|---|
| `README.md` | The six sections the pipeline requires (Synthetic data, Tracks, Setup, Run, Built during the submission window, AI assistance), plus what it does, known limits and the license |
| `LICENSE` | MIT, copyright 2026 Surya Prasanth R |
| `docs/friction-log.md` | 20 numbered entries, each marked observed, documented or expected |
| `docs/product-feedback.md` | Feedback for Alexa+, the MCP Python SDK and AWS CDK/Cognito, pointing at friction-log entries |

## Decisions that are the author's to confirm

1. **The license is MIT.** The brief asked for "an OSI license" without naming one, so I chose MIT (the simplest) and put
   the author's name in the copyright line. If Apache 2.0 or another license is wanted, replace `LICENSE`; the pipeline
   accepts MIT, Apache, BSD-2/3 and MPL text.
2. **Credits (rule D-3), resolved.** The first README left KINTSUGI, TENURE and CASCADE blank because the repository
   recorded nothing about them. The author then supplied the lineage and repository links, and the README now credits
   all five projects (GNOMON, AXIOM, KINTSUGI, TENURE, CASCADE) plus three supporting research repositories, and states
   the window as 2026-08-31 to 2026-10-23 12:00 PT. The author's statements are taken as given; I could not check them
   against those repositories (the isolation rule keeps this session out of sibling folders).
3. **The AI-assistance paragraph** states that Claude Code with Claude Opus 5.5 and Sonnet 5.5 drafted the code under
   the author's direction, that the last gates' commits carry no trailer at the author's request, and that nothing in the
   repository calls a language model at runtime. Check it says what the author is comfortable saying.
4. **"Built during the submission window."** Resolved: the README gives the window the author stated and says this
   repository's git history starts on 2026-10-20. It says the author is the sole *human* author, and keeps the AI
   assistance disclosure beside it rather than claiming sole authorship outright.

## Disclosure rules (blueprint section 12)

- **D-1, D-2:** the README's "Synthetic data" section says Ring and Bee data are synthetic, that the simulator sets
  `auth.synthetic`, and that tool outputs carry `data_origin`. The same statement must appear **on screen in the video**;
  that is not something a file can do.
- **D-3:** see decision 2 above.
- **D-4:** the "AI assistance" section.
- **D-5:** no performance number appears in the README, friction log or product feedback. I removed one duration from
  the friction log for this reason and searched the three files for others. The README states that none was measured.

## What is still open for submission

- **Human, G7:** record the demo video (public YouTube or Vimeo URL, `duration_s` under 180, synthetic-data disclosure on
  screen) and add `G7.demo_video`; submit on Devpost and add `G7.devpost_submitted`.
- **Human, G3 and G5:** deploy the stack, run `--probe`, link the account in the Alexa+ simulator and attest
  `G3.simulator_linking`; attest `G5.cloudtrail_proof_hash` after a real approval. The README's status banner says
  nothing is deployed; update it once that changes.
- **Deadline:** the blueprint gives Fri 2026-10-23 12:00 PT, with the deployment staying up until 2026-11-20.
- Everything the earlier gate logs list as not built (broker endpoints, anchor job, facts source, wiring the decision
  core into the tools) is still open, and the README's "Known limits" section says so.
