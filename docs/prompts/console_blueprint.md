# HEARTHWARD Console of Verifiable Claims: Master Architectural Blueprint (Proposal Z)

Version 0.1 · 2026-10-22 · This document is the single source of truth for the console build session.
It sits below `docs/master_blueprint.md` and `CLAUDE.md`: if they conflict, those win, and the conflict is reported.

---

## 1. Executive architectural summary

### 1.1 Thesis

A dashboard mockup **asserts** state: "KMS: ONLINE", "Sensors: 3/3".

The Console of Verifiable Claims **demonstrates** state:

- Every screen is one of five claims HEARTHWARD makes.
- Every claim opens into its evidence, then into the raw bytes behind that evidence, then into a way for the judge
  to check it themselves: in the browser, against the deployed stack, or in a terminal.
- Nothing on screen is a hard-coded status.
- A value the page cannot compute, fetch or cite is not shown.

### 1.2 Who it is for

The judges are AWS Principal Solutions Architects, enterprise cloud architects and Devpost technical judges, and
they look for production architecture, real cloud and security integration, and no mocks. The console answers each
directly:

| What a judge asks | What the console answers with |
|---|---|
| Is the AWS part real? | Claim 5 shows the deployed stack. Its probes are RECORDED (by a named tool, at a stated time) and LIVE (run from the judge's browser through CloudFront). It also gives a KMS-signed decision that the judge verifies themselves, and `curl` commands for their own terminal. |
| Is the security design sound? | Claims 2 and 4: the S+/S- asymmetry with a passkey-bound action hash; a hash-chained ledger; ECDSA P-256 over raw canonical bytes. |
| Is the maths real or decorative? | Claim 1's decision sandbox runs a browser port of the decision core, and a pytest conformance test keeps it equal to the Python reference to the milli-nat. |
| Does it handle real-world data mess? | Claim 3: late, duplicate and recovered events change what is believed, never a decision. |
| Can I find my way in 15 seconds? | A one-sentence header, one claim per view, and two buttons that let a judge run a real cryptographic check. |

### 1.3 Provenance badges

Every rendered value carries at least one badge. A panel without a badge fails `tests` (§6.3).

| Badge | Meaning | May be used only when |
|---|---|---|
| `[ LIVE ]` | Fetched now from the deployed stack | a same-origin request made in this page load returned it; the badge shows the time |
| `[ COMPUTED ]` | Computed in this browser from shipped bytes | the value is the output of code running now (WebCrypto, the HMM port, chain replay) |
| `[ RECORDED ]` | Captured earlier by a named tool | the artifact names its tool, its time (UTC) and the commit it ran at |
| `[ SYNTHETIC ]` | Produced by the orrery, seed 7 | always paired with one of the badges above; every DOOR and WRIST datum carries it (D-1) |

### 1.4 Truth-in-advertising rules

| Rule | Statement |
|---|---|
| **T-1** | No string literal in console code states a system status, such as `ONLINE`, `HEALTHY`, `ENCLAVE` or `ACTIVE`. Status words come only from data or computation, and a test scans for them. |
| **T-2** | No performance number appears unless it was measured, with its method stated (D-5). Never write "60 FPS". |
| **T-3** | Ring and Bee are synthetic-only (§1 of the master blueprint). No device model names, no "hardware mesh", no "BLE", no "intercept". |
| **T-4** | Built-but-not-deployed and deferred components are labelled as such. Today these are: the broker, the ladder, the anchor bucket, and L3. |
| **T-5** | A real KMS signature over a synthetic record is labelled exactly that way. A synthetic-key signature is labelled `synthetic-demo-key (not KMS)`. |
| **T-6** | The browser never makes an authorization decision. Verification is display-only (TB-8). |
| **T-7** | Λ is a log-likelihood ratio, so the UI says "likelihood ratio 431 : 1", never "odds". |

---

## 2. System topology and directory structure

### 2.1 Layout

```
web/
  index.html                 -> the console shell (replaces the radar in Phase 7)
  console/
    app.js                   entry: boot, store, router, render loop, keys
    store.js                 single store, actions, pure selectors, URL <-> state
    provenance.js            badge model and rendering helpers; the T-1 status-word guard
    claims/
      walk.js                Claim 1 view + decision sandbox
      asymmetry.js           Claim 2 view
      bitemporal.js          Claim 3 view (uses ../wardline/plane geometry via model.js)
      integrity.js           Claim 4 view (zipper, ledger table, tamper, re-chain)
      boundaries.js          Claim 5 view (architecture map, probes, Ring HMAC sandbox)
    inspector.js             right rail: three tabs (§5)
    hmm.js                   browser port of src/hearthward/decision/hmm.py lambda_trace_mnats
    hmac.js                  WebCrypto HMAC-SHA256 helpers for the Ring sandbox
    console.css              tokens (shared values with wardline.css), layout, breakpoints
    data/
      console_data.js        GENERATED by tools/export_console_data.py (synthetic scenarios, fixtures, spec)
      bundle.kms.json        RECORDED by tools/sign_console_bundle.py (human-run; absent until Phase 0)
      probe.recorded.json    RECORDED by tools/record_probe.py (human-run; absent until Phase 0)
    tests/
      store.test.js  hmm.test.js  integrity.test.js  boundaries.test.js  static.test.js
  radar/
    index.html               legacy radar, moved unchanged from web/index.html (Phase 7)
    (src/ stays at web/src/; radar/index.html references ../src/radar.js etc.)
  wardline/                  UNTOUCHED (read-only import source only)
  src/, approve.html, verify.html, package.json, package-lock.json   unchanged

tools/
  export_console_data.py     data generator (orrery + fixtures + spec blocks -> console_data.js), --check
  sign_console_bundle.py     human-run: KMS Sign RAW ECDSA_SHA_256 over the decision bytes + GetPublicKey
  record_probe.py            human-run: the G3 probe requests, saved with status, selected headers, body, UTC time

tests/unit/
  test_console.py            gate_g6: export determinism, HMM conformance via node, bundle verification
  test_radar_page.py         paths updated to web/radar/index.html; every assertion unchanged

infra/app.py                 Phase 6 only: S3 + CloudFront (human deploys)
```

### 2.2 Dependency boundaries

- **Wardline is a read-only library.** `web/console/` may import `../wardline/chain.js` and `../wardline/model.js`
  (both are pure and free of DOM access). It never writes under `web/wardline/` and never imports its DOM modules
  (`main.js`, `director.js`).
- **Wardline's API is frozen.** A console test imports every Wardline symbol the console uses. If Wardline changes,
  that test fails before the console does.
- **Nothing in `web/` imports Python output except generated files.** Those are `console_data.js` and the two
  recorded JSON files. Runtime code never reads markdown.
- **`src/` is untouched.** Console data comes from `tools/` generators that import `tools/orrery.py`,
  `tools/export_ui_scenario.py` and `src/hearthward/contracts/spec/*.json` read-only.

### 2.3 The zero-external-dependency rule

The console uses the platform only:

- ES2022 modules;
- CSS3 (custom properties, grid, container queries, `contain`);
- SVG;
- Canvas 2D;
- optionally WebGL2 for one glow layer in Claim 1, with a Canvas 2D fallback;
- `crypto.subtle` (SHA-256, HMAC, ECDSA P-256).

It does **not** use:

- npm packages or CDNs;
- web fonts, which use the system stacks from Wardline;
- `fetch` to anything except same-origin probe paths (§4.5).

Python runs through `uv run`; tests run with `node:test` and `pytest`.

---

## 3. Data schemas and reactive state

### 3.1 The store

```js
state = {
  claim: "walk" | "asymmetry" | "bitemporal" | "integrity" | "boundaries",
  seq: 1..N,                             // belief-sequence cursor, shared by every claim
  selection: null | { kind: "envelope" | "rule" | "decision" | "probe" | "node", id: string },
  sandboxes: {
    walk:       { preset: "doorstep_scam" | "grandparent_call" | "benign_delivery" | "custom",
                  observations: Array<string[] | { claim: string[] }>,   // tactic lists or room claims
                  terms: { door_unknown_overlap: bool, identity_mismatch: bool,
                           untrusted_org_irreversible_payment: bool, verified_official_callback: bool } },
    integrity:  { edits: Array<{ seq: number, path: string, value: number | string }>,  // field edits
                  tamper: 0 | 1, rechain: 0 | 1 },
    boundaries: { ringBytePatch: null | { offset: number, value: number }, ringPretty: 0 | 1 }
  },
  live: { [probeId]: { status: "idle" | "pending" | "ok" | "error", at: string | null, response: object | null } },
  provenance: { kms: "recorded" | "synthetic-only", probes: "recorded" | "absent", hosting: "cloudfront" | "local" }
}
```

- **Actions** are plain objects, applied through `store.dispatch(action)`. Reducers are pure and return a new state.
- **Derived values come from memoized selectors only.** Examples: Λ trace, replay, checks, plane marks.
- **Rendering:**
  - `render(state, derived)` patches only the regions whose inputs changed.
  - A region's dirty check compares the selector outputs it reads, by reference.
  - At most one `requestAnimationFrame` per state change.
  - Nothing animates while idle.
- **`state.provenance`** is set at boot from which recorded files exist and from `location.origin`. Panels read it
  to choose their badges and labels (T-4, T-5).

### 3.2 URL hash contract

```
#claim=<walk|asymmetry|bitemporal|integrity|boundaries>&seq=<n>&tamper=<0|1>[&rechain=<0|1>][&sel=<kind>:<id>][&preset=<name>]
```

- **Hash to state:** parsed on load and on `hashchange`. Unknown keys are ignored. Invalid values fall back to the
  defaults (`claim=walk`, `seq=N`, `tamper=0`).
- **State to hash:** written with `history.replaceState` (no history spam). The exception is a claim change, which
  uses `pushState` so the Back button moves between claims.
- **What stays out of the hash:** sandbox edits beyond `tamper`, `rechain` and `preset`. Deep links stay short and
  stable.
- **Deep links the README and video must use:**
  - `#claim=walk&seq=8`
  - `#claim=asymmetry&seq=11`
  - `#claim=bitemporal&seq=7`
  - `#claim=integrity&tamper=1`
  - `#claim=integrity&tamper=1&rechain=1`
  - `#claim=boundaries`

### 3.3 Caching and debouncing

- **Crypto cache:** a `Map` keyed by `op + ":" + hex(SHA-256(input bytes))`, storing the promise. A replay of 12
  envelopes is 12 cache lookups after the first run, and re-verifying is instant.
- **Precomputation at boot:** the intact, tampered and re-chained replays and the five decision checks are computed
  once (Wardline's `precompute`) before the first paint of Claim 4. A small `verifying…` state shows until they
  resolve.
- **The decision sandbox** recomputes Λ synchronously; it is pure and takes about 1 ms for 20 observations. Typing
  in numeric fields is debounced by 120 ms.
- **Integrity field edits** are debounced by 200 ms, then re-hash only from the edited sequence onward, reusing
  earlier heads.
- **Live probes** run only on an explicit click, never on load. There is one in-flight request per probe and a
  5 s timeout. A failure shows `[ LIVE ] error <status|timeout>`; nothing falls back silently to recorded data.

---

## 4. The five verifiable claims

**Shared centre-stage frame:**

- **Container:** at most 1040 px wide, padded 32 px.
- **Header:**
  - the claim title (20/28, weight 600);
  - the assertion sentence (16/24);
  - the badges.
- **Hero visual:** about 55% of the view height.
- **"Try it" bar:** the sandbox controls (40 px buttons and toggles, labelled with their key).
- **"Proof" row:** each check as `✓/✗ text [badge]`, mono 14/20.

**Tokens and type:** reuse Wardline's tokens. Colour carries meaning only, and trust levels are shown by shape
(filled for observed, dashed for claimed, double ring for verified).

### 4.1 Claim 1: "It catches the scam with a bounded error rate"

**a. Assertion.**

- Λₜ = log L₁ − log L₀ (HMM forward pass against independent Bernoulli draws, §6.4) plus the cross-device terms.
- Wald's bounds:
  - A = ln((1−β)/α) = ln 95, which is **4,554 mn**.
  - B = ln(β/(1−α)), which is **−2,986 mn**.
  - α = 0.01 and β = 0.05.
- doorstep_scam crosses A after observation 4 (seq 8). The `+6,066 mn` there includes the `door_unknown_overlap`
  term (+700). The likelihood ratio is e^6.066, about **431 : 1**.

**b. Provenance.**

- The scripted run is `[ COMPUTED ] [ SYNTHETIC ]`: `hmm.js` recomputes the trace in the browser from
  `console_data.js` observations and `spec/sprt.json` parameters.
- The orrery's recorded trace is shipped beside it as the parity reference.

**c. Visual.**

- **The walk:**
  - A step polyline in a 1000 × 460 SVG.
  - y maps Λ over [−4,000, 9,000] mn onto 440…20.
  - x is the observation index, evenly spaced (the sandbox has no wall-clock time).
- **Ward lines:**
  - A: 2 px `--toward-scam`; solid `--intervene` once crossed. Label `A · INTERVENE · 95 : 1`.
  - B: 1 px `--toward-benign`. Label `B · DISMISS · 1 : 20`.
- **Readout column** (220 px, left): the hero Λ (mono 48), the likelihood ratio and the state chip.
- **Tactic strip:** under each step, the 7-cell tactic presence (filled cells for present tactics, dashed for room
  claims) and ΔΛ.
- **Optional glow:** a WebGL2 glow under the trail, using Wardline's shader math. It is off when reduced motion is
  on or WebGL2 is missing.

**d. Sandbox.**

- **Presets** from the orrery's scenarios.
- **"Add line":** seven tactic toggles, then Add.
- **"Add room claim":** a partial observation (§6.4). It adds only features not yet observed, per DC-2; the UI
  explains this when a toggled feature is dropped.
- **The four cross-device terms:**
  - each can be toggled once (DC-3);
  - only `verified_official_callback` is negative (DC-1), and it carries the verified double ring.
- **Editing lines:** remove or reorder.
- **Keys:** `A` adds a line, `Z` undoes.

**e. Proof output.**

- `Λ trace (mn): [1500, 1777, 3623, 6066]`, computed now.
- A crossing marker: `crossed A at observation 4`.
- For presets, a parity line: `matches tools/orrery.py reference_trace_mnats ✓ [ COMPUTED ]`. It compares
  against the shipped recorded trace.
- A pytest test guarantees the port equals the reference on `orrery.CONFORMANCE_VECTORS` (§6.2).

### 4.2 Claim 2: "A voice in the room cannot lower protection"

**a. Assertion.**

- **S+ fails open; S- fails closed (§5.1).**
- **The blocked request:** at seq 11 the room asks for `dismiss_alert` (S-), and the broker returns
  `PROOF_REQUIRED [HX-01, HX-02, HX-09]`. Λ is unchanged (ΔΛ = 0).
- **What an S- action needs:** a WebAuthn assertion whose `challenge = SHA-256("hw-action-v1" ‖ 0x00 ‖
  JCS({tool, args, household, episode_version, nonce, exp}))` (HX-03). An Alexa token alone never suffices (HX-02).

**b. Provenance.**

| Item | Badges |
|---|---|
| The blocked call and its rule IDs | `[ RECORDED ] [ SYNTHETIC ]` (orrery run of `src/hearthward/authz/hx_rules.py` semantics, from `console_data.js`) |
| Rule titles and statements | from the `hearthward:hx` spec block |
| The action hash | `[ COMPUTED ]` |

**c. Visual.**

- **Two columns of tools,** from `spec/tools.json`:
  - S+ (6 tools): fails open, rate-limited by HX-10.
  - S- (5 tools): fails closed.
  Each tool is a row: name, class, failure direction.
- **The request:** an arrow from the "Room (Alexa+)" source node to `dismiss_alert` ends at a membrane line and
  bounces. This is Wardline's ward bounce in a 1000 × 220 SVG.
- **The stamp:** `BLOCKED · PROOF_REQUIRED`, plus the three rule titles.
- **The action-hash builder:** a canonical-JSON card for `{tool, args, household, episode_version, nonce, exp}`
  with the resulting hash.

**d. Sandbox.**

- **Pick any S- tool:**
  - S- tools show the same fail-closed path, with the rules that apply to them.
  - S+ tools show "passes, no proof needed".
- **Edit `args`** (bounded strings and integers) and **change `nonce` and `exp`** in the action builder. The hash
  updates. It is shown as the WebAuthn challenge (base64url).
- **Link** to `approve.html`, the caregiver's page that signs exactly this challenge. Label it `built; the broker
  endpoint is not deployed`.

**e. Proof output.**

- `action_hash = sha256:… [ COMPUTED ]`.
- `HX-03: challenge equals action_hash ✓`.
- Any edit to an arg changes the hash, which shows why a passkey assertion for one action can't authorize another.

### 4.3 Claim 3: "Late or duplicate data can't rewrite a decision"

**a. Assertion.** These are BT-1 to BT-4:

- versions are immutable;
- the decision records `at_seq` and `ledger_head`;
- replaying to S reproduces belief at S;
- a late event creates new versions and never changes a decision record.

`late_and_lossy` has:

- a Ring event delivered on attempt 5, 2 min 34 s late (seq 11);
- an absorbed duplicate (E-6);
- a Bee line recovered by sync (seq 12);
- a decision at seq 7 that stays unchanged.

**b. Provenance.** `[ RECORDED ] [ SYNTHETIC ]` from the orrery run in `console_data.js`. The plane geometry is
`[ COMPUTED ]` by Wardline's `model.js` mappings.

**c. Visual.**

- **The plane:** a 1000 × 520 SVG.
  - x is valid time; y is belief sequence (R = 13 rows).
  - Envelopes are I-beams. Visit versions are rects spanning [ts, te).
  - "Possibly" extensions are feathered; "certainly" bounds are crisp.
  - Each source's watermark is a stepped frontier.
  - The decision stake is at `at_seq`.
- **Side column** (200 px): `AS OF SEQ`, Λ as of S, and the legend.

**d. Sandbox.**

- **Scrub the belief sequence:** a slider, ←/→ keys, or clicking a row.
- **"Show what was known at the decision"** jumps to `at_seq`.
- **A Ring-retry toggle** compares the on-time delivery (doorstep_scam) with the late one (late_and_lossy). It
  overlays the two visits' version histories.

**e. Proof output.**

- `decision at seq 7 · ledger_head sha256:… · unchanged by 5 later envelopes ✓`
- `visit belief at seq 7: [+00:02.800, +00:30.801] v2; now [−00:01.200, +01:17.803] v5` (the orrery's `AS_OF`)

### 4.4 Claim 4: "Every decision is tamper-evident and signed by AWS KMS"

**a. Assertion.**

- **Ledger heads:** `head_n = SHA-256("hw-ledger-v1" ‖ 0x00 ‖ JCS(envelope_n without head))`, where each envelope
  includes `prev = head_{n−1}` and the genesis head is 64 zeros.
- **The decision record:** signed with ECDSA P-256 over its **raw canonical bytes**. KMS uses `MessageType=RAW` and
  `ECDSA_SHA_256` (`src/hearthward/attest/sign.py:53`).
- **The decision digest:** `SHA-256("hw-decision-v1" ‖ 0x00 ‖ bytes)`.
- **The signed head:** `record.ledger_head` equals the replayed head at `at_seq` (8).

**b. Provenance.**

- The chain replay and all checks are `[ COMPUTED ]`.
- The signature is one of:
  - `[ RECORDED ]` for `bundle.kms.json` (human-run KMS Sign, with key ARN, region, UTC time and commit);
  - `[ SYNTHETIC ]` for the demo key, when the KMS bundle is absent.
- The ledger is `[ SYNTHETIC ]`.

**c. Visual.**

- **The zipper:** twelve 64 px bit tiles (16 × 16 cells of the head's 256 bits) joined by 8-tooth zipper links, as
  in Wardline.
- **Below it, the ledger table**, one 28 px row per envelope:

  | Column | Content |
  |---|---|
  | seq | sequence number |
  | lane | DOOR / ROOM / WRIST |
  | kind | e.g. `door.motion` |
  | valid | [lo, hi] |
  | received | arrival time |
  | head | glyph and short hash |
  | content | ✓ / ✗ |
  | link | ✓ / ✗ |
- **Then the five-line check list.**

**d. Sandbox.**

- **Edit any scalar field of any envelope in the table.** Integers stay integers and strings stay bounded. The view
  re-hashes from that envelope onward and shows which tiles changed and where the zipper opened.
- **One-click Tamper** (`T`, or `tamper=1`): `received_at += 1 ms` at `seq = at_seq − 3` (5).
- **Re-chain** (`rechain=1`): rewrite the later `prev` and `head` values. Every link closes, but check 5 fails.
- **Reset.**
- **Verify** (`V`): re-runs every check live.
- **Download `bundle.json`:** the exact signed bytes, signature, public key, decision digest and current ledger,
  ready for `python tools/verify_attestation.py bundle.json`.

**e. Proof output.** Five lines, each `✓/✗` with detail:

1. `Signature · ECDSA P-256 over 521 raw bytes · key <ARN | synthetic-demo-key (not KMS)>`
2. `Record · re-encodes to the identical signed bytes`
3. `Digest · sha256:… equals decision_digest`
4. `Chain · 12 envelopes replay from genesis`
5. `Head · ledger_head equals the replayed head at seq 8`, which shows `— not evaluated` when check 4 fails

After a re-chain, checks 1–4 pass and 5 fails. That is the lesson.

### 4.5 Claim 5: "It really runs on AWS, behind Alexa+'s OAuth"

**a. Assertion.**

- **Alexa+** reaches the MCP add-on at `POST /mcp` (API Gateway HTTP API → VPC Link → internal ALB → ECS Fargate).
- **Missing or invalid token:** `401` with a JSON body and no `WWW-Authenticate` header (P-5).
- **Metadata:** the protected-resource metadata names the façade as `authorization_servers[0]`. The façade's RFC 8414
  metadata advertises `S256`. Its JWKS key is held in KMS (`facade-token-signer`).
- **Why the façade exists:** four reasons Cognito can't be the authorization server directly (§7.1).
- **The attestation key** `alias/hearthward/attest-signer` (ECC_NIST_P256, SIGN_VERIFY) is in the same stack.

**b. Provenance.**

- Probes are `[ RECORDED ]` (`probe.recorded.json`) and, when served from CloudFront, also `[ LIVE ]` on click.
- The architecture map is from §2 of the master blueprint. Each node shows its badge plus a deployment status, taken
  from STATE.md at build time and shipped in `console_data.js`:
  - `deployed (probe-verified 2026-10-21)`
  - `built, not deployed` (broker, ladder, anchor bucket, the approval endpoints)
  - `deferred` (L3, Flink, real Ring and Bee)

**c. Visual.**

- **The architecture map:** a 1000 × 560 hand-laid SVG.
  - Nodes: Alexa+, the API Gateways, ALB, Fargate (mcp, fusion, falkordb), Lambdas, DynamoDB tables, KMS keys,
    Cognito, Step Functions, S3 Object Lock, CloudFront.
  - The trust boundaries TB-1 to TB-8 are numbered dashed edges.
  - Node outlines show status: solid means deployed, dashed means built, dotted means deferred.
  - Clicking a node or boundary selects it into the inspector.
- **The probe panel:** four rows (`GET` protected-resource, `GET` façade metadata, `GET /jwks`, `POST /mcp`). Each
  shows the method, path, expected status, recorded status and time, and a `Run live` button.
- **The Ring HMAC card.**

**d. Sandbox.**

- **`Run live`** (only on the CloudFront origin) fetches same-origin paths, which CloudFront routes to the APIs. It
  shows the status, the selected headers (proving `WWW-Authenticate` is absent) and the body.
- **`Copy curl`** gives the exact command for the API Gateway URL.
- **The Ring raw-bytes sandbox** uses `fixtures/ring/sample.json` and its manifest signature, keyed with
  `SYNTHETIC_RING_KEY` (labelled synthetic):
  - flip any byte (hex editor row) → ✗;
  - **Pretty-print**, the same JSON with whitespace added → ✗;
  - **Reset** → ✓.

  This teaches C-5: authenticate the raw bytes and never re-serialize.

**e. Proof output.**

- Per probe: `401 · no WWW-Authenticate · no redirect ✓ [ LIVE 14:02:11Z ]`, or the recorded equivalent.
- `JWKS kid … · EC P-256 [ RECORDED/LIVE ]`.
- `X-Signature sha256=… matches HMAC-SHA256(raw body) ✓ [ COMPUTED ] [ SYNTHETIC ]`.

**Hosting (Phase 6):**

- **Origin:** a private S3 bucket with Origin Access Control and Block Public Access.
- **CloudFront behaviours:**

  | Path | Routed to | Allowed methods |
  |---|---|---|
  | default | S3 | GET/HEAD |
  | `/.well-known/oauth-protected-resource` | mcp API | GET |
  | `/mcp` | mcp API | GET, POST, OPTIONS (…) |
  | `/.well-known/oauth-authorization-server`, `/jwks` | auth API | GET |

  The probe behaviours use the `CachingDisabled` cache policy and `AllViewerExceptHostHeader`.
- **Response-headers policy (static behaviour):**
  - CSP `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self';
    base-uri 'none'; form-action 'none'; frame-ancestors 'none'`;
  - HSTS;
  - `nosniff`;
  - `Referrer-Policy: no-referrer`.
- **Upload:** `BucketDeployment` of `web/`, excluding `tests/`, `node_modules/`, `*.py` and `package*.json`.
- **Unchanged:** the URLs Alexa+ uses and the manifest's `resource`. The human runs `cdk deploy`.

---

## 5. Inspector drawer (right rail, 380 px)

**Behaviour by viewport width:**

| Viewport width | Inspector |
|---|---|
| ≥ 1440 px | docked |
| 1024–1439 px | overlay sheet with a 1 px `--line-2` edge (`I` toggles it) |
| < 1024 px | bottom sheet |

**Tabs** (keys `[`/`]` cycle them):

1. **Formatted telemetry.** The selection as labelled fields: times as `+mm:ss.mmm` and UTC, Λ with its likelihood
   ratio, trust level by shape, the source's trust boundary, and the blueprint section link (for example
   `§4.4 BT-4`).
2. **Canonical HW-JCS bytes.** The exact bytes that are hashed or signed, in mono 12/18 with soft wrap. The header
   shows the byte count and the digest beside the tag used (`hw-ledger-v1`, `hw-decision-v1`, `hw-action-v1`).
   **Copy** copies the bytes exactly. For a signed record, these are the decoded `bytes_b64`, never a
   re-serialization.
3. **Proof verification tree.** A collapsible tree from the claim down to its leaves, for example:

   ```
   Claim 4 ✓
   ├─ signature ✓  ECDSA P-256 · 521 bytes · key …
   ├─ record ✓     canonical round trip
   ├─ digest ✓     hw-decision-v1
   └─ chain ✓
      ├─ seq 1 ✓ content ✓ link
      └─ …
   ```

   Every node carries its badges. A failed node expands automatically and shows its failure detail.

**Context switching:**

| Selection | Default tab |
|---|---|
| none | the active claim's proof tree |
| an envelope | Formatted |
| a decision | Proof tree |
| a probe | Formatted (response) |
| a rule | Formatted (statement and enforcement point) |
| an S- tool | Canonical bytes of its action document |

When the claim changes and the current selection doesn't belong to it, the selection is cleared.

---

## 6. Test gates and conformance contracts

### 6.1 Protecting gate G6

1. `git mv web/index.html web/radar/index.html`, then fix its relative paths (`src/…` → `../src/…`). The radar's
   behaviour is unchanged.
2. In `tests/unit/test_radar_page.py`, change only `INDEX = WEB / "radar/index.html"` and the script-path regex
   (`src/radar.js` → `../src/radar.js`). **Every assertion stays as it is.** This is a relocation, and it needs the
   human's explicit OK under hard rule 7, with the diff shown first.
3. `web/index.html` becomes the console shell.
4. New `gate_g6` tests in `tests/unit/test_console.py` run the console node tests through `node` (as
   `test_attest.py` already does).
5. **Before and after this phase:** `--only F`, `--only G6`, the full `pytest` suite and `cd web && npm test` all
   pass. The number of tests must not drop.

### 6.2 Conformance contracts

| Contract | How it is checked |
|---|---|
| **HMM parity** | pytest feeds each of `orrery.CONFORMANCE_VECTORS` (and the three presets' observation lists) to `node web/console/tests/hmm_cli.mjs` and asserts integer equality with `orrery.reference_trace_mnats` and with `hearthward.decision.hmm.lambda_trace_mnats`. Any one-mn mismatch fails. The port must keep the reference's operation order (logsumexp with a max shift; `log1p(−p)` for absent features; noisy rates `tpr·p + fpr·(1−p)`; −∞ for zero transitions; rounding through the same `mnats()` rule). |
| **Chain parity** | node replays `console_data.js` envelopes and equals every recorded `head`. Python's `verify_attestation.replay_ledger` agrees on the same data. |
| **Signature parity** | `bundle.kms.json`, when present, passes `tools/verify_attestation.py` (exit 0) **and** the console's WebCrypto check. Without it, the synthetic bundle passes both. |
| **Export determinism** | `tools/export_console_data.py --check` is up to date, and two runs produce identical bytes. |
| **Ring HMAC parity** | the browser HMAC over `fixtures/ring/sample.json` equals the manifest's signature, and any single-byte flip or a pretty-printed copy fails. |
| **Truth in advertising** | `static.test.js`: every claim view and inspector tab renders at least one badge; no status words from T-1 appear as string literals; no `60 fps`; no `http(s)://` except the SVG namespace; `fetch(` appears only in `boundaries.js` with relative paths; `SYNTHETIC` is present. |
| **URL contract** | every deep link in §3.2 parses to the expected state, and state → hash → state round-trips. |

---

## 7. Phased execution protocol

The submission deadline is **Fri 2026-10-23 12:00 PT**. Every phase ends with `--only F` and `--only G6` passing,
plus the node tests. No commits without the human's go-ahead. Estimates are rough.

| Phase | Work | Done when | Est. |
|---|---|---|---|
| **0 Evidence** | Write `tools/sign_console_bundle.py` (rebuilds `export_ui_scenario`'s decision record, KMS `Sign` RAW `ECDSA_SHA_256` with `alias/hearthward/attest-signer`, `GetPublicKey`, writes `web/console/data/bundle.kms.json` with ARN, region, UTC and commit) and `tools/record_probe.py`. **The human runs both** (no AWS calls by Claude, rule 3). | `uv run python tools/verify_attestation.py web/console/data/bundle.kms.json` exits 0; `probe.recorded.json` shows 401 with no `WWW-Authenticate` | 1 h + human 20 min |
| **1 Core** | `export_console_data.py` + `console_data.js`; `store.js`, `provenance.js`, URL router; the Wardline API-freeze test | export `--check` passes; store and URL tests pass | 2 h |
| **2 Shell and Claims 1 + 4** | `index` shell (served at `web/console/index.html` until Phase 7), header, claim nav, inspector (3 tabs), Claim 1 scripted walk, Claim 4 full integrity sandbox | the deep links for walk and integrity work; all five checks behave as §4.4e | 4 h |
| **— CUT LINE A —** | Shippable: the record-and-submit minimum | | |
| **3 Claims 2 + 3** | asymmetry view with the action-hash builder; bitemporal view using Wardline geometry | Claim 2 and 3 deep links work | 3 h |
| **4 Decision sandbox** | `hmm.js` + the pytest HMM parity test | parity on every vector | 2–3 h |
| **5 Claim 5** | architecture map, recorded probes, Ring HMAC sandbox, curl blocks | Ring parity passes; probes render from the recorded file | 2 h |
| **— CUT LINE B —** | Complete console, local hosting | | |
| **6 Hosting** | CDK: S3 + OAC + CloudFront + headers policy + probe behaviours; **the human runs `cdk diff` and `cdk deploy`** | `Run live` returns 401 with no `WWW-Authenticate` from the CloudFront domain; the static pages load with the CSP header | 1 h + deploy |
| **7 Swap** | Radar relocation and the test path change (with approval), `web/index.html` = console, README deep links and badge legend | F, G6, full pytest and `npm test` pass with no drop in test count | 1 h |

**Order if time runs short:** 0 → 1 → 2 → 7, then record the video, then 3 → 4 → 5 → 6.

Changes visible to judges after the deadline are subject to the Devpost rules; check them before you change
anything live.
