// Wardline's SVG ink layer: every crisp line, rect and label of the three acts and the background lattice.
// It draws exactly what director.frameAt() and model.js return; colours come from classes in wardline.css.
import { FIELD, PLANE, chipWidth, formatMnats, lrLabel, tileBits, wave, yOfLambda } from "./model.js";

const NS = "http://www.w3.org/2000/svg";

// A keyed retained pool per z-level: elements are created once, only changed attributes are written, and elements
// not touched by the current frame are removed.
function makeLayer(parent, levels) {
  const groups = Object.fromEntries(levels.map((name) => {
    const g = document.createElementNS(NS, "g");
    parent.appendChild(g);
    return [name, g];
  }));
  const live = new Map();
  let used = new Set();
  return {
    begin() { used = new Set(); },
    el(level, key, tag, attrs, text) {
      let e = live.get(key);
      if (!e) {
        e = document.createElementNS(NS, tag);
        e.cache = {};
        groups[level].appendChild(e);
        live.set(key, e);
      }
      for (const name of Object.keys(attrs)) {
        const v = String(attrs[name]);
        if (e.cache[name] !== v) { e.setAttribute(name, v); e.cache[name] = v; }
      }
      if (text !== undefined && e.textContent !== text) e.textContent = text;
      used.add(key);
      return e;
    },
    end() {
      for (const [key, e] of live) if (!used.has(key)) { e.remove(); live.delete(key); }
    },
  };
}

const r1 = (n) => Math.round(n * 10) / 10;
const text = (L, level, key, x, y, cls, str, extra = {}) => L.el(level, key, "text", { x: r1(x), y: r1(y), class: cls, ...extra }, str);
const line = (L, level, key, x1, y1, x2, y2, cls, extra = {}) => L.el(level, key, "line", { x1: r1(x1), y1: r1(y1), x2: r1(x2), y2: r1(y2), class: cls, ...extra });
const rect = (L, level, key, x, y, w, h, cls, extra = {}) => L.el(level, key, "rect", { x: r1(x), y: r1(y), width: r1(Math.max(w, 0)), height: r1(Math.max(h, 0)), class: cls, ...extra });

const chip = (L, level, key, c, cls) => {
  rect(L, level, `${key}:r`, c.x, c.y - 12, c.w, 24, cls, { rx: 2, "stroke-dasharray": "4 3", opacity: c.alpha });
  text(L, level, `${key}:t`, c.x + c.w / 2, c.y + 4, "t-label f-ink1", c.text, { "text-anchor": "middle", opacity: c.alpha });
};

