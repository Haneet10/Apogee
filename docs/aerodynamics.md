# Aerodynamics

A rocket's flight is decided by three aerodynamic numbers: where the air's
sideways push acts (the **centre of pressure**, CP), how much the air slows it
down (the **drag coefficient**, Cd), and how quickly air resistance damps out a
wobble (the **pitch-damping coefficient**). This page shows how Apogee works
them out and how each method is checked.

```bash
apogee aero                      # the built-in example, Kestrel
apogee aero my_rocket.yaml --mach 0.3 0.6 0.9 --altitude 1500
```

```python
from apogee import load_rocket
from apogee.aero import Aerodynamics

aero = Aerodynamics(load_rocket("examples/rocket.yaml"))
aero.center_of_pressure()          # 1.990 m from the nose tip (Barrowman, Mach 0)
aero.stability_margin(t=0.0)       # 2.28 calibres at liftoff
aero.drag(mach=0.5).total          # 0.513, coasting at sea level
aero.drag(0.5, thrusting=True)     # DragBreakdown(friction=…, base=…, …)
aero.normal_force(alpha=0.1)       # (CN, x_cp) at 5.7° angle of attack
aero.damping_coefficient(density=1.225, speed=100.0, cg=1.757)   # N·m·s/rad
```

## Stability: the centre of pressure

When the rocket flies at a small angle of attack α, each part produces a
normal (sideways) force proportional to α. The CP is where the sum of those
forces acts. If it is behind the CG, the force turns the nose back into the
airflow. The distance between them, divided by the body diameter, is the
**static margin** in calibres. One to two calibres off the launch rail is the
usual target; Apogee says so in words:

| Margin | Verdict |
|---|---|
| below 0 | unstable: add fin area or nose weight |
| 0 to 1 | marginal |
| 1 to 3 | stable |
| above 3 | very stable: expect noticeable weathercocking into the wind |

The margin always grows during the burn because the propellant burns away
behind the CG, moving the CG forward.

### Method (Barrowman, 1967)

All slopes are per radian and use the reference area `A_ref = π d² / 4` of the
largest body diameter `d`.

**Nose cone.** Slender-body theory gives `CNα = 2 A_base / A_ref`, acting at

```
X_nose = L − V / A_base
```

with `L` the nose length and `V` its volume, integrated numerically from the
profile. This one formula reproduces every closed form in Barrowman's tables:

| Shape | CP / L | Why |
|---|---|---|
| conical | 2/3 | V = A L / 3 |
| ellipsoid | 1/3 | V = 2 A L / 3 |
| parabolic | 7/15 = 0.467 | ∫(2ξ − ξ²)² dξ = 8/15 |
| tangent ogive | 0.462 to 0.466 | tends to Barrowman's 0.466 as the nose gets slender |
| von Kármán | 1/2 | V = A L / 2 exactly |
| LV-Haack | 7/16 | |

**Fins.** Barrowman's fin formula for `N` trapezoidal fins of span `s`, root
chord `Cr`, tip chord `Ct` and mid-chord line length `l_m`:

```
CNα_fins = K_fb · 4 N (s/d)² / (1 + sqrt(1 + (2 l_m / (Cr + Ct))²))
K_fb     = 1 + R / (s + R)                     (fin–body interference, R the body radius)
X_fins   = X_root + (X_t / 3)(Cr + 2 Ct)/(Cr + Ct) + (Cr + Ct − Cr Ct / (Cr + Ct)) / 6
```

where `X_t` is the sweep of the tip's leading edge. Apogee writes the same
formula in Diederich's form so that it can carry the compressibility factor
`β = sqrt(1 − M²)`; at Mach 0 the two are identical. Five to eight fins lose a
little lift to each other (factors 0.948, 0.913, 0.854, 0.810, from Niskanen
2009).

**Faster than sound.** Above Mach 1.5 each fin follows linear supersonic
theory with Bonney's tip-loss correction, `CNα = 4/β (1 − 1/(2 A β))` per unit
fin area with `β = sqrt(M² − 1)` and aspect ratio `A`. Very stubby fins
(`A β < 1`) blend towards slender-wing theory's `π A / 2`. Between Mach 0.9 and
1.5 the slope is interpolated linearly, as OpenRocket does. The fins' lift
peaks around Mach 1.5 and the CP moves aft by a few centimetres.

**Body lift at angle of attack.** Barrowman's body tubes carry no lift, which is
true only at small angles. Galejs (1999) adds the cross-flow term

```
CN_body = 1.1 · (A_plan / A_ref) · sin²α     at the centroid of the side-on area A_plan
```

For a long rocket this is significant: at 5° Kestrel's CP moves 125 mm forward
and the liftoff margin falls from 2.28 to 1.06 calibres. That is why rockets
leaving the rail slowly in a crosswind can go unstable even when the Barrowman
margin looks healthy.

## Drag

Apogee builds the zero-lift drag coefficient from its parts (Niskanen 2009, the
method behind OpenRocket, with coefficients from Hoerner 1965). Each term below
is referred to `A_ref`.

