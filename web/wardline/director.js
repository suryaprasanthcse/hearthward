// Wardline's director: time -> frame. Pure (no DOM, no Date, no Math.random): the same inputs always give a
// deep-equal, JSON-serializable frame. It reveals the real crypto results of chain.precompute() on a schedule.
import { parseRecord } from "./chain.js";
import {
  FIELD, PLANE, SPEAKER, asOfIndex, beliefSeqs, chainLayout, chipWidth, clamp, clamp01, clock90, easeInOutCubic,
  easeInQuad, easeOutCubic, formatInt, formatMnats, humanFeature, isUtterance, lerp, lrLabel, planeView,
  scenarioOf, sentenceFor, shortHash, springLeft, stateAt, teethOpen, walkPoints, xOfAt, yOfLambda,
} from "./model.js";

export const DURATION = 90000;
export const ACTS = Object.freeze([{ n: 1, start: 0 }, { n: 2, start: 35000 }, { n: 3, start: 65000 }]);
const WINDOWS = {
  intro: [0, 1600], A: [1600, 23000], holdA: [23000, 26000], cross1: [26000, 26600], B: [26600, 34400], holdB: [34400, 35000],
  fade2: [35000, 36200], C: [36200, 54000], D: [54000, 58000], rewind: [58000, 62000], holdC: [62000, 65000],
  fade3: [65000, 66200], verify: [66200, 72000], tamper: [72000, 77000], rechain: [77000, 82500], restore: [82500, 85500],
  card: [85500, 90000],
};
const CHIP_Y = 880;
const FLIGHT_MS = 420;
const STAGGER_MS = 90;

// ── row scheduling (§10.2) ──
export function rowWeight(row) {
  if (/^INGEST@\d+$/.test(row.event) || row.event === "INGEST+SYNC") return 3.2;
  switch (row.event) {
    case "INTERVENE": return 2.6;
    case "PROOF_REQ": return 4.0;
    case "HOLD": case "DISMISS": case "INCONCLUSIVE": return 2.4;
    case "DUPLICATE": case "QUARANTINE": case "REJECTED": return 2.0;
    case "VISIT_CLOSED": return 1.2;
    case "AS_OF": return 1.8;
    case "END": return 1.0;
    default: break;
  }
  if (row.lane === "door") return 0.6;
  if (row.lane === "wrist") return isUtterance(row) ? 1.6 + 0.12 * row.label.split(/\s+/).length : 0.5;
  if (row.lane === "room") return 2.0;
  return 3.0;
}

// Each row lasts floor(window * w / sum(w)) ms; the remainder goes to the last row.
export function scheduleRows(rows, from, to, t0, t1) {
  const idx = Array.from({ length: to - from + 1 }, (_, i) => from + i);
  const total = idx.reduce((s, i) => s + rowWeight(rows[i]), 0);
  const span = t1 - t0;
  const lengths = idx.map((i) => Math.floor((span * rowWeight(rows[i])) / total));
  lengths[lengths.length - 1] += span - lengths.reduce((a, b) => a + b, 0);
  let at = t0;
  return idx.map((i, n) => {
    const cue = { i, t0: at, t1: at + lengths[n] };
    at = cue.t1;
    return cue;
  });
}

const rowKind = (row) => {
  if (row.event === "INTERVENE") return "intervene";
  if (row.event === "PROOF_REQ") return "proof";
  if (row.event === "HOLD") return "hold";
  if (row.event === "DISMISS") return "dismiss";
  if (row.label === "system.verified_callback") return "verified";
  return "row";
};

