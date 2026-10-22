# WARDLINE: build brief

Paste this whole document into a fresh Claude Code session opened at `C:\myspace\hacckathons\hearthward`.

---

## 0. What you are building

Build **Wardline** from scratch in `web/wardline/`. It is a standalone web instrument with no dependencies, and it
tells HEARTHWARD's thesis in a deterministic 90-second performance meant for screen recording. It has three acts:

1. **MEMBRANE.** Evidence of danger moves Λ freely. A request to *lower* protection (the S- tool `dismiss_alert`,
   spoken in the room) hits the ward line and bounces off it, because it needs a trusted-circle member's passkey
   (HX-01, HX-02).
2. **BITEMPORAL PLANE.** Valid time is plotted against belief time. Late, duplicated and recovered events change
   what HEARTHWARD believes, but never the decision it already made (BT-1 to BT-4).
3. **CRYPTOGRAPHIC ZIPPER.** The ledger's hash chain and the signed decision record are verified in the browser.
   Tampering visibly opens the zipper, and re-chaining to hide the change is still caught by the signed head.

The quality bar is a defense-grade instrument:
- Every pixel maps to data or to a formula in this brief.
- The screen is still until something changes.
- Everything is legible in a YouTube-compressed 1080p recording.
- All data is synthetic, and the screen says so at all times.

There are **no reference images**. This brief specifies every visual through geometry, tokens and shader math.

---

## 1. Boundaries (non-negotiable)

- **Write only under `web/wardline/`.** Everything else is read-only, including:
  - `tools/`, `src/`, `tests/`, `infra/`, `docs/`, `fixtures/`
  - `web/src/`, `web/*.html`, `web/package*.json`
  - the root `.gitattributes` and `.claude/`

  Before you report, `git status --porcelain` must list only paths under `web/wardline/`.
- **Why this leaves deployed assets untouched:** `.dockerignore` admits only `src/` and the two Dockerfiles, and
  `infra/app.py` has no static hosting, so nothing under `web/` ships. Do not run `cdk`, `aws`, `docker` or any
  deploy, and do not change any of this.
- **No network, at build or run time.** That means:
  - no CDN, web fonts, `fetch`, XHR, WebSocket or `eval`
  - no `npm install`
  - only the Node ≥ 20 built-ins and the browser platform

  Python always runs through `uv run` (CLAUDE.md).
- **Isolation (CLAUDE.md).** Never list, read or open sibling folders of `C:\myspace\hacckathons\`. Do not look for
  reference images.
- **Do not borrow the existing radar page's look** (`web/index.html`). No pyramid, no eye, no Aries, no Latin
  mottos, no Navier–Stokes fluid.
- **No commits.** Do not stage, commit or push. Stop and report; the human commits.
- **No private keys.** Never export, print or log one. `data.js` carries only the SPKI public key, the DER
  signature and the signed bytes.
- **Honesty rules:**
  - **D-1:** "SYNTHETIC DEMO DATA" is visible in every act.
  - **D-5:** no performance number appears unless it was measured, with its method. Never write "60 FPS".
  - **TB-8:** Verify is display-only; the browser never authorizes anything.
  - **§8:** L3 is deferred, and this replay does not run the Step Functions ladder.
- **The repo wins.** If this brief contradicts `docs/master_blueprint.md` or the code, follow the repo and list the
  conflict in your report.

---

## 2. Read before you write (read-only)

| File | What you need from it |
|---|---|
| `CLAUDE.md` | Hard rules, commands, isolation |
| `docs/master_blueprint.md` | §3 (TL-1 to TL-3), §4.1 to §4.4, §5.1, §5.4 to §5.5, §6.4 to §6.7, §8, §9, §12 |
| `tools/orrery.py` | `Orrery` (`.run()`, `.acc.log_alpha`, `.watermark(lane)`, `.flushed`, `.store.versions`, `.ledger`, `.decision`, `.decision_digest`); `SCENARIOS`; `Row.as_dict()`; `INF = 2**62`; `EPOCH_MS`; `LANES`; `SOURCE`; `canonical()`; `mnats()`; `Model.from_spec()`; `load_spec()` |
| `tools/export_ui_scenario.py` | `build()` gives doorstep_scam (seed 7): envelopes, steps, `intervene_at_seq`, `proof_required_at_seq` and an `attestation` signed with a synthetic key (`key_id` `synthetic-demo-key`, not KMS) |
| `tools/verify_attestation.py` | The reference verifier. Your browser checks must agree with it. |
| `src/hearthward/attest/sign.py` | Production signing: KMS `Sign` with `MessageType="RAW"` and `SigningAlgorithm="ECDSA_SHA_256"`, over the canonical decision bytes |
| `web/src/encoding.js`, `web/src/jcs.js`, `web/src/attest.js` | Existing browser code. Read it for reference but do not import it: Wardline is standalone, and `chain.js` must give identical results. |

---

## 3. Ground-truth snapshot (seed 7): confirm it, don't trust it

Run this probe first. If any value differs, the repo wins; adapt and note the difference.

```bash
uv run python - <<'EOF'
import sys; sys.path.insert(0, "tools")
import orrery
spec = orrery.load_spec()
for name in ("doorstep_scam", "late_and_lossy", "verified_plumber"):
    o = orrery.Orrery(spec, orrery.SCENARIOS[name](), 7)
    rows = list(o.run())
    print(f"== {name}: {len(rows)} rows, {len(o.ledger)} envelopes, decision at_seq={o.decision and o.decision['at_seq']}")
    for r in rows:
        print(f"{r.at:>7} {r.lane:<6} {r.event:<12} seq={r.seq} L={r.lambda_mnats} d={r.delta_mnats} {r.features} {r.label!r} | {r.note[:80]}")
EOF
```

What the probe printed on 2026-10-22:

- **doorstep_scam:** 16 rows, 12 envelopes.
  - Λ after seq 4, 5, 7 and 8 is 1500, 1777, 3623 and 6066 mn.
  - The `INTERVENE` row follows seq 8 (observation 4). The signed record from `export_ui_scenario.build()` has
    `at_seq` 8 and `lambda_mnats` 6066.
  - Seq 9 is the room claim, +1200 (`untrusted_org_irreversible_payment`).
  - Seq 11 is `PROOF_REQ`, whose note reads `dismiss_alert returns PROOF_REQUIRED [HX-01, HX-02, HX-09]`.
  - Final Λ is 8256.
- **late_and_lossy:** 17 rows, 12 envelopes. INTERVENE follows seq 7 (Λ 6762).
  - **`DUPLICATE`** follows seq 5 and is absorbed (E-6).
  - **Seq 11, `INGEST@5`:** the first front-door motion, delivered on Ring attempt 5. It was received at
    +2:36.742, with valid time [−1.200 s, +2.800 s], and it revises the visit start *after* the decision.
  - **Seq 12, `INGEST+SYNC`:** a lost Bee line recovered by sync at +5:00.114 (Λ 9831).
  - **The visit has five versions.** v5 has `ts` 13 (a belief sequence past the 12 envelopes) and `vt` +3:17.803.
  - **The `AS_OF` note reads:** "believed at seq 7: [+00:02.800, +00:30.801] v2; now [−00:01.200, +01:17.803] v5".
  - **Watermarks, relative to `EPOCH_MS`:** door about −72.5 min and wrist about −9.7 min (both off the plane
    for the whole stream); room 42,598 ms, then 55,437 ms. After END all three are `INF`, because the stream is
    flushed.
- **verified_plumber:** 14 rows, 10 envelopes, no decision.
  - Seq 5 is a benign line at −593 (evidence can fall without any verification).
  - Seq 7 is `system.verified_callback` at −3500 (TL-1, DC-1), taking Λ to −2593.
  - Seq 8 takes Λ to −3132, at or below B, which produces `HOLD` (BT-A).
  - `DISMISS` is near the end; final Λ is −3552.
- **Labels:**
  - Utterance rows carry the quoted line, quotes included.
  - Door rows read like `motion driveway` or `button front_door`.
  - Bee rows read `conversation starts`; the room reads `assess_visitor`.
  - `PROOF_REQ` reads `dismiss_alert (S-)`.
- **Thresholds:** `mnats(Model.A)` = 4554 (ln 95) and `mnats(Model.B)` = −2986 (a 1 : 20 likelihood ratio).
  Export them; never hard-code them.

---

## 4. File tree

```
web/wardline/
  index.html         page shell, CSP, layers, accessible text
  wardline.css       tokens, type, layout of the HUD
  main.js            entry: DOM wiring, clock, input, URL params, governor (keeps the 8 modules DOM-free)
  data.js            GENERATED by export_data.py; never hand-edited
  model.js           pure: data -> geometry, state as of a row, sentences, formatters
  director.js        pure: time -> frame; the 90 s choreography and the key map
  ward.gl.js         WebGL2 light layer (background, ward glows, trail, shock, particles)
  plane.svg.js       the SVG ink layer: Act I lines and labels, Act II plane, Act III tiles, zipper, lattice
  chain.js           HW-JCS, SHA-256 chain replay, DER -> raw P-256, ECDSA verify, tamper and re-chain
  fallback.c2d.js    Canvas 2D implementation of ward.gl.js's interface
  export_data.py     generator for data.js (imports tools/ read-only)
  .gitattributes     data.js -text   (nested; strengthens, never weakens, the root file)
  README.md          run, record and verify instructions, plus the synthetic-data disclosure
  tests/
    chain.test.js  model.test.js  director.test.js  static.test.js
