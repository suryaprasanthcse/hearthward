# Product feedback

Feedback for the teams behind the platforms HEARTHWARD builds on. It comes from one build, finished before the
first deployment, so it covers the documentation and the SDKs, and nothing about how the live services behave.
The evidence for each point is in `docs/friction-log.md` (entry numbers in brackets).

## Alexa+ MCP add-ons

- **Support `WWW-Authenticate` on 401, or document the exact response Alexa+ wants.** Today a resource server that
  follows the MCP authorization spec fails a requirement that is stated only in the Alexa+ documentation, and the
  SDK that implements the spec gives no way to turn the header off. [1]
- **Accept an authorization server the way the MCP authorization spec describes it.** Needing RFC 8414 metadata with
  `S256` in it, no OIDC discovery and no client registration is a narrow path; a short, tested checklist (or a
  validator command that reads a manifest and a metadata URL and says what is wrong) would have saved the façade
  design work. [2][4]
- **Say how strict the `resource` match is and offer a way to update it without a redeploy.** An exact match
  including the trailing slash, cached at deploy time, is easy to get wrong and slow to correct. [3]
- **Document what the simulator can and cannot test**, in particular whether a public MCP server can skip the
  service-level (client credentials) tier. This build did not settle that question.

## MCP Python SDK

- **Make a stateless GET answer 405 (or end the stream).** An endless stream on `GET` in stateless mode is surprising
  and hid behind a test that simply never finished. [7]
- **Let the authentication layer omit `WWW-Authenticate`.** A flag on `RequireAuthMiddleware` would remove the need
  to replace it. [1]
- **Offer a tool registry that takes ready-made schemas.** Serving a fixed contract needed the low-level API. [8]
- **Negotiate protocol versions against a configured set.** Echoing an unsupported requested version means each
  server writes its own guard. [9]
- **Allow the session manager's lifespan to run more than once**, or say so in the testing guide. [10]

## AWS CDK, Cognito and friends

- **Expose PKCE on Cognito app clients** so "S256 required" can be part of the stack instead of a statement in a
  README. [11]
- **Cognito discovery should include `code_challenge_methods_supported`.** It is the reason an entire service had to
  be written. [4]
- **Allow underscores in CloudFormation output ids**, or let an output carry a separate export name for the
  environment variable it feeds. [12]
- **Document IAM's limit for DynamoDB item scoping next to the session-policy examples:** partition and attributes
  only. [14]
- **Clean up jsii's temp folder reliably on Windows.** [16]

## What worked well

- Step Functions `.waitForTaskToken` with a heartbeat maps directly onto a human-in-the-loop ladder, and the pipeline
  could check its bounds mechanically.
- KMS `ECDSA_SHA_256` signatures verify with a short standard-library routine, which is what made an offline judge
  verifier possible.
- Object Lock in compliance mode is the right primitive for anchoring a ledger head, and the S3 API makes
  write-once easy to request.
