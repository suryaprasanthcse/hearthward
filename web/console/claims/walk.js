// Claim 1: "It decides with an explicit error budget" (§4.1, §6.5). The HMM part of Λ is recomputed in the browser by
// hmm.js (a port of hmm.py, checked to the milli-nat against the reference in pytest). The cross-device terms of a
// recorded run come from the orrery and are checked against the spec; the sandbox applies them itself.
import { LAMBDA, formatMnats, humanFeature, lrLabel } from "../../wardline/model.js";
import { lambdaTraceMnats, mnats, walkTrace } from "../hmm.js";
import { badge, badges, esc } from "../provenance.js";
import { memoize, selectWalk } from "../store.js";

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
export const TERM_NAMES = Object.freeze(["door_unknown_overlap", "identity_mismatch", "untrusted_org_irreversible_payment", "verified_official_callback"]);

// Which of the spec's cross-device terms add up to `gap` milli-nats (at most four terms, so every subset is tried).
export function explainGap(gap, crossMn) {
  const names = Object.keys(crossMn);
  for (let mask = 0; mask < 1 << names.length; mask += 1) {
    const picked = names.filter((_, i) => mask & (1 << i));
    if (picked.reduce((sum, n) => sum + crossMn[n], 0) === gap) return picked;
  }
  return null;
}

const crossMnOf = (data) => Object.fromEntries(Object.entries(data.spec.sprt_params.cross_device_llr).map(([k, v]) => [k, mnats(v)]));
const presetTrace = memoize((data, preset) => lambdaTraceMnats(data.walk[preset].observations, data.spec.sprt_params));

// Everything the view needs: the steps to draw, the computed HMM trace, and what it was checked against.
export const walkModel = memoize((data, sw, seq) => {
  const params = data.spec.sprt_params;
  const bounds = waldBounds(data.spec.sprt);
  if (sw.preset === "custom") {
    const applied = TERM_NAMES.filter((n) => sw.terms[n]);
    const { hmm, termMn, lambda } = walkTrace(sw.observations, params, applied);
    const steps = sw.observations.map((o, i) => ({
      seq: i + 1, kind: Array.isArray(o) ? "utterance" : "claim", features: Array.isArray(o) ? o : o.claim,
      label: Array.isArray(o) ? (o.length ? o.map(humanFeature).join(", ") : "no tactic") : "room claim",
      lambda_mnats: lambda[i], delta_mnats: lambda[i] - (i === 0 ? 0 : lambda[i - 1]),
    }));
    const crossing = steps.findIndex((s) => s.lambda_mnats >= bounds.a) + 1 || null;
    const walk = { name: "custom", summary: "Your own lines and cross-device terms, recomputed in this browser.", steps, crossing, cross_terms: applied, observations: sw.observations };
    return {
      walk, shown: steps, crossed: crossing !== null, crossingStep: crossing ? steps[crossing - 1] : null, lambda: steps.length ? steps[steps.length - 1].lambda_mnats : 0,
      computed: { hmm, termMn, custom: true, parity: null, gaps: null },
    };
  }
  const base = selectWalk(data, sw.preset, seq);
  const hmm = presetTrace(data, sw.preset);
  const crossMn = crossMnOf(data);
  const gaps = base.walk.steps.map((s, i) => {
    const gap = s.lambda_mnats - hmm[i];
    return { gap, terms: explainGap(gap, crossMn) };
  });
  return { ...base, computed: { hmm, custom: false, parity: JSON.stringify(hmm) === JSON.stringify(base.walk.reference_trace_mnats), gaps } };
});

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
  const K = Math.max(walk.steps.length, 1);
  const xs = (i) => X0 + (i * (X1 - X0)) / K;
  const features = data.spec.sprt.features;
  const pts = [`${xs(0)},${yOf(0)}`];
  shown.forEach((s, i) => {
    const prev = i === 0 ? 0 : shown[i - 1].lambda_mnats;
    pts.push(`${xs(i + 1)},${yOf(prev)}`, `${xs(i + 1)},${yOf(s.lambda_mnats)}`);
  });
  const aLabel = `A · INTERVENE · ${Math.round(Math.exp(bounds.a / 1000))} : 1`;
  const bLabel = `B · DISMISS · 1 : ${Math.round(Math.exp(-bounds.b / 1000))}`;
  const dots = shown.map((s, i) => `<g ${walk.name === "custom" ? "" : `data-action="select-envelope" data-seq="${s.seq}" `}class="dot"><circle cx="${xs(i + 1)}" cy="${yOf(s.lambda_mnats)}" r="5" class="${s.kind === "claim" ? "pt-claim" : "pt-obs"}"/><text x="${xs(i + 1)}" y="${yOf(s.lambda_mnats) - 10}" text-anchor="middle" class="svg-mono">${esc(formatMnats(s.lambda_mnats))}</text></g>`).join("");
  const strips = shown.map((s, i) => tacticStrip(s, xs(i + 1), features) +
    `<text x="${xs(i + 1)}" y="${H - 20}" text-anchor="middle" class="svg-mono dim">${esc(formatMnats(s.delta_mnats))}</text>`).join("");
  const mark = crossed ? `<line x1="${xs(walk.crossing)}" y1="20" x2="${xs(walk.crossing)}" y2="440" class="cross"/><text x="${Math.min(xs(walk.crossing) + 6, 700)}" y="34" class="svg-mono hot">crossed A at observation ${walk.crossing}</text>` : "";
  return `<svg class="hero-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Lambda after each observation against Wald's two bounds">
<line x1="${X0}" y1="${yOf(bounds.a)}" x2="${X1}" y2="${yOf(bounds.a)}" class="${crossed ? "ward-a crossed" : "ward-a"}"/>
<text x="${X1}" y="${yOf(bounds.a) - 6}" text-anchor="end" class="svg-label">${esc(aLabel)}</text>
<line x1="${X0}" y1="${yOf(bounds.b)}" x2="${X1}" y2="${yOf(bounds.b)}" class="ward-b"/>
<text x="${X1}" y="${yOf(bounds.b) - 6}" text-anchor="end" class="svg-label">${esc(bLabel)}</text>
<line x1="${X0}" y1="${yOf(0)}" x2="${X1}" y2="${yOf(0)}" class="axis"/><text x="${X0 - 8}" y="${yOf(0) + 4}" text-anchor="end" class="svg-mono dim">0</text>
<polyline points="${pts.join(" ")}" class="trail"/>${mark}${dots}${strips}</svg>`;
}

