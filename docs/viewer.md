# Flight viewer

The viewer plays back a simulated flight in the browser. It is live at
**https://haneet10.github.io/Apogee/**, opening on Kestrel (the example rocket in a 4 m/s
west wind), and it plays any flight you simulate yourself:

```bash
apogee simulate my_rocket.yaml --json flight.json
```

Choose **Open flight** or drop `flight.json` on the page. The file is read in your browser
with the File API; nothing is uploaded.

## What you see

| Panel | Shows |
|---|---|
| Summary strip | Apogee, max speed and Mach, peak acceleration, rail-exit speed and liftoff margin, landing distance, flight time |
| Trajectory | Orbitable 3D path from pad to landing, coloured by phase (powered, coast, drogue, main), with its ground track, the apogee and landing marked, and the wind direction |
| Telemetry | Altitude, speed, Mach, acceleration and downrange distance at the playback time |
| Attitude | The rocket's real outline (nose profile, body sections, fins) tilted to its pitch from vertical, with CG and CP marked; a canopy once a parachute is open |
| Flight data | Altitude, speed and acceleration against time. **Auto** shows the ascent, then eases out to the whole flight after apogee; **Ascent** and **Whole flight** pin either range. Drag across a plot to scrub |
| Events | Every event with its time and altitude; select one to jump to it |

Controls: drag the 3D view to orbit, scroll or pinch to zoom, double-click (or `R`) to reset.
`Space` plays and pauses, `←` `→` step 1 s (`Shift` for 10 s), `,` `.` jump between events,
`1`–`4` pick 1×, 4×, 16× or 64×. Playback defaults to real time. With
`prefers-reduced-motion` the page opens paused at apogee and the camera and axes move
without easing.

## Design notes

- **No dependencies.** Four small ES modules (`flight`, `scene`, `charts`, `attitude`) on
  2D canvases. The 3D view is an orthographic orbit camera written out by hand: yaw about
  the vertical, then elevation, fitted once from the default view so rotating never
  changes the scale.
- **Calm motion.** One `requestAnimationFrame` loop drives every view from a single clock
  and stops when nothing moves. Camera, plot axes and attitude ease exponentially toward
  their targets instead of snapping; text readouts refresh ten times a second rather than
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
| `series` | Equal-length columns `t`, `east`, `north`, `up`, `speed`, `mach`, `acceleration_g`, `pitch` and `attitude` (body-to-world quaternions `[w, x, y, z]`) |

Samples are thinned by phase: at most every 0.02 s on the rail and under thrust, 0.1 s
while coasting and 0.5 s under a canopy, and the step at or nearest each event is always
kept, so the apogee and landing in the file are the simulated ones. Linear interpolation
between samples stays within 0.5 m of every RK4 step on the way up and within 1 m over the
whole flight; `tests/test_playback.py` checks this along with the spacing, phase
boundaries, outline geometry and summary.

## Deployment

`.github/workflows/pages.yml` installs the package, flies Kestrel with
`apogee simulate --wind 4 --json viewer/flights/kestrel.json` and publishes `viewer/` to
GitHub Pages on every push to `main`, so the page always shows what the current solver
predicts. To work on the viewer locally:

```bash
mkdir -p viewer/flights
apogee simulate --wind 4 -o none --json viewer/flights/kestrel.json
python -m http.server -d viewer 8000     # then open http://localhost:8000
```
