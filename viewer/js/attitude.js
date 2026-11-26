// Side view of the rocket, drawn from its real outline, tilted to the current pitch.

import { fmt } from "./format.js";

const DEG = Math.PI / 180;

export class Attitude {
  constructor(canvas, palette, { reducedMotion }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.palette = palette;
    this.reducedMotion = reducedMotion;
    this.angle = 0; // shown tilt from vertical [rad], eased
    this.goal = 0;
    this.hang = 0; // 0 = flying, 1 = under a canopy (eased)
    this.hangGoal = 0;
    this.dirty = true;
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.resize();
  }

  setFlight(flight) {
    this.flight = flight;
    this.outline = flight.rocket.outline;
    this.dirty = true;
  }

  setState(st) {
    this.state = st;
    this.goal = st.pitch * DEG;
    this.hangGoal = st.kind === "drogue" || st.kind === "main" ? 1 : 0;
    this.dirty = true;
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = r.width;
    this.h = r.height;
    this.canvas.width = Math.round(r.width * dpr);
    this.canvas.height = Math.round(r.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.dirty = true;
  }

  tick(dt) {
    const k = this.reducedMotion ? 1 : 1 - Math.exp(-dt * 10);
    let moving = false;
    for (const [key, goal] of [["angle", this.goal], ["hang", this.hangGoal]]) {
      const d = goal - this[key];
      if (Math.abs(d) > 1e-4) { this[key] += d * k; moving = true; } else this[key] = goal;
    }
    if (moving) this.dirty = true;
    return moving;
  }

  draw() {
    if (!this.dirty || !this.outline || !this.w) return;
    this.dirty = false;
    const ctx = this.ctx, P = this.palette.colors, o = this.outline;
    const { w, h } = this;
    ctx.clearRect(0, 0, w, h);

    const cx = w / 2, cy = h * 0.52;
    const span = o.fins.length ? Math.max(...o.fins.map((f) => f.span)) : 0;
    const half = o.diameter / 2 + span;
    // fit the rocket whether upright or lying down
    const scale = Math.min((h * 0.78) / o.length, (w * 0.8) / o.length, (w * 0.42) / half);
    const cg = o.length / 2; // pivot on the middle so the whole airframe stays in frame
    // lean the way the nose points: west (left in the default 3D view) or east
    const lean = this.state && this.state.axis[0] < 0 ? -1 : 1;
    const tilt = lean * this.angle * (1 - this.hang) + (Math.PI / 2) * this.hang;

    // vertical reference through the CG
    ctx.strokeStyle = P.grid;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.moveTo(cx, 8); ctx.lineTo(cx, h - 8); ctx.stroke();
    ctx.setLineDash([]);

    // canopy above a hanging rocket
    if (this.hang > 0.01) {
      ctx.globalAlpha = this.hang;
      const kind = this.state ? this.state.kind : "main";
      const color = P[kind] || P.main;
      const r = Math.min(w * 0.22, 54);
      const top = cy - h * 0.32;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(cx, top + r * 0.55, r, Math.PI * 1.08, Math.PI * 1.92); ctx.stroke();
      ctx.globalAlpha = this.hang * 0.12;
      ctx.fill();
      ctx.globalAlpha = this.hang;
      ctx.lineWidth = 1;
      const left = [cx + r * Math.cos(Math.PI * 1.08), top + r * 0.55 + r * Math.sin(Math.PI * 1.08)];
      const right = [cx + r * Math.cos(Math.PI * 1.92), top + r * 0.55 + r * Math.sin(Math.PI * 1.92)];
      ctx.beginPath();
      ctx.moveTo(left[0], left[1]); ctx.lineTo(cx, cy - o.diameter * scale);
      ctx.moveTo(right[0], right[1]); ctx.lineTo(cx, cy - o.diameter * scale);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(tilt);
    ctx.scale(scale, scale);
    // rocket axis runs up the screen: nose tip at y = -cg, tail at length - cg
    const Y = (x) => x - cg;
    const R = o.diameter / 2;
    ctx.lineWidth = 1.25 / scale;
    ctx.lineJoin = "round";
    ctx.strokeStyle = P.ink;
    ctx.fillStyle = P.bg;

    // fins (one each side, as drawn on a side elevation)
    for (const f of o.fins) {
      for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.moveTo(side * R, Y(f.start));
        ctx.lineTo(side * (R + f.span), Y(f.start + f.sweep));
        ctx.lineTo(side * (R + f.span), Y(f.start + f.sweep + f.tip_chord));
        ctx.lineTo(side * R, Y(f.start + f.root_chord));
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
    }

    // nose and body as one outline
    ctx.beginPath();
    const profile = o.nose ? o.nose.profile : [[0, R]];
    profile.forEach(([x, r], k) => (k ? ctx.lineTo(r, Y(x)) : ctx.moveTo(r, Y(x))));
    ctx.lineTo(R, Y(o.length));
    ctx.lineTo(-R, Y(o.length));
    for (let k = profile.length - 1; k >= 0; k--) ctx.lineTo(-profile[k][1], Y(profile[k][0]));
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // joints between airframe sections
    ctx.strokeStyle = P.ink3;
    ctx.beginPath();
    for (const b of o.bodies) {
      ctx.moveTo(-R, Y(b.start)); ctx.lineTo(R, Y(b.start));
    }
    ctx.stroke();

    // plume while the motor burns
    const st = this.state;
    const burn = this.flight.rocket.motor ? this.flight.rocket.motor.burn_time : 0;
    if (st && st.t > 0 && st.t <= burn + 0.5 && this.hang < 0.5) {
      const fade = st.t <= burn ? 1 : 1 - (st.t - burn) / 0.5;
      const L = o.length * 0.22;
      const grad = ctx.createLinearGradient(0, Y(o.length), 0, Y(o.length) + L);
      grad.addColorStop(0, P.boost);
      grad.addColorStop(1, "rgba(0,0,0,0)");
      ctx.globalAlpha = fade;
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(-R * 0.6, Y(o.length));
      ctx.quadraticCurveTo(0, Y(o.length) + L * 1.2, R * 0.6, Y(o.length));
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    ctx.restore();

    // CG and CP marks stay upright, placed along the tilted axis
    const along = (x) => [cx - Math.sin(tilt) * (x - cg) * scale, cy + Math.cos(tilt) * (x - cg) * scale];
    const g = along(o.cg_liftoff), p = along(o.cp);
    this._cgMark(ctx, P, g[0], g[1]);
    this._cpMark(ctx, P, p[0], p[1]);
    ctx.font = `500 10.5px ${this.palette.mono}`;
    ctx.textBaseline = "middle";
    ctx.fillStyle = P.ink2;
    const labelSide = tilt > 0.6 ? -1 : 1;
    ctx.textAlign = labelSide > 0 ? "left" : "right";
    const off = (R * scale + 10) * labelSide;
    if (this.hang < 0.5) {
      ctx.fillText("CG", g[0] + off * Math.cos(tilt), g[1] + off * Math.sin(tilt));
      ctx.fillText("CP", p[0] + off * Math.cos(tilt), p[1] + off * Math.sin(tilt));
    }

    // tilt arc and readout
    if (this.hang < 0.5 && this.angle > 0.5 * DEG) {
      const r = Math.min(46, h * 0.18);
      ctx.strokeStyle = P.boost;
      ctx.lineWidth = 1.25;
      const a0 = -Math.PI / 2, a1 = a0 + lean * this.angle;
      ctx.beginPath(); ctx.arc(cx, cy, r, Math.min(a0, a1), Math.max(a0, a1)); ctx.stroke();
    }
  }

  _cgMark(ctx, P, x, y) {
    const r = 5;
    ctx.fillStyle = P.bg;
    ctx.strokeStyle = P.ink;
    ctx.lineWidth = 1.25;
    ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    ctx.fillStyle = P.ink;
    for (const a of [0, Math.PI]) {
      ctx.beginPath(); ctx.moveTo(x, y); ctx.arc(x, y, r, a - Math.PI / 2, a); ctx.closePath(); ctx.fill();
    }
  }

  _cpMark(ctx, P, x, y) {
    ctx.fillStyle = P.bg;
    ctx.strokeStyle = P.boost;
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(x, y, 5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    ctx.fillStyle = P.boost;
    ctx.beginPath(); ctx.arc(x, y, 1.6, 0, 2 * Math.PI); ctx.fill();
  }

  caption(st) {
    if (st.kind === "drogue" || st.kind === "main") return "Hanging under the canopy. Attitude is not modelled on the way down.";
    const [e, n] = st.axis;
    const az = (Math.atan2(e, n) / DEG + 360) % 360;
    const toward = st.pitch < 0.5 ? "" : `, nose toward ${["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(az / 45) % 8]}`;
    return `${fmt(st.pitch, 1)}° from vertical${toward}`;
  }
}
