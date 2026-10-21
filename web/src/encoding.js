// Byte helpers. Everything the pages exchange with the server is base64, base64url or hex.

export function bytesToHex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function hexToBytes(hex) {
  if (typeof hex !== "string" || hex.length % 2 !== 0 || !/^[0-9a-f]*$/.test(hex)) {
    throw new Error("not lowercase hex");
  }
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i += 1) out[i] = parseInt(hex.slice(2 * i, 2 * i + 2), 16);
  return out;
}

export function bytesToB64(bytes) {
  let text = "";
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text);
}

export function b64ToBytes(text) {
  if (typeof text !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(text) || text.length % 4 !== 0) {
    throw new Error("not base64");
  }
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
}

export function bytesToB64url(bytes) {
  return bytesToB64(bytes).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

export function b64urlToBytes(text) {
  if (typeof text !== "string" || !/^[A-Za-z0-9_-]*$/.test(text)) throw new Error("not base64url");
  const padded = text.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - (text.length % 4)) % 4);
  return b64ToBytes(padded);
}

function readTlv(data, pos) {
  if (pos + 2 > data.length) throw new Error("DER: truncated");
  const tag = data[pos];
  let length = data[pos + 1];
  let start = pos + 2;
  if (length >= 0x80) {
    const count = length & 0x7f;
    if (count === 0 || count > 2 || start + count > data.length) throw new Error("DER: bad length");
    length = 0;
    for (let i = 0; i < count; i += 1) length = length * 256 + data[start + i];
    start += count;
    if (length < 0x80) throw new Error("DER: non-minimal length");
  }
  if (start + length > data.length) throw new Error("DER: truncated");
  return { tag, body: data.subarray(start, start + length), end: start + length };
}

function intTo32(body) {
  if (body.length === 0 || (body[0] & 0x80)) throw new Error("DER: not a non-negative integer");
  let value = body;
  if (value.length > 1 && value[0] === 0) {
    if (!(value[1] & 0x80)) throw new Error("DER: non-minimal integer");
    value = value.subarray(1);
  }
  if (value.length > 32) throw new Error("DER: integer too large for P-256");
  const out = new Uint8Array(32);
  out.set(value, 32 - value.length);
  return out;
}

// An ECDSA signature as KMS and authenticators return it (DER) to the raw r || s that WebCrypto verifies.
export function derToRaw(der) {
  const outer = readTlv(der, 0);
  if (outer.tag !== 0x30 || outer.end !== der.length) throw new Error("signature is not one DER SEQUENCE");
  const r = readTlv(outer.body, 0);
  const s = readTlv(outer.body, r.end);
  if (r.tag !== 0x02 || s.tag !== 0x02 || s.end !== outer.body.length) throw new Error("not SEQUENCE { INTEGER, INTEGER }");
  const raw = new Uint8Array(64);
  raw.set(intTo32(r.body), 0);
  raw.set(intTo32(s.body), 32);
  return raw;
}

// The inverse, used by tests to make KMS-shaped signatures from WebCrypto's raw ones.
export function rawToDer(raw) {
  const encode = (part) => {
    let body = part;
    while (body.length > 1 && body[0] === 0) body = body.subarray(1);
    if (body[0] & 0x80) body = Uint8Array.from([0, ...body]);
    return Uint8Array.from([0x02, body.length, ...body]);
  };
  const r = encode(raw.subarray(0, 32));
  const s = encode(raw.subarray(32, 64));
  return Uint8Array.from([0x30, r.length + s.length, ...r, ...s]);
}
