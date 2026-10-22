// Claim 4: "Every decision is tamper-evident and signed" (§4.4). The logic is async and DOM-free (Wardline's chain.js
// does the cryptography); `integrityView` turns a result into strings.
import {
  ZERO_HEAD, b64ToBytes, jcs, jcsText, ledgerHead, replayChain, taggedDigest, verifySignature,
} from "../../wardline/chain.js";
import { fmtClock, hashTail, shortHash, tileBits, teethOpen } from "../../wardline/model.js";
import { badge, badges, esc, keyLabel } from "../provenance.js";

const SAFE = Number.MAX_SAFE_INTEGER;
const MAX_STRING = 256;

// ── the signed material: the KMS bundle when it was recorded, else the synthetic demo-key bundle ──
export function attestationOf(data) {
  const b = data.recorded?.bundle?.data;
  if (b && b.kms) {
    return {
      source: "kms", meta: b.kms,
      attestation: { public_key_b64: b.public_key_b64, decision: b.decision, decision_digest: b.decision_digest },
      ledger: b.ledger,
    };
  }
  const s = data.ward.signed;
  return { source: "synthetic", meta: null, attestation: s.attestation, ledger: data.ward.scenarios[s.scenario].envelopes };
}

// ── crypto cache (§3.3): the promise of each head, keyed by the exact bytes it hashes ──
const headCache = new Map();
export function cachedHead(envelope) {
  const { head, ...body } = envelope;
  const key = `head:${jcsText(body)}`;
  if (!headCache.has(key)) headCache.set(key, ledgerHead(envelope));
  return headCache.get(key);
}
export const cacheSize = () => headCache.size;

// ── field edits: scalars only; an integer stays an integer, a string stays bounded ──
export const EDITABLE = Object.freeze(["received_at", "valid.lo", "valid.hi", "kind", "source_event_id"]);

export function coerceEdit(current, value) {
  if (typeof current === "number") {
    const n = typeof value === "number" ? value : Number(String(value).trim());
    if (!Number.isSafeInteger(n) || Math.abs(n) > SAFE) throw new Error("must be an integer within 2^53 - 1");
    return n;
  }
  if (typeof current === "string") {
    const text = String(value);
    if (text.length > MAX_STRING) throw new Error(`must be at most ${MAX_STRING} characters`);
    return text;
  }
  throw new Error("only scalar fields can be edited");
}

function setPath(envelope, path, value) {
  const keys = path.split(".");
  let node = envelope;
  for (const k of keys.slice(0, -1)) node = node?.[k];
  const last = keys[keys.length - 1];
  if (node === undefined || node === null || !(last in node)) throw new Error(`no field ${path}`);
  node[last] = coerceEdit(node[last], value);
}

export const readPath = (envelope, path) => path.split(".").reduce((node, k) => node?.[k], envelope);

// The sandbox applied to the signed ledger: edits, then the one-click tamper, then (optionally) a re-chain.
export async function applySandbox(ledger, sandbox, atSeq) {
  const copy = structuredClone(ledger);
  const touched = [];
  const rejected = [];
  for (const edit of sandbox.edits) {
    try {
      const env = copy.find((e) => e.seq === edit.seq);
      if (!env) throw new Error(`no envelope ${edit.seq}`);
      setPath(env, edit.path, edit.value);
      touched.push(edit.seq);
    } catch (error) {
      rejected.push({ ...edit, reason: error.message });
    }
  }
  const k = atSeq - 3;
  if (sandbox.tamper) {
    copy.find((e) => e.seq === k).received_at += 1;
    touched.push(k);
  }
  if (sandbox.rechain && touched.length) {
    const from = Math.min(...touched);
    let previous = copy.find((e) => e.seq === from - 1)?.head ?? ZERO_HEAD;
    for (const env of copy) {
      if (env.seq < from) continue;
      if (env.seq > from) env.prev = previous;
      env.head = await cachedHead(env);
      previous = env.head;
    }
  }
  return { ledger: copy, touched, rejected, firstTouched: touched.length ? Math.min(...touched) : null };
}