function actISegment(sc, from, to, t0, t1) {
  const rows = sc.rows;
  const atFirst = rows[from].at;
  const atLast = rows[to].at;
  const xOf = (i) => xOfAt(rows[i].at, atFirst, atLast);
  const cues = scheduleRows(rows, from, to, t0, t1).map((c) => {
    const row = rows[c.i];
    const prev = c.i > 0 ? rows[c.i - 1] : null;
    const kind = rowKind(row);
    const tactics = row.features.map((f) => humanFeature(f).toUpperCase());
    const tj = kind === "verified" ? 0.5 : tactics.length ? ((tactics.length - 1) * STAGGER_MS + FLIGHT_MS) / 1000 : 0.24;
    const dPost = row.posterior_ppm ? row.posterior_ppm.map((p, s) => p - (prev?.posterior_ppm?.[s] ?? 0)) : [0, 0, 0, 0, 0];
    return {
      ...c, kind, tj, tactics, xEnd: xOf(c.i), dx: xOf(c.i) - (prev ? xOf(c.i - 1) : xOf(c.i)),
      dLambda: row.lambda_mnats - (prev?.lambda_mnats ?? 0), dPost, delta: row.delta_mnats,
    };
  });
  return { sc, from, to, cues, atFirst, atLast, xOf };
}

export function buildTimeline(ward) {
  const door = scenarioOf(ward, "doorstep_scam");
  const plumber = scenarioOf(ward, "verified_plumber");
  const late = scenarioOf(ward, "late_and_lossy");
  const find = (sc, event) => sc.rows.findIndex((r) => r.event === event);
  const segs = {
    A: actISegment(door, 0, find(door, "PROOF_REQ"), ...WINDOWS.A),
    B: actISegment(plumber, 0, find(plumber, "DISMISS"), ...WINDOWS.B),
  };
  const lateCues = scheduleRows(late.rows, 0, find(late, "INGEST+SYNC"), ...WINDOWS.C);
  const tailCues = scheduleRows(late.rows, find(late, "VISIT_CLOSED"), late.rows.length - 1, ...WINDOWS.D);
  const bs = beliefSeqs(late);
  const atSeq = late.decision.at_seq;
  const R = Math.max(...bs);
  const changes = [];
  for (const c of lateCues) if (late.rows[c.i].seq !== null) changes.push({ t0: c.t0, target: late.rows[c.i].seq, dur: 300, ease: easeOutCubic });
  changes.push({ t0: tailCues[0].t0, target: R, dur: 300, ease: easeOutCubic });
  changes.push({ t0: WINDOWS.rewind[0], target: atSeq, dur: 1200, ease: easeInOutCubic });

  const n = ward.scenarios[ward.signed.scenario].envelopes.length;
  const k = ward.signed.intervene_at_seq - 3;
  const sentences = [{ t0: 0, text: sentenceFor({ type: "act1_intro" }) }];
  const rowSentences = (sc, cues) => {
    let seenDoor = false;
    for (const c of cues) {
      const row = sc.rows[c.i];
      const firstDoor = row.lane === "door" && !seenDoor;
      if (row.lane === "door") seenDoor = true;
      sentences.push({ t0: c.t0, text: sentenceFor({ type: "row", row, envelope: sc.envelopes.find((e) => e.seq === row.seq), firstDoor }) });
    }
  };
  rowSentences(door, segs.A.cues);
  sentences.push({ t0: WINDOWS.cross1[0], text: sentenceFor({ type: "counterpoint" }) });
  rowSentences(plumber, segs.B.cues);
  sentences.push({ t0: WINDOWS.fade2[0], text: sentenceFor({ type: "act2_intro" }) });
  rowSentences(late, lateCues);
  rowSentences(late, tailCues);
  sentences.push({ t0: WINDOWS.rewind[0], text: sentenceFor({ type: "rewind", atSeq }) });
  sentences.push({ t0: WINDOWS.fade3[0], text: sentenceFor({ type: "act3_intro", n }) });
  sentences.push({ t0: WINDOWS.verify[0], text: sentenceFor({ type: "verify", atSeq: ward.signed.intervene_at_seq }) });
  sentences.push({ t0: WINDOWS.tamper[0], text: sentenceFor({ type: "tamper", k }) });
  sentences.push({ t0: WINDOWS.rechain[0], text: sentenceFor({ type: "rechain", atSeq: ward.signed.intervene_at_seq }) });
  sentences.push({ t0: WINDOWS.restore[0], text: sentenceFor({ type: "restore" }) });

  const starts = new Set(Object.values(WINDOWS).map((w) => w[0]));
  for (const c of [...segs.A.cues, ...segs.B.cues, ...lateCues, ...tailCues]) starts.add(c.t0);
  const record = parseRecord(ward.signed.attestation);

  return {
    duration: DURATION, acts: ACTS, windows: WINDOWS, segs, late, lateCues, tailCues, changes, R, atSeq, sentences,
    cueStarts: [...starts].sort((a, b) => a - b), n, k, record,
  };
}

