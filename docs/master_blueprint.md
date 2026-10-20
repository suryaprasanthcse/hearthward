# HEARTHWARD Master Blueprint

Version 0.1 · 2026-10-21 · Owner: Surya Prasanth R · Status: foundation (gate F)

This document is HEARTHWARD's single source of truth. Fenced blocks tagged `json hearthward:<name>`
are machine-read: `tools/pipeline.py` lints them and `tools/orrery.py` runs on them. Editing a block
changes the system, so run `python tools/pipeline.py --gate F` after every edit.

---

## 1. Purpose and track posture

HEARTHWARD helps people who live alone resist scams. It joins what the front door sees, what is said
in the room and what Alexa+ is asked into one bitemporal record, decides with an explicit error
budget, and never lets a voice in the room lower the household's protection.

| Item | Decision |
|---|---|
| Primary track | **Alexa+**: a self-hosted MCP add-on on Streamable HTTP that implements protocol 2025-11-25 and negotiates 2025-03-26 with the Alexa+ client. Tested in the Alexa+ web simulator. |
| Mini challenges | AWS Builder; Open Source |
| DOOR (Ring) and WRIST (Bee) | **Synthetic only.** Produced by the orrery and replay fixtures, and labeled `synthetic` in every envelope, every tool output (`data_origin`), the README and the video. Not entered in the Ring or Bee tracks, whose rules require real devices. |
| Deadline | Fri 2026-10-23 12:00 PT. The deployment stays up until judging closes Fri 2026-11-20 12:00 PT. |
| Deferred past the deadline | Managed Service for Apache Flink, the Fire TV app, Lambda MicroVM vision workers, Amazon Connect voice calls, real Ring and Bee devices. |

---

## 2. System topology (deadline build)

```
                          ALEXA+ (Amazon)                       CAREGIVER PHONE / BROWSER
                                │ Streamable HTTP, JSON responses          │ WebAuthn, HTTPS
                                ▼                                          ▼
 ┌─────────── API Gateway HTTP API "mcp" ───────────┐      ┌──── API Gateway HTTP API "web" ────┐
 │ POST /mcp   GET /.well-known/oauth-protected-…   │      │ approval page, verify page (Lambda) │
 └────────────────────────┬─────────────────────────┘      └──────────────────┬──────────────────┘
                          │ VPC Link → internal ALB                           │
                          ▼                                                   ▼
 ┌──────────────── ECS Fargate task (one per environment) ─────────────┐   λ broker (AXIOM pattern)
 │  mcp      uvicorn + mcp 2.3.0, stateless_http, json_response        │──▶ STS executor role (900 s,
 │  fusion   SQS FIFO consumer: SPRT, visits, episodes                 │     session policy, proof_hash tag)
 │  falkordb GNOMON kernel UDFs, pinned digest, EBS, AOF always        │        │
 └──────┬──────────────────────────────▲───────────────────────────────┘        ▼
        │ invoke (S- actions)          │ SQS FIFO (group = household)    DynamoDB SecurityState
        ▼                              │                                 DynamoDB ProofLedger, Nonces
   λ broker                   EventBridge Pipe ◀── DynamoDB Streams ◀── DynamoDB EventLedger (hash chain)
                                                                              ▲
 ┌──── API Gateway HTTP API "auth" (façade issuer) ────┐     λ ring_ingest ───┤ X-Signature (synthetic key)
 │ /.well-known/oauth-authorization-server  /authorize │     λ bee_ingest  ───┘ bridge HMAC (synthetic replay)
 │ /token  /jwks                    (λ facade_as)      │
 └──────────────┬──────────────────────────────────────┘     Step Functions Standard "InterventionLadder"
                ▼ 302 / proxy                                Bedrock (tactic extraction, cassettes in tests)
        Cognito user pool (Essentials, managed login)        KMS: attest signer, façade token signer, data key
```

| Component | Runtime | Responsibility | State it owns |
|---|---|---|---|
| `mcp` | Fargate, Python 3.12 | Alexa+ MCP endpoint, token validation, S+ tools, S- delegation to the broker | none (stateless) |
| `fusion` | Fargate, Python 3.12 | Ordered per-household processing: visits, episodes, SPRT, ladder start | GNOMON tenant graph `t:hh:<id>` |
| `falkordb` | Fargate sidecar | GNOMON kernel (`GRAPH.UDF LOAD`), bitemporal world model | EBS volume |
| `ring_ingest`, `bee_ingest` | Lambda | Authenticate raw bytes, dedupe, canonicalize, append to the ledger | `EventLedger` |
| `facade_as` | Lambda | RFC 8414 metadata, `/authorize` redirect, `/token` dispatch, service tokens | none |
| `broker` | Lambda | HX rules, WebAuthn verification, nonce consumption, STS minting | `ProofLedger`, `Nonces` |
| `InterventionLadder` | Step Functions Standard | L1 to L3 escalation with callback tokens | execution history |
| Cognito | managed | User identity for account linking and the caregiver web | user pool |

---

## 3. Trust boundaries

| ID | Crossing | Authentication | Trusted after crossing |
|---|---|---|---|
| TB-1 | Internet → `POST /mcp` | OAuth 2.1 bearer token (Cognito user token or façade service token) | The **household** principal. Never the speaker. |
| TB-2 | Alexa+ LLM arguments → tool handler | none beyond TB-1 | Arguments are **claims** (trust `claimed`) |
| TB-3 | Ring webhook → `ring_ingest` | `X-Signature: sha256=<hex>`, HMAC-SHA256 over raw body bytes | Authentic Ring delivery, trust `observed` |
| TB-4 | Bee bridge → `bee_ingest` | Bridge HMAC over the raw line | Authentic bridge delivery, trust `observed` |
| TB-5 | `mcp` → security state | IAM explicitly denies the `mcp` role any write to `SecurityState` | Only broker-minted executor sessions can write |
| TB-6 | Caregiver device → broker | WebAuthn assertion whose challenge is the action hash | A specific circle member approved a specific action |
| TB-7 | Bedrock output → decision core | Schema-validated enum features only | Observations; free text never reaches a privileged prompt |
| TB-8 | Server → web presentation | KMS signature over the exact bytes | Display only; the browser never makes authorization decisions |

