// node --test web/wardline/tests. The pure model: mappings, formatters, state as of a row, sentences, geometry.
import assert from "node:assert/strict";
import { test } from "node:test";

import { WARD } from "../data.js";
import {
  FIELD, GHOST, LAMBDA, OMEGA, PLANE, STAGE, asOfIndex, beliefSeqs, chainLayout, easeInOutCubic, easeOutCubic,
  formatMnats, fmtClock, headWords, humanFeature, joinList, lrLabel, planeGeometry, planeView, scenarioOf,
  sentenceFor, shortHash, spring, springLeft, stateAt, teethOpen, tileBits, walkPoints, wave, xOfAt, yOfLambda,
} from "../model.js";

const near = (a, b, eps = 0.01) => assert.ok(Math.abs(a - b) <= eps, `${a} is not within ${eps} of ${b}`);
const names = Object.keys(WARD.scenarios);

test("the Act I field mapping of §7.1", () => {
  assert.deepEqual([FIELD.walkX0, FIELD.walkX1, FIELD.wardX0, FIELD.wardX1, FIELD.top, FIELD.bottom], [560, 1760, 560, 1824, 120, 860]);
  assert.deepEqual([LAMBDA.max, LAMBDA.min], [9000, -4000]);
  near(yOfLambda(WARD.sprt.a_mnats), 373.08);
  near(yOfLambda(WARD.sprt.b_mnats), 802.28);
  near(yOfLambda(0), 632.31);
  near(yOfLambda(8256), 162.35);
  near(yOfLambda(-3552), 834.5);
  near(yOfLambda(99999), 120);
  near(yOfLambda(-99999), 860);
  assert.equal(xOfAt(100, 100, 300), 560);
  assert.equal(xOfAt(300, 100, 300), 1760);
  assert.equal(xOfAt(200, 100, 300), 1160);
});

test("lrLabel, formatMnats and the other formatters", () => {
  assert.equal(lrLabel(4554), "95 : 1");
  assert.equal(lrLabel(-2986), "1 : 20");
  assert.equal(lrLabel(0), "1 : 1");
  assert.equal(lrLabel(8256), "3,851 : 1");
  assert.equal(lrLabel(6066), "431 : 1");
  assert.equal(formatMnats(6066), "+6,066");
  assert.equal(formatMnats(-2986), "−2,986");
  assert.equal(formatMnats(0), "0");
  assert.equal(fmtClock(-1200), "−00:01.200");
  assert.equal(fmtClock(20000), "+00:20.000");
  assert.equal(shortHash(`sha256:9f2c${"0".repeat(56)}a71e`), "sha256:9f2c…a71e");
  assert.equal(humanFeature("remote_access"), "remote access");
  assert.equal(humanFeature("info_request"), "information request");
  assert.equal(joinList(["a", "b", "c"]), "a, b and c");
  assert.equal(joinList(["a", "b"]), "a and b");
  assert.equal(WARD.sprt.a_mnats, 4554);
  assert.equal(WARD.sprt.b_mnats, -2986);
});

test("the spring is critically damped: monotone, no overshoot, settles", () => {
  near(OMEGA, 13.0384, 1e-4);
  let prev = 1;
  for (let tau = 0; tau < 1; tau += 0.01) {
    const left = springLeft(tau);
    assert.ok(left <= prev + 1e-12 && left >= 0);
    prev = left;
  }
  assert.ok(springLeft(0.6) < 0.01);
  near(spring(0, 100, 0), 0, 1e-9);
  near(spring(0, 100, 10), 100, 1e-6);
  near(easeOutCubic(0.5), 0.875, 1e-9);
  near(easeInOutCubic(0.5), 0.5, 1e-9);
});

test("wave is zero before impact, when disabled, and far from the impact; it decays", () => {
  const w = [1000, 2, 14, 1];
  assert.equal(wave(w, 1000, 1.9), 0);
  assert.equal(wave([1000, 2, 14, 0], 1000, 2.1), 0);
  assert.ok(Math.abs(wave(w, 1000, 2.0)) > 13.9);
  assert.ok(Math.abs(wave(w, 1500, 2.1)) < 1e-6);
  assert.ok(Math.abs(wave(w, 1000, 3.5)) < 0.3);
});

