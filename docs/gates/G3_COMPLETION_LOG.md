# G3 completion log

Gate G3 (deployed and linked) executed by the main session (Claude Sonnet 5.5). **G3 is INCOMPLETE (exit 3), and
that is the correct state for this session.** What can be built and verified offline is done; two checks cannot be
satisfied without things this session may not do (see "Why G3 cannot pass yet").

- **Starting state:** branch `main`, tree clean, HEAD `273b995`, gates F, G0, G1 and G2 PASS.
- **Result:** `G3.infra` PASS, `G3.env` PASS, `G3.probe` PENDING, `G3.simulator_linking` MANUAL.

## Commits created

| Hash | Message |
|---|---|
| `c4253a1886d4369a272990511874ed54a9340797` | feat(G3): CDK infrastructure stack, topology definitions, and endpoint probe suite |
| (this commit) | docs(G3): completion log and state snapshot |

Gate F was run (PASS) before the feat commit. Paths were staged explicitly. No push, remote, amend, rebase or force.

## Tests (`uv run pytest -m gate_g3`)

| File | Passed |
|---|---|
| `tests/unit/test_infra.py` | 30 |
| `tests/unit/test_g3_probe.py` | 10 |
| `tests/unit/test_g3_runtime.py` | 15 |
| **Total** | **55 passed, 0 failed, 0 skipped, 0 xfailed** |

Full suite: `uv run pytest` gives 606 passed (360 gate_g1 + 191 gate_g2 + 55 gate_g3). No network is used. KMS runs
under `moto`; the CDK app is synthesized in-process and nothing is deployed.

`test_infra.py` checks the synthesized template: the four tables (names, key schemas, stream, PITR, CMK encryption,
deletion protection), the three keys, Cognito (Essentials, managed login v2, resource server, code-only client), both
HTTP APIs' exact route sets, the VPC link and internal ALB, the container Lambda, and IAM. For TB-5 it asserts that
the `mcp` task role has exactly one explicit deny on every DynamoDB write to `SecurityState` (and its indexes), no
allow that could write it, no access to `ProofLedger`, `Nonces` or the signing keys, and that no allow statement in
the stack grants `*` or `service:*`.

`test_g3_probe.py` runs the **real** `pipeline.g3_probe` with `pipeline._http` routed to in-process clients for the
two hosts, so the pipeline's own code judges the local apps: it passes, and it fails when the 401 gains a
`WWW-Authenticate`, when `/mcp` redirects, when S256 is missing, or when `authorization_servers[0]` is not the
facade. It also checks the three G3 variable values you gave against `ENV_RULES`.

## `uv run python tools/pipeline.py --only G3` (exit 3)

With `AWS_REGION=us-east-1`, `HW_LEDGER_TABLE=HearthwardEventLedger` and
`HW_ATTEST_KEY_ARN=arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012` set.

```
== G3 | Deployed and linked ==
  PASS     G3.infra                     3 paths present
  PASS     G3.env                       3 variables set and well-formed
  PENDING  G3.probe                     run with --probe to check the deployed endpoints
  MANUAL   G3.simulator_linking         add G3.simulator_linking with by, at and evidence to docs\gates\attestations.json
gate G3: INCOMPLETE

summary: G3 INCOMPLETE
```

## `uv run python tools/pipeline.py --gate G3 --keep-going` (exit 3)

The same three variables plus the five G2 variables (synthetic values from `tests/g2_support.py`).

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
  PASS     F.secrets                    76 tracked or new files scanned, no secrets
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
  PASS     G1.tests                     pytest -m gate_g1: 360 passed, 246 deselected in 51.70s
gate G1: PASS

== G2 | MCP server and facade ==
  PASS     G2.modules                   7 paths present
  PASS     G2.env                       5 variables set and well-formed
  PASS     G2.schema_drift              11 served tool schemas identical to the blueprint
  PASS     G2.tests                     pytest -m gate_g2: 191 passed, 415 deselected in 10.07s
  PASS     G2.alexa_discovery_summary   attested by Surya Prasanth R on 2026-10-21
gate G2: PASS

== G3 | Deployed and linked ==
  PASS     G3.infra                     3 paths present
  PASS     G3.env                       3 variables set and well-formed
  PENDING  G3.probe                     run with --probe to check the deployed endpoints
  MANUAL   G3.simulator_linking         add G3.simulator_linking with by, at and evidence to docs\gates\attestations.json
gate G3: INCOMPLETE

