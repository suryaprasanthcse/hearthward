// Claim 2: "A voice in the room cannot lower protection" (§4.2). The blocked call and its rules are recorded; the
// action hash a passkey must sign (HX-03) is computed here from the exact document.
import { bytesToHex, b64ToBytes, jcsText, taggedDigest } from "../../wardline/chain.js";
import { esc, badge, badges } from "../provenance.js";

const NONCE = /^[A-Za-z0-9_-]{22}$/;

export const toolsOf = (data) => data.spec.tools;
export const toolNamed = (data, name) => data.spec.tools.find((t) => t.name === name);
export const ruleTitle = (data, id) => data.spec.hx.find((h) => h.id === id)?.title ?? id;

// A value the schema accepts for each property, so any S- tool can be shown.
export function defaultArgs(tool, data) {
  const out = {};
  const props = tool.input_schema.properties ?? {};
  for (const name of tool.input_schema.required ?? Object.keys(props)) {
    const p = props[name];
    if (p.enum) out[name] = p.enum[0];
    else if (p.type === "integer") out[name] = p.minimum ?? 1;
    else if (name === "episode_id") out[name] = data.ward.scenarios.doorstep_scam.decision.episode;
    else if (name === "phone_e164") out[name] = "+15555550100";
    else out[name] = "Example";
  }
  return out;
}

// Throws unless `value` satisfies the property schema; returns the typed value.
export function validateArg(schema, value) {
  if (schema.type === "integer") {
    const n = typeof value === "number" ? value : Number(String(value).trim());
    if (!Number.isSafeInteger(n)) throw new Error("must be an integer");
    if (schema.minimum !== undefined && n < schema.minimum) throw new Error(`must be at least ${schema.minimum}`);
    if (schema.maximum !== undefined && n > schema.maximum) throw new Error(`must be at most ${schema.maximum}`);
    return n;
  }
  const text = String(value);
  if (schema.enum && !schema.enum.includes(text)) throw new Error(`must be one of ${schema.enum.join(", ")}`);
  if (schema.minLength !== undefined && text.length < schema.minLength) throw new Error(`needs at least ${schema.minLength} characters`);
  if (schema.maxLength !== undefined && text.length > schema.maxLength) throw new Error(`at most ${schema.maxLength} characters`);
  if (schema.pattern && !new RegExp(schema.pattern).test(text)) throw new Error("does not match the required pattern");
  return text;
}

// The action document of HX-03, from the recorded blocked call plus the sandbox's edits.
export function actionDocument(data, sandbox) {
  const base = data.action.document;
  const tool = toolNamed(data, sandbox.tool);
  const args = sandbox.tool === base.tool ? { ...base.args } : defaultArgs(tool, data);
  const problems = [];
  const props = tool.input_schema.properties ?? {};
  for (const [name, raw] of Object.entries(sandbox.args)) {
    try {
      args[name] = validateArg(props[name], raw);
    } catch (error) {
      problems.push(`${name}: ${error.message}`);
    }
  }
  let nonce = base.nonce;
  if (sandbox.nonce !== null) {
    if (NONCE.test(sandbox.nonce)) nonce = sandbox.nonce;
    else problems.push("nonce: 22 base64url characters (128 bits)");
  }
  let exp = base.exp;
  if (sandbox.exp !== null) {
    try { exp = validateArg({ type: "integer", minimum: 0 }, sandbox.exp); } catch (error) { problems.push(`exp: ${error.message}`); }
  }
  return { doc: { tool: sandbox.tool, args, household: base.household, episode_version: base.episode_version, nonce, exp }, problems };
}

const b64url = (hex) => btoa(String.fromCharCode(...hex.match(/../g).map((h) => parseInt(h, 16)))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
const hexOfB64url = (text) => bytesToHex(b64ToBytes(text.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - (text.length % 4)) % 4)));

