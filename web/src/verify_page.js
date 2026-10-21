// Glue for verify.html: pick a bundle file, verify it in the browser, show what verified.
import { verifyBundle } from "./attest.js";
import { renderResult } from "./render.js";

const input = document.querySelector("#bundle");
const output = document.querySelector("#result");

input.addEventListener("change", async () => {
  output.textContent = "Checking...";
  try {
    const bundle = JSON.parse(await input.files[0].text());
    const result = await verifyBundle(bundle);
    output.innerHTML = renderResult(result);
  } catch (error) {
    output.textContent = `Could not read the bundle: ${error.message}`;
  }
});