// ── Act I ──
function renderAct1(L, f) {
  const a = f.act1;
  L.begin();
  const yA = a.yA;
  const yB = a.yB;
  const y0 = yOfLambda(0);
  const lerpX = (k) => FIELD.wardX0 + (FIELD.wardX1 - FIELD.wardX0) * k;
  const labelA = a.intro.a > 0.6 ? 1 : 0;
  const labelB = a.intro.b > 0.6 ? 1 : 0;

  line(L, "lines", "zero", FIELD.wardX0, y0, lerpX(a.intro.b), y0, "s-ink3 nf", { "stroke-dasharray": "1 4", "stroke-width": 1, opacity: 0.25 });
  text(L, "labels", "zero:l", FIELD.wardX1, y0 - 8, "t-mono12 f-ink3", "Λ = 0 · 1 : 1", { "text-anchor": "end", opacity: labelB });

  // A: the membrane. The path samples the same wave() as the shader (§11).
  const pts = [];
  for (let i = 0; i < 96; i += 1) {
    const x = FIELD.wardX0 + ((lerpX(a.intro.a) - FIELD.wardX0) * i) / 95;
    pts.push(`${r1(x)},${r1(yA + wave(a.waveA, x, f.t / 1000))}`);
  }
  const crossed = a.aLit >= 0.5;
  L.el("lines", "A", "polyline", { points: pts.join(" "), class: `nf ${crossed ? "s-hot" : "s-scam"}`, "stroke-width": crossed ? 3 : 2, opacity: crossed ? 1 : 0.4, "stroke-linejoin": "round" });
  text(L, "labels", "A:l", FIELD.wardX0, yA - 10, "t-label f-ink2", "A · INTERVENE", { opacity: labelA });
  text(L, "labels", "A:v", FIELD.wardX1, yA - 10, "t-mono12 f-ink2", `${formatMnats(a.aMnats)} mn · ${lrLabel(a.aMnats)}`, { "text-anchor": "end", opacity: labelA });

  const bw = a.bState === "dismiss" ? 2 : a.bState === "hold" ? 1.5 : 1;
  line(L, "lines", "B", FIELD.wardX0, yB, lerpX(a.intro.b), yB, "s-safe nf", {
    "stroke-width": bw, opacity: a.bState === "idle" ? 0.35 : 1, "stroke-dasharray": a.bState === "hold" ? "6 4" : "none",
  });
  text(L, "labels", "B:l", FIELD.wardX0, yB + 20, "t-label f-ink2", "B · DISMISS", { opacity: labelB });
  text(L, "labels", "B:v", FIELD.wardX1, yB + 20, "t-mono12 f-ink2", `${formatMnats(a.bMnats)} mn · ${lrLabel(a.bMnats)}`, { "text-anchor": "end", opacity: labelB });
  if (a.bState === "hold") text(L, "labels", "B:h", FIELD.wardX0, yB + 38, "t-mono12 f-safe", "Λ ≤ B · waiting for every watermark (BT-A)");

  if (a.counterpoint) text(L, "labels", "cp", FIELD.wardX1, 136, "t-label f-ink2", "COUNTERPOINT · verified_plumber", { "text-anchor": "end" });

  if (a.walk.length > 1) L.el("lines", "walk", "polyline", { points: a.walk.map(([x, y]) => `${r1(x)},${r1(y)}`).join(" "), class: "nf s-ink1", "stroke-width": 1.5, opacity: 0.7, "stroke-linejoin": "round" });
  L.el("marks", "point", "circle", { cx: r1(a.point.x), cy: r1(a.point.y), r: 6, class: "f-ink1", opacity: a.point.alpha });
  if (a.delta) text(L, "labels", "delta", a.point.x + 14, a.point.y - 14, "t-mono14 f-ink1", a.delta.text, { opacity: r1(a.delta.alpha) });

  a.chips.forEach((c, i) => chip(L, "marks", `chip${i}`, c, "nf s-scam"));
  a.flyers.forEach((c, i) => chip(L, "marks", `fly${i}`, c, "nf s-scam"));

  const b = a.bounce;
  if (b) {
    chip(L, "marks", "req", b.request, "nf s-ink1");
    if (b.particle) L.el("marks", "bp", "circle", { cx: r1(b.particle.x), cy: r1(b.particle.y), r: 4, class: "f-ink1", opacity: r1(b.particle.alpha) });
    if (b.stamp) {
      const s = b.stamp;
      rect(L, "marks", "stamp", s.x, s.y, s.w, s.h, "f-bg0 s-hot", { rx: 2, "stroke-width": 1, opacity: s.alpha, "fill-opacity": 0.9 });
      text(L, "labels", "stamp:t", s.x + s.w / 2, s.y + s.h / 2 + 5, "t-mono14 f-hot", "BLOCKED · PROOF_REQUIRED", { "text-anchor": "middle", opacity: s.alpha });
      s.lines.forEach((ln, i) => text(L, "labels", `stamp:r${i}`, s.x, s.y + s.h + 18 + i * 16, "t-small f-ink2", ln, { opacity: s.alpha }));
    }
  }
  const v = a.verified;
  if (v) {
    if (v.particle) {
      L.el("marks", "vp", "circle", { cx: r1(v.particle.x), cy: r1(v.particle.y), r: 5, class: "f-ver", opacity: v.particle.alpha });
      L.el("marks", "vp2", "circle", { cx: r1(v.particle.x), cy: r1(v.particle.y), r: 9, class: "nf s-ver", "stroke-width": 1.5, opacity: v.particle.alpha });
    }
    if (v.label) text(L, "labels", "vl", a.point.x + 14, a.point.y - 36, "t-mono12 f-ink1", v.label.text, { opacity: r1(v.label.alpha) });
  }
  L.end();
}

