// Checking a signed record in the browser (blueprint rule C-6, TB-8). The page only *displays* what verifies; it
// never makes an authorization decision. The signature is checked over the raw bytes before they are parsed.

import { b64ToBytes, bytesToHex, derToRaw } from "./encoding.js";
import { jcs, parseStrict } from "./jcs.js";

const subtle = globalThis.crypto.subtle;
const encoder = new TextEncoder();
const ZERO_DIGEST = `sha256:${"0".repeat(64)}`;
const ECDSA = { name: "ECDSA", namedCurve: "P-256" };

async function sha256(...parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const joined = new Uint8Array(total);
  let at = 0;
  for (const part of parts) {
    joined.set(part, at);
    at += part.length;
  }
  return new Uint8Array(await subtle.digest("SHA-256", joined));
}

// SHA-256(tag || 0x00 || bytes), the domain-separated digest of rule C-7.
export async function taggedDigest(tag, bytes) {
  return `sha256:${bytesToHex(await sha256(encoder.encode(tag), Uint8Array.of(0), bytes))}`;
}

// The challenge the approval page signs: SHA-256("hw-action-v1" || 0x00 || JCS({tool, args, household,
// episode_version, nonce, exp})) as raw bytes.
export async function actionHash(doc) {
  return sha256(encoder.encode("hw-action-v1"), Uint8Array.of(0), jcs(doc));
}

export async function verifyEs256(spkiB64, bytes, sigDerB64) {
  const key = await subtle.importKey("spki", b64ToBytes(spkiB64), ECDSA, false, ["verify"]);
  return subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, derToRaw(b64ToBytes(sigDerB64)), bytes);
}

async function checkSigned(publicKeyB64, signed, what) {
  try {
    const bytes = b64ToBytes(signed.bytes_b64);
    const ok = await verifyEs256(publicKeyB64, bytes, signed.sig_b64);
    return { check: { name: `${what} signature`, ok, detail: ok ? `ECDSA P-256 over ${bytes.length} raw bytes` : "does not verify" }, bytes: ok ? bytes : null };
  } catch (error) {
    return { check: { name: `${what} signature`, ok: false, detail: `unreadable: ${error.message}` }, bytes: null };
  }
}

export async function replayLedger(ledger) {
  if (!Array.isArray(ledger) || ledger.length === 0) return ["the bundle holds no ledger"];
  const problems = [];
  let prev = ZERO_DIGEST;
  for (const [index, env] of ledger.entries()) {
    const n = index + 1;
    if (env === null || typeof env !== "object" || !["seq", "prev", "head"].every((k) => k in env)) {
      return [`position ${n}: not an envelope with seq, prev and head`];
    }
    if (env.seq !== n) problems.push(`position ${n}: seq is ${env.seq}`);
    if (env.prev !== prev) problems.push(`seq ${n}: prev is not the previous head`);
    try {
      const { head, ...body } = env;
      const computed = await taggedDigest("hw-ledger-v1", jcs(body));
      if (computed !== head) problems.push(`seq ${n}: head does not match its contents`);
    } catch (error) {
      problems.push(`seq ${n}: ${error.message}`);
    }
    prev = env.head;
  }
  return problems;
}

const ANCHOR_TAG = encoder.encode("hw-anchor-v1");
const anchorHash = (kind, ...parts) => sha256(ANCHOR_TAG, Uint8Array.of(0, kind), ...parts);

export function leafHash(leaf) {
  return anchorHash(0, jcs({ household: leaf.household, seq: leaf.seq, head: leaf.head }));
}

const split = (n) => {
  let k = 1;
  while (k * 2 < n) k *= 2;
  return k;
};

export async function merkleRoot(leaves) {
  if (leaves.length === 1) return leaves[0];
  const k = split(leaves.length);
  return anchorHash(1, await merkleRoot(leaves.slice(0, k)), await merkleRoot(leaves.slice(k)));
}

