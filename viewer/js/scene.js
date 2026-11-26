// The 3D trajectory view: an orthographic camera you can orbit, drawn on a 2D canvas.

import { phaseKind } from "./flight.js";
import { fmt, niceStep } from "./format.js";

const DEG = Math.PI / 180;
const DEFAULT_VIEW = { yaw: -34 * DEG, elev: 24 * DEG, zoom: 1 };

export class Scene {
  constructor(canvas, palette, { reducedMotion }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.palette = palette;
    this.reducedMotion = reducedMotion;
    this.view = { ...DEFAULT_VIEW };
    this.target = { ...DEFAULT_VIEW };
    this.flight = null;
    this.state = null;
    this.dirty = true;
    this._bindPointer();
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.resize();
  }

  setFlight(flight) {
    this.flight = flight;
    const b = flight.bounds;
    const spanH = Math.max(b.maxE - b.minE, b.maxN - b.minN, b.maxU * 0.25, 50);
    const step = niceStep(spanH / 6);
    // a square plot of ground centred on the flight, so the view reads as 3D
    const half = Math.ceil((spanH / 2 + step * 0.6) / step) * step;
    const midE = Math.round((b.minE + b.maxE) / 2 / step) * step;
    const midN = Math.round((b.minN + b.maxN) / 2 / step) * step;
    this.grid = { step, minE: midE - half, maxE: midE + half, minN: midN - half, maxN: midN + half };
    const g = this.grid;
    this.center = [(g.minE + g.maxE) / 2, (g.minN + g.maxN) / 2, b.maxU / 2];
    // Fit once from the default view so rotating never changes the scale.
    const corners = [];
    for (const e of [g.minE, g.maxE]) for (const n of [g.minN, g.maxN]) for (const u of [0, b.maxU]) corners.push([e, n, u]);
    let w = 0, h = 0;
    for (const c of corners) {
      const p = this._rotate(c, DEFAULT_VIEW);
      w = Math.max(w, Math.abs(p[0]));
      h = Math.max(h, Math.abs(p[1]));
    }
    this.extent = { w: w * 2, h: h * 2 };
    this.view = { ...DEFAULT_VIEW };
    this.target = { ...DEFAULT_VIEW };
    this.dirty = true;
  }

  setState(state) {
    this.state = state;
    this.dirty = true;
  }

