// HTML as strings, with every dynamic value escaped. No innerHTML of anything unescaped, no inline script.

export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function renderChecks(checks) {
  return `<ul class="checks">${checks
    .map((c) => `<li class="${c.ok ? "pass" : "fail"}"><strong>${c.ok ? "PASS" : "FAIL"}</strong> ${escapeHtml(c.name)}<span>${escapeHtml(c.detail)}</span></li>`)
    .join("")}</ul>`;
}

export function renderDecision(record) {
  if (!record) return "<p>No record: nothing here verified.</p>";
  const rows = [
    ["Episode", record.episode], ["Decision", record.decision], ["Evidence (milli-nats)", record.lambda_mnats],
    ["At ledger sequence", record.at_seq], ["Ledger head", record.ledger_head], ["Reasons", record.reasons.join(", ") || "none"],
    ["Cross-device terms", Array.isArray(record.cross_terms) ? record.cross_terms.join(", ") || "none" : Object.keys(record.cross_terms).join(", ") || "none"],
    ["Parameters digest", record.params_digest],
  ];
  return `<dl>${rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>`;
}

export function renderAnchor(doc) {
  if (!doc) return "";
  const leaves = doc.leaves.map((l) => `<tr><td>${escapeHtml(l.household)}</td><td>${escapeHtml(l.seq)}</td><td>${escapeHtml(l.head)}</td></tr>`).join("");
  return `<h3>Merkle anchor</h3><p>Root <code>${escapeHtml(doc.root)}</code> over ${escapeHtml(doc.count)} households at ${escapeHtml(new Date(doc.at_ms).toISOString())}</p>` +
    `<table><thead><tr><th>Household</th><th>Seq</th><th>Head</th></tr></thead><tbody>${leaves}</tbody></table>`;
}

// The whole verification page body: the verdict, every check and, only when everything verified, the record.
export function renderResult(result) {
  const verdict = `<h2 class="${result.ok ? "pass" : "fail"}">${result.ok ? "Verified" : "Not verified"}</h2>`;
  return verdict + renderChecks(result.checks) + (result.ok ? renderDecision(result.record) + renderAnchor(result.anchor) : "");
}