summary: F PASS, G0 PASS, G1 PASS, G2 PASS, G3 INCOMPLETE
```

## Why G3 cannot pass yet

1. **`G3.probe`** only runs with `--probe`, and then calls the deployed endpoints over HTTPS. That needs a deployed
   stack, and hard rule 3 forbids AWS and network calls from this repository's sessions. The probe was **not** run
   with `--probe`. The local stand-in (`test_g3_probe.py`) exercises the probe's code against the same apps the stack
   deploys; it shows the apps satisfy the assertions, not that a deployment does.
2. **`G3.simulator_linking`** is a human attestation (hard rule 5): account linking in the Alexa simulator, which
   also needs the deployment.

To finish G3: `cdk deploy` the stack (Docker is needed to build the two images), set the eight output values as the
gate variables, run `uv run python tools/pipeline.py --only G3 --probe`, link the account in the simulator, and add
the `G3.simulator_linking` attestation.

## What was built

| Path | Role |
|---|---|
| `infra/app.py` | the CDK stack (`HearthwardStack`, `build_app`) |
| `infra/cdk.json`, `infra/cdk.context.json` | CLI config (`uv run python app.py`); region, tags, `synthetic: true`, Cognito domain prefix, Alexa redirect URI placeholders, service-client digests |
| `infra/Dockerfile.facade`, `infra/Dockerfile.mcp`, `.dockerignore` | the two container images; the build context is `src/` only |
| `authz/kms_signer.py` | `KmsEs256Signer`: signs with `facade-token-signer` (DER to raw `r‖s`), publishes the JWK |
| `authz/lambda_handler.py` | API Gateway HTTP API v2 event to ASGI adapter and `handler` for `facade_as` |
| `mcp/asgi.py` | uvicorn factory for the Fargate task |
| `mcp/config.py` | optional `HW_EXTRA_ALLOWED_HOSTS` |

Stack contents: tables `HearthwardEventLedger` (stream NEW_IMAGE), `HearthwardSecurityState`,
`HearthwardProofLedger`, `HearthwardNonces` (TTL `expires_at`); KMS `attest-signer` and `facade-token-signer`
(`ECC_NIST_P256`, SIGN_VERIFY) and a rotating `data-key`; Cognito Essentials with managed login v2, resource server
identified by the MCP URI with scope `tools`, and an authorization-code client with a secret; HTTP API `auth`
(5 routes to the `facade_as` Lambda); HTTP API `mcp` (`POST /mcp`, `GET /.well-known/oauth-protected-resource`)
through a VPC link to an internal ALB and a Fargate service; outputs for the eight gate variables.

## Deviations and architectural notes

1. **Key attribute names are `pk` and `sk`, not `household` and `seq`.** The brief says "PK: household, SK: seq".
   The committed G1 ledger (`ingest/ledger.py`, `tests/support.py`) writes string attributes `pk` (the household) and
   `sk` (the zero-padded sequence, plus the HEAD, dedup and quarantine items that share the table). A table keyed
   `household`/`seq` would reject every G1 write, and `seq` could not hold the `HEAD` item as a number. The tables
   therefore use `pk`/`sk` and mean the same thing. A test pins the schema to what the ledger code writes. Say if you
   want the names changed instead (that is a ledger code change as well).
2. **`cdk synth` was not run.** The CDK CLI is not installed (Node 24 is). The stack was synthesized through the
   Python library: `cd infra && uv run python app.py` writes `infra/cdk.out` (git-ignored) in about 20 seconds and
   calls no AWS API. The same code path is what `cdk synth` runs, through `cdk.json`'s `uv run python app.py`.
3. **Account-agnostic.** Only the region is set (`us-east-1`), so synthesis needs no context lookups. The account in
   `HW_ATTEST_KEY_ARN` (`123456789012`) is the example value from the brief and appears nowhere in the template.
4. **PKCE is not set by the stack.** CDK exposes no PKCE switch for Cognito app clients. The client uses only the
   authorization-code flow, has a secret, and no password, SRP or custom flow (`ExplicitAuthFlows` is refresh-only,
   set through the L1 escape hatch). Cognito accepts S256, and the facade advertises it, but whether the deployed
   client rejects a request without a challenge is a deploy-time observation, not something this stack proves.
5. **Container images are staged, not built.** Synthesis hashes the build context but does not run Docker. The
   Dockerfiles install unpinned `starlette`, `pyjwt[crypto]`, `cryptography` and `boto3` (and `mcp==2.3.0`); pin them
   from `uv.lock` before a real deploy.
6. **Data is still synthetic.** The deployed `mcp` task builds `create_app`, whose store is the in-memory
   `SyntheticStore`, which maps every account to one synthetic household. Do not deploy this with real data. The
   `mcp` role can already read `EventLedger` and `SecurityState`; wiring the DynamoDB-backed store is next-gate work.
7. **Host header.** API Gateway may forward the internal ALB's own name as `Host`. The task gets
   `HW_EXTRA_ALLOWED_HOSTS` set to the ALB DNS name, which extends (never replaces) the pinned hosts of P-6. Whether
   API Gateway actually does this is a deploy-time check.
8. **Output ids.** CloudFormation output ids cannot contain underscores, so outputs are `McpResourceUri` and so on,
   each with the variable name (`HW_MCP_RESOURCE_URI`) as its description.
9. **Not in this stack yet:** `ring_ingest`, `bee_ingest`, `fusion`, `falkordb`, the broker, the `web` API,
   `InterventionLadder`, the SQS FIFO queue and the EventBridge Pipe (later gates); a custom domain for either API.
10. **Dependencies.** `aws-cdk-lib` and `constructs` joined the dev group (`uv.lock` +121 lines, no existing pin moved).
11. **Facade service clients.** `HW_FACADE_SERVICE_CLIENTS` holds `client id -> SHA-256 digest` only. The synthetic
    context has none, so a deployed facade rejects every `client_credentials` login until a digest is configured.

## Open items for the next session

- **Human:** deploy, then `--probe`, then the simulator linking attestation (`G3.simulator_linking`).
- Deploy-time observations to settle (blueprint §7.4 and G2's notes): Cognito and a repeated `resource` parameter;
  the `aud` and scope Cognito really issues (then set `required_user_scopes`); the Host header API Gateway forwards;
  PKCE enforcement on the client.
- Pin the image dependencies; decide where the Alexa service client's secret digest comes from (Secrets Manager).
- G4: the SPRT decision core. G5: the broker, which is what may write `SecurityState`.
