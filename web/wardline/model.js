// Wardline's pure model: data -> geometry, state as of a row, sentences, formatters. No DOM, no clock, no randomness.
// Every mapping here is a formula from the brief; plane.svg.js and ward.gl.js draw exactly what this returns.

export const STAGE = Object.freeze({ w: 1920, h: 1080 });
export const FIELD = Object.freeze({ walkX0: 560, walkX1: 1760, wardX0: 560, wardX1: 1824, top: 120, bottom: 860 });
export const LAMBDA = Object.freeze({ max: 9000, min: -4000 });
export const PLANE = Object.freeze({ x0: 160, x1: 1520, top: 120, bottom: 840 });
export const GHOST = 0.16;
export const LANE_NAMES = Object.freeze({ door: "W_ring", room: "W_alexa", wrist: "W_bee" });

// ── numbers and easing ──
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const clamp01 = (v) => clamp(v, 0, 1);
export const lerp = (a, b, k) => a + (b - a) * k;
export const easeOutCubic = (k) => 1 - (1 - clamp01(k)) ** 3;
export const easeInQuad = (k) => clamp01(k) ** 2;
export const easeInOutCubic = (k) => {
  const x = clamp01(k);
  return x < 0.5 ? 4 * x ** 3 : 1 - (-2 * x + 2) ** 3 / 2;
};

// Critically damped spring, closed form: k = 170, c = 2w, m = 1, so w = sqrt(170). It never overshoots.
export const OMEGA = Math.sqrt(170);
export const springLeft = (tau) => (tau <= 0 ? 1 : (1 + OMEGA * tau) * Math.exp(-OMEGA * tau));
export const spring = (y0, y1, tau) => y1 + (y0 - y1) * springLeft(tau);

// The membrane's displacement in px (+ is down); the same constants (60 px, 0.35 s, 3 Hz) are in ward.gl.js.
export const WAVE = Object.freeze({ sigmaPx: 60, decayS: 0.35, hz: 3 });
export function wave(w, x, t) {
  const dt = t - w[1];
  if (w[3] < 0.5 || dt < 0) return 0;
  const u = (x - w[0]) / 60;
  return w[2] * Math.exp(-u * u) * Math.exp(-dt / 0.35) * Math.cos(18.849556 * dt);
}

// ── Act I mappings (§7.1) ──
export const yOfLambda = (m) => 120 + ((9000 - clamp(m, -4000, 9000)) * 740) / 13000;
export const xOfAt = (at, atFirst, atLast) =>
  atLast === atFirst ? FIELD.walkX0 : FIELD.walkX0 + ((at - atFirst) / (atLast - atFirst)) * (FIELD.walkX1 - FIELD.walkX0);

// ── formatters ──
const group = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
export const MINUS = "−";
export const formatMnats = (m) => (m === 0 ? "0" : m > 0 ? `+${group(m)}` : `${MINUS}${group(-m)}`);
export const formatInt = (n) => (n < 0 ? `${MINUS}${group(-n)}` : group(n));
export function lrLabel(m) {
  return m >= 0 ? `${group(Math.round(Math.exp(m / 1000)))} : 1` : `1 : ${group(Math.round(Math.exp(-m / 1000)))}`;
}
export function fmtClock(relMs) {
  const sign = relMs < 0 ? MINUS : "+";
  const ms = Math.abs(Math.round(relMs));
  const p = (n, w) => String(n).padStart(w, "0");
  return `${sign}${p(Math.floor(ms / 60000), 2)}:${p(Math.floor(ms / 1000) % 60, 2)}.${p(ms % 1000, 3)}`;
}
export function fmtClockShort(relMs) {
  const sign = relMs < 0 ? MINUS : "+";
  const ms = Math.abs(Math.round(relMs));
  return `${sign}${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
}
export const parseClock = (text) => {
  const m = /^([+−-])(\d+):(\d\d)\.(\d{3})$/.exec(text);
  if (!m) return null;
  const ms = (Number(m[2]) * 60 + Number(m[3])) * 1000 + Number(m[4]);
  return m[1] === "+" ? ms : -ms;
};
export const shortHash = (head) => `${head.slice(0, 11)}…${head.slice(-4)}`;
export const hashTail = (head) => `${head.slice(7, 11)}…${head.slice(-4)}`;
export const humanFeature = (f) => ({ info_request: "information request" })[f] ?? f.replaceAll("_", " ");
export function joinList(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
export const pad2 = (n) => String(n).padStart(2, "0");
export const clock90 = (ms) => {
  const t = Math.max(0, Math.round(ms / 100) / 10);
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${pad2(m)}:${s < 10 ? "0" : ""}${s.toFixed(1)}`;
};
export function lateness(ms) {
  const s = Math.round(ms / 1000);
  return s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;
}

