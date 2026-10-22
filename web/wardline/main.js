// Wardline's entry: DOM wiring, clock, input, URL parameters and the performance governor.
// The pure modules (model, director, chain) stay DOM-free; this file applies their frames to the page.
import { WARD } from "./data.js";
import { precompute } from "./chain.js";
import { DURATION, buildTimeline, frameAt, keyAction, manualDuration, nextManualView, seekCue } from "./director.js";
import { pad2 } from "./model.js";
import { createInk } from "./plane.svg.js";
import { createGlowRenderer as createGl } from "./ward.gl.js";
import { createGlowRenderer as createC2d } from "./fallback.c2d.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const timeline = buildTimeline(WARD);
const ink = createInk($("ink"));

const state = {
  t: 0, playing: false, ready: false, crypto: null, manual: null, q: 0.5, ringTile: null,
  motion: params.get("motion") !== "0" && !matchMedia("(prefers-reduced-motion: reduce)").matches,
  debug: false, hints: params.get("hints") !== "0", fullHash: "",
};
let renderer = null;
let raf = 0;
let lastNow = 0;
const intervals = [];
let slowSince = null;
let degraded = 0;

// ── layout ──
function layout() {
  const viewport = $("viewport");
  const vw = viewport.clientWidth;
  const vh = viewport.clientHeight;
  const s = Math.min(vw / 1920, vh / 1080);
  $("stage").style.transform = `translate(${Math.round((vw - 1920 * s) / 2)}px, ${Math.round((vh - 1080 * s) / 2)}px) scale(${s})`;
  if (renderer) renderer.resize(1920 * s, 1080 * s, Math.min(devicePixelRatio || 1, 2) * state.q);
  return s;
}

function freshCanvas() {
  const old = $("glow");
  const canvas = document.createElement("canvas");
  canvas.id = "glow";
  canvas.setAttribute("aria-hidden", "true");
  old.replaceWith(canvas);
  return canvas;
}

function mountRenderer(preferGl) {
  if (renderer) renderer.dispose();
  let canvas = freshCanvas();
  let r = preferGl ? createGl(canvas, { onLost: () => { mountRenderer(false); render(); } }) : null;
  if (!r) {
    canvas = freshCanvas();
    r = createC2d(canvas);
  }
  renderer = r;
  layout();
}

// ── DOM application ──
const setText = (el, value) => { if (el._v !== value) { el.textContent = value; el._v = value; } };
const setStyle = (el, prop, value) => { const key = `_s${prop}`; if (el[key] !== value) { el.style[prop] = value; el[key] = value; } };
const setClass = (el, name, on) => { if (el._c !== undefined && el._c[name] === on) return; el._c = { ...el._c, [name]: on }; el.classList.toggle(name, on); };
const op = (v) => String(Math.round(v * 1000) / 1000);

const els = {
  acts: [...document.querySelectorAll("#acts span")], seq: $("seqlabel"), sentence: $("sentence"), hud: [$("hud1"), $("hud2"), $("hud3")],
  hero: $("hero"), heroNum: $("hero-num"), lr: $("lr-num"), state: $("state"), prow: [...document.querySelectorAll(".prow")], ladder: $("ladder"),
  asN: $("as-n"), asOf: $("as-of"), asLam: $("as-lam"), asLr: $("as-lr"), asState: $("as-state"),
  checks: [...document.querySelectorAll(".check")], glass: $("glass-rows"), full: $("fullhash"), params: $("params-hash"),
  band: $("captionband"), speaker: $("cap-speaker"), capText: $("cap-text"), card: $("card"), fill: $("progress-fill"), code: $("timecode"),
  play: $("b-play"), buttons: $("buttons"), debug: $("debug"), transcript: $("transcript"),
};

function syncTranscript(count) {
  const list = els.transcript;
  while (list.children.length > count) list.lastElementChild.remove();
  for (let i = list.children.length; i < count; i += 1) {
    const li = document.createElement("li");
    li.textContent = timeline.sentences[i].text;
    list.appendChild(li);
  }
}

let glassBuilt = false;
function buildGlass(rows) {
  glassBuilt = true;
  els.glass.replaceChildren(...rows.map((row) => {
    const li = document.createElement("li");
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = row.key;
    const v = document.createElement("span");
    v.className = "v";
    if (row.chips) row.chips.forEach((c) => { const chip = document.createElement("span"); chip.className = "rchip"; chip.textContent = c; v.appendChild(chip); });
    else v.textContent = row.text;
    li.append(k, v);
    if (row.link) {
      li.className = "link";
      li.tabIndex = 0;
      const on = () => { state.ringTile = WARD.signed.intervene_at_seq; state.fullHash = row.full ?? ""; render(); };
      const off = () => { state.ringTile = null; state.fullHash = ""; render(); };
      li.addEventListener("mouseenter", on);
      li.addEventListener("focus", on);
      li.addEventListener("mouseleave", off);
      li.addEventListener("blur", off);
    }
    return li;
  }));
}