  resetView() {
    this.target = { ...DEFAULT_VIEW };
    if (this.reducedMotion) this.view = { ...DEFAULT_VIEW };
    this.dirty = true;
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.width = r.width;
    this.height = r.height;
    this.canvas.width = Math.round(r.width * dpr);
    this.canvas.height = Math.round(r.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.dirty = true;
  }

  /** Ease the camera toward its target; true while still moving. */
  tick(dt) {
    const v = this.view, t = this.target;
    const k = this.reducedMotion ? 1 : 1 - Math.exp(-dt * 14);
    let moving = false;
    for (const key of ["yaw", "elev", "zoom"]) {
      const d = t[key] - v[key];
      if (Math.abs(d) > 1e-4) { v[key] += d * k; moving = true; this.dirty = true; } else v[key] = t[key];
    }
    return moving;
  }

  // --- Projection ----------------------------------------------------------

  _rotate([e, n, u], view = this.view) {
    const c = this.center || [0, 0, 0];
    const x = e - c[0], y = n - c[1], z = u - c[2];
    const cy = Math.cos(view.yaw), sy = Math.sin(view.yaw);
    const x1 = x * cy - y * sy;
    const y1 = x * sy + y * cy;
    const ce = Math.cos(view.elev), se = Math.sin(view.elev);
    return [x1, z * ce + y1 * se, y1 * ce - z * se];
  }

  _scale() {
    const margin = this.width < 560 ? 0.80 : 0.84;
    const s = Math.min((this.width * margin) / this.extent.w, (this.height * margin) / this.extent.h);
    return s * this.view.zoom;
  }

  _project(p) {
    const r = this._rotate(p);
    const s = this._scale();
    return [this.width / 2 + r[0] * s, this.height / 2 - r[1] * s];
  }

  /** Screen direction of a world vector (no translation), in px per metre-of-vector. */
  _projectDir(v) {
    const cy = Math.cos(this.view.yaw), sy = Math.sin(this.view.yaw);
    const x1 = v[0] * cy - v[1] * sy;
    const y1 = v[0] * sy + v[1] * cy;
    const ce = Math.cos(this.view.elev), se = Math.sin(this.view.elev);
    return [x1, -(v[2] * ce + y1 * se)];
  }

  // --- Drawing -------------------------------------------------------------

  draw() {
    if (!this.dirty || !this.flight || !this.width) return;
    this.dirty = false;
    const ctx = this.ctx, P = this.palette.colors, f = this.flight;
    ctx.clearRect(0, 0, this.width, this.height);
    const now = this.state ? this.state.t : 0;
    const i = this.state ? f.index(now) : 0;

    this._drawGrid(ctx, P);

    // ground track (shadow) of the whole flight
    ctx.lineWidth = 1;
    ctx.strokeStyle = P.shadow;
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    for (let k = 0; k < f.n; k++) {
      const [x, y] = this._project([f.east[k], f.north[k], 0]);
      if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    // the path still to fly, faint; then the path flown, full strength
    this._drawPath(ctx, P, 0, f.n - 1, 0.28, 1.25);
    if (this.state) this._drawPath(ctx, P, 0, i, 1, 2, this.state);

    this._drawMarkers(ctx, P);
    if (this.state) this._drawRocket(ctx, P, this.state);
    this._drawGizmo(ctx, P);
  }

  _drawGrid(ctx, P) {
    const g = this.grid;
    ctx.lineWidth = 1;
    ctx.strokeStyle = P.grid;
    ctx.beginPath();
    for (let e = g.minE; e <= g.maxE + 1e-6; e += g.step) {
      const a = this._project([e, g.minN, 0]), b = this._project([e, g.maxN, 0]);
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
    }
    for (let n = g.minN; n <= g.maxN + 1e-6; n += g.step) {
      const a = this._project([g.minE, n, 0]), b = this._project([g.maxE, n, 0]);
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
    }
    ctx.stroke();
    // outline the plot of ground a little stronger
    ctx.strokeStyle = P.rule;
    ctx.beginPath();
    const c = [[g.minE, g.minN], [g.maxE, g.minN], [g.maxE, g.maxN], [g.minE, g.maxN]].map(([e, n]) => this._project([e, n, 0]));
    c.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.stroke();
  }

  _drawPath(ctx, P, from, to, alpha, width, state) {
    const f = this.flight;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.globalAlpha = alpha;
    let k = from;
    for (const phase of f.phases) {
      if (k > to) break;
      const kind = phaseKind(phase.name);
      ctx.strokeStyle = P[kind];
      ctx.beginPath();
      let started = false;
      // include the boundary sample so segments join
      while (k <= to && f.t[k] <= phase.end + 1e-9) {
        const [x, y] = this._project([f.east[k], f.north[k], f.up[k]]);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
        if (f.t[k] >= phase.end - 1e-9 && k < f.n - 1) break;
        k++;
      }
      if (state && k >= to && state.t <= phase.end + 1e-9) {
        const [x, y] = this._project([state.east, state.north, state.up]);
        if (started) ctx.lineTo(x, y);
      }
      if (started) ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  _label(ctx, P, text, x, y, align = "left", color = P.ink2) {
    ctx.font = `500 11px ${this.palette.mono}`;
    // keep labels inside the canvas: flip to the other side of the mark if needed
    const w = ctx.measureText(text).width;
    if (align === "left" && x + w > this.width - 6) { x -= w + 20; }
    else if (align === "right" && x - w < 6) { x += w + 18; }
    ctx.textAlign = align;
    ctx.textBaseline = "middle";
    ctx.lineWidth = 4;
    ctx.strokeStyle = P.bg;
    ctx.lineJoin = "round";
    ctx.strokeText(text, x, y);
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
  }

  _drawMarkers(ctx, P) {
    const f = this.flight, s = f.summary;
    // pad
    const pad = this._project([0, 0, 0]);
    ctx.fillStyle = P.ink;
    ctx.beginPath();
    ctx.moveTo(pad[0], pad[1] - 5); ctx.lineTo(pad[0] + 4.5, pad[1] + 3); ctx.lineTo(pad[0] - 4.5, pad[1] + 3);
    ctx.closePath();
    ctx.fill();
    this._label(ctx, P, "Pad", pad[0] - 9, pad[1] + 1, "right");

    // apogee
    const ia = f.up.indexOf(Math.max(...f.up));
    const ap = this._project([f.east[ia], f.north[ia], f.up[ia]]);
    const apG = this._project([f.east[ia], f.north[ia], 0]);
    ctx.strokeStyle = P.shadow;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(ap[0], ap[1]); ctx.lineTo(apG[0], apG[1]); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = P.bg; ctx.strokeStyle = P.ink; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(ap[0], ap[1], 3.5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    this._label(ctx, P, `Apogee ${fmt(s.apogee, 0)} m`, ap[0] + 10, ap[1] - 1);

    // landing
    const last = f.n - 1;
    const ld = this._project([f.east[last], f.north[last], 0]);
    ctx.strokeStyle = P.ink; ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(ld[0] - 4, ld[1] - 4); ctx.lineTo(ld[0] + 4, ld[1] + 4);
    ctx.moveTo(ld[0] + 4, ld[1] - 4); ctx.lineTo(ld[0] - 4, ld[1] + 4);
    ctx.stroke();
    if (s.landing_distance >= 1) {
      this._label(ctx, P, `Landing ${fmt(s.landing_distance, 0)} m ${compass(s.landing_bearing)}`, ld[0] + 10, ld[1] + 1);
    }
  }

  _drawRocket(ctx, P, st) {
    const p = this._project([st.east, st.north, st.up]);
    const g = this._project([st.east, st.north, 0]);
    // drop line to the ground track
    ctx.strokeStyle = P.ink3;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(g[0], g[1]); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = P.ink3;
    ctx.beginPath(); ctx.arc(g[0], g[1], 2, 0, 2 * Math.PI); ctx.fill();

    const descending = st.kind === "drogue" || st.kind === "main";
    if (descending) {
      // canopy above a hanging rocket
      const r = st.kind === "main" ? 9 : 6;
      ctx.strokeStyle = P[st.kind];
      ctx.fillStyle = P[st.kind];
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(p[0], p[1] - 14, r, Math.PI, 0);
      ctx.closePath();
      ctx.globalAlpha = 0.18; ctx.fill(); ctx.globalAlpha = 1;
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(p[0] - r, p[1] - 14); ctx.lineTo(p[0], p[1]);
      ctx.moveTo(p[0] + r, p[1] - 14); ctx.lineTo(p[0], p[1]);
      ctx.stroke();
      ctx.fillStyle = P.ink;
      ctx.beginPath(); ctx.arc(p[0], p[1], 3, 0, 2 * Math.PI); ctx.fill();
      return;
    }

    const d = this._projectDir(st.axis);
    const len = 24;
    const nose = [p[0] + d[0] * len * 0.55, p[1] + d[1] * len * 0.55];
    const tail = [p[0] - d[0] * len * 0.45, p[1] - d[1] * len * 0.45];
    // plume: full under thrust, fading over half a second after burnout
    const burn = this.flight.rocket.motor ? this.flight.rocket.motor.burn_time : 0;
    const plume = st.t <= burn ? 1 : Math.max(0, 1 - (st.t - burn) / 0.5);
    if (plume > 0 && st.t > 0) {
      const end = [tail[0] - d[0] * 16, tail[1] - d[1] * 16];
      const grad = ctx.createLinearGradient(tail[0], tail[1], end[0], end[1]);
      grad.addColorStop(0, P.boost);
      grad.addColorStop(1, "rgba(0,0,0,0)");
      ctx.globalAlpha = plume;
      ctx.strokeStyle = grad;
      ctx.lineWidth = 4;
      ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(tail[0], tail[1]); ctx.lineTo(end[0], end[1]); ctx.stroke();
      ctx.globalAlpha = 1;
    }
    ctx.strokeStyle = P.bg;
    ctx.lineWidth = 6;
    ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(tail[0], tail[1]); ctx.lineTo(nose[0], nose[1]); ctx.stroke();
    ctx.strokeStyle = P.ink;
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(tail[0], tail[1]); ctx.lineTo(nose[0], nose[1]); ctx.stroke();
    ctx.fillStyle = P.ink;
    ctx.beginPath(); ctx.arc(nose[0], nose[1], 2.2, 0, 2 * Math.PI); ctx.fill();
  }

  _drawGizmo(ctx, P) {
    const cx = 40, cy = this.height - 40, L = 22;
    const axes = [
      ["E", [1, 0, 0]],
      ["N", [0, 1, 0]],
      ["Up", [0, 0, 1]],
    ];
    ctx.lineWidth = 1.25;
    for (const [name, v] of axes) {
      const d = this._projectDir(v);
      const x = cx + d[0] * L, y = cy + d[1] * L;
      ctx.strokeStyle = P.ink2;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
      this._label(ctx, P, name, cx + d[0] * (L + 9), cy + d[1] * (L + 9), "center", P.ink2);
    }
    const wind = this.flight.site.wind;
    if (wind.speed > 0) {
      // arrow pointing where the wind blows to
      const to = (wind.direction + 180) * DEG;
      const d = this._projectDir([Math.sin(to), Math.cos(to), 0]);
      const m = Math.hypot(d[0], d[1]) || 1;
      const ux = d[0] / m, uy = d[1] / m;
      const x0 = cx + 64, y0 = cy;
      const a = [x0 - ux * 14, y0 - uy * 14], b = [x0 + ux * 14, y0 + uy * 14];
      ctx.strokeStyle = P.ink2;
      ctx.lineWidth = 1.25;
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
      ctx.moveTo(b[0], b[1]); ctx.lineTo(b[0] - ux * 6 - uy * 4, b[1] - uy * 6 + ux * 4);
      ctx.moveTo(b[0], b[1]); ctx.lineTo(b[0] - ux * 6 + uy * 4, b[1] - uy * 6 - ux * 4);
      ctx.stroke();
      this._label(ctx, P, `Wind ${fmt(wind.speed, wind.speed < 10 ? 1 : 0)} m/s`, x0 + 22, y0, "left", P.ink2);
    }
  }

  // --- Interaction ---------------------------------------------------------

  _bindPointer() {
    const c = this.canvas;
    const pointers = new Map();
    let pinch = null;
    c.addEventListener("pointerdown", (e) => {
      c.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, [e.clientX, e.clientY]);
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), zoom: this.target.zoom };
      }
      c.classList.add("dragging");
    });
    c.addEventListener("pointermove", (e) => {
      if (!pointers.has(e.pointerId)) return;
      const prev = pointers.get(e.pointerId);
      pointers.set(e.pointerId, [e.clientX, e.clientY]);
      if (pointers.size === 2 && pinch) {
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        this.target.zoom = clamp(pinch.zoom * (d / pinch.d), 0.5, 8);
      } else if (pointers.size === 1) {
        this.target.yaw += (e.clientX - prev[0]) * 0.006;
        this.target.elev = clamp(this.target.elev + (e.clientY - prev[1]) * 0.006, 2 * DEG, 88 * DEG);
        if (this.reducedMotion) Object.assign(this.view, this.target);
      }
      this.dirty = true;
    });
    const end = (e) => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
      if (!pointers.size) c.classList.remove("dragging");
    };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("dblclick", () => this.resetView());
    c.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        this.target.zoom = clamp(this.target.zoom * Math.exp(-e.deltaY * 0.0015), 0.5, 8);
        if (this.reducedMotion) this.view.zoom = this.target.zoom;
        this.dirty = true;
      },
      { passive: false }
    );
    c.addEventListener("keydown", (e) => {
      const step = 5 * DEG;
      const keys = {
        ArrowLeft: () => (this.target.yaw -= step),
        ArrowRight: () => (this.target.yaw += step),
        ArrowUp: () => (this.target.elev = clamp(this.target.elev + step, 2 * DEG, 88 * DEG)),
        ArrowDown: () => (this.target.elev = clamp(this.target.elev - step, 2 * DEG, 88 * DEG)),
        "+": () => (this.target.zoom = clamp(this.target.zoom * 1.2, 0.5, 8)),
        "=": () => (this.target.zoom = clamp(this.target.zoom * 1.2, 0.5, 8)),
        "-": () => (this.target.zoom = clamp(this.target.zoom / 1.2, 0.5, 8)),
      };
      if (keys[e.key]) {
        keys[e.key]();
        e.preventDefault();
        e.stopPropagation();
        if (this.reducedMotion) Object.assign(this.view, this.target);
        this.dirty = true;
      }
    });
  }
}

const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
export function compass(bearing) {
  return COMPASS[Math.round(bearing / 45) % 8];
}

function clamp(x, lo, hi) {
  return Math.min(hi, Math.max(lo, x));
}
