// node --test web/wardline/tests. The director: timeline, purity, keys, reduced motion.
import assert from "node:assert/strict";
import { before, test } from "node:test";

import { WARD } from "../data.js";
import { precompute } from "../chain.js";
import { ACTS, DURATION, buildTimeline, frameAt, keyAction, rowWeight, scheduleRows, seekCue } from "../director.js";

let tl;
let crypto;
before(async () => {
  tl = buildTimeline(WARD);
  crypto = await precompute(WARD);
});

const sampleTimes = () => Array.from({ length: 200 }, (_, i) => Math.round((i * DURATION) / 199));

test("the duration is 90,000 ms and the acts start at 0, 35,000 and 65,000", () => {
  assert.equal(DURATION, 90000);
  assert.deepEqual(ACTS.map((a) => a.start), [0, 35000, 65000]);
  assert.equal(tl.duration, 90000);
});

test("every cue lies inside its window and each window's durations sum exactly to it", () => {
  const groups = [
    [tl.segs.A.cues, tl.windows.A], [tl.segs.B.cues, tl.windows.B], [tl.lateCues, tl.windows.C], [tl.tailCues, tl.windows.D],
  ];
  for (const [cues, [a, b]] of groups) {
    assert.equal(cues[0].t0, a);
    assert.equal(cues.at(-1).t1, b);
    let sum = 0;
    for (const c of cues) {
      assert.ok(c.t0 >= a && c.t1 <= b && c.t1 >= c.t0);
      sum += c.t1 - c.t0;
    }
    assert.equal(sum, b - a);
    for (let i = 1; i < cues.length; i += 1) assert.equal(cues[i].t0, cues[i - 1].t1);
  }
  assert.ok(tl.cueStarts.every((t, i, all) => i === 0 || t > all[i - 1]));
});

test("rows are found from the data, never hard-coded", () => {
  const door = WARD.scenarios.doorstep_scam.rows;
  assert.equal(door[tl.segs.A.to].event, "PROOF_REQ");
  assert.equal(WARD.scenarios.verified_plumber.rows[tl.segs.B.to].event, "DISMISS");
  assert.equal(WARD.scenarios.late_and_lossy.rows[tl.lateCues.at(-1).i].event, "INGEST+SYNC");
  assert.equal(WARD.scenarios.late_and_lossy.rows[tl.tailCues[0].i].event, "VISIT_CLOSED");
  assert.equal(tl.atSeq, 7);
  assert.equal(tl.R, 13);
  assert.equal(tl.k, 5);
});

test("row weights follow §10.2", () => {
  const rows = WARD.scenarios.late_and_lossy.rows;
  const weight = (event) => rowWeight(rows.find((r) => r.event === event));
  assert.equal(weight("DUPLICATE"), 2.0);
  assert.equal(weight("INTERVENE"), 2.6);
  assert.equal(weight("PROOF_REQ"), 4.0);
  assert.equal(weight("INGEST@5"), 3.2);
  assert.equal(weight("INGEST+SYNC"), 3.2);
  assert.equal(weight("VISIT_CLOSED"), 1.2);
  assert.equal(weight("AS_OF"), 1.8);
  assert.equal(weight("END"), 1.0);
  assert.equal(rowWeight(rows[0]), 0.6);
  const quoted = rows.find((r) => r.label.startsWith('"'));
  assert.equal(rowWeight(quoted), 1.6 + 0.12 * quoted.label.split(/\s+/).length);
  assert.equal(scheduleRows(rows, 0, 3, 100, 1000).at(-1).t1, 1000);
});

test("frameAt is pure: 200 sampled times give deep-equal, stable frames", () => {
  for (const t of sampleTimes()) {
    for (const motion of [true, false]) {
      const a = frameAt(tl, WARD, crypto, t, { motion });
      const b = frameAt(tl, WARD, crypto, t, { motion });
      assert.deepEqual(a, b);
      assert.equal(JSON.stringify(a), JSON.stringify(b));
      assert.deepEqual(JSON.parse(JSON.stringify(a)), a);
    }
  }
});