const bytesEqual = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
const NOT_RUN = (why) => ({ ok: null, detail: `— not evaluated (${why})` });

// The five checks of §4.4e in the C-6 order, with the key shown as the provenance says (T-5).
export async function runChecks(attestation, replay, keyText) {
  const checks = [];
  const push = (name, result) => checks.push({ id: checks.length + 1, name, ...result });
  let bytes = null;
  let sigOk = false;
  try {
    const res = await verifySignature(attestation.public_key_b64, attestation.decision);
    bytes = res.bytes;
    sigOk = res.ok;
    push("Signature", { ok: sigOk, detail: sigOk ? `ECDSA P-256 over ${bytes.length} raw bytes · key ${keyText}` : "the signature does not verify against the public key" });
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
      push("Record", { ok: same, detail: same ? "re-encodes to the identical signed bytes" : "the signed bytes are not canonical HW-JCS" });
    } catch (error) {
      push("Record", { ok: false, detail: `outside HW-JCS: ${error.message}` });
    }
    const digest = await taggedDigest("hw-decision-v1", bytes);
    const digestOk = digest === attestation.decision_digest;
    push("Digest", { ok: digestOk, detail: digestOk ? `${shortHash(digest)} equals decision_digest` : `${shortHash(digest)} differs from decision_digest`, digest });
  }
  const bad = replay.find((r) => !r.contentOk) ?? replay.find((r) => !r.linkOk);
  const chainOk = bad === undefined;
  push("Chain", {
    ok: chainOk,
    detail: chainOk ? `${replay.length} envelopes replay from genesis`
      : bad.contentOk ? `seq ${bad.seq}: prev does not match the previous head` : `seq ${bad.seq}: head does not match its contents`,
  });
  if (!chainOk) push("Head", NOT_RUN("chain broken"));
  else if (record === null) push("Head", NOT_RUN("record unreadable"));
  else {
    const at = replay.find((r) => r.seq === record.at_seq);
    const headOk = at !== undefined && at.computedHead === record.ledger_head;
    push("Head", { ok: headOk, detail: headOk ? `ledger_head equals the replayed head at seq ${record.at_seq}` : `signed ledger_head ≠ replayed head at seq ${record.at_seq}` });
  }
  return { checks, record, bytes };
}

// Everything the view and the inspector need, for one sandbox state. `original` is the untouched replay.
export async function computeIntegrity(data, sandbox, provenance) {
  const att = attestationOf(data);
  const record = JSON.parse(new TextDecoder().decode(b64ToBytes(att.attestation.decision.bytes_b64)));
  const applied = await applySandbox(att.ledger, sandbox, record.at_seq);
  const replayOf = async (ledger) => {
    const out = [];
    let previous = ZERO_HEAD;
    for (const env of ledger) {
      const computedHead = await cachedHead(env);
      out.push({ seq: env.seq, recordedHead: env.head, computedHead, contentOk: computedHead === env.head, linkOk: env.prev === previous });
      previous = computedHead;
    }
    return out;
  };
  const replay = await replayOf(applied.ledger);
  const original = await replayOf(att.ledger);
  const keyText = att.source === "kms" ? keyLabel(att.attestation.decision.key_id, provenance) : keyLabel(att.attestation.decision.key_id, { kms: "synthetic-only" });
  const { checks, bytes } = await runChecks(att.attestation, replay, keyText);
  const teeth = replay.map((r, i) => (i + 1 < replay.length ? teethOpen(r.computedHead, applied.ledger[i + 1].prev) : null));
  return {
    source: att.source, meta: att.meta, attestation: att.attestation, record, bytes, keyText,
    original: att.ledger, ledger: applied.ledger, replay, originalReplay: original, checks, teeth,
    touched: applied.touched, rejected: applied.rejected, firstTouched: applied.firstTouched,
    ranAt: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
  };
}

