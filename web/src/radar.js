// HEARTHWARD // Bitemporal Causal Radar. Everything shown is a replay of one synthetic orrery run (radar_data.js):
// the evidence Lambda, the ledger and its hash chain, the valid-time intervals and the watermarks are the
// reference simulator's, recomputed here as of whichever ledger sequence the scrubber selects.

import { SCENARIO } from "./radar_data.js";
import { replayLedger, verifyBundle } from "./attest.js";
import { createFluid } from "./fluid.js";
import * as L from "./radar_logic.js";

const NS = "http://www.w3.org/2000/svg";
const $ = (selector) => document.querySelector(selector);
const BOUNDS = SCENARIO.thresholds_mnats;
const N = SCENARIO.envelopes.length;
const STEPS = SCENARIO.steps;
const ENV = SCENARIO.envelopes;
const AXIS = L.timeAxis(ENV);
const LATE = SCENARIO.temporal.lateness_ms;
const COLORS = { ring: "#00ffff", bee: "#ffb000", alexa: "#ff6a3d" };
const TEAL = [0, 0.32, 0.34];
const AMBER = [1.0, 0.27, 0.0];

function svg(tag, attrs, parent, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs ?? {})) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  parent?.append(node);
  return node;
}
function html(tag, attrs, parent, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs ?? {})) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  parent?.append(node);
  return node;
}
const signed = (ms) => `${ms < 0 ? "−" : "+"}${L.clock(Math.abs(ms)).slice(1)}`;
const fmt = (n) => (n < 0 ? "−" : "") + Math.abs(n).toLocaleString("en-US");

const state = { seq: N, playing: false, timer: null, hot: null };

// ── the fluid ──────────────────────────────────────────────────────────────────────────────────────────

const canvas = $("#fluid");
let fluid = null;
try {
  fluid = createFluid(canvas, { simRes: 128, dyeRes: 512 });
} catch (error) {
  console.warn("fluid unavailable:", error.message);
}
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let fluidOn = Boolean(fluid) && !reducedMotion;
let fps = 0;
const toggle = $("#fluid-toggle");

function fitFluid() {
  if (!fluid) return;
  const width = Math.min(innerWidth, 1600);
  fluid.resize(width, Math.max(1, Math.round((width * innerHeight) / innerWidth)));
}

function toggleLabel() {
  toggle.textContent = !fluid ? "[ FLUID DYNAMICS: UNAVAILABLE ]" : fluidOn ? `[ FLUID DYNAMICS: ACTIVE (${fps || "--"} FPS) ]` : `[ FLUID DYNAMICS: PAUSED${reducedMotion ? " · REDUCED MOTION" : ""} ]`;
  toggle.setAttribute("aria-pressed", String(fluidOn));
}
toggle.addEventListener("click", () => {
  if (!fluid) return;
  fluidOn = !fluidOn;
  toggleLabel();
});

const mouse = { x: innerWidth * 0.6, y: innerHeight * 0.4, px: null, py: null };
addEventListener("pointermove", (event) => {
  if (mouse.px !== null && fluidOn) {
    const dx = event.clientX - mouse.px;
    const dy = event.clientY - mouse.py;
    const speed = Math.hypot(dx, dy);
    if (speed > 0.5) fluid.splat(event.clientX / innerWidth, event.clientY / innerHeight, dx * 14, dy * 14, TEAL.map((c) => c * Math.min(1, 0.4 + speed / 40)));
  }
  mouse.px = mouse.x = event.clientX;
  mouse.py = mouse.y = event.clientY;
});

// A crimson-amber shock from a screen point: the fluid layer takes a radial pressure impulse.
function shock(x, y, strength = 1) {
  if (fluid && fluidOn) fluid.pulse(x / innerWidth, y / innerHeight, AMBER, strength, 22);
}
function pulseAt(element, rgb, strength = 0.6) {
  if (!fluid || !fluidOn) return;
  const box = element.getBoundingClientRect();
  fluid.pulse((box.left + box.width / 2) / innerWidth, (box.top + box.height / 2) / innerHeight, rgb, strength, 14);
}

