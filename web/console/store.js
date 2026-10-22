// The single store (§3.1): plain-object actions, pure reducers, memoized selectors, and the URL <-> state contract
// (§3.2). No DOM access here, so node:test can drive all of it.

export const CLAIMS = Object.freeze(["walk", "asymmetry", "bitemporal", "integrity", "boundaries"]);
export const PRESETS = Object.freeze(["doorstep_scam", "grandparent_call", "benign_delivery", "custom"]);
export const SELECTION_KINDS = Object.freeze(["envelope", "rule", "decision", "probe", "node", "tool"]);
export const DEFAULT_PRESET = "doorstep_scam";

// Which claims a selection kind belongs to; changing claim clears a selection that does not belong (§5).
export const SELECTION_CLAIMS = Object.freeze({
  envelope: ["walk", "bitemporal", "integrity"],
  decision: ["walk", "bitemporal", "integrity"],
  rule: ["asymmetry"],
  tool: ["asymmetry"],
  probe: ["boundaries"],
  node: ["boundaries"],
});

const SELECTION_ID = /^[A-Za-z0-9_.\-:]{1,64}$/;

export function initialState({ N, provenance }) {
  return {
    claim: "walk",
    seq: N,
    selection: null,
    sandboxes: {
      walk: {
        preset: DEFAULT_PRESET,
        observations: [],
        terms: { door_unknown_overlap: false, identity_mismatch: false, untrusted_org_irreversible_payment: false, verified_official_callback: false },
      },
      integrity: { edits: [], tamper: 0, rechain: 0 },
      asymmetry: { tool: "dismiss_alert", args: {}, nonce: null, exp: null },   // null = the recorded action document
      bitemporal: { scenario: "late_and_lossy" },
      boundaries: { ringBytePatch: null, ringPretty: 0 },
    },
    live: {},
    provenance,
    N,
  };
}

const clampSeq = (seq, N) => (Number.isInteger(seq) && seq >= 1 && seq <= N ? seq : N);
const validSelection = (sel) =>
  sel === null || (sel && SELECTION_KINDS.includes(sel.kind) && typeof sel.id === "string" && SELECTION_ID.test(sel.id));

// ── reducer ──
export function reduce(state, action) {
  switch (action.type) {
    case "claim": {
      if (!CLAIMS.includes(action.claim) || action.claim === state.claim) return state;
      const keep = state.selection && SELECTION_CLAIMS[state.selection.kind]?.includes(action.claim);
      return { ...state, claim: action.claim, selection: keep ? state.selection : null };
    }
    case "seq": {
      const seq = clampSeq(action.seq, state.N);
      return seq === state.seq ? state : { ...state, seq };
    }
    case "select":
      if (!validSelection(action.selection)) return state;
      return { ...state, selection: action.selection };
    case "preset": {
      if (!PRESETS.includes(action.preset) || action.preset === state.sandboxes.walk.preset) return state;
      return { ...state, sandboxes: { ...state.sandboxes, walk: { ...state.sandboxes.walk, preset: action.preset } } };
    }
    case "tamper": {
      const tamper = action.value ? 1 : 0;
      const cur = state.sandboxes.integrity;
      if (cur.tamper === tamper && (tamper === 1 || cur.rechain === 0)) return state;
      return { ...state, sandboxes: { ...state.sandboxes, integrity: { ...cur, tamper, rechain: tamper ? cur.rechain : 0 } } };
    }
    case "rechain": {
      const cur = state.sandboxes.integrity;
      const rechain = action.value && cur.tamper === 1 ? 1 : 0;
      return rechain === cur.rechain ? state : { ...state, sandboxes: { ...state.sandboxes, integrity: { ...cur, rechain } } };
    }
    case "edit": {
      const cur = state.sandboxes.integrity;
      const edits = [...cur.edits.filter((e) => !(e.seq === action.seq && e.path === action.path)), { seq: action.seq, path: action.path, value: action.value }];
      return { ...state, sandboxes: { ...state.sandboxes, integrity: { ...cur, edits } } };
    }
    case "reset":
      return { ...state, sandboxes: { ...state.sandboxes, integrity: { edits: [], tamper: 0, rechain: 0 } } };
    case "tool": {
      const cur = state.sandboxes.asymmetry;
      return cur.tool === action.tool ? state : { ...state, sandboxes: { ...state.sandboxes, asymmetry: { tool: action.tool, args: {}, nonce: cur.nonce, exp: cur.exp } } };
    }
    case "arg": {
      const cur = state.sandboxes.asymmetry;
      return { ...state, sandboxes: { ...state.sandboxes, asymmetry: { ...cur, args: { ...cur.args, [action.name]: action.value } } } };
    }
    case "nonce":
    case "exp": {
      const cur = state.sandboxes.asymmetry;
      return { ...state, sandboxes: { ...state.sandboxes, asymmetry: { ...cur, [action.type]: action.value } } };
    }
    case "asymmetry_reset":
      return { ...state, sandboxes: { ...state.sandboxes, asymmetry: { tool: "dismiss_alert", args: {}, nonce: null, exp: null } } };
    case "scenario": {
      if (!["doorstep_scam", "late_and_lossy"].includes(action.scenario) || action.scenario === state.sandboxes.bitemporal.scenario) return state;
      return { ...state, sandboxes: { ...state.sandboxes, bitemporal: { scenario: action.scenario } } };
    }
    case "live":
      return { ...state, live: { ...state.live, [action.probe]: { status: action.status, at: action.at ?? null, response: action.response ?? null } } };
    case "hash":
      return applyHash(state, action.parsed);
    default:
      return state;
  }
}

