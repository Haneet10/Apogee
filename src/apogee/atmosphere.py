"""The 1976 US Standard Atmosphere from sea level to 86 km.

This is the atmosphere every serious flight simulator uses below the
thermosphere. It describes the air as seven layers in which temperature varies
linearly with geopotential altitude; pressure then follows from the hydrostatic
equation and density from the ideal-gas law.

Quick use::

    >>> from apogee.atmosphere import atmosphere
    >>> air = atmosphere(1500.0)        # geometric altitude in metres
    >>> round(air.density, 4)           # kg/m³
    1.0581
    >>> round(air.speed_of_sound, 2)    # m/s
    334.49

Arrays work too and are evaluated in one vectorised pass::

    >>> import numpy as np
    >>> atmosphere(np.array([0.0, 11_000.0])).temperature.round(3)
    array([288.15 , 216.774])

Method (all equation and table numbers refer to the standard):

* Layer bases, base temperatures and lapse rates: Table 4.
* Molecular-scale temperature ``T_M`` is linear in geopotential altitude in each
  layer (eq. 23). Kinetic temperature ``T = T_M · M/M0`` (eq. 21), where the
  molecular-weight ratio ``M/M0`` falls slightly above 80 km (Table 8).
* Pressure from the integrated hydrostatic equation (eqs. 33a and 33b). The
  layer base pressures are derived here from the sea-level value, exactly as
  the standard does, and the tests check them against Table 4.
* Density ``ρ = p M0 / (R* T_M)`` (eq. 42), speed of sound
  ``a = sqrt(γ R* T_M / M0)`` (eq. 50), dynamic viscosity from Sutherland's law
  ``μ = β T^1.5 / (T + S)`` (eq. 51).

Reference: *U.S. Standard Atmosphere, 1976*, NOAA-S/T 76-1562, U.S. Government
Printing Office, Washington D.C., 1976.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, overload

import numpy as np
from numpy.typing import ArrayLike, NDArray

from apogee.earth import G0, geopotential_altitude

__all__ = [
    "MAX_ALTITUDE",
    "MIN_ALTITUDE",
    "AtmosphereState",
    "OutOfAtmosphereError",
    "atmosphere",
]

# --- Constants (USSA 1976, Table 2 and section 1.3) ---------------------------

#: Universal gas constant as defined by the standard [J/(kmol·K)].
R_STAR: float = 8314.32
#: Sea-level mean molecular weight of air [kg/kmol].
M0: float = 28.9644
#: Specific gas constant of air, R*/M0 [J/(kg·K)].
R_AIR: float = R_STAR / M0
#: Ratio of specific heats for air.
GAMMA: float = 1.40
#: Sutherland constant β [kg/(s·m·K^0.5)].
SUTHERLAND_BETA: float = 1.458e-6
#: Sutherland temperature S [K].
SUTHERLAND_S: float = 110.4
#: Sea-level pressure [Pa].
P0: float = 101_325.0

#: Lowest geometric altitude the standard defines [m].
MIN_ALTITUDE: float = -5_000.0
#: Highest geometric altitude this model covers [m] (top of the lower atmosphere).
MAX_ALTITUDE: float = 86_000.0

# --- Layer table (USSA 1976, Table 4), geopotential altitude --------------------

#: Layer base geopotential altitudes [m].
_H_BASE = np.array([0.0, 11_000.0, 20_000.0, 32_000.0, 47_000.0, 51_000.0, 71_000.0])
#: Molecular-scale temperature gradients in each layer [K/m].
_LAPSE = np.array([-6.5, 0.0, 1.0, 2.8, 0.0, -2.8, -2.0]) * 1e-3


def _layer_bases() -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Derive base temperatures and pressures of each layer from sea level."""
    t_base = np.empty_like(_H_BASE)
    p_base = np.empty_like(_H_BASE)
    t_base[0], p_base[0] = 288.15, P0
    for i in range(1, len(_H_BASE)):
        dh = _H_BASE[i] - _H_BASE[i - 1]
        t_base[i], p_base[i] = _layer_step(t_base[i - 1], p_base[i - 1], _LAPSE[i - 1], dh)
    return t_base, p_base


def _layer_step(t_b: float, p_b: float, lapse: float, dh: float) -> tuple[float, float]:
    """Advance temperature and pressure through ``dh`` metres of one layer."""
    t = t_b + lapse * dh
    k = G0 * M0 / R_STAR
    p = p_b * np.exp(-k * dh / t_b) if lapse == 0.0 else p_b * (t_b / t) ** (k / lapse)
    return float(t), float(p)


_T_BASE, _P_BASE = _layer_bases()