// The bundle a judge can pass to `python tools/verify_attestation.py bundle.json` (the current, possibly edited, ledger).
export function bundleJson(result, data) {
  const bundle = {
    v: 1, public_key_b64: result.attestation.public_key_b64, decision: result.attestation.decision,
    decision_digest: result.attestation.decision_digest, ledger: result.ledger,
  };
  const text = JSON.stringify(bundle, null, 2);
  // sprt_params is spliced in as Python's own text: JSON.stringify would print 1.0 as 1 and break params_digest.
  const raw = data.recorded?.bundle?.sprt_params_json;
  const withParams = raw ? text.slice(0, -2) + ',\n  "sprt_params": ' + raw + "\n}" : text;
  return withParams + "\n";
}

// ── view ──
const LANES = { door: "DOOR", room: "ROOM", wrist: "WRIST", system: "SYSTEM" };
const laneOf = (env) => (env.source === "ring" ? "door" : env.source === "alexa" ? "room" : env.source === "bee" ? "wrist" : "system");
const mark = (ok) => (ok === null ? "—" : ok ? "✓" : "✗");

function zipper(result) {
  const n = result.replay.length;
  const tile = 64;
  const pitch = Math.floor((1000 - 12 - tile) / Math.max(1, n - 1));
  const x0 = 6;
  const parts = [];
  result.replay.forEach((r, i) => {
    const x = x0 + i * pitch;
    const bits = tileBits(r.computedHead);
    const was = tileBits(result.originalReplay[i].recordedHead);
    const changed = r.computedHead !== result.originalReplay[i].recordedHead;
    const cells = [];
    bits.forEach((row, ry) => row.forEach((on, cx) => {
      const flipped = on !== was[ry][cx];
      if (on || flipped) cells.push(`<rect x="${x + cx * 4}" y="${20 + ry * 4}" width="4" height="4" class="${flipped ? "cell-flip" : "cell-on"}"/>`);
    }));
    parts.push(`<g data-action="select-envelope" data-seq="${r.seq}" class="tile${changed ? " tile-changed" : ""}">`,
      `<rect x="${x}" y="20" width="${tile}" height="${tile}" class="tile-box"/>${cells.join("")}`,
      `<text x="${x + tile / 2}" y="12" class="zip-label" text-anchor="middle">${r.seq}</text>`,
      `<text x="${x + tile / 2}" y="${20 + tile + 14}" class="zip-label" text-anchor="middle">${esc(hashTail(r.computedHead))}</text></g>`);
    const open = result.teeth[i];
    if (open) {
      const gx = x + tile;
      open.forEach((isOpen, j) => {
        const y = 24 + j * 8;
        parts.push(isOpen
          ? `<line x1="${gx}" y1="${y}" x2="${gx + 5}" y2="${y}" class="tooth-open"/><line x1="${gx + pitch - tile - 5}" y1="${y}" x2="${gx + pitch - tile}" y2="${y}" class="tooth-open"/>`
          : `<line x1="${gx}" y1="${y}" x2="${gx + pitch - tile}" y2="${y}" class="tooth"/>`);
      });
    }
  });
  return `<svg class="hero-svg" viewBox="0 0 1000 120" role="img" aria-label="Twelve ledger heads as 16 by 16 bit tiles joined by eight-tooth links">${parts.join("")}</svg>`;
}