// ── the pyramid and the eye ──────────────────────────────────────────────────────────────────────────────

const pyramid = $("#pyramid");
const APEX = { x: 320, y: 112 };
const BASE_Y = 520;
const HALF = 282;
const edge = (y) => (HALF * (y - APEX.y)) / (BASE_Y - APEX.y);
const TIERS = [
  { name: "MDCCLXXVI", y0: 152, y1: 238, hover: "" },
  { name: "ANNUIT COEPTIS", y0: 238, y1: 332, hover: "cyan" },
  { name: "TRUST CAUSALIS", y0: 332, y1: 426, hover: "" },
  { name: "ORDO CAUSALIS", y0: 426, y1: BASE_Y, hover: "cyan" },
];

svg("polygon", { class: "cap", points: `${APEX.x},${APEX.y} ${APEX.x + edge(152)},152 ${APEX.x - edge(152)},152` }, pyramid);
const tierNodes = TIERS.map((tier, i) => {
  const a = edge(tier.y0);
  const b = edge(tier.y1);
  const polygon = svg("polygon", {
    class: `tier ${i % 2 ? "alt" : ""} ${tier.hover}`.trim(), tabindex: 0, role: "button",
    "aria-label": `Pyramid tier ${tier.name}: show its SHA-256 chain`,
    points: `${APEX.x - a},${tier.y0} ${APEX.x + a},${tier.y0} ${APEX.x + b},${tier.y1} ${APEX.x - b},${tier.y1}`,
  }, pyramid);
  const mid = ((a + b) / 2) * 2;
  svg("text", { class: "tier-text", x: APEX.x, y: (tier.y0 + tier.y1) / 2 + 6, textLength: Math.round(mid * 0.7), lengthAdjust: "spacingAndGlyphs" }, pyramid, tier.name);
  polygon.addEventListener("pointerenter", () => activateTier(i));
  polygon.addEventListener("focus", () => activateTier(i));
  polygon.addEventListener("pointerleave", () => deactivateTier(i));
  polygon.addEventListener("blur", () => deactivateTier(i));
  return polygon;
});
svg("polygon", { class: "face", points: `${APEX.x},${APEX.y} ${APEX.x + HALF},${BASE_Y} ${APEX.x},${BASE_Y}` }, pyramid);

// The ledger as cubes on the top tiers: one per envelope, coloured by source, faded when not yet believed.
const cubes = ENV.map((e, i) => {
  const row = Math.floor(i / 6);
  const col = i % 6;
  return svg("rect", { class: "cube", x: APEX.x - 40 + col * 15, y: 172 + row * 28, width: 10, height: 10, fill: COLORS[e.source], transform: `rotate(45 ${APEX.x - 35 + col * 15} ${177 + row * 28})` }, pyramid);
});

const eye = svg("g", { id: "eye" }, pyramid);
svg("circle", { class: "eye-ring", cx: 320, cy: 62, r: 56 }, eye);
svg("circle", { class: "eye-ring rev", cx: 320, cy: 62, r: 44 }, eye);
svg("circle", { id: "eye-anchor", cx: 320, cy: 62, r: 1, fill: "none" }, eye);
const beams = [0, 1, 2].map(() => svg("line", { class: "eye-beam", x1: 320, y1: 62, x2: 320, y2: 62 }, eye));
svg("path", { class: "eye-lid", d: "M 262 62 Q 320 14 378 62 Q 320 110 262 62 Z" }, eye);
svg("circle", { class: "eye-iris", cx: 320, cy: 62, r: 20 }, eye);
const irisGroup = svg("g", {}, eye);
const iris = svg("circle", { class: "eye-iris", cx: 320, cy: 62, r: 17 }, irisGroup);
const pupil = svg("circle", { class: "eye-pupil", cx: 320, cy: 62, r: 7 }, irisGroup);
const glint = svg("circle", { class: "eye-glint", cx: 316, cy: 58, r: 2 }, irisGroup);
const eyeText = [0, 1, 2].map((i) => svg("text", { class: `eye-text ${i ? "dim" : ""}`, x: 392, y: 40 + i * 14 }, eye, ""));
svg("text", { class: "eye-text dim", x: 392, y: 26 }, eye, "CAUSAL EYE");

