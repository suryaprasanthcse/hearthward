// node --test web/console/tests. The store, the URL contract (§3.2) and the badge model.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CONSOLE } from "../data/console_data.js";
import { badge, badges, deriveProvenance, esc, findStatusWords } from "../provenance.js";
import {
  CLAIMS, applyHash, createStore, initialState, memoize, parseHash, reduce, selectWalk, toHash,
} from "../store.js";

const N = CONSOLE.ward.scenarios.doorstep_scam.envelopes.length;
const fresh = () => initialState({ N, provenance: deriveProvenance(CONSOLE, "http://127.0.0.1:8765") });
const roundTrip = (state) => applyHash(fresh(), parseHash(toHash(state), { N }));

test("the defaults are claim=walk, seq=N, tamper=0", () => {
  const s = fresh();
  assert.deepEqual([s.claim, s.seq, s.sandboxes.integrity.tamper], ["walk", N, 0]);
  assert.equal(N, 12);
  assert.deepEqual(parseHash("", { N }), { claim: "walk", seq: N, tamper: 0, rechain: 0, preset: "doorstep_scam", selection: null });
});

test("every deep link of §3.2 parses to the expected state", () => {
  const cases = [
    ["#claim=walk&seq=8", { claim: "walk", seq: 8, tamper: 0, rechain: 0 }],
    ["#claim=asymmetry&seq=11", { claim: "asymmetry", seq: 11, tamper: 0, rechain: 0 }],
    ["#claim=bitemporal&seq=7", { claim: "bitemporal", seq: 7, tamper: 0, rechain: 0 }],
    ["#claim=integrity&tamper=1", { claim: "integrity", seq: N, tamper: 1, rechain: 0 }],
    ["#claim=integrity&tamper=1&rechain=1", { claim: "integrity", seq: N, tamper: 1, rechain: 1 }],
    ["#claim=boundaries", { claim: "boundaries", seq: N, tamper: 0, rechain: 0 }],
  ];
  for (const [hash, want] of cases) {
    const got = parseHash(hash, { N });
    for (const [k, v] of Object.entries(want)) assert.equal(got[k], v, `${hash} ${k}`);
  }
});

test("unknown keys are ignored and invalid values fall back to the defaults", () => {
  const got = parseHash("#claim=nope&seq=99&tamper=2&rechain=1&bogus=1&preset=x&sel=envelope:5", { N });
  assert.deepEqual(got, { claim: "walk", seq: N, tamper: 0, rechain: 0, preset: "doorstep_scam", selection: { kind: "envelope", id: "5" } });
  assert.equal(parseHash("#seq=0", { N }).seq, N);
  assert.equal(parseHash("#seq=-3", { N }).seq, N);
  assert.equal(parseHash("#claim=boundaries&sel=envelope:5", { N }).selection, null, "a selection must belong to the claim");
  assert.equal(parseHash("#claim=walk&sel=envelope:<script>", { N }).selection, null);
  assert.equal(parseHash("#claim=integrity&rechain=1", { N }).rechain, 0, "rechain needs tamper");
});

test("state -> hash -> state round-trips", () => {
  let s = fresh();
  for (const action of [
    { type: "claim", claim: "integrity" }, { type: "seq", seq: 8 }, { type: "tamper", value: 1 }, { type: "rechain", value: 1 },
    { type: "select", selection: { kind: "envelope", id: "5" } },
  ]) s = reduce(s, action);
  const back = roundTrip(s);
  for (const k of ["claim", "seq", "selection"]) assert.deepEqual(back[k], s[k]);
  assert.deepEqual(back.sandboxes.integrity, { edits: [], tamper: 1, rechain: 1 });
  assert.equal(toHash(s), "#claim=integrity&seq=8&tamper=1&rechain=1&sel=envelope:5");
  const other = reduce(reduce(fresh(), { type: "preset", preset: "grandparent_call" }), { type: "claim", claim: "boundaries" });
  assert.equal(roundTrip(other).sandboxes.walk.preset, "grandparent_call");
  for (const claim of CLAIMS) assert.equal(roundTrip(reduce(fresh(), { type: "claim", claim })).claim, claim);
});