export function createStore(initial) {
  let state = initial;
  const listeners = new Set();
  return {
    getState: () => state,
    dispatch(action) {
      const next = reduce(state, action);
      if (next !== state) {
        state = next;
        for (const fn of listeners) fn(state, action);
      }
      return state;
    },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  };
}

// A selector recomputes only when an argument changes by reference (§3.1: derived values come from memoized selectors).
export function memoize(fn) {
  let last = null;
  let value;
  return (...args) => {
    if (last && last.length === args.length && last.every((a, i) => a === args[i])) return value;
    value = fn(...args);
    last = args;
    return value;
  };
}

// ── URL hash contract (§3.2) ──
export function parseHash(hash, { N }) {
  const params = new URLSearchParams(String(hash).replace(/^#/, ""));
  const claim = CLAIMS.includes(params.get("claim")) ? params.get("claim") : "walk";
  const seqText = params.get("seq");
  const seq = /^\d{1,4}$/.test(seqText ?? "") ? clampSeq(Number(seqText), N) : N;
  const tamper = params.get("tamper") === "1" ? 1 : 0;
  const rechain = tamper === 1 && params.get("rechain") === "1" ? 1 : 0;
  const preset = PRESETS.includes(params.get("preset")) ? params.get("preset") : DEFAULT_PRESET;
  let selection = null;
  const sel = params.get("sel");
  if (sel && sel.includes(":")) {
    const at = sel.indexOf(":");
    const candidate = { kind: sel.slice(0, at), id: sel.slice(at + 1) };
    if (validSelection(candidate) && SELECTION_CLAIMS[candidate.kind].includes(claim)) selection = candidate;
  }
  return { claim, seq, tamper, rechain, preset, selection };
}

export function applyHash(state, parsed) {
  const walk = state.sandboxes.walk;
  return {
    ...state,
    claim: parsed.claim, seq: parsed.seq, selection: parsed.selection,
    sandboxes: {
      ...state.sandboxes,
      walk: walk.preset === parsed.preset ? walk : { ...walk, preset: parsed.preset },
      integrity: { ...state.sandboxes.integrity, tamper: parsed.tamper, rechain: parsed.rechain },
    },
  };
}

export function toHash(state) {
  const parts = [`claim=${state.claim}`, `seq=${state.seq}`, `tamper=${state.sandboxes.integrity.tamper}`];
  if (state.sandboxes.integrity.rechain) parts.push("rechain=1");
  if (state.selection) parts.push(`sel=${state.selection.kind}:${state.selection.id}`);
  if (state.sandboxes.walk.preset !== DEFAULT_PRESET) parts.push(`preset=${state.sandboxes.walk.preset}`);
  return `#${parts.join("&")}`;
}

// ── selectors ──
export const selectWalk = memoize((data, preset, seq) => {
  const walk = data.walk[preset === "custom" ? DEFAULT_PRESET : preset];
  const shown = walk.steps.filter((s) => s.seq <= seq);
  const crossingStep = walk.crossing === null ? null : walk.steps[walk.crossing - 1];
  const crossed = crossingStep !== null && crossingStep.seq <= seq;
  return { walk, shown, crossed, crossingStep, lambda: shown.length ? shown[shown.length - 1].lambda_mnats : 0 };
});

export const selectScenario = memoize((data, name) => data.ward.scenarios[name]);
