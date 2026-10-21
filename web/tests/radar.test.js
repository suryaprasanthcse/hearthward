// node --test: the radar page's pure logic and the integrity of the synthetic scenario it replays.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";

import { replayLedger, verifyBundle } from "../src/attest.js";
import * as L from "../src/radar_logic.js";
import { SCENARIO } from "../src/radar_data.js";

const BOUNDS = SCENARIO.thresholds_mnats;
const ENV = SCENARIO.envelopes;
const STEPS = SCENARIO.steps;

// ── gaze ────────────────────────────────────────────────────────────────────────────────────────────────

test("the pupil never leaves the eye, however far the cursor goes", () => {
  const eye = { x: 500, y: 200 };
  for (const mouse of [{ x: 501, y: 200 }, { x: 900, y: 200 }, { x: 5000, y: -9000 }, { x: -3000, y: 4000 }, { x: 500, y: 200 }]) {
    const g = L.gaze(eye, mouse, 15, 420);
    assert.ok(Math.hypot(g.dx, g.dy) <= 15 + 1e-9, JSON.stringify(mouse));
    assert.ok(Number.isFinite(g.dx) && Number.isFinite(g.dy) && Number.isFinite(g.angleDeg));
  }
});

test("the pupil points where the cursor is, with the same angle as the vector from the eye", () => {
  const eye = { x: 300, y: 300 };
  const right = L.gaze(eye, { x: 700, y: 300 }, 15);
  const below = L.gaze(eye, { x: 300, y: 700 }, 15);
  const left = L.gaze(eye, { x: -100, y: 300 }, 15);
  const above = L.gaze(eye, { x: 300, y: -100 }, 15);
  assert.ok(right.dx > 14 && Math.abs(right.dy) < 1e-9 && right.angleDeg === 0);
  assert.ok(below.dy > 14 && Math.abs(below.dx) < 1e-9 && Math.abs(below.angleDeg - 90) < 1e-9);
  assert.ok(left.dx < -14 && Math.abs(left.angleDeg - 180) < 1e-9);
  assert.ok(above.dy < -14 && Math.abs(above.angleDeg - 270) < 1e-9);
  const diagonal = L.gaze(eye, { x: 400, y: 400 }, 15, 400);
  assert.ok(Math.abs(diagonal.angleDeg - 45) < 1e-9 && Math.abs(diagonal.dx - diagonal.dy) < 1e-9);
});

test("the offset grows with distance up to the bound, and reports the focus coordinates and range", () => {
  const eye = { x: 0, y: 0 };
  const near = L.gaze(eye, { x: 40, y: 0 }, 15, 400);
  const far = L.gaze(eye, { x: 200, y: 0 }, 15, 400);
  assert.ok(near.offset < far.offset && far.offset < 15 && L.gaze(eye, { x: 400, y: 0 }, 15, 400).offset === 15);
  const g = L.gaze({ x: 10, y: 10 }, { x: 123.6, y: 456.4 }, 15);
  assert.equal(g.focusX, 124);
  assert.equal(g.focusY, 456);
  assert.ok(Math.abs(g.distance - Math.hypot(113.6, 446.4)) < 1e-9);
});

// ── evidence ────────────────────────────────────────────────────────────────────────────────────────────

test("Wald's bounds are the blueprint's, in integer milli-nats", () => {
  assert.deepEqual(BOUNDS, { a: 4554, b: -2986 });
  assert.equal(Math.round(Math.log(95) * 1000), BOUNDS.a);
  assert.equal(Math.round(Math.log(0.05 / 0.99) * 1000), BOUNDS.b);
});

test("the verdict flips exactly at A and B", () => {
  assert.equal(L.verdict(4553, BOUNDS), "CONTINUE");
  assert.equal(L.verdict(4554, BOUNDS), "INTERVENE");
  assert.equal(L.verdict(-2985, BOUNDS), "CONTINUE");
  assert.equal(L.verdict(-2986, BOUNDS), "DISMISS-ELIGIBLE");
  assert.equal(L.verdict(0, BOUNDS), "CONTINUE");
});

test("only an upward crossing of A is a shockwave", () => {
  assert.equal(L.crossedA(4553, 4554, BOUNDS), true);
  assert.equal(L.crossedA(3623, 6066, BOUNDS), true);
  assert.equal(L.crossedA(4554, 6066, BOUNDS), false);
  assert.equal(L.crossedA(6066, 3623, BOUNDS), false);
  assert.equal(L.crossedA(-3000, 100, BOUNDS), false);
});

test("the gauge clamps and its arc runs 270 degrees from 135", () => {
  const scale = { min: -4000, max: 7000 };
  assert.equal(L.gaugeFraction(-9999, scale), 0);
  assert.equal(L.gaugeFraction(99999, scale), 1);
  assert.ok(Math.abs(L.gaugeFraction(1500, scale) - 0.5) < 1e-12);
  const start = L.arcPoint(0, 0, 10, 0);
  const end = L.arcPoint(0, 0, 10, 1);
  assert.ok(Math.abs(start.x - 10 * Math.cos((135 * Math.PI) / 180)) < 1e-9 && Math.abs(end.y - 10 * Math.sin((405 * Math.PI) / 180)) < 1e-9);
});

test("the waveform is made of the real deltas: flat without them, deterministic, longer with more events", () => {
  assert.ok(L.deltaWaveform(STEPS, 3, 100).every((v) => v === 0));  // seqs 1..3 carry no evidence
  const a = L.deltaWaveform(STEPS, 8, 100);
  assert.deepEqual(a, L.deltaWaveform(STEPS, 8, 100));
  assert.equal(a.length, 100);
  assert.ok(a.some((v) => v !== 0));
  assert.ok(L.deltaWaveform(STEPS, 12, 100).some((v, i) => v !== a[i]));
});