// SHA-256("hw-action-v1" ‖ 0x00 ‖ JCS(doc)), exactly as src/hearthward/authz/broker.py action_hash.
export async function actionHash(doc) {
  const bytes = new TextEncoder().encode(jcsText(doc));
  const digest = await taggedDigest("hw-action-v1", bytes);
  const hex = digest.slice(7);
  return { text: jcsText(doc), byteCount: bytes.length, digest, hex, challenge: b64url(hex) };
}

export async function computeAsymmetry(data, sandbox) {
  const { doc, problems } = actionDocument(data, sandbox);
  const mine = await actionHash(doc);
  const base = await actionHash(actionDocument(data, { tool: sandbox.tool, args: {}, nonce: null, exp: null }).doc);
  return { ...mine, doc, problems, baseline: base.digest, challengeDecodes: hexOfB64url(mine.challenge) === mine.hex };
}

function bounce(tool, closed) {
  const label = esc(`${tool.name} (${tool.class})`);
  return `<svg class="hero-svg" viewBox="0 0 1000 220" role="img" aria-label="A request from the room meets the ward line">
<rect x="30" y="80" width="170" height="60" rx="3" class="node"/><text x="115" y="106" text-anchor="middle" class="svg-label">Room (Alexa+)</text><text x="115" y="124" text-anchor="middle" class="svg-mono dim">voice, token only</text>
<line x1="600" y1="20" x2="600" y2="200" class="membrane"/><text x="600" y="14" text-anchor="middle" class="svg-label">WARD LINE · S-</text>
${closed
    ? `<line x1="200" y1="110" x2="592" y2="110" class="req"/><path d="M592 110 L580 104 L580 116 Z" class="req-head"/><path d="M588 110 C 520 60, 400 60, 330 96" class="bounce"/><path d="M330 96 L342 92 L338 104 Z" class="bounce-head"/>
<text x="396" y="40" text-anchor="middle" class="svg-mono hot">BLOCKED · PROOF_REQUIRED</text>`
    : `<line x1="200" y1="110" x2="960" y2="110" class="req pass"/><path d="M960 110 L948 104 L948 116 Z" class="req-pass-head"/><text x="780" y="96" text-anchor="middle" class="svg-mono">passes, no proof needed</text>`}
<text x="396" y="136" text-anchor="middle" class="svg-mono">${label}</text></svg>`;
}

function toolColumns(data, selected) {
  const col = (cls, title, note) => `<div class="col"><div class="label">${esc(title)}</div><div class="dim">${esc(note)}</div>${toolsOf(data).filter((t) => t.class === cls).map((t) => `<button data-action="tool" data-tool="${t.name}" class="toolrow${t.name === selected ? " on" : ""}"><span class="mono">${esc(t.name)}</span><span class="dim">${t.class} · fails ${t.fails}${t.rules.includes("HX-10") ? " · rate-limited (HX-10)" : ""}</span></button>`).join("")}</div>`;
  return `<div class="cols">${col("S+", "S+ · raises protection", "fails open, rate-limited by HX-10")}${col("S-", "S- · lowers protection", "fails closed")}</div>`;
}

