// The HMM port (§6.2): known answers here; exact equality with the Python references is asserted in tests/unit/test_console.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CONSOLE } from "../data/console_data.js";
import { ForwardFilter, lambdaTraceMnats, logsumexp, mnats, modelFromParams, novelFeatures, parseObservation, pySum, roundHalfEven, walkTrace } from "../hmm.js";
import { walkModel, explainGap, TERM_NAMES } from "../claims/walk.js";
import { initialState } from "../store.js";
import { deriveProvenance } from "../provenance.js";

const params = CONSOLE.spec.sprt_params;

test("rounding is half-to-even like Python's round(), and mnats rounds through it", () => {
  assert.deepEqual([0.5, 1.5, 2.5, 3.5, -1.5, -2.5, 2.4, 2.6].map(roundHalfEven), [0, 2, 2, 4, -2, -2, 2, 3]);
  assert.equal(roundHalfEven(-0.5) === 0, true);
  assert.equal(mnats(0.7), 700);
});

test("pySum compensates like CPython 3.12 (plain left-to-right addition would give 0 here)", () => {
  assert.equal(pySum([1e100, 1.0, -1e100]), 1.0, "Neumaier keeps the small term");
  assert.equal(1e100 + 1.0 + -1e100, 0, "plain addition loses it");
  assert.equal(pySum([]), 0);
  assert.equal(pySum([-Infinity, 1]), -Infinity);
});

test("logsumexp shifts by the max and handles -inf", () => {
  assert.equal(logsumexp([-Infinity, -Infinity]), -Infinity);
  assert.ok(Math.abs(logsumexp([Math.log(0.25), Math.log(0.75)])) < 1e-12);
  assert.ok(Math.abs(logsumexp([-Infinity, Math.log(2)]) - Math.log(2)) < 1e-12);
});

test("the model folds the extractor's noise into every rate and keeps zero transitions at -inf", () => {
  const m = modelFromParams(params);
  assert.equal(m.logTrans[1][0], -Infinity);
  assert.equal(m.logInit[4], -Infinity);
  assert.ok(Math.abs(m.h0[0] - (0.85 * 0.06 + 0.02 * 0.94)) < 1e-15);
  const saturated = { ...params, extractor: { tpr: Array(7).fill(1), fpr: Array(7).fill(0) }, emissions: params.emissions.map((r, i) => (i === 0 ? r.map(() => 1) : r)) };
  assert.throws(() => modelFromParams(saturated), /reached 0 or 1/);
});

test("the doorstep observations give the orrery's HMM trace; claims and unknown features behave", () => {
  const doorstep = CONSOLE.walk.doorstep_scam;
  assert.deepEqual(lambdaTraceMnats(doorstep.observations, params), doorstep.reference_trace_mnats);
  assert.deepEqual(lambdaTraceMnats(doorstep.observations, params).slice(0, 4), [800, 1077, 2923, 5366]);
  assert.deepEqual(lambdaTraceMnats([], params), []);
  assert.throws(() => lambdaTraceMnats([["bogus"]], params));
  assert.throws(() => parseObservation("authority", params.features));
  const f = new ForwardFilter(modelFromParams(params));
  assert.equal(f.value, 0);
});

test("walkTrace adds each cross-device term once, in milli-nats; the verified term is the only negative one", () => {
  const obs = [["authority"], ["pressure"]];
  const base = walkTrace(obs, params, []);
  const both = walkTrace(obs, params, ["door_unknown_overlap", "verified_official_callback"]);
  assert.equal(both.termMn, 700 - 3500);
  assert.deepEqual(both.lambda, base.hmm.map((v) => v - 2800));
  const negatives = Object.entries(params.cross_device_llr).filter(([, v]) => v < 0).map(([k]) => k);
  assert.deepEqual(negatives, ["verified_official_callback"]);
});

test("DC-2: a room claim keeps only the features not yet heard", () => {
  const earlier = [["authority"], { claim: ["secrecy"] }];
  assert.deepEqual(novelFeatures(["authority", "payment", "secrecy"], earlier), { novel: ["payment"], dropped: ["authority", "secrecy"] });
  assert.deepEqual(novelFeatures(["urgency"], []), { novel: ["urgency"], dropped: [] });
});

test("the presets: the browser HMM equals the reference and every gap is a sum of the spec's terms", () => {
  const provenance = deriveProvenance(CONSOLE, "");
  for (const preset of ["doorstep_scam", "grandparent_call", "benign_delivery"]) {
    const state = initialState({ N: 12, provenance });
    const model = walkModel(CONSOLE, { ...state.sandboxes.walk, preset }, 12);
    assert.equal(model.computed.parity, true, preset);
    assert.ok(model.computed.gaps.every((g) => g.terms !== null), preset);
  }
  assert.deepEqual(explainGap(1900, { a: 700, b: 1200, c: 3000 }), ["a", "b"]);
  assert.equal(explainGap(5, { a: 700 }), null);
  assert.deepEqual(TERM_NAMES, Object.keys(params.cross_device_llr));
});

test("the custom sandbox recomputes in the browser: authority, pressure, payment+urgency, secrecy+pressure crosses A", () => {
  const provenance = deriveProvenance(CONSOLE, "");
  const base = initialState({ N: 12, provenance });
  const sw = { ...base.sandboxes.walk, preset: "custom", observations: CONSOLE.walk.doorstep_scam.observations.slice(0, 4), terms: { ...base.sandboxes.walk.terms, door_unknown_overlap: true } };
  const model = walkModel(CONSOLE, sw, 12);
  assert.deepEqual(model.shown.map((s) => s.lambda_mnats), [1500, 1777, 3623, 6066]);
  assert.equal(model.walk.crossing, 4);
  const none = walkModel(CONSOLE, { ...sw, observations: [] }, 12);
  assert.equal(none.lambda, 0);
  assert.equal(none.walk.crossing, null);
});