// ── Act II ──
const BEAM_CLASS = { observed: "s-ink1", claimed: "s-ink1", verified: "s-ver" };

function renderAct2(L, f) {
  L.begin();
  const v = f.act2.view;
  const { x0, x1, top, bottom } = PLANE;
  for (let s = 0; s <= v.R; s += 1) line(L, "bg", `band${s}`, x0, bottom - s * v.h, x1, bottom - s * v.h, "s-line2 nf", { "stroke-width": 1, opacity: 0.55 });
  for (const t of v.xTicks) {
    line(L, "bg", `gx${t.label}`, t.x, top, t.x, bottom, "s-line2 nf", { "stroke-width": 1, opacity: 0.45 });
    line(L, "bg", `tk${t.label}`, t.x, bottom, t.x, bottom + 6, "s-ink2 nf", { "stroke-width": 1 });
    text(L, "labels", `tl${t.label}`, t.x, bottom + 15, "t-mono12 f-ink2", t.label, { "text-anchor": "middle" });
  }
  v.yTicks.forEach((t, i) => text(L, "labels", `yt${i}`, 150, t.y + 4, "t-mono12 f-ink2", t.label, { "text-anchor": "end", opacity: i + 1 <= v.B ? 1 : 0.65 }));
  text(L, "labels", "xtitle", x1, 876, "t-label f-ink2", "VALID TIME · when it happened", { "text-anchor": "end" });
  text(L, "labels", "ytitle", 92, (top + bottom) / 2, "t-label f-ink2", "BELIEF · ledger seq, when HEARTHWARD learned it", { "text-anchor": "middle", transform: `rotate(-90 92 ${(top + bottom) / 2})` });

  if (v.hatch) {
    rect(L, "bg", "hatch", v.hatch.x0, top, v.hatch.x1 - v.hatch.x0, bottom - top, "nf", { fill: "url(#hatch)", opacity: 0.35 });
    if (v.hatch.x1 - v.hatch.x0 > 130) text(L, "labels", "hatch:l", v.hatch.x0 + 10, top + 16, "t-label f-ink3", v.hatch.label);
  }
  if (v.flushLabel) text(L, "labels", "flush", x1 - 8, top + 16, "t-label f-ink2", v.flushLabel, { "text-anchor": "end" });

  if (v.overlap) {
    const o = v.overlap;
    rect(L, "bg", "overlap", o.x0, o.y0, Math.max(o.x1 - o.x0, 2), o.y1 - o.y0, "nf", { fill: "url(#gBand)", opacity: o.opacity });
    text(L, "labels", "overlap:l", (o.x0 + o.x1) / 2, top + 36, "t-mono12 f-scam", o.label, { opacity: o.opacity });
  }

  for (const ver of v.versions) {
    const k = `ver${ver.n}`;
    const h = ver.yBottom - ver.yTop;
    if (ver.fadeTop) {
      rect(L, "ver", `${k}:f`, ver.x0, ver.yTop, ver.x1 - ver.x0, h, "nf", { fill: "url(#gFade)", opacity: ver.opacity });
      line(L, "ver", `${k}:l`, ver.x0, ver.yTop, ver.x0, ver.yBottom, "nf", { stroke: "url(#gFadeEdge)", "stroke-width": 1, opacity: ver.opacity });
      line(L, "ver", `${k}:r`, ver.x1, ver.yTop, ver.x1, ver.yBottom, "nf", { stroke: "url(#gFadeEdge)", "stroke-width": 1, opacity: ver.opacity });
      line(L, "ver", `${k}:b`, ver.x0, ver.yBottom, ver.x1, ver.yBottom, "s-ink2 nf", { "stroke-width": 1, opacity: ver.opacity });
    } else {
      rect(L, "ver", `${k}:f`, ver.x0, ver.yTop, ver.x1 - ver.x0, h, "f-ink2 s-ink2", { "fill-opacity": 0.14, "stroke-width": 1, opacity: ver.opacity * (ver.current ? 1 : 0.8) });
    }
    if (ver.ext.x1 - ver.ext.x0 > 1) rect(L, "ver", `${k}:x`, ver.ext.x0, ver.yTop, ver.ext.x1 - ver.ext.x0, h, "nf", { fill: "url(#gFeather)", opacity: ver.opacity });
    text(L, "labels", `${k}:t`, ver.x0 + 4, Math.max(ver.yTop, 120) + 14, "t-mono12 f-ink2", ver.label, { opacity: ver.opacity });
  }

  for (const fr of v.frontiers) {
    if (!fr.pts.length) continue;
    const d = fr.pts.map((p) => `${r1(p.x)},${r1(p.yBottom)} ${r1(p.x)},${r1(p.yTop)}`).join(" ");
    const dash = fr.lane === "door" ? "none" : fr.lane === "room" ? "6 4" : "1.5 4";
    L.el("front", `fr${fr.lane}`, "polyline", { points: d, class: "nf s-ink2", "stroke-width": 2, "stroke-dasharray": dash, "stroke-linecap": fr.lane === "wrist" ? "round" : "butt" });
    if (fr.chip) text(L, "labels", `frc${fr.lane}`, fr.chip.x, fr.chip.y, "t-mono12 f-ink2", fr.chip.text, { "text-anchor": fr.chip.anchor });
  }

  v.beams.forEach((b) => {
    const k = `bm${b.seq}`;
    const cls = `nf ${BEAM_CLASS[b.trust]}`;
    const dash = b.trust === "claimed" ? "4 3" : "none";
    const op = b.opacity * 1;
    line(L, "beam", `${k}:a`, b.x0, b.y, b.x1, b.y, cls, { "stroke-width": 2, "stroke-dasharray": dash, opacity: op });
    for (const [side, x] of [["l", b.x0], ["r", b.x1]]) {
      line(L, "beam", `${k}:${side}`, x, b.y - 5, x, b.y + 5, cls, { "stroke-width": 2, opacity: op });
      if (b.trust === "verified") L.el("beam", `${k}:${side}o`, "circle", { cx: r1(x), cy: r1(b.y), r: 8, class: "nf s-ver", "stroke-width": 1.5, opacity: op });
    }
    text(L, "labels", `${k}:k`, b.labelX, b.labelY, "t-mono12 f-ink1", b.kind, { opacity: b.opacity });
    const ar = b.arrival;
    if (ar.x > b.x1 + 2) {
      line(L, "beam", `${k}:d`, b.x1, b.y, ar.x, b.y, "s-ink2 nf", { "stroke-width": 1, "stroke-dasharray": "2 3", opacity: b.opacity });
      L.el("beam", `${k}:c`, "circle", { cx: r1(ar.x), cy: r1(b.y), r: 3, class: "f-ink2", opacity: b.opacity });
    }
    if (ar.clipped) {
      L.el("beam", `${k}:w`, "polygon", { points: `${PLANE.x1 + ARROW_W},${r1(b.y)} ${PLANE.x1},${r1(b.y - 4)} ${PLANE.x1},${r1(b.y + 4)}`, class: "f-ink2", opacity: b.opacity });
      text(L, "labels", `${k}:al`, PLANE.x1 - 8, b.y + 18, "t-mono12 f-ink1", ar.label, { "text-anchor": "end", opacity: b.opacity });
    }
  });

  if (v.duplicate) {
    const d = v.duplicate;
    line(L, "beam", "dup:a", d.x0, d.y, d.x1, d.y, "s-ink1 nf", { "stroke-width": 2, "stroke-dasharray": "4 3", opacity: d.opacity });
    line(L, "beam", "dup:l", d.x0, d.y - 5, d.x0, d.y + 5, "s-ink1 nf", { "stroke-width": 2, opacity: d.opacity });
    line(L, "beam", "dup:r", d.x1, d.y - 5, d.x1, d.y + 5, "s-ink1 nf", { "stroke-width": 2, opacity: d.opacity });
    text(L, "labels", "dup:t", d.x1 + 12, d.y + 4, "t-mono12 f-ink1", d.label, { opacity: d.opacity });
  }

  for (const c of v.callouts) {
    line(L, "front", `co${c.seq}`, c.x, c.y0, c.x, c.y1, "s-ink2 nf", { "stroke-width": 1, opacity: c.opacity });
    text(L, "labels", `co${c.seq}:t`, c.labelX, c.labelY, "t-mono12 f-ink2", c.label, { opacity: c.opacity });
  }
  if (v.stake) {
    const s = v.stake;
    line(L, "stake", "stake", s.x0, s.y, s.x1, s.y, "s-hot nf", { "stroke-width": 2, opacity: s.opacity });
    text(L, "labels", "stake:t", s.x1, s.y - 8, "t-mono12 f-hot", s.label, { "text-anchor": "end", opacity: s.opacity });
  }

  // Belief scrubber: a glass line and a 12 px band; the region above it is not yet believed.
  const sy = v.scrubber.y;
  rect(L, "tint", "tint", x0, top, x1 - x0, sy - top, "f-bg0", { opacity: 0.55 });
  rect(L, "tint", "glass", x0, sy, x1 - x0, 12, "f-ink1", { opacity: 0.06 });
  line(L, "tint", "scrub", x0 - 6, sy, x1 + 6, sy, "s-ink1 nf", { "stroke-width": 1 });
  L.end();
}
const ARROW_W = 6;

