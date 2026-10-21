# G2 completion log

Gate G2 (MCP server, OAuth facade, S+ tool surface) executed by the main session (Claude Sonnet 5.5, as the task
brief states; `CLAUDE.md` names Opus 5.5 as the main-session model, see Deviations).

- **Starting state:** branch `main`, tree clean, HEAD `2f5ad88` "docs(G0): record developer attestations for
  console, bedrock, and cognito".
- **Result:** **G2 PASS (exit 0).** Every automated check passed on the first run. `G2.alexa_discovery_summary` is a
  human attestation (hard rule 5), so that run reported INCOMPLETE (exit 3) until Surya Prasanth R recorded it on
  2026-10-21. A second run after the attestation commit reports `summary: F PASS, G0 PASS, G1 PASS, G2 PASS`.

## Commits created

| Hash | Message |
|---|---|
| `813e6d6f148ad71af60fde55b498fdc4dda36f84` | feat(G2): Streamable HTTP MCP server, RFC 8414 OAuth facade, and S+ tools |
| `4d7506b` | docs(G2): completion log and state snapshot |
| `99a0dbb` | docs(G2): record alexa discovery summary attestation (human-written `attestations.json`, committed on request) |
| (this commit) | docs(G2): update state and log to reflect G2 PASS |

Gate F was run (PASS) before the feat commit. Paths were staged explicitly. No push, remote, amend, rebase or force.

## Tests (`uv run pytest -m gate_g2`)

| File | Passed |
|---|---|
| `tests/unit/test_g2_app.py` | 27 |
| `tests/unit/test_g2_auth.py` | 34 |
| `tests/unit/test_g2_facade.py` | 33 |
| `tests/unit/test_g2_schemas.py` | 36 |
| `tests/unit/test_g2_tools.py` | 61 |
| **Total** | **191 passed, 0 failed, 0 skipped, 0 xfailed** |

Full suite: `uv run pytest` gives 551 passed (360 gate_g1 + 191 gate_g2). No network is used: tokens are minted
with throwaway local keys, the Cognito token endpoint is an injected fake, and every value is synthetic.

## `uv run python tools/pipeline.py --gate G2 --keep-going`, before the attestation (exit 3)

Run with the five G2 variables set to synthetic values shaped like the pipeline's rules (the values are listed in
`tests/g2_support.py`; none names a real resource).

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
  PASS     F.secrets                    62 tracked or new files scanned, no secrets
gate F: PASS

== G0 | Environment and pins ==
  PASS     G0.pyproject                 requires-python >=3.12,<3.13; mcp==2.3.0
  PASS     G0.python_version            .python-version 3.12
  PASS     G0.lockfile                  uv.lock pins mcp 2.3.0
  PASS     G0.env_example               12 gate variables named, no values
  PASS     G0.interpreter               running on Python 3.12.14
  PASS     G0.alexa_console             attested by Surya Prasanth R on 2026-10-21
  PASS     G0.bedrock_access            attested by Surya Prasanth R on 2026-10-21
  PASS     G0.cognito_tier              attested by Surya Prasanth R on 2026-10-21
  PASS     G0.track_posture             attested by Surya Prasanth R on 2026-10-21
gate G0: PASS

== G1 | Contracts and ingest ==
  PASS     G1.modules                   5 paths present
  PASS     G1.fixtures                  4 fixtures byte-identical to HEAD, text unset
  PASS     G1.tests                     pytest -m gate_g1: 360 passed, 191 deselected in 56.85s
gate G1: PASS

== G2 | MCP server and facade ==
  PASS     G2.modules                   7 paths present
  PASS     G2.env                       5 variables set and well-formed
  PASS     G2.schema_drift              11 served tool schemas identical to the blueprint
  PASS     G2.tests                     pytest -m gate_g2: 191 passed, 360 deselected in 4.31s
  MANUAL   G2.alexa_discovery_summary   add G2.alexa_discovery_summary with by, at and evidence to docs\gates\attestations.json
gate G2: INCOMPLETE

