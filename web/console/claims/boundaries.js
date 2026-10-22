// Claim 5: "It really runs on AWS, behind Alexa+'s OAuth" (§4.5). Only the recorded probe table exists so far; the
// architecture map, live probes and the Ring HMAC sandbox arrive in Phase 5 and are not shown until they are built (T-4).
import { badge, badges, esc } from "../provenance.js";

export function boundariesView({ state, data }) {
  const rec = data.recorded?.probes;
  const rows = rec ? rec.data.probes.map((p) => {
    const absent = p.www_authenticate_present === false;
    return `<tr data-action="select-probe" data-probe="${esc(p.id)}"><td class="mono">${esc(p.method)}</td><td class="mono wrap">${esc(p.url)}</td><td>${esc(p.expected_status)}</td><td class="mono">${esc(p.status ?? p.error)}</td><td>${absent ? "absent ✓" : "PRESENT ✗"}</td><td class="mono">${esc(p.at_utc)}</td></tr>`;
  }).join("") : "";
  const hero = rec
    ? `<table class="ledger"><thead><tr><th>method</th><th>url</th><th>expected</th><th>recorded</th><th>WWW-Authenticate</th><th>at (UTC)</th></tr></thead><tbody>${rows}</tbody></table>`
    : `<p class="dim">No probe recording is present. Run <span class="mono">tools/record_probe.py</span> with your AWS stack deployed.</p>`;
  const mcp = rec?.data.probes.find((p) => p.id === "mcp_no_token");
  const proof = mcp
    ? `<ul class="proof"><li class="check ${mcp.status === 401 && !mcp.www_authenticate_present ? "ok" : "fail"}"><span class="mark">${mcp.status === 401 && !mcp.www_authenticate_present ? "✓" : "✗"}</span> POST /mcp without a token: ${esc(mcp.status)} · no WWW-Authenticate · no redirect ${badge("recorded", mcp.at_utc)}</li></ul>
<p class="note">Recorded by ${esc(rec.data.tool)} at commit ${esc(rec.data.git_commit.slice(0, 7))}. Not yet built: the architecture map, live probes through CloudFront and the Ring HMAC sandbox (Phase 5).</p>`
    : "";
  return {
    title: "It really runs on AWS, behind Alexa+'s OAuth",
    assertion: "Missing or invalid tokens get a 401 with a JSON body and no WWW-Authenticate header, because Alexa+ does not support it (P-5).",
    badges: rec ? badges("recorded", { detail: rec.data.recorded_at_utc }) : "",
    hero, tryit: "", proof,
  };
}
