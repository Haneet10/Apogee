# Roadmap

Apogee is built one part per day. Each day is a complete, tested step that works on its own.

## Week 1 (Nov 19 – Nov 25, 2026)

- [ ] **Day 1 — Foundations and atmosphere.** Package layout (`src/apogee`), `pyproject.toml`, pytest, GitHub Actions CI. 1976 US Standard Atmosphere (temperature, pressure, density, speed of sound, viscosity to 86 km), tested against published table values.
- [ ] **Day 2 — The rocket.** Component model (nose cone shapes, body tube, trapezoidal fin set, motor mount, parachute) loaded from a YAML file. Mass, centre of gravity and inertia tensor, changing as propellant burns. RASP `.eng` thrust-curve loader with total-impulse check. Example rocket + motor in `examples/`.
- [ ] **Day 3 — Aerodynamics.** Barrowman normal-force slopes and centre of pressure, stability margin in calibres. Drag build-up vs Mach and Reynolds number (skin friction, nose pressure, base, fins). Pitch damping moment. Tests against Barrowman worked examples.
- [ ] **Day 4 — 6-DOF dynamics.** State vector (position, velocity, quaternion, body rates), forces and moments in body and world frames, launch rail constraint, wind. RK4 integrator. Tests: drag-free flight matches closed form, quaternion stays normalised, torque-free spin conserves angular momentum.
- [ ] **Day 5 — Full flight.** Event detection (rail exit, burnout, apogee, deployment, landing), parachute descent, flight summary. CLI: `apogee simulate examples/rocket.yaml` writes a CSV and prints apogee, max speed, max g, rail-exit speed, landing distance.
- [ ] **Day 6 — Validation.** Reference flight compared with OpenRocket (apogee, time to apogee, max velocity), error table and comparison plots in `docs/validation.md`. Monte Carlo landing dispersion (wind, mass, thrust scatter).
- [ ] **Day 7 — Browser viewer and polish.** Static page that plays back a flight (trajectory, altitude/velocity plots, attitude), deployed to GitHub Pages. README screenshots, usage guide, and a row in the projects index.