```

Match the style of `web/src/`: ES modules, 2-space indent, double quotes, semicolons, `const` and arrow functions,
and short comments only where the reason is not obvious. Match `tools/export_ui_scenario.py` for the Python file.

---

## 5. `data.js`: the generated contract (`export_data.py`)

**Command:** `uv run python web/wardline/export_data.py [--check]`. `--check` exits 1 if the file on disk is stale.

**Imports:**

```python
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import orrery
import export_ui_scenario as ui
OUT = Path(__file__).resolve().parent / "data.js"
```

**`build()`:**

1. `spec = orrery.load_spec()`; `signed = ui.build()` (doorstep_scam envelopes and the attestation).
2. For each of `doorstep_scam`, `late_and_lossy` and `verified_plumber`:
   - Create `o = orrery.Orrery(spec, orrery.SCENARIOS[name](), 7)` and iterate `o.run()`.
   - After each row, record `row.as_dict()` plus:
     - **`posterior_ppm`:** P(stage | observations, H1) from `o.acc.log_alpha`, as five integers in parts per
       million that sum to exactly 1,000,000. Use a max-shifted softmax with −inf treated as probability 0, floor
       each value, then hand the remainder out by largest fractional part (ties go to the lower stage index). Use
       `null` before the first observation.
     - **`watermarks`:** `{door, room, wrist}`, each `o.watermark(lane)` as absolute epoch ms, or `null` when it
       equals `INF`.
     - **`flushed`:** `o.flushed`.
     - **`hx_rules`:** the IDs inside `PROOF_REQUIRED [...]` in the note, for `PROOF_REQ` rows; `[]` otherwise.
   - After the loop, record:
     - `envelopes`: `o.ledger`, all 15 fields.
     - `versions`: every version of every fact, as `{fid, n, vf_lo, vf_hi, vt, ts, te, data}`.
     - `decision`: `o.decision` or `null`.
     - `decision_digest`: `o.decision_digest`.
3. **Consistency assertion:** the heads of doorstep_scam's `o.ledger` equal `[e["head"] for e in signed["envelopes"]]`.
4. **`INF` handling:** replace every value equal to `orrery.INF` with `None`. Then assert that no integer anywhere
   exceeds 2^53 − 1.
5. **Profile check:** pass every exported object through `orrery.canonical()`, which raises on floats and on keys
   outside `^[a-z0-9_]+$`. This is why `hx` is a list of objects and the cross-device terms are milli-nats.

**Shape:**

```js
export const WARD = {
  "synthetic": true, "seed": 7, "epoch_ms": <EPOCH_MS>, "household": "hh_demo0001",
  "sprt": {"a_mnats": 4554, "b_mnats": -2986, "stages": [5 names], "features": [7 names],
           "cross_device_llr_mnats": {"door_unknown_overlap": 700, ...}},          // via orrery.mnats
  "temporal": {"lateness_ms": {...}, "visit_gap_ms": 120000, "epsilon_ring_ms": 2000, "delta_ms": {...}},
  "hx": [{"id": "HX-01", "title": "Open-episode lock"}, ...],                    // from spec["hx"]
  "scenarios": {"doorstep_scam": {...}, "late_and_lossy": {...}, "verified_plumber": {...}},
  //   each: {"summary", "rows": [...], "envelopes": [...], "versions": [...], "decision", "decision_digest"}
  "signed": {"scenario": "doorstep_scam", "intervene_at_seq": 8, "proof_required_at_seq": 11,
             "attestation": <ui.build()["attestation"], unchanged>}
};
```

**Rendering:**

```python
HEADER = """// GENERATED by web/wardline/export_data.py from tools/orrery.py (seed 7). Do not edit; run the exporter again.
// SYNTHETIC DATA: every event, hash and number below comes from the reference simulator. The decision signature
// uses a throwaway public synthetic key (key_id synthetic-demo-key), not KMS.
"""
body = json.dumps(data, sort_keys=True, indent=1, ensure_ascii=True)
OUT.write_bytes((HEADER + f"export const WARD = {body};\n").encode("ascii"))   # LF only
```

Add `web/wardline/.gitattributes` containing `data.js -text`. This machine has `core.autocrlf=true`.

---

## 6. Visual system

### 6.1 Stage

- **Stage size:** everything is laid out on a logical **1920 × 1080 stage** (`#stage`).
- **Scaling:** `main.js` scales the stage by `s = min(innerWidth/1920, innerHeight/1080)` and centres it on a
  `--bg-0` letterbox.
- **Coordinates:** every coordinate below is in stage px.
- **Safe area:** x 96–1824, y 64–1016.
- **Narrow screens:** below 900 px of viewport width, show a one-line notice: "16:9 demo instrument, best at ≥ 1280
  px". The visible transcript (§15) still carries every sentence.

### 6.2 Layers (bottom to top)

1. **`#glow`:** a canvas covering the whole stage. `ward.gl.js` (or `fallback.c2d.js`) draws the background, the
   vignette, the dither and all light. It is the only animated raster layer.
2. **`#ink`:** `<svg viewBox="0 0 1920 1080">`, owned by `plane.svg.js`. It holds every crisp line, rect and SVG
   label, in one `<g>` per act plus a lattice group.
3. **HUD DOM:** top bar, sentence strip, Act I readouts, Act II side column, Act III checklist and glass box,
   caption band, bottom bar.
4. **`#card`:** the final card.

**Act transitions:** each act's SVG group and HUD section cross-fade over the windows in §10, using opacity only.
There is no fake geometric morph between scenarios.

### 6.3 Tokens (`wardline.css`). The page is dark-only, built for recording.

```css
:root {
  --bg-0:#07090C; --bg-1:#0C1117; --bg-2:#131A22;            /* canvas, panel, raised */
  --line-1:#1C2430; --line-2:#2A3542;                         /* hairline, emphasized rule */
  --ink-1:#E6EDF3;  /* ~16:1 on bg-0: data and primary text */
  --ink-2:#9AA7B4;  /* ~8:1: secondary */
  --ink-3:#5F6B78;  /* ~3.7:1: 12px caps labels only, never data */
  --toward-benign:#2EC4B6;  /* ΔΛ < 0 */
  --toward-scam:#F2A33A;    /* ΔΛ > 0, claimed tactics */
  --intervene:#FF5D4F;      /* Λ ≥ A, BLOCKED, failed checks */
  --verified:#8EA2FF;       /* trust level verified: official callback, passkey */
  --ok: var(--toward-benign); --fail: var(--intervene);
  --synthetic-hatch: repeating-linear-gradient(135deg, rgba(154,167,180,.07) 0 1px, transparent 1px 6px);
  --font-ui: ui-sans-serif, system-ui, "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", Roboto, Arial, sans-serif;
  --font-data: ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --ease-out: cubic-bezier(.2,.8,.2,1);
}
```

**Rules:**
- Colour carries meaning only. Amber and vermilion collapse under deuteranopia, so INTERVENE and BLOCKED are also
  marked by a solid fill, the word itself, and a solid line.
