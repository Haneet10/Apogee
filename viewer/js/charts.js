// Altitude, speed and acceleration against time, sharing one time axis.

import { shortEventName } from "./flight.js";
import { fmt, ticks } from "./format.js";

const PAD = { left: 52, right: 14, top: 10, bottom: 24 };

const SERIES = [
  { key: "up", title: "Altitude", unit: "m", digits: 0 },
  { key: "speed", title: "Speed", unit: "m/s", digits: 0 },
  { key: "acceleration_g", title: "Acceleration", unit: "g", digits: 1 },
];

export class Charts {
  constructor(root, palette, { reducedMotion, onSeek, onScrub }) {
    this.root = root;
    this.palette = palette;
    this.reducedMotion = reducedMotion;
    this.onSeek = onSeek;
    this.onScrub = onScrub;
    this.mode = "auto";
    this.range = null; // shown [t0, t1]
    this.goal = null;
    this.time = 0;
    this.rows = SERIES.map((spec) => this._row(spec));
    new ResizeObserver(() => this.resize()).observe(root);
  }

  _row(spec) {
    const el = document.createElement("div");
    el.className = "chart";
    el.innerHTML = `
      <div class="chart-head"><span class="label">${spec.title}</span><span class="unit">${spec.unit}</span>
      <output class="chart-value" aria-hidden="true"></output></div>
      <div class="chart-plot"><canvas class="base"></canvas><canvas class="over"></canvas></div>`;
    this.root.append(el);
    const row = {
      spec,
      el,
      value: el.querySelector("output"),
      plot: el.querySelector(".chart-plot"),
      base: el.querySelector("canvas.base"),
      over: el.querySelector("canvas.over"),
    };
    this._bindSeek(row);
    return row;
  }

  setFlight(flight) {
    this.flight = flight;
    for (const row of this.rows) {
      const data = flight[row.spec.key];
      const max = Math.max(...data);
      row.max = ticks(0, max, 3);
      row.yMax = Math.max(row.max.values.at(-1), max) * 1.06;
    }
    this.goal = this._goalRange(0);
    this.range = [...this.goal];
    this.resize();
  }

  setMode(mode) {
    this.mode = mode;
    this.goal = this._goalRange(this.time);
    if (this.reducedMotion) this.range = [...this.goal];
  }

  _goalRange(t) {
    const f = this.flight;
    const ascent = [0, Math.min(f.duration, f.apogeeTime * 1.18)];
    const whole = [0, f.duration];
    if (this.mode === "ascent") return ascent;
    if (this.mode === "whole") return whole;
    return t > f.apogeeTime * 1.12 ? whole : ascent;
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    for (const row of this.rows) {
      const r = row.plot.getBoundingClientRect();
      row.w = r.width;
      row.h = r.height;
      for (const c of [row.base, row.over]) {
        c.width = Math.round(r.width * dpr);
        c.height = Math.round(r.height * dpr);
        c.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
      }
    }
    this.baseDirty = true;
    this.overDirty = true;
  }

  /** Advance the axis animation; true while it is still moving. */
  tick(dt, time) {
    if (!this.flight) return false;
    if (time !== this.time) this.overDirty = true;
    this.time = time;
    this.goal = this._goalRange(time);
    const k = this.reducedMotion ? 1 : 1 - Math.exp(-dt * 7);
    let moving = false;
    for (let j = 0; j < 2; j++) {
      const d = this.goal[j] - this.range[j];
      if (Math.abs(d) > 1e-3 * (this.goal[1] - this.goal[0])) {
        this.range[j] += d * k;
        moving = true;
      } else this.range[j] = this.goal[j];
    }
    if (moving) this.baseDirty = this.overDirty = true;
    return moving;
  }

  x(row, t) {
    const [t0, t1] = this.range;
    return PAD.left + ((t - t0) / (t1 - t0)) * (row.w - PAD.left - PAD.right);
  }

  y(row, v) {
    return PAD.top + (1 - v / row.yMax) * (row.h - PAD.top - PAD.bottom);
  }

  draw() {
    if (!this.flight) return;
    if (this.baseDirty) this.rows.forEach((row, k) => this._drawBase(row, k === this.rows.length - 1));
    if (this.overDirty) this.rows.forEach((row) => this._drawOver(row));
    this.baseDirty = this.overDirty = false;
  }

  _visible() {
    const f = this.flight, [t0, t1] = this.range;
    const a = Math.max(0, f.index(t0) - 1), b = Math.min(f.n - 1, f.index(t1) + 2);
    return [a, b];
  }

  _drawBase(row, last) {
    const ctx = row.base.getContext("2d"), P = this.palette.colors, f = this.flight;
    const { w, h } = row;
    ctx.clearRect(0, 0, w, h);
    const bottom = h - PAD.bottom, right = w - PAD.right;
    ctx.font = `400 10.5px ${this.palette.mono}`;

    // horizontal gridlines with value labels
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const v of row.max.values) {
      const y = Math.round(this.y(row, v)) + 0.5;
      ctx.strokeStyle = v === 0 ? P.rule : P.grid;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.fillStyle = P.ink3;
      ctx.fillText(fmt(v, row.max.step < 1 ? 1 : 0), PAD.left - 8, y);
    }

