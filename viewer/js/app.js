// Apogee flight viewer: loads a playback file and keeps every view on the same clock.

import { Attitude } from "./attitude.js";
import { Charts } from "./charts.js";
import { FlightFileError, parseFlight, phaseKind, phaseLabel, shortEventName } from "./flight.js";
import { fmt, missionTime } from "./format.js";
import { compass, Scene } from "./scene.js";

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (sel) => document.querySelector(sel);
const SPEEDS = [1, 4, 16, 64];
const TEXT_INTERVAL = 0.1; // seconds between text refreshes (no per-frame churn)

// --- Theme ------------------------------------------------------------------

const palette = {
  mono: "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
  colors: {},
  refresh() {
    const css = getComputedStyle(document.documentElement);
    const get = (name) => css.getPropertyValue(name).trim();
    this.colors = {
      bg: get("--bg"),
      ink: get("--ink"),
      ink2: get("--ink-2"),
      ink3: get("--ink-3"),
      rule: get("--rule"),
      grid: get("--grid"),
      shadow: get("--shadow"),
      boost: get("--boost"),
      coast: get("--coast"),
      drogue: get("--drogue"),
      main: get("--main"),
    };
  },
};

function storedTheme() {
  try { return localStorage.getItem("apogee-theme"); } catch { return null; }
}

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  palette.refresh();
  for (const view of [scene, attitude]) view.dirty = true;
  charts.baseDirty = charts.overDirty = true;
  wake();
}

function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set) return set;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// --- State ------------------------------------------------------------------

const app = {
  flight: null,
  time: 0,
  playing: false,
  scrubbing: false,
  wasPlaying: false,
  speed: 1,
  textClock: Infinity,
};

if (storedTheme()) document.documentElement.dataset.theme = storedTheme();
palette.refresh();

const scene = await makeScene();

/** The cinematic WebGL view where the browser can draw it, the line drawing otherwise. */
async function makeScene() {
  let canvas = $("#scene");
  try {
    const { Cinema, webglAvailable } = await import("./cinema.js");
    if (!webglAvailable()) throw new Error("WebGL is not available");
    const view = new Cinema(canvas, palette, {
      reducedMotion,
      labels: $("#scene-labels"),
      onModeChange: (mode) => {
        for (const b of document.querySelectorAll("#camera button")) {
          b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
        }
        wake();
      },
    });
    $("#camera").hidden = false;
    document.body.classList.add("cinema");
    window.apogeeView = view; // handy from the console
    return view;
  } catch (err) {
    console.warn("3D view unavailable, using the line drawing:", err);
    // a canvas that already holds a WebGL context cannot give a 2D one
    const fresh = canvas.cloneNode(false);
    canvas.replaceWith(fresh);
    canvas = fresh;
    $("#camera").hidden = true;
    document.body.classList.remove("cinema");
    return new Scene(canvas, palette, { reducedMotion });
  }
}
const attitude = new Attitude($("#attitude"), palette, { reducedMotion });
const charts = new Charts($("#charts"), palette, {
  reducedMotion,
  onSeek: (t) => seek(t),
  onScrub: (on) => setScrubbing(on),
});

// --- Loading ----------------------------------------------------------------

function loadFlight(doc, { autoplay }) {
  const flight = parseFlight(doc);
  app.flight = flight;
  scene.setFlight(flight);
  attitude.setFlight(flight);
  charts.setFlight(flight);
  renderStatic(flight);
  hideError();
  const start = reducedMotion || !autoplay ? flight.apogeeTime : 0;
  seek(start, { force: true });
  setPlaying(autoplay && !reducedMotion);
  wake();
}

const EXAMPLE_NOTES = {
  "flights/kestrel-ab.json":
    "The flight above is Kestrel AB, the example rocket with airbrakes, flying itself to a 4,400 m target in a 4 m/s west wind.",
  "flights/kestrel.json": "The flight above is Kestrel, the example rocket, flying passively in a 4 m/s west wind.",
};