- **Trust is shown by shape (TL-1 to TL-3):**
  - `observed` (authenticated Ring or Bee delivery): filled.
  - `claimed` (Alexa+ arguments, room claims, extracted tactics, a voice's request): dashed outline, `4 3`.
  - `verified` (official callback, passkey): filled with a 1.5 px double ring in `--verified`.
- 1 px hairlines, 4 px panel radius, 2 px chip radius. No drop shadows; elevation is surface tone only.

### 6.4 Type (stage px; all numbers use `font-variant-numeric: tabular-nums slashed-zero`)

| Token | Size / line height | Use |
|---|---|---|
| label | 12/16, 600, caps, `letter-spacing: .08em` | axis and panel labels (`--ink-3` or `--ink-2`) |
| body | 14/20 | notes, checklist, legend |
| title | 16/24, 600 | panel titles |
| sentence | 20/28, 500 | the plain-language sentence, captions |
| readout | 32/36, mono 500 | secondary numbers |
| hero | 64/64, mono 500, `letter-spacing: -.02em` | Λ |

- **Minimum size:** any number a viewer must read is at least 14 px.
- **Negative numbers:** show them with U+2212 (−). Thousands use `,` separators.

### 6.5 Persistent chrome

- **Top bar, y 0–64:**
  - At x 96: `HEARTHWARD` (14 caps, 600, `.16em`, `--ink-1`) followed by ` WARDLINE` in `--ink-3`.
  - Centred at x 960: `I MEMBRANE · II BITEMPORAL PLANE · III CRYPTOGRAPHIC ZIPPER` (label style). The active
    act is `--ink-1`; the others are `--ink-3`.
  - Right-aligned to x 1824:
    - `seq 08 / 12` (mono 12, `--ink-2`): the active scenario's current seq.
    - `hh_demo0001` (mono 12, `--ink-3`).
    - Chip: `SYNTHETIC DEMO DATA · ORRERY SEED 7`, 12 caps, padding 4 × 8, background `--synthetic-hatch` over
      `--bg-1`, 1 px `--line-2`.
- **Sentence strip, y 72–112, x 96–1824:**
  - Sentence style, `--ink-1`, single line, at most 120 characters (a test enforces this).
  - `aria-live="polite"`.
- **Caption band, y 868–1000, x 560–1824 (Act I), or x 160–1824 (Acts II and III):**
  - Chip row at y 868–892.
  - Speaker tag at y 904, label style, e.g. `WRIST · SYNTHETIC BEE`.
  - Text at y 924–1000: sentence style, at most two lines, clamped.
  - Utterances show the quoted label; other rows show the row note (body, mono, `--ink-2`).
- **Bottom bar, y 1016–1080:**
  - **Progress line:** at y 1024, 2 px, x 96–1824. Track `--line-2`, fill `--ink-2`. Act ticks at 35 s and 65 s
    (`x = 96 + 1728·t/90000`).
  - **Time code:** right-aligned at x 1824, y 1052, mono 12: `00:42.3 / 01:30.0`.
  - **Buttons:** at y 1040–1068, label style, 28 px tall, 1 px `--line-2`, each showing its key:
    `▶ Space` · `I 1` · `II 2` · `III 3` · `Verify V` · `Tamper T` · `Reset R`.
  - **`H`** hides the buttons; the progress line and time code stay.

---

## 7. Act I: MEMBRANE

### 7.1 Field mapping (`model.js` exports these constants and functions; tests assert them)

```
FIELD = { walkX0: 560, walkX1: 1760, wardX0: 560, wardX1: 1824, top: 120, bottom: 860 }
LAMBDA = { max: 9000, min: -4000 }
yOfLambda(m) = 120 + (9000 - clamp(m, -4000, 9000)) * 740 / 13000
   A = 4554 -> 373.08     B = -2986 -> 802.28     0 -> 632.31     8256 -> 162.35     -3552 -> 834.50
xOfAt(at) = 560 + (at - atFirst) / (atLast - atFirst) * 1200
   (atFirst and atLast are taken over the rows of the current segment)
```

**Walk:** a step polyline. P₀ = (x₀, y₀); for each later row *i*, add (xᵢ, yᵢ₋₁) and then (xᵢ, yᵢ). Rows with the
same `at` give zero-length segments, which is fine. There are at most 64 vertices.

### 7.2 Left column (x 96–496)

- **Readout:**
  - y 120: `Λ · EVIDENCE` (label style).
  - y 136–200: hero, e.g. `+6,066`, followed by ` mn` (20 px, `--ink-2`). The hero turns `--intervene` when
    Λ ≥ A and `--toward-benign` when Λ ≤ B.
  - y 208: `likelihood ratio 431 : 1` (16/24, `--ink-2`), with the hint `scam script : benign` (12, `--ink-3`).
    Call it a **likelihood ratio, not odds**: Λ is a log-likelihood ratio.
- **State chip, y 244–268:** label style, padding 4 × 8.

  | State | Style |
  |---|---|
  | `OBSERVE` | 1 px `--line-2`, `--ink-2` |
  | `INTERVENE` | fill `--intervene`, text `--bg-0` |
  | `HOLD` | dashed `--toward-benign` |
  | `DISMISSED` | fill `--toward-benign`, text `--bg-0` |

- **Stage posterior, y 300–420:**
  - Header: `IF THIS IS A SCRIPT · STAGE (under H1)`.
  - Five rows, 20 px apart, from benign to extraction.
  - Each row: name (mono 12, `--ink-2`, x 96); track (x 200–440, 6 px, `--line-2`); fill (`--ink-2`, or
    `--toward-scam` for extraction); value right-aligned at x 496 (mono 12, `ppm/10000` with one decimal and `%`).
- **Ladder card, y 448–640:**
  - Appears 320 ms after INTERVENE with an opacity fade. Panel `--bg-1`, 1 px `--line-1`, padding 16.
  - Content:
    - `INTERVENTION LADDER (§8)`
    - `L1 notify caregiver web view + elder's phone`
    - `L2 trusted circle in priority order · 90 s each`
    - `L3 voice call · deferred, not built` (`--ink-3`, dashed outline)
  - Footer (12, `--ink-3`): `Started by INTERVENE in the deployed system; this replay does not run it.`

### 7.3 Ward lines (crisp in SVG; glow in the shader)

- **A, at `yOfLambda(a_mnats)`, x 560–1824:**
  - Idle: 2 px `--toward-scam` at 40 % opacity. Crossed: 3 px solid `--intervene`.
  - Labels: `A · INTERVENE` (label style) at (560, yA − 10); `+4,554 mn · 95 : 1` (mono 12) right-aligned at
    (1824, yA − 10).
- **B:**
  - Idle: 1 px `--toward-benign` at 35 %.
  - HOLD: 1.5 px dashed `6 4`, with the label `Λ ≤ B · waiting for every watermark (BT-A)`.
  - DISMISS: 2 px solid.
  - Labels: `B · DISMISS` and `−2,986 mn · 1 : 20` at yB + 20.
- **Zero:** a dotted 1 px line in `--ink-3` at 25 %, with the label `Λ = 0 · 1 : 1` at (1824, y0 − 8).
- **Membrane displacement:** during a bounce, the crisp A line is an SVG path of 96 samples across x 560–1824. It
  uses the *same* `wave()` as the shader, exported from `model.js` (§11).

### 7.4 Motion (every effect is a pure function of director time; nothing integrates frame by frame)

- **Row cue:**
  1. The point's x eases from x₍ᵢ₋₁₎ to xᵢ over 240 ms (ease-out-cubic).
  2. If the row has features, one chip per tactic (label style; dashed `--toward-scam` outline, because tactics
     are *claimed*, TL-3) flies from the chip row to the point. Each flight takes 420 ms with ease-in-quad, and
     chips are staggered by 90 ms.
  3. The vertical jump starts when the last chip lands (or at 240 ms if there are no chips).
- **Vertical jump:** a closed-form, critically damped spring: `y(τ) = y1 + (y0 − y1)·(1 + ωτ)·e^(−ωτ)`, with
  `ω = √170 ≈ 13.0384 s⁻¹` (k = 170, c = 2ω, m = 1). It never overshoots. The hero number uses the same curve.
- **Δ label:** `+1,846 mn` (mono 14) at the point + (14, −14). It fades out linearly over 1.2 s.
- **Crossing (INTERVENE cue, `t0` = cue start):**
  - Shock ring at (x of the INTERVENE row, yA): radius `648·easeOutCubic(k)` and alpha `0.45·(1 − k)`, where
    `k = (t − t0)/0.7 s`.
  - `aLit` ramps from 0 to 1 over 160 ms.
  - The state chip switches, and the ladder card fades in at t0 + 320 ms.
- **Bounce (PROOF_REQ cue, τ from cue start):**
  1. **0–0.25 s:** a request chip appears in the chip row: `dismiss_alert · S- · asked by a voice in the room`
     (dashed `--ink-1`, because it is claimed).
  2. **0.25–0.85 s:** a particle of kind 0 falls from (xₚ, yₚ + 20) to (xₚ, yA − 10) with ease-in-quad.
  3. **0.85 s:** impact. `waveA = (xₚ, t_impact, +14 px, 1)`.
  4. **0.85–1.35 s:** the particle rebounds upward by `0.55 ×` its fall distance (ease-out-cubic) while its alpha
     goes from 1 to 0.
  5. **0.95 s:** the stamp fades in over 160 ms.
     - Box: 300 × 56. Text: `BLOCKED · PROOF_REQUIRED` (mono 14, `--intervene`, 1 px border). Below it, the rule
       titles from `WARD.hx` for that row's `hx_rules` (12, `--ink-2`), e.g.
       `HX-01 Open-episode lock · HX-02 Proof origin · HX-09 Simulated effect`.
     - Place it at (xₚ + 28, yA + 22) if it fits within x ≤ 1824; otherwise put it to the left of the impact.
  - **Λ does not move.** The row's delta is 0, and that is the point.
- **Verified callback (verified_plumber, `system.verified_callback` row):**
  1. A particle of kind 1 (`--verified`, filled with a double ring) drops from (xₚ, 140) to the point over 500 ms
     with ease-in-quad.
  2. It then rides the point down the spring (−3,500 mn) and fades over 300 ms.
  3. Label: `verified_official_callback −3,500 mn · only verified terms may be negative (DC-1)`.
- **HOLD and DISMISS:** B line states as in §7.3. On DISMISS, `bLit` ramps from 0 to 1 over 160 ms and the state
  chip becomes `DISMISSED`.
- **Counterpoint tag:** label style, right-aligned at (1824, 136): `COUNTERPOINT · verified_plumber`. The A line is
  reset to idle at the cross-fade.

---

## 8. Act II: BITEMPORAL PLANE (scenario `late_and_lossy`)

### 8.1 Geometry

```
PLANE = { x0: 160, x1: 1520, top: 120, bottom: 840 }       side column: x 1584-1824
R  = max(envelopes.length, max version ts, max non-null version te - 1)       // 13 for late_and_lossy
h  = 720 / R
band(s)    : y in [840 - s*h, 840 - (s-1)*h]        center yc(s) = 840 - (s - 0.5)*h
rel(t)     = t - epoch_ms
V0 = min(rel(env.valid.lo)) - 4000     V1 = max(rel(env.valid.hi)) + 8000
x(v)       = 160 + (v - V0) / (V1 - V0) * 1360    // beyond [V0, V1]: clip at the edge with a 6 px arrow and a label
```

**Axes:**
- **x:** valid time, ticks every 10 s, labelled like the orrery's `fmt_clock` (`+00:20.000`, U+2212 for
  negatives), 12 mono `--ink-3`.
