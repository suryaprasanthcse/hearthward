// Claim 5: "It really runs on AWS, behind Alexa+'s OAuth" (§4.5): the architecture map with its trust boundaries,
// the recorded (and, on the CloudFront origin, live) probes, and the Ring raw-bytes HMAC sandbox (C-5).
// This is the only module that may call fetch, and only for same-origin paths.
import { b64ToBytes, bytesToHex } from "../../wardline/chain.js";
import { badge, badges, esc } from "../provenance.js";

// ── the architecture map: geometry only; labels, statuses and boundaries come from the data ──
const NODE_W = 130;
const NODE_H = 44;
const POS = {
  alexa: [20, 40], mcp_api: [210, 40], alb: [400, 40], mcp: [590, 40], bedrock: [780, 40],
  auth_api: [210, 130], cognito: [400, 130], fusion: [590, 130], falkordb: [780, 130],
  facade_kms: [400, 215],
  ring: [20, 300], bee: [20, 365], ingest: [210, 332], ledger: [590, 332], security_state: [780, 332],
  console: [210, 400], attest_kms: [400, 400],
  caregiver: [20, 470], broker: [210, 470], ladder: [400, 470], l3: [590, 470], anchor: [780, 470],
};
const EDGES = [
  ["alexa", "mcp_api", "TB-1"], ["alb", "mcp", "TB-2"], ["ring", "ingest", "TB-3"], ["bee", "ingest", "TB-4"],
  ["mcp", "security_state", "TB-5"], ["caregiver", "broker", "TB-6"], ["bedrock", "fusion", "TB-7"], ["attest_kms", "console", "TB-8"],
  ["mcp_api", "alb", null], ["mcp", "fusion", null], ["fusion", "falkordb", null], ["fusion", "ledger", null],
  ["ingest", "ledger", null], ["auth_api", "cognito", null], ["broker", "ladder", null], ["ladder", "l3", null],
];
const STATUS_CLASS = { deployed: "st-deployed", built: "st-built", deferred: "st-deferred", synthetic: "st-deferred", external: "st-external", local: "st-deployed" };
const STATUS_TEXT = { deployed: "deployed", built: "built, not deployed", deferred: "deferred", synthetic: "synthetic only", external: "external", local: "this page" };
const centre = (id) => [POS[id][0] + NODE_W / 2, POS[id][1] + NODE_H / 2];

export function architectureMap(data, state) {
  const nodes = data.topology.nodes;
  const sel = state.selection?.kind === "node" ? state.selection.id : null;
  const edges = EDGES.map(([a, b, tb]) => {
    const [x1, y1] = centre(a);
    const [x2, y2] = centre(b);
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    return tb
      ? `<g data-action="select-node" data-node="${tb}" class="tb${sel === tb ? " sel" : ""}"><line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="tb-line"/><circle cx="${mx}" cy="${my}" r="12" class="tb-dot"/><text x="${mx}" y="${my + 4}" text-anchor="middle" class="svg-mono">${tb.slice(3)}</text></g>`
      : `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="edge"/>`;
  }).join("");
  const boxes = nodes.map((n) => {
    const [x, y] = POS[n.id];
    const label = n.id === "console" ? `${n.label}` : n.label;
    const status = n.id === "console" ? `served from ${state.provenance.hosting}` : STATUS_TEXT[n.status];
    return `<g data-action="select-node" data-node="${n.id}" class="node-g ${STATUS_CLASS[n.status]}${sel === n.id ? " sel" : ""}"><rect x="${x}" y="${y}" width="${NODE_W}" height="${NODE_H}" rx="3" class="map-node"/>
<text x="${x + NODE_W / 2}" y="${y + 19}" text-anchor="middle" class="svg-mono">${esc(label.length > 22 ? label.slice(0, 21) + "…" : label)}</text><text x="${x + NODE_W / 2}" y="${y + 35}" text-anchor="middle" class="svg-mono dim">${esc(status)}</text></g>`;
  }).join("");
  const legend = `<g class="legend-g"><rect x="20" y="532" width="26" height="12" class="map-node st-deployed"/><text x="54" y="542" class="svg-mono">solid: deployed</text><rect x="190" y="532" width="26" height="12" class="map-node st-built"/><text x="224" y="542" class="svg-mono">dashed: built, not deployed</text><rect x="440" y="532" width="26" height="12" class="map-node st-deferred"/><text x="474" y="542" class="svg-mono">dotted: deferred or synthetic only</text><circle cx="760" cy="538" r="8" class="tb-dot"/><text x="776" y="542" class="svg-mono">trust boundary TB-n</text></g>`;
  return `<svg class="hero-svg" viewBox="0 0 1000 560" role="img" aria-label="Architecture map with trust boundaries TB-1 to TB-8">${edges}${boxes}${legend}</svg>`;
}

// ── probes ──
export const pathOf = (url) => new URL(url).pathname;