test("reducers are pure, return the same state for no-ops, and clear selections that do not belong", () => {
  const s = fresh();
  assert.equal(reduce(s, { type: "seq", seq: N }), s);
  assert.equal(reduce(s, { type: "claim", claim: "walk" }), s);
  assert.equal(reduce(s, { type: "unknown" }), s);
  assert.equal(reduce(s, { type: "seq", seq: 500 }), s, "out of range is ignored");
  const picked = reduce(s, { type: "select", selection: { kind: "envelope", id: "5" } });
  assert.deepEqual(picked.selection, { kind: "envelope", id: "5" });
  assert.equal(reduce(picked, { type: "claim", claim: "integrity" }).selection?.id, "5");
  assert.equal(reduce(picked, { type: "claim", claim: "asymmetry" }).selection, null);
  assert.equal(reduce(s, { type: "select", selection: { kind: "bad", id: "x" } }), s);
  assert.equal(s.sandboxes.integrity.tamper, 0, "the old state was not mutated");
  const t = reduce(reduce(s, { type: "tamper", value: 1 }), { type: "rechain", value: 1 });
  assert.equal(reduce(t, { type: "tamper", value: 0 }).sandboxes.integrity.rechain, 0);
  assert.equal(reduce(s, { type: "rechain", value: 1 }).sandboxes.integrity.rechain, 0);
  const edited = reduce(reduce(s, { type: "edit", seq: 3, path: "received_at", value: 5 }), { type: "edit", seq: 3, path: "received_at", value: 6 });
  assert.deepEqual(edited.sandboxes.integrity.edits, [{ seq: 3, path: "received_at", value: 6 }]);
  assert.deepEqual(reduce(edited, { type: "reset" }).sandboxes.integrity, { edits: [], tamper: 0, rechain: 0 });
});

test("the store notifies only on real changes", () => {
  const store = createStore(fresh());
  let calls = 0;
  store.subscribe(() => { calls += 1; });
  store.dispatch({ type: "seq", seq: 8 });
  store.dispatch({ type: "seq", seq: 8 });
  store.dispatch({ type: "claim", claim: "integrity" });
  assert.equal(calls, 2);
  assert.equal(store.getState().seq, 8);
});

test("memoize recomputes only when an argument changes by reference", () => {
  let runs = 0;
  const f = memoize((a, b) => { runs += 1; return a + b; });
  assert.equal(f(1, 2), 3);
  assert.equal(f(1, 2), 3);
  assert.equal(runs, 1);
  f(2, 2);
  assert.equal(runs, 2);
  const w1 = selectWalk(CONSOLE, "doorstep_scam", 8);
  assert.equal(selectWalk(CONSOLE, "doorstep_scam", 8), w1);
});

test("the walk selector reproduces the recorded doorstep run: crossing A at observation 4 (seq 8)", () => {
  const w = selectWalk(CONSOLE, "doorstep_scam", 8);
  assert.deepEqual(w.shown.map((s) => s.lambda_mnats), [1500, 1777, 3623, 6066]);
  assert.equal(w.walk.crossing, 4);
  assert.equal(w.crossed, true);
  assert.equal(selectWalk(CONSOLE, "doorstep_scam", 7).crossed, false);
  assert.equal(CONSOLE.spec.sprt.a_mnats, 4554);
  assert.equal(CONSOLE.spec.sprt.b_mnats, -2986);
});

test("provenance: badges are bracketed, escaped, and known; recorded files set the KMS flag", () => {
  assert.equal(badge("recorded", "2026-10-22T08:25:43Z"), '<span class="badge b-recorded" data-badge="recorded">[ RECORDED 2026-10-22T08:25:43Z ]</span>');
  assert.match(badges("computed", { synthetic: true }), /COMPUTED.*SYNTHETIC/s);
  assert.equal((badges("synthetic", { synthetic: true }).match(/SYNTHETIC/g) ?? []).length, 1);
  assert.throws(() => badge("trusted"));
  assert.equal(esc('<b a="1">&'), "&lt;b a=&quot;1&quot;&gt;&amp;");
  const p = deriveProvenance(CONSOLE, "http://127.0.0.1:8765");
  assert.deepEqual([p.kms, p.probes, p.hosting], ["recorded", "recorded", "local"]);
  assert.equal(deriveProvenance(CONSOLE, "https://d111111abcdef8.cloudfront.net").hosting, "cloudfront");
  assert.equal(deriveProvenance({ recorded: { bundle: null, probes: null } }, "").kms, "synthetic-only");
  assert.deepEqual(findStatusWords("the node is ONLINE and Healthy"), ["ONLINE", "HEALTHY"]);
  assert.deepEqual(findStatusWords("reactive inactive"), []);
});