Trust levels: `observed` (an authenticated sensor reported it), `claimed` (someone said it, including
the Alexa+ LLM's extraction), `verified` (confirmed through an independent channel, such as a call to an
official number or a circle member's passkey).

- **TL-1** Only `verified` facts can enable an S- action or lower Λ through a cross-device term.
- **TL-2** `claimed` facts may raise Λ.
- **TL-3** Language-model output is never `verified`.

---

## 4. Contracts

### 4.1 Event envelope

```json hearthward:envelope
{
  "type": "object",
  "additionalProperties": false,
  "required": ["v", "household", "source", "source_event_id", "kind", "valid", "src_time", "sent_time",
               "received_at", "seq", "raw_digest", "payload_digest", "auth", "prev", "head"],
  "properties": {
    "v": {"type": "integer", "const": 1},
    "household": {"type": "string", "pattern": "^hh_[0-9a-z]{8,32}$"},
    "source": {"type": "string", "enum": ["ring", "bee", "alexa", "system"]},
    "source_event_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "kind": {"type": "string", "enum": ["door.motion", "door.button", "room.consult", "room.concern",
                                        "room.s_minus", "wrist.conversation", "wrist.utterance",
                                        "system.verified_callback"]},
    "valid": {
      "type": "object",
      "additionalProperties": false,
      "required": ["lo", "hi"],
      "properties": {"lo": {"type": "integer", "minimum": 0}, "hi": {"type": "integer", "minimum": 0}}
    },
    "src_time": {"type": ["integer", "null"], "minimum": 0},
    "sent_time": {"type": ["integer", "null"], "minimum": 0},
    "received_at": {"type": "integer", "minimum": 0},
    "seq": {"type": "integer", "minimum": 1},
    "raw_digest": {"type": ["string", "null"], "pattern": "^sha256:[0-9a-f]{64}$"},
    "payload_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
    "auth": {
      "type": "object",
      "additionalProperties": false,
      "required": ["scheme", "verified", "synthetic"],
      "properties": {
        "scheme": {"type": "string", "enum": ["ring-hmac", "bridge-hmac", "oauth2.1", "internal"]},
        "verified": {"type": "boolean"},
        "synthetic": {"type": "boolean"}
      }
    },
    "prev": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
    "head": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
  }
}
```

Semantic rules that the schema cannot express:

- **E-1** `valid.lo ≤ valid.hi`.
- **E-2** `seq` starts at 1 and increases by exactly 1 per household.
- **E-3** `prev` is the `head` of `seq − 1`; the genesis `prev` is `sha256:` followed by 64 zeros.
- **E-4** `auth.synthetic` is `true` for every event the orrery or the replay harness produces.
- **E-5** No floating-point numbers anywhere (rule C-2).
- **E-6** `source_event_id` is unique per household and source. Duplicates are absorbed before the ledger.

How each source maps to the envelope:

| Source | `source_event_id` | `src_time` | `sent_time` | `valid` | `raw_digest` | `auth.scheme` |
|---|---|---|---|---|---|---|
| Ring webhook v1.1 | `meta.request_id` | `data.attributes.timestamp` (ms) | `meta.time` (ISO 8601, ns precision, truncated to ms) | `[src_time − ε_ring, src_time + ε_ring]` | SHA-256 of the raw body | `ring-hmac` |
| Bee stream line | `bee:<conversation_uuid>:<sha256(line)[:16]>` | `null` (`new-utterance` carries no time) | `null` | `[received_at − δ_bee, received_at]`; recovered by sync: the conversation's bounds | SHA-256 of the line | `bridge-hmac` |
| Alexa+ `tools/call` | `alexa:<ULID minted on receipt>` | `null` | `null` | `[received_at − δ_alexa, received_at]` | SHA-256 of the HTTP body | `oauth2.1` |
| System (verified callback, broker) | `sys:<ULID>` | `null` | `null` | `[received_at, received_at]` | `null` | `internal` |

### 4.2 Ledger chain

```
head_n = "sha256:" || hex( SHA-256( "hw-ledger-v1" || 0x00 || JCS(envelope_n without "head") ) )
```

The envelope contains `prev` and `seq`, so each head binds the entire history before it. Storage is
DynamoDB `EventLedger` (partition key `household`, sort key `seq`) plus a `HEAD` item. An append is
one `TransactWriteItems` call: put entry `seq = n` with `attribute_not_exists(seq)`, and update `HEAD`
on condition `head.seq = n − 1`. Losing the race means retrying with the new head.

### 4.3 HW-JCS canonicalization

- **C-1** The canonical form is RFC 8785 (JCS). Production code uses the `rfc8785` package.
- **C-2** HEARTHWARD restricts JCS to a profile: object keys match `^[a-z0-9_]+$` (ASCII, so UTF-16 and
  code-point key ordering agree), and numbers are integers with `|n| ≤ 2^53 − 1`. No floats, NaN or
  infinities. Within this profile, `json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
  yields the RFC 8785 bytes, which is what the orrery uses.
- **C-3** Times are integer UTC epoch milliseconds. Evidence values appear only as integer milli-nats (`*_mnats`).
- **C-4** A digest is `sha256:` followed by 64 lowercase hex characters.
- **C-5** Raw bytes are authenticated before parsing (Ring HMAC, bridge HMAC, HTTP body digest) and kept as
  `raw_digest`. Semantic digests use canonical bytes. **Never re-serialize JSON to verify a signature.**
- **C-6** Signatures (KMS `ECDSA_SHA_256` on an `ECC_NIST_P256` key) cover canonical bytes and travel as
  `{bytes_b64, sig_b64, key_id}`. Verifiers check the bytes first and parse second; they never re-canonicalize.
- **C-7** Domain-separation tags: `hw-ledger-v1`, `hw-action-v1`, `hw-decision-v1`, `hw-anchor-v1`.

### 4.4 Bitemporal model

**Valid time.** An instant event has a closed interval `V = [vf_lo, vf_hi]` that certainly contains its
true time. A durative fact (a visit, an episode) has a start bound `vf_lo` and an exclusive end `vt`
(∞ while ongoing), widened conservatively: `vf_lo` is the minimum over contributing events and
`vt = max(vf_hi) + visit_gap_ms` once it closes.

**Belief time.** A fact version is believed for ledger sequences `[ts, te)`, with `te = ∞` while it is
current, and `tt` holds the wall time of `ts`. A change never edits a version. It closes the old one
(`te = seq`) and appends a new one.

**Relations over uncertain intervals X and Y:**

| Relation | Definition | Used for |
|---|---|---|
| `possibly_overlaps(X, Y)` | `X.lo ≤ Y.hi ∧ Y.lo ≤ X.hi` | escalation |
| `certainly_before(X, Y)` | `X.hi < Y.lo` | de-escalation |
| `certainly_during(X, Y)` | `Y.lo ≤ X.lo ∧ X.hi ≤ Y.hi` | de-escalation |

**BT-A (asymmetry).** Escalation may rely on `possibly_*`. De-escalation (dismissing, closing a visit,
pausing) requires `certainly_*` and watermark passage.

**Watermarks.** `W_src = max(basis_src) − lateness_ms[src]`, where the basis is `src_time` for Ring and
`received_at` for Alexa+ and Bee. A visit closes only when `W_ring > hi + visit_gap_ms`. A dismissal
needs `W_src ≥ episode.hi` for every active source. Ring's lateness covers its full retry schedule
(1 s, 5 s, 30 s, 2 min, 10 min, 1 h = 4,356,000 ms). End of stream, which happens only in replay, sets W = ∞.
Events Ring sends from its dead-letter queue (kept 72 h) arrive later than any watermark. They become
belief revisions and never move a decision.

**Skew.** An event whose `src_time` exceeds `received_at` by more than `max_skew_ms` is quarantined: it is
kept in a quarantine table and never ledgered or ordered. This matches GNOMON's `MAX_SKEW_MS = 60_000`.

**Invariants:**

- **BT-1** Versions are immutable.
- **BT-2** Every decision records its belief sequence and the ledger head it read.
- **BT-3** Replaying the ledger to sequence S reproduces the beliefs held at S exactly.
- **BT-4** A late event never changes a decision record. It creates new versions and a revision note.
- **BT-5** A quarantined event never changes belief.

**GNOMON mapping.** GNOMON fact versions carry a point `vf` and `[ts, te)`. HEARTHWARD stores
`vf = vf_lo` in the kernel's field and `vf_hi` as an extra property, and computes interval relations in
the query layer. GNOMON's kernel UDFs, audits K1 to K9 and mutation suite stay untouched.

```json hearthward:temporal
{
  "epsilon_ring_ms": 2000,
  "delta_ms": {"alexa": 3000, "bee": 10000},
  "lateness_ms": {"ring": 4356000, "bee": 600000, "alexa": 5000},
  "max_skew_ms": 60000,
  "visit_gap_ms": 120000,
  "ring_retry_schedule_ms": [1000, 5000, 30000, 120000, 600000, 3600000],
  "bee_sync_interval_ms": 300000
}
```

---

## 5. Tool surface

### 5.1 Classes

| Class | Meaning | Failure direction |
|---|---|---|
| **S+** | Read-only, or raises safety (records a concern, brings family in) | Fails **open**. Worst case is a false alarm, rate-limited by HX-10. |
| **S-** | Lowers trust or protection (dismiss, pause, trust a vendor, change the circle, export evidence) | Fails **closed**. Returns `PROOF_REQUIRED` or `BLOCKED` unless a valid capability exists. |

### 5.2 Protocol rules

- **P-1** Streamable HTTP at exactly `/mcp`, with no redirect, `stateless_http=True` and `json_response=True`.
  Alexa+ keeps no MCP session ID, and API Gateway buffers server-sent events.
- **P-2** Accepted handshake versions: 2025-11-25, 2025-06-18 and 2025-03-26. The SDK negotiates, and Alexa+
  documents sending 2025-03-26. A request without `MCP-Protocol-Version` counts as 2025-03-26.
- **P-3** Nothing a 2025-03-26 session cannot use: no Tasks, no URL-mode elicitation, no sampling, no
  server-to-client requests. Tools are synchronous.
- **P-4** A tool that declares `outputSchema` returns `structuredContent` plus the same JSON in a text block.
- **P-5** A missing or invalid token gets `401` with a JSON body and **no `WWW-Authenticate` header**.
  Insufficient scope gets `403`.
- **P-6** DNS-rebinding protection stays on, with `allowed_hosts` set to the API Gateway host and `localhost:*`.
  Never rely on the SDK's localhost-only default.
- **P-7** Integers only on the wire. No schema uses `number`.
- **P-8** Every string input is bounded and every object schema is closed (`additionalProperties: false`).

### 5.3 Tool definitions

The `_meta` key `hearthward/class` records each tool's class. `evidence_mnats` is Λ in milli-nats.
`risk_level` maps Λ < 1.0 to `low`, 1.0 ≤ Λ < A to `elevated` and Λ ≥ A to `high`.

```json hearthward:tools
[
  {
    "name": "get_door_activity",
    "title": "Door activity",
    "description": "Who came to the door recently, from the household's door cameras. Read-only.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["window"],
      "properties": {"window": {"type": "string", "enum": ["last_15_min", "last_hour", "today"]}}
    },
    "outputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["visits", "summary", "data_origin"],
      "properties": {
        "visits": {
          "type": "array",
          "maxItems": 20,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["visit_id", "first_seen_ms", "last_seen_ms", "trigger", "visitor", "open"],
            "properties": {
              "visit_id": {"type": "string", "pattern": "^vis_[0-9A-HJKMNP-TV-Z]{26}$"},
              "first_seen_ms": {"type": "integer", "minimum": 0},
              "last_seen_ms": {"type": "integer", "minimum": 0},
              "cameras": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 40}},
              "trigger": {"type": "string", "enum": ["motion_human", "motion_other", "button_press"]},
              "vehicle_seen": {"type": "string", "enum": ["yes", "no", "unknown"]},
              "visitor": {"type": "string", "enum": ["known_circle", "known_vendor", "unknown"]},
              "open": {"type": "boolean"}
            }
          }
        },
        "summary": {"type": "string", "maxLength": 200},
        "data_origin": {"type": "string", "enum": ["live", "synthetic"]}
      }
    },
    "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false},
    "_meta": {"hearthward/class": "S+"}
  },
  {
    "name": "assess_visitor",
    "title": "Second opinion on a visitor or caller",
    "description": "Get a second opinion on someone at the door or on the phone. Every argument is the customer's report of what the visitor claimed; none of it is treated as verified.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "claimed_organization": {"type": "string", "maxLength": 80},
        "claimed_purpose": {"type": "string", "enum": ["utility", "repair", "delivery", "government", "bank", "charity", "family_member", "other"]},
        "claimed_identity_name": {"type": "string", "maxLength": 60},
        "claimed_relationship": {"type": "string", "enum": ["none", "grandchild", "child", "other_family", "official", "other"]},
        "payment_requested": {"type": "string", "enum": ["none", "cash", "gift_card", "wire", "crypto", "check", "card", "unknown"]},
        "urgency_claimed": {"type": "boolean"},
        "asked_secrecy": {"type": "boolean"}
      }
    },
    "outputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["risk_level", "reasons", "recommended_next_step", "episode_id", "evidence_mnats", "data_origin"],
      "properties": {
        "risk_level": {"type": "string", "enum": ["low", "elevated", "high"]},
        "reasons": {
          "type": "array",
          "maxItems": 5,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["code", "plain_text"],
            "properties": {
              "code": {"type": "string", "enum": ["NO_VISIT_ON_CAMERA", "VISIT_IN_PROGRESS", "NOT_IN_TRUSTED_VENDORS", "SECRECY_REQUEST", "IRREVERSIBLE_PAYMENT", "IDENTITY_MISMATCH", "URGENCY", "AUTHORITY_CLAIM", "VERIFIED_CONTACT"]},
              "plain_text": {"type": "string", "maxLength": 140}
            }
          }
        },
        "recommended_next_step": {"type": "string", "enum": ["verify_via_official_number", "call_trusted_contact", "decline_politely", "no_action"]},
        "episode_id": {"type": "string", "pattern": "^ep_[0-9A-HJKMNP-TV-Z]{26}$"},
        "evidence_mnats": {"type": "integer", "minimum": -100000, "maximum": 100000},
        "data_origin": {"type": "string", "enum": ["live", "synthetic"]}
      }
    },
    "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false},
    "_meta": {"hearthward/class": "S+"}
  },
  {
    "name": "verify_organization_contact",
    "title": "Find an organization's official number",
    "description": "Look up an organization's official phone number from the household's trusted vendors or the curated directory. Never returns a number supplied by a visitor.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["organization_name"],
      "properties": {"organization_name": {"type": "string", "minLength": 1, "maxLength": 80}}
    },
    "outputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["found", "source", "advice", "data_origin"],
      "properties": {
        "found": {"type": "boolean"},
        "display_name": {"type": "string", "maxLength": 80},
        "official_phone_e164": {"type": "string", "pattern": "^\\+[1-9][0-9]{6,14}$"},
        "source": {"type": "string", "enum": ["trusted_vendors", "curated_directory", "none"]},
        "advice": {"type": "string", "enum": ["call_official_number", "no_official_record"]},
        "data_origin": {"type": "string", "enum": ["live", "synthetic"]}
      }
    },
    "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true},
    "_meta": {"hearthward/class": "S+"}
  },
  {
    "name": "explain_last_alert",
    "title": "Why did Hearthward raise an alert?",
    "description": "Explain the most recent alert in plain language: what was seen, what was said and how strong the evidence was. Read-only.",
    "inputSchema": {"type": "object", "additionalProperties": false, "properties": {}},
    "outputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["state", "reasons", "intervene_threshold_mnats", "data_origin"],
      "properties": {
        "episode_id": {"type": "string", "pattern": "^ep_[0-9A-HJKMNP-TV-Z]{26}$"},
        "state": {"type": "string", "enum": ["none", "open", "intervening", "closed_scam", "closed_benign", "closed_inconclusive", "dismissed"]},
        "reasons": {
          "type": "array",
          "maxItems": 5,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["code", "plain_text"],
            "properties": {
              "code": {"type": "string", "enum": ["NO_VISIT_ON_CAMERA", "VISIT_IN_PROGRESS", "NOT_IN_TRUSTED_VENDORS", "SECRECY_REQUEST", "IRREVERSIBLE_PAYMENT", "IDENTITY_MISMATCH", "URGENCY", "AUTHORITY_CLAIM", "VERIFIED_CONTACT"]},
              "plain_text": {"type": "string", "maxLength": 140}
            }
          }
        },
        "evidence_mnats": {"type": "integer", "minimum": -100000, "maximum": 100000},
        "intervene_threshold_mnats": {"type": "integer", "const": 4554},
        "decided_at_ms": {"type": "integer", "minimum": 0},
        "belief_seq": {"type": "integer", "minimum": 0},
        "attestation_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "data_origin": {"type": "string", "enum": ["live", "synthetic"]}
      }
    },
    "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false},
    "_meta": {"hearthward/class": "S+"}
  },
  {
    "name": "raise_concern",
    "title": "Tell Hearthward something feels wrong",
    "description": "Record a concern about a visitor, call or message. The text is analyzed as untrusted content and can only raise the alert level, never lower it.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["concern", "about"],
      "properties": {
        "concern": {"type": "string", "minLength": 1, "maxLength": 280},
        "about": {"type": "string", "enum": ["door", "phone", "message", "other"]}
      }
    },
    "outputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["acknowledged", "episode_id", "next_step", "data_origin"],
      "properties": {
        "acknowledged": {"type": "boolean"},
        "episode_id": {"type": "string", "pattern": "^ep_[0-9A-HJKMNP-TV-Z]{26}$"},
        "next_step": {"type": "string", "enum": ["monitoring", "family_notified", "call_trusted_contact"]},
        "data_origin": {"type": "string", "enum": ["live", "synthetic"]}
      }
    },
    "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false},
    "_meta": {"hearthward/class": "S+"}
  },
  {
    "name": "call_trusted_contact",
    "title": "Bring in family now",
    "description": "Notify a trusted family member immediately and ask them to call. Uses only numbers stored in the trusted circle.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "properties": {"contact_role": {"type": "string", "enum": ["primary", "secondary"]}}
    },
    "outputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "ladder_level", "data_origin"],
      "properties": {
        "status": {"type": "string", "enum": ["notified", "already_notified", "no_contact_configured"]},
        "contact_display_name": {"type": "string", "maxLength": 60},
        "ladder_level": {"type": "string", "enum": ["L1", "L2", "L3"]},
        "data_origin": {"type": "string", "enum": ["live", "synthetic"]}
      }
    },
    "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true},
    "_meta": {"hearthward/class": "S+"}
  },
  {
    "name": "dismiss_alert",
    "title": "Dismiss an alert",
    "description": "Dismiss an alert as a false alarm. This lowers protection, so it always needs confirmation from a trusted family member's phone.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["episode_id", "reason"],
      "properties": {
        "episode_id": {"type": "string", "pattern": "^ep_[0-9A-HJKMNP-TV-Z]{26}$"},
        "reason": {"type": "string", "enum": ["false_alarm", "known_person", "resolved_with_family"]}
      }
    },
    "outputSchema": {
      "type": "object",
      "oneOf": [
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "episode_id", "proof_hash"],
          "properties": {
            "status": {"type": "string", "const": "DISMISSED"},
            "episode_id": {"type": "string", "pattern": "^ep_[0-9A-HJKMNP-TV-Z]{26}$"},
            "proof_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "challenge_id", "approve_on", "rules", "expires_at_ms", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "PROOF_REQUIRED"},
            "challenge_id": {"type": "string", "pattern": "^chl_[0-9A-HJKMNP-TV-Z]{26}$"},
            "approve_on": {"type": "string", "enum": ["caregiver_phone", "elder_phone"]},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "expires_at_ms": {"type": "integer", "minimum": 0},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "rules", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "BLOCKED"},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        }
      ]
    },
    "annotations": {"readOnlyHint": false, "destructiveHint": true, "idempotentHint": true, "openWorldHint": false},
    "_meta": {"hearthward/class": "S-"}
  },
  {
    "name": "pause_monitoring",
    "title": "Pause monitoring",
    "description": "Pause alerts for up to 24 hours. This lowers protection, so it needs confirmation from a trusted family member's phone and expires on its own.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["hours", "reason"],
      "properties": {
        "hours": {"type": "integer", "minimum": 1, "maximum": 24},
        "reason": {"type": "string", "enum": ["travel", "visitors", "maintenance", "other"]}
      }
    },
    "outputSchema": {
      "type": "object",
      "oneOf": [
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "until_ms", "proof_hash"],
          "properties": {
            "status": {"type": "string", "const": "PAUSED"},
            "until_ms": {"type": "integer", "minimum": 0},
            "proof_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "challenge_id", "approve_on", "rules", "expires_at_ms", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "PROOF_REQUIRED"},
            "challenge_id": {"type": "string", "pattern": "^chl_[0-9A-HJKMNP-TV-Z]{26}$"},
            "approve_on": {"type": "string", "enum": ["caregiver_phone", "elder_phone"]},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "expires_at_ms": {"type": "integer", "minimum": 0},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "rules", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "BLOCKED"},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        }
      ]
    },
    "annotations": {"readOnlyHint": false, "destructiveHint": true, "idempotentHint": false, "openWorldHint": false},
    "_meta": {"hearthward/class": "S-"}
  },
  {
    "name": "add_trusted_vendor",
    "title": "Add a trusted vendor",
    "description": "Add a business to the household's trusted vendors. This lowers protection, so it needs confirmation from a trusted family member's phone.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["name", "phone_e164", "category"],
      "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "phone_e164": {"type": "string", "pattern": "^\\+[1-9][0-9]{6,14}$"},
        "category": {"type": "string", "enum": ["utility", "repair", "delivery", "medical", "other"]}
      }
    },
    "outputSchema": {
      "type": "object",
      "oneOf": [
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "vendor_id", "proof_hash"],
          "properties": {
            "status": {"type": "string", "const": "ADDED"},
            "vendor_id": {"type": "string", "pattern": "^ven_[0-9A-HJKMNP-TV-Z]{26}$"},
            "proof_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "challenge_id", "approve_on", "rules", "expires_at_ms", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "PROOF_REQUIRED"},
            "challenge_id": {"type": "string", "pattern": "^chl_[0-9A-HJKMNP-TV-Z]{26}$"},
            "approve_on": {"type": "string", "enum": ["caregiver_phone", "elder_phone"]},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "expires_at_ms": {"type": "integer", "minimum": 0},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "rules", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "BLOCKED"},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        }
      ]
    },
    "annotations": {"readOnlyHint": false, "destructiveHint": true, "idempotentHint": false, "openWorldHint": false},
    "_meta": {"hearthward/class": "S-"}
  },
  {
    "name": "edit_trusted_circle",
    "title": "Change the trusted circle",
    "description": "Add or remove a family member in the trusted circle. This changes who can approve actions, so another member must confirm on their phone.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["operation", "display_name", "role"],
      "properties": {
        "operation": {"type": "string", "enum": ["add", "remove"]},
        "display_name": {"type": "string", "minLength": 1, "maxLength": 60},
        "phone_e164": {"type": "string", "pattern": "^\\+[1-9][0-9]{6,14}$"},
        "role": {"type": "string", "enum": ["primary", "secondary", "member"]}
      }
    },
    "outputSchema": {
      "type": "object",
      "oneOf": [
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "proof_hash"],
          "properties": {
            "status": {"type": "string", "const": "UPDATED"},
            "proof_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "challenge_id", "approve_on", "rules", "expires_at_ms", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "PROOF_REQUIRED"},
            "challenge_id": {"type": "string", "pattern": "^chl_[0-9A-HJKMNP-TV-Z]{26}$"},
            "approve_on": {"type": "string", "enum": ["caregiver_phone", "elder_phone"]},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "expires_at_ms": {"type": "integer", "minimum": 0},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "rules", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "BLOCKED"},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        }
      ]
    },
    "annotations": {"readOnlyHint": false, "destructiveHint": true, "idempotentHint": false, "openWorldHint": false},
    "_meta": {"hearthward/class": "S-"}
  },
  {
    "name": "share_evidence",
    "title": "Share evidence outside the family",
    "description": "Send an alert's evidence to an outside organization such as a bank's fraud team. It needs a family member's confirmation and the elder's recorded consent.",
    "inputSchema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["episode_id", "recipient"],
      "properties": {
        "episode_id": {"type": "string", "pattern": "^ep_[0-9A-HJKMNP-TV-Z]{26}$"},
        "recipient": {"type": "string", "enum": ["bank_fraud_team", "police_non_emergency"]}
      }
    },
    "outputSchema": {
      "type": "object",
      "oneOf": [
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "share_id", "proof_hash"],
          "properties": {
            "status": {"type": "string", "const": "SHARED"},
            "share_id": {"type": "string", "pattern": "^shr_[0-9A-HJKMNP-TV-Z]{26}$"},
            "proof_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "challenge_id", "approve_on", "rules", "expires_at_ms", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "PROOF_REQUIRED"},
            "challenge_id": {"type": "string", "pattern": "^chl_[0-9A-HJKMNP-TV-Z]{26}$"},
            "approve_on": {"type": "string", "enum": ["caregiver_phone", "elder_phone"]},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "expires_at_ms": {"type": "integer", "minimum": 0},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "rules", "spoken_hint"],
          "properties": {
            "status": {"type": "string", "const": "BLOCKED"},
            "rules": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": "^HX-(0[1-9]|1[0-2])$"}},
            "spoken_hint": {"type": "string", "maxLength": 160}
          }
        }
      ]
    },
    "annotations": {"readOnlyHint": false, "destructiveHint": true, "idempotentHint": false, "openWorldHint": true},
    "_meta": {"hearthward/class": "S-"}
  }
]
```

### 5.4 HX invariants

| ID | Title | Applies to | Rule | Enforced at | On failure |
|---|---|---|---|---|---|
| HX-01 | Open-episode lock | S- | No S- action while an episode is open or intervening, or within `cooldown_ms` after it closes, unless two distinct circle members approve | broker | REQUIRE_DUAL |
| HX-02 | Proof origin | S- | Requires a WebAuthn assertion from a circle member's registered passkey. An Alexa token alone never suffices. | broker | PROOF_REQUIRED |
| HX-03 | Action binding | S- | The assertion challenge must equal the action hash (§5.5) | broker | BLOCK |
| HX-04 | Single use | S- | The nonce is consumed by a conditional write, and the signature counter must increase | broker | BLOCK |
| HX-05 | Number provenance | `verify_organization_contact`, `call_trusted_contact` | Numbers come only from verified circle and vendor entries or the curated directory | mcp | BLOCK |
| HX-06 | Vendor cooling-off | `add_trusted_vendor` | No vendor added within 24 h of a visit or consultation that claimed a matching name | broker | BLOCK |
| HX-07 | Circle changes | `edit_trusted_circle` | Needs proof from a different existing member, and every member is notified | broker | PROOF_REQUIRED |
| HX-08 | Bounded pause | `pause_monitoring` | At most 24 h, expires on its own, cannot start or extend during an open episode | broker | BLOCK |
| HX-09 | Simulated effect | S- | Applied first in a counterfactual branch. If it closes an episode or removes evidence, dual control is required. | kernel | REQUIRE_DUAL |
| HX-10 | Safety-raising rate limits | `raise_concern`, `call_trusted_contact` | 6 concerns per hour per household and 3 calls per episode. CUSUM on alert volume demotes auto-escalation. | mcp | THROTTLE |
| HX-11 | Fail closed only downward | all | If the broker, ledger or nonce store is down, S- returns BLOCKED and S+ keeps working | mcp | BLOCK |
| HX-12 | Evidence egress | `share_evidence` | Needs proof plus the elder's recorded consent | broker+iam | PROOF_REQUIRED |

```json hearthward:hx
[
  {"id": "HX-01", "title": "Open-episode lock", "applies_to": ["S-"], "statement": "No S- action while an episode is open or intervening, or within cooldown_ms after it closes, unless two distinct circle members approve.", "enforced_at": "broker", "on_fail": "REQUIRE_DUAL"},
  {"id": "HX-02", "title": "Proof origin", "applies_to": ["S-"], "statement": "An S- action needs a WebAuthn assertion from a passkey registered to a trusted-circle member. A valid Alexa access token alone never suffices: it identifies the household, not the speaker.", "enforced_at": "broker", "on_fail": "PROOF_REQUIRED"},
  {"id": "HX-03", "title": "Action binding", "applies_to": ["S-"], "statement": "The assertion challenge must equal the action hash SHA-256('hw-action-v1' || 0x00 || JCS({tool, args, household, episode_version, nonce, exp})). Any mismatch blocks.", "enforced_at": "broker", "on_fail": "BLOCK"},
  {"id": "HX-04", "title": "Single use", "applies_to": ["S-"], "statement": "Each challenge nonce is consumed by a conditional write before the action runs; a second use blocks. The passkey signature counter must increase.", "enforced_at": "broker", "on_fail": "BLOCK"},
  {"id": "HX-05", "title": "Number provenance", "applies_to": ["verify_organization_contact", "call_trusted_contact"], "statement": "Phone numbers used to verify or to call come only from verified trusted-circle and trusted-vendor entries or the curated directory, never from tool arguments or conversation text.", "enforced_at": "mcp", "on_fail": "BLOCK"},
  {"id": "HX-06", "title": "Vendor cooling-off", "applies_to": ["add_trusted_vendor"], "statement": "A vendor cannot be added within 24 hours of a visit or consultation that claimed a matching organization name (case-folded, whitespace-normalized).", "enforced_at": "broker", "on_fail": "BLOCK"},
  {"id": "HX-07", "title": "Circle changes", "applies_to": ["edit_trusted_circle"], "statement": "Changing the trusted circle needs proof from an existing member other than the person being added or removed, and every member is notified.", "enforced_at": "broker", "on_fail": "PROOF_REQUIRED"},
  {"id": "HX-08", "title": "Bounded pause", "applies_to": ["pause_monitoring"], "statement": "A pause lasts at most 24 hours, expires on its own and cannot start or be extended while an episode is open.", "enforced_at": "broker", "on_fail": "BLOCK"},
  {"id": "HX-09", "title": "Simulated effect", "applies_to": ["S-"], "statement": "Every S- action is first applied in a counterfactual branch of the household graph. If it would close an open episode or remove an evidence source, two distinct circle members must approve.", "enforced_at": "kernel", "on_fail": "REQUIRE_DUAL"},
  {"id": "HX-10", "title": "Safety-raising rate limits", "applies_to": ["raise_concern", "call_trusted_contact"], "statement": "raise_concern is limited to 6 per hour per household and call_trusted_contact to 3 per episode. A CUSUM on alert volume demotes automatic escalation to notify-only.", "enforced_at": "mcp", "on_fail": "THROTTLE"},
  {"id": "HX-11", "title": "Fail closed only downward", "applies_to": ["*"], "statement": "If the broker, ledger or nonce store is unavailable, every S- tool returns BLOCKED while S+ tools keep working.", "enforced_at": "mcp", "on_fail": "BLOCK"},
  {"id": "HX-12", "title": "Evidence egress", "applies_to": ["share_evidence"], "statement": "Evidence leaves the trusted circle only with proof and the elder's recorded consent in the consent ledger.", "enforced_at": "broker+iam", "on_fail": "PROOF_REQUIRED"}
]
```

### 5.5 Proof-carrying S- calls

1. **Declare.** `mcp` receives an S- call and invokes the broker with `{tool, args, household, episode_version}`.
2. **Compute.** The broker applies the action in a GNOMON `GRAPH.COPY` branch and diffs it (HX-09).
3. **Decide.** HX-01 to HX-12 run as table-driven code, with no model in the path. A rule failure returns `BLOCKED`. Otherwise:
4. **Challenge.** The broker draws a 128-bit nonce, sets `exp = now + 300 000 ms` and computes
   `action_hash = SHA-256("hw-action-v1" || 0x00 || JCS({tool, args, household, episode_version, nonce, exp}))`.
   It stores the challenge in `Nonces`, pushes an approval link to the circle, and returns `PROOF_REQUIRED`.
5. **Approve.** The approval page calls `navigator.credentials.get` with `challenge = action_hash`, using
   HEARTHWARD's own relying party. Cognito's passkeys cannot sign a caller-chosen challenge.
6. **Mint.** The broker verifies the assertion (origin, RP ID, signature counter), consumes the nonce
   with a conditional write, and calls `sts:AssumeRole` on the executor role. The session lasts 900 s (the STS
   minimum), carries a session policy scoped to the exact `SecurityState` item and attributes, and is tagged
   `proof_hash`. The change is applied, `ProofLedger` records it, and CloudTrail links the write to its proof.

---

## 6. Decision core: HMM likelihoods inside Wald's SPRT

### 6.1 Observations

Each WRIST utterance and each ROOM claim becomes a binary vector over seven tactic features. In production,
a Bedrock extractor fills it from schema-validated enums (TB-7). In the orrery, scenarios label it by hand.

| Feature | Present when the speaker… |
|---|---|
| `authority` | claims to act for an organization, an agency or family |
| `urgency` | imposes a deadline ("today", "right now", "or else") |
| `secrecy` | asks for silence or isolation from family |
| `payment` | asks for an irreversible payment (cash, gift card, wire, crypto, courier) |
| `remote_access` | asks to install software, share a screen or read out a one-time code |
| `pressure` | uses fear, threats or emotional leverage |
| `info_request` | asks for personal or financial identifiers |

### 6.2 Hypotheses

- **H1, the scam script:** an HMM over five stages: `benign → pretext → authority → pressure → extraction`.
  It is left-to-right, so a script never returns to an earlier stage. It may skip stages, and it may start in
  `authority` or `pressure`, because cold-open scams ("Grandma, I've been arrested") skip the pretext. Its
  benign stage emits exactly like H0, so small talk inside a scam carries zero evidence.
- **H0, a benign conversation:** features are independent Bernoulli draws with rates `h0`.

### 6.3 Parameters

The emission matrix holds `P(feature present | stage)` before extractor noise:

| Stage | authority | urgency | secrecy | payment | remote_access | pressure | info_request |
|---|---|---|---|---|---|---|---|
| benign | 0.06 | 0.06 | 0.01 | 0.02 | 0.01 | 0.04 | 0.03 |
| pretext | 0.40 | 0.08 | 0.02 | 0.03 | 0.03 | 0.08 | 0.08 |
| authority | 0.45 | 0.15 | 0.05 | 0.06 | 0.10 | 0.15 | 0.20 |
| pressure | 0.15 | 0.40 | 0.30 | 0.12 | 0.08 | 0.45 | 0.10 |
| extraction | 0.12 | 0.30 | 0.20 | 0.55 | 0.30 | 0.25 | 0.35 |
| **H0** | 0.06 | 0.06 | 0.01 | 0.02 | 0.01 | 0.04 | 0.03 |

The rates are per utterance. One sentence usually carries one or two tactics, so no stage sets a rate high
enough to make the *absence* of a tactic strong evidence on its own.

The extractor is modeled as a noisy channel with true-positive rate `tpr_k` and false-positive rate `fpr_k`.
The effective rate is `p̂ = tpr·p + fpr·(1 − p)`, applied to both hypotheses.

```json hearthward:sprt
{
  "alpha": 0.01,
  "beta": 0.05,
  "thresholds_reported": {"A": 4.554, "B": -2.986},
  "stages": ["benign", "pretext", "authority", "pressure", "extraction"],
  "features": ["authority", "urgency", "secrecy", "payment", "remote_access", "pressure", "info_request"],
  "initial": [0.5, 0.3, 0.1, 0.1, 0.0],
  "transitions": [
    [0.85, 0.10, 0.03, 0.02, 0.00],
    [0.00, 0.55, 0.30, 0.12, 0.03],
    [0.00, 0.00, 0.50, 0.35, 0.15],
    [0.00, 0.00, 0.00, 0.55, 0.45],
    [0.00, 0.00, 0.00, 0.00, 1.00]
  ],
  "emissions": [
    [0.06, 0.06, 0.01, 0.02, 0.01, 0.04, 0.03],
    [0.40, 0.08, 0.02, 0.03, 0.03, 0.08, 0.08],
    [0.45, 0.15, 0.05, 0.06, 0.10, 0.15, 0.20],
    [0.15, 0.40, 0.30, 0.12, 0.08, 0.45, 0.10],
    [0.12, 0.30, 0.20, 0.55, 0.30, 0.25, 0.35]
  ],
  "h0": [0.06, 0.06, 0.01, 0.02, 0.01, 0.04, 0.03],
  "extractor": {
    "tpr": [0.85, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85],
    "fpr": [0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02]
  },
  "cross_device_llr": {
    "door_unknown_overlap": 0.7,
    "identity_mismatch": 3.0,
    "untrusted_org_irreversible_payment": 1.2,
    "verified_official_callback": -3.5
  },
  "cooldown_ms": 1800000
}
```

### 6.4 Recursion

For observation `f_t`, with `e_s(f) = Σ_k [f_k·log p̂_{s,k} + (1 − f_k)·log(1 − p̂_{s,k})]`:

```
log α_1(s) = log π_s + e_s(f_1)
log α_t(s) = logsumexp_{s'} ( log α_{t−1}(s') + log T_{s's} ) + e_s(f_t)
log L1_t   = logsumexp_s log α_t(s)
log L0_t   = Σ_{u ≤ t} Σ_k [f_{u,k}·log q̂_k + (1 − f_{u,k})·log(1 − q̂_k)]
Λ_t        = (log L1_t − log L0_t) + Σ (cross-device terms applied so far)
```

All of it runs in log space; the zero entries of `T` and `π` contribute −∞, never `log 0`.

A ROOM claim is a **partial observation**. Its listed features are present, and every other feature is
unobserved and marginalized out, not counted as absent. A claim causes no stage transition, because it
summarizes turns already heard:

```
log α_t(s) = log α_{t−1}(s) + Σ_{k ∈ claim} log p̂_{s,k}        log L0_t = log L0_{t−1} + Σ_{k ∈ claim} log q̂_k
```

### 6.5 Thresholds

With α = 0.01 (false-alarm rate) and β = 0.05 (miss rate), Wald's bounds are:

```
A = ln((1 − β) / α) = ln 95      ≈ +4.554   → intervene      (4554 mnats)
B = ln(β / (1 − α)) = ln 0.0505  ≈ −2.986   → dismiss        (−2986 mnats)
```

Under the HMM's dependence these bounds are approximations. The thresholds that ship are set from the
false-alarm rate measured on replay (§6.8). A and B change only through this block.

### 6.6 Cross-device terms and decision rules

| Term | Condition (each applied at most once per episode) | LLR |
|---|---|---|
| `door_unknown_overlap` | `possibly_overlaps(episode, [visit.lo, visit.hi + visit_gap_ms])` for a visit by an unknown human | +0.7 |
| `identity_mismatch` | A claimed relative's name conflicts with the household's verified facts | +3.0 |
| `untrusted_org_irreversible_payment` | A ROOM claim names an organization outside the trusted vendors **and** asks for an irreversible payment | +1.2 |
| `verified_official_callback` | A call back to the official number confirms the visit (trust `verified`) | −3.5 |

- **DC-1** Only terms named `verified_*` may be negative (TL-1).
- **DC-2** A ROOM claim adds only features the episode has not yet observed, as a partial observation (§6.4), so the elder's summary neither double counts what WRIST already heard nor penalizes what it left out. The rule depends on order: a WRIST line that arrives after the summary still counts, so at most one duplicate per feature remains.
- **DC-3** Each cross-device term applies at most once per episode.
- **DC-4** Extractor output is schema-validated enums. Free text never reaches the accumulator.

### 6.7 Episode lifecycle

```
            Λ ≥ A                            caregiver resolves
  open ───────────────▶ intervening ───────────────────────────▶ closed_scam | closed_benign
    │  Λ ≤ B and W_src ≥ episode.hi for every active source
    ├──────────────────▶ dismissed
    │  end of stream with B < Λ < A
    └──────────────────▶ closed_inconclusive
```

Evidence that arrives after a decision still updates Λ and beliefs. The decision record stays immutable
(BT-4), is canonicalized, and is digested with the `hw-decision-v1` tag.

### 6.8 Calibration

The parameters above are priors. Before they ship, they are fitted on a labeled replay corpus. ROC
curves, false alarms per household-week and detection latency are measured there, and `tpr`/`fpr` come
from the extractor's confusion matrix on held-out data.

### 6.9 Conformance interface (gate G4)

`hearthward.decision.hmm.lambda_trace_mnats(observations: list[list[str] | dict], params: dict) -> list[int]`
returns Λ in milli-nats after each observation, without cross-device terms. A list is an utterance; a
`{"claim": [...]}` object is a ROOM claim, a partial observation. `params` is the `hearthward:sprt` block.
`tools/pipeline.py` compares the result with `orrery.reference_trace_mnats` on `orrery.CONFORMANCE_VECTORS`,
and every value must match exactly.

---

## 7. OAuth 2.1 façade in front of Cognito

### 7.1 Why Cognito cannot be the authorization server directly

| # | Alexa+ requires | Cognito provides | Source |
|---|---|---|---|
| 1 | `code_challenge_methods_supported` containing `S256` in auth-server metadata; otherwise deploy is blocked | Its discovery document omits the field, although PKCE S256 works | Alexa+ account linking docs; MCP TypeScript SDK PR #992 |
| 2 | RFC 8414 OAuth metadata ("OpenID Connect (OIDC)" is listed as not supported) | Only OIDC discovery (`/.well-known/openid-configuration`) | Alexa+ MCP authentication docs |
| 3 | Tier-1 `client_credentials` with a validated `resource` and a service scope such as `mcp:service` | No resource binding on M2M grants; custom scopes take the form `identifier/name` | Cognito resource-binding docs |
| 4 | No `WWW-Authenticate` header on 401 | n/a (resource-server side) | Alexa+ MCP authentication docs |

### 7.2 Architecture

The façade is its own API Gateway HTTP API, so its issuer is a bare host with no path. One Lambda, `facade_as`, serves:

| Path | Behavior |
|---|---|
| `GET /.well-known/oauth-authorization-server` | RFC 8414 metadata (below). The same document is also served at `/.well-known/openid-configuration` for clients that only try that path. |
| `GET /authorize` | `302` to Cognito managed login `/oauth2/authorize` with every query parameter unchanged (`client_id`, `redirect_uri`, `state`, `code_challenge`, `code_challenge_method=S256`, `resource`, `scope`). Cognito binds `resource` into the access token's `aud`. |
| `POST /token` with `authorization_code` or `refresh_token` | Proxied to Cognito `/oauth2/token`, with Basic auth passed through. `resource` is forwarded, or stripped if G2 shows that Cognito rejects it at the token endpoint. Refresh tokens keep the original `aud`. |
| `POST /token` with `client_credentials` | Handled by the façade itself. It authenticates the Alexa service client with HTTP Basic, requires `resource` equal to the canonical MCP URI and `scope=mcp:service`, then issues an ES256 JWT signed by the KMS key `facade-token-signer` (`iss` = façade, `aud` = resource, `exp` ≤ 3600 s, `jti`). No refresh token. Errors follow RFC 6749 §5.2. |
| `GET /jwks` | The façade signer's public key |

Protected-resource metadata, served by `mcp` at `/.well-known/oauth-protected-resource`:

```
{
  "resource": "https://<mcp-api-id>.execute-api.<region>.amazonaws.com/mcp",
  "authorization_servers": ["https://<auth-api-id>.execute-api.<region>.amazonaws.com"],
  "scopes_supported": ["openid", "https://<mcp-api-id>.execute-api.<region>.amazonaws.com/mcp/tools", "mcp:service"]
}
```

`resource` must match the add-on manifest exactly, trailing slash included. Alexa+ reads only
`authorization_servers[0]`, and caches this metadata at deploy time, so these URLs must never change.

Façade authorization-server metadata:

```
{
  "issuer": "https://<auth-api-id>.execute-api.<region>.amazonaws.com",
  "authorization_endpoint": "https://<auth-api-id>.execute-api.<region>.amazonaws.com/authorize",
  "token_endpoint": "https://<auth-api-id>.execute-api.<region>.amazonaws.com/token",
  "jwks_uri": "https://<auth-api-id>.execute-api.<region>.amazonaws.com/jwks",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
  "scopes_supported": ["openid", "https://<mcp-api-id>.execute-api.<region>.amazonaws.com/mcp/tools", "mcp:service"]
}
```

### 7.3 Token validation at `/mcp`

| Token | Issuer | Required claims | Methods allowed |
|---|---|---|---|
| User (account linked) | Cognito user pool | `token_use = access`; `aud` = canonical MCP URI (needs Essentials or Plus with managed login); `client_id` = Alexa app client; a valid signature against JWKS `kid`; `exp` with 60 s leeway | everything |
| Service (Tier 1) | Façade | `aud` = canonical MCP URI, `scope = mcp:service`, a valid ES256 signature | `initialize`, `notifications/initialized`, `tools/list`, `ping` |

ID tokens are rejected. Cognito signs access tokens and ID tokens with different keys.

### 7.4 Questions G2 settles

1. Does Alexa+ require Tier 1 for a public MCP server ("if you have a private MCP Server")? Try public discovery first.
2. Does Cognito's token endpoint accept a repeated `resource` parameter?
3. Which metadata path does `alexa-ai configure-account-linking` read? Check its discovery summary.

---

## 8. Escalation ladder

A Step Functions Standard workflow, `InterventionLadder`, named after the episode so each episode runs
exactly one ladder.

- **L1** notifies the caregiver web view and the elder's phone, in parallel.
- **L2** is a `Map` over the trusted circle in priority order, each step a `.waitForTaskToken` task with
  `TimeoutSeconds: 90` and `HeartbeatSeconds: 30`.
- **L3** (voice call) is deferred.
- **New evidence:** the fusion worker stores the current task token on the episode and calls
  `SendTaskSuccess(token, {"signal": "ESCALATE"})`, which a `Choice` state routes.

At about 14 state transitions × $0.000025, one episode costs about $0.00035.

---

## 9. Integrity and verification

- **Decision records** carry `{episode, decision, at_seq, ledger_head, belief_seq, lambda_mnats, reasons,
  thresholds_mnats, params_digest, extractor_model_ids, cross_terms}`. They are digested with `hw-decision-v1`
  and signed by KMS (`attest-signer`). `params_digest` is the SHA-256 of the `hearthward:sprt` block serialized as
  sorted, compact JSON. The block holds floats, so it is digested as configuration, not as an HW-JCS document.
- **Anchors** (cut-able, §11): every 5 minutes a Merkle root over the households' ledger heads is signed and
  written to S3 Object Lock in compliance mode.
- **Judge verification** runs offline: `tools/verify_attestation.py` checks the signature with the public key,
  recomputes the decision digest and replays the ledger chain to the recorded head.

---

## 10. Gates

`python tools/pipeline.py --gate Gn` runs F, G0 … Gn in order and stops at the first gate that does not pass.
A check reports PASS, FAIL, PENDING (what it inspects does not exist yet), MANUAL (an attestation is missing)
or WARN (advisory). A gate passes only when every check is PASS or WARN.

| Gate | Window | Scope | Automated checks | Attestations | Pass rule |
|---|---|---|---|---|---|
| F | before H0 | Foundation: blueprint, pipeline, orrery, `.gitattributes` | spec blocks parse; tool contract lint (MCP-01 to MCP-12, HW-01 to HW-05); HX table; SPRT consistency; temporal block; envelope schema; orrery self-test and envelope validation; gate table; secret scan | none | all PASS |
| G0 | H0–H2 | Environment and pins | `pyproject.toml` has `requires-python >=3.12,<3.13` and `mcp==2.3.0`; `.python-version` is 3.12; `uv.lock` records mcp 2.3.0; `.env.example` names every gate variable | `G0.alexa_console`, `G0.bedrock_access`, `G0.cognito_tier`, `G0.track_posture` | all PASS |
| G1 | H2–H8 | Contracts and ingest | contracts and ingest modules present; fixtures byte-identical to `HEAD` with `text` unset; `pytest -m gate_g1` | none | all PASS |
| G2 | H8–H16 | MCP server and façade | server and façade modules present; gate variables set and well-formed; served tool schemas identical to `hearthward:tools`; `pytest -m gate_g2` (version matrix, 401 without `WWW-Authenticate`) | `G2.alexa_discovery_summary` | all PASS |
| G3 | H16–H24 | Deployed and linked | CDK app present; `--probe`: protected-resource metadata, façade metadata advertises S256, `/mcp` returns 401 with no `WWW-Authenticate` and no redirect | `G3.simulator_linking` | all PASS |
| G4 | H24–H32 | Causal engine and decision core | GNOMON submodule pinned; causal and decision modules present; `lambda_trace_mnats` equals the orrery; `pytest -m gate_g4` | none | all PASS |
| G5 | H32–H40 | Broker and ladder | broker modules present; `RULE_IDS` equals the HX block; ladder ASL has a `.waitForTaskToken` task with timeouts; `pytest -m gate_g5` | `G5.cloudtrail_proof_hash` | all PASS |
| G6 | H40–H46 | Integrity and web | attestation modules and `tools/verify_attestation.py` present and passing; `web/package-lock.json` committed; `pytest -m gate_g6` | none | all PASS |
| G7 | H46–H48 | Submission | README sections including the synthetic-data disclosure; OSI license; product feedback and friction log | `G7.demo_video`, `G7.devpost_submitted` | all PASS |

Attestations live in `docs/gates/attestations.json`, one entry per key:

```
{"G0.alexa_console": {"by": "Surya Prasanth R", "at": "2026-10-21", "evidence": "Created add-on hw-dev; simulator opens in isolation mode"}}
```

`G7.demo_video` also needs `"duration_s"` under 180 and a public YouTube or Vimeo URL as its evidence.

Pipeline exit codes: `0` every requested gate passed, `1` a check failed, `2` usage error, `3` nothing
failed but checks are pending or await attestation.

---

## 11. Cut-line policy

Cuts are ordered, triggered by time, and recorded in `docs/gates/cuts.md` (append-only: hour, cut, reason).

**Already cut:** Managed Flink (Lambda and SQS FIFO instead), the Fire TV app, Lambda MicroVM vision,
Amazon Connect voice (L3), real Ring and Bee devices.

| Trigger | Cut |
|---|---|
| G2 not green at H16 | **C1** Drop Tier-1 service-token minting if public discovery works. Keep the façade metadata. |
| G3 not green at H24 | **C2** Stop all work outside G3. Replace the 3D twin with the 2D Threat Braid. |
| G4 not green at H32 | **C3** Run the reference bitemporal store behind the `CausalStore` port instead of GNOMON. GNOMON moves past the deadline. |
| G5 not green at H40 | **C4** Drop the Merkle anchor (keep per-decision signatures) and the Echo Show MCP Apps card. |
| G6 not green at H44 | **C5** Reduce the web app to the approval page and the verify page. |

**Never cut:** F, G1, G2, G3, G5, G7. G7 starts no later than two hours before the deadline, whatever
state the other gates are in.

---

## 12. Disclosure rules

- **D-1** Every DOOR and WRIST event in the demo is synthetic, and the video says so on screen.
- **D-2** Every tool output carries `data_origin`.
- **D-3** The README states what was built during the submission window, and which earlier repositories
  (GNOMON, KINTSUGI, TENURE, AXIOM, CASCADE) contributed which patterns.
- **D-4** The README includes an AI-assistance disclosure.
- **D-5** No performance number appears anywhere unless it was measured, with its method.

---

## 13. Unverified facts register

| # | Assumption | Settled at |
|---|---|---|
| U-1 | Alexa+ negotiates 2025-03-26 (documented example) and tolerates `structuredContent` (its own example uses it) | G3 |
| U-2 | Tier 1 is optional for a public MCP server | G2 |
| U-3 | Cognito accepts or ignores a repeated `resource` at `/oauth2/token` | G2 |
| U-4 | Alexa+ add-on development and the simulator work for this developer account's region | G0 |
| U-5 | Bedrock model access on this AWS account (it was unavailable on the AXIOM Free-plan account) | G0 |
| U-6 | The API Gateway Host header reaching the ALB matches `allowed_hosts` | G3 |

---

## 14. Sources

- Alexa+ MCP: [Authentication](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-authentication),
  [Account linking](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-account-linking),
  [Client lifecycle](https://developer.amazon.com/docs/alexaplus/add-ons/category-sdk-mcp-client-lifecycle.html),
  [Web simulator](https://developer.amazon.com/docs/alexaplus/add-ons/test-with-web-simulator)
- Ring: [Partner API documentation](https://developer.amazon.com/docs/ring/api-documentation.html) (webhook
  signature, v1.1 payload, retry schedule, dead-letter queue)
- Bee: [Realtime stream](https://docs.bee.computer/docs/realtime)
- Cognito: [Resource binding](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-define-resource-servers.html),
  [PKCE](https://docs.aws.amazon.com/cognito/latest/developerguide/using-pkce-in-authorization-code.html)
- Missing S256 in Cognito discovery: [MCP TypeScript SDK PR #992](https://github.com/modelcontextprotocol/typescript-sdk/pull/992)
- Python MCP SDK 2.3.0 (`mcp_types.version`, `mcp.server.transport_security`), inspected from the published wheel
- Hackathon: [Official rules](https://amazonappdev2026.devpost.com/rules)