export function curlFor(probe, request) {
  const parts = ["curl", "-si", "-X", probe.method, `'${probe.url}'`];
  for (const [k, v] of Object.entries(request?.headers ?? {})) parts.push("-H", `'${k}: ${v}'`);
  if (request?.body) parts.push("-d", `'${request.body}'`);
  return parts.join(" ");
}

const SELECTED = ["content-type", "cache-control", "location", "www-authenticate", "server", "date", "content-length"];

// Fetches one same-origin path (CloudFront routes it to the API) with a 5 s timeout and no redirect following.
export async function runLive(probe, request, { impl = null, now = () => new Date() } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  const at = now().toISOString().replace(/\.\d{3}Z$/, "Z");
  try {
    const init = { method: probe.method, redirect: "manual", signal: controller.signal, headers: request?.headers ?? {}, body: request?.body };
    const res = await (impl ?? ((path, opts) => fetch(path, opts)))(pathOf(probe.url), init);
    const headers = {};
    for (const [k, v] of res.headers ?? []) if (SELECTED.includes(k.toLowerCase())) headers[k.toLowerCase()] = v;
    const text = (await res.text?.()) ?? "";
    let body = null;
    try { body = JSON.parse(text); } catch { /* not JSON */ }
    const status = res.type === "opaqueredirect" ? "redirect" : res.status;
    return { status: "ok", at, response: { status, headers, www_authenticate_present: "www-authenticate" in headers, body_json: body, body_text: body === null ? text.slice(0, 2000) : null } };
  } catch (error) {
    return { status: "error", at, response: { error: error.name === "AbortError" ? "timeout" : String(error.message ?? error) } };
  } finally {
    clearTimeout(timer);
  }
}

// What a recorded or live response proves, computed from the response itself (never a literal verdict).
export function evidenceOf(id, res, issuer) {
  if (res.status === undefined) return { ok: false, text: `error ${res.error ?? "no response"}` };
  const auth = res.www_authenticate_present ? "WWW-Authenticate present" : "no WWW-Authenticate";
  const redirect = res.status >= 300 && res.status < 400 ? "redirect" : "no redirect";
  const body = res.body_json;
  if (id === "mcp_no_token") return { ok: [401, 403].includes(res.status) && !res.www_authenticate_present && redirect === "no redirect", text: `${res.status} · ${auth} · ${redirect}` };
  if (id === "facade_jwks") {
    const key = body?.keys?.[0];
    return { ok: res.status === 200 && key?.kty === "EC" && key?.crv === "P-256", text: key ? `JWKS kid ${key.kid} · ${key.kty} ${key.crv}` : `${res.status} · no key` };
  }
  if (id === "facade_metadata") {
    const s256 = (body?.code_challenge_methods_supported ?? []).includes("S256");
    return { ok: res.status === 200 && s256, text: `${res.status} · S256 ${s256 ? "advertised" : "missing"} · ${auth}` };
  }
  const first = (body?.authorization_servers ?? [])[0];
  return { ok: res.status === 200 && (!issuer || first === issuer), text: `${res.status} · authorization_servers[0] ${first === issuer ? "is the facade" : "differs"} · ${auth}` };
}

function probePanel(state, data) {
  const rec = data.recorded?.probes;
  if (!rec) return `<p class="dim">No probe recording is present. Run <span class="mono">tools/record_probe.py</span> against a deployed stack.</p>`;
  const live = state.provenance.hosting === "cloudfront";
  const rows = rec.data.probes.map((p) => {
    const lv = state.live[p.id];
    const request = data.topology.probe_requests[p.id];
    const ev = evidenceOf(p.id, p, rec.data.issuer);
    const liveEv = lv?.response ? evidenceOf(p.id, lv.response, rec.data.issuer) : null;
    return `<tr data-action="select-probe" data-probe="${esc(p.id)}"><td class="mono">${esc(p.method)}</td><td class="mono wrap">${esc(pathOf(p.url))}</td><td>${esc(p.expected_status)}</td>
<td class="${ev.ok ? "ok" : "fail"} mono">${esc(p.status ?? p.error)} · ${esc(p.at_utc)}</td>
<td><button class="btn" data-action="run-live" data-probe="${esc(p.id)}"${live && lv?.status !== "pending" ? "" : " disabled"} title="${live ? "" : "Available when this page is served from the CloudFront origin, where same-origin paths reach the APIs."}">Run live</button>
<button class="btn" data-action="copy-bytes" data-text="${esc(curlFor(p, request))}">Copy curl</button>${liveEv ? `<div class="${liveEv.ok ? "ok" : "fail"} mono">${esc(liveEv.text)} ${badge("live", lv.at)}</div>` : lv?.status === "pending" ? `<div class="dim">pending…</div>` : ""}</td></tr>`;
  }).join("");
  return `<table class="ledger probes"><thead><tr><th>method</th><th>path</th><th>expected</th><th>recorded</th><th>live / curl</th></tr></thead><tbody>${rows}</tbody></table>
<p class="note">Recorded by ${esc(rec.data.tool)} at ${esc(rec.data.recorded_at_utc)} ${badge("recorded")}. Run live is for the CloudFront origin only; nothing falls back silently to the recording.</p>`;
}

