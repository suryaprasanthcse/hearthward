// The inspector drawer (§5): formatted telemetry, canonical HW-JCS bytes, and the proof verification tree.
// Pure: it returns strings. The exact bytes shown are the bytes that are hashed or signed, never a re-serialization.
import { b64ToBytes, jcsText } from "../wardline/chain.js";
import { fmtClock, formatMnats, lrLabel, scenarioOf, shortHash } from "../wardline/model.js";
import { ruleTitle, toolNamed } from "./claims/asymmetry.js";
import { evidenceOf, pathOf } from "./claims/boundaries.js";
import { badge, badges, esc } from "./provenance.js";

export const TABS = Object.freeze([
  { id: "formatted", label: "Formatted" },
  { id: "bytes", label: "Canonical bytes" },
  { id: "tree", label: "Proof tree" },
]);

// Context switching (§5): which tab a selection opens on.
export function defaultTab(selection) {
  if (!selection) return "tree";
  return { envelope: "formatted", decision: "tree", probe: "formatted", rule: "formatted", tool: "bytes", node: "formatted" }[selection.kind] ?? "tree";
}

const TRUST = { observed: "● observed (filled)", claimed: "◌ claimed (dashed)", verified: "◎ verified (double ring)" };
const trustOf = (kind) => (kind.startsWith("room.") ? "claimed" : kind === "system.verified_callback" ? "verified" : "observed");
const field = (k, v) => `<dt>${esc(k)}</dt><dd>${v}</dd>`;
const mono = (v) => `<span class="mono">${esc(v)}</span>`;

function envelopeFor(ctx, seq) {
  const { state, data, derived } = ctx;
  if (state.claim === "bitemporal") return scenarioOf(data.ward, state.sandboxes.bitemporal.scenario).envelopes.find((e) => e.seq === seq) ?? null;
  const pool = derived.integrity?.ledger ?? scenarioOf(data.ward, data.ward.signed.scenario).envelopes;
  return pool.find((e) => e.seq === seq) ?? null;
}

function formattedEnvelope(env, ctx) {
  const epoch = ctx.data.ward.epoch_ms;
  const utc = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, (m) => `.${String(ms % 1000).padStart(3, "0")}Z`);
  const trust = trustOf(env.kind);
  return `<dl>${field("seq", mono(env.seq))}${field("kind", mono(env.kind))}${field("source", mono(env.source))}
${field("trust", esc(TRUST[trust]))}${field("valid", `${mono(`[${fmtClock(env.valid.lo - epoch)}, ${fmtClock(env.valid.hi - epoch)}]`)}<br>${mono(`${utc(env.valid.lo)} … ${utc(env.valid.hi)}`)}`)}
${field("received", `${mono(fmtClock(env.received_at - epoch))} ${mono(utc(env.received_at))}`)}
${field("prev", mono(shortHash(env.prev)))}${field("head", mono(shortHash(env.head)))}
${field("auth", mono(JSON.stringify(env.auth)))}${field("section", "§4.4 BT-4 · §4.2 chain")}</dl>
<p>${badges("recorded", { synthetic: true })}</p>`;
}

function decisionRecord(ctx) {
  const { derived, data } = ctx;
  if (derived.integrity) return { record: derived.integrity.record, bytes: derived.integrity.bytes, key: derived.integrity.keyText, source: derived.integrity.source };
  const att = data.ward.signed.attestation;
  const bytes = b64ToBytes(att.decision.bytes_b64);
  return { record: JSON.parse(new TextDecoder().decode(bytes)), bytes, key: att.decision.key_id, source: "synthetic" };
}

function formattedDecision(ctx) {
  const { record, key, source } = decisionRecord(ctx);
  return `<dl>${field("decision", mono(record.decision))}${field("at_seq", mono(record.at_seq))}
${field("Λ", `${mono(`${formatMnats(record.lambda_mnats)} mn`)} · likelihood ratio ${mono(lrLabel(record.lambda_mnats))}`)}
${field("thresholds", mono(`A ${formatMnats(record.thresholds_mnats.a)} · B ${formatMnats(record.thresholds_mnats.b)} mn`))}
${field("ledger_head", mono(shortHash(record.ledger_head)))}${field("cross terms", mono(record.cross_terms.join(", ") || "none"))}
${field("reasons", mono(record.reasons.join(", ")))}${field("key", mono(key))}${field("section", "§9 · C-6")}</dl>
<p>${source === "kms" ? badge("recorded") : badge("synthetic")} ${badge("synthetic")}</p>`;
}