- **y:** belief sequence, one tick per seq (`seq 01`…), at x 96–150.
- **Axis titles:** `VALID TIME · when it happened` at the bottom; `BELIEF · ledger seq, when HEARTHWARD learned
  it` rotated −90° on the left.

### 8.2 Marks

- **Envelope (an instant event):** an I-beam at yc(seq) from x(lo) to x(hi), with a minimum width of 6 px. It is
  2 px with 10 px end caps.
  - **Trust:**
    - Kinds starting with `room.` are dashed (claimed).
    - `system.verified_callback` is `--verified` with double-ring caps.
    - Everything else is solid `--ink-1` at 85 % (observed).
  - **Kind label:** `door.motion` (12 mono, `--ink-2`), 8 px to the right of the beam.
- **Arrival marker:** a circle of radius 3 at (x(rel(received_at)), yc(seq)), joined to the beam's right end by a
  1 px dotted line. Its length *is* the lateness.
  - When it is clipped: `arrived +2:36.742 · attempt 5 · 2 min 34 s late` (attempt number from `INGEST@n`;
    lateness = received_at − valid.hi).
- **Visit version:** a rect from x(vf_lo) to x(vf_hi), spanning y from the bottom of band ts to the bottom of band
  te. When `te` is null, it reaches y 120 with a 40 px fade to transparent.
  - Fill `--ink-2` at 10 %; crisp 1 px `--ink-2` edges, because these bounds are *certain*.
  - **The possibly-extension:** from x(vf_hi) to x(vt), or to x(vf_hi + visit_gap_ms) while `vt` is null. It uses
    a feathered linear gradient from 18 % to 0 % and has no edge line, because it is only *possible*.
  - Label: `v2` (12 mono) at the top-left corner.
- **door_unknown_overlap band:** at the row whose note contains `door_unknown_overlap`, a vertical band over the
  overlap between [visit.lo, visit.hi + visit_gap_ms] and that row's WRIST interval. Fill `--toward-scam` at 14 %,
  feathered edges, label `possibly_overlaps → door_unknown_overlap +700 mn`.
- **Watermark frontiers:** a stepped polyline per lane. For each band s, x = x(rel(W after the last row with
  seq ≤ s)).
  - Dash patterns: door `W_ring` solid 1.5 px; room `W_alexa` `6 4`; wrist `W_bee` dotted `1.5 4` with round caps.
    All are `--ink-2`.
  - Off-plane to the left: a chip at x 164: `← W_ring −72.5 min` (`--ink-3`).
  - Once `flushed`, each frontier sweeps to the right edge over 600 ms, labelled
    `end of stream · W = ∞ (replay only)`.
  - The region to the right of the furthest frontier gets `--synthetic-hatch` at 4 % plus the label
    `may still arrive`.
- **Decision stake:** a horizontal line at the top of band `decision.at_seq` (y = 840 − at_seq·h), x 160–1520,
  1.5 px `--intervene`. Label, right-aligned above it:
  `DECISION · INTERVENE · at_seq 7 · Λ +6,762 mn · head ▦ dbed…77ca`.
- **Revision callouts:** rows whose note contains `after the decision` get a 1 px `--ink-2` connector from the new
  mark to the stake, labelled `revision after the decision · record unchanged (BT-4)`.
- **Duplicate:** a dashed ghost I-beam at the scrubber line that dissolves over 1.2 s, labelled
  `duplicate · absorbed before the ledger (E-6)`.
- **Belief scrubber:**
  - A glass line at y = 840 − S·h: 1 px `--ink-1` with a 12 px `--ink-1` at 6 % band below it.
  - Everything with ts > ⌊S⌋ is drawn at 12 % opacity, and the region above the line is tinted `--bg-0` at 55 %.
  - When S changes, it eases over 300 ms with ease-out-cubic.
- **Side column (x 1584–1824):**
  - `AS OF SEQ` (label style); `07` (readout); `/ 13` (`--ink-3`).
  - Λ as of S (mono 20) and its likelihood ratio (14).
  - State chip.
  - Legend (body, 12/16), one row per mark: I-beam, version rect, feathered "possibly" edge, crisp "certainly"
    edge, the three watermark dash patterns, the stake, and the three trust encodings.

---

## 9. Act III: CRYPTOGRAPHIC ZIPPER (doorstep_scam ledger plus `WARD.signed.attestation`)

### 9.1 Tiles and zipper

- **Tile layout:** 64 × 64 px tiles, 56 px gaps. N = 12 gives a total width of 1384, so x₀ = 268 and
  tileₙ.x = x₀ + (n − 1)·120. If the total width exceeds 1600, scale the tile and gap together. Tiles sit at
  y 240–304.
- **Labels:** the source lane chip at y 222 (label style); `seq 05` at y 328 (12 mono); the short hash
  `9f2c…a71e` at y 346 (12 mono, `--ink-2`).
- **Bits:** each tile is a 16 × 16 grid of 4 px cells drawing the 256 bits of its head.
  - Row r (0–15) uses bytes 2r and 2r + 1. Column c (0–15) uses byte 2r + (c >> 3), bit 7 − (c & 7), MSB first.
  - A 1 bit is `--ink-1` at 85 %; a 0 bit is `--bg-2`. Each tile has a 1 px `--line-2` border, which turns
    `--ok` or `--fail` after a check.
- **Zipper teeth** (in the gap between tile n and tile n + 1; gap centre c = tileₙ.x + 92):
  - Eight teeth, j = 0–7, at y = 240 + 8j + 1, each 6 px tall.
  - Tooth j compares 32-bit word j (bytes 4j to 4j + 3) of `computedHead[n]` with `envelope[n+1].prev`.
  - **Closed:** a left rect [x+68, c+2] and a right rect [c−2, x+116], overlapping by 4 px, `--ink-2` at 70 %.
  - **Open:** a left rect [x+68, c−5] and a right rect [c+5, x+116], leaving a 10 px gap, `--fail`.
- **Background lattice (in every act):**
  - A 16 × 16 grid of 120 × 67.5 px cells, using the same bit mapping, showing the head of the latest ledgered
    row visible in the current act. Act III shows the last computed head.
  - A 1 bit is `--ink-1` at 3.5 %.
  - During Verify, the lattice is *revealed*: alpha(τ) = 0.035 + 0.215·(1 − easeOutCubic(τ/1.2 s)).

### 9.2 Checklist (x 268–1012, y 420–820; body mono 14/20; ✓ in `--ok`, ✗ in `--fail`)

