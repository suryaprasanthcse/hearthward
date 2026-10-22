// Entry: boot, the store, the URL, delegated events and a render loop with at most one animation frame per change.
// Verification is display-only (TB-8): this page authorizes nothing.
import { walkModel } from "./claims/walk.js";
import { novelFeatures } from "./hmm.js";
import { computeRing, ringOriginal, runLive } from "./claims/boundaries.js";
import { computeAsymmetry } from "./claims/asymmetry.js";
import { computeBitemporal } from "./claims/bitemporal.js";
import { bundleJson, computeIntegrity } from "./claims/integrity.js";
import { CONSOLE } from "./data/console_data.js";
import { TABS, defaultTab } from "./inspector.js";
import { deriveProvenance } from "./provenance.js";
import { createStore, initialState, parseHash, toHash } from "./store.js";
import { KEYS, claimRegions, inspectorHtml, navHtml, needs } from "./views.js";

const N = CONSOLE.ward.scenarios[CONSOLE.ward.signed.scenario].envelopes.length;
const provenance = deriveProvenance(CONSOLE, location.host);
const store = createStore(initialState({ N, provenance }));
store.dispatch({ type: "hash", parsed: parseHash(location.hash, { N }) });

const ui = { tab: null, verifyNonce: 0, walkNote: "" };
const derived = { integrity: null, asymmetry: null, bitemporal: null, ring: null };
const pending = {};
const regions = { nav: "#nav", head: "#claim-head", hero: "#claim-hero", tryit: "#claim-tryit", proof: "#claim-proof", inspector: "#inspector" };
const last = new Map();
const $ = (sel) => document.querySelector(sel);

// ── derived values: computed off the render path, applied only if still current ──
const same = (a, b) => (Array.isArray(a) && Array.isArray(b) ? a.length === b.length && a.every((x, i) => x === b[i]) : a === b);
function request(name, key, compute) {
  if (pending[name] && same(pending[name].key, key)) return;
  pending[name] = { key };
  compute().then((value) => {
    if (!pending[name] || !same(pending[name].key, key)) return;
    derived[name] = value;
    render(); // an arriving result is already a state change's second half; do not wait for a frame a hidden tab may not give
  }, (error) => {
    derived[name] = null;
    console.error(name, error);
  });
}

function ensureDerived(state) {
  const want = needs(state);
  if (want.integrity) request("integrity", [state.sandboxes.integrity, ui.verifyNonce], () => computeIntegrity(CONSOLE, state.sandboxes.integrity, state.provenance));
  if (want.asymmetry) request("asymmetry", state.sandboxes.asymmetry, () => computeAsymmetry(CONSOLE, state.sandboxes.asymmetry));
  if (want.ring) request("ring", state.sandboxes.boundaries, () => computeRing(CONSOLE, state.sandboxes.boundaries));
  if (want.bitemporal) request("bitemporal", state.sandboxes.bitemporal.scenario, () => computeBitemporal(CONSOLE, state.sandboxes.bitemporal.scenario));
}

// ── render ──
function patch(name, html) {
  if (last.get(name) === html) return;
  last.set(name, html);
  const el = $(regions[name]);
  const focus = document.activeElement;
  // Do not replace a region while someone is dragging a slider or typing into it.
  if (focus && el.contains(focus) && (focus.type === "range" || (focus.tagName === "INPUT" && ["text", "number"].includes(focus.type)))) {
    last.delete(name);
    return;
  }
  el.innerHTML = html;
}

// One render per change: an animation frame, with a timer as the fallback for a hidden tab (frames are paused there).
let frame = 0;
let timer = 0;
function schedule() {
  if (frame) return;
  const run = () => {
    if (!frame) return;
    cancelAnimationFrame(frame);
    clearTimeout(timer);
    frame = 0;
    render();
  };
  frame = requestAnimationFrame(run);
  timer = setTimeout(run, 100);
}

