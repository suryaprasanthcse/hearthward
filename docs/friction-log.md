# Friction log

What got in the way while building HEARTHWARD, in the order it came up. Each entry says how I know: **observed**
(it happened in this build and a test or log shows it), **documented** (a platform document says so and the code
works around it, but I have not run it against the live service), or **expected** (a risk I have not been able to
test). The CDK stack was deployed to us-east-1 on 2026-10-21, but nothing here was seen against live Alexa+.

## Alexa+ MCP add-ons

1. **No `WWW-Authenticate` on a 401. Documented.** Alexa+ does not support the header, and the MCP Python SDK's
   `RequireAuthMiddleware` always sends it. I could not use the SDK's authentication at all and wrote ASGI middleware
   that returns a JSON 401 instead (`src/hearthward/mcp/auth.py`). The spec's own recommended behavior and the
   platform's limit pull in opposite directions, and the SDK offers no switch.
2. **No OIDC, no dynamic client registration, no step-up. Documented.** Every authorization-server feature a generic
   MCP client would try has to be absent or answered with plain RFC 8414 metadata.
3. **Exact string matching on `resource`, trailing slash included, and metadata cached at deploy time. Documented.**
   A one-character difference between the manifest and the served document fails, and fixing it means redeploying
   the add-on. The CDK stack therefore builds the URI from the API's own endpoint instead of typing it twice.
4. **Cognito cannot be the authorization server on its own. Documented.** Its discovery document omits
   `code_challenge_methods_supported`, offers only OIDC discovery, and has no resource-bound `client_credentials`.
   That is the whole reason for the facade (`src/hearthward/authz/facade_as.py`), a service that exists only to fill
   gaps in the metadata.
5. **A session id that does not exist. Documented.** Alexa+ keeps no MCP session id, so the server has to be stateless.

## The MCP Python SDK 2.3.0

6. **Newer than the tools' knowledge. Observed.** The SDK is post-cutoff for the models that helped write this code,
   so every API had to be read from the installed source before use.
7. **`GET /mcp` hangs. Observed.** In stateless mode the SDK's GET opens a server-sent-event stream that never ends;
   my first probe of the app never returned until the test runner killed it. Alexa+ only posts and API Gateway buffers events, so the app now
   answers GET and DELETE with 405.
8. **`MCPServer` writes tool schemas from function signatures. Observed.** The blueprint needs its schemas served
   byte for byte, so I used the low-level `Server` with raw definitions instead.
9. **Version negotiation echoes what the client asks for. Observed.** An `initialize` for 2024-11-05 was answered with
   2024-11-05 even though only three newer versions are accepted; a small middleware rewrites unaccepted requests so
   the server answers with its newest accepted version.
10. **The session manager runs once per app. Observed.** Reusing an app across test clients raises at the second
    startup, so the tests build a fresh app per client.

## AWS and CDK

11. **No PKCE setting on a Cognito app client in CDK. Observed.** I could not express "PKCE required" in the stack and
    did not claim it; the client is restricted to the authorization-code flow and the facade advertises S256.
12. **CloudFormation output ids cannot contain underscores. Observed.** The gate variables (`HW_MCP_RESOURCE_URI` and
    the rest) could not be output ids, so each is an output description instead.
13. **`consumed` is a DynamoDB reserved word. Observed.** The nonce store's first conditional write failed under
    `moto` with a validation error. It is the kind of mistake a real table would have shown only at the first approval.
14. **IAM cannot pin a sort key. Observed.** "Scoped to the exact item" in the blueprint is only half expressible: a
    session policy can limit the partition (`LeadingKeys`) and the attributes, not the sort key.
15. **`moto` gaps. Observed.** `GetObjectRetention` returned a 500 for an Object Lock object; the test checks the lock
    through `HeadObject` instead. IAM session policies are not evaluated, so they are tested for content, not effect.
16. **jsii on Windows leaves a temp directory behind. Observed.** CDK synthesis prints a Node `ENOTEMPTY` error while
    cleaning its temp folder, after the template is written.
17. **No CDK CLI here, and Docker needed to deploy. Observed.** Synthesis went through the Python library; the two
    container images are staged but not built.

## Building it

18. **Windows line endings versus signed bytes. Observed.** `core.autocrlf=true` would rewrite the fixtures that HMAC
    signatures cover. `.gitattributes` marks them (and all JSON) as not text, and a gate check watches it.
19. **npm and the "only PyPI" rule. Observed.** A lockfile normally needs the npm registry. The web app has no
    dependencies, so `npm install --package-lock-only --offline` produced one without any network.
20. **WebAuthn counters. Expected.** The blueprint requires the signature counter to increase; some synced passkeys
    always report 0 and would never be able to approve. Not tested on real devices yet.

21. **CloudFront refused a new account. Observed.** Deploying the console's hosting stack (a private S3 origin behind
    CloudFront with Origin Access Control, strict response headers and the four probe paths routed to the APIs) failed
    with HTTP 403: "Your account must be verified before you can add new CloudFront resources." CloudFormation rolled
    back cleanly. The console is therefore served locally from `web/`, its "Run live" probes are disabled, and the
    probe rows are recordings made by `tools/record_probe.py` (commit `ebc6552` holds the construct, `aea7956` reverts
    it). Verifying the account with AWS Support would unblock it.