// ── keys (§10.4) ──
export function keyAction(key, { shiftKey = false, ctrlKey = false, metaKey = false, altKey = false, repeat = false } = {}) {
  if (ctrlKey || metaKey || altKey) return null;
  const arrow = key === "ArrowLeft" || key === "ArrowRight";
  if (repeat && !arrow) return null;
  const dir = key === "ArrowLeft" ? -1 : 1;
  if (arrow) return shiftKey ? { type: "nudge", ms: dir * 1000, preventDefault: true } : { type: "cue", dir, preventDefault: true };
  switch (key) {
    case " ": return { type: "toggle", preventDefault: true };
    case "1": return { type: "seek", to: 0, keepPlay: true };
    case "2": return { type: "seek", to: 35000, keepPlay: true };
    case "3": return { type: "seek", to: 65000, keepPlay: true };
    case "r": case "R": return { type: "reset" };
    case "v": case "V": return { type: "verify" };
    case "t": case "T": return { type: "tamper" };
    case "h": case "H": return { type: "hints" };
    case "m": case "M": return { type: "motion" };
    case "d": case "D": return { type: "debug" };
    case "f": case "F": return { type: "fullscreen" };
    default: return null;
  }
}

export function seekCue(tl, t, dir) {
  const starts = tl.cueStarts;
  if (dir > 0) return starts.find((s) => s > t + 1) ?? DURATION;
  return [...starts].reverse().find((s) => s < t - 150) ?? 0;
}

// ── Act I ──
const stampLines = (titles) => {
  const lines = [];
  let line = "";
  for (const item of titles) {
    const next = line ? `${line} · ${item}` : item;
    if (next.length > 46 && line) { lines.push(line); line = item; } else line = next;
  }
  if (line) lines.push(line);
  return lines;
};

