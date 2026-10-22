// Truth in advertising (§6.2) and static hygiene: no status literals, no network, one relative module graph.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";
import { STATUS_WORDS } from "../provenance.js";

const root = new URL("../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8");
const jsFiles = ["app.js", "store.js", "provenance.js", "inspector.js", "views.js",
  ...readdirSync(new URL("claims/", root)).map((f) => `claims/${f}`)];
const html = read("index.html");
const css = read("console.css");

test("index.html: the CSP, one module script, nothing inline", () => {
  assert.match(html, /<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'">/);
  const scripts = [...html.matchAll(/<script\b[^>]*>/g)].map((m) => m[0]);
  assert.deepEqual(scripts, ['<script type="module" src="app.js">']);
  assert.equal(/<style\b/i.test(html), false);
  assert.equal(/\sstyle\s*=/i.test(html), false);
  assert.equal(/\son[a-z]+\s*=/i.test(html), false);
  assert.match(html, /<noscript>/);
  assert.match(html, /SYNTHETIC DEMO DATA/);
});

test("no source or template emits an inline style or event-handler attribute", () => {
  for (const file of jsFiles) {
    const src = read(file);
    assert.equal(/\sstyle="/.test(src), false, `${file}: style attribute`);
    assert.equal(/\son(click|input|change|load|error)=/i.test(src), false, `${file}: handler attribute`);
  }
});

test("T-1: no source states a system status as a literal", () => {
  for (const file of jsFiles) {
    if (file === "provenance.js") continue; // it holds the guard's own word list
    const src = read(file);
    for (const word of STATUS_WORDS) assert.equal(new RegExp(`\\b${word}\\b`, "i").test(src), false, `${file}: ${word}`);
  }
  assert.equal(new RegExp(STATUS_WORDS.join("|"), "i").test(html), false);
});

test("T-2, T-3: no performance claims, no device names, no 'intercept'", () => {
  for (const file of [...jsFiles, "index.html", "console.css"]) {
    const raw = read(file);
    assert.equal(/BLE/.test(raw), false, `${file}: BLE`);
    const src = raw.toLowerCase();
    for (const banned of ["60 fps", "60fps", "intercept", "hardware mesh", "bluetooth", "ring doorbell", "bee pioneer"]) {
      assert.equal(src.includes(banned), false, `${file}: ${banned}`);
    }
  }
});

test("no http(s) URL appears except the SVG namespace, and fetch( is confined to boundaries.js", () => {
  for (const file of [...jsFiles, "index.html", "console.css"]) {
    const src = read(file);
    const urls = [...src.matchAll(/https?:\/\/[^\s"'`)]+/g)].map((m) => m[0]).filter((u) => u !== "http://www.w3.org/2000/svg");
    assert.deepEqual(urls, [], `${file}: URLs`);
  }
  for (const file of jsFiles) {
    for (const banned of ["XMLHttpRequest", "WebSocket", "eval(", "importScripts", "sendBeacon", "new Function"]) {
      assert.equal(read(file).includes(banned), false, `${file}: ${banned}`);
    }
    if (file !== "claims/boundaries.js") assert.equal(read(file).includes("fetch("), false, `${file}: fetch(`);
  }
  assert.equal(/@import|url\(/.test(css), false);
});

test("every import is relative; Wardline is imported only through chain.js and model.js (§2.2)", () => {
  for (const file of jsFiles) {
    for (const m of read(file).matchAll(/(?:^|\n)\s*(?:import|export)\b[^;]*?\bfrom\s+["']([^"']+)["']/g)) {
      assert.ok(m[1].startsWith("./") || m[1].startsWith("../"), `${file}: ${m[1]}`);
      if (m[1].includes("wardline")) assert.match(m[1], /wardline\/(chain|model)\.js$/, `${file}: ${m[1]}`);
    }
  }
});

test("SYNTHETIC is present in the page and the views; the page never writes under web/wardline", () => {
  assert.ok(read("provenance.js").includes("SYNTHETIC"));
  for (const file of jsFiles) assert.equal(/writeFile|appendFile|createWriteStream/.test(read(file)), false, file);
});

test("the claim titles keep their assertions honest: KMS is named only when the bundle is recorded", () => {
  const src = read("claims/integrity.js");
  assert.match(src, /signed by AWS KMS/);
  assert.match(src, /demo key, not KMS/);
});