function render() {
  const state = store.getState();
  ensureDerived(state);
  const ctx = { state, data: CONSOLE, derived, ui };
  const parts = claimRegions(ctx);
  document.title = `${parts.title} · HEARTHWARD`;
  patch("nav", navHtml(state));
  patch("head", parts.head);
  patch("hero", parts.hero);
  patch("tryit", parts.tryit);
  patch("proof", parts.proof);
  patch("inspector", inspectorHtml(ctx, { ...ui, tab: ui.tab ?? defaultTab(state.selection) }));
  for (const range of document.querySelectorAll("#claim-tryit input[type=range]")) {
    if (document.activeElement !== range) range.value = String(state.seq);
    if (range.nextElementSibling) range.nextElementSibling.textContent = String(state.seq);
  }
}

// ── URL ──
let lastClaim = store.getState().claim;
store.subscribe((state) => {
  const hash = toHash(state);
  if (hash !== location.hash) {
    if (state.claim !== lastClaim) history.pushState(null, "", hash);
    else history.replaceState(null, "", hash);
  }
  lastClaim = state.claim;
  schedule();
});
addEventListener("hashchange", () => {
  if (location.hash !== toHash(store.getState())) store.dispatch({ type: "hash", parsed: parseHash(location.hash, { N }) });
});

// ── events ──
const dispatch = (action) => store.dispatch(action);
const select = (kind, id) => { ui.tab = null; dispatch({ type: "select", selection: { kind, id: String(id) } }); schedule(); };

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// The sandbox starts from whatever run is on screen, so adding a line to a preset customizes a copy of it.
function ensureCustom() {
  const state = store.getState();
  const sw = state.sandboxes.walk;
  if (sw.preset === "custom") return;
  const model = walkModel(CONSOLE, sw, state.seq);
  dispatch({ type: "custom", observations: structuredClone(model.walk.observations), terms: Object.fromEntries(model.walk.cross_terms.map((n) => [n, true])) });
}

function addObservation(asClaim) {
  const picked = [...document.querySelectorAll("#claim-tryit input[name=tactic]:checked")].map((el) => el.value);
  ensureCustom();
  let features = CONSOLE.spec.sprt.features.filter((f) => picked.includes(f));
  ui.walkNote = "";
  if (asClaim) {
    const { novel, dropped } = novelFeatures(features, store.getState().sandboxes.walk.observations);
    if (dropped.length) ui.walkNote = `A room claim adds only features not yet heard (DC-2), so ${dropped.join(", ")} ${dropped.length === 1 ? "was" : "were"} dropped.`;
    features = novel;
  }
  dispatch({ type: "obs_add", observation: asClaim ? { claim: features } : features });
  last.delete("tryit");
  schedule();
}