summary: F PASS, G0 PASS, G1 PASS, G2 INCOMPLETE
```

(Exit 3 means incomplete: awaiting attestation.)

## `uv run python tools/pipeline.py --gate G2 --keep-going`, after the attestation (exit 0)

Same five synthetic variables. Only the G2 attestation line and the totals differ from the run above.

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
  PASS     F.secrets                    64 tracked or new files scanned, no secrets
gate F: PASS

== G0 | Environment and pins ==
  PASS     G0.pyproject                 requires-python >=3.12,<3.13; mcp==2.3.0
  PASS     G0.python_version            .python-version 3.12
  PASS     G0.lockfile                  uv.lock pins mcp 2.3.0
  PASS     G0.env_example               12 gate variables named, no values
  PASS     G0.interpreter               running on Python 3.12.14
  PASS     G0.alexa_console             attested by Surya Prasanth R on 2026-10-21
  PASS     G0.bedrock_access            attested by Surya Prasanth R on 2026-10-21
  PASS     G0.cognito_tier              attested by Surya Prasanth R on 2026-10-21
  PASS     G0.track_posture             attested by Surya Prasanth R on 2026-10-21
gate G0: PASS

== G1 | Contracts and ingest ==
  PASS     G1.modules                   5 paths present
  PASS     G1.fixtures                  4 fixtures byte-identical to HEAD, text unset
  PASS     G1.tests                     pytest -m gate_g1: 360 passed, 191 deselected in 54.07s
gate G1: PASS

== G2 | MCP server and facade ==
  PASS     G2.modules                   7 paths present
  PASS     G2.env                       5 variables set and well-formed
  PASS     G2.schema_drift              11 served tool schemas identical to the blueprint
  PASS     G2.tests                     pytest -m gate_g2: 191 passed, 360 deselected in 4.28s
  PASS     G2.alexa_discovery_summary   attested by Surya Prasanth R on 2026-10-21
gate G2: PASS

summary: F PASS, G0 PASS, G1 PASS, G2 PASS
```

## What was built

| Module | Role |
|---|---|
| `mcp/schemas.py` | `tool_definitions()`: the 11 `hearthward:tools` definitions, read from the exported `tools.json` |
| `mcp/app.py` | Starlette app: SDK lowlevel `Server`, `/mcp` stateless JSON, middleware stack, `ToolService` |
| `mcp/auth.py` | `TokenValidator`, `JwksKeySet`, `BearerAuthMiddleware` (401 without `WWW-Authenticate`, 403) |
| `mcp/wellknown.py` | `/.well-known/oauth-protected-resource` |
| `mcp/tools_splus.py` | six S+ handlers |
| `mcp/tools_sminus.py` | five S- handlers, fail closed |
| `mcp/runtime.py`, `mcp/config.py` | `ToolContext`, `SyntheticStore`, id minting; the five G2 environment variables |
| `authz/facade_as.py` | RFC 8414 facade: metadata, `/authorize`, `/token`, `/jwks`, `LocalEs256Signer` |
| `contracts/spec/tools.json`, `hx.json` | new exports of the `hearthward:tools` and `hearthward:hx` blocks (`tools/export_spec.py`) |

## Deviations and architectural notes

1. **Commit trailer.** The task brief and the session's attribution rule say `Co-Authored-By: Claude Sonnet 5.5`;
   `CLAUDE.md` hard rule 10 and the routing table say Opus 5.5 for the main session. This session ran on Sonnet 5.5,
   so the commit said Sonnet 5.5, which was the accurate attribution. (Superseded: all trailers were later removed from the
   history at the author's request; see the README's AI assistance section and `docs/gates/COMMIT_MAP.md`.)
2. **Spec export widened.** `tools/export_spec.py` now also writes `tools.json` and `hx.json`, so runtime code reads
   JSON, never markdown. The existing two outputs are byte-identical. Two G1 test expectations changed to match:
   `test_spec_export.py` lists the four files, and the "LF-terminated" check accepts a closing `]` as well as `}`
   (the new files are arrays). Neither assertion was weakened (still LF only, ASCII only, trailing newline).
3. **Dependencies declared.** `pyproject.toml` now lists `pyjwt`, `cryptography` and `starlette`, which the code
   imports directly (they were already in the lock via `mcp`). `uv lock` added six lines and changed no pins.