async function loadExample(file) {
  for (const b of document.querySelectorAll("#examples button")) {
    b.setAttribute("aria-pressed", String(b.dataset.file === file));
  }
  try {
    let res = await fetch(file, { cache: "no-cache" });
    if (!res.ok && file !== "flights/kestrel.json") {
      file = "flights/kestrel.json";
      res = await fetch(file, { cache: "no-cache" });
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    loadFlight(await res.json(), { autoplay: true });
    $("#source-note").textContent = EXAMPLE_NOTES[file] || "";
  } catch (err) {
    console.error(err);
    showError(
      "The example flight did not load. Check your connection and reload, or open a flight file " +
        "made with  apogee simulate --json flight.json."
    );
  }
}

async function loadFile(file) {
  if (!file) return;
  try {
    const text = await file.text();
    let doc;
    try {
      doc = JSON.parse(text);
    } catch {
      throw new FlightFileError(
        `${file.name} is not JSON. Make a flight file with:  apogee simulate my_rocket.yaml --json flight.json`
      );
    }
    loadFlight(doc, { autoplay: true });
    for (const b of document.querySelectorAll("#examples button")) b.setAttribute("aria-pressed", "false");
    $("#source-note").textContent = `Showing ${file.name}, read in your browser. Nothing was uploaded.`;
  } catch (err) {
    showError(err instanceof FlightFileError ? err.message : `Could not read ${file.name}: ${err.message}`);
  }
}

function showError(message) {
  const el = $("#error");
  el.querySelector("p").textContent = message;
  el.hidden = false;
}

function hideError() {
  $("#error").hidden = true;
}

// --- Static content for a flight ---------------------------------------------

function renderStatic(f) {
  const r = f.rocket, s = f.summary, site = f.site;
  document.title = `${r.name} · Apogee flight viewer`;
  $("#rocket-name").textContent = r.name;
  const motor = r.motor ? `${r.motor.manufacturer} ${r.motor.designation}` : "no motor";
  const rail = `${fmt(site.rail_length, 1)} m rail${site.tilt ? `, ${fmt(site.tilt, 0)}° toward ${compass(site.heading)}` : ""}`;
  const wind = site.wind.speed > 0 ? `wind ${fmt(site.wind.speed, 1)} m/s from ${compass(site.wind.direction)}` : "calm";
  $("#flight-meta").textContent = `${motor}  ·  ${rail}  ·  ${wind}`;

  const ab = f.airbrakes;
  const signed = (x, d) => `${x >= 0 ? "+" : "−"}${fmt(Math.abs(x), d)}`;
  const stats = [
    ["Apogee", fmt(s.apogee, 0), "m", `at ${fmt(s.time_to_apogee, 1)} s`],
    ["Max speed", fmt(s.max_speed, 0), "m/s", `Mach ${fmt(s.max_mach, 2)}`],
    ab
      ? [
          "Target miss",
          signed(ab.error, 1),
          "m",
          ab.passive_apogee != null
            ? `${signed(ab.passive_apogee - ab.target, 0)} m if flown passively`
            : `target ${fmt(ab.target, 0)} m`,
        ]
      : ["Max acceleration", fmt(s.max_acceleration_g, 1), "g", `at ${fmt(s.max_acceleration_time, 2)} s`],
    ["Off the rail", fmt(s.rail_exit_speed, 1), "m/s", `${fmt(r.outline.margin_liftoff, 2)} cal margin`],
    [
      "Landing",
      fmt(s.landing_distance, 0),
      "m",
      s.landing_distance >= 1 ? `${compass(s.landing_bearing)} of the pad` : "on the pad",
    ],
    ["Flight time", fmt(s.flight_time, 0), "s", s.landing_speed != null ? `lands at ${fmt(s.landing_speed, 1)} m/s` : ""],
  ];
  $("#summary").innerHTML = stats
    .map(
      ([label, value, unit, note]) =>
        `<div class="stat"><dt>${label}</dt><dd><span class="num">${value}</span><span class="unit">${unit}</span></dd><dd class="note">${note}</dd></div>`
    )
    .join("");

  // events
  $("#events").innerHTML = f.events
    .map(
      (e, k) => `<li><button type="button" data-k="${k}" data-time="${e.time}">
        <span class="ev-time">${fmt(e.time, e.time < 100 ? 2 : 1)} s</span>
        <span class="ev-name">${e.name}</span>
        <span class="ev-alt">${fmt(e.altitude, e.altitude < 100 ? 1 : 0)} m</span>
      </button></li>`
    )
    .join("");
  for (const b of $("#events").querySelectorAll("button")) {
    b.addEventListener("click", () => seek(Number(b.dataset.time), { force: true }));
  }

  // timeline phases and event ticks
  const track = $("#track-phases");
  track.innerHTML = f.phases
    .map((p) => {
      const left = (p.start / f.duration) * 100, width = ((p.end - p.start) / f.duration) * 100;
      return `<span class="seg ${phaseKind(p.name)}" style="left:${left}%;width:${width}%" title="${phaseLabel(p.name)}"></span>`;
    })
    .join("");
  const seen = new Set();
  $("#track-events").innerHTML = f.events
    .filter((e) => {
      const key = e.time.toFixed(2);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((e) => `<span class="tick" style="left:${(e.time / f.duration) * 100}%" title="${e.name} · ${fmt(e.time, 1)} s"></span>`)
    .join("");
  $("#scrub").setAttribute("aria-valuemax", f.duration.toFixed(1));
  $("#duration").textContent = missionTime(f.duration).replace("T+", "");

  // legend: only the phases this flight has
  const kinds = [...new Set(f.phases.map((p) => phaseKind(p.name)))];
  const names = { boost: "Powered", coast: "Coast", drogue: "Drogue", main: "Main" };
  $("#legend").innerHTML = kinds.map((k) => `<li><span class="swatch ${k}"></span>${names[k]}</li>`).join("");

  $("#computer").hidden = !ab;
  $("#hud-brakes").hidden = !ab;
  if (ab) {
    $("#r-target").textContent = fmt(ab.target, 0);
    $("#hud-target").textContent = `${fmt(ab.target, 0)} m`;
  }
  $("#warnings").innerHTML = f.warnings.map((w) => `<li>${w}</li>`).join("");
  $("#warnings").hidden = !f.warnings.length;
  $("#scene").setAttribute(
    "aria-label",
    `3D trajectory of ${r.name}: up to ${fmt(s.apogee, 0)} m, landing ${fmt(s.landing_distance, 0)} m ${compass(s.landing_bearing)} of the pad. Drag to orbit, scroll or pinch to zoom, double-click to reset.`
  );
}

// --- Clock ------------------------------------------------------------------

function seek(t, { force = false } = {}) {
  const f = app.flight;
  if (!f) return;
  app.time = Math.min(f.duration, Math.max(0, t));
  const st = f.sample(app.time);
  scene.setState(st);
  attitude.setState(st);
  if (force) app.textClock = Infinity;
  wake();
}

function setPlaying(on) {
  const f = app.flight;
  if (on && f && app.time >= f.duration - 1e-6) seek(0, { force: true });
  app.playing = on;
  const btn = $("#play");
  btn.setAttribute("aria-pressed", String(on));
  btn.setAttribute("aria-label", on ? "Pause" : "Play");
  btn.querySelector(".play-label").textContent = on ? "Pause" : "Play";
  btn.classList.toggle("is-playing", on);
  wake();
}

function setScrubbing(on) {
  if (on) {
    app.wasPlaying = app.playing;
    app.playing = false;
  } else if (app.wasPlaying) {
    app.playing = true;
  }
  app.scrubbing = on;
  wake();
}

function setSpeed(speed) {
  app.speed = speed;
  for (const b of document.querySelectorAll("#speeds button")) {
    b.setAttribute("aria-pressed", String(Number(b.dataset.speed) === speed));
  }
}

function updateText() {
  const f = app.flight;
  const st = f.sample(app.time);
  $("#t-plus").textContent = missionTime(app.time);
  $("#r-alt").textContent = fmt(st.up, st.up < 100 ? 1 : 0);
  $("#r-speed").textContent = fmt(st.speed, st.speed < 100 ? 1 : 0);
  $("#r-mach").textContent = fmt(st.mach, 2);
  $("#r-accel").textContent = fmt(st.accel, 1);
  $("#r-range").textContent = fmt(Math.hypot(st.east, st.north), 0);
  $("#r-phase").textContent = phaseLabel(st.phase);
  $("#r-phase").dataset.kind = st.kind;
  $("#attitude-caption").textContent = attitude.caption(st);
  $("#hud-t").textContent = missionTime(app.time);
  $("#hud-phase").textContent = phaseLabel(st.phase);
  if (f.airbrakes) {
    const fc = f.computerAt(app.time);
    const pct = fmt(100 * st.brake, 0);
    const pred = fc && Number.isFinite(fc.predicted) && app.time <= f.apogeeTime ? fmt(fc.predicted, 0) : "–";
    $("#r-brake").textContent = pct;
    $("#r-pred").textContent = pred;
    $("#r-drag").textContent = fc ? fmt(fc.drag, 2) : "1.00";
    $("#hud-brake-pct").textContent = `${pct} %`;
    $("#hud-pred").textContent = pred === "–" ? pred : `${pred} m`;
  }
  charts.updateValues();

  const scrub = $("#scrub");
  scrub.setAttribute("aria-valuenow", app.time.toFixed(1));
  scrub.setAttribute("aria-valuetext", `${missionTime(app.time)}, ${phaseLabel(st.phase)}, altitude ${fmt(st.up, 0)} metres`);
  // next event gets the marker; past ones are inked, future ones muted
  let next = -1;
  f.events.forEach((e, k) => {
    if (next < 0 && e.time > app.time + 1e-3) next = k;
  });
  for (const b of $("#events").querySelectorAll("button")) {
    const k = Number(b.dataset.k);
    b.classList.toggle("past", f.events[k].time <= app.time + 1e-3);
    b.classList.toggle("next", k === next);
  }
}

function updateGauge() {
  const f = app.flight;
  if (!f.airbrakes) return;
  // per frame, eased by the panel motion itself: a transform, so it never re-lays out
  $("#hud-brake-bar").style.transform = `scaleX(${f.sample(app.time).brake.toFixed(4)})`;
}

function updateThumb() {
  const f = app.flight;
  const pct = (app.time / f.duration) * 100;
  $("#track-fill").style.transform = `scaleX(${app.time / f.duration})`;
  $("#thumb").style.left = `${pct}%`;
}

let last = null;
let frame = 0;
function loop(now) {
  frame = 0;
  const dt = last == null ? 0 : Math.min(0.1, (now - last) / 1000);
  last = now;
  const f = app.flight;
  if (f && app.playing && !app.scrubbing) {
    let t = app.time + dt * app.speed;
    if (t >= f.duration) {
      t = f.duration;
      seek(t, { force: true });
      setPlaying(false);
    } else seek(t);
  }
  let busy = app.playing;
  busy = scene.tick(dt) || busy;
  busy = attitude.tick(dt) || busy;
  if (f) busy = charts.tick(dt, app.time) || busy;
  scene.draw();
  attitude.draw();
  charts.draw();
  if (f) {
    updateThumb();
    updateGauge();
    app.textClock += dt;
    if (app.textClock >= TEXT_INTERVAL) {
      app.textClock = 0;
      updateText();
    }
  }
  if (busy) wake();
  else last = null;
}

function wake() {
  if (!frame) frame = requestAnimationFrame(loop);
}

// --- Controls ---------------------------------------------------------------

$("#play").addEventListener("click", () => setPlaying(!app.playing));
for (const b of document.querySelectorAll("#speeds button")) {
  b.addEventListener("click", () => setSpeed(Number(b.dataset.speed)));
}
for (const b of document.querySelectorAll("#range button")) {
  b.addEventListener("click", () => {
    charts.setMode(b.dataset.mode);
    for (const o of document.querySelectorAll("#range button")) o.setAttribute("aria-pressed", String(o === b));
    wake();
  });
}
$("#reset-view").addEventListener("click", () => { scene.resetView(); wake(); });
for (const b of document.querySelectorAll("#camera button")) {
  b.addEventListener("click", () => { scene.setMode?.(b.dataset.mode); wake(); });
}
for (const b of document.querySelectorAll("#examples button")) {
  b.addEventListener("click", () => loadExample(b.dataset.file));
}
function nextCamera() {
  if (!scene.setMode) return;
  const modes = ["auto", "chase", "ground", "orbit"];
  scene.setMode(modes[(modes.indexOf(scene.mode) + 1) % modes.length]);
  wake();
}
$("#theme").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  try { localStorage.setItem("apogee-theme", next); } catch { /* private mode: still switch */ }
  applyTheme(next);
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(storedTheme()));

const fileInput = $("#file");
$("#open").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => { loadFile(fileInput.files[0]); fileInput.value = ""; });
$("#error-close").addEventListener("click", hideError);

// drag and drop a flight file anywhere
let dragDepth = 0;
window.addEventListener("dragenter", (e) => {
  if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
  dragDepth++;
  document.body.classList.add("drop-ready");
});
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) document.body.classList.remove("drop-ready");
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("drop-ready");
  loadFile(e.dataTransfer.files[0]);
});