function applyFrame(frame) {
  renderer.draw(frame);
  ink.render(frame, { ringTile: state.ringTile });

  els.acts.forEach((el) => setClass(el, "on", Number(el.dataset.act) === frame.active));
  setText(els.seq, `seq ${pad2(frame.top.seq)} / ${pad2(frame.top.of)}`);
  setText(els.sentence, frame.sentence);
  syncTranscript(frame.transcript);
  setStyle(els.hud[0], "opacity", op(frame.opacity.act1));
  setStyle(els.hud[1], "opacity", op(frame.opacity.act2));
  setStyle(els.hud[2], "opacity", op(frame.opacity.act3));
  setStyle(els.card, "opacity", op(frame.opacity.card));
  setStyle(els.sentence, "opacity", op(1 - frame.opacity.card));
  setStyle(els.band, "opacity", op(1 - frame.opacity.card));

  const a = frame.act1;
  setText(els.heroNum, a.readout.lambda);
  setClass(els.hero, "intervene", a.readout.tone === "intervene");
  setClass(els.hero, "benign", a.readout.tone === "benign");
  setText(els.lr, a.readout.lr);
  setText(els.state, a.readout.state);
  els.state.dataset.state = a.readout.state;
  a.readout.posterior.forEach((pct, i) => {
    setStyle(els.prow[i].querySelector(".pfill"), "width", `${pct}%`);
    setText(els.prow[i].querySelector(".pval"), `${pct.toFixed(1)}%`);
  });
  setStyle(els.ladder, "opacity", op(a.ladder));

  if (frame.act2) {
    const s = frame.act2.side;
    setText(els.asN, pad2(s.seq));
    setText(els.asOf, ` / ${s.of}`);
    setText(els.asLam, `${s.lambda} mn`);
    setText(els.asLr, s.lr);
    setText(els.asState, s.state);
    els.asState.dataset.state = s.state;
  }
  if (frame.act3) {
    if (!glassBuilt) buildGlass(frame.act3.glassRows);
    setStyle(els.glass, "opacity", op(frame.act3.glass));
    frame.act3.checks.forEach((c, i) => {
      const row = els.checks[i];
      setStyle(row, "opacity", op(c.alpha));
      const mark = row.querySelector(".mark");
      const detail = row.querySelector(".cdetail");
      setText(mark, c.ok === true ? "✓" : c.ok === false ? "✗" : "—");
      mark.className = `mark ${c.ok === true ? "ok" : c.ok === false ? "fail" : "skip"}`;
      setText(detail, c.detail);
      detail.className = `cdetail ${c.ok === false ? "fail" : c.ok === null ? "skip" : ""}`;
    });
    setText(els.params, WARD.signed.attestation ? paramsShort() : "");
    setText(els.full, state.fullHash);
  }

  setClass(els.band, "wide", frame.active !== 1);
  if (frame.caption) {
    setText(els.speaker, frame.caption.speaker);
    setText(els.capText, frame.caption.text);
    setClass(els.capText, "note", !frame.caption.quote && frame.active !== 3);
  } else {
    setText(els.speaker, "");
    setText(els.capText, "");
  }
  setStyle(els.fill, "width", `${Math.round(frame.time.progress * 1728 * 10) / 10}px`);
  setText(els.code, frame.time.code);
  setText(els.play, state.ready ? (state.playing ? "❚❚ Space" : "▶ Space") : "verifying…");
  els.play.disabled = !state.ready;
  setClass(els.buttons, "hidden", !state.hints);
  updateDebug();
}

const record = timeline.record;
function setSeal(verified) {
  const head = record.ledger_head;
  setText($("seal-head"), `${head.slice(7, 11)}…${head.slice(-4)}`);
  setText($("seal-status"), verified ? "VERIFIED" : "NOT VERIFIED");
  $("seal-status").className = verified ? "vok" : "vfail";
  $("seal-pip").className = `pip ${verified ? "ok" : "fail"}`;
}
const paramsShort = () => `${record.params_digest.slice(0, 11)}…${record.params_digest.slice(-4)}`;

function updateDebug() {
  els.debug.hidden = !state.debug;
  if (!state.debug) return;
  const mean = intervals.length === 60 ? `${(intervals.reduce((a, b) => a + b, 0) / 60).toFixed(1)} ms` : "n/a (fewer than 60 intervals yet)";
  setText(els.debug, `renderer ${renderer.kind} · q ${state.q} · mean of the last 60 requestAnimationFrame intervals: ${mean}`);
}