function act1Frame(tl, ward, t, motion) {
  const segKey = t < WINDOWS.cross1[0] + 300 ? "A" : "B";
  const seg = tl.segs[segKey];
  const sc = seg.sc;
  const yA = yOfLambda(ward.sprt.a_mnats);
  const yB = yOfLambda(ward.sprt.b_mnats);
  const started = seg.cues.filter((c) => c.t0 <= t);
  const cur = started[started.length - 1] ?? null;

  let x = FIELD.walkX0;
  let lam = 0;
  const post = [0, 0, 0, 0, 0];
  for (const c of started) {
    x += c.dx * (motion ? easeOutCubic((t - c.t0) / 240) : 1);
    const tau = (t - (c.t0 + c.tj * 1000)) / 1000;
    const k = motion ? 1 - springLeft(tau) : tau >= 0 ? 1 : 0;
    lam += c.dLambda * k;
    for (let s = 0; s < 5; s += 1) post[s] += c.dPost[s] * k;
  }
  const y = yOfLambda(lam);
  const lamR = Math.round(lam);

  const intro = motion
    ? { a: easeOutCubic(t / 600), b: easeOutCubic((t - 600) / 600), point: clamp01((t - 1200) / 400) }
    : { a: 1, b: 1, point: t >= 1200 ? 1 : 0 };
  const iv = seg.cues.find((c) => c.kind === "intervene");
  const tc = iv && t >= iv.t0 ? iv.t0 : null;
  const aLit = tc === null ? 0 : motion ? clamp01((t - tc) / 160) : 1;
  const hold = seg.cues.find((c) => c.kind === "hold");
  const dis = seg.cues.find((c) => c.kind === "dismiss");
  const bState = dis && t >= dis.t0 ? "dismiss" : hold && t >= hold.t0 ? "hold" : "idle";
  const bLit = bState === "dismiss" ? (motion ? clamp01((t - dis.t0) / 160) : 1) : 0;
  const ladder = tc === null ? 0 : motion ? clamp01((t - tc - 320) / 240) : t >= tc + 320 ? 1 : 0;

  const chips = [];
  const flyers = [];
  const particles = [];
  const slotOf = (cue, j) => {
    let sx = FIELD.walkX0;
    for (let q = 0; q < j; q += 1) sx += chipWidth(cue.tactics[q]) + 8;
    return sx;
  };
  for (const c of started) {
    const end = c.t0 + (c.tactics.length - 1) * STAGGER_MS + FLIGHT_MS;
    if (!c.tactics.length || t >= (motion ? end : c.t0 + c.tj * 1000)) continue;
    c.tactics.forEach((text, j) => {
      const w = chipWidth(text);
      const sx = slotOf(c, j);
      const start = c.t0 + j * STAGGER_MS;
      const k = (t - start) / FLIGHT_MS;
      if (!motion || k < 0) {
        chips.push({ text, x: sx, y: CHIP_Y, w, alpha: motion ? clamp01((t - c.t0) / 120) : 1, kind: "claimed" });
      } else if (k < 1) {
        const e = easeInQuad(k);
        const fx = lerp(sx + w / 2, x, e);
        const fy = lerp(CHIP_Y, y, e);
        flyers.push({ text, x: fx - w / 2, y: fy, w, alpha: 1, kind: "claimed" });
        particles.push([fx, fy, 2, 1]);
      }
    });
  }

  let delta = null;
  for (const c of started) {
    if (c.delta === 0 || c.kind === "verified") continue;
    const age = t - (c.t0 + c.tj * 1000);
    if (age >= 0 && age < 1200) delta = { text: `${formatMnats(c.delta)} mn`, alpha: 1 - age / 1200 };
  }

  let bounce = null;
  const pr = seg.cues.find((c) => c.kind === "proof");
  let crossArr = [0, 0, -1];
  let waveA = [0, 0, 0, 0];
  if (tc !== null && motion) crossArr = [iv.xEnd, yA, tc / 1000];
  if (pr && t >= pr.t0) {
    const tau = (t - pr.t0) / 1000;
    const xp = pr.xEnd;
    const yp = y;
    const text = "dismiss_alert · S- · asked by a voice in the room";
    const request = { text, x: FIELD.walkX0, y: CHIP_Y, w: chipWidth(text), alpha: motion ? clamp01(tau / 0.25) : 1 };
    let particle = null;
    if (motion) {
      if (tau >= 0.25 && tau < 0.85) {
        particle = { x: xp, y: lerp(yp + 20, yA - 10, easeInQuad((tau - 0.25) / 0.6)), alpha: 1 };
      } else if (tau >= 0.85 && tau < 1.35) {
        const k = (tau - 0.85) / 0.5;
        particle = { x: xp, y: yA - 10 - 0.55 * (yA - 10 - (yp + 20)) * easeOutCubic(k), alpha: 1 - k };
      }
      waveA = [xp, (pr.t0 + 850) / 1000, 14, 1];
    } else if (tau >= 0.85 && tau < 1.35) {
      particle = { x: xp, y: yA - 10, alpha: 1 };
    }
    if (particle) particles.push([particle.x, particle.y, 0, particle.alpha]);
    const sa = motion ? clamp01((tau - 0.95) / 0.16) : tau >= 0.95 ? 1 : 0;
    const row = sc.rows[pr.i];
    const lines = stampLines(row.hx_rules.map((id) => `${id} ${ward.hx.find((h) => h.id === id)?.title ?? ""}`.trim()));
    const sx = xp + 28 + 300 <= FIELD.wardX1 ? xp + 28 : xp - 28 - 300;
    bounce = { request, particle, impact: [xp, yA - 10], stamp: sa > 0 ? { alpha: sa, x: sx, y: yA + 22, w: 300, h: 56, lines } : null };
  }

  const vc = seg.cues.find((c) => c.kind === "verified");
  let verified = null;
  if (vc && t >= vc.t0) {
    const tau = (t - vc.t0) / 1000;
    const dropK = motion ? easeInQuad(tau / 0.5) : 1;
    const vx = vc.xEnd;
    const px = tau < 0.5 ? vx : x;
    const py = tau < 0.5 ? lerp(140, y, dropK) : y;
    const alpha = tau < 0.5 ? 1 : motion ? clamp01(1 - (tau - 0.5) / 0.3) : 0;
    verified = {
      particle: alpha > 0 ? { x: tau < 0.5 ? vx : px, y: py, alpha } : null,
      label: tau >= 0.5 && tau < 3.5 ? { text: `verified_official_callback ${formatMnats(vc.dLambda)} mn · only verified terms may be negative (DC-1)`, alpha: clamp01(1 - (tau - 2.5)) } : null,
    };
    if (verified.particle) particles.push([verified.particle.x, verified.particle.y, 1, verified.particle.alpha]);
  }

  // The walk: every settled row as a step polyline, then the live point.
  const settled = cur ? cur.i - 1 : -1;
  const trail = [];
  if (settled >= 0) for (const p of walkPoints(sc, 0, settled, [seg.from, seg.to])) trail.push([p.x, p.y]);
  else trail.push([FIELD.walkX0, yOfLambda(0)]);
  const prevY = settled >= 0 ? yOfLambda(sc.rows[settled].lambda_mnats) : yOfLambda(0);
  if (cur) trail.push([x, prevY]);
  trail.push([x, y]);
  const walk = trail.slice(-64);

  const state = cur ? stateAt(sc, cur.i) : stateAt(sc, -1);
  const heroTone = lam >= ward.sprt.a_mnats ? "intervene" : lam <= ward.sprt.b_mnats ? "benign" : "ink";
  const pointAlpha = intro.point;
  const glow = {
    t: t / 1000, aLit, bLit, yA, yB, field: [FIELD.wardX0, FIELD.wardX1], cross: crossArr, waveA, waveB: [0, 0, 0, 0],
    point: [x, y], trail: walk, part: particles.slice(0, 8), pointAlpha,
  };
  return {
    scenario: sc.name, counterpoint: segKey === "B", intro, yA, yB, aMnats: ward.sprt.a_mnats, bMnats: ward.sprt.b_mnats, aLit, bLit, bState, aCrossed: tc !== null,
    point: { x, y, alpha: pointAlpha }, walk, delta, chips, flyers, bounce, verified, ladder,
    readout: {
      lambda: formatMnats(lamR), tone: heroTone, lr: lrLabel(lamR), state: state.state,
      posterior: post.map((p) => Math.round(clamp(p, 0, 1_000_000) / 1000) / 10),
    },
    waveA, crossing: crossArr, membrane: waveA[3] > 0.5, glow,
    cue: cur ? cur.i : -1, seq: state.seq, of: sc.envelopes.length, row: cur ? sc.rows[cur.i] : null, stateRowHead: lastHead(sc, cur ? cur.i : -1),
  };
}