// Approximate widths for chips and labels (stage px), so the pure layers never measure the DOM.
export const chipWidth = (text) => 16 + Math.round(text.length * 8.4);
export const monoWidth = (text, px = 12) => Math.round(text.length * px * 0.6);

// ── scenario views ──
export function scenarioOf(ward, name) {
  const s = ward.scenarios[name];
  return {
    name, rows: s.rows, envelopes: s.envelopes, versions: s.versions, decision: s.decision,
    decisionDigest: s.decision_digest, summary: s.summary, epochMs: ward.epoch_ms,
    visitGapMs: ward.temporal.visit_gap_ms, aMnats: ward.sprt.a_mnats, bMnats: ward.sprt.b_mnats,
    crossMnats: ward.sprt.cross_device_llr_mnats, hx: ward.hx,
  };
}

const STATES = { INTERVENE: "INTERVENE", HOLD: "HOLD", DISMISS: "DISMISSED", INCONCLUSIVE: "INCONCLUSIVE" };

// State as of rows[0..rowIndex] only (BT-3): nothing after rowIndex is read.
export function stateAt(scenario, rowIndex) {
  let seq = 0;
  let state = "OBSERVE";
  let lambdaMnats = 0;
  let posteriorPpm = null;
  let watermarks = { door: null, room: null, wrist: null };
  let flushed = false;
  const last = Math.min(rowIndex, scenario.rows.length - 1);
  for (let i = 0; i <= last; i += 1) {
    const r = scenario.rows[i];
    if (r.seq !== null) seq = r.seq;
    if (STATES[r.event]) state = STATES[r.event];
    lambdaMnats = r.lambda_mnats;
    posteriorPpm = r.posterior_ppm;
    watermarks = r.watermarks;
    flushed = r.flushed;
  }
  return { seq, lambdaMnats, state, posteriorPpm, watermarks, flushed, row: last >= 0 ? scenario.rows[last] : null };
}

// The step polyline of §7.1 for rows from..to of a segment (x spans the segment's own first and last `at`).
export function walkPoints(scenario, from, to, seg = [from, to]) {
  const rows = scenario.rows;
  const atFirst = rows[seg[0]].at;
  const atLast = rows[seg[1]].at;
  const px = (i) => xOfAt(rows[i].at, atFirst, atLast);
  const py = (i) => yOfLambda(rows[i].lambda_mnats);
  const pts = [{ x: px(from), y: py(from) }];
  for (let i = from + 1; i <= to; i += 1) {
    pts.push({ x: px(i), y: py(i - 1) }, { x: px(i), y: py(i) });
  }
  return pts;
}

// ── belief time (Act II) ──
// A row's belief sequence: its own seq; rows stamped one past the ledger (VISIT_CLOSED, AS_OF, END, INCONCLUSIVE)
// belong to the closing belief R; every other row without a seq stays with the seq before it.
export function beliefSeqs(scenario) {
  const closing = new Set(["VISIT_CLOSED", "AS_OF", "END", "INCONCLUSIVE"]);
  const end = scenario.envelopes.length + 1;
  let prev = 0;
  return scenario.rows.map((r) => {
    if (r.seq !== null) prev = r.seq;
    else if (closing.has(r.event)) prev = end;
    return prev;
  });
}
export function asOfIndex(scenario, S) {
  const bs = beliefSeqs(scenario);
  const B = Math.floor(S + 1e-9);
  let at = -1;
  for (let i = 0; i < bs.length; i += 1) if (bs[i] <= B) at = i;
  return at;
}

