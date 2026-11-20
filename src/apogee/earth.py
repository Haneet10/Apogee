"""Earth constants, gravity and altitude conversions.

Altitude comes in two flavours in atmospheric work:

* **Geometric altitude** ``z`` is the ordinary height above mean sea level you
  would measure with a tape. Every public function in Apogee takes geometric
  altitude in metres.
* **Geopotential altitude** ``H`` is height measured in units of constant
  gravitational potential energy. It lets the hydrostatic equation be written
  with a constant ``g0`` and is what the 1976 US Standard Atmosphere tables are
  built on.

The two are related through the effective Earth radius ``r0`` used by the
standard: ``H = r0 z / (r0 + z)``.

Reference: *U.S. Standard Atmosphere, 1976*, NOAA-S/T 76-1562, U.S. Government
Printing Office, Washington D.C., 1976, equations 18 and 19.
"""

from __future__ import annotations

from typing import overload

import numpy as np
from numpy.typing import ArrayLike, NDArray

#: Standard gravitational acceleration at sea level [m/s²] (USSA 1976, Table 2).
G0: float = 9.80665

#: Effective Earth radius used for geopotential conversion [m] (USSA 1976, Table 2).
EARTH_RADIUS: float = 6_356_766.0


@overload
def gravity(altitude: float) -> float: ...
@overload
def gravity(altitude: ArrayLike) -> NDArray[np.float64]: ...
def gravity(altitude: ArrayLike) -> float | NDArray[np.float64]:
    """Return gravitational acceleration at a geometric altitude.

    Uses the inverse-square law about the standard's effective Earth radius,
    ``g = g0 (r0 / (r0 + z))²`` (USSA 1976, eq. 17).

    Args:
        altitude: Geometric altitude above mean sea level [m].

    Returns:
        Gravitational acceleration [m/s²], same shape as the input.
    """
    z = np.asarray(altitude, dtype=np.float64)
    g = G0 * (EARTH_RADIUS / (EARTH_RADIUS + z)) ** 2
    return float(g) if g.ndim == 0 else g


@overload
def geopotential_altitude(altitude: float) -> float: ...
@overload
def geopotential_altitude(altitude: ArrayLike) -> NDArray[np.float64]: ...
def geopotential_altitude(altitude: ArrayLike) -> float | NDArray[np.float64]:
    """Convert geometric altitude ``z`` [m] to geopotential altitude ``H`` [m]."""
    z = np.asarray(altitude, dtype=np.float64)
    h = EARTH_RADIUS * z / (EARTH_RADIUS + z)
    return float(h) if h.ndim == 0 else h


@overload
def geometric_altitude(geopotential: float) -> float: ...
@overload
def geometric_altitude(geopotential: ArrayLike) -> NDArray[np.float64]: ...
def geometric_altitude(geopotential: ArrayLike) -> float | NDArray[np.float64]:
    """Convert geopotential altitude ``H`` [m] to geometric altitude ``z`` [m]."""
    h = np.asarray(geopotential, dtype=np.float64)
    z = EARTH_RADIUS * h / (EARTH_RADIUS - h)
    return float(z) if z.ndim == 0 else z
