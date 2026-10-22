// node --test web/wardline/tests. chain.js against the shipped synthetic attestation and ledgers.
import assert from "node:assert/strict";
import { test } from "node:test";

import { WARD } from "../data.js";
import {
  ZERO_HEAD, b64ToBytes, bytesToHex, checkDecision, derToRaw, jcs, jcsText, parseRecord, precompute, rechain,
  replayChain, tamper, taggedDigest, verifySignature,
} from "../chain.js";

const attestation = WARD.signed.attestation;
const doorstep = WARD.scenarios.doorstep_scam.envelopes;
const bytesToB64 = (bytes) => btoa(String.fromCharCode(...bytes));

// Test-only mirror of encode_signature in tools/verify_attestation.py.
function rawToDer(raw) {
  const integer = (part) => {
    let v = part.subarray(part.findIndex((b) => b !== 0) === -1 ? 31 : part.findIndex((b) => b !== 0));
    if (v[0] & 0x80) v = Uint8Array.of(0, ...v);
    return Uint8Array.of(0x02, v.length, ...v);
  };
  const body = Uint8Array.of(...integer(raw.subarray(0, 32)), ...integer(raw.subarray(32)));
  return Uint8Array.of(0x30, body.length, ...body);
}

const der = (r, s) => Uint8Array.of(0x30, r.length + s.length, ...r, ...s);
const ONE = Uint8Array.of(0x02, 0x01, 0x01);

test("derToRaw turns the shipped KMS-shaped signature into 64 raw bytes and back", () => {
  const sig = b64ToBytes(attestation.decision.sig_b64);
  const raw = derToRaw(sig);
  assert.equal(raw.length, 64);
  assert.deepEqual(rawToDer(raw), sig);
});

test("derToRaw rejects malformed signatures", () => {
  const good = b64ToBytes(attestation.decision.sig_b64);
  const cases = {
    "trailing byte": Uint8Array.of(...good, 0),
    "wrong outer tag": Uint8Array.of(0x31, ...good.subarray(1)),
    "long-form length": Uint8Array.of(0x30, 0x81, 6, ...ONE, ...ONE),
    "non-minimal INTEGER": der(Uint8Array.of(0x02, 0x02, 0x00, 0x01), ONE),
    "negative INTEGER": der(Uint8Array.of(0x02, 0x01, 0x81), ONE),
    "33-byte value": der(Uint8Array.of(0x02, 0x21, 0x01, ...new Uint8Array(32)), ONE),
    "zero INTEGER": der(Uint8Array.of(0x02, 0x01, 0x00), ONE),
    "truncated": good.subarray(0, 20),
  };
  for (const [name, bad] of Object.entries(cases)) assert.throws(() => derToRaw(bad), /DER signature/, name);
});

test("the signature covers the raw bytes (C-6, C-7), not the tagged bytes and not a flipped copy", async () => {
  const signed = attestation.decision;
  const good = await verifySignature(attestation.public_key_b64, signed);
  assert.equal(good.ok, true);
  const bytes = good.bytes;
  const tagged = Uint8Array.of(...new TextEncoder().encode("hw-decision-v1"), 0, ...bytes);
  const asTagged = await verifySignature(attestation.public_key_b64, { ...signed, bytes_b64: bytesToB64(tagged) });
  assert.equal(asTagged.ok, false);
  for (let i = 0; i < 16; i += 1) {
    const bit = Math.floor((i * bytes.length * 8) / 16) + 3;
    const flipped = Uint8Array.from(bytes);
    flipped[bit >> 3] ^= 1 << (bit & 7);
    const res = await verifySignature(attestation.public_key_b64, { ...signed, bytes_b64: bytesToB64(flipped) });
    assert.equal(res.ok, false, `bit ${bit}`);
  }
});

test("every scenario's ledger replays: each head re-derives and each link closes", async () => {
  for (const [name, scenario] of Object.entries(WARD.scenarios)) {
    const replay = await replayChain(scenario.envelopes);
    assert.equal(replay.length, scenario.envelopes.length, name);
    assert.ok(replay.every((r) => r.contentOk && r.linkOk), name);
    assert.equal(replay[0].computedHead.length, ZERO_HEAD.length);
  }
});

