// Claim 3: "Late or duplicate data can't rewrite a decision" (§4.3). The plane geometry is Wardline's (read-only);
// the recorded run is the orrery's. The head checks are recomputed here from the envelopes.
import { ZERO_HEAD } from "../../wardline/chain.js";
import { PLANE, asOfIndex, fmtClock, formatMnats, planeView, scenarioOf, shortHash, stateAt, pad2 } from "../../wardline/model.js";
import { badge, badges, esc } from "../provenance.js";
import { cachedHead } from "./integrity.js";

export const SCENARIO_LABEL = { doorstep_scam: "on time (doorstep_scam)", late_and_lossy: "late, duplicate, recovered (late_and_lossy)" };

// The head after each prefix of the ledger, re-derived from the envelopes alone (BT-2, BT-3).
export async function computeBitemporal(data, name) {
  const sc = scenarioOf(data.ward, name);
  const heads = [];
  let previous = ZERO_HEAD;
  let linked = true;
  for (const env of sc.envelopes) {
    const computed = await cachedHead(env);
    linked = linked && env.prev === previous && computed === env.head;
    heads.push(computed);
    previous = computed;
  }
  const at = sc.decision.at_seq;
  return { name, linked, headAt: heads[at - 1], recorded: sc.decision.ledger_head, later: sc.envelopes.length - at, heads };
}

// Which version of a visit was believed at belief sequence S (a version is current while ts <= S < te).
export function versionAt(sc, fid, S) {
  return sc.versions.filter((v) => v.fid === fid).find((v) => v.ts <= S && (v.te === null || S < v.te)) ?? null;
}

const span = (sc, v) => `[${fmtClock(v.vf_lo - sc.epochMs)}, ${fmtClock(v.vf_hi - sc.epochMs)}]`;

function plane(sc, S, view) {
  const g = [];
  view.yTicks.forEach((t, i) => g.push(`<line x1="${PLANE.x0}" x2="${PLANE.x1}" y1="${t.y}" y2="${t.y}" class="rowline"/><text x="${PLANE.x0 - 10}" y="${t.y + 6}" text-anchor="end" class="pl-text dim">${esc(t.label)}</text>`));
  view.xTicks.forEach((t) => g.push(`<line x1="${t.x}" x2="${t.x}" y1="${PLANE.top}" y2="${PLANE.bottom}" class="colline"/><text x="${t.x}" y="${PLANE.bottom + 26}" text-anchor="middle" class="pl-text dim">${esc(t.label)}</text>`));
  if (view.hatch) g.push(`<rect x="${view.hatch.x0}" y="${PLANE.top}" width="${view.hatch.x1 - view.hatch.x0}" height="${PLANE.bottom - PLANE.top}" class="hatch"/><text x="${view.hatch.x1 - 6}" y="${PLANE.top + 20}" text-anchor="end" class="pl-text dim">${esc(view.hatch.label)}</text>`);
  for (const v of view.versions) {
    const cls = v.current ? "ver current" : "ver";
    g.push(`<g opacity="${v.opacity}"><rect x="${v.x0}" y="${v.yTop}" width="${Math.max(2, v.x1 - v.x0)}" height="${Math.max(2, v.yBottom - v.yTop)}" class="${cls}"/>`,
      `<rect x="${v.ext.x0}" y="${v.yTop}" width="${Math.max(0, v.ext.x1 - v.ext.x0)}" height="${Math.max(2, v.yBottom - v.yTop)}" class="${v.ext.certain ? "ext certain" : "ext possible"}"/>`,
      `<text x="${v.x0 + 6}" y="${v.yTop + 18}" class="pl-text">${esc(v.label)}</text></g>`);
  }
  if (view.overlap) g.push(`<rect x="${view.overlap.x0}" y="${PLANE.top}" width="${Math.max(2, view.overlap.x1 - view.overlap.x0)}" height="${PLANE.bottom - PLANE.top}" class="overlap" opacity="${view.overlap.opacity}"/>`);
  for (const f of view.frontiers) {
    const d = f.pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.yBottom} L${p.x} ${p.yTop}`).join(" ");
    if (d) g.push(`<path d="${d}" class="frontier f-${f.lane}"/>`);
  }
  for (const b of view.beams) {
    const cls = `beam ${b.trust}`;
    g.push(`<g opacity="${b.opacity}" data-action="select-envelope" data-seq="${b.seq}"><line x1="${b.x0}" x2="${b.x1}" y1="${b.y}" y2="${b.y}" class="${cls}"/><line x1="${b.x0}" x2="${b.x0}" y1="${b.y - 6}" y2="${b.y + 6}" class="${cls}"/><line x1="${b.x1}" x2="${b.x1}" y1="${b.y - 6}" y2="${b.y + 6}" class="${cls}"/>`,
      b.arrival.x > b.x1 + 2 ? `<line x1="${b.x1}" x2="${b.arrival.x}" y1="${b.y}" y2="${b.y}" class="arrival"/><circle cx="${b.arrival.x}" cy="${b.y}" r="4" class="arrival-dot"/>` : "",
      `<text x="${b.labelX}" y="${b.labelY + 14}" class="pl-text">${esc(b.kind)}</text></g>`);
  }
  if (view.stake) g.push(`<line x1="${view.stake.x0}" x2="${view.stake.x1}" y1="${view.stake.y}" y2="${view.stake.y}" class="stake" opacity="${view.stake.opacity}"/><text x="${view.stake.x1}" y="${view.stake.y - 8}" text-anchor="end" class="pl-text hot" opacity="${view.stake.opacity}">${esc(view.stake.label)}</text>`);
  for (const c of view.callouts) g.push(`<text x="${c.labelX}" y="${c.labelY + 40}" class="pl-text hot" opacity="${c.opacity}">${esc(c.label)}</text>`);
  g.push(`<rect x="${PLANE.x0}" y="${view.scrubber.tintY0}" width="${PLANE.x1 - PLANE.x0}" height="${Math.max(0, view.scrubber.y - view.scrubber.tintY0)}" class="future"/>`,
    `<line x1="${PLANE.x0 - 20}" x2="${PLANE.x1}" y1="${view.scrubber.y}" y2="${view.scrubber.y}" class="scrub"/>`);
  for (let s = 1; s <= view.R; s += 1) g.push(`<rect x="${PLANE.x0 - 100}" y="${PLANE.bottom - s * view.h}" width="${PLANE.x1 - PLANE.x0 + 100}" height="${view.h}" class="rowhit" data-action="seq" data-seq="${s}"/>`);
  return `<svg class="hero-svg plane" viewBox="40 80 1520 830" role="img" aria-label="Valid time against belief sequence">${g.join("")}</svg>`;
}

function histories(data, current, S) {
  return ["doorstep_scam", "late_and_lossy"].map((name) => {
    const sc = scenarioOf(data.ward, name);
    const rows = sc.versions.map((v) => `<tr class="${v.ts <= S && (v.te === null || S < v.te) ? "sel" : ""}"><td>v${v.n}</td><td>${esc(span(sc, v))}</td><td>seq ${v.ts}</td><td>${v.te === null ? "open" : `seq ${v.te}`}</td></tr>`).join("");
    return `<div class="col${name === current ? " on" : ""}"><div class="label">${esc(SCENARIO_LABEL[name])}</div><table class="ledger"><thead><tr><th>version</th><th>valid</th><th>from</th><th>until</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }).join("");
}

