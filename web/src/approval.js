// The approval page's logic (blueprint section 5.5, step 5). The browser never decides anything: it shows what is
// being approved, checks that what it shows is what the challenge is bound to, and has the passkey sign
// challenge = action_hash. The server's broker verifies the assertion and applies the rules.

import { actionHash } from "./attest.js";
import { b64urlToBytes, bytesToB64url, bytesToHex, hexToBytes } from "./encoding.js";

const FIELDS = ["tool", "args", "household", "episode_version", "nonce", "exp"];
const TOOLS = {
  dismiss_alert: (a) => `Dismiss the alert ${a.episode_id} as ${String(a.reason).replaceAll("_", " ")}`,
  pause_monitoring: (a) => `Pause monitoring for ${a.hours} hour${a.hours === 1 ? "" : "s"} (${a.reason})`,
  add_trusted_vendor: (a) => `Add ${a.name} (${a.category}) as a trusted vendor`,
  edit_trusted_circle: (a) => `${a.operation === "add" ? "Add" : "Remove"} ${a.display_name} ${a.operation === "add" ? "to" : "from"} the trusted circle`,
  share_evidence: (a) => `Share the evidence for ${a.episode_id} with the ${String(a.recipient).replaceAll("_", " ")}`,
};

// A sentence for the caregiver. Display only; unknown tools are refused rather than described loosely.
export function summarize(doc) {
  const describe = TOOLS[doc.tool];
  if (!describe) throw new Error(`unknown action: ${String(doc.tool).slice(0, 40)}`);
  return describe(doc.args);
}

// The document the broker bound the challenge to must reproduce the challenge. If it does not, the page shows
// nothing to approve: what is displayed would not be what is signed.
export async function checkDisplayed(doc, challengeHex) {
  const bound = Object.fromEntries(FIELDS.map((k) => [k, doc[k]]));
  if (FIELDS.some((k) => !(k in doc))) throw new Error("the challenge document is incomplete");
  const recomputed = bytesToHex(await actionHash(bound));
  if (recomputed !== challengeHex) throw new Error("the displayed action does not match the challenge");
  return summarize(doc);
}

export function assertionRequest({ challengeHex, rpId, credentialIds, timeoutMs = 60000 }) {
  if (!Array.isArray(credentialIds) || credentialIds.length === 0) throw new Error("no passkeys to ask for");
  return {
    challenge: hexToBytes(challengeHex),
    rpId,
    allowCredentials: credentialIds.map((id) => ({ type: "public-key", id: b64urlToBytes(id) })),
    userVerification: "required",
    timeout: timeoutMs,
  };
}

// The JSON the broker's approve endpoint takes; field names match authz.webauthn_rp.Assertion.
export function serializeAssertion(credential) {
  const r = credential.response;
  return {
    credential_id: bytesToB64url(new Uint8Array(credential.rawId)),
    client_data_json: bytesToB64url(new Uint8Array(r.clientDataJSON)),
    authenticator_data: bytesToB64url(new Uint8Array(r.authenticatorData)),
    signature: bytesToB64url(new Uint8Array(r.signature)),
  };
}

export async function requestApproval({ doc, challengeHex, rpId, credentialIds, credentials = globalThis.navigator?.credentials }) {
  await checkDisplayed(doc, challengeHex);
  const credential = await credentials.get({ publicKey: assertionRequest({ challengeHex, rpId, credentialIds }) });
  return serializeAssertion(credential);
}