| # | Check | Passes when |
|---|---|---|
| 1 | Signature | `ECDSA P-256 over {n} raw bytes · key_id synthetic-demo-key (not KMS)` |
| 2 | Record | strict HW-JCS: the parsed record re-encodes to the identical signed bytes |
| 3 | Digest | `SHA-256(hw-decision-v1 ‖ 0x00 ‖ bytes)` equals `decision_digest` |
| 4 | Chain | every `contentOk` and every `linkOk` (§13) |
| 5 | Head | `record.ledger_head` equals the replayed head at `at_seq`. Evaluated only if check 4 passes; otherwise it shows `— not evaluated (chain broken)`. |

Below the table, in `--ink-3`:
`params_digest sha256:… · re-derived offline by tools/verify_attestation.py`.

### 9.3 Glass box (x 1076–1652, y 420–820)

- **Header:** `DECISION RECORD · display only (TB-8)`.
- **Rows:** the 11 fields of the record *parsed from the verified bytes*, in key order, 28 px apart. Key in mono
  12 `--ink-3` at x 1076; value in mono 14 `--ink-1` at x 1260.
- **Formatting:**
  - `lambda_mnats`: `+6,066 mn · 431 : 1`.
  - `thresholds_mnats`: `a 4,554 · b −2,986`.
  - Hashes: short form.
  - `reasons`: tactic chips.
- **Hover and focus:** `at_seq` or `belief_seq` rings tile `at_seq`. `ledger_head` rings the same tile and prints
  the full 64-hex digest at y 790.

### 9.4 Tamper and re-chain (real computation, precomputed at load: §13)

- **Choice of entry:** `k = signed.at_seq − 3` (5).
- **Tamper:** `received_at += 1` on envelope k in a deep copy. Tile k's head changes, link k → k+1 opens, and check
  4 fails with `seq k: head does not match its contents`.
- **Re-chain:** the attacker recomputes head k, then for n > k rewrites `prev = head[n−1]` and recomputes
  `head[n]`.
  - Every link closes, and tiles k to N re-scramble.
  - Check 4 passes, but check 5 fails with `signed ledger_head ≠ replayed head at seq 8`, and tile 8 is ringed in
    `--fail`.
  - The signature still verifies: it signs the record, and the record names the original head. That is the lesson.
- **Restore:** everything returns, and checks 1 to 5 all pass.

### 9.5 Final card (centred; `--bg-0` at 92 % over everything)

Three lines in 32/40 `--ink-1`, at y 420, 468 and 516:

- `Evidence of danger passes freely.`
- `Lowering protection needs proof.`
- `Every decision replays to its ledger head.`

Then, at y 600 in 14/20 `--ink-2`:
`HEARTHWARD · synthetic demo data (orrery seed 7) · signature: synthetic demo key, not KMS`.

---

## 10. `director.js`: the 90-second choreography

### 10.1 Purity and clock

- **API:** `buildTimeline(ward)` and `frameAt(timeline, ward, crypto, tMs, {motion})`.
  - Both are pure; `frameAt` returns a JSON-serializable `Frame`.
  - The same inputs always give a deep-equal output.
  - No DOM, no `Date`, no `Math.random`.
- **Clock (`main.js`):** while playing, `t += min(now − last, 50)` ms on each `requestAnimationFrame`, where `now`
  comes from the rAF timestamp. `t` is clamped to [0, 90000], and the clock pauses at 90000. Frame sampling may
  vary, but the content at any t never does.
- **Crypto results:** `chain.precompute(ward)` runs once at load. It returns intact, tampered and re-chained
  replays plus the check lines, and the result is frozen. The Play button reads `verifying…` and stays disabled
  until it resolves. The director only *reveals* these real results at scheduled times. A manual `V` or `T` runs
  live and overrides the chain view until the next seek.

### 10.2 Row scheduling inside a window

Each row has a weight w. A row's duration is `floor(window·w/Σw)` ms; the remainder goes to the last row. Cue
start times are cumulative.

| Row | Weight |
|---|---|
| door `INGEST` | 0.6 |
| wrist `INGEST`, unquoted label | 0.5 |
| wrist `INGEST`, quoted utterance | 1.6 + 0.12·(word count) |
| room `INGEST` | 2.0 |
| system `INGEST` | 3.0 |
| `INTERVENE` | 2.6 |
| `PROOF_REQ` | 4.0 |
| `HOLD`, `DISMISS`, `INCONCLUSIVE` | 2.4 |
| `DUPLICATE`, `QUARANTINE`, `REJECTED` | 2.0 |
| `/^INGEST@\d+$/`, `INGEST+SYNC` | 3.2 |
| `VISIT_CLOSED` | 1.2 |
| `AS_OF` | 1.8 |
| `END` | 1.0 |

### 10.3 Timeline (ms; every row index is found from the data, never hard-coded)

| Window | Content |
|---|---|
| **0–1,600** | Act I intro. The ward lines draw in from x 560 (A, then B, 600 ms each, ease-out-cubic). The point fades in at Λ = 0. |
| **1,600–23,000** | doorstep_scam rows from index 0 through the first `PROOF_REQ`. |
| **23,000–26,000** | Hold on the BLOCKED stamp. |
| **26,000–26,600** | Cross-fade to the counterpoint. |
| **26,600–34,400** | verified_plumber rows from index 0 through `DISMISS`. |
| **34,400–35,000** | Hold. |
| **35,000–36,200** | Act II cross-fade. |
| **36,200–54,000** | late_and_lossy rows through `INGEST+SYNC`. S follows each row's seq; rows without a seq keep S. |
| **54,000–58,000** | `VISIT_CLOSED`, `AS_OF`, `END`: S goes to R, then the 600 ms watermark flush sweep. |
| **58,000–62,000** | Rewind S from R to `decision.at_seq` over 1,200 ms (ease-in-out-cubic), then hold. |
| **62,000–65,000** | Hold. |
| **65,000–66,200** | Act III cross-fade. |
| **66,200–72,000** | Verify. Tile n lights at 66,400 + 80·(n−1). Lattice reveal at 66,400. Checks 1–5 appear at 67,800, 68,200, 68,600, 69,000 and 69,400, each fading in over 160 ms. Glass box at 69,800. |
| **72,000–77,000** | Tamper. Diff chip on tile k at 72,400: `received_at +1 ms`. Tile k cross-dissolves to its computed bits over 400 ms. Teeth k→k+1 open from 72,800, 40 ms apart. Check 4 turns ✗ at 73,400. |
| **77,000–82,500** | Re-chain. Tiles k to N re-scramble from 77,400, 120 ms apart, and teeth close as each tile updates. At 79,400 check 4 turns ✓, check 5 turns ✗, and tile `at_seq` gets a `--fail` ring. |
| **82,500–85,500** | Restore. Tiles cross-dissolve back over 400 ms; all checks turn ✓ at 83,200. |
| **85,500–90,000** | Final card fades in over 600 ms. |

### 10.4 Keys (`keyAction(key, {shiftKey})` is pure; `main.js` applies it)

**Key handling rules:**
- Ignore any key pressed with Ctrl, Meta or Alt.
- Call `preventDefault` for Space and the arrow keys.
- Ignore `event.repeat` for every key except the arrows.

| Key | Action |
|---|---|
| Space | Play or pause. At 90 s, restart from 0. |
| 1 / 2 / 3 | Seek to 0 / 35,000 / 65,000 ms, keeping the play state |
| ← / → | Seek to the previous or next cue start, and pause |
| Shift + ← / → | Move ∓1,000 ms, and pause |
| R | Seek to 0, pause, and clear any manual tamper |
| V | Act III only: re-run verification live, animated |
| T | Act III only: cycle tamper → re-chain → restore |
| H | Hide or show the buttons and key hints |
| M | Toggle reduced motion |
| D | Toggle the debug overlay (§12) |
| F | Toggle fullscreen on `#stage` |

**URL parameters:**

| Parameter | Effect |
|---|---|
| `?t=<seconds>` | Seek and pause there (for deterministic stills) |
| `?autoplay=1` | Start playing |
| `?gl=0` | Force the Canvas 2D fallback |
| `?motion=0` | Force reduced motion |
| `?hints=0` | Start with the buttons and hints hidden |

---

## 11. `ward.gl.js`: the light layer (WebGL2, one pass)

**Interface:** `createGlowRenderer(canvas) → {kind: "webgl2", draw(frame), resize(cssW, cssH, scale), dispose()}`.
It returns `null` when WebGL2 is unavailable or a shader fails to compile. `fallback.c2d.js` exports the same
interface with `kind: "canvas2d"`.