function lastHead(sc, i) {
  for (let q = i; q >= 0; q -= 1) if (sc.rows[q].head) return sc.rows[q].head;
  return null;
}

// ── Act II ──
function act2Frame(tl, t, motion) {
  const late = tl.late;
  let S = 0;
  let prev = 0;
  for (const ch of tl.changes) {
    if (ch.t0 <= t) {
      S = motion ? lerp(prev, ch.target, ch.ease((t - ch.t0) / ch.dur)) : ch.target;
      prev = ch.target;
    }
  }
  const sweepT0 = tl.tailCues[0].t0 + 300;
  const flush = motion ? clamp01((t - sweepT0) / 600) : t >= sweepT0 ? 1 : 0;
  const all = [...tl.lateCues, ...tl.tailCues].filter((c) => c.t0 <= t);
  const cur = all[all.length - 1] ?? null;
  const dupCue = tl.lateCues.find((c) => late.rows[c.i].event === "DUPLICATE");
  let dup = null;
  if (dupCue && t >= dupCue.t0) {
    const k = (t - dupCue.t0) / 1200;
    if (motion && k < 1) dup = { rowIndex: dupCue.i, k };
    else if (!motion && t < dupCue.t1) dup = { rowIndex: dupCue.i, k: 0 };
  }
  const view = planeView(late, S, { flush, dup });
  const asOf = stateAt(late, asOfIndex(late, S));
  const features = cur ? late.rows[cur.i].features.map((f) => humanFeature(f).toUpperCase()) : [];
  let sx = PLANE.x0;
  const chips = features.map((text) => {
    const w = chipWidth(text);
    const chip = { text, x: sx, y: CHIP_Y, w, alpha: 1, kind: "claimed" };
    sx += w + 8;
    return chip;
  });
  return {
    S, B: view.B, view, flush, chips,
    side: { seq: view.B, of: view.R, lambda: formatMnats(asOf.lambdaMnats), lr: lrLabel(asOf.lambdaMnats), state: asOf.state },
    cue: cur ? cur.i : -1, seq: Math.min(view.B, late.envelopes.length), of: late.envelopes.length,
    row: cur ? late.rows[cur.i] : null, head: lastHead(late, asOfIndex(late, S)),
  };
}