test("posterior_ppm sums to 1,000,000 wherever it is non-null", () => {
  let seen = 0;
  for (const name of names) {
    for (const row of WARD.scenarios[name].rows) {
      if (row.posterior_ppm === null) continue;
      seen += 1;
      assert.equal(row.posterior_ppm.length, 5);
      assert.equal(row.posterior_ppm.reduce((a, b) => a + b, 0), 1_000_000, `${name} ${row.event}`);
    }
  }
  assert.ok(seen > 20);
});

test("BT-3: state as of row i reads only rows 0..i", () => {
  for (const name of names) {
    const sc = scenarioOf(WARD, name);
    for (let i = -1; i < sc.rows.length; i += 1) {
      const cut = { ...sc, rows: sc.rows.slice(0, i + 1) };
      assert.deepEqual(stateAt(cut, i), stateAt(sc, i), `${name} row ${i}`);
    }
  }
});

test("the state machine follows INTERVENE, HOLD, DISMISS", () => {
  const door = scenarioOf(WARD, "doorstep_scam");
  const iv = door.rows.findIndex((r) => r.event === "INTERVENE");
  assert.equal(stateAt(door, iv - 1).state, "OBSERVE");
  assert.equal(stateAt(door, iv).state, "INTERVENE");
  assert.equal(stateAt(door, iv).lambdaMnats, 6066);
  const plumber = scenarioOf(WARD, "verified_plumber");
  assert.equal(stateAt(plumber, plumber.rows.findIndex((r) => r.event === "HOLD")).state, "HOLD");
  assert.equal(stateAt(plumber, plumber.rows.findIndex((r) => r.event === "DISMISS")).state, "DISMISSED");
});

test("walkPoints is the step polyline of §7.1 and stays under 64 vertices", () => {
  const door = scenarioOf(WARD, "doorstep_scam");
  const last = door.rows.findIndex((r) => r.event === "PROOF_REQ");
  const pts = walkPoints(door, 0, last);
  assert.equal(pts.length, 1 + 2 * last);
  assert.ok(pts.length <= 64);
  near(pts[0].x, 560);
  near(pts[0].y, yOfLambda(0));
  near(pts.at(-1).x, 1760);
  near(pts.at(-1).y, yOfLambda(8256));
  for (let i = 1; i < pts.length; i += 2) assert.equal(pts[i].y, pts[i - 1].y, "horizontal then vertical");
});

test("sentenceFor is at most 120 characters for every row and every scripted context", () => {
  const scripted = [
    { type: "act1_intro" }, { type: "counterpoint" }, { type: "act2_intro" }, { type: "rewind", atSeq: 7 },
    { type: "act3_intro", n: 12 }, { type: "verify", atSeq: 8 }, { type: "tamper", k: 5 }, { type: "rechain", atSeq: 8 },
    { type: "restore" },
  ];
  for (const ctx of scripted) assert.ok(sentenceFor(ctx).length <= 120, ctx.type);
  for (const name of names) {
    const sc = scenarioOf(WARD, name);
    let seenDoor = false;
    sc.rows.forEach((row) => {
      const envelope = sc.envelopes.find((e) => e.seq === row.seq);
      const firstDoor = row.lane === "door" && !seenDoor;
      if (row.lane === "door") seenDoor = true;
      const text = sentenceFor({ type: "row", row, envelope, firstDoor });
      assert.ok(text.length > 10 && text.length <= 120, `${name} ${row.event}: ${text}`);
    });
  }
  const late = scenarioOf(WARD, "late_and_lossy");
  const retry = late.rows.find((r) => r.event === "INGEST@5");
  assert.match(sentenceFor({ type: "row", row: retry, envelope: late.envelopes.find((e) => e.seq === retry.seq) }), /arrived 2 min 34 s late, on attempt 5/);
  assert.match(sentenceFor({ type: "row", row: late.rows.find((r) => r.event === "AS_OF") }), /^Belief at seq 7: v2, 28 s\. Belief now: v5, 79 s\./);
  assert.throws(() => sentenceFor({ type: "nope" }));
});