const overlay = { line: $("#gaze-line"), ring: $("#gaze-ring"), dot: $("#gaze-dot") };
function updateEye() {
  const box = $("#eye-anchor").getBoundingClientRect();
  const center = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
  const g = L.gaze(center, mouse, 15, 420);
  for (const node of [iris, pupil]) node.setAttribute("transform", `translate(${g.dx.toFixed(2)} ${g.dy.toFixed(2)})`);
  glint.setAttribute("transform", `translate(${(g.dx * 1.05).toFixed(2)} ${(g.dy * 1.05).toFixed(2)})`);
  const base = Math.atan2(mouse.y - center.y, mouse.x - center.x);
  beams.forEach((beam, i) => {
    const a = base + (i - 1) * 0.22;
    beam.setAttribute("x2", (320 + Math.cos(a) * (70 + i * 10)).toFixed(1));
    beam.setAttribute("y2", (62 + Math.sin(a) * (70 + i * 10)).toFixed(1));
  });
  overlay.line.setAttribute("x1", center.x);
  overlay.line.setAttribute("y1", center.y);
  overlay.line.setAttribute("x2", mouse.x);
  overlay.line.setAttribute("y2", mouse.y);
  for (const node of [overlay.ring, overlay.dot]) { node.setAttribute("cx", mouse.x); node.setAttribute("cy", mouse.y); }
  const pad = (n, w) => String(Math.abs(n)).padStart(w, "0");
  eyeText[0].textContent = `FOCUS  X:${pad(g.focusX, 4)}  Y:${pad(g.focusY, 4)}`;
  eyeText[1].textContent = `GAZE  θ ${g.angleDeg.toFixed(1).padStart(5, "0")}°  r ${pad(Math.round(g.distance), 4)}px`;
  eyeText[2].textContent = `IRIS OFFSET ${g.offset.toFixed(1)} / 15.0`;
}

// ── audit panel: hashes and proofs ───────────────────────────────────────────────────────────────────────

const hashBox = $("#hashes");
let cascadeToken = 0;
function showHeads() {
  hashBox.replaceChildren();
  html("span", { class: "h-title" }, hashBox, `SHA-256 LEDGER HEADS (seq ${Math.max(1, state.seq - 2)}..${state.seq})`);
  for (const e of L.visibleAt(ENV, state.seq).slice(-3)) html("code", { title: `head of seq ${e.seq}` }, hashBox, `${e.seq}: ${e.head}`);
}
async function activateTier(i) {
  state.hot = i;
  tierNodes[i].classList.add("hot");
  pulseAt(tierNodes[i], i % 2 ? TEAL.map((c) => c * 2.4) : AMBER, 0.7);
  const token = ++cascadeToken;
  const head = ENV[Math.max(0, state.seq - 1)].head;
  const lines = await L.tierCascade(TIERS[i].name, head, 6);
  if (token !== cascadeToken) return;
  hashBox.replaceChildren();
  html("span", { class: "h-title" }, hashBox, `${TIERS[i].name} · SHA-256(prev | tier | n) from head ${head.slice(7, 19)}…`);
  lines.forEach((line, n) => setTimeout(() => token === cascadeToken && html("code", { class: `new ${i % 2 ? "" : "amber"}` }, hashBox, `${n}: ${line}`), n * 90));
}
function deactivateTier(i) {
  tierNodes[i].classList.remove("hot");
  if (state.hot === i) {
    state.hot = null;
    cascadeToken += 1;
    showHeads();
  }
}

const proofList = $("#proofs");
function proofItem(cls, mark, text, small) {
  const li = html("li", { class: cls }, proofList);
  html("span", { class: "mark" }, li, mark);
  const body = html("span", {}, li, text);
  if (small) html("small", {}, body, small);
}
let attestation = null;
verifyBundle({ ...SCENARIO.attestation, ledger: ENV }).then((result) => { attestation = result; render(); });