test("the stills of §18 contain what they should", () => {
  const f = (s) => frameAt(tl, WARD, crypto, s * 1000);
  assert.equal(f(3).act1.readout.lambda, "0");
  assert.ok(f(3).act1.point.alpha > 0.9);
  assert.ok(f(18).act1.aLit === 1 && f(18).act1.readout.state === "INTERVENE");
  assert.equal(f(22).act1.bounce.stamp.lines.join(" ").includes("HX-01 Open-episode lock"), true);
  assert.ok(f(22).act1.bounce.stamp.alpha === 1);
  assert.equal(f(30).act1.scenario, "verified_plumber");
  assert.equal(f(33).act1.bState, "hold");
  assert.equal(f(34).act1.bState, "dismiss");
  assert.equal(f(44).active, 2);
  assert.ok(f(61).act2.S <= 7.0001 && f(61).act2.side.seq === 7);
  assert.equal(f(61).act2.side.state, "INTERVENE");
  assert.deepEqual(f(70).act3.checks.map((c) => c.ok), [true, true, true, true, true]);
  assert.deepEqual(f(75).act3.checks.map((c) => c.ok), [true, true, true, false, null]);
  assert.deepEqual(f(80).act3.checks.map((c) => c.ok), [true, true, true, true, false]);
  assert.deepEqual(f(84).act3.checks.map((c) => c.ok), [true, true, true, true, true]);
  assert.equal(f(88).opacity.card, 1);
  const tampered = f(75).act3.teeth.find((t) => t.gap === tl.k);
  assert.ok(tampered.open.every(Boolean));
  assert.equal(f(80).act3.tiles[WARD.signed.intervene_at_seq - 1].ring, "fail");
  assert.equal(f(70).act3.teeth.every((t) => t.open.every((o) => !o)), true);
});

test("the keyAction table of §10.4 is honoured", () => {
  assert.deepEqual(keyAction(" "), { type: "toggle", preventDefault: true });
  assert.deepEqual(keyAction("1"), { type: "seek", to: 0, keepPlay: true });
  assert.deepEqual(keyAction("2"), { type: "seek", to: 35000, keepPlay: true });
  assert.deepEqual(keyAction("3"), { type: "seek", to: 65000, keepPlay: true });
  assert.deepEqual(keyAction("ArrowRight"), { type: "cue", dir: 1, preventDefault: true });
  assert.deepEqual(keyAction("ArrowLeft"), { type: "cue", dir: -1, preventDefault: true });
  assert.deepEqual(keyAction("ArrowRight", { shiftKey: true }), { type: "nudge", ms: 1000, preventDefault: true });
  assert.deepEqual(keyAction("ArrowLeft", { shiftKey: true }), { type: "nudge", ms: -1000, preventDefault: true });
  const plain = { r: "reset", v: "verify", t: "tamper", h: "hints", m: "motion", d: "debug", f: "fullscreen" };
  for (const [key, type] of Object.entries(plain)) {
    assert.equal(keyAction(key).type, type);
    assert.equal(keyAction(key.toUpperCase()).type, type);
    assert.equal(keyAction(key).preventDefault, undefined);
  }
  for (const mod of ["ctrlKey", "metaKey", "altKey"]) assert.equal(keyAction("r", { [mod]: true }), null);
  assert.equal(keyAction("r", { repeat: true }), null);
  assert.equal(keyAction(" ", { repeat: true }), null);
  assert.equal(keyAction("ArrowRight", { repeat: true }).type, "cue");
  assert.equal(keyAction("x"), null);
});

test("seekCue moves to the previous or next cue start", () => {
  assert.equal(seekCue(tl, 0, 1), 1600);
  assert.equal(seekCue(tl, 1600, -1), 0);
  assert.ok(tl.cueStarts.includes(seekCue(tl, 30000, 1)));
  assert.ok(seekCue(tl, 30000, -1) < 30000);
  assert.equal(seekCue(tl, DURATION, 1), DURATION);
});

test("reduced motion produces no wave and no shock, and the timeline is unchanged", () => {
  for (const t of sampleTimes()) {
    const f = frameAt(tl, WARD, crypto, t, { motion: false });
    assert.equal(f.glow.waveA[3], 0);
    assert.equal(f.glow.waveB[3], 0);
    assert.equal(f.glow.cross[2], -1);
    assert.equal(f.act1.membrane, false);
  }
  const on = frameAt(tl, WARD, crypto, 23000, { motion: true });
  assert.equal(on.glow.waveA[3], 1);
  const crossing = frameAt(tl, WARD, crypto, tl.segs.A.cues.find((c) => c.kind === "intervene").t0 + 100, { motion: true });
  assert.ok(crossing.glow.cross[2] >= 0);
  for (const t of [1000, 18000, 40000, 70000]) {
    assert.equal(frameAt(tl, WARD, crypto, t, { motion: false }).sentence, frameAt(tl, WARD, crypto, t, { motion: true }).sentence);
  }
});

test("every sentence on the timeline is at most 120 characters", () => {
  assert.ok(tl.sentences.length > 40);
  for (const s of tl.sentences) assert.ok(s.text.length <= 120, s.text);
  assert.ok(tl.sentences.every((s, i, all) => i === 0 || s.t0 >= all[i - 1].t0));
});

test("a manual verify or tamper overrides the chain view of Act III", () => {
  const manual = { kind: "tamper", view: "tampered", from: "intact", elapsed: 3000 };
  const f = frameAt(tl, WARD, crypto, 70000, { manual });
  assert.deepEqual(f.act3.checks.map((c) => c.ok), [true, true, true, false, null]);
  assert.match(f.sentence, /One millisecond changed in seq 5/);
});
