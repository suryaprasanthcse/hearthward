// Wardline's Canvas 2D light layer: the same interface and the same frame.glow as ward.gl.js, with stacked strokes
// and radial gradients instead of a shader (and no dither).
import { wave } from "./model.js";

const COLORS = {
  bg: [8, 11, 16], ink: [242, 246, 250], scam: [242, 163, 58], hot: [255, 93, 79], safe: [46, 196, 182], ver: [142, 162, 255],
};
const rgba = (c, a) => `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${a})`;
const mix = (a, b, k) => a.map((v, i) => v + (b[i] - v) * k);
const STAGE_W = 1920;
const STAGE_H = 1080;

export function createGlowRenderer(canvas) {
  const ctx = canvas.getContext("2d", { alpha: false });
  if (!ctx) return null;

  const stroke = (pts, color, lit) => {
    if (pts.length < 2) return;
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (const [w, a] of [[24, 0.06], [12, 0.12], [4, 0.5]]) {
      ctx.lineWidth = w;
      ctx.strokeStyle = rgba(color, a * lit);
      ctx.stroke();
    }
  };
  const radial = (x, y, r, color, a) => {
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rgba(color, a));
    g.addColorStop(1, rgba(color, 0));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  };

  return {
    kind: "canvas2d",
    draw(frame) {
      const g = frame.glow;
      const s = canvas.width / STAGE_W;
      ctx.setTransform(s, 0, 0, s, 0, 0);
      ctx.globalAlpha = 1;
      ctx.fillStyle = rgba(COLORS.bg, 1);
      ctx.fillRect(0, 0, STAGE_W, STAGE_H);
      ctx.globalAlpha = g.fade;
      const hot = mix(COLORS.scam, COLORS.hot, g.aLit);
      const t = g.t;
      const lineA = [];
      const lineB = [];
      for (let i = 0; i < 96; i += 1) {
        const x = g.field[0] + ((g.field[1] - g.field[0]) * i) / 95;
        lineA.push([x, g.yA + wave(g.waveA, x, t)]);
        lineB.push([x, g.yB + wave(g.waveB, x, t)]);
      }
      stroke(lineA, hot, 0.4 + 0.6 * g.aLit);
      stroke(lineB, COLORS.safe, 0.3 + 0.7 * g.bLit);
      if (g.trail.length > 1) stroke(g.trail, hot, 0.9);
      radial(g.point[0], g.point[1], 18, COLORS.ink, 0.9);
      ctx.fillStyle = rgba(COLORS.ink, 1);
      ctx.beginPath();
      ctx.arc(g.point[0], g.point[1], 6.5, 0, Math.PI * 2);
      ctx.fill();
      if (g.cross[2] >= 0) {
        const k = (t - g.cross[2]) / 0.7;
        if (k >= 0 && k <= 1) {
          ctx.beginPath();
          ctx.arc(g.cross[0], g.cross[1], 648 * (1 - (1 - k) ** 3), 0, Math.PI * 2);
          ctx.lineWidth = 6;
          ctx.strokeStyle = rgba(COLORS.hot, 0.45 * (1 - k));
          ctx.stroke();
        }
      }
      for (const [x, y, kind, alpha] of g.part) {
        const color = kind < 0.5 ? COLORS.ink : kind < 1.5 ? COLORS.ver : COLORS.scam;
        radial(x, y, 12, color, 0.8 * alpha);
        if (kind > 0.5 && kind < 1.5) {
          ctx.beginPath();
          ctx.arc(x, y, 16, 0, Math.PI * 2);
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = rgba(COLORS.ver, alpha);
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
      const v = ctx.createRadialGradient(STAGE_W / 2, STAGE_H / 2, 420, STAGE_W / 2, STAGE_H / 2, 1150);
      v.addColorStop(0, "rgba(0,0,0,0)");
      v.addColorStop(1, "rgba(0,0,0,0.3)");
      ctx.fillStyle = v;
      ctx.fillRect(0, 0, STAGE_W, STAGE_H);
    },
    resize(cssW, cssH, scale) {
      canvas.width = Math.max(1, Math.round(cssW * scale));
      canvas.height = Math.max(1, Math.round(cssH * scale));
    },
    dispose() {},
  };
}
