// Claim 1: "It catches the scam with a bounded error rate" (§4.1). Phase 2 plays the orrery's recorded runs; the
// in-browser HMM port (hmm.js, Phase 4) will recompute them. The thresholds below are computed here, from alpha and beta.
import { LAMBDA, formatMnats, humanFeature, lrLabel } from "../../wardline/model.js";
import { badge, badges, esc } from "../provenance.js";
import { selectWalk } from "../store.js";

const W = 1000;
const H = 460;
const X0 = 70;
const X1 = 960;
const yOf = (m) => 440 - ((Math.min(LAMBDA.max, Math.max(LAMBDA.min, m)) - LAMBDA.min) * 420) / (LAMBDA.max - LAMBDA.min);

// Wald's bounds from the error budget, in integer milli-nats (the same rounding as tools/orrery.py mnats()).
export function waldBounds({ alpha, beta }) {
  return {
    a: Math.round(Math.log((1 - beta) / alpha) * 1000),
    b: Math.round(Math.log(beta / (1 - alpha)) * 1000),
  };
}

export const stateWord = (lambda, { a, b }) => (lambda >= a ? "INTERVENE" : lambda <= b ? "BELOW B" : "BETWEEN B AND A");

function tacticStrip(step, x, features) {
  const cell = 9;
  const x0 = x - (features.length * (cell + 2)) / 2;
  return features.map((f, i) => {
    const on = step.features.includes(f);
    const cls = on ? (step.kind === "claim" ? "cell-claim" : "cell-on") : "cell-off";
    return `<rect x="${x0 + i * (cell + 2)}" y="${H - 14}" width="${cell}" height="${cell}" class="${cls}"><title>${esc(humanFeature(f))}${on ? "" : " (absent)"}</title></rect>`;
  }).join("");
}

export function walkHero({ walk, shown, crossed }, data, bounds) {
  const K = walk.steps.length;
  const xs = (i) => X0 + (K === 0 ? 0 : (i * (X1 - X0)) / K);
  const features = data.spec.sprt.features;
  const pts = [`${xs(0)},${yOf(0)}`];
  shown.forEach((s, i) => {
    const prev = i === 0 ? 0 : shown[i - 1].lambda_mnats;
    pts.push(`${xs(i + 1)},${yOf(prev)}`, `${xs(i + 1)},${yOf(s.lambda_mnats)}`);
  });
  const aLabel = `A · INTERVENE · ${Math.round(Math.exp(bounds.a / 1000))} : 1`;
  const bLabel = `B · DISMISS · 1 : ${Math.round(Math.exp(-bounds.b / 1000))}`;
  const dots = shown.map((s, i) => `<g data-action="select-envelope" data-seq="${s.seq}" class="dot"><circle cx="${xs(i + 1)}" cy="${yOf(s.lambda_mnats)}" r="5" class="${s.kind === "claim" ? "pt-claim" : "pt-obs"}"/><text x="${xs(i + 1)}" y="${yOf(s.lambda_mnats) - 10}" text-anchor="middle" class="svg-mono">${esc(formatMnats(s.lambda_mnats))}</text></g>`).join("");
  const strips = walk.steps.map((s, i) => (i < shown.length ? tacticStrip(s, xs(i + 1), features) +
    `<text x="${xs(i + 1)}" y="${H - 20}" text-anchor="middle" class="svg-mono dim">${esc(formatMnats(s.delta_mnats))}</text>` : "")).join("");
  const mark = crossed ? `<line x1="${xs(walk.crossing)}" y1="20" x2="${xs(walk.crossing)}" y2="440" class="cross"/><text x="${xs(walk.crossing) + 6}" y="34" class="svg-mono hot">crossed A at observation ${walk.crossing}</text>` : "";
  return `<svg class="hero-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Lambda after each observation against Wald's two bounds">
<line x1="${X0}" y1="${yOf(bounds.a)}" x2="${X1}" y2="${yOf(bounds.a)}" class="${crossed ? "ward-a crossed" : "ward-a"}"/>
<text x="${X1}" y="${yOf(bounds.a) - 6}" text-anchor="end" class="svg-label">${esc(aLabel)}</text>
<line x1="${X0}" y1="${yOf(bounds.b)}" x2="${X1}" y2="${yOf(bounds.b)}" class="ward-b"/>
<text x="${X1}" y="${yOf(bounds.b) - 6}" text-anchor="end" class="svg-label">${esc(bLabel)}</text>
<line x1="${X0}" y1="${yOf(0)}" x2="${X1}" y2="${yOf(0)}" class="axis"/><text x="${X0 - 8}" y="${yOf(0) + 4}" text-anchor="end" class="svg-mono dim">0</text>
<polyline points="${pts.join(" ")}" class="trail"/>${mark}${dots}${strips}</svg>`;
}

