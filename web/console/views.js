// Pure view assembly: every region of the page as a string, from (state, data, derived, ui). No DOM access, so the
// static and view tests can render every claim in node.
import { asymmetryView } from "./claims/asymmetry.js";
import { bitemporalView } from "./claims/bitemporal.js";
import { boundariesView } from "./claims/boundaries.js";
import { integrityView } from "./claims/integrity.js";
import { walkView } from "./claims/walk.js";
import { inspectorView } from "./inspector.js";
import { esc } from "./provenance.js";
import { CLAIMS } from "./store.js";

export const CLAIM_VIEWS = Object.freeze({
  walk: walkView, asymmetry: asymmetryView, bitemporal: bitemporalView, integrity: integrityView, boundaries: boundariesView,
});
export const CLAIM_NAMES = Object.freeze({
  walk: "1 · The walk", asymmetry: "2 · The asymmetry", bitemporal: "3 · The timeline", integrity: "4 · The ledger", boundaries: "5 · The boundary",
});
export const KEYS = Object.freeze({ "1": "walk", "2": "asymmetry", "3": "bitemporal", "4": "integrity", "5": "boundaries" });

export const navHtml = (state) =>
  CLAIMS.map((c) => `<button class="navbtn${c === state.claim ? " on" : ""}" data-action="claim" data-claim="${c}"${c === state.claim ? ' aria-current="page"' : ""}>${esc(CLAIM_NAMES[c])}</button>`).join("");

// The pieces of one claim: head, hero, tryit, proof.
export function claimRegions(ctx) {
  const view = CLAIM_VIEWS[ctx.state.claim](ctx);
  const inspectDecision = ["walk", "integrity", "bitemporal"].includes(ctx.state.claim)
    ? `<div class="head-actions"><button class="btn" data-action="select-decision">Inspect the decision</button></div>` : "";
  return {
    head: `<h1>${esc(view.title)}</h1><p>${esc(view.assertion)}</p><div class="badges">${view.badges}</div>${inspectDecision}`,
    hero: view.hero, tryit: view.tryit, proof: view.proof, title: view.title,
  };
}

export const inspectorHtml = (ctx, ui) => inspectorView(ctx, ui);

// Which asynchronous results a claim needs (the app computes them and re-renders when they arrive).
export const needs = (state) => ({
  integrity: state.claim === "integrity" || state.claim === "walk" || state.selection?.kind === "decision" || state.selection?.kind === "envelope",
  asymmetry: state.claim === "asymmetry",
  bitemporal: state.claim === "bitemporal",
  ring: state.claim === "boundaries",
});
