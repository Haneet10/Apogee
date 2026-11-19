# Apogee

**A rocket flight simulator you can read.** Give it a rocket (nose cone, body tube, fins, motor) and it predicts the whole flight: how high it goes, how fast, how stable it is, and where it lands. Results are checked against analytic solutions and against OpenRocket, the hobby-rocketry standard.

> **Status:** in active development (week of Nov 19, 2026). See [`docs/roadmap.md`](docs/roadmap.md) for what lands each day.

## What it will do

- Simulate a full flight in six degrees of freedom (position *and* orientation), from the launch rail to touchdown
- Model the atmosphere, motor thrust curves (standard `.eng` files), drag, wind and weathercocking, and parachute recovery
- Report apogee, max velocity, max acceleration, stability margin and landing distance
- Play the flight back in the browser

## For engineers

- Rigid-body 6-DOF equations with quaternion attitude, integrated with fixed-step RK4 and event detection (burnout, apogee, deployment, landing)
- 1976 US Standard Atmosphere; Barrowman centre-of-pressure and normal-force slopes; component drag build-up (skin friction, pressure, base, fin) as a function of Mach and Reynolds number
- Validation suite: drag-free ballistic flight vs closed form, energy and quaternion-norm checks, and a reference flight compared to OpenRocket

## Stack

Python 3.11+ and NumPy for the solver, pytest for tests, plain HTML/JS for the viewer.
