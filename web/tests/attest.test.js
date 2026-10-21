// node --test web/tests. Self-contained: keys are made with WebCrypto, signatures are turned into KMS-shaped DER.
import assert from "node:assert/strict";
import { test } from "node:test";

import { actionHash, merkleRoot, leafHash, taggedDigest, verifyBundle, verifyEs256 } from "../src/attest.js";
import { b64ToBytes, b64urlToBytes, bytesToB64, bytesToB64url, bytesToHex, derToRaw, hexToBytes, rawToDer } from "../src/encoding.js";
import { jcs, parseStrict } from "../src/jcs.js";
import { assertionRequest, checkDisplayed, requestApproval, serializeAssertion, summarize } from "../src/approval.js";
import { escapeHtml, renderAnchor, renderChecks, renderDecision, renderResult } from "../src/render.js";

const subtle = globalThis.crypto.subtle;
const ECDSA = { name: "ECDSA", namedCurve: "P-256" };
const text = (s) => new TextEncoder().encode(s);

async function makeKey() {
  const pair = await subtle.generateKey(ECDSA, true, ["sign", "verify"]);
  const spki = bytesToB64(new Uint8Array(await subtle.exportKey("spki", pair.publicKey)));
  const sign = async (bytes) => bytesToB64(rawToDer(new Uint8Array(await subtle.sign({ name: "ECDSA", hash: "SHA-256" }, pair.privateKey, bytes))));
  return { spki, sign };
}

async function signed(key, bytes) {
  return { bytes_b64: bytesToB64(bytes), sig_b64: await key.sign(bytes), key_id: "test" };
}

async function world() {
  const key = await makeKey();
  const ledger = [];
  let prev = `sha256:${"0".repeat(64)}`;
  for (let n = 1; n <= 4; n += 1) {
    const env = { v: 1, household: "hh_webtest01", source: "system", seq: n, received_at: 1000 * n, prev, auth: { synthetic: true } };
    env.head = await taggedDigest("hw-ledger-v1", jcs(env));
    ledger.push(env);
    prev = env.head;
  }
  const record = { episode: "ep_0123456789ABCDEFGHJKMNPQRS", decision: "INTERVENE", at_seq: 3, ledger_head: ledger[2].head,
    belief_seq: 3, lambda_mnats: 5000, reasons: ["SECRECY_REQUEST"], thresholds_mnats: { a: 4554, b: -2986 },
    params_digest: `sha256:${"1".repeat(64)}`, extractor_model_ids: [], cross_terms: [] };
  const bytes = jcs(record);
  const heads = [{ household: "hh_aaaaaaaa", seq: 1, head: `sha256:${"2".repeat(64)}` }, { household: "hh_webtest01", seq: 4, head: ledger[3].head }, { household: "hh_zzzzzzzz", seq: 2, head: `sha256:${"3".repeat(64)}` }];
  const leaves = await Promise.all(heads.map(leafHash));
  const document = { v: 1, at_ms: 1800000000000, count: 3, root: `sha256:${bytesToHex(await merkleRoot(leaves))}`, leaves: heads };
  const left = await merkleRoot(leaves.slice(0, 2));
  void left;
  // For 3 leaves and index 1 the audit path is [leaf 0, leaf 2]: the tree splits 2 | 1.
  const path = [bytesToHex(leaves[0]), bytesToHex(leaves[2])];
  return { key, bundle: { v: 1, public_key_b64: key.spki, decision: await signed(key, bytes), decision_digest: await taggedDigest("hw-decision-v1", bytes), ledger,
    anchor: { attestation: await signed(key, jcs(document)), inclusion: { index: 1, size: 3, path } } }, record };
}

test("encodings round trip and refuse junk", () => {
  const raw = Uint8Array.from([0, 1, 250, 251, 252, 253, 254, 255]);
  assert.deepEqual(b64urlToBytes(bytesToB64url(raw)), raw);
  assert.deepEqual(b64ToBytes(bytesToB64(raw)), raw);
  assert.deepEqual(hexToBytes(bytesToHex(raw)), raw);
  assert.throws(() => hexToBytes("zz"));
  assert.throws(() => hexToBytes("ABCD"));
  assert.throws(() => b64urlToBytes("a+b"));
  assert.throws(() => b64ToBytes("a"));
});

test("DER and raw signatures convert both ways, including leading-zero and high-bit integers", () => {
  for (const r0 of [0x01, 0x7f, 0x80, 0xff]) {
    const raw = new Uint8Array(64).fill(r0);
    raw[0] = 0;
    raw[31] = r0;
    raw[32] = 0;
    assert.deepEqual(derToRaw(rawToDer(raw)), raw);
  }
  assert.throws(() => derToRaw(Uint8Array.from([0x30, 0x02, 0x02, 0x00])));
  assert.throws(() => derToRaw(Uint8Array.from([0x31, 0x00])));
});