function formattedRule(ctx, id) {
  const h = ctx.data.spec.hx.find((x) => x.id === id);
  if (!h) return `<p class="dim">no such rule</p>`;
  return `<dl>${field("rule", mono(h.id))}${field("title", esc(h.title))}${field("statement", esc(h.statement))}${field("applies to", mono(h.applies_to.join(", ")))}${field("enforced at", mono(h.enforced_at))}${field("on fail", mono(h.on_fail))}</dl><p>${badge("computed")} read from the hearthward:hx block as exported</p>`;
}

function bytesPanel(text, tag, digest, note) {
  const count = new TextEncoder().encode(text).length;
  return `<div class="bytes-head"><span class="mono">${count} bytes</span> · tag <span class="mono">${esc(tag)}</span>${digest ? ` · <span class="mono">${esc(shortHash(digest))}</span>` : ""} ${note}</div>
<pre class="bytes" id="bytes-pre">${esc(text)}</pre><button class="btn" data-action="copy-bytes" data-text="${esc(text)}">Copy</button>`;
}

function canonicalOf(ctx) {
  const { state, derived } = ctx;
  const sel = state.selection;
  if (!sel) return `<p class="dim">Select an envelope, the decision, or an S- tool to see the exact bytes that are hashed or signed.</p>`;
  if (sel.kind === "envelope") {
    const env = envelopeFor(ctx, Number(sel.id));
    if (!env) return `<p class="dim">no such envelope</p>`;
    const { head, ...body } = env;
    const replay = derived.integrity?.replay.find((r) => r.seq === env.seq);
    return bytesPanel(jcsText(body), "hw-ledger-v1", replay?.computedHead ?? head, badge("computed"));
  }
  if (sel.kind === "decision") {
    const { bytes, source } = decisionRecord(ctx);
    return bytesPanel(new TextDecoder().decode(bytes), "hw-decision-v1", derived.integrity?.checks[2]?.digest ?? derived.integrity?.attestation.decision_digest, source === "kms" ? badge("recorded") : badge("synthetic"));
  }
  if (sel.kind === "tool") {
    if (!derived.asymmetry) return `<p class="pending">computing…</p>`;
    return bytesPanel(derived.asymmetry.text, "hw-action-v1", derived.asymmetry.digest, badge("computed"));
  }
  return `<p class="dim">This selection has no hashed document.</p>`;
}

function formattedOf(ctx) {
  const sel = ctx.state.selection;
  if (!sel) return `<p class="dim">Nothing selected. Click an envelope, the decision, a rule or a tool.</p>`;
  if (sel.kind === "envelope") {
    const env = envelopeFor(ctx, Number(sel.id));
    return env ? formattedEnvelope(env, ctx) : `<p class="dim">no such envelope</p>`;
  }
  if (sel.kind === "decision") return formattedDecision(ctx);
  if (sel.kind === "rule") return formattedRule(ctx, sel.id);
  if (sel.kind === "tool") {
    const t = toolNamed(ctx.data, sel.id);
    return t ? `<dl>${field("tool", mono(t.name))}${field("class", mono(`${t.class} · fails ${t.fails}`))}${field("rules", t.rules.map((id) => `${mono(id)} ${esc(ruleTitle(ctx.data, id))}`).join("<br>"))}${field("description", esc(t.description))}</dl>` : "";
  }
  if (sel.kind === "node") return formattedNode(ctx, sel.id);
  if (sel.kind === "probe") return formattedProbe(ctx, sel.id);
  return `<p class="dim">${esc(sel.kind)} ${esc(sel.id)}</p>`;
}

function formattedNode(ctx, id) {
  const topo = ctx.data.topology;
  const tb = topo.tb.find((t) => t.id === id);
  if (tb) return `<dl>${field("boundary", mono(tb.id))}${field("crossing", esc(tb.boundary))}${field("enforced by", esc(tb.mechanism))}${field("what it establishes", esc(tb.trust))}${field("section", "master blueprint §2 trust boundaries")}</dl><p>${badge("computed")} read from the blueprint table as exported</p>`;
  const n = topo.nodes.find((x) => x.id === id);
  if (!n) return `<p class="dim">no such node</p>`;
  const synthetic = n.status === "synthetic" ? ` ${badge("synthetic")}` : "";
  return `<dl>${field("node", esc(n.label))}${field("group", mono(n.group))}${field("status", esc(n.id === "console" ? `served from ${ctx.state.provenance.hosting}` : n.status))}${field("detail", esc(n.detail))}${field("source", esc(topo.source))}</dl><p>${badge("recorded")}${synthetic}</p>`;
}

