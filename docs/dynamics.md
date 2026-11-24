# Flight dynamics

This is the part that actually flies the rocket. Every hundredth of a second
Apogee adds up the forces and moments on the rocket (thrust, gravity, drag and
the air's sideways push) and works out where it will be, how fast it will be
going, and which way it will be pointing a moment later. It does this in six
degrees of freedom: three for position and three for orientation, so wind,
weathercocking and pitch wobble all come out of the physics rather than being
assumed.

```python
from apogee import load_rocket
from apogee.dynamics import Dynamics, integrate
from apogee.environment import LaunchSite, Wind

rocket = load_rocket("examples/rocket.yaml")
site = LaunchSite(
    rail_length=5.2,                      # guided travel on the rail [m]
    tilt=0.0, heading=0.0,                # rail angle from vertical, and which way it leans
    altitude=0.0,                         # pad above sea level [m]
    wind=Wind(speed=4.0, direction=270),  # 4 m/s at 10 m, from the west
)
flight = integrate(Dynamics(rocket, site), t_end=60.0, dt=0.01,
                   stop=lambda t, y: t > 5 and y[5] < 0)   # stop once it starts falling

flight.altitude.max()          # 4,841 m above the pad
flight.position[-1]            # [-254, 0, 4841] m east, north, up: it flew 254 m upwind
flight.pitch_from_vertical     # degrees, at every step
```

For a whole flight with events, parachutes and a summary, use `apogee simulate`
([`flight.md`](flight.md)); this page covers the equations underneath.

## State and frames

The state is 13 numbers:

| | Symbol | Frame | Units |
|---|---|---|---|
| Position of the CG | **r** | world | m |
| Velocity of the CG | **v** | world | m/s |
| Attitude | *q* = [w, x, y, z] | body → world | – |
| Angular rates | **ω** | body | rad/s |

*World* axes are east, north, up, with the origin at the CG as the rocket
sits on the pad. The Earth is treated as flat and non-rotating; over a few
kilometres the error is a few metres of Coriolis drift. *Body* axes have x
along the rocket towards the nose.

Attitude is a unit quaternion rather than Euler angles because Euler angles
have a singularity at 90° pitch, which is exactly where a rocket points at
launch (J. Diebel, *Representing Attitude*, Stanford, 2006).

## Equations of motion

Translation (world axes):

```
r' = v
v' = (T + W + D + N) / m
```

* **Thrust** `T = T(t) x̂_body`, from the thrust curve, along the axis
  through the CG (no thrust misalignment yet).
* **Weight** `W = −m g(h) ẑ`, inverse-square gravity from the 1976 standard.
* **Drag** `D = −½ρV² A Cd v̂_air` against the air-relative velocity
  `v_air = v − v_wind(h)`. `Cd` comes from the drag build-up in
  [`aerodynamics.md`](aerodynamics.md) at the current Mach number, with the
  base drag reduced while the motor burns.
* **Normal force** `N = ½ρV² A CN(α)`, across the body, opposite the
  cross-flow, where α is the total angle between the body axis and
  `v_air`. `CN(α)` includes the body's non-linear cross-flow lift, so the CP
  moves forward at large α.

Rotation (body axes), Euler's equations for an axisymmetric rigid body with
principal inertias `Ixx` (roll) and `Iyy = Izz` (pitch, yaw):

```
q' = ½ q ⊗ [0, ω]
I ω' = M − ω × (I ω)
M = (x_cg − x_cp) x̂ × N_body − (C_aero + C_jet) [0, ω_y, ω_z]
```

* The **restoring moment** comes from the normal force acting at the CP. When
  the CP is behind the CG it turns the nose into the relative wind.
* **Aerodynamic damping** `C_aero = ½ρV A Σ CNα_i (x_i − x_cg)²` (Stine).
* **Jet damping** `C_jet = ṁ (x_nozzle − x_cg)²`. Exhaust leaving the nozzle
  carries away the transverse velocity it had at the nozzle, which acts like a
  damper on pitch and yaw (W. Box, C. Bishop and E. Hunt, *Estimating the
  dynamic and aerodynamic parameters of passively controlled high power
  rockets for flight simulation*, 2009). For Kestrel during the burn it is
  about 0.7 N·m·s/rad, some 40 % of the aerodynamic damping at rail-exit
  speed, so it matters most in the first second of free flight.

Mass, CG and inertia follow the motor burn (propellant consumed in proportion
to impulse delivered, see [`rocket.md`](rocket.md)). They are tabulated once
at 400+ instants and interpolated, which matches the exact values to 10⁻⁵ and
is far faster than rebuilding them from every part at every step.

### The launch rail

Until the CG has travelled `rail_length` along the rail, the rail carries every
force across it: the rocket slides along the rail direction, attitude and
rates stay frozen, and it sits on the launch stop until thrust exceeds weight
(0.055 s after ignition for Kestrel). Once it leaves, it is never caught by
the rail again, even if it comes back down past the pad.

### Wind

Wind blows horizontally and strengthens with height by the 1/7 power law used
for open ground (`v(h) = v_ref (h / 10 m)^(1/7)`). `direction` is where it
blows *from*, like a weather report. Set `exponent=0` for a uniform wind.

## Integration

Classic fourth-order Runge-Kutta with a fixed step (0.01 s by default). The
quaternion is renormalised after every step to clear round-off; the RK4 drift
it removes is below 10⁻¹⁰ over a 4 s, 20 rad/s spin. Rail exit and burnout
are discontinuities, so the step is kept small; event-located steps arrive on
Day 5.

## Validation

Every check below runs in CI (`tests/test_dynamics.py`).

| Check | Reference | Result |
|---|---|---|
| Vacuum ballistic flight, 8 s | `r = v₀t − ½gt²` | exact to 10⁻⁹ m |
| Powered vertical flight in vacuum, Kestrel burn | rocket equation `c ln(m_lift / m_bo) − g (t_b − t_lift)` with `c = I / m_p`, 608.25 m/s | 5 × 10⁻⁸ relative |
| Torque-free spin, Ixx ≠ Iyy, 4 s | body-frame precession at `Ω = (Ixx − Iyy) ω_x / Iyy` | rates to 10⁻⁷ rad/s |
| Same spin | angular momentum (world) and rotational energy conserved | 10⁻⁷ and 10⁻⁹ relative |
| Pitch oscillation at 150 m/s | linear theory `ω_d = √(k/I) √(1 − ζ²)`, `k = q A CNα (x_cp − x_cg)` | period 0.1610 s vs 0.1610 s (0.01 %), ζ = 0.10, every swing smaller |
| Launch rail, tilted 10° in a crosswind | stays on the rail line, attitude unchanged | to 10⁻⁹ m |
| Crosswind | nose turns and drifts upwind, no motion across the wind | ✓ |
| Step size | 0.02 s vs 0.01 s after 6 s of Kestrel flight | < 0.5 m |
| RK4 order | `y' = y` | error ratio 16 per halving |

### Kestrel, first look

With no wind Kestrel leaves a 5.2 m rail at 41 m/s, peaks at 514 m/s
(Mach 1.5) and reaches 4,857 m. (Before the nose's wave drag was added on
Day 5 it reached 5,005 m.) A full comparison with OpenRocket is Day 6.
