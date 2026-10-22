# Wardline

A standalone web instrument that tells HEARTHWARD's thesis in a deterministic 90-second performance meant for screen
recording. No dependencies, no network, no build step.

1. **I MEMBRANE.** Evidence of danger moves the log-likelihood ratio Λ freely. A request to *lower* protection (the
   S- tool `dismiss_alert`, spoken in the room) hits the ward line and bounces off it, because it needs a trusted-circle
   member's passkey (HX-01, HX-02).
2. **II BITEMPORAL PLANE.** Valid time against belief time. Late, duplicated and recovered events change what
   HEARTHWARD believes, never the decision it already made (BT-1 to BT-4).
3. **III CRYPTOGRAPHIC ZIPPER.** The ledger's hash chain and the signed decision record are verified in the browser.
   Tampering opens the zipper; re-chaining to hide it is still caught by the signed head.

## Synthetic data, and what the signature is

**Everything on screen is synthetic.** Every event, hash and number comes from the reference simulator
(`tools/orrery.py`, seed 7). The page says `SYNTHETIC DEMO DATA · ORRERY SEED 7` in every act.

The decision record is signed with a throwaway, public, **synthetic demo key** (`key_id synthetic-demo-key`), **not KMS**.
`data.js` carries only the SPKI public key, the DER signature and the signed bytes; there is no private key anywhere.
Verification is display-only (TB-8): the browser authorizes nothing. L3 is deferred, and this replay does not run the
Step Functions intervention ladder (§8).

No performance number is shown unless it was measured, with its method. The debug overlay (`D`) reports the renderer,
the render scale and the mean of the last 60 `requestAnimationFrame` intervals, and nothing else.

## Run it

ES modules do not load from `file://`. From the repository root:

```bash
uv run python -m http.server 8765 --bind 127.0.0.1 --directory web
```

Then open <http://127.0.0.1:8765/wardline/>. Nothing under `web/` is deployed (`.dockerignore` admits only `src/` and
the Dockerfiles, and `infra/app.py` has no static hosting).

The stage is a logical 1920 × 1080 canvas, scaled to fit and letterboxed. It works best at 1280 px wide or more. Below
900 px a notice says so; the transcript below the stage carries every sentence.

## Keys

| Key | Action |
|---|---|
| Space | Play or pause. At 90 s, restart from 0. |
| 1 / 2 / 3 | Seek to 0 / 35 s / 65 s, keeping the play state |
| ← / → | Previous or next cue start, and pause |
| Shift + ← / → | Move ∓1 s, and pause |
| R | Seek to 0, pause, and clear any manual tamper |
| V | Act III: re-run verification live, animated |
| T | Act III: cycle tamper → re-chain → restore |
| H | Hide or show the buttons (the progress line and time code stay) |
| M | Toggle reduced motion |
| D | Toggle the debug overlay |
| F | Toggle fullscreen (on `#viewport`, the element that holds `#stage`) |

Keys pressed with Ctrl, Meta or Alt are ignored, and so is auto-repeat (except on the arrows).

## URL parameters

| Parameter | Effect |
|---|---|
| `?t=<seconds>` | Seek and pause there (deterministic stills) |
| `?autoplay=1` | Start playing |
| `?gl=0` | Force the Canvas 2D fallback |
| `?motion=0` | Force reduced motion |
| `?hints=0` | Start with the buttons hidden |

`prefers-reduced-motion: reduce` also starts in reduced motion. Springs become steps, chips and particles appear in
place, the membrane wave and the shock ring are off, cross-fades become cuts and the belief scrubber steps once per
cue. The timeline is unchanged.

## Recording

- Press `H` (or open with `?hints=0`) to hide the buttons; then press Space to start. The frame is a 16:9 stage.
- The page is still until something changes: when paused it stops its animation loop and keeps its last frame.
- Every frame is a pure function of the director's time. Seek with `?t=` and the picture is the same every time.
- If a machine cannot keep up, the governor (while playing) lowers the light layer's resolution once and then falls
  back to Canvas 2D for the rest of the session. It never steps back up.

## Files

| File | Role |
|---|---|
| `index.html`, `wardline.css` | Shell with a CSP (no inline script or style), tokens and the HUD |
| `main.js` | DOM wiring, clock, input, URL parameters, governor |
| `data.js` | **Generated** by `export_data.py`; never hand-edited |
| `export_data.py` | Generator; imports `tools/` read-only |
| `model.js` | Pure: data → geometry, state as of a row, sentences, formatters |
| `director.js` | Pure: time → frame; the 90 s choreography and the key map |
| `chain.js` | HW-JCS, SHA-256 chain replay, DER → raw P-256, ECDSA verify, tamper and re-chain |
| `plane.svg.js` | SVG ink layer (crisp lines and labels of all three acts, tiles, zipper, background registration marks) |
| `ward.gl.js`, `fallback.c2d.js` | Light layer: WebGL2 shader, and the Canvas 2D equivalent |
| `tests/` | `node:test` suites: chain, model, director, static |

## Regenerate and test

```bash
uv run python web/wardline/export_data.py            # rewrite data.js (byte-deterministic)
uv run python web/wardline/export_data.py --check    # exit 1 if data.js is stale
cd web && node --test wardline/tests/*.test.js       # Wardline's suites
cd web && npm test                                   # everything under web/, including the existing tests
```

`.gitattributes` here keeps `data.js` byte-exact (`-text`); this repository's Windows checkouts use
`core.autocrlf=true`.