// ── Act III ──
const VERIFY_T0 = 66400;
const view4 = (T) => (T >= 83200 ? "intact" : T >= 79400 ? "rechained" : T >= 73400 ? "tampered" : "intact");

function tileView(n, k, T) {
  const events = [{ t: -Infinity, view: "intact" }];
  if (n === k) events.push({ t: 72400, view: "tampered" }, { t: 77400, view: "rechained" }, { t: 82500, view: "intact" });
  else if (n > k) events.push({ t: 77400 + 120 * (n - k), view: "rechained" }, { t: 82500, view: "intact" });
  let cur = events[0];
  let prev = events[0];
  for (const e of events) if (e.t <= T) { prev = cur; cur = e; }
  return { view: cur.view, from: prev.view, since: cur.t };
}

function checkRows(crypto, viewOf, alphaOf) {
  return crypto.intact.checks.map((c, i) => {
    const res = crypto[viewOf(i)].checks[i];
    return { id: c.id, name: c.name, ok: res.ok, detail: res.detail, alpha: alphaOf(i) };
  });
}

function act3Core(tl, ward, crypto, spec) {
  const lay = chainLayout(tl.n);
  const k = tl.k;
  const atSeq = ward.signed.intervene_at_seq;
  const tiles = [];
  const teeth = [];
  const views = [];
  for (const slot of lay.tiles) {
    const v = spec.tile(slot.n);
    views.push(v);
    const head = crypto[v.view].replay[slot.n - 1].computedHead;
    const from = crypto[v.from].replay[slot.n - 1].computedHead;
    const mix = from === head ? 1 : clamp01(spec.age(v.since) / 400);
    const content = crypto[v.view].replay[slot.n - 1].contentOk;
    tiles.push({
      n: slot.n, x: slot.x, y: lay.y, lane: ward.scenarios[ward.signed.scenario].envelopes[slot.n - 1].source.toUpperCase(),
      head, from, mix: spec.motion ? mix : 1, border: spec.lit(slot.n) ? (content ? "ok" : "fail") : "none",
      ring: spec.ring && slot.n === atSeq ? spec.ring : null, diff: slot.n === k ? spec.diff : null,
    });
  }
  for (let n = 1; n < tl.n; n += 1) {
    const here = views[n - 1].view;
    const next = views[n].view;
    const open = teethOpen(crypto[here].replay[n - 1].computedHead, crypto[next].envelopes[n].prev);
    teeth.push({ gap: n, open: open.map((o, j) => o && spec.tooth(n, j)) });
  }
  const checks = checkRows(crypto, spec.checkView, spec.checkAlpha);
  const head = tiles[tiles.length - 1].head;
  return { lay: { tile: lay.tile, gap: lay.gap, pitch: lay.pitch, x0: lay.x0, y: lay.y, cell: lay.cell }, tiles, teeth, checks, glass: spec.glass, lattice: { head, alpha: spec.lattice }, k, atSeq };
}

