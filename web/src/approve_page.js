// Glue for approve.html. The link carries #id=<challenge_id>&rp=<rp_id>; the broker's endpoints return the
// challenge document (the fields the action hash is bound to), the action hash and the member's passkey ids.
import { requestApproval } from "./approval.js";
import { escapeHtml } from "./render.js";
import { summarize } from "./approval.js";

const params = new URLSearchParams(location.hash.slice(1));
const id = params.get("id");
const status = document.querySelector("#status");
const button = document.querySelector("#approve");

async function load() {
  if (!id || !/^chl_[0-9A-HJKMNP-TV-Z]{26}$/.test(id)) throw new Error("This link is not a valid approval link.");
  const reply = await fetch(`/api/challenges/${id}`, { headers: { accept: "application/json" } });
  if (!reply.ok) throw new Error("This approval has expired or does not exist.");
  return reply.json();
}

let challenge;
try {
  challenge = await load();
  document.querySelector("#summary").textContent = summarize(challenge.document);
  button.disabled = false;
} catch (error) {
  status.textContent = error.message;
}

button.addEventListener("click", async () => {
  button.disabled = true;
  status.textContent = "Waiting for your passkey...";
  try {
    const assertion = await requestApproval({
      doc: challenge.document, challengeHex: challenge.action_hash, rpId: params.get("rp") ?? location.hostname,
      credentialIds: challenge.credential_ids,
    });
    const reply = await fetch(`/api/challenges/${id}/approve`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(assertion),
    });
    const answer = await reply.json();
    status.innerHTML = `Result: <strong>${escapeHtml(answer.status)}</strong>`;
  } catch (error) {
    status.textContent = `Not approved: ${error.message}`;
    button.disabled = false;
  }
});
