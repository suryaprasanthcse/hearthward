// Browser port of src/hearthward/decision/hmm.py `lambda_trace_mnats` (§4.1, §6.2). Pure and DOM-free.
// It keeps the reference's operation order so every trace agrees to the milli-nat:
//   - logsumexp with a max shift; -inf for zero probabilities (never log 0);
//   - log1p(-p) for absent features; noisy rates tpr*p + fpr*(1-p);
//   - sums use CPython 3.12's compensated float sum (Neumaier), because the reference's `sum()` does;
//   - milli-nats round half to even, like Python's round().

const NEG_INF = -Infinity;

// CPython 3.12 sum() over floats: Neumaier compensation, added back only when finite.
export function pySum(values) {
  let total = 0;
  let c = 0;
  for (const x of values) {
    const t = total + x;
    c += Math.abs(total) >= Math.abs(x) ? (total - t) + x : (x - t) + total;
    total = t;
  }
  if (c && Number.isFinite(c)) total += c;
  return total;
}

export function roundHalfEven(x) {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff < 0.5) return floor;
  if (diff > 0.5) return floor + 1;
  return floor % 2 === 0 ? floor : floor + 1;
}
export const mnats = (nats) => roundHalfEven(nats * 1000);

const logOrNegInf = (p) => (p > 0 ? Math.log(p) : NEG_INF);

export function logsumexp(values) {
  const items = [...values];
  const top = Math.max(...items);
  if (top === NEG_INF) return NEG_INF;
  return top + Math.log(pySum(items.map((v) => Math.exp(v - top))));
}

// The `hearthward:sprt` block with the extractor's noise folded into every rate (HmmModel.from_params).
export function modelFromParams(params) {
  const { features, stages } = params;
  const { tpr, fpr } = params.extractor;
  const noisy = (rates) => rates.map((p, i) => tpr[i] * p + fpr[i] * (1 - p));
  const model = {
    features, stages,
    logInit: params.initial.map(logOrNegInf),
    logTrans: params.transitions.map((row) => row.map(logOrNegInf)),
    emit: params.emissions.map(noisy),
    h0: noisy(params.h0),
  };
  for (const rates of [...model.emit, model.h0]) {
    if (rates.some((p) => !(p > 0 && p < 1))) throw new Error("a noisy rate reached 0 or 1");
  }
  return model;
}

const logEmit = (model, rates, observed) => pySum(model.features.map((f, i) => (observed.has(f) ? Math.log(rates[i]) : Math.log1p(-rates[i]))));
const logClaim = (model, rates, present) => pySum(model.features.flatMap((f, i) => (present.has(f) ? [Math.log(rates[i])] : [])));

export class ForwardFilter {
  constructor(model) {
    this.model = model;
    this.logAlpha = null;
    this.logL0 = 0;
  }

  get value() {
    return this.logAlpha === null ? 0 : logsumexp(this.logAlpha) - this.logL0;
  }

  observe(observed) {
    const m = this.model;
    const e = m.emit.map((rates) => logEmit(m, rates, observed));
    if (this.logAlpha === null) {
      this.logAlpha = m.logInit.map((li, s) => li + e[s]);
    } else {
      const prev = this.logAlpha;
      this.logAlpha = e.map((es, s) => logsumexp(prev.map((a, i) => a + m.logTrans[i][s])) + es);
    }
    this.logL0 += logEmit(m, m.h0, observed);
  }

  observeClaim(present) {
    const m = this.model;
    const e = m.emit.map((rates) => logClaim(m, rates, present));
    this.logAlpha = this.logAlpha === null ? m.logInit.map((li, s) => li + e[s]) : this.logAlpha.map((a, s) => a + e[s]);
    this.logL0 += logClaim(m, m.h0, present);
  }
}

// A list is an utterance; {claim: [...]} is a ROOM claim. Unknown features are an error, as in parse_observation.
export function parseObservation(item, features) {
  const list = Array.isArray(item) ? item : item?.claim;
  if (!Array.isArray(list) || list.some((f) => !features.includes(f))) throw new Error("observation is not a list of known features");
  return { kind: Array.isArray(item) ? "utterance" : "claim", features: new Set(list) };
}

// Lambda in milli-nats after each observation, without cross-device terms.
export function lambdaTraceMnats(observations, params) {
  const model = modelFromParams(params);
  const forward = new ForwardFilter(model);
  return observations.map((item) => {
    const o = parseObservation(item, model.features);
    if (o.kind === "claim") forward.observeClaim(o.features);
    else forward.observe(o.features);
    return mnats(forward.value);
  });
}

// DC-2: a ROOM claim adds only features that no earlier line or claim has already carried.
export function novelFeatures(claim, earlier) {
  const seen = new Set(earlier.flatMap((o) => (Array.isArray(o) ? o : o.claim)));
  const novel = claim.filter((f) => !seen.has(f));
  return { novel, dropped: claim.filter((f) => seen.has(f)) };
}

// The sandbox's Lambda: the HMM trace plus the cross-device terms in force (each once, DC-3; included from the first observation).
export function walkTrace(observations, params, termNames) {
  const hmm = lambdaTraceMnats(observations, params);
  const termMn = termNames.reduce((sum, name) => sum + mnats(params.cross_device_llr[name]), 0);
  return { hmm, termMn, lambda: hmm.map((v) => v + termMn) };
}