const handlers = {
  claim: (el) => dispatch({ type: "claim", claim: el.dataset.claim }),
  preset: (el) => dispatch({ type: "preset", preset: el.dataset.preset }),
  seq: (el) => dispatch({ type: "seq", seq: Number(el.dataset.seq ?? el.value) }),
  "seq-decision": (el) => dispatch({ type: "seq", seq: Number(el.dataset.seq) }),
  scenario: (el) => dispatch({ type: "scenario", scenario: el.dataset.scenario }),
  tamper: () => dispatch({ type: "tamper", value: store.getState().sandboxes.integrity.tamper ? 0 : 1 }),
  rechain: () => dispatch({ type: "rechain", value: store.getState().sandboxes.integrity.rechain ? 0 : 1 }),
  reset: () => dispatch({ type: "reset" }),
  verify: () => { ui.verifyNonce += 1; schedule(); },
  download: () => { if (derived.integrity) download("bundle.json", bundleJson(derived.integrity, CONSOLE)); },
  "select-envelope": (el) => select("envelope", el.dataset.seq),
  "select-decision": () => select("decision", "latest"),
  "select-rule": (el) => select("rule", el.dataset.rule),
  "select-probe": (el) => select("probe", el.dataset.probe),
  tool: (el) => { dispatch({ type: "tool", tool: el.dataset.tool }); select("tool", el.dataset.tool); },
  tab: (el) => { ui.tab = el.dataset.tab; schedule(); },
  "inspector-toggle": () => document.body.classList.toggle("insp-open"),
  "asymmetry-reset": () => dispatch({ type: "asymmetry_reset" }),
  "select-node": (el) => select("node", el.dataset.node),
  "ring-flip": (el) => {
    const offset = Number(el.dataset.offset);
    dispatch({ type: "ring_patch", offset, value: ringOriginal(CONSOLE)[offset] ^ 1 });
  },
  "ring-pretty": () => dispatch({ type: "ring_pretty" }),
  "ring-reset": () => dispatch({ type: "ring_reset" }),
  "run-live": async (el) => {
    const id = el.dataset.probe;
    const probe = CONSOLE.recorded.probes.data.probes.find((p) => p.id === id);
    dispatch({ type: "live", probe: id, status: "pending" });
    const result = await runLive(probe, CONSOLE.topology.probe_requests[id]);
    dispatch({ type: "live", probe: id, status: result.status, at: result.at, response: result.response });
  },
  "custom-new": () => { ui.walkNote = ""; dispatch({ type: "custom", observations: [], terms: {} }); },
  "add-line": () => addObservation(false),
  "add-claim": () => addObservation(true),
  "obs-undo": () => dispatch({ type: "obs_undo" }),
  "obs-remove": (el) => dispatch({ type: "obs_remove", index: Number(el.dataset.index) }),
  "obs-move": (el) => dispatch({ type: "obs_move", index: Number(el.dataset.index), delta: Number(el.dataset.delta) }),
  "copy-bytes": (el) => navigator.clipboard?.writeText(el.dataset.text),
};

document.addEventListener("click", (event) => {
  const el = event.target.closest("[data-action]");
  const fn = el && handlers[el.dataset.action];
  if (fn && el.tagName !== "FORM" && el.tagName !== "INPUT" && el.tagName !== "SELECT") fn(el);
});
document.addEventListener("input", (event) => {
  if (event.target.matches("input[type=range][data-action=seq]")) handlers.seq(event.target);
});
document.addEventListener("change", (event) => {
  const el = event.target;
  const name = el.dataset?.action;
  if (name === "term") dispatch({ type: "term", name: el.dataset.term });
  else if (name === "arg") dispatch({ type: "arg", name: el.dataset.name, value: el.value });
  else if (name === "nonce" || name === "exp") dispatch({ type: name, value: el.value === "" ? null : el.value });
  if (name) { last.delete("tryit"); schedule(); }
});
document.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.dataset.action !== "edit-field") return;
  const data = new FormData(form);
  if (String(data.get("value")).trim() === "") return;
  dispatch({ type: "edit", seq: Number(data.get("seq")), path: String(data.get("path")), value: String(data.get("value")) });
  last.delete("tryit");
  schedule();
});
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey || event.target.matches("input, select, textarea")) return;
  const state = store.getState();
  const key = event.key;
  if (KEYS[key]) dispatch({ type: "claim", claim: KEYS[key] });
  else if (key === "ArrowLeft") dispatch({ type: "seq", seq: state.seq - 1 });
  else if (key === "ArrowRight") dispatch({ type: "seq", seq: state.seq + 1 });
  else if (key === "a" || key === "A") { if (state.claim === "walk") handlers["add-line"](); }
  else if (key === "z" || key === "Z") { if (state.claim === "walk") dispatch({ type: "obs_undo" }); }
  else if (key === "t" || key === "T") handlers.tamper();
  else if (key === "v" || key === "V") handlers.verify();
  else if (key === "i" || key === "I") handlers["inspector-toggle"]();
  else if (key === "[" || key === "]") {
    const cur = TABS.findIndex((t) => t.id === (ui.tab ?? defaultTab(state.selection)));
    ui.tab = TABS[(cur + (key === "]" ? 1 : TABS.length - 1)) % TABS.length].id;
    schedule();
  }
});

render();