| Term | Model |
|---|---|
| Skin friction | `Cf = 1 / (1.50 ln Re − 5.6)²` (turbulent), or `0.032 (ε/L)^0.2` once the surface roughness `ε` dominates, whichever is larger. Compressibility: × `(1 − 0.1 M²)` subsonic, ÷ `(1 + 0.15 M²)^0.58` supersonic. Applied to the wetted area with factors `1 + 1/(2 f)` for body fineness `f = L/d` and `1 + 2 t/c̄` for fin thickness. |
| Fin leading edges | Rounded: stagnation-pressure drag `(1 − M²)^−0.417 − 1` below Mach 0.9, Hoerner's fits above, × cos² of the leading-edge sweep, on the edges' frontal area. |
| Fin trailing edges | Square: base drag on their frontal area. |
| Base | `0.12 + 0.13 M²` below Mach 1, `0.25 / M` above. While the motor burns its exhaust fills the nozzle area, so that part of the base has no base drag (`Cd burn` in the CLI). |
| Nose pressure | Cone: `0.8 sin²φ` subsonic, Hoerner's supersonic cone value above Mach 1.3, linear between. Smooth shapes: zero below Mach 0.8. |

The Reynolds number uses the rocket's length and the 1976 Standard Atmosphere's
viscosity at the given altitude. Surface roughness comes from `finish:` in the
rocket file:

| `finish:` | Roughness |
|---|---|
| `polished` | 2 µm |
| `smooth_paint` | 20 µm |
| `regular_paint` (default) | 60 µm |
| `unfinished` | 150 µm |
| `rough` | 500 µm |

or any height, such as `finish: 35um`. For a rocket of Kestrel's size the
finish usually limits skin friction, which is why sanding and painting pay off.

**Known limit.** Above Mach 0.8 smooth noses (ogive, von Kármán, Haack,
parabolic, ellipsoid) start to carry wave drag that is not modelled yet, so Cd
there is a lower bound and the CLI says so. This is scheduled with the full
flight (Day 5), before the OpenRocket comparison.

## Pitch damping

A rocket rotating at pitch rate ω sees each part meet the air at an extra angle
`ω (x_i − x_cg) / v`. Summing the resulting forces times their arms gives a
moment `−C ω` with

```
C = ½ ρ v A_ref Σ CNα_i (x_i − x_cg)²
```

(Stine, *Handbook of Model Rocketry*, ch. 10). The 6-DOF solver uses it on Day
4, together with the motor's jet damping.

## Validation

Every check is a test in [`tests/test_aero.py`](../tests/test_aero.py).

| Check | Result |
|---|---|
| Fin `CNα` and CP vs Barrowman's equations worked by hand (4 fins, `CNα = 6.4 × 4/3`, `X = 1.21875 m`) | exact to 10⁻¹² |
| Whole-rocket CP vs the lift-weighted average by hand (1.02532 m) | within 10⁻⁶ |
| Nose CP vs the closed forms in the table above (six shapes) | within 10⁻⁶ (ogive 0.2 %) |
| Fin count scaling: 3 fins = ¾ of 4, 6 fins = 1.5 × 4 × 0.913 | exact |
| High-aspect fin at Mach 2 vs Ackeret with Bonney's tip loss | exact |
| Fin lift rises with Mach (Prandtl–Glauert trend) and is continuous at Mach 0.9 and 1.5 | ✓ |
| Small-angle `CN = CNα α`; Galejs body lift at 10° | within 0.1 % / exact |
| Skin friction formula at Re = 10⁶ by hand (0.0043725); roughness limit; compressibility factor | within 0.01 % |
| Cone and tube wetted areas vs closed forms | within 10⁻⁶ |
| Base drag lost to the exhaust equals base Cd × nozzle area / A_ref | exact |
| Damping coefficient by hand; linear in airspeed | within 10⁻⁶ |
| Kestrel: margin 1.5–3 cal at liftoff and larger at burnout; Cd 0.35–0.65 at Mach 0.3 | 2.28 / 3.83 cal; Cd 0.49 |

The end-to-end check against OpenRocket's Kestrel model comes on Day 6.

## References

- J. S. Barrowman, *The Practical Calculation of the Aerodynamic
  Characteristics of Slender Finned Vehicles*, M.S. thesis, Catholic University
  of America, 1967.
- S. Niskanen, *Development of an Open Source Model Rocket Simulation Software*,
  M.Sc. thesis, Helsinki University of Technology, 2009 (the OpenRocket
  technical documentation).
- S. F. Hoerner, *Fluid-Dynamic Drag*, 1965.
- R. Galejs, "Wind Instability: What Barrowman Left Out", 1999.
- E. A. Bonney, *Engineering Supersonic Aerodynamics*, McGraw-Hill, 1950.
- R. T. Jones, "Properties of Low-Aspect-Ratio Pointed Wings at Speeds Below
  and Above the Speed of Sound", NACA Report 835, 1946.
- G. H. Stine, *Handbook of Model Rocketry*, 7th ed., Wiley, 2004.