4. **SDK auth bypassed on purpose.** `MCPServer`/`Server.streamable_http_app(auth=...)` installs
   `RequireAuthMiddleware`, which sends `WWW-Authenticate` (P-5). G2 passes no `auth`, and puts its own ASGI
   middleware in front. The lowlevel `Server` is used (not `MCPServer`) because `MCPServer` derives tool schemas
   from function signatures and the blueprint schemas must be served byte for byte.
5. **Per-instance lifespan.** The SDK session manager can run its lifespan once per app instance, so tests build a
   fresh app per client (`Stack.new_mcp_app`).
6. **Protocol negotiation.** Header: missing becomes 2025-03-26 (injected), anything outside the three accepted
   versions is 400. Body: an `initialize` asking for an unaccepted version (2024-11-05, 2026-07-28, ...) is
   rewritten to 2025-11-25 so the server answers with the newest accepted version and the client decides. The SDK
   would otherwise echo 2024-11-05.
7. **POST only.** `GET`/`DELETE /mcp` return 405. In stateless mode the SDK's `GET` opens a server-sent-event stream
   that never ends and API Gateway buffers (P-1). Authentication runs first, so an anonymous `GET` is 401.
8. **`/token` 401.** A failed Basic client login on the facade's `/token` returns 401 with `WWW-Authenticate: Basic`
   as RFC 6749 §5.2 requires. P-5 applies to the MCP resource server only. A failed `client_secret_post` login is 400.
9. **Service-client secrets** are held as SHA-256 hex digests (`FacadeConfig.service_clients`) and compared in
   constant time, including for unknown client ids. Where the Alexa service client's secret lives in production
   (Secrets Manager) is a G3 decision.
10. **S- tools.** With no broker, every S- call is `BLOCKED` (HX-02 plus HX-11, and HX-01 when an episode is open),
    never `PROOF_REQUIRED`: a challenge nobody can approve would be a lie. An injected broker may only answer with a
    well-formed, unexpired `PROOF_REQUIRED`; any other answer, or an exception, becomes `BLOCKED`. No success status
    (`DISMISSED`, `PAUSED`, `ADDED`, `UPDATED`, `SHARED`) is reachable.
11. **S+ scoring is a stand-in.** `assess_visitor` adds fixed milli-nat weights per reason code and maps the total with
    the blueprint's `low < 1000 <= elevated < 4554 <= high`. It is monotone: an open episode's evidence never falls,
    and a claim of a trusted vendor name never lowers the score. The SPRT replaces it in G4.
12. **`raise_concern` throttling.** The 7th concern within an hour is still recorded (nothing is dropped) but its
    `next_step` is demoted to `monitoring` and no family notification is sent (HX-10 THROTTLE).
13. **Data layer.** `SyntheticStore` is in memory and labels every answer `data_origin: synthetic`. Wiring to the
    DynamoDB ledger and the household/subject mapping is later work.

## Open items for the next session

- **§7.4 questions that need a deployed endpoint** (hard rule 3 forbids network use here):
  1. Does Alexa+ need Tier 1 for a public MCP server?
  2. Does Cognito's token endpoint accept a repeated `resource`? The facade forwards the form byte for byte for
     `authorization_code` and `refresh_token`; if Cognito rejects `resource`, strip it in `Facade._proxy`.
  3. Which metadata path does `alexa-ai configure-account-linking` read? Both RFC 8414 and OIDC paths are served.
- **User-token scope.** `TokenValidator(required_user_scopes=())` requires none, as §7.3 lists none. Add
  `config.user_scope` once G3 shows which scope Cognito's resource server actually issues.
- **KMS signer.** `LocalEs256Signer` is for tests. A `facade-token-signer` KMS signer (DER to raw r||s) is G3.
- **JWKS loading.** `JwksKeySet.from_url` uses `urllib` and runs only when deployed; it is untested here by design.
- **Cognito `aud`.** §7.3 requires `aud` = the MCP URI; real Cognito access tokens carry `client_id`, and
  `aud` only with resource binding on Essentials/Plus. G3 must confirm this on the deployed pool.
