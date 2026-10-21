// A self-contained WebGL2 fluid: the incompressible Navier-Stokes equations on a grid, solved on the GPU each frame
// by semi-Lagrangian advection, vorticity confinement, a divergence pass, a Jacobi pressure solve and a gradient
// subtraction (Stam's "stable fluids"). Dye is advected through the velocity field and shown over a dark void.
// No libraries. If WebGL2 or float render targets are missing, createFluid returns null and the page carries on.

const VERT = `#version 300 es
precision highp float;
in vec2 aPosition;
out vec2 vUv; out vec2 vL; out vec2 vR; out vec2 vT; out vec2 vB;
uniform vec2 texel;
void main() {
  vUv = aPosition * 0.5 + 0.5;
  vL = vUv - vec2(texel.x, 0.0); vR = vUv + vec2(texel.x, 0.0);
  vT = vUv + vec2(0.0, texel.y); vB = vUv - vec2(0.0, texel.y);
  gl_Position = vec4(aPosition, 0.0, 1.0);
}`;

const HEAD = `#version 300 es
precision highp float; precision highp sampler2D;
in vec2 vUv; in vec2 vL; in vec2 vR; in vec2 vT; in vec2 vB;
out vec4 outColor;
`;

const FRAG = {
  splat: `${HEAD}
uniform sampler2D uTarget; uniform float aspect; uniform vec3 color; uniform vec2 point; uniform float radius;
void main() {
  vec2 p = vUv - point; p.x *= aspect;
  vec3 splat = exp(-dot(p, p) / radius) * color;
  outColor = vec4(texture(uTarget, vUv).xyz + splat, 1.0);
}`,
  advection: `${HEAD}
uniform sampler2D uVelocity; uniform sampler2D uSource; uniform vec2 texel; uniform float dt; uniform float dissipation;
void main() {
  vec2 coord = vUv - dt * texture(uVelocity, vUv).xy * texel;
  outColor = texture(uSource, coord) / (1.0 + dissipation * dt);
}`,
  divergence: `${HEAD}
uniform sampler2D uVelocity;
void main() {
  float L = texture(uVelocity, vL).x; float R = texture(uVelocity, vR).x;
  float T = texture(uVelocity, vT).y; float B = texture(uVelocity, vB).y;
  vec2 C = texture(uVelocity, vUv).xy;
  if (vL.x < 0.0) L = -C.x; if (vR.x > 1.0) R = -C.x; if (vT.y > 1.0) T = -C.y; if (vB.y < 0.0) B = -C.y;
  outColor = vec4(0.5 * (R - L + T - B), 0.0, 0.0, 1.0);
}`,
  curl: `${HEAD}
uniform sampler2D uVelocity;
void main() {
  float L = texture(uVelocity, vL).y; float R = texture(uVelocity, vR).y;
  float T = texture(uVelocity, vT).x; float B = texture(uVelocity, vB).x;
  outColor = vec4(0.5 * (R - L - T + B), 0.0, 0.0, 1.0);
}`,
  vorticity: `${HEAD}
uniform sampler2D uVelocity; uniform sampler2D uCurl; uniform float curl; uniform float dt;
void main() {
  float L = texture(uCurl, vL).x; float R = texture(uCurl, vR).x;
  float T = texture(uCurl, vT).x; float B = texture(uCurl, vB).x; float C = texture(uCurl, vUv).x;
  vec2 force = 0.5 * vec2(abs(T) - abs(B), abs(R) - abs(L));
  force /= length(force) + 0.0001; force *= curl * C; force.y *= -1.0;
  vec2 velocity = texture(uVelocity, vUv).xy + force * dt;
  outColor = vec4(clamp(velocity, -1000.0, 1000.0), 0.0, 1.0);
}`,
  pressure: `${HEAD}
uniform sampler2D uPressure; uniform sampler2D uDivergence;
void main() {
  float L = texture(uPressure, vL).x; float R = texture(uPressure, vR).x;
  float T = texture(uPressure, vT).x; float B = texture(uPressure, vB).x;
  float divergence = texture(uDivergence, vUv).x;
  outColor = vec4((L + R + B + T - divergence) * 0.25, 0.0, 0.0, 1.0);
}`,
  gradient: `${HEAD}
uniform sampler2D uPressure; uniform sampler2D uVelocity;
void main() {
  float L = texture(uPressure, vL).x; float R = texture(uPressure, vR).x;
  float T = texture(uPressure, vT).x; float B = texture(uPressure, vB).x;
  vec2 velocity = texture(uVelocity, vUv).xy - vec2(R - L, T - B);
  outColor = vec4(velocity, 0.0, 1.0);
}`,
  scale: `${HEAD}
uniform sampler2D uTexture; uniform float value;
void main() { outColor = value * texture(uTexture, vUv); }`,
  display: `${HEAD}
uniform sampler2D uDye;
void main() {
  vec3 dye = texture(uDye, vUv).rgb;
  vec3 mapped = 1.0 - exp(-dye * 1.4);              // soft clip so bright shocks glow instead of clipping
  vec3 void_ = vec3(0.004, 0.016, 0.026);           // the deep void behind everything
  outColor = vec4(void_ + mapped, 1.0);
}`,
};