// ── Act III ──
const bitPathCache = new Map();
function bitsPath(head, cell, w = cell, h = cell) {
  const key = `${head}|${cell}|${w}|${h}`;
  let d = bitPathCache.get(key);
  if (d === undefined) {
    d = "";
    tileBits(head).forEach((row, r) => row.forEach((bit, c) => { if (bit) d += `M${r1(c * w)} ${r1(r * h)}h${r1(w)}v${r1(h)}h${r1(-w)}z`; }));
    bitPathCache.set(key, d);
  }
  return d;
}

function renderAct3(L, f, ui) {
  const a = f.act3;
  L.begin();
  const lay = a.lay;
  const k = lay.tile / 64;
  a.tiles.forEach((t) => {
    const id = `t${t.n}`;
    const g = `translate(${r1(t.x)} ${lay.y})`;
    text(L, "labels", `${id}:lane`, t.x, lay.y - 18, "t-label f-ink3", t.lane);
    rect(L, "tile", `${id}:bg`, t.x, lay.y, lay.tile, lay.tile, "f-bg2");
    if (t.mix < 1) L.el("tile", `${id}:from`, "path", { d: bitsPath(t.from, lay.cell), transform: g, class: "f-ink1", "fill-opacity": 0.85, opacity: r1(1 - t.mix) });
    L.el("tile", `${id}:to`, "path", { d: bitsPath(t.head, lay.cell), transform: g, class: "f-ink1", "fill-opacity": 0.85, opacity: r1(t.mix) });
    const border = t.border === "ok" ? "s-safe" : t.border === "fail" ? "s-hot" : "s-line2";
    rect(L, "tile", `${id}:bd`, t.x + 0.5, lay.y + 0.5, lay.tile - 1, lay.tile - 1, `nf ${border}`, { "stroke-width": 1 });
    const hovered = ui.ringTile === t.n;
    if (t.ring === "fail" || hovered) rect(L, "tile", `${id}:ring`, t.x - 4, lay.y - 4, lay.tile + 8, lay.tile + 8, `nf ${t.ring === "fail" ? "s-hot" : "s-ink1"}`, { "stroke-width": 2, rx: 2 });
    text(L, "labels", `${id}:seq`, t.x, lay.y + lay.tile + 24, "t-mono12 f-ink3", `seq ${String(t.n).padStart(2, "0")}`);
    text(L, "labels", `${id}:h`, t.x, lay.y + lay.tile + 42, "t-mono12 f-ink2", `${t.head.slice(7, 11)}…${t.head.slice(-4)}`);
    if (t.diff) {
      const w = chipWidth(t.diff) - 12;
      rect(L, "labels", `${id}:dr`, t.x, lay.y + lay.tile + 54, w, 20, "f-bg0 s-hot", { rx: 2, "stroke-width": 1 });
      text(L, "labels", `${id}:dt`, t.x + 6, lay.y + lay.tile + 68, "t-mono12 f-hot", t.diff);
    }
  });
  for (const gap of a.teeth) {
    const x = lay.x0 + (gap.gap - 1) * lay.pitch;
    const c = x + 92 * k;
    gap.open.forEach((open, j) => {
      const y = lay.y + 8 * j * k + k;
      const h = 6 * k;
      if (open) {
        rect(L, "tooth", `g${gap.gap}j${j}l`, x + 68 * k, y, c - 5 * k - (x + 68 * k), h, "f-hot");
        rect(L, "tooth", `g${gap.gap}j${j}r`, c + 5 * k, y, x + 116 * k - (c + 5 * k), h, "f-hot");
      } else {
        rect(L, "tooth", `g${gap.gap}j${j}l`, x + 68 * k, y, c + 2 * k - (x + 68 * k), h, "f-ink2", { opacity: 0.7 });
        rect(L, "tooth", `g${gap.gap}j${j}r`, c - 2 * k, y, x + 116 * k - (c - 2 * k), h, "f-ink2", { opacity: 0.7 });
      }
    });
  }
  L.end();
}