async function renderProofs() {
  const seq = state.seq;
  const chain = await replayLedger(L.visibleAt(ENV, seq));
  if (seq !== state.seq) return;
  proofList.replaceChildren();
  const passkey = SCENARIO.proof_required_at_seq;
  if (passkey !== null && seq >= passkey) {
    proofItem("warn", "!", "PASSKEY PROOF: REQUIRED, NONE PRESENTED", "an S- request arrived (HX-02): blocked until a circle member signs the action hash");
  } else {
    proofItem("idle", "·", "PASSKEY PROOF: NO S- REQUEST YET", "a passkey is only asked for when something would lower protection");
  }
  const decided = seq >= SCENARIO.intervene_at_seq;
  const sig = attestation?.checks.find((c) => c.name === "decision signature");
  if (!decided) proofItem("idle", "·", "ATTESTATION SIGNATURE: NO DECISION YET", "a decision record is signed when Λ crosses A");
  else if (sig?.ok) proofItem("ok", "✓", "ATTESTATION SIGNATURE: ECDSA_SHA_256 VERIFIED", "checked here in your browser; signed with a synthetic demo key, KMS in production");
  else proofItem("bad", "✗", "ATTESTATION SIGNATURE: NOT VERIFIED", sig?.detail ?? "checking…");
  proofItem(chain.length === 0 ? "ok" : "bad", chain.length === 0 ? "✓" : "✗", `SHA-256 LEDGER HASH CHAIN ${chain.length === 0 ? "VERIFIED" : "BROKEN"}`,
    chain.length === 0 ? `${seq} envelope${seq === 1 ? "" : "s"} replayed from the genesis head in this page` : chain[0]);
}

// ── the evidence gauge, trend and waveforms ──────────────────────────────────────────────────────────────

const gaugeSvg = $("#gauge");
const SCALE = { min: -4000, max: 7000 };
const GC = { x: 150, y: 158, r: 104 };
function arcD(f0, f1) {
  const [a, b] = f0 <= f1 ? [f0, f1] : [f1, f0];
  const p0 = L.arcPoint(GC.x, GC.y, GC.r, a);
  const p1 = L.arcPoint(GC.x, GC.y, GC.r, b);
  return `M ${p0.x.toFixed(2)} ${p0.y.toFixed(2)} A ${GC.r} ${GC.r} 0 ${(b - a) * 270 > 180 ? 1 : 0} 1 ${p1.x.toFixed(2)} ${p1.y.toFixed(2)}`;
}
const gdefs = svg("defs", {}, gaugeSvg);
const grad = svg("linearGradient", { id: "gauge-grad", gradientUnits: "userSpaceOnUse", x1: 40, y1: 0, x2: 260, y2: 0 }, gdefs);
[["0", "#00ffff"], ["0.55", "#ffd000"], ["1", "#ff3b1a"]].forEach(([o, c]) => svg("stop", { offset: o, "stop-color": c }, grad));
svg("path", { class: "gauge-track", d: arcD(0, 1) }, gaugeSvg);
const gaugeFill = svg("path", { class: "gauge-fill", d: "" }, gaugeSvg);
for (const [key, value] of [["a", BOUNDS.a], ["b", BOUNDS.b]]) {
  const f = L.gaugeFraction(value, SCALE);
  const inner = L.arcPoint(GC.x, GC.y, GC.r - 14, f);
  const outer = L.arcPoint(GC.x, GC.y, GC.r + 14, f);
  const label = L.arcPoint(GC.x, GC.y, GC.r + 27, f);
  svg("line", { class: `gauge-bound ${key}`, x1: inner.x, y1: inner.y, x2: outer.x, y2: outer.y }, gaugeSvg);
  svg("text", { class: `gauge-label ${key}`, x: label.x, y: label.y + 4, "text-anchor": "middle" }, gaugeSvg, `${key.toUpperCase()} ${value > 0 ? "+" : "−"}${Math.abs(value)}`);
}
const needle = svg("circle", { class: "gauge-needle", r: 5, cx: 0, cy: 0 }, gaugeSvg);
svg("text", { class: "gauge-cap", x: GC.x, y: 112 }, gaugeSvg, "CURRENT EVIDENCE (Λ)");
const gaugeNum = svg("text", { class: "gauge-num", x: GC.x, y: 162 }, gaugeSvg, "0");
svg("text", { class: "gauge-unit", x: GC.x, y: 184 }, gaugeSvg, "mnats");
const gaugeVerdict = svg("text", { class: "gauge-verdict", x: GC.x, y: 212 }, gaugeSvg, "");