test("JCS matches RFC 8785 inside the profile and refuses outside it", () => {
  assert.equal(new TextDecoder().decode(jcs({ b: 1, a: [true, null, "x\né"] })), '{"a":[true,null,"x\\né"],"b":1}');
  for (const bad of [{ a: 1.5 }, { A: 1 }, { a: 2 ** 53 }, { a: NaN }, { a: "\ud800" }, { a: undefined }, { a: () => 1 }]) {
    assert.throws(() => jcs(bad), undefined, JSON.stringify(bad));
  }
  assert.throws(() => parseStrict(text('{"a":1,"a":2}')), /duplicate/);
  assert.throws(() => parseStrict(text('{"a":1.0}')), /integers/);
  assert.deepEqual(parseStrict(text('{"a":{"b":1},"c":[{"b":2},{"b":3}]}')), { a: { b: 1 }, c: [{ b: 2 }, { b: 3 }] });
});

test("a signature verifies over the raw bytes and nothing else", async () => {
  const key = await makeKey();
  const bytes = text("some canonical bytes");
  const sig = await key.sign(bytes);
  assert.equal(await verifyEs256(key.spki, bytes, sig), true);
  assert.equal(await verifyEs256(key.spki, text("some canonical byteS"), sig), false);
  assert.equal(await verifyEs256((await makeKey()).spki, bytes, sig), false);
});

test("a good bundle verifies end to end", async () => {
  const { bundle } = await world();
  const result = await verifyBundle(bundle);
  assert.equal(result.ok, true, JSON.stringify(result.checks.filter((c) => !c.ok)));
  assert.deepEqual(result.checks.map((c) => c.name), ["decision signature", "decision record", "decision digest", "ledger replay", "anchor signature", "anchor root", "anchor inclusion"]);
  assert.equal(result.record.decision, "INTERVENE");
});

for (const [name, tamper, failing] of [
  ["a changed record", (b) => { const raw = b64ToBytes(b.decision.bytes_b64); raw[12] ^= 1; b.decision.bytes_b64 = bytesToB64(raw); }, "decision signature"],
  ["another key", async (b) => { b.public_key_b64 = (await makeKey()).spki; }, "decision signature"],
  ["a wrong claimed digest", (b) => { b.decision_digest = `sha256:${"0".repeat(64)}`; }, "decision digest"],
  ["an edited envelope", (b) => { b.ledger[1].received_at = 1; }, "ledger replay"],
  ["a dropped envelope", (b) => { b.ledger.splice(1, 1); }, "ledger replay"],
  ["a ledger that stops early", (b) => { b.ledger.length = 2; }, "ledger replay"],
  ["a float in an envelope", (b) => { b.ledger[0].received_at = 1.5; }, "ledger replay"],
  ["a wrong audit path", (b) => { b.anchor.inclusion.path[0] = "00".repeat(32); }, "anchor inclusion"],
  ["a wrong leaf index", (b) => { b.anchor.inclusion.index = 0; }, "anchor inclusion"],
]) {
  test(`tampering is caught: ${name}`, async () => {
    const { bundle } = await world();
    await tamper(bundle);
    const result = await verifyBundle(bundle);
    assert.equal(result.ok, false);
    assert.ok(result.checks.some((c) => !c.ok && c.name === failing), JSON.stringify(result.checks));
  });
}

test("an anchor that does not list the household fails inclusion", async () => {
  const { bundle } = await world();
  for (const env of bundle.ledger) env.household = "hh_unlisted01";
  const result = await verifyBundle(bundle);
  assert.ok(result.checks.some((c) => c.name === "anchor inclusion" && !c.ok));
});

// ── the approval page ────────────────────────────────────────────────────────────────────────────────

const DOC = { tool: "pause_monitoring", args: { hours: 4, reason: "travel" }, household: "hh_synthetic", episode_version: 3, nonce: "ab".repeat(16), exp: 1800000300000 };

test("the action hash is SHA-256 of the tag, a zero byte and the JCS document", async () => {
  const expected = new Uint8Array(await subtle.digest("SHA-256", Uint8Array.from([...text("hw-action-v1"), 0, ...jcs(DOC)])));
  assert.deepEqual(await actionHash(DOC), expected);
  assert.notDeepEqual(await actionHash({ ...DOC, exp: DOC.exp + 1 }), expected);
});

test("the page shows only an action that reproduces the challenge", async () => {
  const challenge = bytesToHex(await actionHash(DOC));
  assert.equal(await checkDisplayed(DOC, challenge), "Pause monitoring for 4 hours (travel)");
  await assert.rejects(checkDisplayed({ ...DOC, args: { hours: 24, reason: "travel" } }, challenge), /does not match/);
  await assert.rejects(checkDisplayed({ ...DOC, household: "hh_other" }, challenge), /does not match/);
  const { nonce, ...incomplete } = DOC;
  void nonce;
  await assert.rejects(checkDisplayed(incomplete, challenge), /incomplete/);
});