test("the decision digest, the strict record and all five checks hold on the intact ledger", async () => {
  const bytes = b64ToBytes(attestation.decision.bytes_b64);
  assert.equal(await taggedDigest("hw-decision-v1", bytes), attestation.decision_digest);
  const record = parseRecord(attestation);
  assert.equal(Object.keys(record).length, 11);
  assert.deepEqual(jcs(record), bytes);
  const checks = await checkDecision(attestation, await replayChain(doorstep));
  assert.deepEqual(checks.map((c) => c.ok), [true, true, true, true, true]);
  assert.match(checks[0].detail, /ECDSA P-256 over \d+ raw bytes · key_id synthetic-demo-key \(not KMS\)/);
});

test("the signed head names the replayed head at at_seq", async () => {
  const replay = await replayChain(doorstep);
  const record = parseRecord(attestation);
  assert.equal(replay[record.at_seq - 1].computedHead, record.ledger_head);
});

test("tamper breaks exactly head k and the link into k + 1, and check 4 says so", async () => {
  const k = WARD.signed.intervene_at_seq - 3;
  const replay = await replayChain(tamper(doorstep, k));
  assert.deepEqual(replay.filter((r) => !r.contentOk).map((r) => r.seq), [k]);
  assert.deepEqual(replay.filter((r) => !r.linkOk).map((r) => r.seq), [k + 1]);
  const checks = await checkDecision(attestation, replay);
  assert.deepEqual(checks.map((c) => c.ok), [true, true, true, false, null]);
  assert.equal(checks[3].detail, `seq ${k}: head does not match its contents`);
  assert.match(checks[4].detail, /not evaluated \(chain broken\)/);
  assert.equal(doorstep.find((e) => e.seq === k).received_at + 1, tamper(doorstep, k)[k - 1].received_at);
});

test("re-chaining closes every link but the signed head still catches it (check 5 only)", async () => {
  const k = WARD.signed.intervene_at_seq - 3;
  const forged = await rechain(doorstep, k);
  const replay = await replayChain(forged);
  const original = await replayChain(doorstep);
  assert.ok(replay.every((r) => r.contentOk && r.linkOk));
  for (let n = 1; n <= doorstep.length; n += 1) {
    assert.equal(replay[n - 1].computedHead === original[n - 1].computedHead, n < k, `seq ${n}`);
  }
  const checks = await checkDecision(attestation, replay);
  assert.deepEqual(checks.map((c) => c.ok), [true, true, true, true, false]);
  assert.equal(checks[4].detail, `signed ledger_head ≠ replayed head at seq ${WARD.signed.intervene_at_seq}`);
  assert.equal(doorstep[k].prev, original[k - 1].computedHead, "the input ledger is not mutated");
});

test("precompute returns a frozen intact, tampered and re-chained view", async () => {
  const pre = await precompute(WARD);
  assert.equal(pre.k, 5);
  assert.ok(Object.isFrozen(pre) && Object.isFrozen(pre.tampered.checks));
  assert.deepEqual(pre.intact.checks.map((c) => c.ok), [true, true, true, true, true]);
  assert.deepEqual(pre.tampered.checks.map((c) => c.ok), [true, true, true, false, null]);
  assert.deepEqual(pre.rechained.checks.map((c) => c.ok), [true, true, true, true, false]);
});

test("HW-JCS rejects what the profile forbids", () => {
  assert.throws(() => jcsText({ a: 1.5 }), /safe integer/);
  assert.throws(() => jcsText(2 ** 53), /safe integer/);
  assert.throws(() => jcsText(NaN), /safe integer/);
  assert.throws(() => jcsText({ "Bad-Key": 1 }), /outside the profile/);
  assert.equal(jcsText({ b: [1, null, true], a: "x" }), '{"a":"x","b":[1,null,true]}');
  assert.equal(bytesToHex(Uint8Array.of(0, 255)), "00ff");
});