function renderGauge(lambda) {
  const f = L.gaugeFraction(lambda, SCALE);
  const zero = L.gaugeFraction(0, SCALE);
  gaugeFill.setAttribute("d", Math.abs(f - zero) < 0.002 ? "" : arcD(zero, f));
  const p = L.arcPoint(GC.x, GC.y, GC.r, f);
  needle.setAttribute("cx", p.x);
  needle.setAttribute("cy", p.y);
  gaugeNum.textContent = fmt(lambda);
  const v = L.verdict(lambda, BOUNDS);
  gaugeVerdict.textContent = v === "INTERVENE" ? "INTERVENE · Λ ≥ A" : v === "DISMISS-ELIGIBLE" ? "DISMISS-ELIGIBLE · Λ ≤ B" : "CONTINUE · B < Λ < A";
  gaugeVerdict.setAttribute("class", `gauge-verdict ${v === "INTERVENE" ? "hot" : v === "DISMISS-ELIGIBLE" ? "cool" : ""}`);
  gaugeFill.classList.toggle("hot", v === "INTERVENE");
}

const trend = $("#trend");
const TX = { l: 44, r: 706, t: 14, b: 172 };
const TY = { min: -4000, max: 9000 };
const tx = (seq) => TX.l + ((seq - 1) / Math.max(1, N - 1)) * (TX.r - TX.l);
const ty = (v) => TX.b - ((v - TY.min) / (TY.max - TY.min)) * (TX.b - TX.t);
function renderTrend() {
  trend.replaceChildren();
  for (const [v, cls, text] of [[BOUNDS.a, "a", `A = +${BOUNDS.a}`], [BOUNDS.b, "b", `B = −${Math.abs(BOUNDS.b)}`], [0, "zero", "0"]]) {
    svg("line", { class: `trend-bound ${cls}`.replace("trend-bound zero", "trend-zero"), x1: TX.l, x2: TX.r, y1: ty(v), y2: ty(v) }, trend);
    svg("text", { class: `trend-text ${cls}`, x: TX.r - 4, y: ty(v) - 4, "text-anchor": "end" }, trend, text);
  }
  const all = STEPS.map((s) => `${tx(s.seq)},${ty(s.lambda_mnats)}`).join(" ");
  svg("polyline", { class: "trend-ghost", points: all }, trend);
  const seen = STEPS.filter((s) => s.seq <= state.seq);
  const line = seen.map((s) => `${tx(s.seq)},${ty(s.lambda_mnats)}`).join(" ");
  if (seen.length) {
    svg("polygon", { class: "trend-area", points: `${tx(1)},${ty(0)} ${line} ${tx(seen.at(-1).seq)},${ty(0)}` }, trend);
    svg("polyline", { class: "trend-line", points: line }, trend);
    for (const s of seen) svg("circle", { cx: tx(s.seq), cy: ty(s.lambda_mnats), r: s.seq === state.seq ? 4.5 : 2.5, fill: s.seq === state.seq ? "#ffb000" : "#00ffff" }, trend);
  }
  svg("line", { class: "trend-now", x1: tx(state.seq), x2: tx(state.seq), y1: TX.t, y2: TX.b }, trend);
  svg("line", { class: "trend-now", x1: tx(SCENARIO.intervene_at_seq), x2: tx(SCENARIO.intervene_at_seq), y1: TX.t, y2: TX.b, stroke: "#ff4500", opacity: 0.5 }, trend);
  svg("text", { class: "trend-text a", x: tx(SCENARIO.intervene_at_seq) + 4, y: TX.t + 10 }, trend, `INTERVENE @ seq ${SCENARIO.intervene_at_seq}`);
  STEPS.forEach((s) => svg("text", { class: "trend-text", x: tx(s.seq), y: TX.b + 13, "text-anchor": "middle" }, trend, s.seq));
}