// ── clock, governor, idle ──
function manualRunning() { return state.manual && state.manual.elapsed < manualDuration(state.manual.kind); }
function needsLoop() { return state.playing || manualRunning(); }

function render() {
  const manual = state.manual && state.t >= 65000 ? state.manual : null;
  applyFrame(frameAt(timeline, WARD, state.crypto, state.t, { motion: state.motion, manual }));
}

function govern(now) {
  if (!state.playing) { slowSince = null; return; }
  if (lastNow) intervals.push(now - lastNow);
  if (intervals.length > 60) intervals.shift();
  if (intervals.length < 60) return;
  const mean = intervals.reduce((a, b) => a + b, 0) / 60;
  if (mean <= 22) { slowSince = null; return; }
  slowSince ??= now;
  if (now - slowSince < 2000) return;
  slowSince = null;
  intervals.length = 0;
  degraded += 1;
  if (degraded === 1 && renderer.kind === "webgl2") { state.q = 0.35; layout(); } else if (renderer.kind === "webgl2") mountRenderer(false);
}

function tick(now) {
  raf = 0;
  const dt = Math.min(now - (lastNow || now), 50);
  govern(now);
  lastNow = now;
  if (state.playing) {
    state.t = Math.min(DURATION, state.t + dt);
    if (state.t >= DURATION) state.playing = false;
  }
  if (manualRunning()) state.manual.elapsed += dt;
  render();
  if (needsLoop()) schedule();
  else lastNow = 0;
}
function schedule() { if (!raf) raf = requestAnimationFrame(tick); }
function wake() { lastNow = 0; render(); if (needsLoop()) schedule(); }

// ── input ──
function apply(action) {
  switch (action.type) {
    case "toggle":
      if (!state.ready) return;
      if (state.t >= DURATION) { state.t = 0; state.playing = true; state.manual = null; } else state.playing = !state.playing;
      break;
    case "seek": state.t = action.to; state.manual = null; if (!action.keepPlay) state.playing = false; break;
    case "cue": state.t = seekCue(timeline, state.t, action.dir); state.playing = false; state.manual = null; break;
    case "nudge": state.t = Math.min(DURATION, Math.max(0, state.t + action.ms)); state.playing = false; state.manual = null; break;
    case "reset": state.t = 0; state.playing = false; state.manual = null; break;
    case "verify":
      if (state.t < 65000 || !state.ready) return;
      state.playing = false;
      state.t = Math.max(state.t, 66200);
      state.manual = { kind: "verify", view: state.manual?.view ?? "intact", from: state.manual?.view ?? "intact", elapsed: 0 };
      break;
    case "tamper": {
      if (state.t < 65000 || !state.ready) return;
      state.playing = false;
      state.t = Math.max(state.t, 66200);
      const from = state.manual?.view ?? "intact";
      const view = nextManualView(from);
      state.manual = { kind: view === "tampered" ? "tamper" : view === "rechained" ? "rechain" : "restore", view, from, elapsed: 0 };
      break;
    }
    case "hints": state.hints = !state.hints; break;
    case "motion": state.motion = !state.motion; break;
    case "debug": state.debug = !state.debug; break;
    case "fullscreen":
      if (document.fullscreenElement) document.exitFullscreen(); else $("viewport").requestFullscreen?.();
      break;
    default: return;
  }
  wake();
}

document.addEventListener("keydown", (e) => {
  if (e.target instanceof HTMLElement && e.target.closest("button") && (e.key === " " || e.key === "Enter")) return;
  const action = keyAction(e.key, e);
  if (!action) return;
  if (action.preventDefault) e.preventDefault();
  apply(action);
});
for (const button of document.querySelectorAll("#buttons button")) {
  button.addEventListener("click", () => { const action = keyAction(button.dataset.key); if (action) apply(action); });
}
addEventListener("resize", () => { layout(); render(); });
document.addEventListener("fullscreenchange", () => { layout(); render(); });
document.addEventListener("visibilitychange", () => { if (document.hidden) { state.playing = false; wake(); } });

// ── start ──
mountRenderer(params.get("gl") !== "0");
render();
precompute(WARD).then((crypto) => {
  state.crypto = crypto;
  setSeal(crypto.intact.checks.every((c) => c.ok === true));
  state.ready = true;
  if (params.has("t")) { state.t = Math.min(DURATION, Math.max(0, Number(params.get("t")) * 1000 || 0)); }
  if (params.get("autoplay") === "1") state.playing = true;
  wake();
}, (error) => {
  setText(els.sentence, `Verification could not start: ${error.message}`);
});