#: Molecular-weight ratio M/M0 between 80 and 86 km geometric (USSA 1976, Table 8).
_MW_ALT = np.arange(80_000.0, 86_001.0, 500.0)
_MW_RATIO = np.array(
    [
        1.000000, 0.999996, 0.999989, 0.999971, 0.999941, 0.999909, 0.999870,
        0.999829, 0.999786, 0.999741, 0.999694, 0.999641, 0.999579,
    ]
)  # fmt: skip


# --- Public API ---------------------------------------------------------------

_V = TypeVar("_V", float, NDArray[np.float64])


@dataclass(frozen=True, slots=True)
class AtmosphereState(Generic[_V]):
    """Properties of still air at one altitude (or an array of altitudes).

    Every field is a ``float`` when :func:`atmosphere` was called with a scalar
    and a NumPy array of the same shape when it was called with an array.

    Attributes:
        altitude: Geometric altitude above mean sea level [m].
        temperature: Kinetic temperature [K].
        pressure: Static pressure [Pa].
        density: Air density [kg/m³].
        speed_of_sound: Speed of sound [m/s].
        dynamic_viscosity: Dynamic viscosity μ [Pa·s].
        kinematic_viscosity: Kinematic viscosity ν = μ/ρ [m²/s].
    """

    altitude: _V
    temperature: _V
    pressure: _V
    density: _V
    speed_of_sound: _V
    dynamic_viscosity: _V
    kinematic_viscosity: _V


class OutOfAtmosphereError(ValueError):
    """Raised when an altitude lies outside the range the model covers."""


@overload
def atmosphere(altitude: float) -> AtmosphereState[float]: ...
@overload
def atmosphere(altitude: ArrayLike) -> AtmosphereState[NDArray[np.float64]]: ...
def atmosphere(
    altitude: ArrayLike,
) -> AtmosphereState[float] | AtmosphereState[NDArray[np.float64]]:
    """Return 1976 US Standard Atmosphere properties at a geometric altitude.

    Args:
        altitude: Geometric altitude above mean sea level [m], as a number or
            array-like. Valid from -5,000 m to 86,000 m.

    Returns:
        An :class:`AtmosphereState`. Fields are floats for scalar input and
        arrays of the input's shape otherwise.

    Raises:
        OutOfAtmosphereError: If any altitude is NaN or outside -5 km to 86 km.
    """
    z = np.asarray(altitude, dtype=np.float64)
    _check_range(z)

    h = np.asarray(geopotential_altitude(z))
    layer = np.clip(np.searchsorted(_H_BASE, h, side="right") - 1, 0, len(_H_BASE) - 1)
    h_b, t_b, p_b, lapse = _H_BASE[layer], _T_BASE[layer], _P_BASE[layer], _LAPSE[layer]

    t_m = t_b + lapse * (h - h_b)
    k = G0 * M0 / R_STAR
    isothermal = lapse == 0.0
    safe_lapse = np.where(isothermal, 1.0, lapse)
    pressure = np.where(
        isothermal,
        p_b * np.exp(-k * (h - h_b) / t_b),
        p_b * (t_b / t_m) ** (k / safe_lapse),
    )

    temperature = t_m * np.interp(z, _MW_ALT, _MW_RATIO, left=1.0, right=_MW_RATIO[-1])
    density = pressure * M0 / (R_STAR * t_m)
    speed_of_sound = np.sqrt(GAMMA * R_STAR * t_m / M0)
    mu = SUTHERLAND_BETA * temperature**1.5 / (temperature + SUTHERLAND_S)
    nu = mu / density

    if z.ndim == 0:
        return AtmosphereState(
            altitude=float(z),
            temperature=float(temperature),
            pressure=float(pressure),
            density=float(density),
            speed_of_sound=float(speed_of_sound),
            dynamic_viscosity=float(mu),
            kinematic_viscosity=float(nu),
        )
    return AtmosphereState(
        altitude=z.copy(),
        temperature=temperature,
        pressure=pressure,
        density=density,
        speed_of_sound=speed_of_sound,
        dynamic_viscosity=mu,
        kinematic_viscosity=nu,
    )


def _check_range(z: NDArray[np.float64]) -> None:
    """Raise a clear error if any altitude is outside the modelled range."""
    if np.isnan(z).any():
        raise OutOfAtmosphereError("altitude is NaN; pass a finite height in metres")
    bad = (z < MIN_ALTITUDE) | (z > MAX_ALTITUDE)
    if bad.any():
        worst = float(z[bad].flat[0])
        raise OutOfAtmosphereError(
            f"altitude {worst:,.0f} m is outside the 1976 US Standard Atmosphere range "
            f"({MIN_ALTITUDE:,.0f} m to {MAX_ALTITUDE:,.0f} m). Pass geometric altitude in "
            "metres above mean sea level."
        )