**Context and canvas:**
- `getContext("webgl2", {alpha: false, antialias: false, premultipliedAlpha: false, preserveDrawingBuffer: false})`.
- Backing store: `round(1920·s·dpr·q) × round(1080·s·dpr·q)`, with q = 0.5 by default, upscaled by CSS.
- Draw one full-screen triangle from `gl_VertexID` with an empty VAO.
- Handle `webglcontextlost` by switching to the fallback.

**Uniforms:** fill from `frame`. Colours are display sRGB copied from the CSS tokens; static tests compare them.

```glsl
#version 300 es
const vec2 V[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
void main() { gl_Position = vec4(V[gl_VertexID], 0.0, 1.0); }
```

```glsl
#version 300 es
precision highp float;
uniform vec2  u_res;        // drawing-buffer px
uniform float u_t;          // director time, s
uniform float u_fade;       // Act I light opacity, 0..1 (0 in Acts II and III: background only)
uniform vec2  u_field;      // ward x extent (560, 1824)
uniform float u_yA, u_yB;   // ward lines, stage px
uniform float u_aLit, u_bLit;
uniform vec3  u_cross;      // crossing x, y, t0 (t0 < 0: none)
uniform vec4  u_waveA, u_waveB;  // impact x0, t0, amplitude px, enabled
uniform vec2  u_point;
uniform vec2  u_trail[64];
uniform int   u_trailN;
uniform vec4  u_part[8];    // x, y, kind (0 S- request, 1 verified, 2 tactic), alpha
uniform int   u_partN;
out vec4 outColor;

const vec2 STAGE = vec2(1920.0, 1080.0);
const vec3 BG   = vec3(0.0275, 0.0353, 0.0471);  // --bg-0
const vec3 INK  = vec3(0.902, 0.929, 0.953);     // --ink-1
const vec3 SCAM = vec3(0.949, 0.639, 0.227);     // --toward-scam
const vec3 HOT  = vec3(1.000, 0.365, 0.310);     // --intervene
const vec3 SAFE = vec3(0.180, 0.769, 0.714);     // --toward-benign
const vec3 VER  = vec3(0.557, 0.635, 1.000);     // --verified

float glow(float d, float w) { return exp(-(d * d) / (w * w)); }
float segDist(vec2 p, vec2 a, vec2 b) {
  vec2 ab = b - a;
  float h = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-4), 0.0, 1.0);
  return length(p - a - h * ab);
}
float wave(vec4 w, float x) {               // membrane displacement in px, + is down
  float dt = u_t - w.y;
  if (w.w < 0.5 || dt < 0.0) return 0.0;
  float u = (x - w.x) / 60.0;               // u*u, never pow(): pow of a negative base is undefined in GLSL
  return w.z * exp(-u * u) * exp(-dt / 0.35) * cos(18.849556 * dt);   // 2π · 3 Hz
}
float hash12(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main() {
  vec2 p = vec2(gl_FragCoord.x, u_res.y - gl_FragCoord.y) * (STAGE / u_res);
  vec3 light = vec3(0.0);
  vec3 hot = mix(SCAM, HOT, u_aLit);
  if (p.x >= u_field.x && p.x <= u_field.y) {
    float flash = u_cross.z >= 0.0 ? exp(-max(u_t - u_cross.z, 0.0) / 0.7) : 0.0;
    light += hot  * glow(abs(p.y - (u_yA + wave(u_waveA, p.x))), mix(24.0, 40.0, flash)) * mix(0.30, 0.85, u_aLit);
    light += SAFE * glow(abs(p.y - (u_yB + wave(u_waveB, p.x))), 20.0) * mix(0.22, 0.75, u_bLit);
  }
  float trail = 0.0;
  for (int i = 0; i < 63; i++) {
    if (i + 1 >= u_trailN) break;
    float age = float(i + 1) / float(max(u_trailN - 1, 1));        // 0 oldest .. 1 newest
    trail = max(trail, (0.35 + 0.65 * age) * glow(segDist(p, u_trail[i], u_trail[i + 1]), 10.0));
  }
  light += hot * trail * 0.55;
  float dp = length(p - u_point);
  light += INK * (0.9 * glow(dp, 18.0) + (1.0 - smoothstep(6.0, 7.5, dp)));  // never smoothstep(7.5, 6.0, …): undefined
  if (u_cross.z >= 0.0) {
    float k = (u_t - u_cross.z) / 0.7;
    if (k >= 0.0 && k <= 1.0) {
      float r = 648.0 * (1.0 - (1.0 - k) * (1.0 - k) * (1.0 - k));
      light += HOT * glow(abs(length(p - u_cross.xy) - r), 6.0) * 0.45 * (1.0 - k);
    }
  }
  for (int i = 0; i < 8; i++) {
    if (i >= u_partN) break;
    vec4 q = u_part[i];
    vec3 col = q.z < 0.5 ? INK : (q.z < 1.5 ? VER : SCAM);
    float d = length(p - q.xy);
    float ring = (q.z > 0.5 && q.z < 1.5) ? glow(abs(d - 16.0), 1.5) : 0.0;
    light += col * q.w * (0.8 * glow(d, 12.0) + ring);
  }
  vec2 uv = p / STAGE - 0.5;
  float vignette = 1.0 - 0.30 * smoothstep(0.45, 0.85, length(uv * vec2(1.0, 0.75)) * 1.4);
  vec3 color = (BG + light * u_fade) * vignette;
  color += (hash12(gl_FragCoord.xy) - 0.5) / 255.0;   // static dither: no banding, no shimmer after video compression
  outColor = vec4(clamp(color, 0.0, 1.0), 1.0);
}
```

**`model.js` mirrors** `wave()` (with the same constants 60, 0.35 and 3 Hz), the spring and the easing curves.
That way the crisp SVG and the glow always agree.

---

## 12. Fallback, reduced motion, governor, idle

- **`fallback.c2d.js`:**
  - Fill `--bg-0`, then draw the vignette as a radial gradient.
  - Each glow (ward lines, trail) is three stacked strokes: widths 24, 12 and 4 px at alpha 0.06, 0.12 and 0.5,
    multiplied by the lit factor.
  - The point is a radial gradient of radius 18.
  - The shock ring is `arc` with `lineWidth` 6 and the same alpha curve.
  - Particles are radial gradients.
  - No dither. It reads the same `frame`.
- **Reduced motion** (`prefers-reduced-motion: reduce`, `?motion=0` or `M`): `frameAt` gets `motion: false` and
  then:
  - springs become steps, and chips and particles appear in place;
  - wave amplitude is 0;
  - the shock ring is replaced by the line's colour change alone;
  - cross-fades become cuts at the window midpoint;
  - scrubber sweeps step once per cue.

  The timeline itself is unchanged.
- **Governor** (while playing):
  1. Keep the last 60 rAF intervals.
  2. If their mean stays above 22 ms for 2 s, drop q from 0.5 to 0.35.
  3. If it happens again, switch to `fallback.c2d.js` for the rest of the session.
  4. Never step back up (that would oscillate).
- **Debug overlay (`D`):** the renderer kind, q, and the mean interval in ms with its method: `mean of the last 60
  requestAnimationFrame intervals`. No frame-rate figure appears anywhere else (D-5).
- **Idle:** when paused and no effect is in progress, stop the rAF loop; the canvas keeps its last frame. Restart
  on any input. Pause on `document.hidden`.

---

## 13. `chain.js`: exact crypto (pure, DOM-free; Node ≥ 20 and browsers)

### 13.1 The hashing rule

- **Signatures cover the raw canonical bytes.** Production signs with KMS `Sign` using `MessageType="RAW"` and
  `SigningAlgorithm="ECDSA_SHA_256"` (`src/hearthward/attest/sign.py:53`). KMS hashes the message itself with
  SHA-256, and WebCrypto's `verify({name:"ECDSA", hash:"SHA-256"})` does the same. So pass WebCrypto **exactly the
  decoded `bytes_b64`**:
  - not the tagged bytes;
  - not a digest;
  - not a re-serialization.

  Check the signature first and parse second (C-5, C-6).
- **Domain-separated tags are for identifiers only (C-7).**
  - `decision_digest = "sha256:" + hex(SHA-256(utf8("hw-decision-v1") ‖ 0x00 ‖ bytes))`
  - `head_n = "sha256:" + hex(SHA-256(utf8("hw-ledger-v1") ‖ 0x00 ‖ JCS(envelope_n without "head")))`
  - Ledger heads are the one place where JSON is canonicalized again, because the chain is defined over it (§4.2).
- **A test pins the rule:** `verify(bytes)` is true, and verifying the tagged bytes, or the bytes with any single
  bit flipped, is false.

### 13.2 Code: use exactly these and keep their behaviour