export function planeGeometry(sc) {
  const rel = (t) => t - sc.epochMs;
  const R = Math.max(sc.envelopes.length, ...sc.versions.map((v) => v.ts), ...sc.versions.filter((v) => v.te !== null).map((v) => v.te - 1));
  const h = (PLANE.bottom - PLANE.top) / R;
  const V0 = Math.min(...sc.envelopes.map((e) => rel(e.valid.lo))) - 4000;
  const V1 = Math.max(...sc.envelopes.map((e) => rel(e.valid.hi))) + 8000;
  const xRaw = (v) => PLANE.x0 + ((v - V0) / (V1 - V0)) * (PLANE.x1 - PLANE.x0);
  const xClip = (v) => clamp(xRaw(v), PLANE.x0, PLANE.x1);
  return {
    R, h, V0, V1, rel, xRaw, xClip,
    yc: (s) => PLANE.bottom - (s - 0.5) * h,
    yBottom: (s) => PLANE.bottom - (s - 1) * h,
    yTop: (s) => PLANE.bottom - s * h,
  };
}

const trustOf = (kind) => (kind.startsWith("room.") ? "claimed" : kind === "system.verified_callback" ? "verified" : "observed");
const ARROW = 6;

// Every mark of §8.2 with its geometry and opacity, for belief S. opts: {flush: 0..1, dup: {rowIndex, k}}.
export function planeView(sc, S, opts = {}) {
  const g = planeGeometry(sc);
  const { R, h, rel, xRaw, xClip, yc, yBottom } = g;
  const B = clamp(Math.floor(S + 1e-9), 0, R);
  const flush = opts.flush ?? 0;
  const seqRows = sc.rows.filter((r) => r.seq !== null);
  const rowOfSeq = new Map(seqRows.map((r) => [r.seq, r]));
  const op = (ts) => (ts > B ? GHOST : 1);

  const xTicks = [];
  for (let v = Math.ceil(g.V0 / 10000) * 10000; v <= g.V1; v += 10000) xTicks.push({ x: xRaw(v), label: fmtClock(v) });
  const yTicks = Array.from({ length: R }, (_, i) => ({ y: yc(i + 1), label: `seq ${pad2(i + 1)}` }));

  const beams = sc.envelopes.map((e) => {
    const lo = rel(e.valid.lo);
    const hi = rel(e.valid.hi);
    let x0 = xClip(lo);
    let x1 = xClip(hi);
    if (x1 - x0 < 6) { const mid = (x0 + x1) / 2; x0 = mid - 3; x1 = mid + 3; }
    const y = yc(e.seq);
    const arrivalRel = rel(e.received_at);
    const ax = xClip(arrivalRel);
    const clipped = xRaw(arrivalRel) > PLANE.x1;
    const row = rowOfSeq.get(e.seq);
    const attempt = /^INGEST@(\d+)$/.exec(row?.event ?? "");
    const via = attempt ? `attempt ${attempt[1]}` : row?.event === "INGEST+SYNC" ? "recovered by sync" : "late";
    const kindLabel = e.kind;
    return {
      seq: e.seq, kind: kindLabel, trust: trustOf(e.kind), x0, x1, y, opacity: op(e.seq),
      clipLeft: xRaw(lo) < PLANE.x0, clipRight: xRaw(hi) > PLANE.x1,
      labelX: x1 + 8, labelY: y - 6,
      arrival: {
        x: ax, y, from: x1, clipped,
        label: clipped ? `arrived ${fmtClockShort(arrivalRel)} · ${via} · ${lateness(e.received_at - e.valid.hi)} late` : null,
      },
    };
  });

  const versions = sc.versions.map((v) => {
    const open = v.te === null;
    const yBot = yBottom(v.ts);
    const yTop = open ? PLANE.top : yBottom(v.te);
    const extHi = v.vt === null ? rel(v.vf_hi) + sc.visitGapMs : rel(v.vt);
    return {
      fid: v.fid, n: v.n, label: `v${v.n}`, x0: xClip(rel(v.vf_lo)), x1: xClip(rel(v.vf_hi)), yTop, yBottom: yBot,
      fadeTop: open, current: v.ts <= B && (open || B < v.te),
      ext: { x0: xClip(rel(v.vf_hi)), x1: xClip(extHi), clipped: xRaw(extHi) > PLANE.x1, certain: v.vt !== null },
      opacity: op(v.ts),
    };
  });

  let overlap = null;
  const overlapRow = seqRows.find((r) => r.note.includes("door_unknown_overlap"));
  if (overlapRow) {
    const env = sc.envelopes.find((e) => e.seq === overlapRow.seq);
    const vis = sc.versions.find((v) => v.ts <= env.seq && (v.te === null || env.seq < v.te));
    const lo = Math.max(rel(vis.vf_lo), rel(env.valid.lo));
    const hi = Math.min(rel(vis.vf_hi) + sc.visitGapMs, rel(env.valid.hi));
    if (lo <= hi) {
      overlap = {
        x0: xClip(lo), x1: xClip(hi), y0: PLANE.top, y1: PLANE.bottom, opacity: op(env.seq),
        label: `possibly_overlaps → door_unknown_overlap ${formatMnats(sc.crossMnats.door_unknown_overlap)} mn`,
      };
    }
  }

  const frontiers = Object.keys(LANE_NAMES).map((lane) => {
    const pts = [];
    for (let s = 1; s <= B; s += 1) {
      let last = null;
      for (const r of seqRows) if (r.seq <= s) last = r;
      if (!last) continue;
      const w = last.watermarks[lane];
      const finite = w === null ? null : rel(w);
      let x = finite === null ? PLANE.x1 : xClip(finite);
      if (s === R && flush > 0) x = lerp(x, PLANE.x1, easeOutCubic(flush));
      pts.push({ s, x, yBottom: yBottom(s), yTop: yBottom(s + 1), off: finite === null ? 0 : xRaw(finite) < PLANE.x0 ? -1 : xRaw(finite) > PLANE.x1 ? 1 : 0, rel: finite });
    }
    const end = pts[pts.length - 1];
    let chip = null;
    if (end && end.rel !== null && end.off !== 0 && !(B === R && flush > 0)) {
      const min = `${end.rel < 0 ? MINUS : "+"}${(Math.abs(end.rel) / 60000).toFixed(1)} min`;
      chip = end.off < 0 ? { x: PLANE.x0 + 4, anchor: "start", text: `← ${LANE_NAMES[lane]} ${min}` }
        : { x: PLANE.x1 - 4, anchor: "end", text: `${LANE_NAMES[lane]} ${min} →` };
    }
    return { lane, name: LANE_NAMES[lane], pts, chip };
  });
  // The off-plane chips stack just above the belief line, or just below it when the line is at the top.
  const scrubY = PLANE.bottom - clamp(S, 0, R) * h;
  frontiers.filter((f) => f.chip).forEach((f, i) => {
    f.chip.y = scrubY > PLANE.top + 40 ? scrubY - 6 - 16 * i : scrubY + 44 + 16 * i;
  });
  const furthest = Math.max(PLANE.x0, ...frontiers.map((f) => f.pts[f.pts.length - 1]?.x ?? PLANE.x0));
  const hatch = furthest < PLANE.x1 - 1 ? { x0: furthest, x1: PLANE.x1, label: "may still arrive" } : null;
  const flushLabel = B === R && flush > 0 ? "end of stream · W = ∞ (replay only)" : null;

  let stake = null;
  const callouts = [];
  if (sc.decision) {
    const d = sc.decision;
    const y = PLANE.bottom - d.at_seq * h;
    stake = {
      y, x0: PLANE.x0, x1: PLANE.x1, opacity: B >= d.at_seq ? 1 : GHOST,
      label: `DECISION · ${d.decision} · at_seq ${d.at_seq} · Λ ${formatMnats(d.lambda_mnats)} mn · head ▦ ${hashTail(d.ledger_head)}`,
    };
    for (const r of seqRows) {
      if (!r.note.includes("after the decision")) continue;
      const beam = beams.find((b) => b.seq === r.seq);
      const cx = (beam.x0 + beam.x1) / 2;
      callouts.push({ seq: r.seq, x: cx, y0: beam.y, y1: y, labelX: Math.max(beam.x1 + 8, PLANE.x0 + 140), labelY: beam.y - 22, opacity: r.seq <= B ? 1 : GHOST, label: "revision after the decision · record unchanged (BT-4)" });
    }
  }

  let duplicate = null;
  if (opts.dup) {
    const row = sc.rows[opts.dup.rowIndex];
    let src = null;
    for (let i = opts.dup.rowIndex - 1; i >= 0; i -= 1) {
      const r = sc.rows[i];
      if (r.seq !== null && r.lane === row.lane && r.label === row.label) { src = r; break; }
    }
    const beam = src && beams.find((b) => b.seq === src.seq);
    if (beam) {
      duplicate = { x0: beam.x0, x1: beam.x1, y: PLANE.bottom - clamp(S, 0, R) * h, opacity: 1 - clamp01(opts.dup.k), label: "duplicate · absorbed before the ledger (E-6)" };
    }
  }

  return {
    R, h, B, V0: g.V0, V1: g.V1, xTicks, yTicks, beams, versions, overlap, frontiers, hatch, flushLabel, stake, callouts, duplicate,
    scrubber: { y: PLANE.bottom - clamp(S, 0, R) * h, tintY0: PLANE.top },
  };
}