function sandboxControls(state, data, sel, note) {
  const sw = state.sandboxes.walk;
  const custom = sw.preset === "custom";
  const presets = ["doorstep_scam", "grandparent_call", "benign_delivery"].map((p) => `<button data-action="preset" data-preset="${p}" class="btn${p === sw.preset ? " on" : ""}">${esc(p.replaceAll("_", " "))}</button>`).join("")
    + `<button data-action="custom-new" class="btn${custom ? " on" : ""}">custom</button>`;
  const tactics = data.spec.sprt.features.map((f) => `<label class="tactic"><input type="checkbox" name="tactic" value="${f}"> ${esc(humanFeature(f))}</label>`).join("");
  const crossMn = crossMnOf(data);
  const terms = TERM_NAMES.map((n) => `<label class="term${crossMn[n] < 0 ? " verified" : ""}"><input type="checkbox" data-action="term" data-term="${n}"${sw.terms[n] ? " checked" : ""}${custom ? "" : " disabled"}> ${crossMn[n] < 0 ? "◎ " : ""}<span class="mono">${esc(n)}</span> <span class="dim">${esc(formatMnats(crossMn[n]))} mn</span></label>`).join("");
  const lines = custom ? sel.walk.steps.map((s, i) => `<li><span class="mono">${i + 1}</span> ${s.kind === "claim" ? "room claim" : "line"}: ${esc(s.features.map(humanFeature).join(", ") || "no tactic")}
 <button data-action="obs-move" data-index="${i}" data-delta="-1" class="link" aria-label="move up">↑</button><button data-action="obs-move" data-index="${i}" data-delta="1" class="link" aria-label="move down">↓</button><button data-action="obs-remove" data-index="${i}" class="link" aria-label="remove">✕</button></li>`).join("") : "";
  return `<div class="controls"><span class="label">Preset</span>${presets}</div>
${custom ? "" : `<div class="controls"><label class="label" for="seq">Belief sequence</label><input id="seq" data-action="seq" type="range" min="1" max="${state.N}" value="${state.seq}"><output class="mono">${state.seq}</output><span class="dim">←/→ step</span></div>`}
<div class="controls tactics"><span class="label">Tactics heard</span>${tactics}
<button data-action="add-line" class="btn">Add line <kbd>A</kbd></button><button data-action="add-claim" class="btn">Add room claim</button><button data-action="obs-undo" class="btn"${custom && sel.walk.steps.length ? "" : " disabled"}>Undo <kbd>Z</kbd></button></div>
${note ? `<p class="note">${esc(note)}</p>` : ""}
<div class="controls terms"><span class="label">Cross-device terms (once each)</span>${terms}</div>
${custom ? `<ol class="lines">${lines || `<li class="dim">no observations yet: toggle tactics and add a line</li>`}</ol>` : `<p class="dim">${esc(sel.walk.summary)} Adding a line copies this run into the custom sandbox.</p>`}`;
}

