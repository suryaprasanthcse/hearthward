// Claims 1-3 and the inspector: logic and rendered strings, with their known answers.
import assert from "node:assert/strict";
import { test } from "node:test";
import { actionDocument, actionHash, computeAsymmetry, defaultArgs, toolNamed, validateArg } from "../claims/asymmetry.js";
import { computeBitemporal, versionAt } from "../claims/bitemporal.js";
import { computeIntegrity } from "../claims/integrity.js";
import { waldBounds } from "../claims/walk.js";
import { CONSOLE } from "../data/console_data.js";
import { TABS, defaultTab, inspectorView } from "../inspector.js";
import { deriveProvenance } from "../provenance.js";
import { CLAIMS, initialState, reduce } from "../store.js";
import { claimRegions, navHtml, needs } from "../views.js";
import { scenarioOf } from "../../wardline/model.js";

const provenance = deriveProvenance(CONSOLE, "127.0.0.1:8765");
const fresh = () => initialState({ N: 12, provenance });
const RECORDED_HASH = "sha256:259e387a748f84585932debdd199b5cde92ce74dc950d815eeb0dda9d06f8c51"; // = hearthward.authz.broker.action_hash

async function derivedFor(state) {
  return {
    integrity: await computeIntegrity(CONSOLE, state.sandboxes.integrity, state.provenance),
    asymmetry: await computeAsymmetry(CONSOLE, state.sandboxes.asymmetry),
    bitemporal: await computeBitemporal(CONSOLE, state.sandboxes.bitemporal.scenario),
  };
}

test("Wald's bounds are computed from alpha and beta and equal the spec: 4,554 and -2,986 mn", () => {
  assert.deepEqual(waldBounds(CONSOLE.spec.sprt), { a: 4554, b: -2986 });
});

test("the action hash of the recorded blocked call equals the Python broker's, and any edit changes it", async () => {
  const { doc, problems } = actionDocument(CONSOLE, { tool: "dismiss_alert", args: {}, nonce: null, exp: null });
  assert.deepEqual(problems, []);
  const h = await actionHash(doc);
  assert.equal(h.digest, RECORDED_HASH);
  assert.equal(h.challenge.length, 43);
  assert.match(h.challenge, /^[A-Za-z0-9_-]+$/);
  const edited = await actionHash(actionDocument(CONSOLE, { tool: "dismiss_alert", args: { reason: "known_person" }, nonce: null, exp: null }).doc);
  assert.notEqual(edited.digest, h.digest);
  const nonce = await actionHash(actionDocument(CONSOLE, { tool: "dismiss_alert", args: {}, nonce: "A".repeat(22), exp: null }).doc);
  assert.notEqual(nonce.digest, h.digest);
  const tool = await computeAsymmetry(CONSOLE, { tool: "pause_monitoring", args: {}, nonce: null, exp: null });
  assert.equal(tool.doc.tool, "pause_monitoring");
  assert.deepEqual(tool.doc.args, { hours: 1, reason: "travel" });
  assert.equal(tool.challengeDecodes, true);
});

test("arguments are validated against the tool schema; invalid values are reported and not used", async () => {
  const pause = toolNamed(CONSOLE, "pause_monitoring").input_schema.properties;
  assert.equal(validateArg(pause.hours, "5"), 5);
  assert.throws(() => validateArg(pause.hours, "25"), /at most 24/);
  assert.throws(() => validateArg(pause.hours, "1.5"));
  assert.throws(() => validateArg(pause.reason, "holiday"), /one of/);
  const bad = await computeAsymmetry(CONSOLE, { tool: "pause_monitoring", args: { hours: "99" }, nonce: "short", exp: "x" });
  assert.equal(bad.problems.length, 3);
  assert.equal(bad.doc.args.hours, 1, "the last valid value is kept");
  for (const t of CONSOLE.spec.tools) {
    const args = defaultArgs(t, CONSOLE);
    for (const [k, v] of Object.entries(args)) assert.doesNotThrow(() => validateArg(t.input_schema.properties[k], v), `${t.name}.${k}`);
  }
});

test("the tools are the spec's: six S+ that fail open, five S- that fail closed", () => {
  const by = (c) => CONSOLE.spec.tools.filter((t) => t.class === c);
  assert.equal(by("S+").length, 6);
  assert.equal(by("S-").length, 5);
  assert.ok(by("S-").every((t) => t.fails === "closed" && t.rules.includes("HX-02")));
  assert.deepEqual(CONSOLE.action.rules, ["HX-01", "HX-02", "HX-09"]);
});

test("Claim 3: the late run keeps the decision at seq 7 and re-derives its head", async () => {
  const r = await computeBitemporal(CONSOLE, "late_and_lossy");
  assert.equal(r.linked, true);
  assert.equal(r.headAt, r.recorded);
  assert.equal(r.later, 5);
  const sc = scenarioOf(CONSOLE.ward, "late_and_lossy");
  const fid = sc.versions[0].fid;
  assert.equal(versionAt(sc, fid, 7).n, 2);
  assert.equal(sc.versions.filter((v) => v.fid === fid).find((v) => v.te === null).n, 5);
});