```js
const encoder = new TextEncoder();
export const ZERO_HEAD = `sha256:${"0".repeat(64)}`;
const KEY = /^[a-z0-9_]+$/;

export function b64ToBytes(text) {
  if (typeof text !== "string" || text.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(text)) {
    throw new Error("not base64");
  }
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
}
export const bytesToHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
export const headBytes = (head) => Uint8Array.from(head.slice(7).match(/../g), (h) => parseInt(h, 16));

// HW-JCS (C-2): keys ^[a-z0-9_]+$ sorted by UTF-16 code unit, safe integers only, no floats.
export function jcsText(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error(`HW-JCS: ${value} is not a safe integer`);
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(jcsText).join(",")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    for (const k of keys) if (!KEY.test(k)) throw new Error(`HW-JCS: key ${JSON.stringify(k)} is outside the profile`);
    return `{${keys.map((k) => `${JSON.stringify(k)}:${jcsText(value[k])}`).join(",")}}`;
  }
  throw new Error(`HW-JCS: unsupported ${typeof value}`);
}
export const jcs = (value) => encoder.encode(jcsText(value));

export async function sha256(...parts) {
  const joined = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const part of parts) { joined.set(part, at); at += part.length; }
  return new Uint8Array(await crypto.subtle.digest("SHA-256", joined));
}
export async function taggedDigest(tag, bytes) {
  return `sha256:${bytesToHex(await sha256(encoder.encode(tag), Uint8Array.of(0), bytes))}`;
}
export async function ledgerHead(envelope) {
  const { head, ...body } = envelope;
  return taggedDigest("hw-ledger-v1", jcs(body));
}

// KMS Sign (ECDSA_SHA_256) returns a DER ECDSA-Sig-Value: SEQUENCE { r INTEGER, s INTEGER }.
// WebCrypto verifies the IEEE P1363 form: r and s as 32-byte big-endian integers, concatenated (64 bytes).
// A P-256 DER signature is at most 72 bytes, so every length is in short form; reject anything else.
export function derToRaw(der) {
  const fail = (why) => { throw new Error(`DER signature: ${why}`); };
  const tlv = (buf, at) => {
    if (at + 2 > buf.length) fail("truncated");
    const len = buf[at + 1];
    if (len & 0x80) fail("long-form length");
    if (at + 2 + len > buf.length) fail("truncated");
    return { tag: buf[at], body: buf.subarray(at + 2, at + 2 + len), end: at + 2 + len };
  };
  const int32 = (body) => {
    if (body.length === 0) fail("empty INTEGER");
    if (body[0] & 0x80) fail("negative INTEGER");
    if (body.length > 1 && body[0] === 0x00 && !(body[1] & 0x80)) fail("non-minimal INTEGER");
    const v = body[0] === 0x00 ? body.subarray(1) : body;
    if (v.length === 0) fail("zero INTEGER");
    if (v.length > 32) fail("INTEGER longer than 32 bytes");
    const out = new Uint8Array(32);
    out.set(v, 32 - v.length);
    return out;
  };
  const seq = tlv(der, 0);
  if (seq.tag !== 0x30 || seq.end !== der.length) fail("not one SEQUENCE");
  const r = tlv(seq.body, 0);
  const s = tlv(seq.body, r.end);
  if (r.tag !== 0x02 || s.tag !== 0x02 || s.end !== seq.body.length) fail("not SEQUENCE { INTEGER, INTEGER }");
  const raw = new Uint8Array(64);
  raw.set(int32(r.body), 0);
  raw.set(int32(s.body), 32);
  return raw;
}

const ECDSA_P256 = { name: "ECDSA", namedCurve: "P-256" };
export async function verifySignature(spkiB64, signed) {
  const bytes = b64ToBytes(signed.bytes_b64);       // the exact signed bytes (C-6)
  const key = await crypto.subtle.importKey("spki", b64ToBytes(spkiB64), ECDSA_P256, false, ["verify"]);
  const ok = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, derToRaw(b64ToBytes(signed.sig_b64)), bytes);
  return { ok, bytes };
}
```

### 13.3 Also export

- **`replayChain(envelopes)`:** returns `[{seq, recordedHead, computedHead, contentOk, linkOk}]`.
  - `contentOk = computedHead === env.head`.
  - `linkOk = env.prev === (seq === 1 ? ZERO_HEAD : previous computedHead)`.
  - The zipper teeth between n and n+1 compare `computedHead[n]` with `env[n+1].prev`.
- **`checkDecision(attestation, replay)`:** returns the five check lines of §9.2, in the C-6 order: signature;
  then strict parse (`JSON.parse`, then `jcs()` must re-encode to bytes identical to the signed bytes); then digest;
  then chain; then head.
- **`tamper(envelopes, k)`** and **`async rechain(envelopes, k)`:** both work on deep copies (`structuredClone`) as
  defined in §9.4.
- **`async precompute(ward)`:** returns a frozen `{k, intact, tampered, rechained}`, where each value holds its
  replay and its checks.

---

## 14. `model.js`: pure state and mappings

- **Constants and mappings:** export `STAGE`, `FIELD`, `LAMBDA`, `PLANE`, `yOfLambda`, `xOfAt`, the plane mappings,
  `chainLayout(n)`, `tileBits(head)` (16 × 16 booleans), `wave`, `spring` and the easing curves.
- **`stateAt(scenario, rowIndex)`:** reads `rows[0..rowIndex]` only (BT-3) and returns
  `{seq, lambdaMnats, state, posteriorPpm, watermarks, flushed, row}`.
  - The state machine: starts at `OBSERVE`; `INTERVENE`, `HOLD`, `DISMISS` and `INCONCLUSIVE` rows set
    `INTERVENE`, `HOLD`, `DISMISSED` and `INCONCLUSIVE`.
- **`walkPoints(scenario, from, to)`:** the step polyline of §7.1.
- **`planeView(scenario, S)`:** returns every mark of §8.2 with its geometry and opacity.
- **`lrLabel(m)`:**
  - For m ≥ 0: `${round(e^(m/1000))} : 1`.
  - For m < 0: `1 : ${round(e^(−m/1000))}`.
  - `en-US` grouping. 4554 gives `95 : 1`, −2986 gives `1 : 20`, 0 gives `1 : 1`, and 8256 gives `3,851 : 1`.
- **Formatters:** `formatMnats` (`+6,066`, `−2,986`, `0`), `fmtClock(relMs)`, `shortHash` (`sha256:9f2c…a71e`),
  `humanFeature` (`remote_access` → `remote access`, `info_request` → `information request`), `joinList`
  (`a, b and c`).
- **`sentenceFor(context)`:** see §16.

---

## 15. `index.html`, `main.js`, `wardline.css`

**`index.html`:**

```html
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'">
```

- **Scripts and styles:** exactly one `<script type="module" src="main.js">` and one stylesheet. No inline scripts
  or styles and no `style=` attributes, because the CSP blocks them. Set styles from JS through `element.style.*`
  or SVG presentation attributes, never `setAttribute("style", …)`.
- **Structure:** `#stage` holds `canvas#glow`, `svg#ink`, the HUD sections, `#card` and a visible transcript
  `ol#transcript` (the sentence history, `aria-live="polite"`) beside the stage.
- **Accessibility:** the canvas and SVG are `aria-hidden`, because the DOM carries every number and sentence.
- **Controls:** buttons are real `<button>`s with visible focus rings (2 px `--ink-1`, offset 2 px).
- **`<noscript>`:** explains that the page needs JavaScript and WebGL2 or Canvas 2D.

**`main.js`:**
1. Import `WARD`.
2. Build the timeline and `await chain.precompute(WARD)`.
3. Create the renderer (WebGL2 unless `?gl=0`, otherwise the fallback).
4. Wire resize and scaling, the clock and the keys.
5. Apply each frame to the canvas, the SVG and the HUD.

**Serving:** ES modules do not load from `file://`. Serve with
`uv run python -m http.server 8765 --bind 127.0.0.1 --directory web` and open
`http://127.0.0.1:8765/wardline/`.

---

## 16. Sentences (`sentenceFor`; at most 120 characters; placeholders filled from data)