export function walkView({ state, data }) {
  const preset = state.sandboxes.walk.preset;
  const sel = selectWalk(data, preset, state.seq);
  const { walk, shown, crossed, lambda } = sel;
  const bounds = waldBounds(data.spec.sprt);
  const boundsOk = bounds.a === data.spec.sprt.a_mnats && bounds.b === data.spec.sprt.b_mnats;
  const trace = shown.map((s) => s.lambda_mnats);
  const word = stateWord(lambda, bounds);
  const readout = `<div class="readout"><div class="label">Λ NOW</div><div class="big mono">${esc(formatMnats(lambda))} <small>mn</small></div>
<div class="label">LIKELIHOOD RATIO</div><div class="mono">${esc(lrLabel(lambda))}</div>
<div class="chip ${crossed ? "chip-hot" : ""}">${esc(word)}</div>
<div class="dim">${shown.length} of ${walk.steps.length} observations · seq ≤ ${state.seq}</div></div>`;
  const presets = ["doorstep_scam", "grandparent_call", "benign_delivery"].map((p) => `<button data-action="preset" data-preset="${p}" class="btn${p === preset ? " on" : ""}">${esc(p.replaceAll("_", " "))}</button>`).join("");
  const tryit = `<div class="controls"><span class="label">Preset</span>${presets}</div>
<div class="controls"><label class="label" for="seq">Belief sequence</label><input id="seq" data-action="seq" type="range" min="1" max="${state.N}" value="${state.seq}"><output class="mono">${state.seq}</output>
<span class="dim">←/→ step · the walk shows observations with seq ≤ cursor</span></div>
<p class="dim">${esc(walk.summary)}</p>`;
  const crossLine = crossed
    ? `<li class="check ok"><span class="mark">✓</span> crossed A at observation ${walk.crossing} (seq ${sel.crossingStep.seq}) ${badge("computed")}</li>`
    : `<li class="check na"><span class="mark">—</span> A not crossed at seq ${state.seq}${walk.crossing === null ? " in this run" : ""} ${badge("computed")}</li>`;
  const proof = `<ul class="proof">
<li class="check ${boundsOk ? "ok" : "fail"}"><span class="mark">${boundsOk ? "✓" : "✗"}</span> A = ln((1−β)/α) = ${esc(formatMnats(bounds.a))} mn · B = ln(β/(1−α)) = ${esc(formatMnats(bounds.b))} mn, from α=${data.spec.sprt.alpha}, β=${data.spec.sprt.beta}, equal to the spec block ${badge("computed")}</li>
<li class="check ok"><span class="mark">✓</span> Λ trace (mn): [${esc(trace.join(", "))}] ${badges("recorded", { synthetic: true })}</li>
${crossLine}</ul>
<p class="note">Recorded by the orrery (seed 7), with the cross-device terms applied: ${esc(walk.cross_terms.join(", ") || "none")}. The browser port that recomputes this trace arrives in Phase 4. Likelihood ratio, not odds (T-7).</p>`;
  return {
    title: "It catches the scam with a bounded error rate",
    assertion: "Λ is a log-likelihood ratio. Wald's test stops at A (intervene) or B (dismiss), so the false-alarm rate is at most α and the miss rate at most β.",
    badges: badges("recorded", { synthetic: true }) + badge("computed"),
    hero: `<div class="walk">${readout}${walkHero(sel, data, bounds)}</div>`, tryit, proof,
  };
}