export function walkView({ state, data, ui }) {
  const sw = state.sandboxes.walk;
  const sel = walkModel(data, sw, state.seq);
  const { walk, shown, crossed, lambda, computed } = sel;
  const bounds = waldBounds(data.spec.sprt);
  const boundsOk = bounds.a === data.spec.sprt.a_mnats && bounds.b === data.spec.sprt.b_mnats;
  const trace = shown.map((s) => s.lambda_mnats);
  const word = shown.length ? stateWord(lambda, bounds) : "NO OBSERVATIONS";
  const readout = `<div class="readout"><div class="label">Λ NOW</div><div class="big mono">${esc(formatMnats(lambda))} <small>mn</small></div>
<div class="label">LIKELIHOOD RATIO</div><div class="mono">${esc(lrLabel(lambda))}</div>
<div class="chip ${crossed ? "chip-hot" : ""}">${esc(word)}</div>
<div class="dim">${shown.length} of ${walk.steps.length} observations${sw.preset === "custom" ? "" : ` · seq ≤ ${state.seq}`}</div></div>`;
  const crossLine = crossed
    ? `<li class="check ok"><span class="mark">✓</span> crossed A at observation ${walk.crossing}${sw.preset !== "custom" ? ` (seq ${sel.crossingStep.seq})` : ""} ${badge("computed")}</li>`
    : `<li class="check na"><span class="mark">—</span> A not crossed${sw.preset === "custom" ? "" : ` at seq ${state.seq}`}${walk.crossing === null ? "" : " yet"} ${badge("computed")}</li>`;
  let evidence;
  if (computed.custom) {
    evidence = `<li class="check ok"><span class="mark">✓</span> Λ trace (mn): [${esc(trace.join(", "))}] = HMM [${esc(computed.hmm.join(", "))}] ${computed.termMn ? `${esc(formatMnats(computed.termMn))} mn of terms` : "no terms"} ${badge("computed")}</li>`;
  } else {
    const n = shown.length;
    const gaps = computed.gaps.slice(0, n);
    const explained = gaps.every((g) => g.terms !== null);
    const last = gaps[n - 1];
    evidence = `<li class="check ok"><span class="mark">✓</span> Λ trace (mn): [${esc(trace.join(", "))}] ${badges("recorded", { synthetic: true })}</li>
<li class="check ${computed.parity ? "ok" : "fail"}"><span class="mark">${computed.parity ? "✓" : "✗"}</span> HMM trace [${esc(computed.hmm.slice(0, n).join(", "))}] matches tools/orrery.py reference_trace_mnats ${badge("computed")}</li>
<li class="check ${explained ? "ok" : "fail"}"><span class="mark">${explained ? "✓" : "✗"}</span> recorded Λ − HMM = cross-device terms${last ? `: ${esc(formatMnats(last.gap))} mn${last.terms?.length ? ` (${esc(last.terms.join(" + "))})` : ""}` : ""}, each a sum of the spec's terms ${badge("computed")}</li>`;
  }
  const proof = `<ul class="proof">
<li class="check ${boundsOk ? "ok" : "fail"}"><span class="mark">${boundsOk ? "✓" : "✗"}</span> A = ln((1−β)/α) = ${esc(formatMnats(bounds.a))} mn · B = ln(β/(1−α)) = ${esc(formatMnats(bounds.b))} mn, from α=${data.spec.sprt.alpha}, β=${data.spec.sprt.beta}, equal to the spec block ${badge("computed")}</li>
${evidence}${crossLine}</ul>
<p class="note">${computed.custom ? "Lines and terms are yours" : `Recorded by the orrery (seed 7), terms: ${esc(walk.cross_terms.join(", ") || "none")}`}. A room claim adds only features not yet heard (DC-2); each term applies once (DC-3); only verified_official_callback lowers Λ (DC-1). Likelihood ratio, not odds (T-7).</p>`;
  return {
    title: "It decides with an explicit error budget",
    assertion: "Λ is a log-likelihood ratio. Wald's test stops at A (intervene) or B (dismiss), with α and β as the error budget. Under the HMM's dependence the bounds are approximations, and the parameters are uncalibrated priors (§6.5, §6.8).",
    badges: (computed.custom ? badge("computed") : badges("recorded", { synthetic: true }) + badge("computed")) + (computed.custom ? badge("synthetic") : ""),
    hero: `<div class="walk">${readout}${walkHero(sel, data, bounds)}</div>`, tryit: sandboxControls(state, data, sel, ui?.walkNote ?? ""), proof,
  };
}