test("every claim renders its four regions, every region set carries a badge, and the proof rows are present", async () => {
  for (const claim of CLAIMS) {
    const state = reduce(fresh(), { type: "claim", claim });
    const parts = claimRegions({ state, data: CONSOLE, derived: await derivedFor(state) });
    assert.match(parts.head, /<h1>/, claim);
    assert.match(parts.head, /data-badge="(recorded|computed|live|synthetic)"/, `${claim} head has a badge`);
    assert.ok(parts.hero.length > 200, `${claim} hero`);
    assert.match(parts.proof + parts.head, /data-badge=/, `${claim} proof badge`);
    assert.doesNotMatch(parts.head + parts.hero + parts.tryit + parts.proof, /undefined|NaN|\[object/, `${claim} leaks a bad value`);
  }
});

test("the deep links render the numbers the blueprint promises", async () => {
  let state = reduce(reduce(fresh(), { type: "claim", claim: "walk" }), { type: "seq", seq: 8 });
  let parts = claimRegions({ state, data: CONSOLE, derived: await derivedFor(state) });
  assert.match(parts.hero, /\+6,066/);
  assert.match(parts.hero, /431 : 1/);
  assert.match(parts.hero, /A · INTERVENE · 95 : 1/);
  assert.match(parts.proof, /\[1500, 1777, 3623, 6066\]/);
  assert.match(parts.proof, /crossed A at observation 4 \(seq 8\)/);
  state = reduce(fresh(), { type: "claim", claim: "asymmetry" });
  parts = claimRegions({ state, data: CONSOLE, derived: await derivedFor(state) });
  assert.match(parts.hero, /BLOCKED · PROOF_REQUIRED/);
  for (const id of ["HX-01", "HX-02", "HX-09"]) assert.match(parts.hero, new RegExp(id));
  assert.match(parts.proof, new RegExp(RECORDED_HASH));
  assert.match(parts.tryit, /built; the broker endpoint is not deployed/);
  state = reduce(reduce(fresh(), { type: "claim", claim: "bitemporal" }), { type: "seq", seq: 7 });
  parts = claimRegions({ state, data: CONSOLE, derived: await derivedFor(state) });
  assert.match(parts.proof, /decision at seq 7/);
  assert.match(parts.proof, /\[\+00:02\.800, \+00:30\.801\] v2; now \[−00:01\.200, \+01:17\.803\] v5/);
});

test("the walk presets switch, and an S+ tool shows the pass-through path", async () => {
  let state = reduce(reduce(fresh(), { type: "claim", claim: "walk" }), { type: "preset", preset: "benign_delivery" });
  let parts = claimRegions({ state, data: CONSOLE, derived: {} });
  assert.match(parts.proof, /A not crossed/);
  assert.doesNotMatch(parts.hero, /crossed A at observation/);
  state = reduce(reduce(fresh(), { type: "claim", claim: "asymmetry" }), { type: "tool", tool: "get_door_activity" });
  parts = claimRegions({ state, data: CONSOLE, derived: await derivedFor(state) });
  assert.match(parts.hero, /passes, no proof needed/);
  assert.doesNotMatch(parts.hero, /BLOCKED/);
});

test("the navigation marks the current claim, and only the needed derived values are requested", () => {
  const state = reduce(fresh(), { type: "claim", claim: "integrity" });
  assert.match(navHtml(state), /class="navbtn on" data-action="claim" data-claim="integrity" aria-current="page"/);
  assert.deepEqual(needs(state), { integrity: true, asymmetry: false, bitemporal: false, ring: false });
  assert.equal(needs(reduce(fresh(), { type: "claim", claim: "boundaries" })).integrity, false);
});

test("the inspector: three tabs, the right default per selection, a badge on every tab", async () => {
  assert.deepEqual(TABS.map((t) => t.id), ["formatted", "bytes", "tree"]);
  assert.equal(defaultTab(null), "tree");
  assert.equal(defaultTab({ kind: "envelope", id: "3" }), "formatted");
  assert.equal(defaultTab({ kind: "decision", id: "latest" }), "tree");
  assert.equal(defaultTab({ kind: "tool", id: "dismiss_alert" }), "bytes");
  assert.equal(defaultTab({ kind: "rule", id: "HX-02" }), "formatted");
  const base = reduce(fresh(), { type: "claim", claim: "integrity" });
  const cases = [null, { kind: "envelope", id: "5" }, { kind: "decision", id: "latest" }];
  for (const selection of cases) {
    const state = { ...base, selection };
    const derived = await derivedFor(state);
    for (const tab of TABS) {
      const html = inspectorView({ state, data: CONSOLE, derived }, { tab: tab.id });
      assert.match(html, /data-badge=|dim">/, `${selection?.kind ?? "none"}/${tab.id}`);
    }
  }
  const state = { ...base, selection: { kind: "envelope", id: "5" } };
  const html = inspectorView({ state, data: CONSOLE, derived: await derivedFor(state) }, { tab: "bytes" });
  assert.match(html, /tag <span class="mono">hw-ledger-v1<\/span>/);
  assert.match(html, /&quot;received_at&quot;:/);
  const dec = { ...base, selection: { kind: "decision", id: "latest" } };
  const dhtml = inspectorView({ state: dec, data: CONSOLE, derived: await derivedFor(dec) }, { tab: "bytes" });
  assert.match(dhtml, /hw-decision-v1/);
  assert.match(dhtml, /521 bytes/);
});

test("a failed node expands automatically in the proof tree", async () => {
  const state = reduce(reduce(reduce(fresh(), { type: "claim", claim: "integrity" }), { type: "tamper", value: 1 }), { type: "rechain", value: 1 });
  const html = inspectorView({ state, data: CONSOLE, derived: await derivedFor(state) }, { tab: "tree" });
  assert.match(html, /<details open><summary><span class="mark fail">✗<\/span> <b>Claim 4<\/b>/);
  assert.match(html, /signed ledger_head ≠ replayed head at seq 8/);
});
