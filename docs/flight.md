# A full flight

`apogee simulate` flies a rocket from the launch rail to the ground: up to
apogee in six degrees of freedom, then down under its parachutes, drifting
with the wind. It prints what happened and when, the numbers people ask about
first, and writes every time step to a CSV file for plotting.

```bash
apogee simulate examples/rocket.yaml --rail 5.2 --wind 4 --wind-from 270
```

```
Kestrel  ·  CTI M1670-BS
5.2 m rail, vertical  ·  wind 4 m/s from W (270°)  ·  pad at sea level

Event                     Time  Altitude  Speed
                           [s]       [m]  [m/s]
──────────────────────  ──────  ────────  ─────
Rail exit                 0.32       5.2   41.3
Burnout                   3.90     1,201  486.9
Apogee                   27.43     4,841    3.2
Drogue parachute opens   27.43     4,841    3.2
Main parachute opens    261.41       300   18.7
Landing                 317.28       0.0    6.1

Apogee            4,841 m above the pad, 27.4 s after ignition
Max speed         513 m/s (Mach 1.53) at 3.3 s
Max acceleration  19.6 g at 0.15 s
Off the rail      41.3 m/s
Descent           17.5 m/s under the drogue parachute, then 5.3 m/s under the main parachute
Landing           2,046 m E of the pad (bearing 90°) at 5.3 m/s
Flight time       317.3 s

Wrote 8,543 time steps to kestrel-flight.csv
```

The rocket weathercocks into the west wind on the way up, then the wind carries
it back east under the canopies: 4 m/s at 10 m is almost 10 m/s at 5 km with
the power-law wind profile, so a 4-minute drogue descent ends 2 km downwind. A
smaller drogue, or a main that opens lower, brings it home closer.

## Options

| Option | Meaning | Default |
|---|---|---|
| `FILE` | Rocket YAML file | the bundled Kestrel |
| `--rail M` | Guided length of the launch rail [m] | 5 |
| `--tilt DEG`, `--heading DEG` | Rail angle from vertical, and the compass bearing it leans towards | vertical |
| `--wind M/S`, `--wind-from DEG` | Wind at 10 m and the bearing it blows from (weather-report convention) | calm, from 270° |
| `--elevation ALT` | Pad height above sea level (`1400`, `4600ft`) | sea level |
| `--dt S` | 6-DOF time step [s] | 0.01 |
| `-o CSV` | Where to write the time steps; `none` to skip | `<rocket name>-flight.csv`, e.g. `kestrel-flight.csv` |

Warnings are printed in plain words when something needs fixing: a rail-exit
speed under 15 m/s, a landing faster than 7.5 m/s, or no parachute at all.

From Python:

```python
from apogee import LaunchSite, Wind, load_rocket, simulate

flight = simulate(load_rocket("examples/rocket.yaml"),
                  LaunchSite(rail_length=5.2, wind=Wind(speed=4.0, direction=270)))
flight.apogee                  # 4,841 m
flight.event("Main parachute opens").time
flight.landing_distance        # 2,046 m
flight.write_csv("flight.csv")
```

### The CSV

One row per time step: `time_s`, position `east_m`, `north_m`, `altitude_m`
(above the pad), velocity `v_east_mps`, `v_north_mps`, `v_up_mps`,
`speed_mps`, air-relative `mach`, `acceleration_g`, `pitch_deg` (from
vertical), the attitude quaternion `q_w … q_z` and the flight `phase`
(`rail`, `boost`, `coast`, `under Drogue parachute`, ...). Event instants are
rows too, so the apogee row is the exact peak.

## Method

### Events

Each event is found between two integration steps by bisection on the cubic
Hermite curve through the step's end points (their positions and velocities),
which is as accurate as the RK4 step:

| Event | Condition |
|---|---|
| Rail exit | CG has travelled the rail's guided length |
| Burnout | end of the thrust curve |
| Apogee | vertical velocity changes sign |
| Parachute opens | `deploy: apogee`, or on the way down at `deploy: 300m` above the pad; a parachute set higher than the rocket gets opens at apogee, and the table says so |
| Landing | CG back at pad height |

When a parachute opens the simulation restarts from the interpolated event
state, so no step straddles a change in the equations.

### Descent

Under a parachute the rocket's attitude no longer matters, so the descent is a
3-DOF point mass: weight against the drag of the open canopies,

```
m dv/dt = −½ ρ |v − w| (v − w) Σ Cd·A  −  m g ẑ
```

with `w` the wind at the current height and `ρ` from the 1976 Standard
Atmosphere. This is the model OpenRocket uses under a parachute. Canopies open
instantly (opening loads are not modelled), so the "max acceleration" line
covers the flight up to apogee. Without a parachute the 6-DOF model keeps
flying the rocket ballistically to the ground.

Just after a canopy opens, drag relaxes the velocity towards terminal at a
rate `λ = ρ Cd·A |v − w| / m`. The descent step is the smaller of 0.05 s and
`0.5 / λ`, well inside RK4's stability limit (`λ dt ≈ 2.8`), so any canopy
size integrates cleanly.

## Validation

Every check is a test in [`tests/test_flight.py`](../tests/test_flight.py).

| Check | Expected | Result |
|---|---|---|
| Event location | rail exit at 5.2 m, burnout at the motor's burn time, main at 300 m, landing at 0 m | to 10⁻⁶ |
| Apogee is the true peak | no sample above the interpolated apogee; vertical speed there | 0 to 10⁻³ m/s |
| Step size | apogee at 0.02 s and 0.005 s vs 0.01 s | 0.19 m and 0.0002 m |
| Drogue sink rate at 300 m | terminal velocity `√(2 m g / ρ Cd·A)` = 17.48 m/s | 17.49 m/s (0.08 %; still settling as the air thickens) |
| Landing under both canopies | terminal velocity at sea level = 5.291 m/s | 5.292 m/s (0.007 %) |
| Constant wind | drifts at wind speed under the main; landing point downwind | to 0.1 % |
| Calm, vertical rail | lands on the pad | < 1 m |
| Parachute set above apogee | opens at apogee, with a note | ✓ |
| No parachute | ballistic to the ground, with a warning | ✓ |
| Too heavy to lift off | refused with a fix | ✓ |

The comparison with OpenRocket's Kestrel model is Day 6.

## References

- S. Niskanen, *Development of an Open Source Model Rocket Simulation Software*,
  M.Sc. thesis, Helsinki University of Technology, 2009, ch. 4 (flight
  simulation and recovery).
- E. Hairer, S. P. Nørsett and G. Wanner, *Solving Ordinary Differential
  Equations I*, 2nd ed., Springer, 1993, §II.6 (dense output and event location).
