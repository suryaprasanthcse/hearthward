// Provenance badges (§1.3) and the truth-in-advertising guard (§1.4). Pure: it builds strings and decides labels.
// A panel without a badge fails the static test, and a status word is never written as a literal (T-1).

export const BADGES = Object.freeze({
  live: "LIVE",
  computed: "COMPUTED",
  recorded: "RECORDED",
  synthetic: "SYNTHETIC",
});

export const esc = (value) =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// One badge, optionally with a detail such as a UTC time: `[ RECORDED 2026-10-22T08:25:43Z ]`.
export function badge(kind, detail = "") {
  if (!(kind in BADGES)) throw new Error(`unknown badge ${kind}`);
  const text = detail ? `${BADGES[kind]} ${detail}` : BADGES[kind];
  return `<span class="badge b-${kind}" data-badge="${kind}">[ ${esc(text)} ]</span>`;
}

// D-1: every datum from the orrery carries SYNTHETIC beside the badge that says how it was produced.
export function badges(kind, { synthetic = false, detail = "" } = {}) {
  return badge(kind, detail) + (synthetic && kind !== "synthetic" ? badge("synthetic") : "");
}

// Words the page may only show when data or computation says so (T-1). The test scans source literals for these.
export const STATUS_WORDS = Object.freeze(["ONLINE", "HEALTHY", "ENCLAVE", "ACTIVE"]);
export const findStatusWords = (text) => STATUS_WORDS.filter((w) => new RegExp(`\\b${w}\\b`, "i").test(text));

// state.provenance, set at boot from which recorded files exist and where the page was served from (§3.1).
export function deriveProvenance(data, origin = "") {
  const bundle = data.recorded?.bundle ?? null;
  const probes = data.recorded?.probes ?? null;
  return {
    kms: bundle && bundle.data?.kms?.key_arn ? "recorded" : "synthetic-only",
    probes: probes ? "recorded" : "absent",
    hosting: /(^|\.)cloudfront\.net(:\d+)?$/.test(String(origin).replace(/^https?:\/\//, "")) ? "cloudfront" : "local",
  };
}

// T-5: a real KMS signature over a synthetic record is labelled exactly that way; the demo key says it is not KMS.
export function keyLabel(keyId, provenance) {
  return provenance.kms === "recorded" ? `${keyId}` : `${keyId} (not KMS)`;
}

// T-4: components that exist as code but are not deployed, or are deferred. Shown wherever they appear.
export const DEPLOYMENT = Object.freeze({
  broker: "built, not deployed",
  ladder: "built, not deployed",
  anchor_bucket: "built, not deployed",
  l3: "deferred",
});