// ── the Ring raw-bytes sandbox (C-5): authenticate the bytes that arrived, never a re-serialization ──
export const ringOriginal = (data) => b64ToBytes(data.ring.body_b64);

export function ringBytes(data, sandbox) {
  const original = ringOriginal(data);
  if (sandbox.ringPretty) return new TextEncoder().encode(JSON.stringify(JSON.parse(new TextDecoder().decode(original)), null, 2));
  if (sandbox.ringBytePatch) {
    const out = Uint8Array.from(original);
    out[sandbox.ringBytePatch.offset] = sandbox.ringBytePatch.value;
    return out;
  }
  return original;
}

export async function computeRing(data, sandbox) {
  const bytes = ringBytes(data, sandbox);
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(data.ring.synthetic_key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = bytesToHex(new Uint8Array(await crypto.subtle.sign("HMAC", key, bytes)));
  const header = `sha256=${mac}`;
  // The result names the edit it was computed for, so a view never pairs a new edit with an old verdict.
  return { bytes, header, ok: header === data.ring.x_signature, byteCount: bytes.length, patch: sandbox.ringBytePatch, pretty: sandbox.ringPretty };
}

function ringCard(state, data, ring) {
  const sb = state.sandboxes.boundaries;
  const bytes = ring?.bytes ?? ringOriginal(data);
  const patched = sb.ringBytePatch?.offset;
  const cells = [];
  for (let row = 0; row * 16 < bytes.length; row += 1) {
    const slice = Array.from(bytes.subarray(row * 16, row * 16 + 16));
    cells.push(`<div class="hexrow"><span class="dim mono">${(row * 16).toString(16).padStart(4, "0")}</span>${slice.map((b, i) => `<button class="hexb${row * 16 + i === patched ? " patched" : ""}" data-action="ring-flip" data-offset="${row * 16 + i}"${sb.ringPretty ? " disabled" : ""} aria-label="flip a bit of byte ${row * 16 + i}">${b.toString(16).padStart(2, "0")}</button>`).join("")}</div>`);
  }
  return `<div class="card"><div class="label">Ring webhook, raw bytes ${badges("synthetic")} key: <span class="mono">${esc(data.ring.synthetic_key)}</span> (synthetic)</div>
<div class="controls"><button class="btn${sb.ringPretty ? " on" : ""}" data-action="ring-pretty">Pretty-print (same JSON)</button><button class="btn" data-action="ring-reset">Reset</button><span class="dim">click a byte to flip its lowest bit</span></div>
<div class="hex">${cells.join("")}</div>
<p class="mono wrap">X-Signature header: ${esc(data.ring.x_signature)}<br>HMAC-SHA256(raw body): ${esc(ring?.header ?? "…")}</p></div>`;
}

export function boundariesView({ state, data, derived }) {
  const rec = data.recorded?.probes;
  const sb = state.sandboxes.boundaries;
  const ring = derived.ring;
  const probeLines = rec ? rec.data.probes.map((p) => {
    const ev = evidenceOf(p.id, p, rec.data.issuer);
    return `<li class="check ${ev.ok ? "ok" : "fail"}"><span class="mark">${ev.ok ? "✓" : "✗"}</span> ${esc(p.method)} ${esc(pathOf(p.url))}: ${esc(ev.text)} ${badge("recorded", p.at_utc)}</li>`;
  }).join("") : "";
  const ringLine = ring
    ? `<li class="check ${ring.ok ? "ok" : "fail"}"><span class="mark">${ring.ok ? "✓" : "✗"}</span> X-Signature sha256=… ${ring.ok ? "matches" : "does not match"} HMAC-SHA256(raw body, ${ring.byteCount} bytes)${ring.pretty ? " after pretty-printing" : ring.patch ? ` after flipping a bit of byte ${ring.patch.offset}` : ""} ${badges("computed", { synthetic: true })}</li>` : "";
  return {
    title: "It really runs on AWS, behind Alexa+'s OAuth",
    assertion: "A missing or invalid token gets a 401 with a JSON body and no WWW-Authenticate header (Alexa+ does not support it, P-5). Ring signatures cover the raw bytes, so a re-serialized body fails (C-5).",
    badges: (rec ? badge("recorded", rec.data.recorded_at_utc) : "") + badge("computed") + badge("synthetic"),
    hero: architectureMap(data, state),
    tryit: `<div class="label">Probes</div>${probePanel(state, data)}${ringCard(state, data, ring)}`,
    proof: `<ul class="proof">${probeLines}${ringLine}</ul><p class="note">Node status comes from ${esc(data.topology.source)}. CloudFront and the live probes arrive with Phase 6 and are not drawn until deployed.</p>`,
  };
}
