// Reading and sampling an Apogee playback file (written by `apogee simulate --json`).

export const FORMAT = "apogee-flight";
export const VERSION = 1;

export class FlightFileError extends Error {}

const HOW_TO =
  "Make one with:  apogee simulate my_rocket.yaml --json flight.json";

const SERIES = ["t", "east", "north", "up", "speed", "mach", "acceleration_g", "pitch"];

/** Short labels for the plots and the timeline. */
export function shortEventName(name) {
  if (name === "Rail exit") return "Rail exit";
  if (name === "Airbrakes open") return "Brakes out";
  if (name === "Airbrakes stowed") return "Brakes in";
  const chute = name.match(/^(.*) parachute opens$/);
  if (chute) return chute[1];
  return name.replace(/ opens$/, "");
}

/** Colour family for a phase name from the file. */
export function phaseKind(name) {
  if (name === "rail" || name === "boost") return "boost";
  if (name === "coast") return "coast";
  if (/drogue/i.test(name)) return "drogue";
  if (name.startsWith("under")) return "main";
  return "coast";
}

/** Human label for a phase name from the file. */
export function phaseLabel(name) {
  if (name === "rail") return "On the rail";
  if (name === "boost") return "Powered ascent";
  if (name === "coast") return "Coasting to apogee";
  const chute = name.match(/^under (.*)$/);
  if (chute) return `Under the ${chute[1].charAt(0).toLowerCase()}${chute[1].slice(1)}`;
  return name;
}

export class Flight {
  constructor(doc) {
    this.doc = doc;
    this.rocket = doc.rocket;
    this.site = doc.site;
    this.summary = doc.summary;
    this.events = doc.events;
    this.phases = doc.phases;
    this.warnings = doc.warnings || [];
    const s = doc.series;
    for (const key of SERIES) this[key] = Float64Array.from(s[key]);
    this.attitude = s.attitude;
    this.n = this.t.length;
    this.duration = this.t[this.n - 1];
    this.apogeeTime = doc.summary.time_to_apogee;

    let minE = 0, maxE = 0, minN = 0, maxN = 0, maxU = 0;
    for (let i = 0; i < this.n; i++) {
      minE = Math.min(minE, this.east[i]); maxE = Math.max(maxE, this.east[i]);
      minN = Math.min(minN, this.north[i]); maxN = Math.max(maxN, this.north[i]);
      maxU = Math.max(maxU, this.up[i]);
    }
    this.bounds = { minE, maxE, minN, maxN, maxU };
    this.maxSpeed = Math.max(...this.speed);
    this.maxAccel = Math.max(...this.acceleration_g);

    // Airbrake flights carry the panel extension and the flight computer's log.
    this.airbrakes = doc.airbrakes && Array.isArray(s.airbrakes) && s.airbrakes.length === this.n
      ? doc.airbrakes
      : null;
    if (this.airbrakes) {
      this.brake = Float64Array.from(s.airbrakes);
      this.brake_pct = this.brake.map((v) => 100 * v);
      const log = this.airbrakes.log;
      this.fc = {
        t: Float64Array.from(log.t),
        predicted: log.predicted_apogee.map((v) => (v == null ? NaN : v)),
        command: Float64Array.from(log.command),
        drag: Float64Array.from(log.drag_factor),
      };
    }
  }

  /** The flight computer's latest log row at or before ``time`` (or null). */
  computerAt(time) {
    const fc = this.fc;
    if (!fc || !fc.t.length || time < fc.t[0]) return null;
    let lo = 0, hi = fc.t.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (fc.t[mid] <= time) lo = mid; else hi = mid;
    }
    if (fc.t[hi] <= time) lo = hi;
    return { predicted: fc.predicted[lo], command: fc.command[lo], drag: fc.drag[lo] };
  }

  /** Index i with t[i] <= time < t[i+1], clamped to the series. */
  index(time) {
    const t = this.t;
    if (time <= t[0]) return 0;
    if (time >= t[this.n - 1]) return this.n - 2;
    let lo = 0, hi = this.n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (t[mid] <= time) lo = mid; else hi = mid;
    }
    return lo;
  }

  phaseAt(time) {
    for (const p of this.phases) if (time < p.end) return p.name;
    return this.phases[this.phases.length - 1].name;
  }

  /** The flight state at any time, interpolated between samples. */
  sample(time) {
    const i = this.index(time);
    const t0 = this.t[i], t1 = this.t[i + 1];
    const f = t1 > t0 ? Math.min(1, Math.max(0, (time - t0) / (t1 - t0))) : 0;
    const lerp = (a) => a[i] + (a[i + 1] - a[i]) * f;
    const qa = this.attitude[i], qb = this.attitude[i + 1];
    const dot = qa[0] * qb[0] + qa[1] * qb[1] + qa[2] * qb[2] + qa[3] * qb[3];
    const sign = dot < 0 ? -1 : 1;
    let q = qa.map((v, k) => v + (sign * qb[k] - v) * f);
    const norm = Math.hypot(...q) || 1;
    q = q.map((v) => v / norm);
    const [w, x, y, z] = q;
    // body x axis (the nose) in east-north-up
    const axis = [1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)];
    const phase = this.phaseAt(time);
    return {
      t: time,
      east: lerp(this.east),
      north: lerp(this.north),
      up: lerp(this.up),
      speed: lerp(this.speed),
      mach: lerp(this.mach),
      accel: lerp(this.acceleration_g),
      pitch: lerp(this.pitch),
      axis,
      phase,
      kind: phaseKind(phase),
      brake: this.brake ? lerp(this.brake) : 0,
    };
  }
}

/** Check a parsed JSON document and wrap it, with errors that say what to fix. */
export function parseFlight(doc) {
  if (!doc || typeof doc !== "object" || doc.format !== FORMAT) {
    throw new FlightFileError(`That file is not an Apogee flight. ${HOW_TO}`);
  }
  if (doc.version > VERSION) {
    throw new FlightFileError(
      `This flight was written by a newer Apogee (file version ${doc.version}). ` +
        "Reload the page to get the latest viewer."
    );
  }
  const s = doc.series || {};
  const n = (s.t || []).length;
  if (n < 2) throw new FlightFileError(`The flight has no trajectory samples. ${HOW_TO}`);
  for (const key of [...SERIES, "attitude"]) {
    if (!Array.isArray(s[key]) || s[key].length !== n) {
      throw new FlightFileError(`The flight's "${key}" column is missing or the wrong length. ${HOW_TO}`);
    }
  }
  if (!Array.isArray(doc.events) || !Array.isArray(doc.phases) || !doc.summary || !doc.rocket) {
    throw new FlightFileError(`The flight file is incomplete. ${HOW_TO}`);
  }
  return new Flight(doc);
}
