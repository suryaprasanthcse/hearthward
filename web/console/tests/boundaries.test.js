// Claim 5: the architecture map, the recorded probes, runLive with a stub, and the Ring raw-bytes sandbox (C-5).
import assert from "node:assert/strict";
import { test } from "node:test";
import { architectureMap, boundariesView, computeRing, curlFor, evidenceOf, pathOf, ringOriginal, runLive } from "../claims/boundaries.js";
import { CONSOLE } from "../data/console_data.js";
import { deriveProvenance } from "../provenance.js";
import { initialState, reduce } from "../store.js";

const fresh = (origin = "127.0.0.1:8765") => initialState({ N: 12, provenance: deriveProvenance(CONSOLE, origin) });
const sandbox = (over = {}) => ({ ringBytePatch: null, ringPretty: 0, ...over });
const runButtons = (html) => (html.match(/data-action="run-live"[^>]*disabled/g) ?? []).length;

test("Ring HMAC parity: the browser MAC over fixtures/ring/sample.json equals the manifest's X-Signature", async () => {
  const r = await computeRing(CONSOLE, sandbox());
  assert.equal(r.ok, true);
  assert.equal(r.header, CONSOLE.ring.x_signature);
  assert.equal(r.byteCount, 460);
  assert.equal(CONSOLE.ring.key_label, "synthetic");
});

test("flipping any single byte fails, and so does a pretty-printed copy of the same JSON; reset passes", async () => {
  const original = ringOriginal(CONSOLE);
  for (let offset = 0; offset < original.length; offset += 1) {
    const r = await computeRing(CONSOLE, sandbox({ ringBytePatch: { offset, value: original[offset] ^ 1 } }));
    assert.equal(r.ok, false, `byte ${offset}`);
  }
  const pretty = await computeRing(CONSOLE, sandbox({ ringPretty: 1 }));
  assert.equal(pretty.ok, false);
  assert.ok(pretty.byteCount > original.length);
  const again = (bytes) => JSON.stringify(JSON.parse(new TextDecoder().decode(bytes)));
  assert.equal(again(pretty.bytes), again(original), "same JSON value");
  assert.equal((await computeRing(CONSOLE, reduce(fresh(), { type: "ring_reset" }).sandboxes.boundaries)).ok, true);
});

test("the ring actions: a patch toggles at the same offset, pretty-print and a patch exclude each other", () => {
  let s = reduce(fresh(), { type: "ring_patch", offset: 5, value: 1 });
  assert.deepEqual(s.sandboxes.boundaries, { ringPretty: 0, ringBytePatch: { offset: 5, value: 1 } });
  assert.equal(reduce(s, { type: "ring_patch", offset: 5, value: 1 }).sandboxes.boundaries.ringBytePatch, null);
  s = reduce(s, { type: "ring_pretty" });
  assert.deepEqual(s.sandboxes.boundaries, { ringBytePatch: null, ringPretty: 1 });
  assert.deepEqual(reduce(s, { type: "ring_reset" }).sandboxes.boundaries, { ringBytePatch: null, ringPretty: 0 });
});

test("the recorded probes read as the blueprint says: 401, no WWW-Authenticate, no redirect, a P-256 JWKS, S256", () => {
  const rec = CONSOLE.recorded.probes.data;
  for (const p of rec.probes) assert.equal(evidenceOf(p.id, p, rec.issuer).ok, true, p.id);
  const mcp = rec.probes.find((p) => p.id === "mcp_no_token");
  assert.equal(evidenceOf("mcp_no_token", mcp, rec.issuer).text, "401 · no WWW-Authenticate · no redirect");
  assert.match(evidenceOf("facade_jwks", rec.probes.find((p) => p.id === "facade_jwks"), rec.issuer).text, /^JWKS kid \S+ · EC P-256$/);
  assert.equal(evidenceOf("mcp_no_token", { ...mcp, www_authenticate_present: true }, rec.issuer).ok, false);
  assert.equal(evidenceOf("mcp_no_token", { ...mcp, status: 302 }, rec.issuer).ok, false);
  assert.equal(evidenceOf("mcp_no_token", { error: "timeout" }, rec.issuer).ok, false);
});

