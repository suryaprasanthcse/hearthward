// Wardline's light layer: one WebGL2 pass drawing the background, the vignette, the dither and all light.
// It reads frame.glow from director.frameAt(); the crisp lines are SVG (plane.svg.js). Returns null when WebGL2 is
// unavailable or a shader fails to compile; fallback.c2d.js implements the same interface.

const VERT = `#version 300 es
const vec2 V[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
void main() { gl_Position = vec4(V[gl_VertexID], 0.0, 1.0); }
`;

const FRAG = `#version 300 es
precision highp float;
uniform vec2  u_res;        // drawing-buffer px
uniform float u_t;          // director time, s
uniform float u_fade;       // Act I light opacity, 0..1 (0 in Acts II and III: background only)
uniform vec2  u_field;      // ward x extent (560, 1824)
uniform float u_yA, u_yB;   // ward lines, stage px
uniform float u_aLit, u_bLit;
uniform vec3  u_cross;      // crossing x, y, t0 (t0 < 0: none)
uniform vec4  u_waveA, u_waveB;  // impact x0, t0, amplitude px, enabled
uniform vec2  u_point;
uniform vec2  u_trail[64];
uniform int   u_trailN;
uniform vec4  u_part[8];    // x, y, kind (0 S- request, 1 verified, 2 tactic), alpha
uniform int   u_partN;
out vec4 outColor;

const vec2 STAGE = vec2(1920.0, 1080.0);
const vec3 BG   = vec3(0.0314, 0.0431, 0.0627);  // --bg-0
const vec3 INK  = vec3(0.9490, 0.9647, 0.9804);  // --ink-1
const vec3 SCAM = vec3(0.949, 0.639, 0.227);     // --toward-scam
const vec3 HOT  = vec3(1.000, 0.365, 0.310);     // --intervene
const vec3 SAFE = vec3(0.180, 0.769, 0.714);     // --toward-benign
const vec3 VER  = vec3(0.557, 0.635, 1.000);     // --verified

float glow(float d, float w) { return exp(-(d * d) / (w * w)); }
float segDist(vec2 p, vec2 a, vec2 b) {
  vec2 ab = b - a;
  float h = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-4), 0.0, 1.0);
  return length(p - a - h * ab);
}
float wave(vec4 w, float x) {               // membrane displacement in px, + is down
  float dt = u_t - w.y;
  if (w.w < 0.5 || dt < 0.0) return 0.0;
  float u = (x - w.x) / 60.0;               // u*u, never pow(): pow of a negative base is undefined in GLSL
  return w.z * exp(-u * u) * exp(-dt / 0.35) * cos(18.849556 * dt);   // 2π · 3 Hz
}
float hash12(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main() {
  vec2 p = vec2(gl_FragCoord.x, u_res.y - gl_FragCoord.y) * (STAGE / u_res);
  vec3 light = vec3(0.0);
  vec3 hot = mix(SCAM, HOT, u_aLit);
  if (p.x >= u_field.x && p.x <= u_field.y) {
    float flash = u_cross.z >= 0.0 ? exp(-max(u_t - u_cross.z, 0.0) / 0.7) : 0.0;
    light += hot  * glow(abs(p.y - (u_yA + wave(u_waveA, p.x))), mix(24.0, 40.0, flash)) * mix(0.30, 0.85, u_aLit);
    light += SAFE * glow(abs(p.y - (u_yB + wave(u_waveB, p.x))), 20.0) * mix(0.22, 0.75, u_bLit);
  }
  float trail = 0.0;
  for (int i = 0; i < 63; i++) {
    if (i + 1 >= u_trailN) break;
    float age = float(i + 1) / float(max(u_trailN - 1, 1));        // 0 oldest .. 1 newest
    trail = max(trail, (0.35 + 0.65 * age) * glow(segDist(p, u_trail[i], u_trail[i + 1]), 10.0));
  }
  light += hot * trail * 0.55;
  float dp = length(p - u_point);
  light += INK * (0.9 * glow(dp, 18.0) + (1.0 - smoothstep(6.0, 7.5, dp)));  // never smoothstep(7.5, 6.0, …): undefined
  if (u_cross.z >= 0.0) {
    float k = (u_t - u_cross.z) / 0.7;
    if (k >= 0.0 && k <= 1.0) {
      float r = 648.0 * (1.0 - (1.0 - k) * (1.0 - k) * (1.0 - k));
      light += HOT * glow(abs(length(p - u_cross.xy) - r), 6.0) * 0.45 * (1.0 - k);
    }
  }
  for (int i = 0; i < 8; i++) {
    if (i >= u_partN) break;
    vec4 q = u_part[i];
    vec3 col = q.z < 0.5 ? INK : (q.z < 1.5 ? VER : SCAM);
    float d = length(p - q.xy);
    float ring = (q.z > 0.5 && q.z < 1.5) ? glow(abs(d - 16.0), 1.5) : 0.0;
    light += col * q.w * (0.8 * glow(d, 12.0) + ring);
  }
  vec2 uv = p / STAGE - 0.5;
  float vignette = 1.0 - 0.30 * smoothstep(0.45, 0.85, length(uv * vec2(1.0, 0.75)) * 1.4);
  vec3 color = (BG + light * u_fade) * vignette;
  color += (hash12(gl_FragCoord.xy) - 0.5) / 255.0;   // static dither: no banding, no shimmer after video compression
  outColor = vec4(clamp(color, 0.0, 1.0), 1.0);
}
`;

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

