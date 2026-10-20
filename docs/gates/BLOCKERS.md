# Blockers and spec questions

Nothing here blocks gate G1, which passes. These items came out of the G1 security review (see
`G1_COMPLETION_LOG.md`). Each needs a decision about the blueprint or the deployment, so they are recorded rather
than fixed in code. Rule 6 forbids editing the `hearthward:*` blocks or check semantics to work around them.

## SQ-1. Bee `sync_bounds` are not authenticated (review M3)

- **What:** TB-4 says the bridge HMAC covers "the raw line". `ingest_line(..., sync_bounds=...)` takes the valid
  interval of a sync-recovered line as an unauthenticated parameter, yet the envelope records `auth.verified: true`.
- **Evidence:** blueprint §3 TB-4 and §4.1 (Bee row: "recovered by sync: the conversation's bounds");
  `src/hearthward/ingest/bee_bridge.py`, `ingest_line`. Someone holding a signed line before its first ingestion
  could ledger it with arbitrary bounds. A very wide `hi` keeps an episode from ever being dismissed (§4.4
  watermarks) and makes `door_unknown_overlap` fire spuriously.
- **Mitigated in code:** bounds must satisfy `0 <= lo <= hi <= received_at`, and anything else is quarantined. The
  quarantine claims the event id, so the attacker can at most suppress that one line, not distort belief.
- **Needs a decision:** either the bridge MACs `line || 0x00 || lo || hi` for sync deliveries (or signs a sync
  manifest), or ingest derives the bounds from the conversation's ledgered lines. Either one changes TB-4.

## SQ-2. Ring deliveries are not bound to a household (review L2)

- **What:** the Ring payload has no household. The handler takes `household` from deployment configuration and
  never reads `meta.account_id`, which is covered by the MAC.
- **Evidence:** `src/hearthward/ingest/ring_webhook.py`, `handle(..., household=...)`. If two households share a
  signing key, a captured delivery replays into the other household. DEDUP keys are per household, so it is not
  absorbed as a duplicate.
- **Needs a decision (G3):** use a signing key per household, or map `meta.account_id` to a household and
  quarantine on mismatch.

## SQ-3. The Bee dedup id hashes the raw line (review L9)

- **What:** blueprint §4.1 defines `source_event_id = bee:<conversation_uuid>:<sha256(line)[:16]>`. Two identical
  utterances in one conversation, such as the same phrase said twice, collide, and the second is absorbed as a
  duplicate. In the other direction, a bridge that re-serializes a line during sync gets a new id and ledgers it
  twice.
- **Evidence:** blueprint §4.1 table, Bee row; `tools/orrery.py` `_prepare` (wrist); `bee_bridge.py` follows both
  exactly.
- **Needs a decision:** have the bridge embed a per-utterance sequence number or id in the signed line, and key
  dedup on that.

## SQ-4. Quarantine records share the EventLedger table (review L10)

- **What:** blueprint §4.4 says a quarantined event is "kept in a quarantine table". The G1 brief specified a
  single table with `QUAR#` sort keys, and `DEDUP#` and `HEAD` items live there too.
- **Evidence:** `src/hearthward/ingest/ledger.py` module docstring; blueprint §2 topology (DynamoDB Streams →
  EventBridge Pipe → SQS FIFO → fusion).
- **Needs a decision (G3):** the Pipe must filter to `sk` beginning with `SEQ#` (INSERT events only). Otherwise
  quarantine, dedup and head writes reach the ordered consumer and break BT-5. The alternative is to move
  quarantine to its own table.

## SQ-5. Unconfirmed assumption: Ring `request_id` is stable across retries

- **What:** idempotency (E-6) and the quarantine claim both assume Ring resends the same `meta.request_id` on every
  retry and on dead-letter-queue redelivery. The orrery assumes this too.
- **Evidence:** blueprint §4.1 maps `source_event_id` to `meta.request_id`. The assumption is not in the §13
  unverified-facts register.
- **Needs a decision:** add it to §13 so it gets settled when real Ring access exists. That is out of scope, since
  DOOR is synthetic only.
