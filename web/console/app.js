// Entry: boot, the store, the URL, delegated events and a render loop with at most one animation frame per change.
// Verification is display-only (TB-8): this page authorizes nothing.
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

const ui = { tab: null, verifyNonce: 0 };
const derived = { integrity: null, asymmetry: null, bitemporal: null };
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
    schedule();
  }, (error) => {
    derived[name] = null;
    console.error(name, error);
  });
}

function ensureDerived(state) {
  const want = needs(state);
  if (want.integrity) request("integrity", [state.sandboxes.integrity, ui.verifyNonce], () => computeIntegrity(CONSOLE, state.sandboxes.integrity, state.provenance));
  if (want.asymmetry) request("asymmetry", state.sandboxes.asymmetry, () => computeAsymmetry(CONSOLE, state.sandboxes.asymmetry));
  if (want.bitemporal) request("bitemporal", state.sandboxes.bitemporal.scenario, () => computeBitemporal(CONSOLE, state.sandboxes.bitemporal.scenario));
}

// ── render ──
function patch(name, html) {
  if (last.get(name) === html) return;
  last.set(name, html);
  const el = $(regions[name]);
  const focus = document.activeElement;
  // Do not replace a region while someone is dragging a slider or typing into it.
  if (focus && el.contains(focus) && (focus.type === "range" || (focus.tagName === "INPUT" && focus.type !== "range"))) {
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
  const ctx = { state, data: CONSOLE, derived };
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
  if (name === "arg") dispatch({ type: "arg", name: el.dataset.name, value: el.value });
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