async function rootFromPath(leaf, index, size, path) {
  if (index < 0 || index >= size) return null;
  if (size === 1) return path.length === 0 ? leaf : null;
  if (path.length === 0) return null;
  const k = split(size);
  const rest = path.slice(0, -1);
  const sibling = path[path.length - 1];
  if (index < k) {
    const sub = await rootFromPath(leaf, index, k, rest);
    return sub && anchorHash(1, sub, sibling);
  }
  const sub = await rootFromPath(leaf, index - k, size - k, rest);
  return sub && anchorHash(1, sibling, sub);
}

export async function verifyAnchor(publicKeyB64, anchor, household) {
  const { check, bytes } = await checkSigned(publicKeyB64, anchor.attestation, "anchor");
  const checks = [check];
  if (!bytes) return { checks, document: null };
  try {
    const doc = parseStrict(bytes);
    const root = `sha256:${bytesToHex(await merkleRoot(await Promise.all(doc.leaves.map(leafHash))))}`;
    const rootOk = root === doc.root && doc.count === doc.leaves.length;
    checks.push({ name: "anchor root", ok: rootOk, detail: rootOk ? `${doc.count} households under ${doc.root.slice(0, 19)}...` : "the leaves do not hash to the signed root" });
    const mine = doc.leaves.find((leaf) => leaf.household === household);
    if (!mine) {
      checks.push({ name: "anchor inclusion", ok: false, detail: `${household} is not among the anchored households` });
    } else {
      const { index, size, path } = anchor.inclusion;
      const got = await rootFromPath(await leafHash(mine), index, size, path.map((p) => Uint8Array.from(p.match(/../g) ?? [], (h) => parseInt(h, 16))));
      const ok = got !== null && `sha256:${bytesToHex(got)}` === doc.root && size === doc.leaves.length && doc.leaves[index] === mine;
      checks.push({ name: "anchor inclusion", ok, detail: ok ? `${household} is leaf ${index} of ${size}` : "the audit path does not lead to the signed root" });
    }
    return { checks, document: doc };
  } catch (error) {
    checks.push({ name: "anchor document", ok: false, detail: `unreadable: ${error.message}` });
    return { checks, document: null };
  }
}

// The same checks as tools/verify_attestation.py, in the browser. The offline tool is the reference.
export async function verifyBundle(bundle) {
  const checks = [];
  const { check, bytes } = await checkSigned(bundle.public_key_b64, bundle.decision ?? {}, "decision");
  checks.push(check);
  if (!bytes) return { ok: false, checks, record: null };
  let record = null;
  try {
    record = parseStrict(bytes);
    checks.push({ name: "decision record", ok: true, detail: `episode ${record.episode}, ${record.decision}` });
  } catch (error) {
    checks.push({ name: "decision record", ok: false, detail: error.message });
    return { ok: false, checks, record: null };
  }
  const digest = await taggedDigest("hw-decision-v1", bytes);
  const claimed = bundle.decision_digest;
  checks.push({ name: "decision digest", ok: claimed === undefined || claimed === digest, detail: digest });
  const problems = await replayLedger(bundle.ledger);
  if (problems.length === 0) {
    const recorded = record.at_seq === 0 ? ZERO_DIGEST : bundle.ledger[record.at_seq - 1]?.head;
    if (recorded !== record.ledger_head) problems.push(`the head at seq ${record.at_seq} is not the recorded ledger_head`);
  }
  checks.push({ name: "ledger replay", ok: problems.length === 0, detail: problems.length ? problems.slice(0, 3).join("; ") : `${bundle.ledger.length} envelopes chain from the genesis head` });
  let anchor = null;
  if (bundle.anchor) {
    const result = await verifyAnchor(bundle.public_key_b64, bundle.anchor, bundle.ledger?.[0]?.household);
    checks.push(...result.checks);
    anchor = result.document;
  }
  return { ok: checks.every((c) => c.ok), checks, record, anchor };
}