function drawWave(canvasEl, values, color) {
  const ctx = canvasEl.getContext("2d");
  const { width, height } = canvasEl;
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(255,255,255,.18)";
  ctx.beginPath(); ctx.moveTo(0, height / 2); ctx.lineTo(width, height / 2); ctx.stroke();
  const peak = Math.max(1e-6, ...values.map(Math.abs));
  ctx.strokeStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 6; ctx.lineWidth = 1.5;
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = (i / Math.max(1, values.length - 1)) * width;
    const y = height / 2 - (v / peak) * (height / 2 - 4);
    if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
  });
  ctx.stroke();
  ctx.shadowBlur = 0;
}
function renderWaves() {
  drawWave($("#wave-delta"), L.deltaWaveform(STEPS, state.seq, 220), "#ffb000");
  const seen = L.visibleAt(ENV, state.seq);
  const gaps = seen.slice(1).map((e, i) => e.received_at - seen[i].received_at);
  const cadence = new Array(220).fill(0);
  gaps.forEach((g, i) => { const at = Math.floor((i / N) * 220); for (let k = 0; k < 6 && at + k < 220; k += 1) cadence[at + k] = (g / 1000) * Math.sin((k / 6) * Math.PI); });
  drawWave($("#wave-cadence"), cadence, "#00ffff");
}

// ── the ladder ───────────────────────────────────────────────────────────────────────────────────────────

function renderLadder() {
  const s = L.ladderState(state.seq, SCENARIO.intervene_at_seq);
  const map = { l1: s.l1, l2: s.l2, l3: s.l3 };
  for (const [id, value] of Object.entries(map)) {
    const node = $(`#${id}`);
    node.className = `step ${value === "ACTIVE" ? "active" : value === "SENT" ? "sent" : value === "DEFERRED" ? "deferred" : ""}`;
    node.querySelector(".state").textContent = value === "DEFERRED" ? "DEFERRED (voice call not built)" : value;
  }
  const note = $("#ladder-note");
  note.textContent = s.active ? "ACTIVE · TARGET TRACKING PROCESS" : "STANDBY · Λ BELOW A";
  note.classList.toggle("on", s.active);
}

// ── bitemporal orbits ────────────────────────────────────────────────────────────────────────────────────

const orbit = $("#orbit-svg");
const LANES = [{ source: "ring", y: 78, label: "DOOR", sub: "(Ring)" }, { source: "alexa", y: 146, label: "ROOM", sub: "(Alexa+)" }, { source: "bee", y: 214, label: "WRIST", sub: "(Bee)" }];
const PLOT = { left: 160, width: 770 };
const KIND = { "door.motion": "motion", "door.button": "button", "wrist.conversation": "convo", "wrist.utterance": "utter", "room.consult": "consult", "room.s_minus": "s_minus", "room.concern": "concern" };

