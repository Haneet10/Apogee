# Apogee

**A rocket flight simulator you can read.** Give it a rocket (nose cone, body tube, fins, motor) and it predicts the whole flight: how high it goes, how fast, how stable it is, and where it lands. Results are checked against analytic solutions and against OpenRocket, the hobby-rocketry standard.

> **Status:** in active development (week of Nov 19, 2026). The atmosphere model is done; the rocket, aerodynamics and flight solver land over the coming days. See [`docs/roadmap.md`](docs/roadmap.md).

## Quick start

```bash
pip install git+https://github.com/Haneet10/Apogee
apogee atmosphere 0 1.5km 10000ft
```

```
Altitude    Temp    Temp  Pressure   Density  Sound speed  Viscosity
     [m]     [K]    [°C]      [Pa]   [kg/m³]        [m/s]    [µPa·s]
────────  ──────  ──────  ────────  ────────  ───────────  ─────────
       0  288.15   15.00   101,325    1.2250       340.29    17.8938
   1,500  278.40    5.25    84,560    1.0581       334.49    17.4196
   3,048  268.35   -4.80    69,695   0.90477       328.39    16.9221
```

Altitudes take `m` (the default), `km` or `ft`. Leave them out for a table from sea level to 20 km, or add `--csv` to pipe the numbers into a spreadsheet. From Python:

```python
from apogee import atmosphere

air = atmosphere(1500.0)   # metres above sea level; NumPy arrays work too
air.density                # 1.0581 kg/m³
```

## What it will do

- Simulate a full flight in six degrees of freedom (position *and* orientation), from the launch rail to touchdown
- Model the atmosphere, motor thrust curves (standard `.eng` files), drag, wind and weathercocking, and parachute recovery
- Report apogee, max velocity, max acceleration, stability margin and landing distance
- Play the flight back in the browser

## For engineers

- Rigid-body 6-DOF equations with quaternion attitude, integrated with fixed-step RK4 and event detection (burnout, apogee, deployment, landing)
- 1976 US Standard Atmosphere, -5 to 86 km, matching every published table value tested to within 0.007 % (the table's own rounding) and checked for hydrostatic balance. Method and validation: [`docs/atmosphere.md`](docs/atmosphere.md)
- Barrowman centre-of-pressure and normal-force slopes; component drag build-up (skin friction, pressure, base, fin) as a function of Mach and Reynolds number
- Validation suite: drag-free ballistic flight vs closed form, energy and quaternion-norm checks, and a reference flight compared to OpenRocket

## Development

```bash
git clone https://github.com/Haneet10/Apogee && cd Apogee
pip install -e ".[dev]"
pytest            # unit tests and doctests
ruff check . && mypy
```

Python 3.11+ and NumPy for the solver, pytest for tests, plain HTML/JS for the viewer.
