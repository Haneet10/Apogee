# Flight viewer

The viewer plays back a simulated flight in the browser, in 3D. It is live at
**https://haneet10.github.io/Apogee/**, opening on Kestrel AB (the example rocket with
airbrakes, flying itself to a 4,400 m target in a 4 m/s west wind; **Passive** switches to
plain Kestrel), and it plays any flight you simulate yourself:

```bash
apogee simulate my_rocket.yaml --json flight.json
```

Choose **Open flight** or drop `flight.json` on the page. The file is read in your browser
with the File API; nothing is uploaded.

## What you see

| Panel | Shows |
|---|---|
| Summary strip | Apogee, max speed and Mach, peak acceleration, rail-exit speed and liftoff margin, landing distance, flight time |
| Flight (3D) | The rocket modelled from its own outline (nose profile, body sections with joint lines, fins, airbrake panels that slide out with the real deployment), on its rail on a concrete pad, over procedural terrain under a day sky (light theme) or a starry dusk (dark theme). Exhaust plume and nozzle glow under thrust; a smoke trail that grows and drifts with the wind; drogue and main canopies that ease open and sway gently; the flight path coloured by phase; the apogee, landing and pad labelled; a halo at the target apogee for airbrake flights. Cameras: **Auto** (a ground tracking camera 420 m from the pad for the launch, then a chase camera), **Chase**, **Ground** and **Orbit** (drag to orbit the whole trajectory) |
| HUD | Mission time and phase over the scene; for airbrake flights a panel gauge, the flight computer's predicted apogee and the target |
| Flight computer | Predicted apogee, airbrake extension, target and the learned drag correction at the playback time (airbrake flights only) |
| Telemetry | Altitude, speed, Mach, acceleration and downrange distance at the playback time |
| Attitude | The rocket's real outline (nose profile, body sections, fins) tilted to its pitch from vertical, with CG and CP marked; a canopy once a parachute is open |
| Flight data | Altitude, speed, acceleration (and airbrake extension, for airbrake flights) against time. **Auto** shows the ascent, then eases out to the whole flight after apogee; **Ascent** and **Whole flight** pin either range. Drag across a plot to scrub |
| Events | Every event with its time and altitude; select one to jump to it |

Controls: drag the 3D view to orbit, scroll or pinch to zoom, double-click (or `R`) to reset, `C` to cycle the cameras.
`Space` plays and pauses, `←` `→` step 1 s (`Shift` for 10 s), `,` `.` jump between events,
`1`–`4` pick 1×, 4×, 16× or 64×. Playback defaults to real time. With
`prefers-reduced-motion` the page opens paused at apogee and the camera and axes move
without easing.

## Design notes

- **No build step.** ES modules served as they are. The 3D view (`cinema.js`) uses three.js
  0.170, vendored in `viewer/vendor/` (MIT licence alongside) and resolved with an import
  map. Everything in it is a pure function of playback time (smoke puffs are emitted
  along the powered path and aged, canopies open on their event times), so scrubbing
  backwards rebuilds exactly the same frame. The terrain is fractal value noise, flat for
  3.5 km around the pad so a low tracking camera sees the horizon, rising to a hazy range
  beyond. Where WebGL is missing the page falls back to the original hand-written
  orthographic line drawing (`scene.js`) on a 2D canvas.
- **Calm motion.** One `requestAnimationFrame` loop drives every view from a single clock
  and stops when nothing moves. Camera cuts blend over 1.8 s with smoothstep easing, the
  chase camera's offset eases round as the rocket turns, plot axes and attitude ease
  exponentially toward their targets instead of snapping; the plume ripples slowly at a
  steady brightness, stars do not twinkle, and the airbrake gauge moves with the panels
  as a CSS transform; text readouts refresh ten times a second rather than
  every frame; nothing blinks.
- **Plots stay cheap.** Each plot has a static canvas (grid, events, the whole series) and
  an overlay (the flown part and the cursor), so a playback frame redraws only the overlay.

## The playback file

`apogee.playback.playback(flight)` builds it and `write_playback(flight, path)` writes it as
compact JSON (about 80 kB for Kestrel). Top-level keys:

| Key | Contents |
|---|---|
| `format`, `version` | `"apogee-flight"`, `1`. The viewer refuses other formats and newer versions with a message saying what to do |
| `rocket` | Name, description, motor (designation, impulse, burn time), liftoff mass and `outline`: nose profile as `[x, r]` points, body sections, fin sets, liftoff CG, CP and margin. Positions from the nose tip [m] |
| `site` | Rail length, tilt, heading, pad elevation, wind speed and direction |
| `summary` | The numbers `apogee simulate` prints |
| `events` | `name`, `time`, `altitude`, `speed`, `note` for each event |
| `phases` | Run-length `{name, start, end}`; boundaries sit on the event that caused them |
| `series` | Equal-length columns `t`, `east`, `north`, `up`, `speed`, `mach`, `acceleration_g`, `pitch` and `attitude` (body-to-world quaternions `[w, x, y, z]`); airbrake flights add `airbrakes`, the panel extension 0 to 1 |
| `airbrakes` | `null`, or for an airbrake flight: `target`, `error`, `passive_apogee` (when written by `apogee simulate`, which also flies the rocket passively), `max_mach`, `travel_time` and `log`, the flight computer's 10 Hz record (`t`, filtered `altitude` and `velocity`, `predicted_apogee` with `null` before burnout, `command`, `deployment`, `drag_factor`). `rocket.outline.airbrakes` gives the module's station and panel size. Older viewers ignore these keys |

Samples are thinned by phase: at most every 0.02 s on the rail and under thrust, 0.1 s
while coasting and 0.5 s under a canopy, and the step at or nearest each event is always
kept, so the apogee and landing in the file are the simulated ones. Linear interpolation
between samples stays within 0.5 m of every RK4 step on the way up and within 1 m over the
whole flight; `tests/test_playback.py` checks this along with the spacing, phase
boundaries, outline geometry and summary.

## Deployment

`.github/workflows/pages.yml` installs the package, flies Kestrel and Kestrel AB with
`apogee simulate --wind 4 --json viewer/flights/kestrel.json` (and
`examples/kestrel-airbrakes.yaml` into `kestrel-ab.json`) and publishes `viewer/` to
GitHub Pages on every push to `main`, so the page always shows what the current solver
predicts. To work on the viewer locally:

```bash
mkdir -p viewer/flights
apogee simulate --wind 4 -o none --json viewer/flights/kestrel.json
python -m http.server -d viewer 8000     # then open http://localhost:8000
```
