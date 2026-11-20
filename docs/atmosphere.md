# Atmosphere model

Apogee needs to know what the air is like at every point of a flight: density
sets drag, the speed of sound sets the Mach number, and viscosity sets the
Reynolds number that skin-friction drag depends on. It uses the **1976 US
Standard Atmosphere**, the reference atmosphere behind OpenRocket, RASAero and
most aerospace tables, from 5 km below sea level up to 86 km.

```python
from apogee import atmosphere

air = atmosphere(1500.0)      # geometric altitude, metres above sea level
air.density                   # 1.0581 kg/m³
air.speed_of_sound            # 334.49 m/s
air.kinematic_viscosity       # 1.6463e-05 m²/s
```

Pass a NumPy array and every property comes back as an array of the same shape,
computed in one vectorised pass (this is what the flight solver will do each
time step).

## Method

The standard splits the air below 86 km into seven layers. Inside each layer the
*molecular-scale temperature* `T_M` changes linearly with *geopotential*
altitude `H`, a height scale in which gravity is constant:

| Layer | Base `H` (km) | Base `T_M` (K) | Lapse rate (K/km) | Base pressure (Pa) |
|---|---|---|---|---|
| Troposphere | 0 | 288.15 | −6.5 | 101,325 |
| Tropopause | 11 | 216.65 | 0.0 | 22,632.06 |
| Stratosphere 1 | 20 | 216.65 | +1.0 | 5,474.889 |
| Stratosphere 2 | 32 | 228.65 | +2.8 | 868.0187 |
| Stratopause | 47 | 270.65 | 0.0 | 110.9063 |
| Mesosphere 1 | 51 | 270.65 | −2.8 | 66.93887 |
| Mesosphere 2 | 71 | 214.65 | −2.0 | 3.956420 |

Equation numbers below refer to the standard.

1. **Altitude.** Geometric altitude `z` is converted to geopotential altitude
   with `H = r0 z / (r0 + z)`, `r0 = 6,356,766 m` (eq. 18).
2. **Temperature.** `T_M = T_b + L (H − H_b)` in the layer containing `H`
   (eq. 23). The kinetic temperature is `T = T_M · M/M0` (eq. 21); the
   molecular-weight ratio `M/M0` is 1 below 80 km and falls to 0.999579 at
   86 km (Table 8, interpolated linearly).
3. **Pressure.** The hydrostatic equation integrated through each layer gives
   `p = p_b (T_b / T_M)^(g0 M0 / R* L)` where `L ≠ 0` and
   `p = p_b exp(−g0 M0 (H − H_b) / R* T_b)` where `L = 0` (eqs. 33a, 33b).
   The base pressures in the table are *derived* from sea level by the same
   formulas rather than typed in; the tests check them against the standard.
4. **Density** `ρ = p M0 / (R* T_M)` (eq. 42).
5. **Speed of sound** `a = sqrt(γ R* T_M / M0)`, `γ = 1.4` (eq. 50).
6. **Viscosity** from Sutherland's law, `μ = β T^1.5 / (T + S)` with
   `β = 1.458 × 10⁻⁶ kg/(s·m·K^½)` and `S = 110.4 K` (eq. 51); `ν = μ / ρ`.

Constants: `g0 = 9.80665 m/s²`, `R* = 8314.32 J/(kmol·K)`, `M0 = 28.9644 kg/kmol`
(Table 2).

## Validation

`tests/test_atmosphere.py` checks the model three ways.

**Against the published tables.** Temperature, pressure, density, speed of sound
and viscosity at eight geometric altitudes from Table I of the standard, which
prints five significant figures. The largest difference across all 40 values is
**0.007 %**, which is the table's own rounding.

| `z` (km) | `T` table (K) | `T` Apogee (K) | `p` table (Pa) | `p` Apogee (Pa) | `ρ` table (kg/m³) | `ρ` Apogee (kg/m³) | Worst error, all 5 properties |
|---|---|---|---|---|---|---|---|
| 0 | 288.150 | 288.150 | 101,325 | 101,325 | 1.2250 | 1.2250 | 0.0012 % |
| 1 | 281.651 | 281.651 | 89,876 | 89,876 | 1.1117 | 1.1117 | 0.0037 % |
| 11 | 216.774 | 216.774 | 22,700 | 22,700 | 0.36480 | 0.36480 | 0.0013 % |
| 20 | 216.650 | 216.650 | 5,529.3 | 5,529.3 | 0.088910 | 0.088910 | 0.0009 % |
| 32 | 228.490 | 228.490 | 889.06 | 889.06 | 0.013555 | 0.013555 | 0.0022 % |
| 50 | 270.650 | 270.650 | 79.779 | 79.779 | 1.0269 × 10⁻³ | 1.0269 × 10⁻³ | 0.0021 % |
| 80 | 198.639 | 198.639 | 1.0524 | 1.0525 | 1.8458 × 10⁻⁵ | 1.8458 × 10⁻⁵ | 0.0070 % |
| 86 | 186.87 | 186.867 | 0.37338 | 0.37338 | 6.958 × 10⁻⁶ | 6.9578 × 10⁻⁶ | 0.0025 % |

**Against Table 4.** The layer base pressures derived from sea level match the
standard's tabulated values to 2 parts in 10⁷, and the pressure at the base of
the top layer (H = 84.852 km) matches 0.3733836 Pa to 1 part in 10⁶.

**Against physics.**

- *Hydrostatic balance:* a central-difference `dp/dz` equals `−ρ g(z)` to
  10⁻⁶ relative at 400 altitudes spanning the whole model, with `g(z)` from the
  inverse-square law.
- *Ideal-gas law:* `p = ρ R T` holds to 10⁻¹² below 80 km, and above 80 km
  differs by exactly the molecular-weight ratio.
- *Continuity:* every property is continuous across all six layer boundaries.

## Limits

- Above 86 km the gas is no longer well mixed and the standard switches to a
  different model. Apogee stops there and raises `OutOfAtmosphereError`; no
  amateur or university rocket comes close.
- The standard atmosphere is a mid-latitude annual average. Real launch-day
  temperature can differ by 20 K or more, which shifts density by several
  percent. A launch-site temperature and pressure offset will come with the
  flight model.

## Reference

*U.S. Standard Atmosphere, 1976*, NOAA-S/T 76-1562, U.S. Government Printing
Office, Washington D.C., October 1976.