// ── background registration marks (every act): a clean void with hairline frame rules, ticks and crosses ──
function renderRegistration(L) {
  L.begin();
  const path = (key, d, cls, extra = {}) => L.el("l", key, "path", { d, class: `nf ${cls}`, "stroke-width": 1, "shape-rendering": "crispEdges", ...extra });
  const [x0, x1, yTop, yBot] = [96, 1824, 64.5, 1012.5];
  path("rules", `M${x0} ${yTop}H${x1}M${x0} ${yBot}H${x1}`, "s-line2");
  let ticks = "";
  for (let x = x0; x <= x1; x += 120) ticks += `M${x + 0.5} ${yTop}v5M${x + 0.5} ${yBot}v-5`;
  path("ticks", ticks, "s-line2");
  let crosses = "";
  for (let x = 240; x < 1920; x += 240) for (let y = 216; y < 1080; y += 216) crosses += `M${x - 5} ${y + 0.5}h11M${x + 0.5} ${y - 5}v11`;
  path("crosses", crosses, "s-ink3", { opacity: 0.35 });
  const arm = 14;
  path("brackets", `M${x0} ${yTop + arm}V${yTop}H${x0 + arm}M${x1 - arm} ${yTop}H${x1}V${yTop + arm}M${x0} ${yBot - arm}V${yBot}H${x0 + arm}M${x1 - arm} ${yBot}H${x1}V${yBot - arm}`, "s-ink3", { "stroke-width": 1.5, opacity: 0.8 });
  L.end();
}

export function createInk(svg) {
  const lattice = makeLayer(svg.querySelector("#lattice"), ["l"]);
  const a1 = makeLayer(svg.querySelector("#act1"), ["lines", "marks", "labels"]);
  const a2 = makeLayer(svg.querySelector("#act2"), ["bg", "ver", "front", "beam", "stake", "tint", "labels"]);
  const a3 = makeLayer(svg.querySelector("#act3"), ["tile", "tooth", "labels"]);
  let registered = false;
  const groups = { act1: svg.querySelector("#act1"), act2: svg.querySelector("#act2"), act3: svg.querySelector("#act3") };
  return {
    render(frame, ui = {}) {
      if (!registered) { renderRegistration(lattice); registered = true; }
      for (const name of ["act1", "act2", "act3"]) groups[name].setAttribute("opacity", String(Math.round(frame.opacity[name] * 1000) / 1000));
      if (frame.opacity.act1 > 0) renderAct1(a1, frame);
      if (frame.act2 && frame.opacity.act2 > 0) renderAct2(a2, frame);
      if (frame.act3 && frame.opacity.act3 > 0) renderAct3(a3, frame, ui);
    },
  };
}
