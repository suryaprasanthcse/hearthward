// Wardline's crypto: HW-JCS, the SHA-256 ledger chain, DER -> raw P-256, ECDSA verify, tamper and re-chain.
// Pure and DOM-free (Node >= 20 and browsers). Standalone: it imports nothing, and must agree with
// tools/verify_attestation.py. Verification here is display-only (TB-8); the browser authorizes nothing.

const encoder = new TextEncoder();
export const ZERO_HEAD = `sha256:${"0".repeat(64)}`;
const KEY = /^[a-z0-9_]+$/;

export function b64ToBytes(text) {
  if (typeof text !== "string" || text.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(text)) {
    throw new Error("not base64");
  }
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
}
export const bytesToHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
export const headBytes = (head) => Uint8Array.from(head.slice(7).match(/../g), (h) => parseInt(h, 16));

// HW-JCS (C-2): keys ^[a-z0-9_]+$ sorted by UTF-16 code unit, safe integers only, no floats.
export function jcsText(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error(`HW-JCS: ${value} is not a safe integer`);
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(jcsText).join(",")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    for (const k of keys) if (!KEY.test(k)) throw new Error(`HW-JCS: key ${JSON.stringify(k)} is outside the profile`);
    return `{${keys.map((k) => `${JSON.stringify(k)}:${jcsText(value[k])}`).join(",")}}`;
  }
  throw new Error(`HW-JCS: unsupported ${typeof value}`);
}
export const jcs = (value) => encoder.encode(jcsText(value));

export async function sha256(...parts) {
  const joined = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const part of parts) { joined.set(part, at); at += part.length; }
  return new Uint8Array(await crypto.subtle.digest("SHA-256", joined));
}
export async function taggedDigest(tag, bytes) {
  return `sha256:${bytesToHex(await sha256(encoder.encode(tag), Uint8Array.of(0), bytes))}`;
}
export async function ledgerHead(envelope) {
  const { head, ...body } = envelope;
  return taggedDigest("hw-ledger-v1", jcs(body));
}

// KMS Sign (ECDSA_SHA_256) returns a DER ECDSA-Sig-Value: SEQUENCE { r INTEGER, s INTEGER }.
// WebCrypto verifies the IEEE P1363 form: r and s as 32-byte big-endian integers, concatenated (64 bytes).
// A P-256 DER signature is at most 72 bytes, so every length is in short form; reject anything else.
export function derToRaw(der) {
  const fail = (why) => { throw new Error(`DER signature: ${why}`); };
  const tlv = (buf, at) => {
    if (at + 2 > buf.length) fail("truncated");
    const len = buf[at + 1];
    if (len & 0x80) fail("long-form length");
    if (at + 2 + len > buf.length) fail("truncated");
    return { tag: buf[at], body: buf.subarray(at + 2, at + 2 + len), end: at + 2 + len };
  };
  const int32 = (body) => {
    if (body.length === 0) fail("empty INTEGER");
    if (body[0] & 0x80) fail("negative INTEGER");
    if (body.length > 1 && body[0] === 0x00 && !(body[1] & 0x80)) fail("non-minimal INTEGER");
    const v = body[0] === 0x00 ? body.subarray(1) : body;
    if (v.length === 0) fail("zero INTEGER");
    if (v.length > 32) fail("INTEGER longer than 32 bytes");
    const out = new Uint8Array(32);
    out.set(v, 32 - v.length);
    return out;
  };
  const seq = tlv(der, 0);
  if (seq.tag !== 0x30 || seq.end !== der.length) fail("not one SEQUENCE");
  const r = tlv(seq.body, 0);
  const s = tlv(seq.body, r.end);
  if (r.tag !== 0x02 || s.tag !== 0x02 || s.end !== seq.body.length) fail("not SEQUENCE { INTEGER, INTEGER }");
  const raw = new Uint8Array(64);
  raw.set(int32(r.body), 0);
  raw.set(int32(s.body), 32);
  return raw;
}

const ECDSA_P256 = { name: "ECDSA", namedCurve: "P-256" };
export async function verifySignature(spkiB64, signed) {
  const bytes = b64ToBytes(signed.bytes_b64);       // the exact signed bytes (C-6)
  const key = await crypto.subtle.importKey("spki", b64ToBytes(spkiB64), ECDSA_P256, false, ["verify"]);
  const ok = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, derToRaw(b64ToBytes(signed.sig_b64)), bytes);
  return { ok, bytes };
}

export async function replayChain(envelopes) {
  const out = [];
  let previous = ZERO_HEAD;
  for (const env of envelopes) {
    const computedHead = await ledgerHead(env);
    out.push({
      seq: env.seq, recordedHead: env.head, computedHead,
      contentOk: computedHead === env.head,
      linkOk: env.prev === (env.seq === 1 ? ZERO_HEAD : previous),
    });
    previous = computedHead;
  }
  return out;
}