const fadeIn = (T, at, dur = 160, motion = true) => (motion ? clamp01((T - at) / dur) : T >= at ? 1 : 0);

function act3Scheduled(tl, ward, crypto, t, motion) {
  const T = Math.max(t, WINDOWS.verify[0]);
  const k = tl.k;
  const ringOn = T >= 79400 && T < 82500;
  return act3Core(tl, ward, crypto, {
    motion,
    tile: (n) => tileView(n, k, T),
    age: (since) => T - since,
    lit: (n) => T >= VERIFY_T0 + 80 * (n - 1),
    ring: ringOn ? "fail" : null,
    diff: T >= 72400 && T < 82500 ? "received_at +1 ms" : null,
    tooth: (n, j) => (n === k && T >= 72400 && T < 77400 ? T >= 72800 + 40 * j : true),
    checkView: (i) => (i >= 3 ? view4(T) : "intact"),
    checkAlpha: (i) => fadeIn(T, 67800 + 400 * i, 160, motion),
    glass: fadeIn(T, 69800, 160, motion),
    lattice: T >= VERIFY_T0 ? 0.035 + 0.215 * (1 - (motion ? easeOutCubic((T - VERIFY_T0) / 1200) : T >= VERIFY_T0 + 600 ? 1 : 0)) : 0.035,
  });
}

const NEXT_VIEW = { intact: "tampered", tampered: "rechained", rechained: "intact" };
export const nextManualView = (view) => NEXT_VIEW[view];
const MANUAL_MS = { verify: 3600, tamper: 2000, rechain: 2000, restore: 1200 };
export const manualDuration = (kind) => MANUAL_MS[kind] ?? 0;

function act3Manual(tl, ward, crypto, manual, motion) {
  const e = manual.elapsed;
  const k = tl.k;
  const kind = manual.kind;
  const switchAt = kind === "verify" ? -Infinity : 400;
  return act3Core(tl, ward, crypto, {
    motion,
    tile: (n) => ({ view: manual.view, from: kind === "verify" ? manual.view : manual.from, since: 0 }),
    age: () => e,
    lit: (n) => kind !== "verify" || e >= 200 + 80 * (n - 1),
    ring: manual.view === "rechained" && e >= switchAt + 600 ? "fail" : null,
    diff: manual.view !== "intact" ? "received_at +1 ms" : null,
    tooth: (n, j) => (n === k && kind === "tamper" ? e >= 400 + 40 * j : true),
    checkView: (i) => (i >= 3 && kind !== "verify" && e < 1000 ? manual.from : manual.view),
    checkAlpha: (i) => (kind === "verify" ? fadeIn(e, 1400 + 400 * i, 160, motion) : 1),
    glass: 1,
    lattice: kind === "verify" ? 0.035 + 0.215 * (1 - (motion ? easeOutCubic(e / 1200) : 1)) : 0.035,
  });
}

function glassRows(tl) {
  const r = tl.record;
  const hash = (h) => shortHash(h);
  const rows = Object.keys(r).sort().map((key) => {
    const v = r[key];
    let text = String(v);
    let chips = null;
    let link = null;
    if (key === "lambda_mnats") text = `${formatMnats(v)} mn · ${lrLabel(v)}`;
    else if (key === "thresholds_mnats") text = `a ${formatInt(v.a)} · b ${formatInt(v.b)}`;
    else if (key === "ledger_head" || key === "params_digest") text = hash(v);
    else if (key === "reasons") { text = ""; chips = v.map((x) => humanFeature(x)); }
    else if (Array.isArray(v)) text = v.length ? v.join(", ") : "—";
    if (key === "at_seq" || key === "belief_seq" || key === "ledger_head") link = "tile";
    return { key, text, chips, link, full: key === "ledger_head" ? v.slice(7) : null };
  });
  return rows;
}