// ── Act III geometry (§9.1) ──
export function chainLayout(n) {
  let tile = 64;
  let gap = 56;
  let total = n * tile + (n - 1) * gap;
  if (total > 1600) {
    const k = 1600 / total;
    tile *= k;
    gap *= k;
    total = 1600;
  }
  const x0 = Math.round((STAGE.w - total) / 2);
  const pitch = tile + gap;
  return { n, tile, gap, pitch, x0, y: 240, cell: tile / 16, tiles: Array.from({ length: n }, (_, i) => ({ n: i + 1, x: x0 + i * pitch })) };
}

const hexBytes = (head) => Uint8Array.from(head.slice(7).match(/../g), (h) => parseInt(h, 16));
// 16 x 16 booleans: row r reads bytes 2r and 2r + 1; column c is byte 2r + (c >> 3), bit 7 - (c & 7), MSB first.
export function tileBits(head) {
  const b = hexBytes(head);
  return Array.from({ length: 16 }, (_, r) => Array.from({ length: 16 }, (_, c) => ((b[2 * r + (c >> 3)] >> (7 - (c & 7))) & 1) === 1));
}
export const headWords = (head) => {
  const b = hexBytes(head);
  return Array.from({ length: 8 }, (_, j) => ((b[4 * j] << 24) | (b[4 * j + 1] << 16) | (b[4 * j + 2] << 8) | b[4 * j + 3]) >>> 0);
};
// Tooth j is open when 32-bit word j of the computed head differs from the same word of the next envelope's prev.
export function teethOpen(computedHead, nextPrev) {
  const a = headWords(computedHead);
  const b = headWords(nextPrev);
  return a.map((w, j) => w !== b[j]);
}