function formattedProbe(ctx, id) {
  const rec = ctx.data.recorded?.probes;
  const p = rec?.data.probes.find((x) => x.id === id);
  if (!p) return `<p class="dim">no recording for ${esc(id)}</p>`;
  const live = ctx.state.live[id];
  const ev = evidenceOf(id, p, rec.data.issuer);
  const body = p.body_json !== null && p.body_json !== undefined ? JSON.stringify(p.body_json, null, 2) : (p.body_text ?? "");
  return `<dl>${field("request", mono(`${p.method} ${pathOf(p.url)}`))}${field("status", mono(p.status ?? p.error))}${field("verdict", esc(ev.text))}${field("headers", `<pre class="bytes">${esc(JSON.stringify(p.headers, null, 2))}</pre>`)}
${field("body", `<pre class="bytes">${esc(body.slice(0, 2000))}</pre>`)}${field("recorded", mono(p.at_utc))}${live?.response ? field("live", mono(`${live.response.status ?? live.response.error} at ${live.at}`)) : ""}</dl><p>${badge("recorded", p.at_utc)}</p>`;
}

// ── proof tree ──
const node = (label, ok, detail, kids = "", kinds = ["computed"]) => {
  const mark = ok === null ? "—" : ok ? "✓" : "✗";
  const head = `<span class="mark ${ok === false ? "fail" : ok ? "ok" : ""}">${mark}</span> <b>${esc(label)}</b> <span class="dim">${esc(detail)}</span> ${kinds.map((k) => badge(k)).join("")}`;
  return kids ? `<details${ok === false ? " open" : ""}><summary>${head}</summary>${kids}</details>` : `<div class="leaf">${head}</div>`;
};

function treeOf(ctx) {
  const { state, derived } = ctx;
  if (state.claim === "integrity") {
    const r = derived.integrity;
    if (!r) return `<p class="pending">verifying…</p>`;
    const chainKids = r.replay.map((x) => node(`seq ${x.seq}`, x.contentOk && x.linkOk, `content ${x.contentOk ? "✓" : "✗"} · link ${x.linkOk ? "✓" : "✗"}`)).join("");
    const kids = r.checks.map((c) => (c.name === "Chain" ? node("chain", c.ok, c.detail, chainKids) : node(c.name.toLowerCase(), c.ok, c.detail))).join("");
    const all = r.checks.every((c) => c.ok);
    return `<div class="tree">${node("Claim 4", all ? true : r.checks.some((c) => c.ok === false) ? false : null, "tamper-evident and signed", kids)}</div>`;
  }
  if (state.claim === "asymmetry") {
    const a = derived.asymmetry;
    return `<div class="tree">${node("Claim 2", a ? a.problems.length === 0 : null, "S- needs proof bound to the action", a ? node("action_hash", true, shortHash(a.digest)) + node("HX-03 challenge", a.challengeDecodes, "equals action_hash") : "")}</div>`;
  }
  if (state.claim === "bitemporal") {
    const b = derived.bitemporal;
    return `<div class="tree">${node("Claim 3", b ? b.linked && b.headAt === b.recorded : null, "a late event never rewrites the decision", b ? node("head at at_seq", b.headAt === b.recorded, shortHash(b.headAt)) + node("later envelopes", true, String(b.later)) : "")}</div>`;
  }
  if (state.claim === "walk") return `<div class="tree">${node("Claim 1", null, "see the proof row under the walk", "", ["recorded", "synthetic"])}</div>`;
  const rec = ctx.data.recorded?.probes;
  const kids = (rec?.data.probes ?? []).map((p) => { const ev = evidenceOf(p.id, p, rec.data.issuer); return node(`${p.method} ${pathOf(p.url)}`, ev.ok, ev.text, "", ["recorded"]); }).join("");
  const ring = derived.ring;
  const ringKid = ring ? node("Ring X-Signature", ring.ok, `HMAC-SHA256 over ${ring.byteCount} raw bytes`, "", ["computed", "synthetic"]) : "";
  const all = rec ? (rec.data.probes.every((p) => evidenceOf(p.id, p, rec.data.issuer).ok) && (ring?.ok ?? true)) : null;
  return `<div class="tree">${node("Claim 5", all, "runs on AWS behind OAuth", kids + ringKid, ["recorded"])}</div>`;
}

export function inspectorView(ctx, ui) {
  const tab = ui.tab ?? defaultTab(ctx.state.selection);
  const tabs = TABS.map((t) => `<button role="tab" data-action="tab" data-tab="${t.id}" aria-selected="${t.id === tab}" class="tab${t.id === tab ? " on" : ""}">${esc(t.label)}</button>`).join("");
  const body = tab === "formatted" ? formattedOf(ctx) : tab === "bytes" ? canonicalOf(ctx) : treeOf(ctx);
  const sel = ctx.state.selection;
  return `<div class="tabs" role="tablist">${tabs}</div><div class="insp-sub label">${sel ? esc(`${sel.kind} ${sel.id}`) : "no selection"}</div><div class="insp-body" role="tabpanel">${body}</div>`;
}