| Context | Template |
|---|---|
| Act I intro | `HEARTHWARD watches one household's door, room and wrist, and decides with an explicit error budget.` |
| First door row | `The front door reported {label}. Synthetic Ring event.` |
| Later door row | `Ring: {label}. The visit's valid time widens; no version is overwritten (BT-1).` |
| Conversation starts | `A conversation began near the resident. Synthetic Bee stream.` |
| Utterance with tactics | `Heard {features}. Evidence {Δ} mn → likelihood ratio {LR}.` |
| Utterance without tactics | `No scam tactic in that line. Evidence {Δ} mn.` |
| Room claim | `The resident asked Alexa about the visitor. A claim adds only tactics not yet heard (DC-2).` |
| INTERVENE | `Λ crossed A at {LR}. HEARTHWARD intervenes and alerts the trusted circle.` |
| PROOF_REQ | `A voice asked Alexa to dismiss the alert. Lowering protection needs a circle member's passkey.` |
| Counterpoint intro | `Counterpoint: a real plumber, confirmed by calling the official number (verified_plumber).` |
| Verified callback | `The official number confirmed the visit. Only a verified fact may pull evidence down (TL-1).` |
| HOLD | `Λ is below B, but dismissal waits until every source's watermark passes the episode (BT-A).` |
| DISMISS | `Every watermark passed. The episode is dismissed.` |
| Act II intro | `Same scam on a harsher network: a Ring retry, a duplicate, a lost Bee line (late_and_lossy).` |
| DUPLICATE | `Ring sent the same event twice. The duplicate is absorbed before the ledger (E-6).` |
| `INGEST@n` | `A Ring event arrived {late} late, on attempt {n}. It revises belief, never the decision (BT-4).` |
| `INGEST+SYNC` | `A lost Bee line came back through sync. Λ moves; the signed decision does not (BT-4).` |
| VISIT_CLOSED | `The Ring watermark passed, so the visit closes (vt {vt}).` |
| AS_OF | `Belief at seq {a}: {then}. Belief now: {now}. Both versions stay on record (BT-1).` |
| END | `End of stream (replay only): every watermark goes to ∞ and everything settles.` |
| Rewind | `As of seq {a}: exactly what HEARTHWARD knew when it decided. Replay reproduces it (BT-2, BT-3).` |
| Act III intro | `The ledger behind the decision: {n} hashes, each binding everything before it (§4.2).` |
| Verify | `Every head re-derives from its envelope, and the signed decision names the head at seq {a}.` |
| Tamper | `One millisecond changed in seq {k}. Its head no longer matches, and the zipper opens.` |
| Re-chain | `Re-chaining hides the break, but the signed record still names the original head at seq {a}.` |
| Restore | `Restored. The chain and the signature verify again.` |

---

## 17. Tests (`node:test`, no dependencies; `web/` already has `"type": "module"`)

**`tests/chain.test.js`:**
- **`derToRaw` on the shipped signature:** returns 64 bytes, and re-encoding it to DER gives back the original.
  Write a small test-only `rawToDer` that mirrors `encode_signature` in `tools/verify_attestation.py`.
- **`derToRaw` rejects:**
  - a trailing byte;
  - a wrong outer tag;
  - a long-form length;
  - a non-minimal INTEGER (`02 02 00 01`);
  - a negative INTEGER (first byte ≥ 0x80 with no `00` pad);
  - a 33-byte value;
  - a zero INTEGER.
- **The hashing rule of §13.1:**
  - `verifySignature(attestation)` is true.
  - Verifying the tagged bytes, and verifying with any one of 16 sampled bits flipped, is false.
- **Chain and decision checks:**
  - `replayChain` of every scenario: every `contentOk` and every `linkOk`.
  - `taggedDigest("hw-decision-v1", bytes)` equals `attestation.decision_digest`.
  - The strict round-trip check of the record passes.
- **Tamper and re-chain:**
  - `tamper(…, k)` breaks exactly `contentOk[k]` and the link into k + 1.
  - `rechain` restores every link but changes heads k to N, and `checkDecision` fails only check 5.
- **`jcsText` throws** on `1.5`, `2**53`, `NaN`, and a key such as `"Bad-Key"`.

**`tests/model.test.js`:**
- The mapping values of §7.1, to within 0.01.
- The `lrLabel` cases of §14.
- `posteriorPpm` sums to 1,000,000 wherever it is non-null.
- **BT-3 property:** for every i, `stateAt` on rows truncated to `[0..i]` equals `stateAt` on the full data at i.
- `sentenceFor` is at most 120 characters for every row of every scenario and for every scripted context.

**`tests/director.test.js`:**
- The duration is 90,000, and the acts start at 0, 35,000 and 65,000.
- Every scheduled cue lies inside its window, and the durations of each window sum exactly to it.
- `frameAt` called twice at 200 sampled times gives deep-equal results, and `JSON.stringify(frame)` is stable.
- The `keyAction` table of §10.4 is honoured.
- **Reduced motion:** `motion: false` produces no wave and no shock.

**`tests/static.test.js`:**
- **`index.html`:** has the CSP meta, exactly one module script (`main.js`), and no inline `<script>`, `<style>`,
  `style=` or `on*=` attributes.
- **Imports and network:** every import specifier in `web/wardline/*.js` starts with `./`. There are no
  `http(s)://` URLs except the SVG namespace, and no `fetch(`, `XMLHttpRequest`, `WebSocket` or `eval(`.
- **Banned strings:**
  - `60 fps` / `60 FPS` appear nowhere.
  - `SYNTHETIC DEMO DATA` appears in `index.html`.
  - `data.js` is ASCII with LF only, starts with the GENERATED header, and contains no `PRIVATE` or `BEGIN`.
- **Colour consistency:** every GLSL colour constant in `ward.gl.js` equals the matching CSS token in
  `wardline.css` (hex / 255, to 3 decimals). The `wave` constants 60, 0.35 and 3 Hz appear in both `model.js` and
  `ward.gl.js`.

**Run, in Bash:**

```bash
uv run python web/wardline/export_data.py --check
cd web && node --test wardline/tests/*.test.js && npm test
uv run python tools/pipeline.py --only F
uv run python tools/pipeline.py --only G6
```

`npm test` auto-discovers `**/*.test.js`, so it also runs the existing tests. Both pipeline runs must pass exactly
as they did before.

---

## 18. Browser verification (the built-in browser)

1. **Start the server** in the background (Bash `run_in_background`):
   `uv run python -m http.server 8765 --bind 127.0.0.1 --directory web`.
   Do **not** create `.claude/launch.json`: it lies outside the write boundary.
2. **Open the page:** `http://127.0.0.1:8765/wardline/` with the browser tool's URL mode.
3. **Read the console.** It must show no errors and no warnings from Wardline.
4. **Take stills at 1920 × 1080** with `?t=` at each time below, plus `?gl=0` and `?motion=0` variants at 21 s and
   75 s:

   | `?t=` | Expect |
   |---|---|
   | 3 | Ward lines and the point at Λ = 0 |
   | 12 | Tactic chips in flight |
   | 18 | Λ above A, the shock ring, INTERVENE, the ladder card |
   | 22 | BLOCKED stamp and the membrane |
   | 30 | Indigo verified particle |
   | 33 | B dismissed |
   | 44 | Plane, duplicate ghost |
   | 52 | Late Ring arrival, top-left |
   | 57 | Watermark flush |
   | 61 | As of seq 7 |
   | 70 | All checks ✓ |
   | 75 | Zipper open at k |
   | 80 | Check 5 ✗ |
   | 84 | Restored |
   | 88 | Final card |

5. **Play once end to end** with Space, and try each key in §10.4.
6. **Stop the server** when you are done.

---

## 19. Build order

1. Preflight: read §2, run the §3 probe, and note any differences.
2. `export_data.py`, `data.js` and `.gitattributes`; `--check` passes and two runs produce identical bytes.
3. `chain.js` and `chain.test.js`. This is the riskiest part, so it goes first.
4. `model.js` and `model.test.js`.
5. `director.js` and `director.test.js`.
6. `index.html`, `wardline.css`, `main.js`, `plane.svg.js` and `fallback.c2d.js`: the complete Canvas 2D path
   working end to end.
7. `ward.gl.js` and the governor.
8. `static.test.js`, the full test run, and browser verification.
9. `README.md`, covering how to serve the page, the keys, `?t=` stills, recording tips (`H` hides hints), the
   synthetic-data disclosure, and that the signature uses a synthetic demo key, not KMS.

---

## 20. Acceptance criteria and the report

**Acceptance criteria (all must hold):**
- `git status --porcelain` lists only `web/wardline/**`.
- Every command in §17 passes, with output.
- Browser checks: no console errors; every still in §18 matches; WebGL2, Canvas 2D and reduced-motion paths all
  render.
- Every on-screen number traces to `data.js` or to a formula in this brief.
- The honesty rules of §1 hold in every act.

**The report:**
- The files created.
- The tail of each test command's output.
- The stills taken.
- Any conflicts between this brief and the repo, and how you resolved them.
- What you could not verify, for example frame time on real GPU hardware. State nothing about speed that you did
  not measure.

Do not commit.
