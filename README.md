# Apogee

**A rocket flight simulator you can read.** Give it a rocket (nose cone, body tube, fins, motor) and it predicts the whole flight: how high it goes, how fast, how stable it is, and where it lands. Results are checked against analytic solutions and against OpenRocket, the hobby-rocketry standard.

> **Status:** in active development (week of Nov 19, 2026). The atmosphere, motors, rocket mass model and aerodynamics are done; the 6-DOF flight solver lands over the coming days. See [`docs/roadmap.md`](docs/roadmap.md).

## Quick start

```bash
pip install git+https://github.com/Haneet10/Apogee
apogee rocket
```

With no file, `apogee rocket` loads the built-in example, Kestrel: a 4-inch fiberglass rocket on a 75 mm M motor. It lists every part, then the numbers that matter for flight:

```
                Mass       CG  Ixx roll  Iyy pitch
                [kg]     [mm]   [kg·m²]    [kg·m²]
─────────────  ─────  ───────  ────────  ─────────
Dry, no motor  5.731  1,392.0   0.01348      3.154
Liftoff        10.96  1,756.8   0.01865      5.004
Burnout        7.861  1,599.2   0.01647      4.164

Motor   CTI M1670-BS  ·  class M  ·  6,026 N·s  ·  3.90 s burn
Thrust-to-weight at liftoff 14.4 average, 20.5 peak
The CG moves 158 mm forward as the propellant burns.
```

### Your own rocket

Describe it in a YAML file, nose to tail. Lengths and masses take units (`mm`, `in`, `g`, `oz`...), and any part you have weighed can carry its real `mass`:

```yaml
name: My first rocket
motor:
  file: Estes_C6.eng            # any RASP .eng file, e.g. from thrustcurve.org
components:
  - nose_cone: {shape: ogive, length: 100mm, diameter: 24.8mm, thickness: 1.5mm, material: polystyrene}
  - body_tube:
      length: 300mm
      thickness: 0.5mm
      material: cardboard
      contents:
        - inner_tube: {length: 70mm, diameter: 19mm, thickness: 0.5mm, material: cardboard}
        - fins: {count: 3, root_chord: 60mm, tip_chord: 30mm, span: 45mm, sweep: 30mm, thickness: 2.4mm, material: balsa}
        - parachute: {diameter: 300mm, mass: 10g, position: 40mm}
```

```bash
apogee rocket my_rocket.yaml     # parts, mass, CG and inertia
apogee motor Estes_C6.eng        # impulse, class check, thrust curve
```

Every setting is listed in [`docs/rocket.md`](docs/rocket.md). Typos get a pointed answer: `unknown setting 'lenght'; did you mean 'length'?`

### Will it fly straight?

```bash
apogee aero my_rocket.yaml
```

`apogee aero` finds the centre of pressure, the static margin off the rail and at burnout, and the drag coefficient from Mach 0.1 to 2, split by source. For Kestrel:

```
            CG     CP  Margin
          [mm]   [mm]   [cal]
───────  ─────  ─────  ──────
Liftoff  1,757  1,990    2.28
Burnout  1,599  1,990    3.83
Off the rail: stable. The margin grows as the CG moves forward through the burn.
At 5° angle of attack the body's own lift moves the CP to 1,865 mm (liftoff margin 1.06 cal).

Mach         Re  Friction   Fins   Base   Nose  Cd coast  Cd burn     CP
      [million]                                                     [mm]
────  ─────────  ────────  ─────  ─────  ─────  ────────  ───────  ─────
0.30       17.6     0.339  0.020  0.132  0.000     0.491    0.420  1,992
0.60       35.2     0.330  0.032  0.167  0.000     0.529    0.439  2,000
```

A margin under 1 calibre gets a plain warning and a fix (more fin area or nose weight). Add `finish: smooth_paint` (or `polished`, `unfinished`, ...) to the rocket file to see what a better finish is worth. Method and validation: [`docs/aerodynamics.md`](docs/aerodynamics.md).

### The atmosphere

```bash
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

## What it does

- Load a rocket from a readable YAML file and a motor from any standard `.eng` thrust curve
- Work out mass, centre of gravity and moments of inertia, and how they change as the motor burns
- Find the centre of pressure and stability margin, and the drag coefficient across the speed range

Coming this week:

- Simulate a full flight in six degrees of freedom (position *and* orientation), from the launch rail to touchdown
- Model wind and weathercocking, and parachute recovery
- Report apogee, max velocity, max acceleration and landing distance
- Play the flight back in the browser

## For engineers

- Rigid-body 6-DOF equations with quaternion attitude, integrated with fixed-step RK4 and event detection (burnout, apogee, deployment, landing)
- 1976 US Standard Atmosphere, -5 to 86 km, matching every published table value tested to within 0.007 % (the table's own rounding) and checked for hydrostatic balance. Method and validation: [`docs/atmosphere.md`](docs/atmosphere.md)
- Mass model: closed-form hollow cylinders, nose cones integrated as shells of revolution (six profiles, Crowell 1996), trapezoidal fin sets integrated strip by strip, combined with the parallel-axis theorem. Motor propellant burns in proportion to delivered impulse. Every part is checked against a closed-form result, and the example motor's curve integrates to its published 6,026 N·s within 0.006 %. Method and validation: [`docs/rocket.md`](docs/rocket.md)
- Aerodynamics: Barrowman normal-force slopes and CP (nose CP from volume, fins with body interference and Diederich compressibility, Bonney/Ackeret above Mach 1.5), Galejs body lift at angle of attack, Niskanen/Hoerner drag build-up (roughness-limited skin friction, fin edges, base drag reduced by the motor plume) against Mach and Reynolds number, and pitch damping. Fin and whole-rocket CP match Barrowman's equations worked by hand to 10⁻⁶ and nose CPs match six closed forms. Method and validation: [`docs/aerodynamics.md`](docs/aerodynamics.md)
- Validation suite: drag-free ballistic flight vs closed form, energy and quaternion-norm checks, and a reference flight compared to OpenRocket

## Development

```bash
git clone https://github.com/Haneet10/Apogee && cd Apogee
pip install -e ".[dev]"
pytest            # unit tests and doctests
ruff check . && mypy
```

Python 3.11+, NumPy and PyYAML for the solver, pytest for tests, plain HTML/JS for the viewer.