test("beliefSeqs and asOfIndex put the decision row at belief 7 (late_and_lossy)", () => {
  const late = scenarioOf(WARD, "late_and_lossy");
  const at7 = asOfIndex(late, 7);
  assert.equal(late.rows[at7].event, "INTERVENE");
  assert.equal(stateAt(late, at7).lambdaMnats, 6762);
  assert.equal(beliefSeqs(late).at(-1), 13);
  assert.equal(asOfIndex(late, 0), -1);
});

test("planeGeometry matches §8.1 for late_and_lossy", () => {
  const late = scenarioOf(WARD, "late_and_lossy");
  const g = planeGeometry(late);
  assert.equal(g.R, 13);
  near(g.h, 720 / 13, 1e-9);
  near(g.yc(1), 840 - 0.5 * g.h, 1e-9);
  assert.equal(g.V0, Math.min(...late.envelopes.map((e) => e.valid.lo - late.epochMs)) - 4000);
  near(g.xRaw(g.V0), 160, 1e-9);
  near(g.xRaw(g.V1), 1520, 1e-9);
  assert.equal(PLANE.x1 - PLANE.x0, 1360);
});

test("planeView: late arrival, duplicate, watermarks, stake and ghosting", () => {
  const late = scenarioOf(WARD, "late_and_lossy");
  const dupIndex = late.rows.findIndex((r) => r.event === "DUPLICATE");
  const v = planeView(late, 7, { dup: { rowIndex: dupIndex, k: 0.5 } });
  assert.equal(v.B, 7);
  assert.equal(v.beams.length, 12);
  assert.ok(v.beams.filter((b) => b.seq <= 7).every((b) => b.opacity === 1));
  assert.ok(v.beams.filter((b) => b.seq > 7).every((b) => b.opacity === GHOST));
  const retry = v.beams.find((b) => b.seq === 11);
  assert.equal(retry.arrival.clipped, true);
  assert.equal(retry.arrival.label, "arrived +2:36.742 · attempt 5 · 2 min 34 s late");
  assert.equal(v.beams.find((b) => b.seq === 1).trust, "observed");
  assert.equal(v.beams.find((b) => b.seq === 7).trust, "claimed");
  assert.equal(v.stake.label, `DECISION · INTERVENE · at_seq 7 · Λ +6,762 mn · head ▦ ${late.decision.ledger_head.slice(7, 11)}…${late.decision.ledger_head.slice(-4)}`);
  near(v.stake.y, 840 - 7 * v.h, 1e-9);
  assert.equal(v.callouts.length, 3);
  assert.equal(v.duplicate.opacity, 0.5);
  assert.match(v.overlap.label, /door_unknown_overlap \+700 mn$/);
  const door = v.frontiers.find((f) => f.lane === "door");
  assert.match(door.chip.text, /^← W_ring −72\.\d min$/);
  assert.ok(v.hatch && v.hatch.label === "may still arrive");
  assert.equal(v.versions.find((x) => x.n === 2).current, true);
  const end = planeView(late, 13, { flush: 1 });
  assert.equal(end.hatch, null);
  assert.match(end.flushLabel, /end of stream/);
  assert.equal(end.versions.find((x) => x.n === 5).fadeTop, true);
});

test("chainLayout, tileBits and the teeth follow §9.1", () => {
  const lay = chainLayout(12);
  assert.equal(lay.x0, 268);
  assert.equal(lay.pitch, 120);
  assert.equal(lay.tiles[11].x, 268 + 11 * 120);
  assert.equal(chainLayout(20).tile < 64, true);
  const head = `sha256:80${"00".repeat(30)}01`;
  const bits = tileBits(head);
  assert.equal(bits.length, 16);
  assert.equal(bits[0][0], true);
  assert.equal(bits[0][1], false);
  assert.equal(bits[15][15], true);
  assert.equal(bits.flat().filter(Boolean).length, 2);
  assert.equal(headWords(head)[0], 0x80000000);
  assert.deepEqual(teethOpen(head, head), Array(8).fill(false));
  assert.deepEqual(teethOpen(head, `sha256:80${"00".repeat(30)}02`), [...Array(7).fill(false), true]);
  assert.equal(STAGE.w * 9, STAGE.h * 16);
});