function renderOrbits() {
  orbit.replaceChildren();
  const defs2 = svg("defs", {}, orbit);
  const mark = svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 8, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto" }, defs2);
  svg("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "lane-arrow" }, mark);
  const wm = L.watermarks(ENV, state.seq, LATE);
  LANES.forEach((lane) => {
    svg("text", { class: "lane-label", x: 16, y: lane.y + 2 }, orbit, lane.label);
    svg("text", { class: "lane-sub", x: 16, y: lane.y + 17 }, orbit, lane.sub);
    svg("line", { class: "lane-line", x1: 140, y1: lane.y, x2: 946, y2: lane.y, "marker-end": "url(#arrow)" }, orbit);
  });
  for (let t = 0; t <= AXIS.max - AXIS.min; t += 15000) {
    const x = PLOT.left + (t / (AXIS.max - AXIS.min)) * PLOT.width;
    svg("text", { class: "axis-text", x, y: 244 }, orbit, L.clock(t));
  }
  const tip = svg("g", { class: "tipg", visibility: "hidden" });
  const tipBg = svg("rect", { class: "tip-bg", rx: 3, height: 34 }, tip);
  const tip1 = svg("text", { class: "tip", x: 8, y: 14 }, tip, "");
  const tip2 = svg("text", { class: "tip", x: 8, y: 28 }, tip, "");
  ENV.forEach((e, i) => {
    const lane = LANES.find((l) => l.source === e.source);
    const visible = e.seq <= state.seq;
    const mid = L.xFor((e.valid.lo + e.valid.hi) / 2, AXIS, PLOT.left, PLOT.width).x;
    const g = svg("g", { class: `evt ${visible ? "" : "ghost"} ${e.seq === state.seq ? "latest" : ""}`, color: COLORS[e.source], tabindex: 0, "aria-label": `${KIND[e.kind]} at seq ${e.seq}` }, orbit);
    svg("circle", { cx: mid, cy: lane.y, r: 6, fill: COLORS[e.source] }, g);
    svg("text", { class: "evt-text", x: mid, y: lane.y + 20 + (i % 2) * 11 }, g, `${KIND[e.kind] ?? e.kind}`);
    if (visible) {
      const x0 = L.xFor(e.valid.lo, AXIS, PLOT.left, PLOT.width).x;
      const x1 = L.xFor(e.valid.hi, AXIS, PLOT.left, PLOT.width).x;
      const y = lane.y - 14 - (i % 3) * 8;
      svg("path", { class: "bracket", d: `M ${x0} ${y + 6} L ${x0} ${y} L ${x1} ${y} L ${x1} ${y + 6}`, stroke: COLORS[e.source] }, orbit);
      if (e.seq === state.seq) {
        svg("text", { class: "bracket-text", x: x0 - 2, y: y - 3, "text-anchor": "end" }, orbit, "[vf_lo");
        svg("text", { class: "bracket-text", x: x1 + 2, y: y - 3 }, orbit, "vf_hi]");
      }
    }
    const show = () => {
      tip1.textContent = `${e.kind}  seq ${e.seq}${visible ? "" : "  (not yet believed)"}`;
      tip2.textContent = `[vf_lo, vf_hi] = [${L.clock(e.valid.lo - AXIS.min)}, ${L.clock(e.valid.hi - AXIS.min)}]`;
      const w = Math.max(tip1.getComputedTextLength(), tip2.getComputedTextLength()) + 16;
      tipBg.setAttribute("width", w);
      tip.setAttribute("transform", `translate(${Math.min(mid + 10, 950 - w)} ${lane.y + 26})`);
      tip.setAttribute("visibility", "visible");
      orbit.append(tip);
    };
    g.addEventListener("pointerenter", show);
    g.addEventListener("focus", show);
    g.addEventListener("pointerleave", () => tip.setAttribute("visibility", "hidden"));
    g.addEventListener("blur", () => tip.setAttribute("visibility", "hidden"));
  });
  const names = { ring: "W_ring", bee: "W_bee", alexa: "W_alexa" };
  let stack = 0;
  for (const source of ["ring", "bee", "alexa"]) {
    if (wm[source] === null) continue;
    const pos = L.xFor(wm[source], AXIS, PLOT.left, PLOT.width);
    const x = pos.clamped ? PLOT.left - 10 : pos.x;
    svg("line", { class: `wm ${source}`, x1: x, x2: x, y1: 26, y2: 232 }, orbit);
    const text = pos.clamped ? `◀ ${names[source]} = start ${signed(wm[source] - AXIS.min)}` : `${names[source]} ${signed(wm[source] - AXIS.min)}`;
    svg("text", { class: `wm-text ${source}`, x: x + 5, y: 22 + stack * 0, transform: pos.clamped ? `translate(0 ${stack * 13})` : "" }, orbit, text);
    stack += pos.clamped ? 1 : 0;
  }
}

// ── everything that depends on the belief sequence ────────────────────────────────────────────────────────

const seqInput = $("#seq");
function render() {
  const lambda = L.lambdaAt(STEPS, state.seq);
  renderGauge(lambda);
  renderTrend();
  renderWaves();
  renderLadder();
  renderOrbits();
  cubes.forEach((cube, i) => cube.classList.toggle("ghost", ENV[i].seq > state.seq));
  $("#seq-out").textContent = `seq ${state.seq}`;
  seqInput.value = state.seq;
  if (state.hot === null) showHeads();
  renderProofs();
  return lambda;
}
function setSeq(next) {
  const before = L.lambdaAt(STEPS, state.seq);
  state.seq = Math.min(N, Math.max(1, next));
  const lambda = render();
  if (L.crossedA(before, lambda, BOUNDS)) {
    const box = gaugeSvg.getBoundingClientRect();
    shock(box.left + box.width / 2, box.top + box.height / 2, 1.2);
    gaugeSvg.classList.remove("flash");
    void gaugeSvg.getBoundingClientRect();
    gaugeSvg.classList.add("flash");
  }
}
seqInput.max = N;
$("#seq-max").textContent = N;
$("#scenario-name").textContent = `${SCENARIO.scenario.toUpperCase()} (SEED ${SCENARIO.seed})`;
seqInput.addEventListener("input", () => setSeq(Number(seqInput.value)));
$("#play").addEventListener("click", () => {
  if (state.playing) { clearInterval(state.timer); state.playing = false; $("#play").textContent = "▶ REPLAY"; return; }
  state.playing = true;
  $("#play").textContent = "❚❚ PAUSE";
  setSeq(1);
  state.timer = setInterval(() => {
    if (state.seq >= N) { clearInterval(state.timer); state.playing = false; $("#play").textContent = "▶ REPLAY"; return; }
    setSeq(state.seq + 1);
  }, 850);
});

$("#bundle-link").href = URL.createObjectURL(new Blob([JSON.stringify({ ...SCENARIO.attestation, ledger: ENV }, null, 2)], { type: "application/json" }));

// ── the frame loop ───────────────────────────────────────────────────────────────────────────────────────

let frames = 0;
let windowStart = performance.now();
function frame(now) {
  requestAnimationFrame(frame);
  frames += 1;
  if (now - windowStart >= 1000) {
    fps = Math.round((frames * 1000) / (now - windowStart));
    frames = 0;
    windowStart = now;
    toggleLabel();
  }
  if (fluid && fluidOn) {
    if (Math.floor(now / 2600) !== Math.floor((now - 16) / 2600)) {  // a gentle swirl now and then keeps the void alive
      const a = (now / 2600) * 1.7;
      fluid.splat(0.5 + 0.35 * Math.cos(a), 0.55 + 0.3 * Math.sin(a * 1.3), Math.cos(a) * 300, Math.sin(a) * 300, TEAL.map((c) => c * 0.7), 0.004);
    }
    fluid.step();
    fluid.draw();
  }
  updateEye();
}

addEventListener("resize", fitFluid);
fitFluid();
if (fluid && !fluidOn) {  // paused from the start (reduced motion): settle a still frame so the void is not empty
  fluid.pulse(0.3, 0.3, TEAL.map((c) => c * 2.2), 0.8, 12);
  fluid.pulse(0.72, 0.5, TEAL.map((c) => c * 2.2), 0.6, 12);
  for (let i = 0; i < 70; i += 1) fluid.step();
  fluid.draw();
}
toggleLabel();
render();
requestAnimationFrame(frame);
setTimeout(() => pulseAt($("#eye-anchor"), TEAL.map((c) => c * 2.5), 0.8), 400);
