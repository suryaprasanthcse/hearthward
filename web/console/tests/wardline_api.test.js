// The Wardline API freeze (§2.2): every Wardline symbol the console uses is imported here, with a known answer.
// If Wardline changes, this test fails before the console does. The console never writes under web/wardline/.
import assert from "node:assert/strict";
import { test } from "node:test";
import * as chain from "../../wardline/chain.js";
import * as model from "../../wardline/model.js";
import { CONSOLE } from "../data/console_data.js";

const CHAIN_USED = ["ZERO_HEAD", "b64ToBytes", "bytesToHex", "jcsText", "jcs", "sha256", "taggedDigest", "ledgerHead", "verifySignature", "replayChain"];
const MODEL_USED = ["PLANE", "LAMBDA", "formatMnats", "formatInt", "lrLabel", "shortHash", "hashTail", "yOfLambda", "fmtClock", "lateness", "pad2",
  "scenarioOf", "beliefSeqs", "asOfIndex", "planeGeometry", "planeView", "chainLayout", "tileBits", "teethOpen", "humanFeature", "stateAt"];

test("every Wardline symbol the console imports still exists", () => {
  for (const name of CHAIN_USED) assert.ok(name in chain, `chain.js lost ${name}`);
  for (const name of MODEL_USED) assert.ok(name in model, `model.js lost ${name}`);
});

test("their behaviour is frozen at known answers", async () => {
  assert.equal(chain.ZERO_HEAD, `sha256:${"0".repeat(64)}`);
  assert.equal(new TextDecoder().decode(chain.jcs({ b: 1, a: [true, null, "x"] })), '{"a":[true,null,"x"],"b":1}');
  assert.throws(() => chain.jcs({ a: 1.5 }));
  assert.equal(chain.bytesToHex(Uint8Array.of(0, 255, 16)), "00ff10");
  assert.equal(await chain.taggedDigest("hw-decision-v1", new Uint8Array(0)),
    "sha256:" + chain.bytesToHex(await chain.sha256(new TextEncoder().encode("hw-decision-v1"), Uint8Array.of(0))));
  assert.equal(model.yOfLambda(9000), 120);
  assert.equal(model.yOfLambda(-4000), 860);
  assert.equal(model.formatMnats(6066), "+6,066");
  assert.equal(model.formatMnats(-2986), "−2,986");
  assert.equal(model.formatMnats(0), "0");
  assert.equal(model.lrLabel(6066).replace(/\s+/g, " "), "431 : 1");
  assert.equal(model.tileBits("sha256:" + "ff".repeat(32)).flat().every(Boolean), true);
  assert.deepEqual(model.chainLayout(12).tiles.length, 12);
  assert.deepEqual(model.PLANE, { x0: 160, x1: 1520, top: 120, bottom: 840 });
});

test("scenarioOf and planeView still read the console's ward data", () => {
  const sc = model.scenarioOf(CONSOLE.ward, "late_and_lossy");
  assert.equal(sc.envelopes.length, 12);
  const view = model.planeView(sc, sc.decision.at_seq);
  assert.equal(view.R, 13);
  assert.equal(view.beams.length, 12);
  assert.equal(view.stake.opacity, 1);
});