const bytesEqual = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
const NOT_RUN = (why) => ({ ok: null, detail: `— not evaluated (${why})` });

// The record, parsed from the signed bytes (display only). Call it only after the signature checked out.
export function parseRecord(attestation) {
  return JSON.parse(new TextDecoder().decode(b64ToBytes(attestation.decision.bytes_b64)));
}

// The five checks of §9.2, in the C-6 order: signature first, parse second, then digest, chain, head.
export async function checkDecision(attestation, replay) {
  const checks = [];
  const push = (name, result) => checks.push({ id: checks.length + 1, name, ...result });
  let bytes = null;
  let sigOk = false;
  try {
    const res = await verifySignature(attestation.public_key_b64, attestation.decision);
    bytes = res.bytes;
    sigOk = res.ok;
    push("Signature", {
      ok: sigOk,
      detail: sigOk
        ? `ECDSA P-256 over ${bytes.length} raw bytes · key_id ${attestation.decision.key_id} (not KMS)`
        : "the signature does not verify against the public key",
    });
  } catch (error) {
    push("Signature", { ok: false, detail: `unreadable: ${error.message}` });
  }

  let record = null;
  if (!sigOk) {
    push("Record", NOT_RUN("signature failed"));
    push("Digest", NOT_RUN("signature failed"));
  } else {
    try {
      record = JSON.parse(new TextDecoder().decode(bytes));
      const same = bytesEqual(jcs(record), bytes);
      push("Record", {
        ok: same,
        detail: same ? "strict HW-JCS: the parsed record re-encodes to the identical signed bytes"
          : "the signed bytes are not canonical HW-JCS",
      });
    } catch (error) {
      push("Record", { ok: false, detail: `outside HW-JCS: ${error.message}` });
    }
    const digest = await taggedDigest("hw-decision-v1", bytes);
    const digestOk = digest === attestation.decision_digest;
    push("Digest", {
      ok: digestOk,
      detail: digestOk ? "SHA-256(hw-decision-v1 ‖ 0x00 ‖ bytes) equals decision_digest"
        : "SHA-256(hw-decision-v1 ‖ 0x00 ‖ bytes) differs from decision_digest",
    });
  }

  const bad = replay.find((r) => !r.contentOk) ?? replay.find((r) => !r.linkOk);
  const chainOk = bad === undefined;
  push("Chain", {
    ok: chainOk,
    detail: chainOk ? `${replay.length} of ${replay.length} heads re-derive and every link closes`
      : bad.contentOk ? `seq ${bad.seq}: prev does not match the previous head`
        : `seq ${bad.seq}: head does not match its contents`,
  });

  if (!chainOk) push("Head", NOT_RUN("chain broken"));
  else if (record === null) push("Head", NOT_RUN("record unreadable"));
  else {
    const at = replay.find((r) => r.seq === record.at_seq);
    const headOk = at !== undefined && at.computedHead === record.ledger_head;
    push("Head", {
      ok: headOk,
      detail: headOk ? `record.ledger_head equals the replayed head at seq ${record.at_seq}`
        : `signed ledger_head ≠ replayed head at seq ${record.at_seq}`,
    });
  }
  return checks;
}

// The attacker changes one millisecond in envelope k (seq k) and nothing else.
export function tamper(envelopes, k) {
  const copy = structuredClone(envelopes);
  const env = copy.find((e) => e.seq === k);
  env.received_at += 1;
  return copy;
}

// The attacker then recomputes head k and rewrites prev and head for every later envelope.
export async function rechain(envelopes, k) {
  const copy = tamper(envelopes, k);
  let previous = null;
  for (const env of copy) {
    if (env.seq < k) { previous = env.head; continue; }
    if (env.seq > k) env.prev = previous;
    env.head = await ledgerHead(env);
    previous = env.head;
  }
  return copy;
}

const deepFreeze = (value) => {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const v of Object.values(value)) deepFreeze(v);
  }
  return value;
};

// Real results, computed once at load; the director only reveals them at scheduled times.
export async function precompute(ward) {
  const attestation = ward.signed.attestation;
  const envelopes = ward.scenarios[ward.signed.scenario].envelopes;
  const k = ward.signed.intervene_at_seq - 3;
  const view = async (list) => {
    const replay = await replayChain(list);
    return { envelopes: list, replay, checks: await checkDecision(attestation, replay) };
  };
  return deepFreeze({
    k,
    intact: await view(envelopes),
    tampered: await view(tamper(envelopes, k)),
    rechained: await view(await rechain(envelopes, k)),
  });
}