test("curl commands replay the recorded request", () => {
  const rec = CONSOLE.recorded.probes.data;
  const mcp = rec.probes.find((p) => p.id === "mcp_no_token");
  const cmd = curlFor(mcp, CONSOLE.topology.probe_requests.mcp_no_token);
  assert.ok(cmd.startsWith(`curl -si -X POST '${mcp.url}'`));
  assert.match(cmd, /-H 'Content-Type: application\/json'/);
  assert.match(cmd, /-H 'MCP-Protocol-Version: 2025-03-26'/);
  assert.match(cmd, /-d '\{.*"tools\/call".*\}'$/);
  const get = rec.probes.find((p) => p.id === "facade_jwks");
  assert.equal(curlFor(get, undefined), `curl -si -X GET '${get.url}'`);
  assert.equal(pathOf(mcp.url), "/mcp");
});

test("runLive: a same-origin path, no redirect following, headers selected, errors never fall back to the recording", async () => {
  const probe = CONSOLE.recorded.probes.data.probes.find((p) => p.id === "mcp_no_token");
  const seen = [];
  const ok = await runLive(probe, CONSOLE.topology.probe_requests.mcp_no_token, {
    now: () => new Date("2026-10-23T14:02:11.123Z"),
    impl: async (path, init) => {
      seen.push([path, init.method, init.redirect]);
      return { status: 401, type: "basic", headers: new Map([["Content-Type", "application/json"], ["X-Secret", "x"]]), text: async () => '{"error":"invalid_token"}' };
    },
  });
  assert.deepEqual(seen, [["/mcp", "POST", "manual"]]);
  assert.equal(ok.status, "ok");
  assert.equal(ok.at, "2026-10-23T14:02:11Z");
  assert.deepEqual(ok.response.headers, { "content-type": "application/json" });
  assert.equal(ok.response.www_authenticate_present, false);
  assert.equal(evidenceOf("mcp_no_token", ok.response).text, "401 · no WWW-Authenticate · no redirect");
  const bad = await runLive(probe, undefined, { impl: async () => ({ status: 401, headers: new Map([["WWW-Authenticate", "Bearer"]]), text: async () => "" }) });
  assert.equal(bad.response.www_authenticate_present, true);
  const redirect = await runLive(probe, undefined, { impl: async () => ({ status: 0, type: "opaqueredirect", headers: new Map(), text: async () => "" }) });
  assert.equal(redirect.response.status, "redirect");
  const failed = await runLive(probe, undefined, { impl: async () => { throw new TypeError("network down"); } });
  assert.deepEqual([failed.status, failed.response.error], ["error", "network down"]);
});

test("the map draws every node and all eight boundaries; undeployed components are never drawn as deployed (T-4)", () => {
  const svg = architectureMap(CONSOLE, fresh());
  assert.equal((svg.match(/class="node-g /g) ?? []).length, CONSOLE.topology.nodes.length);
  for (let i = 1; i <= 8; i += 1) assert.match(svg, new RegExp(`data-node="TB-${i}"`));
  const status = Object.fromEntries(CONSOLE.topology.nodes.map((n) => [n.id, n.status]));
  for (const id of ["broker", "ladder", "anchor"]) assert.equal(status[id], "built", id);
  assert.equal(status.l3, "deferred");
  assert.deepEqual(["ring", "bee"].map((id) => status[id]), ["synthetic", "synthetic"]);
  assert.match(svg, /built, not deployed/);
  assert.equal(CONSOLE.topology.tb.length, 8);
  assert.match(CONSOLE.topology.tb[4].mechanism, /IAM explicitly denies the `mcp` role/);
});

test("the view: probes with curl, Run live disabled locally and enabled on the CloudFront origin, badges everywhere", async () => {
  const state = reduce(fresh(), { type: "claim", claim: "boundaries" });
  const ring = await computeRing(CONSOLE, state.sandboxes.boundaries);
  const v = boundariesView({ state, data: CONSOLE, derived: { ring } });
  assert.match(v.tryit, /data-action="copy-bytes" data-text="curl -si/);
  assert.equal(runButtons(v.tryit), 4);
  assert.match(v.proof, /POST \/mcp: 401 · no WWW-Authenticate · no redirect/);
  assert.match(v.proof, /X-Signature sha256=… matches HMAC-SHA256/);
  assert.match(v.badges + v.proof, /data-badge="recorded"/);
  assert.match(v.proof, /data-badge="synthetic"/);
  const cf = { ...state, provenance: deriveProvenance(CONSOLE, "d1234.cloudfront.net") };
  assert.equal(runButtons(boundariesView({ state: cf, data: CONSOLE, derived: {} }).tryit), 0);
  const broken = await computeRing(CONSOLE, sandbox({ ringPretty: 1 }));
  const pretty = reduce(state, { type: "ring_pretty" });
  assert.match(boundariesView({ state: pretty, data: CONSOLE, derived: { ring: broken } }).proof, /does not match HMAC-SHA256.*after pretty-printing/);
});