    // time ticks
    const [t0, t1] = this.range;
    const tt = ticks(t0, t1, w < 420 ? 4 : 7);
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const t of tt.values) {
      const x = Math.round(this.x(row, t)) + 0.5;
      ctx.strokeStyle = P.grid;
      ctx.beginPath(); ctx.moveTo(x, PAD.top); ctx.lineTo(x, bottom); ctx.stroke();
      ctx.fillStyle = P.ink3;
      ctx.fillText(last ? `${fmt(t, tt.step < 1 ? 1 : 0)} s` : fmt(t, tt.step < 1 ? 1 : 0), x, bottom + 6);
    }

    // events as hairlines, labelled on the top chart
    ctx.save();
    ctx.beginPath(); ctx.rect(PAD.left, 0, right - PAD.left, h); ctx.clip();
    const groups = [];
    for (const e of f.events) {
      if (e.name === "Landing" && t1 < f.duration * 0.98) continue;
      const x = this.x(row, e.time);
      const g = groups.at(-1);
      if (g && Math.abs(g.x - x) < 14) g.names.push(shortEventName(e.name));
      else groups.push({ x, names: [shortEventName(e.name)] });
    }
    ctx.setLineDash([2, 3]);
    ctx.strokeStyle = P.ink3;
    for (const g of groups) {
      const x = Math.round(g.x) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, PAD.top); ctx.lineTo(x, bottom); ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.restore();
    if (row === this.rows[0]) {
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.font = `500 10.5px ${this.palette.mono}`;
      let lastEnd = -Infinity;
      for (const g of groups) {
        const text = g.names.join(" · ");
        const tw = ctx.measureText(text).width;
        let x = g.x + 5;
        if (x + tw > right) x = g.x - 5 - tw;
        if (x < lastEnd + 6 || x < PAD.left || g.x > right) continue;
        ctx.fillStyle = P.bg;
        ctx.fillRect(x - 2, PAD.top + 1, tw + 4, 13);
        ctx.fillStyle = P.ink2;
        ctx.fillText(text, x, PAD.top + 2);
        lastEnd = x + tw;
      }
    }

    // the whole series, faint
    const data = f[row.spec.key];
    const [a, b] = this._visible();
    ctx.save();
    ctx.beginPath(); ctx.rect(PAD.left, 0, right - PAD.left, h); ctx.clip();
    ctx.strokeStyle = P.ink3;
    ctx.globalAlpha = 0.55;
    ctx.lineWidth = 1.25;
    ctx.lineJoin = "round";
    ctx.beginPath();
    for (let i = a; i <= b; i++) {
      const x = this.x(row, f.t[i]), y = this.y(row, data[i]);
      if (i === a) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();
  }

  _drawOver(row) {
    const ctx = row.over.getContext("2d"), P = this.palette.colors, f = this.flight;
    const { w, h } = row;
    ctx.clearRect(0, 0, w, h);
    const data = f[row.spec.key];
    const right = w - PAD.right, bottom = h - PAD.bottom;
    const [a] = this._visible();
    const now = this.time;
    const i = f.index(now);
    const frac = Math.min(1, Math.max(0, (now - f.t[i]) / (f.t[i + 1] - f.t[i] || 1)));
    const v = data[i] + (data[i + 1] - data[i]) * frac;

    ctx.save();
    ctx.beginPath(); ctx.rect(PAD.left, 0, right - PAD.left, h); ctx.clip();
    ctx.strokeStyle = P.ink;
    ctx.lineWidth = 1.75;
    ctx.lineJoin = "round";
    ctx.beginPath();
    let started = false;
    for (let k = a; k <= i; k++) {
      const x = this.x(row, f.t[k]), y = this.y(row, data[k]);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
    const cx = this.x(row, now), cy = this.y(row, v);
    if (started) ctx.lineTo(cx, cy);
    ctx.stroke();
    ctx.restore();

    if (cx >= PAD.left - 1 && cx <= right + 1) {
      ctx.strokeStyle = P.boost;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(cx) + 0.5, PAD.top); ctx.lineTo(Math.round(cx) + 0.5, bottom); ctx.stroke();
      ctx.fillStyle = P.bg;
      ctx.strokeStyle = P.boost;
      ctx.lineWidth = 1.75;
      ctx.beginPath(); ctx.arc(cx, cy, 3.5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    }
    row.current = v;
  }

  /** Refresh the numbers in the chart headers (called a few times a second). */
  updateValues() {
    for (const row of this.rows) {
      if (row.current === undefined) continue;
      row.value.textContent = fmt(row.current, row.spec.digits);
    }
  }

  _bindSeek(row) {
    const toTime = (e) => {
      const r = row.plot.getBoundingClientRect();
      const [t0, t1] = this.range;
      const t = t0 + ((e.clientX - r.left - PAD.left) / (r.width - PAD.left - PAD.right)) * (t1 - t0);
      return Math.min(this.flight.duration, Math.max(0, t));
    };
    let active = false;
    row.plot.addEventListener("pointerdown", (e) => {
      if (!this.flight) return;
      active = true;
      row.plot.setPointerCapture(e.pointerId);
      this.onScrub(true);
      this.onSeek(toTime(e));
    });
    row.plot.addEventListener("pointermove", (e) => active && this.onSeek(toTime(e)));
    const end = () => {
      if (!active) return;
      active = false;
      this.onScrub(false);
    };
    row.plot.addEventListener("pointerup", end);
    row.plot.addEventListener("pointercancel", end);
  }
}