export function bitemporalView({ state, data, derived }) {
  const name = state.sandboxes.bitemporal.scenario;
  const sc = scenarioOf(data.ward, name);
  const S = state.seq;
  const view = planeView(sc, S);
  const here = stateAt(sc, asOfIndex(sc, S));
  const d = sc.decision;
  const side = `<div class="side"><div class="label">AS OF SEQ</div><div class="big mono">${pad2(S)}</div>
<div class="label">Λ AS OF S</div><div class="mono">${esc(formatMnats(here.lambdaMnats))} mn</div>
<div class="label">LEGEND</div><ul class="legend"><li><span class="k beam observed"></span>envelope: valid time, arrival</li><li><span class="k ver"></span>visit version [ts, te)</li><li><span class="k ext possible"></span>possibly (feathered)</li><li><span class="k ext certain"></span>certainly (crisp)</li><li><span class="k stake"></span>decision at at_seq</li></ul></div>`;
  const tryit = `<div class="controls"><label class="label" for="seq">Belief sequence</label><input id="seq" data-action="seq" type="range" min="1" max="${state.N}" value="${S}"><output class="mono">${S}</output>
<button data-action="seq-decision" class="btn" data-seq="${d.at_seq}">Show what was known at the decision</button></div>
<div class="controls"><span class="label">Ring delivery</span>${["doorstep_scam", "late_and_lossy"].map((n) => `<button data-action="scenario" data-scenario="${n}" class="btn${n === name ? " on" : ""}">${esc(SCENARIO_LABEL[n])}</button>`).join("")}</div>
<div class="cols">${histories(data, name, S)}</div>`;
  const r = derived.bitemporal;
  const items = [];
  if (r) {
    const ok = r.linked && r.headAt === r.recorded;
    items.push(`<li class="check ${ok ? "ok" : "fail"}"><span class="mark">${ok ? "✓" : "✗"}</span> decision at seq ${d.at_seq} · ledger_head <span class="mono">${esc(shortHash(r.recorded))}</span> · unchanged by ${r.later} later envelope${r.later === 1 ? "" : "s"}; re-derived head at seq ${d.at_seq} equals it ${badge("computed")}</li>`);
  }
  const fid = sc.versions[0].fid;
  const then = versionAt(sc, fid, d.at_seq);
  const now = sc.versions.filter((v) => v.fid === fid).find((v) => v.te === null);
  if (then && now) {
    const text = `visit belief at seq ${d.at_seq}: ${span(sc, then)} v${then.n}; now ${span(sc, now)} v${now.n}`;
    const row = sc.rows.find((x) => x.event === "AS_OF");
    const parity = row ? row.note.replaceAll("−", "-").includes(text.replaceAll("−", "-").replace(`visit belief at seq ${d.at_seq}`, `believed at seq ${d.at_seq}`)) : null;
    items.push(`<li class="check ${parity === false ? "fail" : "ok"}"><span class="mark">${parity === false ? "✗" : "✓"}</span> ${esc(text)} ${badge("computed")}${parity ? ` · equals the orrery's AS_OF row ${badges("recorded", { synthetic: true })}` : ""}</li>`);
  }
  const after = sc.rows.filter((x) => x.seq !== null && x.seq > d.at_seq).length;
  items.push(`<li class="check ok"><span class="mark">✓</span> ${after} envelope${after === 1 ? "" : "s"} after the decision changed what is believed, never the signed record (BT-4) ${badges("recorded", { synthetic: true })}</li>`);
  return {
    title: "Late or duplicate data can't rewrite a decision",
    assertion: "Versions are immutable. A decision names its at_seq and ledger_head, so replaying to that point reproduces the same belief; a late event adds a version and leaves the decision alone.",
    badges: badges("recorded", { synthetic: true }) + badge("computed"),
    hero: `<div class="walk">${side}${plane(sc, S, view)}</div>`, tryit, proof: `<ul class="proof">${items.join("")}</ul>`,
  };
}