export function createGlowRenderer(canvas, { onLost } = {}) {
  let gl = null;
  try {
    gl = canvas.getContext("webgl2", { alpha: false, antialias: false, premultipliedAlpha: false, preserveDrawingBuffer: false });
  } catch {
    gl = null;
  }
  if (!gl) return null;
  const vs = compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return null;
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  gl.useProgram(program);
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const u = (name) => gl.getUniformLocation(program, name);
  const loc = {
    res: u("u_res"), t: u("u_t"), fade: u("u_fade"), field: u("u_field"), yA: u("u_yA"), yB: u("u_yB"), aLit: u("u_aLit"),
    bLit: u("u_bLit"), cross: u("u_cross"), waveA: u("u_waveA"), waveB: u("u_waveB"), point: u("u_point"),
    trail: u("u_trail[0]"), trailN: u("u_trailN"), part: u("u_part[0]"), partN: u("u_partN"),
  };
  const trail = new Float32Array(128);
  const part = new Float32Array(32);
  const lost = (event) => { event.preventDefault(); if (onLost) onLost(); };
  canvas.addEventListener("webglcontextlost", lost);

  return {
    kind: "webgl2",
    draw(frame) {
      const g = frame.glow;
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform2f(loc.res, canvas.width, canvas.height);
      gl.uniform1f(loc.t, g.t);
      gl.uniform1f(loc.fade, g.fade);
      gl.uniform2f(loc.field, g.field[0], g.field[1]);
      gl.uniform1f(loc.yA, g.yA);
      gl.uniform1f(loc.yB, g.yB);
      gl.uniform1f(loc.aLit, g.aLit);
      gl.uniform1f(loc.bLit, g.bLit);
      gl.uniform3f(loc.cross, g.cross[0], g.cross[1], g.cross[2]);
      gl.uniform4f(loc.waveA, g.waveA[0], g.waveA[1], g.waveA[2], g.waveA[3]);
      gl.uniform4f(loc.waveB, g.waveB[0], g.waveB[1], g.waveB[2], g.waveB[3]);
      gl.uniform2f(loc.point, g.point[0], g.point[1]);
      trail.fill(0);
      const n = Math.min(g.trail.length, 64);
      for (let i = 0; i < n; i += 1) { trail[2 * i] = g.trail[i][0]; trail[2 * i + 1] = g.trail[i][1]; }
      gl.uniform2fv(loc.trail, trail);
      gl.uniform1i(loc.trailN, n);
      part.fill(0);
      const m = Math.min(g.part.length, 8);
      for (let i = 0; i < m; i += 1) part.set(g.part[i], 4 * i);
      gl.uniform4fv(loc.part, part);
      gl.uniform1i(loc.partN, m);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    resize(cssW, cssH, scale) {
      canvas.width = Math.max(1, Math.round(cssW * scale));
      canvas.height = Math.max(1, Math.round(cssH * scale));
    },
    dispose() {
      canvas.removeEventListener("webglcontextlost", lost);
      gl.deleteProgram(program);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      gl.deleteVertexArray(vao);
    },
  };
}