function ledgerTable(result, selected, epochMs) {
  const rows = result.ledger.map((env, i) => {
    const r = result.replay[i];
    const edited = result.touched.includes(env.seq);
    const lane = LANES[laneOf(env)];
    return `<tr data-action="select-envelope" data-seq="${env.seq}" class="${selected === String(env.seq) ? "sel" : ""}${edited ? " edited" : ""}">
<td>${env.seq}</td><td>${lane}</td><td>${esc(env.kind)}</td>
<td>[${esc(fmtClock(env.valid.lo - epochMs))}, ${esc(fmtClock(env.valid.hi - epochMs))}]</td>
<td>${esc(fmtClock(env.received_at - epochMs))}</td>
<td class="mono">${esc(hashTail(r.computedHead))}</td>
<td class="${r.contentOk ? "ok" : "fail"}">${mark(r.contentOk)}</td><td class="${r.linkOk ? "ok" : "fail"}">${mark(r.linkOk)}</td></tr>`;
  });
  return `<table class="ledger"><thead><tr><th>seq</th><th>lane</th><th>kind</th><th>valid</th><th>received</th><th>head</th><th>content</th><th>link</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

export function integrityView({ state, data, derived }) {
  const result = derived.integrity;
  const kms = state.provenance.kms === "recorded";
  const title = kms ? "Every decision is tamper-evident and signed by AWS KMS" : "Every decision is tamper-evident and signed (demo key, not KMS)";
  const sigBadge = kms ? badge("recorded", result?.meta?.signed_at_utc ?? "") : badge("synthetic");
  const head = {
    title,
    assertion: "head = SHA-256(\"hw-ledger-v1\" ‖ 0x00 ‖ JCS(envelope without head)); the decision is signed over its raw canonical bytes, and its ledger_head must equal the replayed head.",
    badges: badge("computed") + sigBadge + badge("synthetic"),
  };
  if (!result) return { ...head, hero: `<p class="pending">verifying…</p>`, tryit: "", proof: "" };
  const epochMs = data.ward.epoch_ms;
  const sandbox = state.sandboxes.integrity;
  const sel = state.selection?.kind === "envelope" ? state.selection.id : null;
  const editable = result.ledger.find((e) => String(e.seq) === (sel ?? "5")) ?? result.ledger[0];
  const tryit = `
<div class="controls">
  <button data-action="tamper" class="btn${sandbox.tamper ? " on" : ""}">Tamper <kbd>T</kbd></button>
  <button data-action="rechain" class="btn${sandbox.rechain ? " on" : ""}"${sandbox.tamper ? "" : " disabled"}>Re-chain</button>
  <button data-action="reset" class="btn">Reset</button>
  <button data-action="verify" class="btn">Verify <kbd>V</kbd></button>
  <button data-action="download" class="btn">Download bundle.json</button>
</div>
<form class="controls edit" data-action="edit-field" autocomplete="off">
  <label>envelope <select name="seq">${result.ledger.map((e) => `<option value="${e.seq}"${e.seq === editable.seq ? " selected" : ""}>${e.seq}</option>`).join("")}</select></label>
  <label>field <select name="path">${EDITABLE.map((p) => `<option>${p}</option>`).join("")}</select></label>
  <label>value <input name="value" type="text" maxlength="${MAX_STRING}" spellcheck="false" aria-label="new value" placeholder="${esc(String(readPath(editable, "received_at")))}"></label>
  <button class="btn" type="submit">Apply edit</button>
  ${result.rejected.length ? `<span class="fail">rejected: ${esc(result.rejected.map((r) => `${r.path} (${r.reason})`).join("; "))}</span>` : ""}
</form>`;
  const lines = result.checks.map((c) => `<li class="check ${c.ok === null ? "na" : c.ok ? "ok" : "fail"}"><span class="mark">${mark(c.ok)}</span> <b>${c.id}. ${esc(c.name)}</b> · ${esc(c.detail)} ${badge("computed")}</li>`);
  const keyBadge = kms ? badge("recorded", result.meta?.signed_at_utc ?? "") : badge("synthetic");
  const proof = `<ol class="proof">${lines.join("")}</ol>
<p class="note">Key ${esc(result.keyText)} ${keyBadge}${kms ? ` · ${esc(result.meta.region)} · signed at commit ${esc(result.meta.git_commit.slice(0, 7))}` : ""} · verified at ${esc(result.ranAt)} ${badge("computed")}</p>`;
  const hero = zipper(result) + ledgerTable(result, sel, epochMs);
  return { ...head, hero, tryit, proof };
}