// ── sentences (§16) ──
const MAX_SENTENCE = 120;
const asClock = (text) => parseClock(text.replace("-", MINUS)) ?? parseClock(text);

function rowSentence(ctx) {
  const { row } = ctx;
  const features = row.features.map(humanFeature);
  const attempt = /^INGEST@(\d+)$/.exec(row.event);
  if (attempt) {
    const late = ctx.envelope ? lateness(ctx.envelope.received_at - ctx.envelope.valid.hi) : "much";
    return `A Ring event arrived ${late} late, on attempt ${attempt[1]}. It revises belief, never the decision (BT-4).`;
  }
  switch (row.event) {
    case "INGEST+SYNC": return "A lost Bee line came back through sync. Λ moves; the signed decision does not (BT-4).";
    case "INTERVENE": return `Λ crossed A at ${lrLabel(row.lambda_mnats)}. HEARTHWARD intervenes and alerts the trusted circle.`;
    case "PROOF_REQ": return "A voice asked Alexa to dismiss the alert. Lowering protection needs a circle member's passkey.";
    case "HOLD": return "Λ is below B, but dismissal waits until every source's watermark passes the episode (BT-A).";
    case "DISMISS": return "Every watermark passed. The episode is dismissed.";
    case "INCONCLUSIVE": return "The stream ended between B and A: no action, and nothing dismissed.";
    case "DUPLICATE": return "Ring sent the same event twice. The duplicate is absorbed before the ledger (E-6).";
    case "QUARANTINE": return "A timestamp from the future stays out of the ledger and out of belief (BT-5).";
    case "REJECTED": return "A delivery failed authentication and was never parsed.";
    case "VISIT_CLOSED": {
      const vt = /vt = (\S+)/.exec(row.note)?.[1];
      return `The Ring watermark passed, so the visit closes (vt ${vt ? fmtClock(asClock(vt)) : "set"}).`;
    }
    case "AS_OF": {
      const m = /believed at seq (\d+): \[(\S+), (\S+)\] v(\d+); now \[(\S+), (\S+)\] v(\d+)/.exec(row.note);
      if (!m) return "Belief then and belief now both stay on record (BT-1).";
      const span = (a, b) => `${Math.round((asClock(b) - asClock(a)) / 1000)} s`;
      return `Belief at seq ${m[1]}: v${m[4]}, ${span(m[2], m[3])}. Belief now: v${m[7]}, ${span(m[5], m[6])}. Both versions stay on record (BT-1).`;
    }
    case "END": return "End of stream (replay only): every watermark goes to ∞ and everything settles.";
    default: break;
  }
  if (row.lane === "door") {
    return ctx.firstDoor ? `The front door reported ${row.label}. Synthetic Ring event.`
      : `Ring: ${row.label}. The visit's valid time widens; no version is overwritten (BT-1).`;
  }
  if (row.label === "conversation starts") return "A conversation began near the resident. Synthetic Bee stream.";
  if (row.label === "system.verified_callback") return "The official number confirmed the visit. Only a verified fact may pull evidence down (TL-1).";
  if (row.lane === "wrist") {
    return features.length
      ? `Heard ${joinList(features)}. Evidence ${formatMnats(row.delta_mnats)} mn → likelihood ratio ${lrLabel(row.lambda_mnats)}.`
      : `No scam tactic in that line. Evidence ${formatMnats(row.delta_mnats)} mn.`;
  }
  if (row.lane === "room") return "The resident asked Alexa about the visitor. A claim adds only tactics not yet heard (DC-2).";
  return row.note;
}