test("summaries cover the five actions and refuse an unknown one", () => {
  assert.match(summarize({ tool: "dismiss_alert", args: { episode_id: "ep_x", reason: "false_alarm" } }), /false alarm/);
  assert.match(summarize({ tool: "add_trusted_vendor", args: { name: "Acme", category: "repair" } }), /Acme/);
  assert.match(summarize({ tool: "edit_trusted_circle", args: { operation: "remove", display_name: "Sam" } }), /Remove Sam from/);
  assert.match(summarize({ tool: "share_evidence", args: { episode_id: "ep_x", recipient: "bank_fraud_team" } }), /bank fraud team/);
  assert.equal(summarize({ tool: "pause_monitoring", args: { hours: 1, reason: "other" } }), "Pause monitoring for 1 hour (other)");
  assert.throws(() => summarize({ tool: "format_disk", args: {} }), /unknown action/);
});

test("the assertion request asks for user verification with the action hash as the challenge", async () => {
  const challengeHex = bytesToHex(await actionHash(DOC));
  const request = assertionRequest({ challengeHex, rpId: "approve.hearthward.example", credentialIds: ["AQID"] });
  assert.deepEqual(request.challenge, hexToBytes(challengeHex));
  assert.equal(request.userVerification, "required");
  assert.equal(request.rpId, "approve.hearthward.example");
  assert.deepEqual(request.allowCredentials, [{ type: "public-key", id: Uint8Array.from([1, 2, 3]) }]);
  assert.throws(() => assertionRequest({ challengeHex, rpId: "x", credentialIds: [] }), /no passkeys/);
});

test("requestApproval calls navigator.credentials.get with that request and serializes the answer", async () => {
  const challengeHex = bytesToHex(await actionHash(DOC));
  let seen;
  const credentials = {
    async get(options) {
      seen = options;
      return { rawId: Uint8Array.from([1, 2, 3]).buffer, response: { clientDataJSON: text("cd").buffer, authenticatorData: text("ad").buffer, signature: Uint8Array.from([255, 254]).buffer } };
    },
  };
  const out = await requestApproval({ doc: DOC, challengeHex, rpId: "approve.hearthward.example", credentialIds: ["AQID"], credentials });
  assert.deepEqual(seen.publicKey.challenge, hexToBytes(challengeHex));
  assert.deepEqual(out, { credential_id: "AQID", client_data_json: bytesToB64url(text("cd")), authenticator_data: bytesToB64url(text("ad")), signature: "__4" });
  assert.deepEqual(Object.keys(serializeAssertion({ rawId: new ArrayBuffer(0), response: { clientDataJSON: new ArrayBuffer(0), authenticatorData: new ArrayBuffer(0), signature: new ArrayBuffer(0) } })), ["credential_id", "client_data_json", "authenticator_data", "signature"]);
});

test("nothing is asked of the passkey when the displayed action does not match", async () => {
  let asked = false;
  const credentials = { async get() { asked = true; } };
  await assert.rejects(requestApproval({ doc: { ...DOC, args: { hours: 2, reason: "travel" } }, challengeHex: bytesToHex(await actionHash(DOC)), rpId: "x.example", credentialIds: ["AQID"], credentials }));
  assert.equal(asked, false);
});

// ── rendering ──────────────────────────────────────────────────────────────────────────────────────────

test("every dynamic value is escaped", () => {
  assert.equal(escapeHtml(`<img src=x onerror="a()">&'`), "&lt;img src=x onerror=&quot;a()&quot;&gt;&amp;&#39;");
  const html = renderChecks([{ ok: false, name: "<b>x</b>", detail: '"><script>' }]);
  assert.ok(!html.includes("<script>") && !html.includes("<b>x"));
  const record = { episode: "<e>", decision: "D", lambda_mnats: 1, at_seq: 1, ledger_head: "h", reasons: ["<r>"], cross_terms: { "<t>": 1 }, params_digest: "p" };
  assert.ok(!renderDecision(record).includes("<e>") && !renderDecision(record).includes("<r>") && !renderDecision(record).includes("<t>"));
  assert.ok(!renderAnchor({ root: "<r>", count: 1, at_ms: 0, leaves: [{ household: "<h>", seq: 1, head: "<x>" }] }).includes("<h>"));
  assert.equal(renderAnchor(null), "");
  assert.match(renderDecision(null), /nothing here verified/);
});

test("renderResult shows the record only when everything verified", () => {
  const record = { episode: "ep_x", decision: "D", lambda_mnats: 1, at_seq: 1, ledger_head: "h", reasons: [], cross_terms: [], params_digest: "p" };
  const good = renderResult({ ok: true, checks: [{ ok: true, name: "n", detail: "d" }], record, anchor: null });
  assert.ok(good.includes("Verified") && good.includes("ep_x"));
  const bad = renderResult({ ok: false, checks: [{ ok: false, name: "n", detail: "d" }], record, anchor: null });
  assert.ok(bad.includes("Not verified") && !bad.includes("ep_x"));
});