// ── the frame ──
export function frameAt(tl, ward, crypto, tMs, { motion = true, manual = null } = {}) {
  const t = clamp(tMs, 0, DURATION);
  const fade = (a, b) => (motion ? clamp01((t - a) / (b - a)) : t >= (a + b) / 2 ? 1 : 0);
  const to2 = fade(...WINDOWS.fade2);
  const to3 = fade(...WINDOWS.fade3);
  const cross = motion
    ? t < WINDOWS.cross1[0] + 300 ? 1 - clamp01((t - WINDOWS.cross1[0]) / 300) : clamp01((t - WINDOWS.cross1[0] - 300) / 300)
    : 1;
  const card = fade(WINDOWS.card[0], WINDOWS.card[0] + 600);
  const opacity = { act1: cross * (1 - to2), act2: to2 * (1 - to3), act3: to3 * (1 - card), card };
  const active = t < 35600 ? 1 : t < 65600 ? 2 : 3;

  const a1 = act1Frame(tl, ward, t, motion);
  a1.glow.fade = opacity.act1;
  const a2 = opacity.act2 > 0 || active === 2 ? act2Frame(tl, t, motion) : null;
  const a3 = opacity.act3 > 0 || active === 3
    ? (manual && t >= WINDOWS.fade3[0] ? act3Manual(tl, ward, crypto, manual, motion) : act3Scheduled(tl, ward, crypto, t, motion))
    : null;
  if (a3) a3.glassRows = glassRows(tl);

  let sentence = "";
  let transcript = 0;
  tl.sentences.forEach((s, i) => { if (s.t0 <= t) { sentence = s.text; transcript = i + 1; } });
  if (manual && a3 && t >= WINDOWS.fade3[0]) {
    sentence = sentenceFor(manualContext(tl, ward, manual.kind));
  }

  const rowOf = active === 1 ? a1.row : active === 2 ? a2?.row : null;
  let caption = null;
  if (rowOf) {
    caption = {
      speaker: SPEAKER[rowOf.lane] ?? SPEAKER.system, text: isUtterance(rowOf) ? rowOf.label : rowOf.note, quote: isUtterance(rowOf),
    };
  } else if (active === 3 && a3) {
    caption = { speaker: "LEDGER · SYNTHETIC REPLAY", text: act3Caption(tl, a3), quote: false };
  }
  const seq = active === 1 ? { n: a1.seq, of: a1.of } : active === 2 ? { n: a2.seq, of: a2.of } : { n: tl.n, of: tl.n };
  const latticeHead = active === 1 ? a1.stateRowHead : active === 2 ? a2.head : a3?.lattice.head ?? null;
  const lattice = {
    head: latticeHead,
    alpha: active === 3 && a3 ? a3.lattice.alpha : 0.035,
  };
  return {
    t, active, opacity, motion, sentence, transcript,
    top: { seq: seq.n, of: seq.of, household: ward.household },
    caption, lattice,
    time: { progress: t / DURATION, code: `${clock90(t)} / ${clock90(DURATION)}` },
    act1: a1, act2: a2, act3: a3, glow: a1.glow,
  };
}

function manualContext(tl, ward, kind) {
  const atSeq = ward.signed.intervene_at_seq;
  if (kind === "tamper") return { type: "tamper", k: tl.k };
  if (kind === "rechain") return { type: "rechain", atSeq };
  if (kind === "restore") return { type: "restore" };
  return { type: "verify", atSeq };
}

function act3Caption(tl, a3) {
  const fails = a3.checks.filter((c) => c.ok === false && c.alpha > 0).map((c) => c.name.toLowerCase());
  if (fails.length) return `✗ ${fails.join(", ")} · ${a3.checks.find((c) => c.ok === false).detail}`;
  const shown = a3.checks.filter((c) => c.alpha > 0).length;
  return shown ? `${shown} of 5 checks shown · verification is display-only (TB-8)` : "Waiting to verify · display-only (TB-8)";
}