export function sentenceFor(ctx) {
  let text;
  switch (ctx.type) {
    case "act1_intro": text = "HEARTHWARD watches one household's door, room and wrist, and decides with an explicit error budget."; break;
    case "counterpoint": text = "Counterpoint: a real plumber, confirmed by calling the official number (verified_plumber)."; break;
    case "act2_intro": text = "Same scam on a harsher network: a Ring retry, a duplicate, a lost Bee line (late_and_lossy)."; break;
    case "rewind": text = `As of seq ${ctx.atSeq}: exactly what HEARTHWARD knew when it decided. Replay reproduces it (BT-2, BT-3).`; break;
    case "act3_intro": text = `The ledger behind the decision: ${ctx.n} hashes, each binding everything before it (§4.2).`; break;
    case "verify": text = `Every head re-derives from its envelope, and the signed decision names the head at seq ${ctx.atSeq}.`; break;
    case "tamper": text = `One millisecond changed in seq ${ctx.k}. Its head no longer matches, and the zipper opens.`; break;
    case "rechain": text = `Re-chaining hides the break, but the signed record still names the original head at seq ${ctx.atSeq}.`; break;
    case "restore": text = "Restored. The chain and the signature verify again."; break;
    case "row": text = rowSentence(ctx); break;
    default: throw new Error(`sentenceFor: unknown context ${ctx.type}`);
  }
  if (text.length > MAX_SENTENCE) throw new Error(`sentence is ${text.length} characters: ${text}`);
  return text;
}

export const SPEAKER = Object.freeze({
  door: "DOOR · SYNTHETIC RING", wrist: "WRIST · SYNTHETIC BEE", room: "ROOM · SYNTHETIC ALEXA+", system: "SYSTEM · HEARTHWARD",
});

export const isUtterance = (row) => row.lane === "wrist" && row.label.startsWith('"');
