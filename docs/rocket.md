# Rockets and motors

Before Apogee can fly a rocket it has to know how heavy it is, where it
balances, and how hard it is to rotate, and how all three change as the motor
burns. This page covers the rocket file format, the motor thrust curves, and
the maths behind the mass numbers.

```bash
apogee rocket examples/rocket.yaml      # or just `apogee rocket` for the built-in example
apogee motor examples/motors/Cesaroni_M1670.eng
```

```python
from apogee import load_rocket

kestrel = load_rocket("examples/rocket.yaml")
liftoff = kestrel.mass_properties(t=0.0)     # 10.962 kg, CG 1,756.8 mm from the nose tip
burnout = kestrel.mass_properties(t=3.9)     # 7.861 kg,  CG 1,599.2 mm
liftoff.inertia_tensor                       # 3×3 matrix about the CG, kg·m²
```

## Describing a rocket

A rocket is a YAML file. The outside of the rocket is a `nose_cone` followed by
one or more `body_tube` sections, listed nose to tail; they stack end to end.
Everything else goes in a body tube's `contents`.

| Part | Where | Required settings | Optional settings (default) |
|---|---|---|---|
| `nose_cone` | first | `length`, `diameter`, `thickness`, `material` or `density` | `shape` (`ogive`), `mass` |
| `body_tube` | after the nose | `length`, `thickness`, `material` or `density` | `diameter` (previous section's), `mass`, `contents` |
| `fins` | contents | `root_chord`, `span`, `thickness`, `material` or `density` | `count` (3), `tip_chord` (0), `sweep` (0), `mass` |
| `inner_tube` | contents | `length`, `diameter`, `thickness`, `material` or `density` | `motor_mount` (false), `mass` |
| `ring`, `bulkhead` | contents | `thickness`, `material` or `density`; `inner_diameter` for a ring | `diameter` (tube's inside), `mass` |
| `parachute` | contents | `diameter`, `mass` | `cd` (0.8), `deploy` (`apogee`, or an altitude like `300m`), `packed_length`, `packed_diameter` |
| `mass` | contents | `mass` | `length` (0, a point mass), `diameter` (0) |

* **Units.** Every length and mass may carry a unit: `mm`, `cm`, `m`, `in`,
  `ft` and `g`, `kg`, `oz`, `lb`. Bare numbers are SI. Densities take `kg/m3`
  or `g/cm3`.
* **Position.** Inside a tube, `position` is the distance from the tube's front
  end to the part's front end; `aft_offset` is the distance from the tube's aft
  end to the part's aft end. Fins and inner tubes sit flush with the tail of
  their tube unless told otherwise; everything else starts at the front.
* **Measured masses.** Add `mass:` to any part you have weighed. The weighed
  mass replaces the computed one and the moments of inertia scale with it.
* **Materials.** `cardboard` (680 kg/m³), `kraft phenolic` (950), `blue tube`
  (1300), `fiberglass` (1850), `carbon fiber` (1780), `balsa` (170), `basswood`
  (500), `plywood` (630), `polystyrene` (1050), `abs` (1050), `pla` (1250),
  `pvc` (1390) and `aluminum` (2700). British spellings work too. These are
  typical values; give `density:` for anything else.
* **Motor.** The `motor` block names a `.eng` file (relative to the YAML file)
  and an optional `overhang` past the mount's aft end. The motor goes in the
  inner tube marked `motor_mount: true` (or the only inner tube), and Apogee
  stops with a clear message if it does not fit.

* **Finish.** An optional top-level `finish:` sets the surface roughness used
  for skin-friction drag: `polished`, `smooth_paint`, `regular_paint` (the
  default), `unfinished`, `rough`, or a height such as `35um`. See
  [`aerodynamics.md`](aerodynamics.md).

Mistakes are reported with the exact setting and a suggestion, for example
`components[1].body_tube: unknown setting 'lenght'; did you mean 'length'?`.

## Motors

Thrust curves use the RASP `.eng` format that thrustcurve.org, OpenRocket and
RockSim all export:

```
; comments start with a semicolon
M1670-BS 75 757 0 3.101 5.231 CTI
0.055 100
0.092 1500
...
3.9 0
```

The header gives the designation, diameter and length in millimetres, the
ejection delays, the propellant and loaded masses in kilograms and the
manufacturer. The curve begins at (0 s, 0 N) and ends at zero thrust.

On loading, Apogee integrates the curve with the trapezoidal rule (exact for
the straight-line segments the format describes) and checks the result against
the impulse class in the designation. Each letter doubles the impulse: A is
1.26 to 2.50 N·s, B up to 5 N·s, C up to 10 N·s, and so on up to O (NFPA 1122
and 1127). A curve that integrates into the wrong class is flagged, because it
almost always means a typing error or a file in the wrong units.

**Burning mass.** With only a thrust curve to go on, propellant is consumed in
proportion to the impulse delivered:

    m_p(t) = m_p0 · (1 − I(t) / I_total)

which is exact for a constant effective exhaust velocity. The casing (loaded
mass minus propellant) is modelled as a thin-walled tube and the propellant as
a solid cylinder, both the full motor length. The motor's CG therefore stays at
its mid-length while its mass and moments of inertia fall.

## Mass, centre of gravity and inertia

Every part is symmetric about the rocket's axis, so each one is summarised by
four numbers: mass `m`, CG position `x` along the axis, axial (roll) moment
`Ixx` and transverse (pitch and yaw) moment `Iyy` about its own CG. The full
inertia tensor in body axes is `diag(Ixx, Iyy, Iyy)`.

| Part | Model |
|---|---|
| Body tube, inner tube, ring, bulkhead | Hollow cylinder: `Ixx = m(R² + r²)/2`, `Iyy = m(3(R² + r²) + L²)/12` |
| Nose cone | Shell of revolution, integrated in 2,000 annular slices between `r(x)` and `r(x) − t` |
| Fins | Flat trapezoidal plates, integrated in 2,000 span-wise strips |
| Parachute, mass, motor | Solid cylinders (the motor as tube plus cylinder, above) |

Nose profiles `r(x)` follow Crowell, *The Descriptive Geometry of Nose Cones*
(1996): `conical`, tangent `ogive`, `ellipsoid`, `parabolic`, `von_karman`
(Haack series, C = 0) and `lv_haack` (C = 1/3). A wall thicker than the base
radius gives a solid nose.

For fins, each strip at distance `ρ` from the rocket's axis contributes
`m ρ²` to `Ixx`. With two or more evenly spaced fins the transverse moment is
the same about every axis, so `Iyy = Σ m (x − x̄)² + Σ m c²/12 + ½ Σ m ρ²`.

The parts are combined about the rocket's CG with the parallel-axis theorem:

    m = Σ mᵢ        x_cg = Σ mᵢ xᵢ / m
    Ixx = Σ Ixxᵢ    Iyy = Σ (Iyyᵢ + mᵢ (xᵢ − x_cg)²)

## Validation

Every model is checked in `tests/` against a closed-form or published result.

| Check | Reference | Result |
|---|---|---|
| Hollow tube mass, CG, `Ixx`, `Iyy` | closed form | exact |
| Solid cone mass, CG at ¾ L, `Ixx = 3/10 m R²`, `Iyy = m(3R²/20 + 3L²/80)` | closed form | within 0.001 % |
| Thin conical shell CG at ⅔ L, mass `ρπtRL` | closed form | within 0.1 % |
| Every nose profile starts at r = 0, ends at R, never narrows | geometry | pass |
| Trapezoidal fin area, mass and chord-wise centroid | closed form | within 0.0001 % |
| Rectangular fin set `Ixx` and `Iyy` | closed form | within 0.0001 % |
| Two point masses combined (parallel axis) | hand calculation | exact |
| Trapezoidal test motor: impulse 9.0 N·s, partial impulse, mass, inertia | hand calculation | exact |
| Impulse class boundaries 1/8A to O | NFPA 1122 / 1127 | pass |
| Cesaroni M1670 thrust curve total impulse | 6,026 N·s published | 6,026.4 N·s (+0.006 %) |

## The example: Kestrel

`examples/rocket.yaml` is a 4-inch (102 mm) fiberglass high-power rocket,
2.52 m long, with dual deployment (a drogue at apogee and a main at 300 m) and
a Cesaroni M1670 Blue Streak 75 mm motor.

|  | Mass | CG from nose | `Ixx` | `Iyy` |
|---|---|---|---|---|
| Dry, no motor | 5.731 kg | 1,392 mm | 0.0135 kg·m² | 3.154 kg·m² |
| Liftoff | 10.962 kg | 1,757 mm | 0.0187 kg·m² | 5.004 kg·m² |
| Burnout | 7.861 kg | 1,599 mm | 0.0165 kg·m² | 4.164 kg·m² |

The CG moves 158 mm forward during the burn, which raises the stability margin
in flight; Day 3 adds the centre of pressure to put a number on it.

The M1670 thrust curve ships with RocketPy (MIT licence) and originally comes
from thrustcurve.org.