function argInputs(tool, sandbox, derived) {
  const props = tool.input_schema.properties ?? {};
  const rows = Object.entries(props).map(([name, p]) => {
    const shown = derived?.doc.args[name] ?? "";
    const raw = sandbox.args[name] ?? shown;
    const field = p.enum
      ? `<select name="${name}" data-action="arg" data-name="${name}">${p.enum.map((v) => `<option${v === String(raw) ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>`
      : `<input name="${name}" data-action="arg" data-name="${name}" type="${p.type === "integer" ? "number" : "text"}" value="${esc(raw)}" ${p.maxLength ? `maxlength="${p.maxLength}"` : ""} spellcheck="false">`;
    return `<label>${esc(name)} ${field}</label>`;
  });
  return rows.join("");
}

export function asymmetryView({ state, data, derived }) {
  const sandbox = state.sandboxes.asymmetry;
  const tool = toolNamed(data, sandbox.tool);
  const closed = tool.class === "S-";
  const result = derived.asymmetry;
  const rules = tool.rules.filter((id) => id !== "HX-11");
  const stamp = closed
    ? `<div class="stamp">BLOCKED · PROOF_REQUIRED</div><ul class="rules">${rules.map((id) => `<li><button data-action="select-rule" data-rule="${id}" class="link"><span class="mono">${id}</span> ${esc(ruleTitle(data, id))}</button></li>`).join("")}</ul>`
    : `<div class="stamp pass">PASSES · no proof needed</div>`;
  const proofRow = data.ward.scenarios.doorstep_scam.rows.find((r) => r.event === "PROOF_REQ");
  const recorded = sandbox.tool === "dismiss_alert";
  const hero = bounce(tool, closed) + stamp +
    (recorded ? `<p class="note">Recorded blocked call: seq ${proofRow.seq}, rules ${esc(data.action.rules.join(", "))}, ΔΛ = ${proofRow.delta_mnats} ${badges("recorded", { synthetic: true })}</p>` : "");
  const builder = result
    ? `<div class="card"><div class="label">Action document · HW-JCS · ${result.byteCount} bytes ${badge("computed")}</div><pre class="bytes">${esc(result.text)}</pre>
<div class="label">WebAuthn challenge (base64url)</div><div class="mono wrap">${esc(result.challenge)}</div></div>` : `<p class="pending">computing…</p>`;
  const tryit = `${toolColumns(data, sandbox.tool)}
${closed ? `<form class="controls edit" autocomplete="off" data-action="noop">${argInputs(tool, sandbox, result)}
<label>nonce <input name="nonce" data-action="nonce" type="text" value="${esc(result?.doc.nonce ?? "")}" maxlength="22" spellcheck="false"></label>
<label>exp <input name="exp" data-action="exp" type="number" value="${esc(result?.doc.exp ?? "")}"></label>
<button type="button" data-action="asymmetry-reset" class="btn">Reset</button></form>
${result?.problems.length ? `<p class="fail">${esc(result.problems.join("; "))} (last valid value kept)</p>` : ""}${builder}
<p class="note"><a href="../approve.html">approve.html</a>, the caregiver's page that signs exactly this challenge: built; the broker endpoint is not deployed.</p>` : `<p class="dim">${esc(tool.description)}</p>`}`;
  const proofItems = [];
  if (closed && result) {
    const edited = result.digest !== result.baseline;
    proofItems.push(
      `<li class="check ok"><span class="mark">✓</span> action_hash = <span class="mono">${esc(result.digest)}</span> ${badge("computed")}</li>`,
      `<li class="check ${result.challengeDecodes ? "ok" : "fail"}"><span class="mark">${result.challengeDecodes ? "✓" : "✗"}</span> HX-03: challenge equals action_hash ${badge("computed")}</li>`,
      `<li class="check ${result.problems.length ? "fail" : "ok"}"><span class="mark">${edited ? "≠" : "="}</span> ${edited ? "this edit changes the hash, so a passkey assertion for one action cannot authorize another" : "unedited: same hash as the recorded action"} ${badge("computed")}</li>`,
    );
  }
  if (recorded) {
    proofItems.push(`<li class="check ${proofRow.delta_mnats === 0 ? "ok" : "fail"}"><span class="mark">${proofRow.delta_mnats === 0 ? "✓" : "✗"}</span> the blocked request moved Λ by ${proofRow.delta_mnats} mn ${badges("recorded", { synthetic: true })}</li>`);
  }
  return {
    title: "A voice in the room cannot lower protection",
    assertion: "S+ fails open and S- fails closed. Dismissing an alert needs a passkey assertion whose challenge is the hash of this exact action; an Alexa token alone never suffices (HX-02).",
    badges: badges("recorded", { synthetic: true }) + badge("computed"),
    hero, tryit, proof: `<ul class="proof">${proofItems.join("") || `<li class="check ok"><span class="mark">✓</span> ${esc(tool.name)} is S+: rules that apply are ${esc(rules.join(", ") || "none")}, none requires a passkey ${badge("computed")}</li>`}</ul>`,
  };
}
