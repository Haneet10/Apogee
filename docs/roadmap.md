# Roadmap

Apogee is built one part per day. Each day is a complete, tested step that works on its own.

## Week 1 (Nov 19 – Nov 25, 2026)

- [x] **Day 1 — Foundations and atmosphere.** Package layout (`src/apogee`), `pyproject.toml`, pytest, GitHub Actions CI. 1976 US Standard Atmosphere (temperature, pressure, density, speed of sound, viscosity to 86 km), tested against published table values.
- [x] **Day 2 — The rocket.** Component model (nose cone shapes, body tube, trapezoidal fin set, motor mount, parachute) loaded from a YAML file. Mass, centre of gravity and inertia tensor, changing as propellant burns. RASP `.eng` thrust-curve loader with total-impulse check. Example rocket + motor in `examples/`.
- [x] **Day 3 — Aerodynamics.** Barrowman normal-force slopes and centre of pressure, stability margin in calibres. Drag build-up vs Mach and Reynolds number (skin friction, nose pressure, base, fins). Pitch damping moment. Tests against Barrowman worked examples.
- [x] **Day 4 — 6-DOF dynamics.** State vector (position, velocity, quaternion, body rates), forces and moments in body and world frames, launch rail constraint, wind. RK4 integrator. Tests: drag-free flight matches closed form, quaternion stays normalised, torque-free spin conserves angular momentum.
- [x] **Day 5 — Full flight.** Event detection (rail exit, burnout, apogee, deployment, landing), parachute descent, flight summary. CLI: `apogee simulate examples/rocket.yaml` writes a CSV and prints apogee, max speed, max g, rail-exit speed, landing distance. Carried from Day 3: wave drag of smooth noses (ogive, von Kármán, Haack) above Mach 0.8, since Kestrel flies transonic.
- [x] **Day 6 — Validation.** Reference flight compared with an independent simulator (apogee, time to apogee, max velocity), error table and comparison plots in `docs/validation.md`. Monte Carlo landing dispersion (wind, mass, thrust scatter). OpenRocket could not run in the build environment, so the reference is RocketPy 1.13 on the same inputs; `apogee dispersion` and `docs/dispersion.md` cover the Monte Carlo.
- [x] **Day 7 — Browser viewer and polish.** Static page that plays back a flight (trajectory, altitude/velocity plots, attitude), deployed to GitHub Pages. README screenshots, usage guide, and a row in the projects index. `apogee simulate --json` writes the playback file; the viewer lives in `viewer/` and deploys from the Viewer workflow. The projects-index row moved to the backlog (that repository is edited by hand).

## Apogee v2: airbrakes and a cinematic viewer (Nov 26, 2026)

- [x] **Active airbrakes.** `airbrakes` part and `flight_computer` section in rocket files; panel drag and a rate-limited servo in the 6-DOF model; noisy barometer and accelerometer, a Kalman filter, in-flight drag learning and a predictive controller (`apogee.avionics`, `apogee.control`); `apogee simulate --target`, `--no-airbrakes`; `apogee airbrakes` Monte Carlo study; Kestrel AB example; `docs/airbrakes.md`.
- [x] **Cinematic viewer.** three.js scene: the rocket modelled from its outline with airbrake panels, exhaust plume, smoke trail drifting with the wind, drogue and main canopies, procedural terrain, day and dusk skies; directed, chase, ground-tracking and orbit cameras; target-apogee halo, airbrake HUD and plot; 2D fallback without WebGL. The page opens on Kestrel AB.

## Backlog

- [ ] Add Apogee to the projects index in Haneet10/Public, linking the repo and the live viewer.

- [ ] Airbrake realism: barometer position error near Mach 1, non-standard atmosphere, servo dynamics and backlash (see `docs/airbrakes.md`, "What it leaves out").

- [ ] OpenRocket cross-check: fly Kestrel in OpenRocket 23.09 (calm and 4 m/s crosswind) to settle the 146 m weathercocking gap between Apogee and RocketPy described in `docs/validation.md`.