// timeline: pointer and keyboard
const scrub = $("#scrub");
function scrubTo(e) {
  const r = scrub.getBoundingClientRect();
  seek(((e.clientX - r.left) / r.width) * app.flight.duration, { force: true });
}
scrub.addEventListener("pointerdown", (e) => {
  if (!app.flight) return;
  scrub.setPointerCapture(e.pointerId);
  setScrubbing(true);
  scrubTo(e);
});
scrub.addEventListener("pointermove", (e) => app.scrubbing && scrub.hasPointerCapture(e.pointerId) && scrubTo(e));
for (const type of ["pointerup", "pointercancel"]) {
  scrub.addEventListener(type, () => app.scrubbing && setScrubbing(false));
}

function jumpEvent(direction) {
  const f = app.flight;
  const times = [...new Set(f.events.map((e) => e.time))];
  const target = direction > 0
    ? times.find((t) => t > app.time + 0.01)
    : [...times].reverse().find((t) => t < app.time - 0.01);
  seek(target ?? (direction > 0 ? f.duration : 0), { force: true });
}

document.addEventListener("keydown", (e) => {
  if (!app.flight || e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = e.target.tagName;
  const onButton = tag === "BUTTON" || tag === "INPUT";
  const step = e.shiftKey ? 10 : 1;
  const actions = {
    " ": () => !onButton && setPlaying(!app.playing),
    k: () => setPlaying(!app.playing),
    ArrowLeft: () => seek(app.time - step, { force: true }),
    ArrowRight: () => seek(app.time + step, { force: true }),
    Home: () => seek(0, { force: true }),
    End: () => seek(app.flight.duration, { force: true }),
    ",": () => jumpEvent(-1),
    ".": () => jumpEvent(1),
    1: () => setSpeed(SPEEDS[0]),
    2: () => setSpeed(SPEEDS[1]),
    3: () => setSpeed(SPEEDS[2]),
    4: () => setSpeed(SPEEDS[3]),
    r: () => { scene.resetView(); wake(); },
    c: () => nextCamera(),
  };
  const action = actions[e.key];
  if (!action) return;
  if (e.key === " " && onButton) return; // let the focused button handle it
  action();
  e.preventDefault();
});

setSpeed(1);
loadExample("flights/kestrel-ab.json");