export function createFluid(canvas, options = {}) {
  const simRes = options.simRes ?? 128;
  const dyeRes = options.dyeRes ?? 512;
  const gl = canvas.getContext("webgl2", { alpha: false, antialias: false, depth: false, stencil: false });
  if (!gl || !gl.getExtension("EXT_color_buffer_float")) return null;

  const compile = (type, source) => {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  };
  const vertex = compile(gl.VERTEX_SHADER, VERT);
  const programs = {};
  for (const [name, source] of Object.entries(FRAG)) {
    const program = gl.createProgram();
    gl.attachShader(program, vertex);
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, source));
    gl.bindAttribLocation(program, 0, "aPosition");
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    const uniforms = {};
    for (let i = 0; i < gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS); i += 1) {
      const info = gl.getActiveUniform(program, i);
      uniforms[info.name] = gl.getUniformLocation(program, info.name);
    }
    programs[name] = { program, uniforms };
  }

  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, -1, 1, 1, 1, 1, -1]), gl.STATIC_DRAW);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0, 1, 2, 0, 2, 3]), gl.STATIC_DRAW);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.enableVertexAttribArray(0);

  const size = (res) => {
    const aspect = gl.drawingBufferWidth / gl.drawingBufferHeight;
    return aspect >= 1 ? { w: Math.round(res * aspect), h: res } : { w: res, h: Math.round(res / aspect) };
  };

  const target = (w, h, internal, format, filter) => {
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, gl.HALF_FLOAT, null);
    const fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    return { texture, fbo, w, h, texel: [1 / w, 1 / h], bind(unit) { gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, texture); return unit; } };
  };
  const pair = (...args) => {
    let read = target(...args);
    let write = target(...args);
    return { get read() { return read; }, get write() { return write; }, swap() { [read, write] = [write, read]; } };
  };

  let velocity, dye, pressure, divergence, curl;
  const build = () => {
    const sim = size(simRes);
    const hi = size(dyeRes);
    velocity = pair(sim.w, sim.h, gl.RG16F, gl.RG, gl.LINEAR);
    dye = pair(hi.w, hi.h, gl.RGBA16F, gl.RGBA, gl.LINEAR);
    pressure = pair(sim.w, sim.h, gl.R16F, gl.RED, gl.NEAREST);
    divergence = target(sim.w, sim.h, gl.R16F, gl.RED, gl.NEAREST);
    curl = target(sim.w, sim.h, gl.R16F, gl.RED, gl.NEAREST);
  };

  const use = (name) => {
    gl.useProgram(programs[name].program);
    return programs[name].uniforms;
  };
  const blit = (to) => {
    if (to) { gl.viewport(0, 0, to.w, to.h); gl.bindFramebuffer(gl.FRAMEBUFFER, to.fbo); }
    else { gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight); gl.bindFramebuffer(gl.FRAMEBUFFER, null); }
    gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0);
  };

  const config = { dt: 0.016, velocityDissipation: 0.35, dyeDissipation: 0.9, pressureDecay: 0.8, iterations: 20, curl: 28, splatRadius: 0.0035 };

  function splat(x, y, dx, dy, rgb, radius = config.splatRadius) {
    const u = use("splat");
    gl.uniform1f(u.aspect, canvas.width / canvas.height);
    gl.uniform2f(u.point, x, 1 - y);
    gl.uniform1f(u.radius, radius);
    gl.uniform1i(u.uTarget, velocity.read.bind(0));
    gl.uniform3f(u.color, dx, -dy, 0);
    blit(velocity.write); velocity.swap();
    gl.uniform1i(u.uTarget, dye.read.bind(0));
    gl.uniform3f(u.color, rgb[0], rgb[1], rgb[2]);
    blit(dye.write); dye.swap();
  }

  // A radial pressure impulse: dye and outward velocity in a ring around (x, y), x and y in 0..1 (top-left origin).
  function pulse(x, y, rgb, strength = 1, rays = 14) {
    for (let i = 0; i < rays; i += 1) {
      const a = (i / rays) * Math.PI * 2;
      splat(x + Math.cos(a) * 0.012, y + Math.sin(a) * 0.012, Math.cos(a) * 520 * strength, Math.sin(a) * 520 * strength,
        rgb.map((c) => c * strength), config.splatRadius * 1.6);
    }
  }

  function step() {
    let u = use("curl");
    gl.uniform2f(u.texel, ...velocity.read.texel);
    gl.uniform1i(u.uVelocity, velocity.read.bind(0));
    blit(curl);

    u = use("vorticity");
    gl.uniform2f(u.texel, ...velocity.read.texel);
    gl.uniform1i(u.uVelocity, velocity.read.bind(0));
    gl.uniform1i(u.uCurl, curl.bind(1));
    gl.uniform1f(u.curl, config.curl);
    gl.uniform1f(u.dt, config.dt);
    blit(velocity.write); velocity.swap();

    u = use("divergence");
    gl.uniform2f(u.texel, ...velocity.read.texel);
    gl.uniform1i(u.uVelocity, velocity.read.bind(0));
    blit(divergence);

    u = use("scale");
    gl.uniform1i(u.uTexture, pressure.read.bind(0));
    gl.uniform1f(u.value, config.pressureDecay);
    blit(pressure.write); pressure.swap();

    u = use("pressure");
    gl.uniform2f(u.texel, ...velocity.read.texel);
    gl.uniform1i(u.uDivergence, divergence.bind(0));
    for (let i = 0; i < config.iterations; i += 1) {
      gl.uniform1i(u.uPressure, pressure.read.bind(1));
      blit(pressure.write); pressure.swap();
    }

    u = use("gradient");
    gl.uniform2f(u.texel, ...velocity.read.texel);
    gl.uniform1i(u.uPressure, pressure.read.bind(0));
    gl.uniform1i(u.uVelocity, velocity.read.bind(1));
    blit(velocity.write); velocity.swap();

    u = use("advection");
    gl.uniform2f(u.texel, ...velocity.read.texel);
    gl.uniform1i(u.uVelocity, velocity.read.bind(0));
    gl.uniform1i(u.uSource, velocity.read.bind(0));
    gl.uniform1f(u.dt, config.dt);
    gl.uniform1f(u.dissipation, config.velocityDissipation);
    blit(velocity.write); velocity.swap();

    gl.uniform2f(u.texel, ...dye.read.texel);
    gl.uniform1i(u.uVelocity, velocity.read.bind(0));
    gl.uniform1i(u.uSource, dye.read.bind(1));
    gl.uniform1f(u.dissipation, config.dyeDissipation);
    blit(dye.write); dye.swap();
  }

  function draw() {
    const u = use("display");
    gl.uniform1i(u.uDye, dye.read.bind(0));
    blit(null);
  }

  function resize(width, height) {
    if (canvas.width === width && canvas.height === height && velocity) return;
    canvas.width = width;
    canvas.height = height;
    build();
  }

  resize(canvas.clientWidth || 800, canvas.clientHeight || 600);
  return { splat, pulse, step, draw, resize, config };
}
