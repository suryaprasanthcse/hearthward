// The radar page's logic, with no DOM and no WebGL so it can be tested under node. Everything here is a pure
// function of the synthetic scenario (radar_data.js) and the cursor.

import { bytesToHex } from "./encoding.js";

const encoder = new TextEncoder();

// ── gaze ────────────────────────────────────────────────────────────────────────────────────────────────

// The pupil follows the cursor: the same angle as the vector from the eye to the cursor, and a distance that grows
// with the cursor's distance but never leaves `maxOffset` (the iris stays inside the eye).
export function gaze(center, mouse, maxOffset, reach = 400) {
  const vx = mouse.x - center.x;
  const vy = mouse.y - center.y;
  const distance = Math.hypot(vx, vy);
  const angle = Math.atan2(vy, vx);
  const offset = Math.min(maxOffset, maxOffset * (distance / reach));
  return {
    dx: Math.cos(angle) * offset, dy: Math.sin(angle) * offset, offset, distance,
    angleDeg: ((angle * 180) / Math.PI + 360) % 360, focusX: Math.round(mouse.x), focusY: Math.round(mouse.y),
  };
}

// ── evidence ────────────────────────────────────────────────────────────────────────────────────────────

export function lambdaAt(steps, seq) {
  if (seq <= 0) return 0;
  const step = steps.filter((s) => s.seq <= seq).at(-1);
  return step ? step.lambda_mnats : 0;
}

// The Wald verdict at Lambda: crossing A intervenes, crossing B makes a dismissal eligible (it still needs the
// watermarks and certainly_* relations), anything between continues.
export function verdict(lambda, bounds) {
  if (lambda >= bounds.a) return "INTERVENE";
  if (lambda <= bounds.b) return "DISMISS-ELIGIBLE";
  return "CONTINUE";
}

export function crossedA(previous, next, bounds) {
  return previous < bounds.a && next >= bounds.a;
}

// Lambda to a point on the gauge arc, clamped to the scale.
export function gaugeFraction(lambda, scale) {
  return Math.min(1, Math.max(0, (lambda - scale.min) / (scale.max - scale.min)));
}

export function arcPoint(cx, cy, r, fraction, startDeg = 135, sweepDeg = 270) {
  const rad = ((startDeg + sweepDeg * fraction) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

// A telemetry trace made from the real per-event deltas: each event is a pulse of height |delta| that rings down,
// so the waveform moves when the evidence moves and is flat when it does not. Deterministic; no random noise.
export function deltaWaveform(steps, seq, samples = 120) {
  const pulses = steps.filter((s) => s.seq <= seq && s.delta_mnats !== 0);
  const out = new Array(samples).fill(0);
  const total = Math.max(1, steps.length);
  for (const pulse of pulses) {
    const at = Math.floor(((pulse.seq - 1) / total) * samples);
    for (let i = 0; at + i < samples; i += 1) {
      out[at + i] += (pulse.delta_mnats / 1000) * Math.exp(-i / 8) * Math.cos(i * 1.3);
    }
  }
  return out;
}

// ── bitemporal state ───────────────────────────────────────────────────────────────────────────────────

// Which envelopes are believed at ledger sequence `seq`: those appended by then (belief time [ts, te) with ts = seq).
export function visibleAt(envelopes, seq) {
  return envelopes.filter((e) => e.seq <= seq);
}

// W_src = max(basis_src) - lateness_src, where the basis is src_time for Ring and received_at for Bee and Alexa+.
export function watermarks(envelopes, seq, lateness) {
  const out = { ring: null, bee: null, alexa: null };
  for (const e of visibleAt(envelopes, seq)) {
    const basis = e.source === "ring" ? e.src_time : e.received_at;
    if (basis === null || !(e.source in out)) continue;
    out[e.source] = Math.max(out[e.source] ?? -Infinity, basis);
  }
  for (const k of Object.keys(out)) out[k] = out[k] === null ? null : out[k] - lateness[k];
  return out;
}

export function timeAxis(envelopes) {
  return {
    min: Math.min(...envelopes.map((e) => e.valid.lo)),
    max: Math.max(...envelopes.map((e) => e.valid.hi)),
  };
}

// A time to an x position, clamped to the plot. `clamped` says the true position lies off the plot, so the page can
// draw an arrow and the real offset instead of a marker that would be misleading.
export function xFor(t, axis, left, width) {
  const raw = (t - axis.min) / (axis.max - axis.min);
  const clamped = raw < 0 || raw > 1;
  return { x: left + Math.min(1, Math.max(0, raw)) * width, clamped, side: raw < 0 ? "left" : raw > 1 ? "right" : null };
}

export const LANES = { ring: "door", alexa: "room", bee: "wrist" };

// ── the ladder ──────────────────────────────────────────────────────────────────────────────────────────

// Once Lambda crosses A the InterventionLadder starts L1 and L2; L3 (voice) is deferred, as in blueprint section 8.
export function ladderState(seq, interveneSeq) {
  if (seq < interveneSeq) return { l1: "STANDBY", l2: "STANDBY", l3: "DEFERRED", active: false };
  return { l1: "SENT", l2: "ACTIVE", l3: "DEFERRED", active: true };
}

// ── hashes ──────────────────────────────────────────────────────────────────────────────────────────────

export async function sha256Hex(text) {
  return bytesToHex(new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", encoder.encode(text))));
}

// What hovering a pyramid tier shows: a chain of real SHA-256 digests, each over the previous one and the tier's
// name, starting from the ledger head at the scrubbed sequence.
export async function tierCascade(tier, head, count = 6) {
  const lines = [];
  let previous = head;
  for (let i = 0; i < count; i += 1) {
    previous = await sha256Hex(`${previous}|${tier}|${i}`);
    lines.push(previous);
  }
  return lines;
}

export function clock(msFromStart) {
  const s = Math.max(0, msFromStart) / 1000;
  return `+${String(Math.floor(s / 60)).padStart(2, "0")}:${(s % 60).toFixed(1).padStart(4, "0")}`;
}