// ── the scenario and its belief state ──────────────────────────────────────────────────────────────────

test("the replayed scenario's evidence crosses A at the recorded sequence", () => {
  const at = SCENARIO.intervene_at_seq;
  assert.ok(L.lambdaAt(STEPS, at - 1) < BOUNDS.a && L.lambdaAt(STEPS, at) >= BOUNDS.a);
  assert.equal(L.lambdaAt(STEPS, 0), 0);
  assert.equal(L.lambdaAt(STEPS, 999), STEPS.at(-1).lambda_mnats);
  assert.ok(STEPS.every((s) => Number.isInteger(s.lambda_mnats) && Number.isInteger(s.delta_mnats)));
});

test("the scenario's ledger replays from the genesis head, and any tampering is found", async () => {
  assert.deepEqual(await replayLedger(ENV), []);
  for (let n = 1; n <= ENV.length; n += 1) assert.deepEqual(await replayLedger(L.visibleAt(ENV, n)), []);
  const tampered = structuredClone(ENV);
  tampered[4].received_at += 1;
  assert.notDeepEqual(await replayLedger(tampered), []);
  assert.ok(ENV.every((e) => e.auth.synthetic === true));
});

test("the attestation in the scenario verifies in the browser's own check", async () => {
  const result = await verifyBundle({ ...SCENARIO.attestation, ledger: ENV });
  assert.equal(result.ok, true, JSON.stringify(result.checks.filter((c) => !c.ok)));
  assert.equal(result.record.lambda_mnats, L.lambdaAt(STEPS, result.record.at_seq));
  assert.equal(result.record.ledger_head, ENV[result.record.at_seq - 1].head);
  assert.deepEqual(result.record.thresholds_mnats, BOUNDS);
  const broken = structuredClone(SCENARIO.attestation);
  broken.decision.sig_b64 = broken.decision.sig_b64.slice(0, -4) + "AAAA";
  assert.equal((await verifyBundle({ ...broken, ledger: ENV })).ok, false);
});

test("belief visibility follows the ledger sequence", () => {
  assert.equal(L.visibleAt(ENV, 0).length, 0);
  assert.equal(L.visibleAt(ENV, 5).length, 5);
  assert.equal(L.visibleAt(ENV, 99).length, ENV.length);
  assert.ok(L.visibleAt(ENV, 5).every((e) => e.seq <= 5));
});

test("watermarks are the latest basis minus the lateness, per source, and absent before a source speaks", () => {
  const late = SCENARIO.temporal.lateness_ms;
  assert.deepEqual(L.watermarks(ENV, 0, late), { ring: null, bee: null, alexa: null });
  const w3 = L.watermarks(ENV, 3, late);
  assert.equal(w3.alexa, null);
  assert.equal(w3.ring, Math.max(...ENV.filter((e) => e.source === "ring" && e.seq <= 3).map((e) => e.src_time)) - late.ring);
  assert.equal(w3.bee, ENV[2].received_at - late.bee);
  const full = L.watermarks(ENV, ENV.length, late);
  assert.equal(full.alexa, Math.max(...ENV.filter((e) => e.source === "alexa").map((e) => e.received_at)) - late.alexa);
  assert.equal(late.ring, 4356000);
  const earlier = L.watermarks(ENV, 6, late);
  for (const k of ["ring", "bee", "alexa"]) assert.ok(full[k] === null || earlier[k] === null || full[k] >= earlier[k], "watermarks never move back");
});

test("a time maps to a position on the plot, and an off-plot time says so", () => {
  const axis = L.timeAxis(ENV);
  assert.ok(axis.max > axis.min);
  assert.deepEqual(L.xFor(axis.min, axis, 100, 800), { x: 100, clamped: false, side: null });
  assert.equal(L.xFor(axis.max, axis, 100, 800).x, 900);
  const off = L.xFor(axis.min - 4356000, axis, 100, 800);
  assert.deepEqual(off, { x: 100, clamped: true, side: "left" });
});

test("the ladder starts L1 and L2 at the intervene sequence and defers L3", () => {
  const at = SCENARIO.intervene_at_seq;
  assert.deepEqual(L.ladderState(at - 1, at), { l1: "STANDBY", l2: "STANDBY", l3: "DEFERRED", active: false });
  assert.deepEqual(L.ladderState(at, at), { l1: "SENT", l2: "ACTIVE", l3: "DEFERRED", active: true });
});

test("the tier cascade is a chain of real SHA-256 digests over the previous one and the tier", async () => {
  const head = ENV[4].head;
  const lines = await L.tierCascade("TRUST CAUSALIS", head, 6);
  assert.equal(lines.length, 6);
  let previous = head;
  for (const [i, line] of lines.entries()) {
    previous = createHash("sha256").update(`${previous}|TRUST CAUSALIS|${i}`).digest("hex");
    assert.equal(line, previous);
    assert.match(line, /^[0-9a-f]{64}$/);
  }
  assert.notDeepEqual(lines, await L.tierCascade("ORDO CAUSALIS", head, 6));
  assert.deepEqual(lines, await L.tierCascade("TRUST CAUSALIS", head, 6));
});

test("the clock helper formats offsets", () => {
  assert.equal(L.clock(0), "+00:00.0");
  assert.equal(L.clock(75865), "+01:15.9");
  assert.equal(L.clock(-5), "+00:00.0");
});
