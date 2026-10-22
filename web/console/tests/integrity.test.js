// Claim 4: the chain replay, the five checks, tamper and re-chain, edits, and the signature parity with WebCrypto.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CONSOLE } from "../data/console_data.js";
import { applySandbox, attestationOf, bundleJson, cacheSize, coerceEdit, computeIntegrity, integrityView } from "../claims/integrity.js";
import { deriveProvenance } from "../provenance.js";
import { initialState } from "../store.js";

const provenance = deriveProvenance(CONSOLE, "http://127.0.0.1");
const sandbox = (over = {}) => ({ edits: [], tamper: 0, rechain: 0, ...over });
const run = (over, data = CONSOLE) => computeIntegrity(data, sandbox(over), deriveProvenance(data, ""));
const flags = (r) => r.checks.map((c) => c.ok);

test("the recorded KMS bundle is the signed material, and WebCrypto verifies it", async () => {
  const att = attestationOf(CONSOLE);
  assert.equal(att.source, "kms");
  assert.match(att.meta.key_arn, /^arn:aws:kms:[a-z0-9-]+:\d{12}:key\//);
  const r = await run({});
  assert.deepEqual(flags(r), [true, true, true, true, true]);
  assert.match(r.checks[0].detail, /ECDSA P-256 over 521 raw bytes · key arn:aws:kms:/);
  assert.equal(r.record.at_seq, 8);
  assert.equal(r.replay.length, 12);
  assert.ok(r.replay.every((x) => x.contentOk && x.linkOk));
  assert.ok(r.teeth.slice(0, 11).every((t) => t.every((open) => !open)), "every tooth is closed");
});

test("without the KMS bundle the demo key is used and labelled as not KMS", async () => {
  const data = { ...CONSOLE, recorded: { bundle: null, probes: null } };
  const r = await run({}, data);
  assert.equal(r.source, "synthetic");
  assert.deepEqual(flags(r), [true, true, true, true, true]);
  assert.match(r.checks[0].detail, /key synthetic-demo-key \(not KMS\)$/);
});

test("tamper (received_at += 1 ms at seq 5): the content breaks there, the link at 6, the head is not evaluated", async () => {
  const r = await run({ tamper: 1 });
  assert.deepEqual(r.touched, [5]);
  assert.deepEqual(flags(r), [true, true, true, false, null]);
  assert.match(r.checks[3].detail, /seq 5: head does not match its contents/);
  assert.match(r.checks[4].detail, /not evaluated \(chain broken\)/);
  assert.equal(r.replay[4].contentOk, false);
  assert.equal(r.replay[5].linkOk, false);
  assert.ok(r.teeth[4].some(Boolean), "the zipper opens after the tampered tile");
  assert.ok(r.replay.filter((x) => !x.contentOk).length === 1);
});

test("re-chain closes every link, checks 1-4 pass, and check 5 fails: the lesson of §4.4", async () => {
  const r = await run({ tamper: 1, rechain: 1 });
  assert.deepEqual(flags(r), [true, true, true, true, false]);
  assert.ok(r.replay.every((x) => x.contentOk && x.linkOk));
  assert.match(r.checks[4].detail, /signed ledger_head ≠ replayed head at seq 8/);
  assert.notEqual(r.replay[7].computedHead, r.record.ledger_head);
  assert.equal(r.replay[3].computedHead, r.originalReplay[3].computedHead, "heads before the tamper are unchanged");
});

test("a field edit re-hashes from that envelope on; integers stay integers; bad edits are rejected, not applied", async () => {
  const edit = (seq, path, value) => ({ edits: [{ seq, path, value }] });
  const r = await run(edit(9, "valid.lo", "123"));
  assert.deepEqual(r.touched, [9]);
  assert.equal(r.ledger[8].valid.lo, 123);
  assert.equal(r.replay.filter((x) => !x.contentOk).length, 1);
  assert.equal(flags(r)[4], null);
  const head = await run(edit(11, "received_at", "5"));
  assert.equal(head.checks[4].ok, null, "chain broken -> head not evaluated");
  const bad = await run(edit(3, "received_at", "1.5"));
  assert.equal(bad.rejected.length, 1);
  assert.deepEqual(flags(bad), [true, true, true, true, true], "a rejected edit changes nothing");
  assert.throws(() => coerceEdit(5, "abc"));
  assert.throws(() => coerceEdit(5, 2 ** 60));
  assert.throws(() => coerceEdit("x", "y".repeat(300)));
  assert.throws(() => coerceEdit({}, 1));
  const unknown = await run(edit(99, "kind", "x"));
  assert.match(unknown.rejected[0].reason, /no envelope 99/);
});

test("re-chaining an edit after the decision keeps the signed head valid; before it, the head fails", async () => {
  const after = await run({ edits: [{ seq: 10, path: "received_at", value: 7 }], rechain: 1, tamper: 0 });
  assert.equal(after.checks[4].ok, true, "an envelope after at_seq cannot move the head at seq 8 (BT-4)");
  const before = await applySandbox(attestationOf(CONSOLE).ledger, sandbox({ edits: [{ seq: 2, path: "received_at", value: 7 }], rechain: 1 }), 8);
  assert.equal(before.firstTouched, 2);
});

test("the crypto cache returns the same promise for the same bytes", async () => {
  await run({});
  const before = cacheSize();
  await run({});
  await run({});
  assert.equal(cacheSize(), before);
});

test("bundleJson carries the current ledger and the exact signed bytes", async () => {
  const r = await run({ tamper: 1 });
  const bundle = JSON.parse(bundleJson(r, CONSOLE));
  assert.equal(bundle.decision.bytes_b64, attestationOf(CONSOLE).attestation.decision.bytes_b64);
  assert.equal(bundle.ledger[4].received_at, attestationOf(CONSOLE).ledger[4].received_at + 1);
  assert.ok(bundle.sprt_params && bundle.v === 1);
});

test("the view shows five checks with badges, a tamper control and the key as recorded", async () => {
  const state = { ...initialState({ N: 12, provenance }), claim: "integrity" };
  const pending = integrityView({ state, data: CONSOLE, derived: {} });
  assert.match(pending.hero, /verifying/);
  const view = integrityView({ state, data: CONSOLE, derived: { integrity: await run({}) } });
  assert.match(view.title, /signed by AWS KMS/);
  assert.equal((view.proof.match(/class="check ok"/g) ?? []).length, 5);
  assert.match(view.proof, /RECORDED/);
  assert.match(view.hero, /<table class="ledger">/);
  assert.equal((view.hero.match(/data-action="select-envelope" data-seq=/g) ?? []).length, 24);
  const broken = integrityView({ state, data: CONSOLE, derived: { integrity: await run({ tamper: 1, rechain: 1 }) } });
  assert.equal((broken.proof.match(/class="check fail"/g) ?? []).length, 1);
  const demo = { ...CONSOLE, recorded: { bundle: null, probes: null } };
  const dstate = { ...state, provenance: deriveProvenance(demo, "") };
  const dview = integrityView({ state: dstate, data: demo, derived: { integrity: await run({}, demo) } });
  assert.match(dview.title, /demo key, not KMS/);
});
